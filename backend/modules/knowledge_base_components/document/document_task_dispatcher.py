"""Task dispatch adapter for knowledge document workflows."""

from __future__ import annotations

from typing import Optional
from types import SimpleNamespace

from modules.orchestration.task_runtime import get_task_runtime


def enqueue_parse_document_task(
    *,
    doc_id: int,
    file_path: str,
    force: bool = False,
    user_id: Optional[int] = None,
):
    """Dispatch parse task without exposing Celery details to callers."""
    task_id = get_task_runtime().dispatch(
        task_name="modules.orchestration.tasks.parse_knowledge_document_task",
        kwargs={
            "document_id": doc_id,
            "file_path": file_path,
            "force": force,
            "user_id": user_id,
        },
    )
    return SimpleNamespace(id=task_id)
