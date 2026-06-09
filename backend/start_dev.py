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
        ensure_database_schema,
        ensure_redis_ready,
        kill_process_on_port,
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
        ensure_database_schema,
        ensure_redis_ready,
        kill_process_on_port,
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

    if not ensure_database_schema(current_dir):
        print("[ERROR] Database schema check failed. Please fix DB and retry.")
        return

    # Clean up any processes already occupying the dev ports.
    kill_process_on_port(8000)
    kill_process_on_port(5173)

    backend_port = int(os.environ.get("AI_TEST_PLATFORM_PORT", os.environ.get("PORT", "8000")))
    app_dir = current_dir
    beat_runtime_dir = os.path.join(app_dir, "runtime", "system")
    os.makedirs(beat_runtime_dir, exist_ok=True)
    cleanup_celery_beat_schedule(beat_runtime_dir)

    print(f"Starting Celery Worker in {app_dir}...")
    celery_cmd = _build_celery_command(sys.executable)
    celery_process = subprocess.Popen(celery_cmd, cwd=app_dir, env=runtime_env.copy())

    print(f"Starting Celery Beat in {app_dir}...")
    beat_schedule_file = os.path.join(beat_runtime_dir, "celerybeat-schedule")
    beat_cmd = _build_beat_command(sys.executable, beat_schedule_file)
    beat_process = subprocess.Popen(beat_cmd, cwd=app_dir, env=runtime_env.copy())

    print(f"Starting FastAPI Server in {app_dir}...")
    uvicorn_cmd = _build_uvicorn_command(sys.executable, backend_port)

    frontend_dir = os.path.join(root_dir, "frontend")
    frontend_process = None
    uvicorn_process = None

    try:
        uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=app_dir, env=runtime_env.copy())

        print(f"Waiting for backend health check: http://127.0.0.1:{backend_port}/api/health")
        backend_ready = wait_for_backend_ready(backend_port, timeout_seconds=90)
        if backend_ready:
            print("Backend is ready.")
        else:
            print("[WARNING] Backend health check timed out. Frontend may see temporary proxy errors.")

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
                print(f"Celery worker stopped (code {celery_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                celery_process = subprocess.Popen(celery_cmd, cwd=app_dir, env=runtime_env.copy())
                print("Celery worker restarted.")

            if beat_process.poll() is not None:
                print(f"Celery beat stopped (code {beat_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                beat_process = subprocess.Popen(beat_cmd, cwd=app_dir, env=runtime_env.copy())
                print("Celery beat restarted.")

            if uvicorn_process and uvicorn_process.poll() is not None:
                print(f"Uvicorn server stopped (code {uvicorn_process.returncode}). Restarting in 3s...")
                time.sleep(3)
                uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=app_dir, env=runtime_env.copy())
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

