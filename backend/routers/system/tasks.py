from fastapi import APIRouter

from modules.orchestration.task_status import (
    build_task_status_payload,
    get_runtime_task_status,
)

router = APIRouter(
    prefix="/tasks",
    tags=["Tasks"]
)

@router.get("/{task_id}")
async def get_task_status(task_id: str):
    """
    Get status of a Celery task.
    """
    task_status = get_runtime_task_status(task_id=task_id)
    return build_task_status_payload(task_id=task_id, task_status=task_status)
