"""Task dispatch adapter for snapshot rebuild workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from modules.orchestration.background_task_governance import (
    BackgroundTaskKind,
    submit_background_task,
)

if TYPE_CHECKING:
    from modules.orchestration.task_dispatcher import TaskDispatchResult


def enqueue_snapshot_build_task(
    *,
    project_id: int,
    user_id: Optional[int],
    force_rebuild: bool,
) -> TaskDispatchResult:
    """Dispatch snapshot build task without exposing Celery details."""
    return submit_background_task(
        BackgroundTaskKind.CONTEXT_SNAPSHOT_REBUILD,
        kwargs={
            "project_id": project_id,
            "user_id": user_id,
            "force_rebuild": force_rebuild,
        },
        business_id=project_id,
    )
