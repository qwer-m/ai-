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
from .environment import load_environment

load_environment()


_logger = logging.getLogger(__name__)


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
    # 额度按每个已激活 Agent 实例独立计算；Run 级只累计用量，不共享一份阻断额度。
    # 当前按需求临时放开单实例 token 上限；仍按 Agent 实例独立记账，可由环境变量覆盖。
    AGENT_RUN_MAX_REQUESTS = _env_int("AGENT_RUN_MAX_REQUESTS", 80, minimum=1)
    AGENT_RUN_MAX_INPUT_TOKENS = _env_int(
        "AGENT_RUN_MAX_INPUT_TOKENS", 2000000, minimum=1
    )
    AGENT_RUN_MAX_OUTPUT_TOKENS = _env_int(
        "AGENT_RUN_MAX_OUTPUT_TOKENS", 800000, minimum=1
    )
    AGENT_RUN_MAX_TOTAL_TOKENS = _env_int(
        "AGENT_RUN_MAX_TOTAL_TOKENS", 2800000, minimum=1
    )
    # 上游模型网关在高并发下更容易排队超时；允许部署侧限制映射节点的实际并发。
    AGENT_MAP_MAX_CONCURRENCY = _env_int(
        "AGENT_MAP_MAX_CONCURRENCY", 6, minimum=1, maximum=16
    )
    # 并发映射发生局部慢请求时保留最小吞吐，避免整个阶段退化为串行。
    AGENT_MAP_MIN_CONCURRENCY = _env_int(
        "AGENT_MAP_MIN_CONCURRENCY", 2, minimum=1, maximum=16
    )
    # 累计多个上游压力信号后再降一级并发，单个离群请求不应拖慢整个批次。
    AGENT_MAP_CONCURRENCY_PRESSURE_FAILURES = _env_int(
        "AGENT_MAP_CONCURRENCY_PRESSURE_FAILURES", 2, minimum=1, maximum=10
    )
    # 上游压力降载后，连续成功达到该数量再恢复一级并发，避免立即回冲。
    AGENT_MAP_CONCURRENCY_RECOVERY_SUCCESSES = _env_int(
        "AGENT_MAP_CONCURRENCY_RECOVERY_SUCCESSES", 6, minimum=1, maximum=100
    )
    # map 首轮请求优先快速失败并换路重试；后续尝试仍使用 Agent 自身完整超时。
    AGENT_MAP_FIRST_ATTEMPT_TIMEOUT_SECONDS = _env_float(
        "AGENT_MAP_FIRST_ATTEMPT_TIMEOUT_SECONDS", 120.0, minimum=10.0, maximum=600.0
    )
    # 单次真实运行需覆盖分批来源分析、生成和多轮独立终审，保留一小时整轮边界。
    AGENT_RUN_DEADLINE_SECONDS = _env_int(
        "AGENT_RUN_DEADLINE_SECONDS", 3600, minimum=60, maximum=3600
    )
    # 运行租约只用于识别失联执行器，必须明显短于整轮执行预算。
    AGENT_RUN_LEASE_SECONDS = _env_int(
        "AGENT_RUN_LEASE_SECONDS", 120, minimum=30, maximum=600
    )
    # 每个需求来源只保留有限数量的终态运行；不同需求文档必须各自保留可复用结果。
    AGENT_RUN_HISTORY_LIMIT = _env_int(
        "AGENT_RUN_HISTORY_LIMIT", 1, minimum=1, maximum=20
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
    SECRET_KEY = os.getenv("SECRET_KEY")
    if not SECRET_KEY:
        if ENV in {"prod", "production"}:
            raise RuntimeError("SECRET_KEY environment variable is required in production")
        SECRET_KEY = "dev-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30 days

# 创建配置实例
settings = Config()
