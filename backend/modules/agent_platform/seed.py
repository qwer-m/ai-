from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.model_defs import (
    AgentDefinition,
    AgentToolBinding,
    AgentToolDefinition,
    AgentWorkflowDefinition,
)
from .contracts import parse_execution_definition
from .registry import (
    BUILTIN_AGENT_SPECS,
    BUILTIN_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS,
    tool_registry,
)
from .repository import AgentPlatformRepository


def seed_builtin_definitions(*, db: Session, project_id: int, user_id: int) -> None:
    """同步全局内置模板，并保留项目覆盖定义。"""

    repo = AgentPlatformRepository(db)
    active_tool_keys = {str(spec["tool_key"]) for spec in BUILTIN_TOOL_SPECS}
    active_agent_keys = {str(spec["agent_key"]) for spec in BUILTIN_AGENT_SPECS}
    active_workflow_keys = {str(spec["workflow_key"]) for spec in BUILTIN_WORKFLOW_SPECS}
    obsolete_tools = (
        db.query(AgentToolDefinition)
        .filter(
            AgentToolDefinition.project_id == project_id,
            AgentToolDefinition.builtin.is_(True),
            AgentToolDefinition.tool_key.notin_(active_tool_keys),
        )
        .all()
    )
    for tool in obsolete_tools:
        tool.enabled = False
        db.add(tool)
        for binding in (
            db.query(AgentToolBinding)
            .filter(AgentToolBinding.tool_definition_id == tool.id)
            .all()
        ):
            binding.enabled = False
            db.add(binding)
    # 旧版本曾把内置定义复制到每个项目；统一停用这些副本，运行时回退到全局模板。
    for agent in (
        db.query(AgentDefinition)
        .filter(
            AgentDefinition.project_id.is_not(None),
            AgentDefinition.builtin.is_(True),
            AgentDefinition.enabled.is_(True),
        )
        .all()
    ):
        agent.enabled = False
        db.add(agent)
    for agent in (
        db.query(AgentDefinition)
        .filter(
            AgentDefinition.project_id.is_(None),
            AgentDefinition.builtin.is_(True),
            AgentDefinition.agent_key.notin_(active_agent_keys),
        )
        .all()
    ):
        agent.enabled = False
        db.add(agent)
    for workflow in (
        db.query(AgentWorkflowDefinition)
        .filter(
            AgentWorkflowDefinition.project_id == project_id,
            AgentWorkflowDefinition.builtin.is_(True),
            AgentWorkflowDefinition.workflow_key.notin_(active_workflow_keys),
        )
        .all()
    ):
        workflow.enabled = False
        db.add(workflow)

    tools_by_key: dict[str, AgentToolDefinition] = {}
    for spec in BUILTIN_TOOL_SPECS:
        tool_registry.resolve(str(spec["handler_key"]))
        row = repo.get_tool(
            project_id=project_id,
            tool_key=str(spec["tool_key"]),
            enabled_only=False,
        )
        if row is None:
            row = AgentToolDefinition(
                project_id=project_id,
                user_id=user_id,
                tool_key=spec["tool_key"],
                builtin=True,
            )
        row.name = spec["name"]
        row.description = spec["description"]
        row.handler_key = spec["handler_key"]
        row.input_schema = spec["input_schema"]
        row.output_schema = spec["output_schema"]
        row.risk_level = spec["risk_level"]
        row.requires_approval = bool(spec["requires_approval"])
        row.enabled = True
        db.add(row)
        db.flush()
        tools_by_key[row.tool_key] = row

    for spec in BUILTIN_AGENT_SPECS:
        agent_key = str(spec["agent_key"])
        target_version = int(spec.get("version") or 1)
        existing_versions = (
            db.query(AgentDefinition)
            .filter(
                AgentDefinition.project_id.is_(None),
                AgentDefinition.agent_key == agent_key,
                AgentDefinition.builtin.is_(True),
            )
            .all()
        )
        for existing in existing_versions:
            existing.enabled = int(existing.version or 1) == target_version
            db.add(existing)
            if not existing.enabled:
                for binding in (
                    db.query(AgentToolBinding)
                    .filter(AgentToolBinding.agent_definition_id == existing.id)
                    .all()
                ):
                    binding.enabled = False
                    db.add(binding)
        row = next(
            (
                existing
                for existing in existing_versions
                if int(existing.version or 1) == target_version
            ),
            None,
        )
        if row is None:
            row = AgentDefinition(
                project_id=None,
                user_id=user_id,
                agent_key=spec["agent_key"],
                version=target_version,
                builtin=True,
            )
        # 内置定义以代码为事实源；同版本也要同步，避免 Worker 长期执行旧配置。
        row.name = spec["name"]
        row.description = spec["description"]
        row.instructions = spec["instructions"]
        row.model = spec["model"]
        row.output_schema = spec["output_schema"]
        row.runtime_config = spec["runtime_config"]
        row.enabled = True
        db.add(row)
        db.flush()

        requested_tool_keys = list((row.runtime_config or {}).get("tool_keys") or [])
        existing_bindings = (
            db.query(AgentToolBinding)
            .filter(AgentToolBinding.agent_definition_id == row.id)
            .all()
        )
        for binding in existing_bindings:
            binding.enabled = False
            db.add(binding)
        # 全局模板的工具按执行项目动态解析，不绑定某个项目的工具记录。
        if row.project_id is None:
            continue
        for tool_key in requested_tool_keys:
            tool = tools_by_key.get(str(tool_key))
            if tool is None:
                raise ValueError(f"内置智能体引用了未知工具: {tool_key}")
            binding = (
                db.query(AgentToolBinding)
                .filter(
                    AgentToolBinding.agent_definition_id == row.id,
                    AgentToolBinding.tool_definition_id == tool.id,
                )
                .first()
            )
            if binding is None:
                binding = AgentToolBinding(
                    agent_definition_id=row.id,
                    tool_definition_id=tool.id,
                )
            binding.enabled = True
            db.add(binding)

    for spec in BUILTIN_WORKFLOW_SPECS:
        execution = parse_execution_definition(spec["definition"])
        workflow_key = str(spec["workflow_key"])
        target_version = int(spec.get("version") or 1)
        existing_versions = (
            db.query(AgentWorkflowDefinition)
            .filter(
                AgentWorkflowDefinition.project_id == project_id,
                AgentWorkflowDefinition.workflow_key == workflow_key,
                AgentWorkflowDefinition.builtin.is_(True),
            )
            .all()
        )
        for existing in existing_versions:
            existing.enabled = int(existing.version or 1) == target_version
            db.add(existing)
        row = next(
            (
                existing
                for existing in existing_versions
                if int(existing.version or 1) == target_version
            ),
            None,
        )
        if row is None:
            row = AgentWorkflowDefinition(
                project_id=project_id,
                user_id=user_id,
                workflow_key=spec["workflow_key"],
                version=target_version,
                builtin=True,
            )
        # 工作流节点与恢复边界也必须跟随当前代码，不能保留同版本旧图。
        row.name = spec["name"]
        row.description = spec["description"]
        row.definition = execution.model_dump()
        row.enabled = True
        db.add(row)
    db.commit()
