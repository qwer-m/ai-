#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库连接模块

该模块负责处理数据库连接，包括：
1. MySQL数据库连接尝试
2. 数据库会话管理
3. 数据库模型基类定义

当前仅支持 MySQL，连接失败时会直接抛错并终止启动。
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from core.config import settings
import logging


# ===========================
# 日志配置
# ===========================
logging.basicConfig()
logger = logging.getLogger('sqlalchemy.engine')
logger.setLevel(logging.WARNING)  # 设置SQLAlchemy日志级别


# ===========================
# 数据库连接
# ===========================
# 仅支持 MySQL
if "mysql" not in settings.DATABASE_URL:
    raise RuntimeError(
        f"Only MySQL is supported. Current DATABASE_URL is not mysql: {settings.DATABASE_URL}"
    )

# 处理MySQL连接URL，确保正确的字符集设置
if "charset=" not in settings.DATABASE_URL:
    database_url = f"{settings.DATABASE_URL}?charset=utf8mb4"
else:
    database_url = settings.DATABASE_URL

# 创建数据库引擎
# 针对大规模数据库（2千张表）的优化配置
engine = create_engine(
    database_url,
    # 连接池配置优化
    pool_size=50,  # 连接池大小，根据服务器资源和并发需求调整
    max_overflow=100,  # 最大溢出连接数，处理突发高并发
    pool_pre_ping=True,  # 连接池预检查，确保连接有效
    pool_recycle=3600,  # 连接回收时间，避免连接长时间占用
    pool_timeout=30,  # 连接池超时时间
    # 执行优化
    echo=False,  # 关闭SQL语句日志，提高性能
    echo_pool=False,  # 关闭连接池日志
    # 连接参数，设置超时时间和字符集
    connect_args={
        "connect_timeout": 3,  # 连接超时时间
        "charset": "utf8mb4",  # 支持emoji等特殊字符
        "sql_mode": "STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION",  # 严格SQL模式
        "init_command": "SET NAMES utf8mb4",  # 初始化命令
        "read_timeout": 60,  # 读超时时间
        "write_timeout": 60,  # 写超时时间
    }
)

# 测试数据库连接
try:
    with engine.connect() as conn:
        pass  # 连接成功，不执行任何操作
    print(f"Successfully connected to MySQL database: {settings.DB_NAME}")
except Exception as e:
    raise RuntimeError(
        f"Could not connect to MySQL database: {e}. "
        "Please set valid DATABASE_URL / DB_* / MYSQL_* environment variables."
    ) from e


# ===========================
# 会话管理
# ===========================
# 创建数据库会话工厂
# 针对大规模数据库（2千张表）的优化配置
SessionLocal = sessionmaker(
    autocommit=False,  # 不自动提交事务，需要手动commit
    autoflush=False,   # 不自动刷新会话，避免不必要的数据库查询
    bind=engine,       # 绑定到前面创建的数据库引擎
    expire_on_commit=False,  # 提交后不失效对象，提高性能
    
    # SQLAlchemy 2.0+ 优化选项
    future=True,       # 使用SQLAlchemy 2.0+ API
    # 其他可选优化
    # twophase=False,   # 不使用两阶段提交
    # binds=None,       # 不使用多绑定
    # enable_baked_queries=False,  # 禁用baked查询（SQLAlchemy 1.x特性，2.x已废弃）
)


# ===========================
# 模型基类
# ===========================
# 创建数据库模型基类，所有ORM模型都继承自这个基类
Base = declarative_base()


# ===========================
# 数据库依赖
# ===========================
def get_db():
    """
    获取数据库会话的依赖函数
    
    用于FastAPI路由中获取数据库会话，自动管理会话的创建和关闭。
    
    Yields:
        Session: 数据库会话对象
    """
    db = SessionLocal()  # 创建新的数据库会话
    try:
        yield db  # 提供会话给路由函数使用
    finally:
        db.close()  # 无论路由函数执行成功或失败，都会关闭会话
