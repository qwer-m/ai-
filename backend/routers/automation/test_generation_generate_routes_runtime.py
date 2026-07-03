from __future__ import annotations

from importlib import import_module
from typing import Any

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


class LazyObject:
    def __init__(self, module_name: str, attr_name: str):
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_attr_name", attr_name)
        object.__setattr__(self, "_target", None)

    def _resolve(self) -> Any:
        target = object.__getattribute__(self, "_target")
        if target is None:
            module_name = object.__getattribute__(self, "_module_name")
            attr_name = object.__getattribute__(self, "_attr_name")
            target = getattr(import_module(module_name), attr_name)
            object.__setattr__(self, "_target", target)
        return target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        setattr(self._resolve(), name, value)


WorkflowKind = LazyObject("core.processing.workflow", "WorkflowKind")
WorkflowStage = LazyObject("core.processing.workflow", "WorkflowStage")
context_orchestrator = LazyObject("modules.orchestration.context_orchestrator", "context_orchestrator")
knowledge_base = LazyObject("modules.domain.knowledge_base", "knowledge_base")
test_generator = LazyObject("modules.testing.test_generation", "test_generator")


def get_db():
    from core.db.database import get_db as real_get_db

    yield from real_get_db()


async def get_current_user(token: str = Depends(oauth2_scheme), db: Any = Depends(get_db)) -> Any:
    from core.authn.auth import get_current_user as real_get_current_user

    return await real_get_current_user(token=token, db=db)


def get_owned_project(*args: Any, **kwargs: Any) -> Any:
    from routers.test_generation_routes.support import get_owned_project as real_get_owned_project

    return real_get_owned_project(*args, **kwargs)


async def parse_requirement_for_generation(*args: Any, **kwargs: Any) -> Any:
    from routers.test_generation_routes.support import (
        parse_requirement_for_generation as real_parse_requirement_for_generation,
    )

    return await real_parse_requirement_for_generation(*args, **kwargs)


def detect_duplicate_document(*args: Any, **kwargs: Any) -> Any:
    from routers.test_generation_routes.support import detect_duplicate_document as real_detect_duplicate_document

    return real_detect_duplicate_document(*args, **kwargs)


def build_generation_qm(*args: Any, **kwargs: Any) -> Any:
    from routers.test_generation_routes.support import build_generation_qm as real_build_generation_qm

    return real_build_generation_qm(*args, **kwargs)


def log_to_db(*args: Any, **kwargs: Any) -> Any:
    from core.processing.utils import log_to_db as real_log_to_db

    return real_log_to_db(*args, **kwargs)


def log_workflow_trace(*args: Any, **kwargs: Any) -> Any:
    from core.processing.workflow import log_workflow_trace as real_log_workflow_trace

    return real_log_workflow_trace(*args, **kwargs)
