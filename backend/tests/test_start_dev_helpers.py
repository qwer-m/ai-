from __future__ import annotations

from types import SimpleNamespace
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from dotenv import dotenv_values
import pytest

import start_dev_helpers


def test_windows_process_snapshot_decodes_legacy_code_page(monkeypatch) -> None:
    root = r"D:\Qoder\测试开发平台"
    payload = [
        {
            "ProcessId": 100,
            "ParentProcessId": 1,
            "Name": "python.exe",
            "CommandLine": rf'"{root}\\backend\\start_dev.py" --名称 测试',
        }
    ]
    monkeypatch.setattr(start_dev_helpers.os, "name", "nt")
    monkeypatch.setattr(
        start_dev_helpers.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload, ensure_ascii=False).encode("cp936"),
        ),
    )

    snapshot = start_dev_helpers._windows_process_snapshot()

    assert snapshot[0]["pid"] == 100
    assert "测试" in snapshot[0]["command_line"]


def test_windows_process_snapshot_skips_missing_stdout(monkeypatch) -> None:
    monkeypatch.setattr(start_dev_helpers.os, "name", "nt")
    monkeypatch.setattr(
        start_dev_helpers.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=None),
    )

    assert start_dev_helpers._windows_process_snapshot() == []


@pytest.mark.skipif(start_dev_helpers.os.name != "nt", reason="仅验证 Windows 启动器进程树")
def test_cleanup_project_service_processes_stops_root_trees_once(monkeypatch) -> None:
    root = r"D:\Qoder\测试开发平台"
    snapshot = [
        {
            "pid": 90,
            "parent_pid": 1,
            "name": "python.exe",
            "command_line": rf'"{root}\.venv\Scripts\python.exe" "{root}\backend\start_dev.py"',
        },
        {
            "pid": 100,
            "parent_pid": 90,
            "name": "python.exe",
            # 旧启动器可能由 PATH 中的 Python 启动，命令行不含项目绝对路径。
            "command_line": r'"C:\Program Files\Python311\python.exe" start_dev.py',
        },
        {
            "pid": 101,
            "parent_pid": 100,
            "name": "python.exe",
            "command_line": rf'"{root}\.venv\Scripts\python.exe" -m celery -A celery_worker.celery_app worker',
        },
        {
            "pid": 102,
            "parent_pid": 100,
            "name": "python.exe",
            "command_line": rf'"{root}\.venv\Scripts\python.exe" -m uvicorn main:app --port 8000',
        },
        {
            "pid": 103,
            "parent_pid": 101,
            "name": "python.exe",
            "command_line": r'"C:\Program Files\Python311\python.exe" -m celery -A celery_worker.celery_app worker',
        },
        {
            "pid": 200,
            "parent_pid": 1,
            "name": "node.exe",
            "command_line": rf'"{root}\frontend\node_modules\.bin\vite.js" --host 0.0.0.0',
        },
    ]
    monkeypatch.setattr(start_dev_helpers, "_windows_process_snapshot", lambda: snapshot)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(start_dev_helpers.subprocess, "run", fake_run)
    cleaned = start_dev_helpers.cleanup_project_service_processes(root, exclude_pids={999})

    assert cleaned == [90, 200]
    assert calls == [
        ["taskkill", "/F", "/T", "/PID", "90"],
        ["taskkill", "/F", "/T", "/PID", "200"],
    ]


@pytest.mark.skipif(
    os.name != "nt" or os.getenv("RUN_LIVE_CELERY_TESTS") != "1",
    reason="Windows 设置 RUN_LIVE_CELERY_TESTS=1 后验证真实隔离 Celery Worker",
)
@pytest.mark.parametrize("use_project_venv", [False, True], ids=["current-python", "project-venv"])
def test_worker_ready_handshake_with_real_isolated_worker(tmp_path, use_project_venv) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    root_dir = backend_dir.parent
    executable = root_dir / ".venv" / "Scripts" / "python.exe" if use_project_venv else Path(sys.executable)
    if not executable.is_file():
        pytest.skip("当前项目没有可验证的 Python 虚拟环境")
    identity = f"startup-readiness-{uuid.uuid4().hex}"
    ready_file = tmp_path / "ready.json"
    log_file = tmp_path / "worker.log"
    env = start_dev_helpers._build_runtime_env(str(backend_dir), str(root_dir))
    for directory in (backend_dir, root_dir):
        for key, value in dotenv_values(directory / ".env").items():
            if value is not None:
                env.setdefault(key, value)
    env[start_dev_helpers.CELERY_WORKER_READY_FILE_ENV] = str(ready_file)
    command = start_dev_helpers._build_celery_command(str(executable)) + [
        f"--queues={identity}",
        f"--hostname={identity}@%h",
        "--without-mingle",
        "--without-gossip",
    ]
    with log_file.open("wb") as log:
        process = subprocess.Popen(
            command, cwd=backend_dir, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            assert start_dev_helpers.wait_for_celery_worker_ready(
                process, ready_file=str(ready_file),
            ), log_file.read_text(encoding="utf-8", errors="replace")
            receipt = json.loads(ready_file.read_text(encoding="utf-8"))
            assert process.pid in (receipt["pid"], receipt["parent_pid"])
            assert receipt["hostname"].startswith(identity + "@")
        finally:
            if process.poll() is None:
                # 仅终止本测试创建的进程树，同时关闭虚拟环境包装进程及真实子进程。
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True, check=True,
                )
            process.wait(timeout=10)
    assert not start_dev_helpers.wait_for_celery_worker_ready(
        process, ready_file=str(ready_file), timeout_seconds=1,
    )
    assert " received" not in log_file.read_text(encoding="utf-8", errors="replace")
