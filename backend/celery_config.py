"""
Celery 配置模块 (Celery Config)

该模块配置 Celery 异步任务队列，包括 Broker (Redis)、Result Backend (Redis) 以及定时任务 (Beat)。
主要功能：
1. 初始化 Celery 应用实例。
2. 配置 Redis 连接池。
3. 定义定时任务 (Beat Schedule)：
   - 每周日凌晨 3 点归档旧数据。
   - 每小时清理过期日志。
   
调用关系：
- 依赖 `core.cache_layer.redis_pool` 复用 Redis 连接。
- 自动发现 `modules.orchestration.tasks` 中的任务。
"""

import os
from urllib.parse import quote
from celery import Celery
from core.cache_layer.redis_pool import redis_pool
from modules.orchestration.task_names import TaskName

# Initialize Celery app
celery_app = Celery("ai_test_platform")

from celery.schedules import crontab

DEFAULT_VISIBILITY_TIMEOUT_SECONDS = 7200
CELERY_VISIBILITY_TIMEOUT_SECONDS = int(
    os.getenv("CELERY_VISIBILITY_TIMEOUT", str(DEFAULT_VISIBILITY_TIMEOUT_SECONDS))
)


def _redis_url() -> str:
    configured = os.getenv("REDIS_URL", "").strip()
    if configured:
        return configured
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    db = os.getenv("REDIS_DB", "0")
    password = os.getenv("REDIS_PASSWORD", "")
    auth = f":{quote(password, safe='')}@" if password else ""
    return f"redis://{auth}{host}:{port}/{db}"

# Update configuration using the shared Redis pool
celery_app.conf.update(
    broker_url=_redis_url(),
    result_backend=_redis_url(),
    
    # Use shared connection pool
    broker_connection_pool=redis_pool,
    result_backend_connection_pool=redis_pool,
    
    # Robustness settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={
        'visibility_timeout': CELERY_VISIBILITY_TIMEOUT_SECONDS
    },
    
    # Serialization
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    result_expires=86400,

    # Periodic Tasks (Beat)
    beat_schedule={
        'archive-old-data-every-week': {
            'task': TaskName.ARCHIVE_OLD_DATA.value,
            'schedule': crontab(hour=3, minute=0, day_of_week=0), # Run every Sunday at 3 AM
            'kwargs': {'retention_days': 30},
        },
        'cleanup-logs-every-hour': {
            'task': TaskName.CLEANUP_LOGS.value,
            'schedule': crontab(minute=0), # Run every hour
            'kwargs': {'retention_hours': 72},
        },
        'audit-knowledge-index-daily': {
            'task': TaskName.AUDIT_KNOWLEDGE_INDEX_CONSISTENCY.value,
            'schedule': crontab(hour=3, minute=30), # Run daily at 03:30
            'kwargs': {'project_id': None, 'user_id': None, 'limit': 5000},
        },
        'recover-expired-pipeline-runs': {
            'task': TaskName.RECOVER_EXPIRED_PIPELINE_RUNS.value,
            'schedule': crontab(minute='*/5'),
            'kwargs': {'limit': int(os.getenv('PIPELINE_RUN_RECOVERY_LIMIT', '20'))},
        },
    },
    timezone='Asia/Shanghai'
)

# Auto-discover tasks
celery_app.autodiscover_tasks(['modules'])
