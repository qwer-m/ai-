"""Evaluation router entrypoint."""

from fastapi import APIRouter

from routers.orchestration.evaluation_execute_routes import router as execute_router
from routers.orchestration.evaluation_history_routes import router as history_router

router = APIRouter(tags=["Evaluation"])
router.include_router(execute_router)
router.include_router(history_router)
