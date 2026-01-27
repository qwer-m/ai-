"""
Celery Worker 入口脚本 (Celery Worker Entry Point)

该脚本用于启动 Celery Worker 进程，处理异步任务。
在生产环境中，通常由 Systemd 或 Docker 启动此脚本。

命令示例:
celery -A celery_worker.celery_app worker --loglevel=info
"""

from celery_config import celery_app

# Import modules containing tasks to ensure they are registered
# 导入任务模块以确保任务被注册
import modules.tasks

if __name__ == "__main__":
    celery_app.start()
