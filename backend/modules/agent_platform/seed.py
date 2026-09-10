from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .contracts import parse_execution_definition
from .definition_repository import AgentDefinitionRepository
from .registry import (
    BUILTIN_AGENT_SPECS,
    BUILTIN_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS,
    tool_registry,
)


def _sync_project_tools(
    repo: AgentDefinitionRepository, *, project_id: int, user_id: int,
) -> None:
    repo.disable_obsolete_tools(
        project_id=project_id,
        active_keys={str(spec["tool_key"]) for spec in BUILTIN_TOOL_SPECS},
    )
    for spec in BUILTIN_TOOL_SPECS:
        repo.sync_builtin_tool(project_id=project_id, user_id=user_id, spec=spec)


def _sync_global_agents(repo: AgentDefinitionRepository, *, user_id: int) -> None:
    repo.disable_obsolete_agents(
        active_keys={str(spec["agent_key"]) for spec in BUILTIN_AGENT_SPECS},
    )
    for spec in BUILTIN_AGENT_SPECS:
        repo.sync_builtin_agent(user_id=user_id, spec=spec)


def _sync_project_workflows(
    repo: AgentDefinitionRepository,
    *,
    project_id: int,
    user_id: int,
    definitions: list[dict[str, Any]],
) -> None:
    repo.disable_obsolete_workflows(
        project_id=project_id,
        active_keys={str(spec["workflow_key"]) for spec in BUILTIN_WORKFLOW_SPECS},
    )
    for spec, definition in zip(BUILTIN_WORKFLOW_SPECS, definitions, strict=True):
        repo.sync_builtin_workflow(
            project_id=project_id, user_id=user_id, spec=spec, definition=definition,
        )


def seed_builtin_definitions(*, db: Session, project_id: int, user_id: int) -> None:
    """同步内置定义，保留项目覆盖；由调用方统一提交事务。"""

    # 写库前完成注册校验，避免无效处理器或工作流留下部分同步状态。
    for spec in BUILTIN_TOOL_SPECS:
        tool_registry.resolve(str(spec["handler_key"]))
    definitions = [
        parse_execution_definition(spec["definition"]).model_dump()
        for spec in BUILTIN_WORKFLOW_SPECS
    ]
    repo = AgentDefinitionRepository(db)
    _sync_project_tools(repo, project_id=project_id, user_id=user_id)
    _sync_global_agents(repo, user_id=user_id)
    _sync_project_workflows(
        repo, project_id=project_id, user_id=user_id, definitions=definitions,
    )
    db.flush()
