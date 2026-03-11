import os
import subprocess
import sys
import time
import socket
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

def _can_connect(host: str, port: int, timeout_seconds: float = 1.5) -> bool:
    """快速 TCP 连通性检测。"""
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except Exception:
        return False


def _list_wsl_distros() -> list[str]:
    try:
        proc = subprocess.run(
            ["wsl", "-l", "-q"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return []
        return [line.strip().lstrip("*").strip() for line in proc.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def _probe_wsl_ready(distro: str) -> bool:
    last_error: Exception | None = None
    for timeout_seconds in (8, 20, 35):
        try:
            proc = subprocess.run(
                ["wsl", "-d", distro, "-e", "bash", "-lc", "echo WSL_OK"],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            if proc.returncode == 0:
                return True
        except Exception as e:
            last_error = e

    if last_error is not None:
        print(f"[ERROR] WSL startup failed: {last_error}")
    return False


def _get_wsl_ip(distro: str) -> str | None:
    """获取 WSL 发行版当前 IPv4。"""
    try:
        proc = subprocess.run(
            ["wsl", "-d", distro, "-e", "bash", "-lc", "hostname -I"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            return None
        ips = [item.strip() for item in proc.stdout.split() if item.strip()]
        for ip in ips:
            if ip.count(".") == 3 and all(part.isdigit() for part in ip.split(".")):
                return ip
    except Exception:
        return None
    return None


def _ensure_wsl_redis_running(distro: str, port: int) -> bool:
    """确保 WSL 里 redis-server 进程已启动。"""
    bash_cmd = (
        f"if ! pgrep -f 'redis-server.*:{port}' >/dev/null 2>&1; then "
        f"nohup redis-server --bind 0.0.0.0 --port {port} >/tmp/redis-start.log 2>&1 & "
        "sleep 1; "
        "fi; "
        "pgrep -f 'redis-server' >/dev/null 2>&1"
    )
    try:
        proc = subprocess.run(
            ["wsl", "-d", distro, "-e", "bash", "-lc", bash_cmd],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return proc.returncode == 0
    except Exception:
        return False


def ensure_redis_ready() -> bool:
    """
    开发环境 Redis 强校验:
    1) 先检测 .env 中 REDIS_HOST/REDIS_PORT
    2) 若不可达，自动尝试拉起 WSL Redis，并更新为当前 WSL IP
    """
    redis_host = os.environ.get("REDIS_HOST", "localhost").strip()
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))

    host_candidates: list[str] = []
    for candidate in (redis_host, "127.0.0.1", "localhost"):
        normalized = candidate.strip()
        if normalized and normalized not in host_candidates:
            host_candidates.append(normalized)

    for host in host_candidates:
        if _can_connect(host, redis_port):
            os.environ["REDIS_HOST"] = host
            print(f"Redis reachable: {host}:{redis_port}")
            return True

    print(f"[WARN] Redis not reachable at {redis_host}:{redis_port}. Trying WSL auto-repair...")
    configured_distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu").strip() or "Ubuntu"
    installed_distros = _list_wsl_distros()
    distro = configured_distro
    if installed_distros and configured_distro not in installed_distros:
        ubuntu_like = [name for name in installed_distros if "ubuntu" in name.lower()]
        distro = ubuntu_like[0] if ubuntu_like else installed_distros[0]
        print(f"[WARN] WSL distro '{configured_distro}' not found. Using '{distro}'.")

    if not _probe_wsl_ready(distro):
        return False

    if not _ensure_wsl_redis_running(distro, redis_port):
        print("[ERROR] Failed to start redis-server inside WSL.")
        return False

    wsl_ip = _get_wsl_ip(distro)
    if not wsl_ip:
        print("[ERROR] Failed to detect WSL IP.")
        return False

    for host in (wsl_ip, "127.0.0.1", "localhost"):
        if _can_connect(host, redis_port):
            os.environ["REDIS_HOST"] = host
            print(f"Redis auto-repaired via WSL: {host}:{redis_port}")
            return True

    print(f"[ERROR] WSL Redis still unreachable at {wsl_ip}:{redis_port}.")
    return False


def _sanitize_windows_home_path(candidate: str | None) -> str | None:
    if not candidate:
        return None
    path = os.path.normpath(candidate.strip().strip('"').strip("'"))
    if not path:
        return None

    lowered = path.lower()
    for marker in ("\\desktop\\", "\\桌面\\"):
        if marker in lowered:
            idx = lowered.find(marker)
            if idx > 0:
                path = path[:idx]
                break
    if lowered.endswith("\\desktop") or lowered.endswith("\\桌面"):
        path = os.path.dirname(path)

    return path or None


def _resolve_windows_user_home(env: dict[str, str], root_dir: str) -> str:
    homedrive = (env.get("HOMEDRIVE") or "").strip()
    homepath = (env.get("HOMEPATH") or "").strip()
    derived_home = f"{homedrive}{homepath}" if homedrive and homepath else ""
    username = (env.get("USERNAME") or "").strip()
    system_drive = (env.get("SystemDrive") or "C:").strip()
    canonical_home = os.path.join(system_drive, "Users", username) if username else ""

    candidates = [
        env.get("USERPROFILE", ""),
        derived_home,
        os.path.expanduser("~"),
        canonical_home,
        root_dir,
    ]

    for item in candidates:
        sanitized = _sanitize_windows_home_path(item)
        if sanitized and os.path.isdir(sanitized):
            return sanitized

    return root_dir


def _build_runtime_env(current_dir: str, root_dir: str) -> dict[str, str]:
    """Normalize paths for child processes."""
    env = os.environ.copy()
    if os.name == "nt":
        user_home = _resolve_windows_user_home(env, root_dir)
    else:
        user_home = env.get("HOME") or os.path.expanduser("~") or root_dir

    local_app_data = env.get("LOCALAPPDATA") or os.path.join(user_home, "AppData", "Local")
    npm_cache_dir = os.path.join(local_app_data, "npm-cache")

    env["HOME"] = user_home
    env["USERPROFILE"] = user_home
    homedrive, homepath = os.path.splitdrive(user_home)
    env["HOMEDRIVE"] = homedrive or env.get("HOMEDRIVE", "C:")
    if user_home.startswith(env["HOMEDRIVE"]):
        env["HOMEPATH"] = user_home[len(env["HOMEDRIVE"]):] or "\\"
    else:
        env["HOMEPATH"] = homepath or env.get("HOMEPATH", "\\")
    env["NPM_CONFIG_CACHE"] = npm_cache_dir
    env["npm_config_cache"] = npm_cache_dir
    env["AI_TEST_PLATFORM_ROOT"] = root_dir
    env["AI_TEST_PLATFORM_BACKEND"] = current_dir
    return env


def _validate_project_layout(current_dir: str, root_dir: str) -> tuple[bool, str]:
    expected_files = [
        os.path.join(current_dir, "start_dev.py"),
        os.path.join(current_dir, "main.py"),
        os.path.join(root_dir, "backend", "start_dev.py"),
        os.path.join(root_dir, "frontend", "package.json"),
    ]
    missing = [path for path in expected_files if not os.path.isfile(path)]
    if missing:
        detail = "; ".join(missing)
        return False, f"Project layout validation failed. Missing required files: {detail}"
    return True, ""


def main() -> None:
    print("Starting AI Test Platform (Dev Mode)...")

    # 优先加载 backend/.env，再加载仓库根目录 .env
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
    celery_process = subprocess.Popen(celery_cmd, cwd=app_dir, env=runtime_env.copy())

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
    beat_process = subprocess.Popen(beat_cmd, cwd=app_dir, env=runtime_env.copy())

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
        uvicorn_process = subprocess.Popen(uvicorn_cmd, cwd=app_dir, env=runtime_env.copy())

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

