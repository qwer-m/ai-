from fastapi import APIRouter

from modules.orchestration.task_runtime import get_task_runtime

router = APIRouter(
    prefix="/tasks",
    tags=["Tasks"]
)

@router.get("/{task_id}")
async def get_task_status(task_id: str):
    """
    Get status of a Celery task.
    """
    return get_task_runtime().get_status(task_id=task_id)
