"""Test generation router entrypoint."""

from fastapi import APIRouter

from routers.automation.test_generation_generate_routes import router as generate_router
from routers.automation.test_generation_history_routes import router as history_router

router = APIRouter(prefix="", tags=["Test Generation"])
router.include_router(history_router)
router.include_router(generate_router)
