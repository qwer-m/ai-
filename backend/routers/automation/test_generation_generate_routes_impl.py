from __future__ import annotations

from fastapi import APIRouter

from .test_generation_generate_routes_estimate import estimate_test_count
from .test_generation_generate_routes_estimate import router as estimate_router
from .test_generation_generate_routes_stream import generate_tests_stream
from .test_generation_generate_routes_stream import router as stream_router
from .test_generation_generate_routes_json import generate_tests, generate_tests_async
from .test_generation_generate_routes_json import router as json_router
from .test_generation_generate_routes_file import (
    generate_tests_from_file,
    generate_tests_from_file_async,
)
from .test_generation_generate_routes_file import router as file_router
from .test_generation_generate_routes_excel import (
    export_tests_excel,
    generate_tests_excel,
    generate_tests_from_file_excel,
)
from .test_generation_generate_routes_excel import router as excel_router

router = APIRouter()
router.include_router(estimate_router)
router.include_router(stream_router)
router.include_router(json_router)
router.include_router(file_router)
router.include_router(excel_router)

__all__ = [
    "router",
    "estimate_test_count",
    "generate_tests_stream",
    "generate_tests",
    "generate_tests_async",
    "generate_tests_from_file",
    "generate_tests_from_file_async",
    "generate_tests_excel",
    "generate_tests_from_file_excel",
    "export_tests_excel",
]
