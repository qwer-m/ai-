"""Celery-backed task runtime adapter."""

from __future__ import annotations

from typing import Any

from celery.result import AsyncResult

from celery_config import celery_app
from modules.orchestration.ports.task_runtime_port import TaskRuntimePort


class CeleryTaskRuntime(TaskRuntimePort):
    """Production task runtime using Celery broker/result backend."""

    def __init__(self, app=celery_app) -> None:
        self._app = app

    def dispatch(self, *, task_name: str, kwargs: dict[str, Any] | None = None) -> str:
        result = self._app.send_task(task_name, kwargs=kwargs or {})
        return str(result.id)

    def get_status(self, *, task_id: str) -> dict[str, Any]:
        task_result = AsyncResult(task_id, app=self._app)
        payload: dict[str, Any] = {
            "task_id": task_id,
            "status": str(task_result.state),
            "result": task_result.result if task_result.ready() else None,
        }
        if task_result.state == "STARTED" and isinstance(task_result.info, dict):
            payload.update(task_result.info)
        elif task_result.state == "FAILURE":
            payload["error"] = str(task_result.result)
        return payload

