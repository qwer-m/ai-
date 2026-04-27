"""Task dispatch adapter for snapshot rebuild workflows."""

from __future__ import annotations

from typing import Optional
from types import SimpleNamespace

from modules.orchestration.task_runtime import get_task_runtime


def enqueue_snapshot_build_task(
    *,
    project_id: int,
    user_id: Optional[int],
    force_rebuild: bool,
):
    """Dispatch snapshot build task without exposing Celery details."""
    task_id = get_task_runtime().dispatch(
        task_name="modules.orchestration.tasks.build_context_snapshot_task",
        kwargs={
            "project_id": project_id,
            "user_id": user_id,
            "force_rebuild": force_rebuild,
        },
    )
    return SimpleNamespace(id=task_id)
