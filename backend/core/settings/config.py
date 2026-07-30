#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置模块

该模块定义了项目的所有配置参数，包括：
1. AI模型配置
2. 数据库配置
3. UI自动化配置
4. API测试配置

所有配置参数集中管理，便于维护和修改。
"""

import os
import urllib.parse
import logging
from dotenv import load_dotenv

# 优先加载后端目录下 .env，其次加载仓库根目录 .env
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(_BACKEND_DIR), ".env"))


_logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        _logger.warning("Invalid integer env %s=%r; using default=%s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        _logger.warning("Env %s=%r below minimum=%s; using minimum", name, raw, minimum)
        return minimum
    if maximum is not None and value > maximum:
        _logger.warning("Env %s=%r above maximum=%s; using maximum", name, raw, maximum)
        return maximum
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        _logger.warning("Invalid float env %s=%r; using default=%s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        _logger.warning("Env %s=%r below minimum=%s; using minimum", name, raw, minimum)
        return minimum
    if maximum is not None and value > maximum:
        _logger.warning("Env %s=%r above maximum=%s; using maximum", name, raw, maximum)
        return maximum
    return value


class Config:
    """配置类，包含项目所有配置参数"""
    ENV = os.getenv("APP_ENV", os.getenv("ENV", "development")).lower()
    IS_DEVELOPMENT = ENV in {"dev", "development", "local"}
    
    # ===========================
    # AI模型配置
    # ===========================
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")  # DashScope API密钥
    EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()
    EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "").strip()
    EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "").strip()
    EMBEDDING_API_KEY_ENV = os.getenv("EMBEDDING_API_KEY_ENV", "").strip()
    EMBEDDING_TIMEOUT_SECONDS = _env_float("EMBEDDING_TIMEOUT_SECONDS", 30.0, minimum=1.0)
    MODEL_NAME = os.getenv("MODEL_NAME", "").strip()
    VL_MODEL_NAME = os.getenv("VL_MODEL_NAME", "").strip()
    TURBO_MODEL_NAME = os.getenv("TURBO_MODEL_NAME", "").strip()
    MAX_TOKENS = _env_int("MAX_TOKENS", 10000, minimum=1)  # 最大输出token数

    # 测试用例生成的逻辑批次大小，JSON 与 Stream 入口共用同一默认值。
    TEST_GENERATION_BATCH_SIZE = _env_int(
        "TEST_GENERATION_BATCH_SIZE",
        25,
        minimum=1,
        maximum=200,
    )
    
    # ===========================
    # 数据库配置
    # ===========================
    DB_USER = os.getenv("DB_USER", os.getenv("MYSQL_USER", "root"))  # 数据库用户名
    DB_PASSWORD_RAW = os.getenv("DB_PASSWORD", os.getenv("MYSQL_PASSWORD", ""))  # 数据库密码（原始）
    DB_PASSWORD = urllib.parse.quote_plus(DB_PASSWORD_RAW)  # 数据库密码（URL编码）
    DB_HOST = os.getenv("DB_HOST", os.getenv("MYSQL_HOST", "localhost"))  # 数据库主机
    DB_PORT = os.getenv("DB_PORT", os.getenv("MYSQL_PORT", "3306"))  # 数据库端口
    DB_NAME = os.getenv("DB_NAME", os.getenv("MYSQL_DATABASE", "ai_test_platform"))  # 数据库名称

    # 处理用户名中的特殊字符（如果有）
    DB_USER_ENCODED = urllib.parse.quote_plus(DB_USER)
    
    # 数据库连接URL
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        DATABASE_URL = f"mysql+pymysql://{DB_USER_ENCODED}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

    # ===========================
    # UI自动化配置
    # ===========================
    HEADLESS_MODE = True  # 是否启用无头模式（无界面运行浏览器）
    
    # ===========================
    # API测试配置
    # ===========================
    DEFAULT_TIMEOUT = 10  # API测试默认超时时间（秒）

    # ===========================
    # Redis配置（用于健康检查）
    # ===========================
    REDIS_URL = os.getenv("REDIS_URL", "").strip()
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = _env_int("REDIS_PORT", 6379, minimum=1, maximum=65535)
    REDIS_DB = _env_int("REDIS_DB", 0, minimum=0)
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

    # ===========================
    # 安全配置
    # ===========================
    ENABLE_DIAGNOSTIC_ROUTES = _env_flag("ENABLE_DIAGNOSTIC_ROUTES", IS_DEVELOPMENT)
    SECRET_KEY = os.getenv("SECRET_KEY")
    if not SECRET_KEY:
        if ENV in {"prod", "production"}:
            raise RuntimeError("SECRET_KEY environment variable is required in production")
        SECRET_KEY = "dev-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30 days

    # ===========================
    # Stream generation coverage shard flags
    # ===========================
    # 单批达到公共批大小时默认启用内部覆盖分片，避免把 25 条用例压成一次长响应。
    GENERATION_STREAM_COVERAGE_SHARDS_ENABLED = _env_flag(
        "GENERATION_STREAM_COVERAGE_SHARDS_ENABLED",
        True,
    )
    GENERATION_STREAM_COVERAGE_SHARD_MAX_WORKERS = _env_int(
        "GENERATION_STREAM_COVERAGE_SHARD_MAX_WORKERS",
        2,
        minimum=1,
        maximum=4,
    )
    GENERATION_STREAM_COVERAGE_SHARD_MIN_EXPECTED_COUNT = _env_int(
        "GENERATION_STREAM_COVERAGE_SHARD_MIN_EXPECTED_COUNT",
        TEST_GENERATION_BATCH_SIZE,
        minimum=1,
        maximum=500,
    )
    GENERATION_STREAM_COVERAGE_SHARD_MIN_RULES = _env_int(
        "GENERATION_STREAM_COVERAGE_SHARD_MIN_RULES",
        8,
        minimum=1,
        maximum=100,
    )
    GENERATION_STREAM_COVERAGE_SHARD_DUPLICATE_RATE_ABORT = _env_float(
        "GENERATION_STREAM_COVERAGE_SHARD_DUPLICATE_RATE_ABORT",
        0.25,
        minimum=0.0,
        maximum=1.0,
    )
    GENERATION_STREAM_COVERAGE_SHARD_MIN_UNIQUE_RATIO = _env_float(
        "GENERATION_STREAM_COVERAGE_SHARD_MIN_UNIQUE_RATIO",
        0.45,
        minimum=0.0,
        maximum=1.0,
    )

    # ===========================
    # Execution plan persistence gate
    # ===========================
    EXECUTION_PLAN_GATE_MODE = os.getenv("EXECUTION_PLAN_GATE_MODE", "enforce").strip().lower()
    CASE_QUALITY_ENFORCE_MIN_ACCEPTABLE_FINAL = _env_flag(
        "CASE_QUALITY_ENFORCE_MIN_ACCEPTABLE_FINAL",
        False,
    )


# 创建配置实例
settings = Config()
