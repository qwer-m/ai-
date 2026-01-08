import os
import subprocess
import sys
import time

def main():
    print("Starting AI Test Platform (Dev Mode)...")
    
    # Start Celery Worker
    print("Starting Celery Worker...")
    # Windows often requires pool=solo or threads for Celery
    celery_cmd = [sys.executable, "-m", "celery", "-A", "celery_config.celery_app", "worker", "--loglevel=info", "--pool=solo"]
    celery_process = subprocess.Popen(celery_cmd, cwd=os.getcwd(), env=os.environ.copy())

    # Start Celery Beat (for periodic tasks)
    print("Starting Celery Beat...")
    beat_cmd = [sys.executable, "-m", "celery", "-A", "celery_config.celery_app", "beat", "--loglevel=info"]
    beat_process = subprocess.Popen(beat_cmd, cwd=os.getcwd(), env=os.environ.copy())
    
    # Start FastAPI
    print("Starting FastAPI Server...")
    uvicorn_cmd = [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]
    
    try:
        uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=os.getcwd(), env=os.environ.copy())
        
        print("\n" + "="*50)
        print("Service started successfully!")
        print("Access the API at: http://localhost:8000")
        print("Swagger UI: http://localhost:8000/docs")
        print("="*50 + "\n")
        
        # Keep alive loop to monitor processes
        while True:
            time.sleep(1)
            
            # Check Celery Worker
            if celery_process.poll() is not None:
                print(f"Celery worker stopped (code {celery_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                celery_process = subprocess.Popen(celery_cmd, cwd=os.getcwd(), env=os.environ.copy())
                print("Celery worker restarted.")

            # Check Celery Beat
            if beat_process.poll() is not None:
                print(f"Celery beat stopped (code {beat_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                beat_process = subprocess.Popen(beat_cmd, cwd=os.getcwd(), env=os.environ.copy())
                print("Celery beat restarted.")

            # Check Uvicorn
            if uvicorn_process.poll() is not None:
                print(f"Uvicorn server stopped (code {uvicorn_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=os.getcwd(), env=os.environ.copy())
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
