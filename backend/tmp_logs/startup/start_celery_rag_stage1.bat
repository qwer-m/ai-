@echo off
cd /d D:\Qoder\??????\backend
python -m celery -A celery_config.celery_app worker --pool=solo -l info >> "D:\Qoder\??????\backend\tmp_logs\rag_stage1_celery.log" 2>&1
