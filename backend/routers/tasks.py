from fastapi import APIRouter
from celery.result import AsyncResult
from celery_config import celery_app

router = APIRouter(
    prefix="/tasks",
    tags=["Tasks"]
)

@router.get("/{task_id}")
async def get_task_status(task_id: str):
    """
    Get status of a Celery task.
    """
    task_result = AsyncResult(task_id, app=celery_app)
    result = {
        "task_id": task_id,
        "status": task_result.state,
        "result": task_result.result if task_result.ready() else None
    }
    # Handle meta info for progress if available (if we implemented custom state updates)
    if task_result.state == 'STARTED':
        if isinstance(task_result.info, dict):
            result.update(task_result.info)
    elif task_result.state == 'FAILURE':
         result['error'] = str(task_result.result)
         
    return result
