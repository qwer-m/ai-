from __future__ import annotations

from types import SimpleNamespace
import json

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


def test_wait_for_celery_worker_ready_retries_until_pong(monkeypatch) -> None:
    responses = iter(
        [
            SimpleNamespace(returncode=1, stdout="", stderr="No nodes replied"),
            SimpleNamespace(returncode=0, stdout='-> worker@local: pong', stderr=""),
        ]
    )
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        return next(responses)

    monkeypatch.setattr(start_dev_helpers.subprocess, "run", fake_run)
    monkeypatch.setattr(start_dev_helpers.time, "sleep", lambda _seconds: None)

    assert start_dev_helpers.wait_for_celery_worker_ready(
        r"D:\Qoder\测试开发平台\.venv\Scripts\python.exe",
        cwd=r"D:\Qoder\测试开发平台\backend",
        env={},
        timeout_seconds=2,
    ) is True
    assert len(commands) == 2
    assert commands[0][-1] == "--timeout=2"
