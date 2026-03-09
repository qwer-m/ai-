"""
后端应用入口。

职责：
1. 初始化 FastAPI 应用与全局资源（数据库、Redis、AI 配置）。
2. 注册中间件与业务路由（认证、项目、知识库、测试生成、UI 自动化等）。
3. 提供健康检查与根路径重定向等基础接口。
"""

import os
import shutil
import uuid
import json
import re
import httpx
import socket
import time
import asyncio
import io
from datetime import datetime, timedelta
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks, Request, Query
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, StreamingResponse, Response, RedirectResponse
from starlette.concurrency import iterate_in_threadpool
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

# 核心基础设施
from core.database import get_db, SessionLocal
from core.models import LogEntry, SystemConfig
from core.utils import logger, log_to_db
from core.config import settings
from core.ai_client import ai_client
from core.config_manager import config_manager
from core.redis_pool import redis_pool
from core.browser_pool import browser_pool

# 业务模块路由
from modules.auth import router as auth_router
from modules.standard_api import router as standard_api_router
from routers.ui_test_cases import router as ui_test_cases_router

# 当前重构后路由
from routers.projects import router as projects_router
from routers.test_generation import router as test_gen_router
from routers.ui_automation import router as ui_auto_router
from routers.common import router as common_router
from routers.debug import router as debug_router
from routers.tasks import router as tasks_router
from routers.logs import router as logs_router
from routers.config import router as config_router

from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

# 限流器配置
limiter = Limiter(key_func=get_remote_address)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理：
    1. 启动：初始化 Redis、检查 MySQL、记录系统日志、加载 AI 激活配置。
    2. 关闭：释放浏览器池等全局资源。
    """
    # 启动阶段：初始化共享资源
    app.state.redis = redis_pool
    print("Application startup: Redis pool initialized (应用启动: Redis 连接池已初始化)")
    
    # 从激活配置初始化 AI 客户端
    try:
        db = SessionLocal()
        
        # 系统健康检查与日志
        redis_status = "Connected"
        try:
            redis_pool.ping()
        except Exception as e:
            redis_status = f"Failed: {str(e)}"
            
        mysql_status = "Connected"
        try:
             db.execute(text("SELECT 1"))
        except Exception as e:
             mysql_status = f"Failed: {str(e)}"
             
        # 记录系统启动日志
        try:
            # 这里直接写库，避免依赖辅助函数的导入状态
            system_log = LogEntry(
                project_id=None,  # 系统级日志
                log_type="system",
                message=f"System started. Redis: {redis_status}, MySQL: {mysql_status}"
            )
            db.add(system_log)
            db.commit()
        except Exception as log_e:
            print(f"Failed to write startup log: {log_e}")

        active_config = config_manager.get_active_config(db)
        if active_config:
            new_client = ai_client.from_config(active_config)
            ai_client.update_provider(new_client.provider, new_client.model)
            print(f"Loaded active AI config: {active_config.provider} / {active_config.model_name}")
        else:
            print("No active AI config found in DB, using settings.py defaults.")
        db.close()
    except Exception as e:
        print(f"Failed to load AI config on startup: {e}")

    yield
    
    # 关闭阶段：清理资源
    # 关闭全局浏览器池
    if browser_pool:
        if browser_pool.playwright:
             await browser_pool.playwright.stop()
    
    print("Application shutdown: Cleaning up resources... (应用关闭: 正在清理资源...)")

app = FastAPI(title="AI Test Platform", lifespan=lifespan)

# 挂载业务路由
app.include_router(auth_router, prefix="/api")
app.include_router(standard_api_router, prefix="/api")
app.include_router(ui_test_cases_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(test_gen_router, prefix="/api")
app.include_router(ui_auto_router, prefix="/api")
app.include_router(common_router, prefix="/api")
app.include_router(debug_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(logs_router, prefix="/api")
app.include_router(config_router, prefix="/api")

from redis import Redis

@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    # 检查 MySQL 连通性
    mysql_ok = False
    mysql_details = ""
    try:
        db.execute(text("SELECT 1"))
        mysql_ok = True
    except Exception as e:
        mysql_details = str(e)
    
    # 检查 Redis 连通性
    redis_ok = False
    redis_details = ""
    try:
        # 从连接池创建临时客户端并执行 ping
        r = Redis(connection_pool=redis_pool)
        r.ping()
        redis_ok = True
    except Exception as e:
        redis_details = str(e)
        
    return {
        "status": "ok" if mysql_ok and redis_ok else "error",
        "time": datetime.now(),
        "mysql": {"ok": mysql_ok, "details": mysql_details},
        "redis": {"ok": redis_ok, "details": redis_details}
    }

# 中间件栈
app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    TrustedHostMiddleware, 
    allowed_hosts=["localhost", "127.0.0.1", "0.0.0.0", "8.130.106.199", "*"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境建议替换为明确的前端域名白名单
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIST_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend", "dist"))
STATIC_DIR = os.path.abspath(os.path.join(BASE_DIR, "static"))
DEV_SERVER_URL = os.environ.get("VITE_DEV_SERVER_URL", "http://127.0.0.1:5173")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/", include_in_schema=False)
async def read_root(request: Request):
    """
    根路径重定向：
    把后端根路径重定向到前端地址，避免用户在新窗口重复打开页面。
    """
    return RedirectResponse(url=DEV_SERVER_URL, status_code=307)


