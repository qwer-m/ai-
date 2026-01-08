import os
import shutil
import uuid
import json
import httpx
import socket
from datetime import datetime, timedelta
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks, Request, Query
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text, desc
from core.database import get_db, SessionLocal
from core.models import Project, TestGeneration, LogEntry, KnowledgeDocument, UIExecution, UIErrorOperation, APIExecution, Evaluation, TestGenerationComparison, RecallMetric, User
from modules.knowledge_base import knowledge_base
from core.utils import logger
from core.file_processing import parse_file_content
from core.config import settings
from core.ai_client import ai_client, DashScopeProvider, OpenAICompatibleProvider
from core.config_manager import config_manager
from core.models import SystemConfig
from core.auth import get_current_user
from modules.auth import router as auth_router
import pypdf
import pandas as pd
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
from celery.result import AsyncResult
from celery_config import celery_app
from modules.tasks import generate_test_cases_task
from modules.test_generation import test_generator
from core.redis_pool import redis_pool
from core.browser_pool import browser_pool

# Rate limiter setup
limiter = Limiter(key_func=get_remote_address)

# Global state for health monitoring
last_redis_status = None
last_mysql_status = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize resources
    app.state.redis = redis_pool
    print("Application startup: Redis pool initialized")
    
    # Initialize AI Client from active config
    try:
        db = SessionLocal()
        
        # System Health Check & Logging
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
             
        # Log system start
        try:
            # We need to manually insert because log_to_db helper might not be imported or available here easily 
            # (it is available but let's be safe with direct DB op if needed, but wait, log_to_db is not imported? 
            # I checked imports, I didn't see log_to_db. I saw LogEntry model.)
            # I will use LogEntry directly.
            system_log = LogEntry(
                project_id=None, # System level
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
    
    # Shutdown: Clean up resources
    # Close global browser pool
    if browser_pool:
        # We don't have a close method on browser_pool instance that closes all browsers?
        # browser_pool.py has release_browser but no close_all.
        # But if we use a single playwright instance, we might want to stop it.
        # Actually, let's just log for now, as BrowserPool manages instances on demand.
        # Ideally we should close playwright.
        if browser_pool.playwright:
             await browser_pool.playwright.stop()
    
    print("Application shutdown: Cleaning up resources...")

app = FastAPI(title="AI测试开发平台", lifespan=lifespan)
app.include_router(auth_router, prefix="/api")

# Middleware Stack
app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    TrustedHostMiddleware, 
    allowed_hosts=["localhost", "127.0.0.1", "0.0.0.0"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace with specific frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIST_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend", "dist"))
STATIC_DIR = os.path.abspath(os.path.join(BASE_DIR, "static"))
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Models
# --- Project APIs ---
class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    parent_id: Optional[int] = None

class ProjectUpdate(BaseModel):
    name: str
    description: Optional[str] = None
    parent_id: Optional[int] = None

@app.post("/api/projects")
def create_project(project: ProjectCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Calculate level
    level = 1
    if project.parent_id:
        parent = db.query(Project).filter(Project.id == project.parent_id, Project.user_id == current_user.id).first()
        if not parent:
            return {"error": "Parent project not found"}
        level = parent.level + 1
        if level > 3:
            return {"error": "Maximum project nesting level (3) reached."}
    
    # Check duplicate name under same parent
    existing = db.query(Project).filter(
        Project.name == project.name,
        Project.parent_id == project.parent_id,
        Project.user_id == current_user.id
    ).first()
    
    if existing:
        return {"error": "Project name already exists in this level"}
    
    new_project = Project(
        name=project.name, 
        description=project.description,
        parent_id=project.parent_id,
        level=level,
        user_id=current_user.id
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    return new_project

@app.get("/api/projects")
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Project).filter(Project.user_id == current_user.id).order_by(Project.created_at.desc()).all()

@app.get("/api/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        return {"error": "Project not found"}
    return project

@app.put("/api/projects/{project_id}")
def update_project(project_id: int, project: ProjectUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Get the project to update
    db_project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not db_project:
        return {"error": "Project not found"}
    
    # Calculate new level if parent is changing
    new_level = db_project.level
    if project.parent_id != db_project.parent_id:
        if project.parent_id:
            parent = db.query(Project).filter(Project.id == project.parent_id, Project.user_id == current_user.id).first()
            if not parent:
                return {"error": "Parent project not found"}
            new_level = parent.level + 1
            if new_level > 3:
                return {"error": "Maximum project nesting level (3) reached."}
        else:
            new_level = 1
    
    # Check duplicate name under same parent
    existing = db.query(Project).filter(
        Project.name == project.name,
        Project.parent_id == project.parent_id,
        Project.id != project_id,
        Project.user_id == current_user.id
    ).first()
    
    if existing:
        return {"error": "Project name already exists in this level"}
    
    # Update project fields
    db_project.name = project.name
    db_project.description = project.description
    db_project.parent_id = project.parent_id
    db_project.level = new_level
    
    # Update child levels if parent changed
    if project.parent_id != db_project.parent_id:
        # Recursively update child levels
        def update_child_levels(current_project, new_parent_level):
            current_project.level = new_parent_level + 1
            for child in current_project.children:
                update_child_levels(child, current_project.level)
        
        for child in db_project.children:
            update_child_levels(child, db_project.level)
    
    db.commit()
    db.refresh(db_project)
    return db_project

@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        # Check if project exists
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            return JSONResponse(status_code=404, content={"error": "Project not found"})
        
        # Check if project has children
        if project.children:
            return JSONResponse(status_code=400, content={"error": "Cannot delete project with child projects. Please delete children first."})
        
        # Delete all related knowledge documents
        db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id).delete()
        
        # Delete other related records
        db.query(LogEntry).filter(LogEntry.project_id == project_id).delete()
        db.query(TestGeneration).filter(TestGeneration.project_id == project_id).delete()
        
        # Delete UI executions and related errors
        # First delete UIErrorOperation that link to UIExecution of this project
        # Since UIErrorOperation also has project_id, we can delete by project_id
        db.query(UIErrorOperation).filter(UIErrorOperation.project_id == project_id).delete()
        db.query(UIExecution).filter(UIExecution.project_id == project_id).delete()
        
        db.query(APIExecution).filter(APIExecution.project_id == project_id).delete()
        db.query(Evaluation).filter(Evaluation.project_id == project_id).delete()
        db.query(TestGenerationComparison).filter(TestGenerationComparison.project_id == project_id).delete()
        db.query(RecallMetric).filter(RecallMetric.project_id == project_id).delete()
        
        # Delete the project
        db.delete(project)
        db.commit()
        
        return {"message": "Project deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting project {project_id}: {str(e)}")
        db.rollback()
        return JSONResponse(status_code=500, content={"error": f"Failed to delete project: {str(e)}"})

# --- Updated APIs with Project ID ---

class TestGenRequest(BaseModel):
    requirement: str
    project_id: int
    compress: bool = False
    expected_count: int = 20
    batch_index: int = 0
    batch_size: int = 20

class UIRequest(BaseModel):
    url: str
    task: str
    project_id: int
    automation_type: str = "web"

class APIRequest(BaseModel):
    requirement: str
    project_id: int
    base_url: Optional[str] = None
    test_types: Optional[List[str]] = None
    mode: str = "natural"  # "natural" | "structured"

class EvalRequest(BaseModel):
    content: str
    project_id: int

class RecallRequest(BaseModel):
    retrieved: list[str]
    relevant: list[str]
    project_id: int

class UIAutoEvalRequest(BaseModel):
    script: str
    execution_result: str
    project_id: int

class APITestEvalRequest(BaseModel):
    script: str
    execution_result: str
    project_id: int

class TestComparisonRequest(BaseModel):
    generated_test_case: str
    modified_test_case: str
    project_id: int

class LogCreate(BaseModel):
    project_id: int
    log_type: str
    message: str

class LogRead(BaseModel):
    id: int
    project_id: int
    log_type: str
    message: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class KnowledgeUpdateRequest(BaseModel):
    filename: Optional[str] = None
    content: Optional[str] = None
    doc_type: Optional[str] = None

# Routes
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    # DYNAMIC PROXY MODE (No 'dist' dependency)
    # Always try to proxy to Vite dev server (localhost:5173)
    dev_server_url = "http://localhost:5173"
    
    try:
        # trust_env=False prevents using system proxy settings which might cause 502 on localhost
        async with httpx.AsyncClient(trust_env=False) as client:
            try:
                resp = await client.get(f"{dev_server_url}/")
            except httpx.ConnectError:
                # If dev server not running, show friendly error
                return HTMLResponse(
                    "<h1>Frontend Dev Server not running (port 5173).</h1>"
                    "<p>Please run <code>npm run dev</code> in frontend directory.</p>"
                )
            
            # Forward headers but exclude those that might conflict with the decompressed content
            excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
            headers = {
                k: v for k, v in resp.headers.items() 
                if k.lower() not in excluded_headers
            }

            return Response(
                content=resp.content, 
                status_code=resp.status_code, 
                media_type=resp.headers.get("content-type"),
                headers=headers
            )
    except Exception as e:
        return HTMLResponse(f"<h1>Proxy Error</h1><p>{str(e)}</p>")

@app.get("/legacy", response_class=HTMLResponse)
async def legacy_ui(request: Request):
    # Legacy UI has been removed
    return HTMLResponse("<h1>Legacy UI has been removed. Please use the new frontend.</h1>")

def log_to_db(db: Session, project_id: int, log_type: str, message: str, user_id: Optional[int] = None):
    try:
        log_entry = LogEntry(
            project_id=project_id,
            log_type=log_type,
            message=message,
            user_id=user_id
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to log to DB: {e}")

@app.post("/api/generate-tests")
def generate_tests(request: TestGenRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify project
    project = db.query(Project).filter(Project.id == request.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    log_to_db(db, request.project_id, "system", f"开始生成测试用例(批次{request.batch_index}): 长度={len(request.requirement)}, 压缩={request.compress}, 预期数量={request.expected_count}, 批次大小={request.batch_size}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}", user_id=current_user.id)
    result = test_generator.generate_test_cases_json(request.requirement, request.project_id, db, "requirement", request.compress, request.expected_count, request.batch_size, request.batch_index, user_id=current_user.id)
    try:
        count = len(result) if isinstance(result, list) else 0
        log_to_db(db, request.project_id, "system", f"测试用例生成完成(批次{request.batch_index}): 数量={count}", user_id=current_user.id)
        kb_ctx = knowledge_base.get_all_context(db, request.project_id) if db else ""
        diag = {
            "kind": "gen_diag",
            "mode": "text",
            "doc_type": "requirement",
            "compress": request.compress,
            "expected_count": request.expected_count,
            "generated_count": count,
            "requirement_length": len(request.requirement),
            "kb_length": len(kb_ctx or ""),
            "model": settings.MODEL_NAME,
            "max_tokens": settings.MAX_TOKENS,
            "batch_index": request.batch_index
        }
        log_to_db(db, request.project_id, "system", f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}", user_id=current_user.id)
        try:
            # Metrics calculation for this batch
            positive = 0
            negative = 0
            edge = 0
            avg_steps = 0.0
            pending = 0
            steps_count = 0
            steps_items = 0
            kw_neg = ["失败", "错误", "异常", "不可用", "拒绝", "超时"]
            kw_edge = ["边界", "最大值", "最小值", "极限", "临界", "空值", "重复", "特殊字符"]
            if isinstance(result, list):
                for item in result:
                    desc = (item.get("description") or "") + " " + (item.get("expected_result") or "")
                    is_neg = any(k in desc for k in kw_neg)
                    is_edge = any(k in desc for k in kw_edge)
                    if is_neg:
                        negative += 1
                    elif is_edge:
                        edge += 1
                    else:
                        positive += 1
                    steps = item.get("steps")
                    if isinstance(steps, list):
                        steps_count += len(steps)
                        steps_items += 1
                    elif isinstance(steps, str):
                        lines = [s for s in steps.splitlines() if s.strip()]
                        steps_count += len(lines)
                        steps_items += 1
                    if isinstance(item.get("description"), str) and "[Pending Confirmation]" in item.get("description"):
                        pending += 1
            avg_steps = steps_count / steps_items if steps_items else 0.0
            qm = {
                "positive": positive,
                "negative": negative,
                "edge": edge,
                "avg_steps": avg_steps,
                "pending": pending,
                "generated_count": count,
                "batch_index": request.batch_index
            }
            log_to_db(db, request.project_id, "system", f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}", user_id=current_user.id)
        except Exception:
            pass
    except Exception:
        log_to_db(db, request.project_id, "system", "测试用例生成完成", user_id=current_user.id)
    return result

@app.post("/api/generate-tests/async")
async def generate_tests_async(request: TestGenRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Trigger test generation asynchronously using Celery.
    Returns task_id for status tracking.
    """
    # Verify project
    project = db.query(Project).filter(Project.id == request.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    task = generate_test_cases_task.delay(
        requirement=request.requirement,
        project_id=request.project_id,
        doc_type="requirement",
        compress=request.compress,
        expected_count=request.expected_count,
        batch_index=request.batch_index,
        batch_size=request.batch_size,
        user_id=current_user.id
    )
    return {"task_id": task.id, "status": "PENDING", "message": "Task submitted successfully"}

@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str):
    """
    Get status of a Celery task.
    """
    task_result = AsyncResult(task_id, app=celery_app)
    result = {
        "task_id": task_id,
        "status": task_result.state,
        "result": task_result.result if task_result.ready() else None
    }
    # Handle meta info for progress if available (if we implemented custom state updates)
    if task_result.state == 'STARTED':
        if isinstance(task_result.info, dict):
            result.update(task_result.info)
    elif task_result.state == 'FAILURE':
         result['error'] = str(task_result.result)
         
    return result

@app.post("/api/generate-tests-file")
async def generate_tests_from_file(
    file: UploadFile = File(...), 
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    prototype_file: UploadFile | None = File(None),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    force: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify project
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Note: parse_file_content is async, so we must keep this endpoint async.
    # However, test_generator.generate_test_cases_json is sync.
    # We should run the blocking part in a threadpool.
    from fastapi.concurrency import run_in_threadpool

    try:
        base_prompt = "OCR: Extract all text from this image."
        proto_prompt = (
            "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
            "Identify input fields, buttons, navigation menus, and any visual indicators of state."
        )
        # Parse main document (text or image)
        content = await parse_file_content(file, base_prompt)

        # If incomplete with prototype image, parse prototype and merge content
        if doc_type == "incomplete" and prototype_file is not None:
            proto_text = await parse_file_content(prototype_file, proto_prompt)
            content = f"{content}\n\n[Prototype Analysis]\n{proto_text}"

        # Try to store document into Knowledge Base; if duplicate and not forced, return promptly
        try:
            kb_add = knowledge_base.add_document(file.filename, content, doc_type, project_id, db, force=force)
            if isinstance(kb_add, dict) and kb_add.get("status") == "duplicate" and not force:
                # Try exact match first
                prev = db.query(TestGeneration).filter(
                    TestGeneration.project_id == project_id,
                    TestGeneration.requirement_text == content,
                    TestGeneration.user_id == current_user.id
                ).order_by(TestGeneration.created_at.desc()).first()
                
                # If exact match fails (e.g. truncated history), try prefix match for long content
                if not prev and len(content) > 60000:
                    prefix = content[:60000]
                    prev = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text.startswith(prefix),
                        TestGeneration.user_id == current_user.id
                    ).order_by(TestGeneration.created_at.desc()).first()

                prev_json = None
                if prev and prev.generated_result:
                    try:
                        prev_json = json.loads(prev.generated_result)
                    except Exception:
                        prev_json = {"raw": prev.generated_result}
                return {
                    "duplicate": True,
                    "filename": kb_add.get("existing_filename"),
                    "previous_json": prev_json
                }
        except Exception:
            pass

        log_to_db(db, project_id, "system", f"文件生成测试用例: 主文档长度={len(content)}, 类型={doc_type}, 压缩={compress}, 预期数量={expected_count}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}", user_id=current_user.id)
        # Run sync generation in threadpool to avoid blocking event loop
        result = await run_in_threadpool(
            test_generator.generate_test_cases_json,
            content, project_id, db, doc_type, compress, expected_count, 20, 0, current_user.id
        )
        try:
            count = len(result) if isinstance(result, list) else 0
            log_to_db(db, project_id, "system", f"文件生成完成: 数量={count}", user_id=current_user.id)
            kb_ctx = knowledge_base.get_all_context(db, project_id) if db else ""
            diag = {
                "kind": "gen_diag",
                "mode": "file",
                "doc_type": doc_type,
                "compress": compress,
                "expected_count": expected_count,
                "generated_count": count,
                "content_length": len(content),
                "kb_length": len(kb_ctx or ""),
                "prototype_included": bool(prototype_file),
                "model": settings.MODEL_NAME,
                "max_tokens": settings.MAX_TOKENS
            }
            log_to_db(db, project_id, "system", f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}", user_id=current_user.id)
            try:
                positive = 0
                negative = 0
                edge = 0
                avg_steps = 0.0
                pending = 0
                steps_count = 0
                steps_items = 0
                kw_neg = ["失败", "错误", "异常", "不可用", "拒绝", "超时"]
                kw_edge = ["边界", "最大值", "最小值", "极限", "临界", "空值", "重复", "特殊字符"]
                if isinstance(result, list):
                    for item in result:
                        desc = (item.get("description") or "") + " " + (item.get("expected_result") or "")
                        is_neg = any(k in desc for k in kw_neg)
                        is_edge = any(k in desc for k in kw_edge)
                        if is_neg:
                            negative += 1
                        elif is_edge:
                            edge += 1
                        else:
                            positive += 1
                        steps = item.get("steps")
                        if isinstance(steps, list):
                            steps_count += len(steps)
                            steps_items += 1
                        elif isinstance(steps, str):
                            lines = [s for s in steps.splitlines() if s.strip()]
                            steps_count += len(lines)
                            steps_items += 1
                        if isinstance(item.get("description"), str) and "[Pending Confirmation]" in item.get("description"):
                            pending += 1
                avg_steps = steps_count / steps_items if steps_items else 0.0
                qm = {
                    "positive": positive,
                    "negative": negative,
                    "edge": edge,
                    "avg_steps": avg_steps,
                    "pending": pending,
                    "generated_count": count
                }
                log_to_db(db, project_id, "system", f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}", user_id=current_user.id)
            except Exception:
                pass
        except Exception:
            pass
        return result
    except ValueError as e:
        return {"error": str(e)}

@app.post("/api/generate-tests-file/async")
async def generate_tests_from_file_async(
    file: UploadFile = File(...), 
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    prototype_file: UploadFile | None = File(None),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    force: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Async version of generate-tests-file.
    Uploads file, parses it (sync), then submits Celery task.
    """
    # Verify project
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        base_prompt = "OCR: Extract all text from this image."
        proto_prompt = (
            "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
            "Identify input fields, buttons, navigation menus, and any visual indicators of state."
        )
        # Parse main document (text or image)
        content = await parse_file_content(file, base_prompt)

        # If incomplete with prototype image, parse prototype and merge content
        if doc_type == "incomplete" and prototype_file is not None:
            proto_text = await parse_file_content(prototype_file, proto_prompt)
            content = f"{content}\n\n[Prototype Analysis]\n{proto_text}"

        # Try to store document into Knowledge Base; if duplicate and not forced, return promptly
        try:
            kb_add = knowledge_base.add_document(file.filename, content, doc_type, project_id, db, force=force)
            if isinstance(kb_add, dict) and kb_add.get("status") == "duplicate" and not force:
                # Try exact match first
                prev = db.query(TestGeneration).filter(
                    TestGeneration.project_id == project_id,
                    TestGeneration.requirement_text == content,
                    TestGeneration.user_id == current_user.id
                ).order_by(TestGeneration.created_at.desc()).first()
                
                # If exact match fails (e.g. truncated history), try prefix match for long content
                if not prev and len(content) > 60000:
                    prefix = content[:60000]
                    prev = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text.startswith(prefix),
                        TestGeneration.user_id == current_user.id
                    ).order_by(TestGeneration.created_at.desc()).first()

                prev_json = None
                if prev and prev.generated_result:
                    try:
                        prev_json = json.loads(prev.generated_result)
                    except Exception:
                        prev_json = {"raw": prev.generated_result}
                return {
                    "duplicate": True,
                    "filename": kb_add.get("existing_filename"),
                    "previous_json": prev_json
                }
        except Exception:
            pass

        # Submit task
        task = generate_test_cases_task.delay(
            requirement=content,
            project_id=project_id,
            doc_type=doc_type,
            compress=compress,
            expected_count=expected_count,
            user_id=current_user.id
        )
        return {"task_id": task.id, "status": "PENDING", "message": "File processed and task submitted successfully"}

    except ValueError as e:
        return {"error": str(e)}

@app.post("/api/generate-tests-excel")
def generate_tests_excel(request: TestGenRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        # Verify project
        project = db.query(Project).filter(Project.id == request.project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # request doesn't have doc_type, assuming standard requirement
        excel_bytes = test_generator.generate_test_cases_excel(request.requirement, request.project_id, db, user_id=current_user.id)
        return Response(content=excel_bytes, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"})
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/generate-tests-file-excel")
async def generate_tests_from_file_excel(
    file: UploadFile = File(...), 
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    prototype_file: UploadFile | None = File(None),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify project
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from fastapi.concurrency import run_in_threadpool
    try:
        base_prompt = "OCR: Extract all text from this image."
        proto_prompt = (
            "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
            "Identify input fields, buttons, navigation menus, and any visual indicators of state."
        )
        content = await parse_file_content(file, base_prompt)
        if doc_type == "incomplete" and prototype_file is not None:
            proto_text = await parse_file_content(prototype_file, proto_prompt)
            content = f"{content}\n\n[Prototype Analysis]\n{proto_text}"
        log_to_db(db, project_id, "system", f"文件生成Excel: 主文档长度={len(content)}, 类型={doc_type}, 压缩={compress}, 预期数量={expected_count}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}", user_id=current_user.id)
        excel_bytes = test_generator.generate_test_cases_excel(content, project_id, db, doc_type, compress, user_id=current_user.id)
        is_excel = True
        if len(excel_bytes) < 4 or excel_bytes[:2] != b'PK':
            is_excel = False
        if is_excel:
            return Response(content=excel_bytes, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"})
        else:
            return Response(content=excel_bytes, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=test_cases.csv"})
    except ValueError as e:
        return {"error": str(e)}

@app.post("/api/export-tests-excel")
def export_tests_excel(
    request: list[dict] | dict, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        data_bytes = test_generator.convert_json_to_excel(request)
        is_excel = True
        # Heuristic: if starts with 'PK' (zip signature) it's xlsx; otherwise CSV
        if len(data_bytes) < 4 or data_bytes[:2] != b'PK':
            is_excel = False
        if is_excel:
            return Response(content=data_bytes, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"})
        else:
            return Response(content=data_bytes, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=test_cases.csv"})
    except Exception as e:
        return {"error": str(e)}

from fastapi.security import OAuth2PasswordBearer
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

@app.post("/api/ui-automation")
def run_ui_automation(req: UIRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme)):
    # Verify project access
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    log_to_db(db, req.project_id, "system", f"开始执行UI自动化: {req.task}", user_id=current_user.id)
    # 1. Generate AI-driven image recognition script
    script = ui_automator.generate_ai_image_recognition_script(req.task, req.url, req.automation_type, db=db, user_id=current_user.id, token=token)
    # 2. Execute script
    result = ui_automator.execute_script(script, req.url, req.task, req.automation_type, db, req.project_id, user_id=current_user.id)
    log_to_db(db, req.project_id, "system", f"UI自动化执行完成，结果: {result.get('status', 'unknown')}", user_id=current_user.id)
    return {"script": script, "result": result}

@app.post("/api/ai-locate-element")
async def ai_locate_element(
    image: UploadFile = File(...),
    element_description: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Use AI to locate element coordinates from screenshot.
    
    Args:
        image: The screenshot image file
        element_description: Description of the element to locate
        
    Returns:
        dict: Coordinates of the located element
    """
    try:
        # Save uploaded image to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_file.write(await image.read())
            temp_file_path = temp_file.name
        
        # Use AI to locate element
        coords = ui_automator.ai_locate_element(temp_file_path, element_description, db=db, user_id=current_user.id)
        
        # Clean up temp file
        os.unlink(temp_file_path)
        
        # Check if coords is an error string
        if isinstance(coords, str):
            return {"error": coords}
        
        return {"coordinates": coords}
    except Exception as e:
        return {"error": f"AI Location Error: {str(e)}"}

@app.post("/api/api-testing")
async def run_api_testing(req: APIRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify project access
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 1. Generate script
    script = api_tester.generate_api_test_script(
        req.requirement, 
        base_url=req.base_url,
        api_path=req.api_path,
        test_types=req.test_types,
        db=db,
        mode=req.mode,
        user_id=current_user.id
    )
    # 2. Execute script
    # Returns dict with result and structured_report
    result_data = api_tester.execute_api_tests(
        script, 
        requirement=req.requirement, 
        base_url=req.base_url,
        db=db, 
        project_id=req.project_id,
        user_id=current_user.id
    )
    return {"script": script, **result_data}

@app.post("/api/evaluate")
def evaluate_content(req: EvalRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify project
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    result = evaluator.evaluate_test_quality(req.content, db, req.project_id, user_id=current_user.id)
    return {"result": result}

@app.post("/api/calculate-recall")
def calculate_recall(req: RecallRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    recall = evaluator.calculate_recall(req.retrieved, req.relevant, db, req.project_id, user_id=current_user.id)
    return {"recall": recall}

@app.post("/api/evaluate-ui-automation")
def evaluate_ui_automation(req: UIAutoEvalRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    result = evaluator.evaluate_ui_automation(req.script, req.execution_result, db, req.project_id, user_id=current_user.id)
    return {"result": result}

@app.post("/api/evaluate-api-test")
def evaluate_api_test(req: APITestEvalRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    result = evaluator.evaluate_api_test(req.script, req.execution_result, db, req.project_id, user_id=current_user.id)
    return {"result": result}

@app.post("/api/compare-test-cases")
def compare_test_cases(req: TestComparisonRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    result = evaluator.compare_test_cases(req.generated_test_case, req.modified_test_case, db, req.project_id, user_id=current_user.id)
    return {"result": result}

@app.post("/api/upload-knowledge")
async def upload_knowledge(
    file: UploadFile = File(...), 
    doc_type: str = Form(...), 
    project_id: int = Form(...),
    force: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        # Verify project
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        content = await parse_file_content(file)
        # Run sync DB operation in threadpool
        from fastapi.concurrency import run_in_threadpool
        result = await run_in_threadpool(
            knowledge_base.add_document, 
            file.filename, content, doc_type, project_id, db, force=force, user_id=current_user.id
        )
        if isinstance(result, dict) and result.get("status") == "duplicate":
            return result
        return {"status": "success", "id": result.id, "filename": result.filename}

    except ValueError as e:
        return {"error": str(e)}

@app.get("/api/knowledge-list")
def get_knowledge_list(
    project_id: int,
    page: int = 1,
    page_size: int = 6,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    doc_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify project
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    docs = knowledge_base.get_documents_list(db, project_id, search, start_date, end_date)
    
    # Source documents used for pagination (requirements / 残缺类文档)
    # 作为“可被关联”的父文档：包含需求文档、旧版残缺文档以及产品需求
    source_docs = [d for d in docs if d.doc_type in ['requirement', 'incomplete', 'product_requirement']]
    
    # Apply doc_type filter if provided
    if doc_type:
        # Frontend still使用 doc_type = 'incomplete' 表示“残缺文档”分类，
        # 这里需要将其映射为多种实际子类型
        if doc_type == "incomplete":
            # “残缺文档”筛选包含旧的 incomplete 以及新的 product_requirement
            source_docs = [d for d in source_docs if d.doc_type in ['incomplete', 'product_requirement']]
        else:
            source_docs = [d for d in source_docs if d.doc_type == doc_type]
    
    # Child documents：测试用例和原型图
    # Logic:
    # 1. If doc_type is specified, only include matching types.
    # 2. If doc_type is NOT specified (default view), only include ORPHANED child docs (hide associated ones to avoid duplication).
    all_child_docs = [d for d in docs if d.doc_type in ['test_case', 'prototype']]
    
    if doc_type:
        child_docs = [d for d in all_child_docs if d.doc_type == doc_type]
    else:
        # Hide associated test cases in main list view
        child_docs = [d for d in all_child_docs if not d.source_doc_id]
    
    # Pagination logic for source documents
    total_source_docs = len(source_docs)
    total_pages = (total_source_docs + page_size - 1) // page_size
    
    # Calculate offset and limit for source documents
    offset = (page - 1) * page_size
    paginated_source_docs = source_docs[offset : offset + page_size]
    
    # Combine paginated source documents with all child documents
    paginated_docs = paginated_source_docs + child_docs
    
    # Pre-fetch source filenames to avoid N+1 issues or lazy load errors in dict comp
    # Or just construct the list manually
    result = []
    
    # Create a lookup for filenames
    id_map = {d.id: d.filename for d in docs}
    
    for d in paginated_docs:
        source_name = id_map.get(d.source_doc_id) if d.source_doc_id else None
        
        # Create a preview of content (first 500 chars)
        # Clean up control characters that might cause encoding issues in JSON/Frontend
        raw_preview = d.content[:500] + "..." if d.content and len(d.content) > 500 else (d.content or "")
        # Remove null bytes and other non-printable chars (simple filter)
        content_preview = "".join(ch for ch in raw_preview if ch.isprintable() or ch in ('\n', '\r', '\t'))

        # Get linked test cases details for requirement documents
        linked_test_cases = []
        if d.doc_type in ['requirement', 'product_requirement', 'incomplete']:
            # Use relationship to get linked documents
            for ld in d.linked_docs:
                if ld.doc_type == 'test_case':
                    # Create a preview for the linked doc
                    raw_ld = ld.content[:500] + "..." if ld.content and len(ld.content) > 500 else (ld.content or "")
                    clean_ld = "".join(ch for ch in raw_ld if ch.isprintable() or ch in ('\n', '\r', '\t'))
                    linked_test_cases.append({
                        "id": ld.project_specific_id,
                        "global_id": ld.id,
                        "filename": ld.filename,
                        "content_preview": clean_ld
                    })

        result.append({
            "id": d.project_specific_id,
            "global_id": d.id,
            "filename": d.filename,
            "doc_type": d.doc_type,
            "created_at": d.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "source_doc_id": d.source_doc_id,
            "source_doc_name": source_name,
            "content_preview": content_preview,
            "linked_test_cases": linked_test_cases
        })
    
    return {
        "documents": result,
        "pagination": {
            "total": total_source_docs,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages
        }
    }

@app.get("/api/knowledge/{doc_id}")
def get_knowledge_detail(doc_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Find document by project_specific_id and current project context
    # This assumes we get project_id from the request or session
    # For now, let's keep using global_id for direct access, but update the response
    doc = db.query(KnowledgeDocument).join(Project).filter(KnowledgeDocument.id == doc_id, Project.user_id == current_user.id).first()
    if not doc:
        return {"error": "Document not found"}
        
    # Sanitize content
    raw_content = doc.content or ""
    sanitized_content = "".join(ch for ch in raw_content if ch.isprintable() or ch in ('\n', '\r', '\t'))
    
    # Get linked documents (test cases)
    linked_docs = []
    if doc.linked_docs:
        for ld in doc.linked_docs:
            if ld.doc_type == 'test_case':
                raw_ld_content = ld.content or ""
                sanitized_ld_content = "".join(ch for ch in raw_ld_content if ch.isprintable() or ch in ('\n', '\r', '\t'))
                linked_docs.append({
                    "id": ld.project_specific_id,
                    "global_id": ld.id,
                    "filename": ld.filename,
                    "content": sanitized_ld_content
                })

    return {
        "id": doc.project_specific_id,
        "global_id": doc.id,
        "filename": doc.filename,
        "content": sanitized_content,
        "linked_docs": linked_docs
    }

class RelationUpdate(BaseModel):
    doc_id: int
    source_doc_id: int

@app.post("/api/knowledge/update-relation")
def update_relation(req: RelationUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify doc
    doc = db.query(KnowledgeDocument).join(Project).filter(KnowledgeDocument.id == req.doc_id, Project.user_id == current_user.id).first()
    if not doc:
        return {"success": False, "error": "Document not found"}

    # For relation updates, we need to use global_id
    # Let's update the knowledge_base.update_relation method to handle this
    success = knowledge_base.update_relation(req.doc_id, req.source_doc_id, db)
    return {"success": success}

@app.put("/api/knowledge/{doc_id}")
def update_knowledge(doc_id: int, req: KnowledgeUpdateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify doc
    doc = db.query(KnowledgeDocument).join(Project).filter(KnowledgeDocument.id == doc_id, Project.user_id == current_user.id).first()
    if not doc:
        return {"error": "Document not found"}

    result = knowledge_base.update_document(doc_id, req.filename, req.content, req.doc_type, db)
    if not result:
        return {"error": "Document not found"}
    return {"status": "success", "id": result.id, "filename": result.filename}

@app.delete("/api/knowledge/{doc_id}")
def delete_knowledge(doc_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Get doc info before delete for logging
    doc = db.query(KnowledgeDocument).join(Project).filter(KnowledgeDocument.id == doc_id, Project.user_id == current_user.id).first()
    if not doc:
        return {"error": "Document not found"}

    filename = doc.filename
    project_id = doc.project_id

    success = knowledge_base.delete_document(doc_id, db)
    if not success:
        return {"error": "Document not found"}
        
    if project_id:
        try:
            log_to_db(db, project_id, "system", f"Knowledge Base: Deleted document '{filename}' (ID: {doc_id})", user_id=current_user.id)
        except: pass
        
    return {"status": "success", "message": "Document deleted successfully"}

@app.post("/api/knowledge/clean-cross-project")
def clean_cross_project_associations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    cleaned_count = knowledge_base.clean_cross_project_associations(db)
    return {"status": "success", "cleaned_count": cleaned_count, "message": f"Cleaned {cleaned_count} invalid cross-project associations"}

# --- Log APIs ---
@app.post("/api/logs")
def create_log(log: LogCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify project
    project = db.query(Project).filter(Project.id == log.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    new_log = LogEntry(
        project_id=log.project_id,
        log_type=log.log_type,
        message=log.message,
        user_id=current_user.id
    )
    db.add(new_log)
    db.commit()
    db.refresh(new_log)
    return {"status": "success", "id": new_log.id}

@app.delete("/api/logs/{project_id}")
def delete_logs(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify project
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        db.query(LogEntry).filter(LogEntry.project_id == project_id).delete()
        db.commit()
        return {"status": "success", "message": "Logs cleared"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/logs/{project_id}", response_model=List[LogRead])
def get_logs(project_id: int, log_type: Optional[str] = None, severity: Optional[str] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify project
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        query = db.query(LogEntry).filter(LogEntry.project_id == project_id)
        if log_type:
            query = query.filter(LogEntry.log_type == log_type)
        logs = query.order_by(LogEntry.created_at.desc()).limit(200).all()
        if severity in ("error", "ok"):
            filtered = []
            error_keywords = ["Error", "Exception", "[QUOTA_EXHAUSTED]", "请求失败", "响应解析失败", "json 生成失败", "额度耗尽", "API 错误", "API Error"]
            for l in logs:
                msg = l.message or ""
                is_err = any(k in msg for k in error_keywords)
                if severity == "error" and is_err:
                    filtered.append(l)
                if severity == "ok" and not is_err:
                    filtered.append(l)
            return filtered
        return logs
    except Exception as e:
        logger.error(f"Error fetching logs for project {project_id}: {e}")
        # Return empty list on error to prevent frontend crash
        return []

@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    mysql_ok = True
    mysql_details = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        mysql_ok = False
        mysql_details = str(e)
        print(f"Health check MySQL error: {e}")

    redis_ok = False
    redis_details = ""
    try:
        with socket.create_connection((settings.REDIS_HOST, settings.REDIS_PORT), timeout=1) as s:
            s.sendall(b"PING\r\n")
            resp = s.recv(128)
            if resp and b"PONG" in resp:
                redis_ok = True
                redis_details = "PONG"
            else:
                redis_details = resp.decode(errors="ignore")
    except Exception as e:
        redis_details = str(e)
        print(f"Health check Redis error: {e}")

    return {
        "mysql": {"ok": mysql_ok, "details": mysql_details},
        "redis": {"ok": redis_ok, "details": redis_details, "host": getattr(settings, "REDIS_HOST", "Unknown"), "port": getattr(settings, "REDIS_PORT", "Unknown")},
    }

from fastapi.responses import StreamingResponse

from core.models import TestGeneration
from sqlalchemy import desc

@app.get("/api/test-generations/{gen_id}")
def get_test_generation(gen_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    gen = db.query(TestGeneration).filter(TestGeneration.id == gen_id, TestGeneration.user_id == current_user.id).first()
    if not gen:
        raise HTTPException(status_code=404, detail="Test generation not found")
    try:
        return json.loads(gen.generated_result)
    except:
        return {"error": "Failed to parse stored JSON", "raw": gen.generated_result}

@app.post("/api/generate-tests-stream")
async def generate_tests_stream(
    file: UploadFile = File(None),
    requirement_text: str = Form(None),
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    prototype_file: UploadFile = File(None),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    force: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify project
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    async def stream_generator():
        try:
            content = ""
            
            if file:
                 yield f"正在解析文件 {file.filename}，请稍候...\n"
                 base_prompt = "OCR: Extract all text from this image."
                 try:
                    content = await parse_file_content(file, base_prompt)
                    yield "文件解析完成。\n"
                 except Exception as e:
                    yield f"文件解析失败: {e}\n"
                    logger.error(f"File parse error: {e}")
                    return

                 # Add to KB
                 yield "正在存入知识库...\n"
                 try:
                    kb_add = knowledge_base.add_document(file.filename, content, doc_type, project_id, db, force=force)
                    if isinstance(kb_add, dict) and kb_add.get("status") == "duplicate":
                        # Check for existing generation result
                        existing_gen = db.query(TestGeneration).filter(
                            TestGeneration.project_id == project_id,
                            TestGeneration.requirement_text == content,
                            TestGeneration.user_id == current_user.id
                        ).order_by(desc(TestGeneration.created_at)).first()

                        # Fallback: if exact match fails, try to use content from the existing KnowledgeDocument
                        if not existing_gen and kb_add.get("existing_doc_id"):
                            existing_doc = db.query(KnowledgeDocument).filter(
                                KnowledgeDocument.id == kb_add.get("existing_doc_id")
                            ).first()
                            if existing_doc and existing_doc.content:
                                existing_gen = db.query(TestGeneration).filter(
                                    TestGeneration.project_id == project_id,
                                    TestGeneration.requirement_text == existing_doc.content,
                                    TestGeneration.user_id == current_user.id
                                ).order_by(desc(TestGeneration.created_at)).first()

                        if existing_gen and existing_gen.generated_result:
                            yield f"@@DUPLICATE@@:{{\"id\": {existing_gen.id}}}"
                            return
                        else:
                            yield f"【提示】文档 '{kb_add.get('existing_filename')}' 内容未发生变化，但未找到历史生成结果，将重新生成。\n"
                    else:
                        yield "知识库更新完成。\n"
                 except Exception as e:
                    yield f"存入知识库失败 (不影响生成): {e}\n"
                    logger.error(f"Failed to add to KB: {e}")

            elif requirement_text:
                 content = requirement_text
                 yield "已接收需求文本。\n"
                 
                 # BUG1 Fix: Check for duplicate requirement_text if not forced
                 if not force:
                     existing_gen = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text == content,
                        TestGeneration.user_id == current_user.id
                     ).order_by(desc(TestGeneration.created_at)).first()
                     
                     if existing_gen and existing_gen.generated_result:
                        yield f"@@DUPLICATE@@:{{\"id\": {existing_gen.id}}}"
                        return

            
            if doc_type == "incomplete" and prototype_file:
                 yield "正在解析原型图...\n"
                 proto_prompt = (
                    "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
                    "Identify input fields, buttons, navigation menus, and any visual indicators of state."
                )
                 try:
                    proto_text = await parse_file_content(prototype_file, proto_prompt)
                    content = f"{content}\n\n[Prototype Analysis]\n{proto_text}"
                    yield "原型图解析完成。\n"
                 except Exception as e:
                    yield f"原型图解析失败: {e}\n"
                    logger.error(f"Prototype parse error: {e}")
            
            if not content:
                yield "错误: 未提供有效内容。\n"
                return

            yield "正在根据内容生成测试用例，AI 思考中...\n\n"
            
            full_response_content = ""
            async for chunk in test_generator.generate_test_cases_stream(
                content, 
                project_id, 
                db, 
                doc_type, 
                compress, 
                expected_count,
                overwrite=force,
                user_id=current_user.id
            ):
                full_response_content += chunk
                yield chunk
            
            # Post-processing: Saving is handled by test_generation module.
        except Exception as e:
            logger.error(f"Stream generation error: {e}")
            yield f"\n\n生成过程中发生错误: {str(e)}"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream"
    )

@app.get("/api/health/redis-test")
async def redis_port_test(host: Optional[str] = None, port: Optional[int] = None):
    h = host or settings.REDIS_HOST
    p = port or settings.REDIS_PORT
    ok = False
    details = ""
    try:
        with socket.create_connection((h, int(p)), timeout=1) as s:
            s.sendall(b"PING\r\n")
            resp = s.recv(128)
            ok = True if (resp and b"PONG" in resp) else True
            details = resp.decode(errors="ignore") if resp else "Connected"
    except Exception as e:
        details = str(e)
    return {"ok": ok, "details": details, "host": h, "port": p}

class ConfigValidateRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: str

class ConfigSaveRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: str

class ConfigDetectRequest(BaseModel):
    candidates: List[str]

@app.post("/api/config/validate")
async def validate_config(req: ConfigValidateRequest):
    try:
        # Create a temporary config object to use with AIClient factory
        temp_config = SystemConfig(
            provider=req.provider,
            model_name=req.model_name,
            # We need to manually handle key here because AIClient expects decrypted or handles it
            # But AIClient.from_config expects a SystemConfig with encrypted key (which it decrypts)
            # OR we can manually instantiate provider.
            base_url=req.base_url
        )
        
        # Manually create provider to avoid DB dependency/encryption complexity for validation
        provider = None
        if req.provider == "dashscope":
            provider = DashScopeProvider(req.api_key or "")
        elif req.provider in ["openai", "ollama", "local"]:
            provider = OpenAICompatibleProvider(
                base_url=req.base_url or "",
                api_key=req.api_key or "",
                model=req.model_name
            )
        
        if not provider:
            return {"valid": False, "error": f"Unknown provider: {req.provider}"}
            
        result = provider.test_connection()
        
        response_data = {"valid": result["success"], "details": result}
        
        if not result["success"]:
            # Extract readable error for frontend
            error_info = result.get("error", "Unknown error")
            if isinstance(error_info, dict):
                response_data["error"] = error_info.get("message", str(error_info))
            else:
                response_data["error"] = str(error_info)
                
        return response_data
        
    except Exception as e:
        logger.error(f"Config validation error: {str(e)}")
        return {"valid": False, "error": f"Validation exception: {str(e)}"}

@app.post("/api/config/save")
async def save_config(req: ConfigSaveRequest, db: Session = Depends(get_db)):
    try:
        # Create and activate config
        new_config = config_manager.create_config(
            db, 
            provider=req.provider,
            model_name=req.model_name,
            api_key=req.api_key,
            base_url=req.base_url,
            activate=True
        )
        
        # Update global AI Client
        new_client = ai_client.from_config(new_config)
        ai_client.update_provider(new_client.provider, new_client.model)
        
        return {"status": "success", "id": new_config.id}
    except Exception as e:
        logger.error(f"Config save error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/config/detect")
async def detect_local_services(req: ConfigDetectRequest):
    """
    Parallel detection of local services.
    """
    results = []
    
    # Use a single client for connection pooling
    async with httpx.AsyncClient(timeout=0.5) as client:
        async def check_url(url):
            try:
                # Try /v1/models or just /v1
                target = url.rstrip('/')
                if not target.endswith('/v1'):
                    target += '/v1'
                
                # Some services might not respond to GET /v1, try /v1/models
                try:
                    resp = await client.get(f"{target}/models")
                    if resp.status_code == 200:
                        data = resp.json()
                        models = []
                        if "data" in data:
                            models = data["data"]
                        return {
                            "url": url,
                            "success": True, 
                            "latency": 0, # Placeholder
                            "models": models
                        }
                except:
                    pass
                
                # Fallback to simple health check
                return {"url": url, "success": False, "error": "Not reachable"}
                    
            except Exception as e:
                return {"url": url, "success": False, "error": str(e)}

        # Run checks in parallel
        tasks = [check_url(url) for url in req.candidates]
        scan_results = await asyncio.gather(*tasks)
    
    # Filter successful ones
    valid_services = [r for r in scan_results if r.get("success")]
    
    return {"services": valid_services}

@app.get("/api/config/test-stream")
async def test_stream(
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    prompt: str = "Hi"
):
    """
    SSE endpoint for testing streaming response
    """
    async def event_generator():
        try:
            # Instantiate provider
            prov = None
            if provider == "dashscope":
                prov = DashScopeProvider(api_key or "")
            elif provider in ["openai", "ollama", "local"]:
                prov = OpenAICompatibleProvider(
                    base_url=base_url or "",
                    api_key=api_key or "",
                    model=model
                )
            
            if not prov:
                yield f"data: {json.dumps({'error': 'Unknown provider'})}\n\n"
                return

            # Generate stream
            # Note: Provider generate_stream might be sync generator, wrap it
            # But OpenAICompatibleProvider.generate_stream is sync generator yielding strings
            # We need to make it async compatible for StreamingResponse
            
            # Actually, FastAPI StreamingResponse accepts sync generators too, but it runs them in threadpool.
            # For better performance, we should iterate.
            
            iterator = prov.generate_stream([{"role": "user", "content": prompt}], model, max_tokens=50)
            
            for chunk in iterator:
                if chunk.startswith("Error:"):
                     yield f"data: {json.dumps({'error': chunk})}\n\n"
                else:
                     yield f"data: {json.dumps({'token': chunk})}\n\n"
                # Add small delay to simulate typing effect if it's too fast (local models)
                # await asyncio.sleep(0.02) 
            
            yield f"data: {json.dumps({'done': True})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/config/current")
def get_current_config(db: Session = Depends(get_db)):
    config = config_manager.get_active_config(db)
    if not config:
        return {"active": False}
    
    return {
        "active": True,
        "provider": config.provider,
        "model_name": config.model_name,
        "vl_model_name": config.vl_model_name,
        "turbo_model_name": config.turbo_model_name,
        "base_url": config.base_url,
        "has_api_key": bool(config.api_key)
    }

@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    if full_path == "api" or full_path.startswith("api/") or full_path == "static" or full_path.startswith("static/"):
        raise HTTPException(status_code=404)
    
    # DYNAMIC PROXY MODE (No 'dist' dependency)
    # Always try to proxy to Vite dev server (localhost:5173)
    dev_server_url = "http://localhost:5173"
    
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            target_url = f"{dev_server_url}/{full_path}"
            # If root, get index.html
            if full_path == "":
                target_url = f"{dev_server_url}/"
            
            try:
                resp = await client.get(target_url)
            except httpx.ConnectError:
                # If dev server not running, show friendly error
                return HTMLResponse(
                    "<h1>Frontend Dev Server not running (port 5173).</h1>"
                    "<p>Please run <code>npm run dev</code> in frontend directory.</p>"
                    "<p>Since 'dist' dependency is removed, the dev server is required.</p>"
                )
            
            # If 404 from dev server, and looks like SPA route (no dot), try index.html
            if resp.status_code == 404 and "." not in full_path:
                resp = await client.get(f"{dev_server_url}/index.html")
            
            # Forward headers but exclude those that might conflict with the decompressed content
            # httpx automatically decodes gzip, so we MUST remove Content-Encoding
            excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
            headers = {
                k: v for k, v in resp.headers.items() 
                if k.lower() not in excluded_headers
            }

            return Response(
                content=resp.content, 
                status_code=resp.status_code, 
                media_type=resp.headers.get("content-type"),
                headers=headers
            )
    except Exception as e:
        return HTMLResponse(f"<h1>Proxy Error</h1><p>{str(e)}</p>")

if __name__ == "__main__":
    # Try to init db on startup if possible
    try:
        from init_db import init_db
        init_db()
    except:
        pass
    uvicorn.run(app, host="0.0.0.0", port=8000)
