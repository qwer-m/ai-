import os
from celery import Celery
from core.redis_pool import redis_pool

# Initialize Celery app
celery_app = Celery("ai_test_platform")

from celery.schedules import crontab

# Update configuration using the shared Redis pool
celery_app.conf.update(
    broker_url=f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0",
    result_backend=f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0",
    
    # Use shared connection pool
    broker_connection_pool=redis_pool,
    result_backend_connection_pool=redis_pool,
    
    # Robustness settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={
        'visibility_timeout': 1800  # 30 minutes
    },
    
    # Serialization
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    result_expires=86400,

    # Periodic Tasks (Beat)
    beat_schedule={
        'archive-old-data-every-week': {
            'task': 'modules.tasks.archive_old_data_task',
            'schedule': crontab(hour=3, minute=0, day_of_week=0), # Run every Sunday at 3 AM
            'kwargs': {'retention_days': 30},
        },
        'cleanup-logs-every-hour': {
            'task': 'modules.tasks.cleanup_logs_task',
            'schedule': crontab(minute=0), # Run every hour
            'kwargs': {'retention_hours': 72},
        },
    },
    timezone='Asia/Shanghai'
)

# Auto-discover tasks
celery_app.autodiscover_tasks(['modules'])
