"""Factory for task runtime adapter."""

from __future__ import annotations

from modules.orchestration.adapters.celery_task_runtime import CeleryTaskRuntime
from modules.orchestration.ports.task_runtime_port import TaskRuntimePort

_runtime: TaskRuntimePort | None = None


def get_task_runtime() -> TaskRuntimePort:
    global _runtime
    if _runtime is None:
        _runtime = CeleryTaskRuntime()
    return _runtime

