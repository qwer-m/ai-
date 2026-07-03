"""Task dispatch adapter for knowledge document workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from modules.orchestration.background_task_governance import (
    BackgroundTaskKind,
    submit_background_task,
)

if TYPE_CHECKING:
    from modules.orchestration.task_dispatcher import TaskDispatchResult


def enqueue_parse_document_task(
    *,
    doc_id: int,
    file_path: str,
    force: bool = False,
    user_id: Optional[int] = None,
) -> TaskDispatchResult:
    """Dispatch parse task without exposing Celery details to callers."""
    return submit_background_task(
        BackgroundTaskKind.KNOWLEDGE_DOCUMENT_PARSE,
        kwargs={
            "document_id": doc_id,
            "file_path": file_path,
            "force": force,
            "user_id": user_id,
        },
        business_id=doc_id,
    )
