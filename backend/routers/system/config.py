"""Configuration center router entrypoint."""

from fastapi import APIRouter

from routers.system.config_routes_runtime import router as runtime_router
from routers.system.config_routes_tools import router as tools_router

router = APIRouter(prefix="/config", tags=["Config"])
router.include_router(runtime_router)
router.include_router(tools_router)
