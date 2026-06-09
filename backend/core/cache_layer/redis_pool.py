"""
Redis 连接池配置模块 (Redis Pool Configuration)

提供全局共享的 Redis 连接池，用于管理 Redis 连接的复用和生命周期。
主要用于 Celery 任务队列和应用层的缓存操作。
"""

import logging
import os
from redis import ConnectionPool


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Invalid integer env %s=%r; using default=%s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("Env %s=%r below minimum=%s; using minimum", name, raw, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("Env %s=%r above maximum=%s; using maximum", name, raw, maximum)
        return maximum
    return value

# Get Redis configuration from environment variables
# (从环境变量获取 Redis 配置)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = _env_int("REDIS_PORT", 6379, minimum=1, maximum=65535)
REDIS_DB = _env_int("REDIS_DB", 0, minimum=0)
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Create a shared connection pool
# (创建共享连接池)
# max_connections=100 ensures we don't exhaust Redis connections (防止耗尽连接)
# health_check_interval=30 ensures we don't use dead connections (定期健康检查)
redis_pool = ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True,
    max_connections=100,
    health_check_interval=30
)
