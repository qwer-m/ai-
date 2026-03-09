import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dotenv import load_dotenv

"""
开发环境一键启动脚本

会同时启动：
1. Celery Worker（异步任务执行）
2. Celery Beat（定时任务调度）
3. FastAPI 后端
4. 前端 Vite 开发服务

并持续监控进程，异常退出后自动重启。
"""

# 关闭 Chroma 遥测，避免本地启动时出现无关 telemetry 报错
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_SERVER_NO_ANALYTICS"] = "True"
os.environ["CHROMA_PRODUCT_TELEMETRY_IMPL"] = "core.chroma_telemetry.NoOpProductTelemetryClient"
os.environ["CHROMA_TELEMETRY_IMPL"] = "core.chroma_telemetry.NoOpProductTelemetryClient"


def cleanup_celery_beat_schedule(app_dir: str) -> None:
    """
    清理损坏的 Celery Beat 本地调度文件，避免 pickle 反序列化异常。
    """
    removed = []
    try:
        for name in os.listdir(app_dir):
            if name.startswith("celerybeat-schedule"):
                target = os.path.join(app_dir, name)
                if os.path.isfile(target):
                    try:
                        os.remove(target)
                        removed.append(name)
                    except Exception:
                        pass
        if removed:
            print(f"Removed stale beat schedule files: {', '.join(removed)}")
    except Exception as e:
        print(f"Warning: Failed to cleanup beat schedule files: {e}")


def kill_process_on_port(port: int) -> None:
    """关闭占用指定端口的进程（Windows）。"""
    try:
        cmd = f"netstat -ano | findstr :{port}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().split("\n")
            pids_to_kill = set()

            for line in lines:
                parts = line.split()
                if len(parts) >= 5:
                    local_addr = parts[1]
                    pid = parts[-1]
                    if local_addr.endswith(f":{port}"):
                        pids_to_kill.add(pid)

            for pid in pids_to_kill:
                if pid == "0":
                    continue
                print(f"Port {port} is in use by PID {pid}. Killing it...")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
    except Exception as e:
        print(f"Warning: Failed to cleanup port {port}: {e}")


def wait_for_backend_ready(port: int, timeout_seconds: int = 90) -> bool:
    """等待后端健康检查就绪，避免前端代理首个请求报 ECONNREFUSED。"""
    health_url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as resp:
                if 200 <= resp.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.5)

    return False


def main() -> None:
    print("Starting AI Test Platform (Dev Mode)...")

    # 优先加载 backend/.env，再加载仓库根目录 .env
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(current_dir)
    load_dotenv(os.path.join(current_dir, ".env"))
    load_dotenv(os.path.join(root_dir, ".env"))

    if (
        not os.environ.get("DATABASE_URL")
        and not os.environ.get("DB_PASSWORD")
        and not os.environ.get("MYSQL_PASSWORD")
    ):
        print("[ERROR] Missing DB credentials. Please set DATABASE_URL or DB_PASSWORD (or MYSQL_PASSWORD) in backend/.env.")
        return

    # 启动前清理端口占用
    kill_process_on_port(8000)  # 后端
    kill_process_on_port(5173)  # 前端

    backend_port = int(os.environ.get("AI_TEST_PLATFORM_PORT", os.environ.get("PORT", "8000")))
    app_dir = current_dir
    cleanup_celery_beat_schedule(app_dir)

    print(f"Starting Celery Worker in {app_dir}...")
    celery_cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "celery_worker.celery_app",
        "worker",
        "--loglevel=info",
        "--pool=solo",
    ]
    celery_process = subprocess.Popen(celery_cmd, cwd=app_dir, env=os.environ.copy())

    print(f"Starting Celery Beat in {app_dir}...")
    beat_schedule_file = os.path.join(app_dir, "celerybeat-schedule")
    beat_cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "celery_worker.celery_app",
        "beat",
        "--loglevel=info",
        "--schedule",
        beat_schedule_file,
    ]
    beat_process = subprocess.Popen(beat_cmd, cwd=app_dir, env=os.environ.copy())

    print(f"Starting FastAPI Server in {app_dir}...")
    uvicorn_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--reload",
        "--host",
        "0.0.0.0",
        "--port",
        str(backend_port),
    ]

    frontend_dir = os.path.join(os.path.dirname(current_dir), "frontend")
    frontend_process = None
    uvicorn_process = None

    try:
        uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=app_dir, env=os.environ.copy())

        print(f"Waiting for backend health check: http://127.0.0.1:{backend_port}/api/health")
        backend_ready = wait_for_backend_ready(backend_port, timeout_seconds=90)
        if backend_ready:
            print("Backend is ready.")
        else:
            print("[WARNING] Backend health check timed out. Frontend may see temporary proxy errors.")

        # 后端可用后再启动前端，避免前端首个 /api 请求 ECONNREFUSED
        if os.path.exists(frontend_dir):
            print(f"Starting Frontend in {frontend_dir}...")
            npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
            try:
                frontend_process = subprocess.Popen([npm_cmd, "run", "dev"], cwd=frontend_dir, env=os.environ.copy())
            except Exception as e:
                print(f"Failed to start frontend: {e}")

        print("\n" + "=" * 50)
        print("Service started successfully!")
        print(f"Backend API: http://localhost:{backend_port}")
        print(f"Swagger UI: http://localhost:{backend_port}/docs")

        if frontend_process:
            frontend_url = "http://localhost:5173"
            print(f"Frontend:    {frontend_url} (typical)")
            auto_open = os.environ.get("AUTO_OPEN_BROWSER", "0").lower() in {"1", "true", "yes"}
            if auto_open:
                import webbrowser

                print("AUTO_OPEN_BROWSER enabled, trying to reuse existing browser window/tab...")
                time.sleep(2)
                webbrowser.open(frontend_url, new=0, autoraise=True)
            else:
                print("Browser auto-open is disabled (AUTO_OPEN_BROWSER=0). Refresh your existing tab instead.")

        print("=" * 50 + "\n")

        while True:
            time.sleep(1)

            if celery_process.poll() is not None:
                print(f"Celery worker stopped (code {celery_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                celery_process = subprocess.Popen(celery_cmd, cwd=app_dir, env=os.environ.copy())
                print("Celery worker restarted.")

            if beat_process.poll() is not None:
                print(f"Celery beat stopped (code {beat_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                beat_process = subprocess.Popen(beat_cmd, cwd=app_dir, env=os.environ.copy())
                print("Celery beat restarted.")

            if uvicorn_process and uvicorn_process.poll() is not None:
                print(f"Uvicorn server stopped (code {uvicorn_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=app_dir, env=os.environ.copy())
                wait_for_backend_ready(backend_port, timeout_seconds=90)
                print("Uvicorn server restarted.")

            if frontend_process and frontend_process.poll() is not None:
                print(f"Frontend stopped (code {frontend_process.returncode}).")
                frontend_process = None

    except KeyboardInterrupt:
        print("\nStopping services...")
        try:
            celery_process.terminate()
        except Exception:
            pass
        try:
            beat_process.terminate()
        except Exception:
            pass
        try:
            if uvicorn_process:
                uvicorn_process.terminate()
        except Exception:
            pass
        if frontend_process:
            try:
                frontend_process.terminate()
            except Exception:
                pass
        print("Services stopped.")
    except Exception as e:
        print(f"Error: {e}")
        try:
            celery_process.terminate()
        except Exception:
            pass
        try:
            beat_process.terminate()
        except Exception:
            pass
        try:
            if uvicorn_process:
                uvicorn_process.terminate()
        except Exception:
            pass
        if frontend_process:
            try:
                frontend_process.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
