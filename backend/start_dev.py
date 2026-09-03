"""Development environment one-click startup script."""

import os
import subprocess
import sys
import time

from dotenv import load_dotenv

try:
    from .start_dev_helpers import (
        _build_beat_command,
        _build_celery_command,
        _build_runtime_env,
        _build_uvicorn_command,
        _validate_project_layout,
        cleanup_celery_beat_schedule,
        cleanup_project_service_processes,
        ensure_database_schema,
        ensure_redis_ready,
        kill_process_on_port,
        wait_for_celery_worker_ready,
        wait_for_backend_ready,
    )
except ImportError:
    from start_dev_helpers import (
        _build_beat_command,
        _build_celery_command,
        _build_runtime_env,
        _build_uvicorn_command,
        _validate_project_layout,
        cleanup_celery_beat_schedule,
        cleanup_project_service_processes,
        ensure_database_schema,
        ensure_redis_ready,
        kill_process_on_port,
        wait_for_celery_worker_ready,
        wait_for_backend_ready,
    )

# Disable Chroma telemetry to avoid noisy local startup errors.
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_SERVER_NO_ANALYTICS"] = "True"
os.environ["CHROMA_PRODUCT_TELEMETRY_IMPL"] = "core.cache_layer.chroma_telemetry.NoOpProductTelemetryClient"
os.environ["CHROMA_TELEMETRY_IMPL"] = "core.cache_layer.chroma_telemetry.NoOpProductTelemetryClient"


def main() -> None:
    print("Starting AI Test Platform (Dev Mode)...")

    # Load backend/.env first, then project root .env.
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(current_dir)
    print(f"Resolved backend dir: {current_dir}")
    print(f"Resolved project root: {root_dir}")
    ok_layout, layout_error = _validate_project_layout(current_dir, root_dir)
    if not ok_layout:
        print(f"[ERROR] {layout_error}")
        print("[ERROR] Please run the launcher from the real project directory, not a copied shortcut workspace.")
        return
    os.chdir(current_dir)
    load_dotenv(os.path.join(current_dir, ".env"))
    load_dotenv(os.path.join(root_dir, ".env"))
    runtime_env = _build_runtime_env(current_dir, root_dir)
    print(f"Resolved runtime home: {runtime_env['USERPROFILE']}")

    if (
        not os.environ.get("DATABASE_URL")
        and not os.environ.get("DB_PASSWORD")
        and not os.environ.get("MYSQL_PASSWORD")
    ):
        print("[ERROR] Missing DB credentials. Please set DATABASE_URL or DB_PASSWORD (or MYSQL_PASSWORD) in backend/.env.")
        return

    if not ensure_redis_ready():
        print("[ERROR] Redis is required but unavailable. Please fix Redis and retry.")
        return
    runtime_env["REDIS_HOST"] = os.environ.get("REDIS_HOST", runtime_env.get("REDIS_HOST", "localhost"))
    runtime_env["REDIS_PORT"] = os.environ.get("REDIS_PORT", runtime_env.get("REDIS_PORT", "6379"))

    if not ensure_database_schema(current_dir):
        print("[ERROR] Database schema check failed. Please fix DB and retry.")
        return

    backend_port = int(os.environ.get("AI_TEST_PLATFORM_PORT", os.environ.get("PORT", "8000")))
    app_dir = current_dir
    # 仅清理同一项目的旧服务树；端口清理作为最后一道保险，避免误杀其他项目。
    cleanup_project_service_processes(
        root_dir,
        exclude_pids={os.getpid()},
    )
    kill_process_on_port(backend_port)
    kill_process_on_port(int(os.environ.get("FRONTEND_PORT", "5173")))

    beat_runtime_dir = os.path.join(app_dir, "runtime", "system")
    os.makedirs(beat_runtime_dir, exist_ok=True)
    cleanup_celery_beat_schedule(beat_runtime_dir)

    celery_cmd = _build_celery_command(sys.executable)
    beat_schedule_file = os.path.join(beat_runtime_dir, "celerybeat-schedule")
    beat_cmd = _build_beat_command(sys.executable, beat_schedule_file)
    uvicorn_cmd = _build_uvicorn_command(sys.executable, backend_port)

    frontend_dir = os.path.join(root_dir, "frontend")
    celery_process = None
    beat_process = None
    frontend_process = None
    uvicorn_process = None

    def _stop_process(process: subprocess.Popen | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=8)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _stop_all_processes() -> None:
        # 先停前端和调度，再停 worker/API，避免退出过程中继续投递任务。
        for process in (frontend_process, beat_process, celery_process, uvicorn_process):
            _stop_process(process)

    try:
        # 启动顺序固定为 API -> Worker -> Beat -> Frontend，只有依赖就绪后才对外提供入口。
        print(f"Starting FastAPI Server in {app_dir}...")
        uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=app_dir, env=runtime_env.copy())

        print(f"Waiting for backend health check: http://127.0.0.1:{backend_port}/api/health")
        backend_ready = wait_for_backend_ready(backend_port, timeout_seconds=90)
        if not backend_ready:
            raise RuntimeError("Backend health check timed out; worker and frontend were not started.")
        print("Backend is ready.")

        print(f"Starting Celery Worker in {app_dir}...")
        celery_process = subprocess.Popen(celery_cmd, cwd=app_dir, env=runtime_env.copy())
        print("Waiting for Celery worker heartbeat...")
        if not wait_for_celery_worker_ready(
            sys.executable,
            cwd=app_dir,
            env=runtime_env.copy(),
            timeout_seconds=45,
        ):
            raise RuntimeError("Celery worker did not answer ping; Beat and frontend were not started.")
        print("Celery worker is ready.")

        print(f"Starting Celery Beat in {app_dir}...")
        beat_process = subprocess.Popen(beat_cmd, cwd=app_dir, env=runtime_env.copy())
        time.sleep(1)
        if beat_process.poll() is not None:
            raise RuntimeError(f"Celery beat exited during startup (code {beat_process.returncode}).")

        # Start the frontend only after the backend is reachable to avoid initial proxy errors.
        if os.path.exists(frontend_dir):
            print(f"Starting Frontend in {frontend_dir}...")
            npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
            try:
                print(f"Using npm cache: {runtime_env['NPM_CONFIG_CACHE']}")
                frontend_process = subprocess.Popen([npm_cmd, "run", "dev"], cwd=frontend_dir, env=runtime_env.copy())
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
                print(f"Celery worker stopped (code {celery_process.returncode}). Restarting after readiness check...")
                time.sleep(3)
                celery_process = subprocess.Popen(celery_cmd, cwd=app_dir, env=runtime_env.copy())
                if not wait_for_celery_worker_ready(
                    sys.executable,
                    cwd=app_dir,
                    env=runtime_env.copy(),
                    timeout_seconds=45,
                ):
                    raise RuntimeError("Celery worker restart did not answer ping.")
                print("Celery worker restarted and is ready.")

            if beat_process.poll() is not None:
                print(f"Celery beat stopped (code {beat_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                beat_process = subprocess.Popen(beat_cmd, cwd=app_dir, env=runtime_env.copy())
                print("Celery beat restarted.")

            if uvicorn_process and uvicorn_process.poll() is not None:
                print(f"Uvicorn server stopped (code {uvicorn_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=app_dir, env=runtime_env.copy())
                if not wait_for_backend_ready(backend_port, timeout_seconds=90):
                    raise RuntimeError("Uvicorn restart did not pass backend health check.")
                print("Uvicorn server restarted.")

            if frontend_process and frontend_process.poll() is not None:
                print(f"Frontend stopped (code {frontend_process.returncode}).")
                frontend_process = None

    except KeyboardInterrupt:
        print("\nStopping services...")
        _stop_all_processes()
        print("Services stopped.")
    except Exception as e:
        print(f"[ERROR] Startup/runtime failure: {e}")
        _stop_all_processes()


if __name__ == "__main__":
    main()

