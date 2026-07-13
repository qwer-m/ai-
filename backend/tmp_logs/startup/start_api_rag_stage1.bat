@echo off
cd /d D:\Qoder\??????\backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 >> "D:\Qoder\??????\backend\tmp_logs\rag_stage1_api.log" 2>&1
