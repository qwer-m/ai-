from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from celery.signals import worker_ready


CELERY_WORKER_READY_FILE_ENV = "AI_TEST_PLATFORM_WORKER_READY_FILE"


@worker_ready.connect(weak=False)
def _notify_celery_worker_ready(sender: object, **_kwargs: object) -> None:
    """通过本次启动独占的文件通知父进程，避免繁忙 solo 阻塞控制命令。"""

    ready_file = os.environ.get(CELERY_WORKER_READY_FILE_ENV)
    if not ready_file:
        return
    temporary_file = ready_file + ".tmp"
    with open(temporary_file, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "pid": os.getpid(),
                "parent_pid": os.getppid(),
                "hostname": str(getattr(sender, "hostname", "")),
            },
            stream,
        )
    os.replace(temporary_file, ready_file)


def _decode_command_output(output: bytes | str | None) -> str:
    """容错解码 Windows 命令输出，避免系统代码页导致启动器崩溃。"""

    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if not output:
        return ""

    encodings: list[str] = []
    if output.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in output:
        encodings.extend(["utf-16", "utf-16le"])
    encodings.extend(
        [
            "utf-8-sig",
            "cp936",
            "mbcs",
            sys.getfilesystemencoding() or "utf-8",
        ]
    )

    seen: set[str] = set()
    for encoding in encodings:
        if encoding in seen:
            continue
        seen.add(encoding)
        try:
            return output.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return output.decode("utf-8", errors="replace")


def cleanup_celery_beat_schedule(schedule_dir: str) -> None:
    """Remove stale Celery Beat schedule files from a runtime directory."""
    removed = []
    try:
        if not os.path.isdir(schedule_dir):
            return
        for name in os.listdir(schedule_dir):
            if name.startswith("celerybeat-schedule"):
                target = os.path.join(schedule_dir, name)
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
    """Terminate any Windows process that is listening on the given port."""
    try:
        cmd = f"netstat -ano | findstr :{port}"
        result = subprocess.run(cmd, shell=True, capture_output=True)

        output = _decode_command_output(getattr(result, "stdout", None))
        if result.returncode == 0 and output:
            lines = output.strip().split("\n")
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


def _windows_process_snapshot() -> list[dict[str, object]]:
    """读取 Windows 进程树，供启动器清理同项目的旧服务实例。"""

    if os.name != "nt":
        return []
    command = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    for shell_name in ("powershell", "pwsh"):
        try:
            result = subprocess.run(
                [shell_name, "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        output = _decode_command_output(getattr(result, "stdout", None))
        if result.returncode != 0 or not output.strip():
            continue
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            continue
        snapshot: list[dict[str, object]] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            try:
                process_id = int(raw.get("ProcessId") or 0)
                parent_id = int(raw.get("ParentProcessId") or 0)
            except (TypeError, ValueError):
                continue
            if process_id > 0:
                snapshot.append(
                    {
                        "pid": process_id,
                        "parent_pid": parent_id,
                        "name": str(raw.get("Name") or ""),
                        "command_line": str(raw.get("CommandLine") or ""),
                    }
                )
        return snapshot
    return []


def cleanup_project_service_processes(
    root_dir: str,
    *,
    exclude_pids: set[int] | None = None,
) -> list[int]:
    """只终止同一项目启动的旧 API/Worker/Beat/前端进程树。

    仅按端口清理会漏掉没有监听端口的 Celery worker/beat，导致多套实例
    竞争同一队列。这里先按项目根目录和服务命令识别启动根进程，再递归
    终止其子树；调用方可排除当前启动器进程。
    """

    if os.name != "nt":
        return []
    snapshot = _windows_process_snapshot()
    if not snapshot:
        return []
    excluded = {int(pid) for pid in (exclude_pids or set()) if int(pid) > 0}
    normalized_root = os.path.normcase(os.path.abspath(root_dir)).replace("/", "\\").rstrip("\\")
    processes = {int(item["pid"]): item for item in snapshot}
    parent_map: dict[int, list[int]] = {}
    for item in snapshot:
        pid = int(item["pid"])
        parent_map.setdefault(int(item["parent_pid"]), []).append(pid)

    def normalized_command(item: dict[str, object]) -> str:
        return os.path.normcase(str(item.get("command_line") or "")).replace("/", "\\")

    def is_project_service(item: dict[str, object]) -> bool:
        """只识别真实服务命令，避免把包含关键字的诊断 shell 一并终止。"""

        command_line = normalized_command(item)
        process_name = str(item.get("name") or "").casefold()
        if process_name in {"powershell.exe", "pwsh.exe", "cmd.exe"}:
            return False
        if normalized_root not in command_line:
            return False
        if not process_name.endswith(("python.exe", "node.exe")):
            return False
        full_start_script = f"{normalized_root}\\backend\\start_dev.py"
        if full_start_script in command_line and "-c" not in command_line:
            return True
        if "-m celery" in command_line and "celery_worker.celery_app" in command_line:
            return True
        if "-m uvicorn" in command_line and "main:app" in command_line:
            return True
        return process_name.endswith("node.exe") and "vite" in command_line

    root_pids: set[int] = set()
    for pid, item in processes.items():
        if is_project_service(item):
            root_pids.add(pid)

    # 有些 Python 启动器的命令行只有 `python.exe start_dev.py`，不会带项目绝对路径。
    # 但它启动的 API/Worker 命令会带项目路径；沿服务进程向上回溯即可准确找到这类
    # 启动器，并整棵终止，避免旧启动器在清理后再次拉起旧版本 Worker。
    for service_pid in tuple(root_pids):
        parent_pid = int(processes.get(service_pid, {}).get("parent_pid") or 0)
        visited: set[int] = set()
        while parent_pid and parent_pid not in visited:
            visited.add(parent_pid)
            parent = processes.get(parent_pid)
            if parent is None:
                break
            parent_command = normalized_command(parent)
            parent_name = str(parent.get("name") or "").casefold()
            if (
                parent_name.endswith("python.exe")
                and "start_dev.py" in parent_command
            ):
                root_pids.add(parent_pid)
            parent_pid = int(parent.get("parent_pid") or 0)

    # 找到启动器根进程后，纳入其命令行不含项目路径的全局 Python 子进程。
    targets = set(root_pids)
    pending = list(root_pids)
    while pending:
        parent_pid = pending.pop()
        for child_pid in parent_map.get(parent_pid, []):
            if child_pid not in targets:
                targets.add(child_pid)
                pending.append(child_pid)
    targets.difference_update(excluded)
    if not targets:
        return []

    # taskkill /T 会递归清理子树，只对最上层 PID 执行一次，避免重复报错。
    top_level = sorted(
        pid for pid in targets if int(processes.get(pid, {}).get("parent_pid") or 0) not in targets
    )
    cleaned: list[int] = []
    for pid in top_level:
        print(f"Stopping previous project service tree (PID {pid})...")
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            cleaned.append(pid)
    return cleaned


def wait_for_backend_ready(port: int, timeout_seconds: int = 90) -> bool:
    """等待 API 完成数据库和 Redis 健康检查。"""
    health_url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as resp:
                if resp.status != 200:
                    time.sleep(0.5)
                    continue
                payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("status") == "ok":
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.5)

    return False


def wait_for_celery_worker_ready(
    process: subprocess.Popen,
    *,
    ready_file: str,
    timeout_seconds: int = 45,
) -> bool:
    """等待当前启动进程的 worker_ready 握手，不依赖任务池处理 ping。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with open(ready_file, encoding="utf-8") as stream:
                receipt = json.load(stream)
        except FileNotFoundError:
            pass
        else:
            # Windows 虚拟环境可能由包装进程启动真正的 Python 子进程。
            return (
                isinstance(receipt, dict)
                and process.pid in (receipt.get("pid"), receipt.get("parent_pid"))
                and process.poll() is None
            )
        time.sleep(0.1)
    return False


def _can_connect(host: str, port: int, timeout_seconds: float = 1.5) -> bool:
    """Perform a quick TCP connectivity check."""
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
            timeout=10,
        )
        if proc.returncode != 0:
            return []
        output = _decode_wsl_stdout(proc.stdout)
        distros: list[str] = []
        for line in output.splitlines():
            name = _clean_wsl_distro_name(line)
            if name and name not in distros:
                distros.append(name)
        return distros
    except Exception:
        return []


def _decode_wsl_stdout(stdout: bytes) -> str:
    return _decode_command_output(stdout).replace("\x00", "")


def _clean_wsl_distro_name(name: str) -> str:
    return name.replace("\x00", "").strip().lstrip("*").strip()


def _probe_wsl_ready(distro: str) -> bool:
    distro = _clean_wsl_distro_name(distro)
    if not distro:
        return False

    last_error: Exception | None = None
    for timeout_seconds in (8, 20, 35):
        try:
            proc = subprocess.run(
                ["wsl", "-d", distro, "-e", "bash", "-lc", "echo WSL_OK"],
                capture_output=True,
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
    """Get the current IPv4 address of the WSL distro."""
    distro = _clean_wsl_distro_name(distro)
    if not distro:
        return None

    try:
        proc = subprocess.run(
            ["wsl", "-d", distro, "-e", "bash", "-lc", "hostname -I"],
            capture_output=True,
            timeout=20,
        )
        if proc.returncode != 0:
            return None
        output = _decode_command_output(getattr(proc, "stdout", None))
        ips = [item.strip() for item in output.split() if item.strip()]
        for ip in ips:
            if ip.count(".") == 3 and all(part.isdigit() for part in ip.split(".")):
                return ip
    except Exception:
        return None
    return None


def _ensure_wsl_redis_running(distro: str, port: int) -> bool:
    """Ensure redis-server is running inside WSL."""
    distro = _clean_wsl_distro_name(distro)
    if not distro:
        return False

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
            timeout=20,
        )
        return proc.returncode == 0
    except Exception:
        return False


def ensure_redis_ready() -> bool:
    """
    Development Redis sanity check.
    1) Prefer the configured REDIS_HOST/REDIS_PORT.
    2) If unavailable, try to auto-start WSL Redis and update REDIS_HOST.
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
    configured_distro = _clean_wsl_distro_name(os.environ.get("WSL_DISTRO_NAME", "Ubuntu")) or "Ubuntu"
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


def ensure_database_schema(current_dir: str) -> bool:
    """Run the local database schema check before starting services."""
    module_script = os.path.join(
        current_dir,
        "scripts",
        "dev_tools",
        "root_tools",
        "migrations",
        "init_db.py",
    )
    if os.path.exists(module_script):
        db_check_cmd = [sys.executable, "-m", "scripts.dev_tools.root_tools.migrations.init_db"]
        db_check_target = "scripts.dev_tools.root_tools.migrations.init_db"
    else:
        print("[ERROR] Database schema script not found:")
        print(f"  - {module_script}")
        return False

    try:
        result = subprocess.run(
            db_check_cmd,
            cwd=current_dir,
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            print("[ERROR] Database schema check failed:")
            print(f"Command: {' '.join(db_check_cmd)}")
            print(_decode_command_output(getattr(result, "stdout", None)))
            print(_decode_command_output(getattr(result, "stderr", None)))
            return False

        print(f"Database schema checked via {db_check_target}.")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to run database schema check ({db_check_target}): {e}")
        return False


def _sanitize_windows_home_path(candidate: str | None) -> str | None:
    if not candidate:
        return None
    path = os.path.normpath(candidate.strip().strip('"').strip("'"))
    if not path:
        return None

    lowered = path.lower()
    for marker in ("\\desktop\\", "\\Desktop\\"):
        if marker in lowered:
            idx = lowered.find(marker)
            if idx > 0:
                path = path[:idx]
                break
    if lowered.endswith("\\desktop") or lowered.endswith("\\Desktop"):
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


def _build_celery_command(python_executable: str) -> list[str]:
    return [
        python_executable,
        "-m",
        "celery",
        "-A",
        "celery_worker.celery_app",
        "worker",
        "--loglevel=info",
        "--pool=solo",
        "--include=start_dev_helpers",
    ]


def _build_beat_command(python_executable: str, beat_schedule_file: str) -> list[str]:
    return [
        python_executable,
        "-m",
        "celery",
        "-A",
        "celery_worker.celery_app",
        "beat",
        "--loglevel=info",
        "--schedule",
        beat_schedule_file,
    ]


def _build_uvicorn_command(python_executable: str, backend_port: int) -> list[str]:
    return [
        python_executable,
        "-m",
        "uvicorn",
        "main:app",
        "--reload",
        "--host",
        "0.0.0.0",
        "--port",
        str(backend_port),
    ]

