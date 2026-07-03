"""Helpers for normalizing queue runtime status with business status."""

from __future__ import annotations

from typing import Any


def get_task_runtime():
    from modules.orchestration.task_runtime import get_task_runtime as _get_task_runtime

    return _get_task_runtime()


def get_runtime_task_status(task_id: str) -> dict[str, Any]:
    """Read queue runtime status and normalize runtime lookup failures."""
    try:
        return dict(get_task_runtime().get_status(task_id=task_id))
    except Exception as exc:
        return {
            "task_id": str(task_id),
            "status": "UNKNOWN",
            "result": None,
            "error": str(exc),
        }


def task_state_from_status(task_status: dict[str, Any] | None) -> str:
    return str((task_status or {}).get("status") or "UNKNOWN")


def build_task_status_payload(
    *,
    task_id: str | None,
    task_status: dict[str, Any] | None = None,
    business_type: str = "",
    business_id: Any | None = None,
    business_status: str | None = None,
    business_error: str | None = None,
    retry_count: int | None = None,
    queue: str = "celery",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge queue state with the domain state tracked by business tables."""
    payload = dict(task_status or {})
    if task_id and not payload.get("task_id"):
        payload["task_id"] = str(task_id)
    payload.setdefault("status", "UNKNOWN" if task_id else "NOT_QUEUED")
    payload["queue"] = queue

    if business_type:
        payload["business_type"] = business_type
    if business_id is not None:
        payload["business_id"] = business_id
    if business_status is not None:
        payload["business_status"] = business_status
    if business_error:
        payload["business_error"] = business_error
    if retry_count is not None:
        payload["retry_count"] = retry_count
    if extra:
        payload.update(extra)
    return payload
