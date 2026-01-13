import os
import subprocess
import sys
import time

def main():
    print("Starting AI Test Platform (Dev Mode)...")
    backend_port = int(os.environ.get("AI_TEST_PLATFORM_PORT", os.environ.get("PORT", "8000")))
    
    # Define the working directory as the 'ai_test_platform' subdirectory
    # Assuming start_dev.py is inside 'ai_test_platform' directory, we use its parent if we run from root
    # But current script is c:\Users\Administrator\Desktop\ai技术辅助测试\ai_test_platform\start_dev.py
    # So if we run `python ai_test_platform/start_dev.py` from root, __file__ is relative.
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # The actual app code is in the same directory as this script
    app_dir = current_dir

    # Start Celery Worker
    print(f"Starting Celery Worker in {app_dir}...")
    # Windows often requires pool=solo or threads for Celery
    celery_cmd = [sys.executable, "-m", "celery", "-A", "celery_worker.celery_app", "worker", "--loglevel=info", "--pool=solo"]
    celery_process = subprocess.Popen(celery_cmd, cwd=app_dir, env=os.environ.copy())

    # Start Celery Beat (for periodic tasks)
    print(f"Starting Celery Beat in {app_dir}...")
    beat_cmd = [sys.executable, "-m", "celery", "-A", "celery_worker.celery_app", "beat", "--loglevel=info"]
    beat_process = subprocess.Popen(beat_cmd, cwd=app_dir, env=os.environ.copy())
    
    # Start FastAPI
    print(f"Starting FastAPI Server in {app_dir}...")
    uvicorn_cmd = [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", str(backend_port)]
    
    try:
        uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=app_dir, env=os.environ.copy())
        
        print("\n" + "="*50)
        print("Service started successfully!")
        print(f"Access the API at: http://localhost:{backend_port}")
        print(f"Swagger UI: http://localhost:{backend_port}/docs")
        print("="*50 + "\n")
        
        # Keep alive loop to monitor processes
        while True:
            time.sleep(1)
            
            # Check Celery Worker
            if celery_process.poll() is not None:
                print(f"Celery worker stopped (code {celery_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                celery_process = subprocess.Popen(celery_cmd, cwd=app_dir, env=os.environ.copy())
                print("Celery worker restarted.")

            # Check Celery Beat
            if beat_process.poll() is not None:
                print(f"Celery beat stopped (code {beat_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                beat_process = subprocess.Popen(beat_cmd, cwd=app_dir, env=os.environ.copy())
                print("Celery beat restarted.")

            # Check Uvicorn
            if uvicorn_process.poll() is not None:
                print(f"Uvicorn server stopped (code {uvicorn_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=app_dir, env=os.environ.copy())
                print("Uvicorn server restarted.")
                
    except KeyboardInterrupt:
        print("\nStopping services...")
        celery_process.terminate()
        beat_process.terminate()
        uvicorn_process.terminate()
        print("Services stopped.")
    except Exception as e:
        print(f"Error: {e}")
        try:
            celery_process.terminate()
        except: pass
        try:
            beat_process.terminate()
        except: pass
        try:
            uvicorn_process.terminate()
        except: pass

if __name__ == "__main__":
    main()
