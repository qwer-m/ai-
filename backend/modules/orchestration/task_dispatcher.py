"""Unified helper for queuing background tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from modules.orchestration.task_names import TaskName, task_name_value


def get_task_runtime():
    from modules.orchestration.task_runtime import get_task_runtime as _get_task_runtime

    return _get_task_runtime()


@dataclass(frozen=True)
class TaskDispatchResult:
    task_id: str
    task_name: str
    queued: bool = True
    status: str = "PENDING"
    reason: str = "queued"
    business_type: str = ""
    business_id: Any | None = None
    queue: str = "celery"
    error: str = ""

    @property
    def id(self) -> str:
        """Compatibility property for existing task-result call sites."""
        return self.task_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "queued": self.queued,
            "status": self.status,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "reason": self.reason,
            "business_type": self.business_type,
            "business_id": self.business_id,
            "queue": self.queue,
            "error": self.error,
        }


def enqueue_task(
    task_name: TaskName | str,
    *,
    kwargs: dict[str, Any] | None = None,
    business_type: str = "",
    business_id: Any | None = None,
    reason: str = "queued",
    queue: str = "celery",
) -> TaskDispatchResult:
    normalized_name = task_name_value(task_name)
    task_id = get_task_runtime().dispatch(task_name=normalized_name, kwargs=kwargs or {})
    return TaskDispatchResult(
        task_id=str(task_id),
        task_name=normalized_name,
        reason=reason,
        business_type=business_type,
        business_id=business_id,
        queue=queue,
    )
