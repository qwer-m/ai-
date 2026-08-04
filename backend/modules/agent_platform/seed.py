from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.model_defs import (
    AgentDefinition,
    AgentToolBinding,
    AgentToolDefinition,
    AgentWorkflowDefinition,
)
from .contracts import WorkflowGraph
from .registry import (
    BUILTIN_AGENT_SPECS,
    BUILTIN_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS,
    tool_registry,
)
from .repository import AgentPlatformRepository


def seed_builtin_definitions(*, db: Session, project_id: int, user_id: int) -> None:
    """把内置模板写入项目数据，运行时只读取数据库定义。"""

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
    for agent in (
        db.query(AgentDefinition)
        .filter(
            AgentDefinition.project_id == project_id,
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
        row = repo.get_agent(
            project_id=project_id,
            agent_key=str(spec["agent_key"]),
            enabled_only=False,
        )
        if row is None:
            row = AgentDefinition(
                project_id=project_id,
                user_id=user_id,
                agent_key=spec["agent_key"],
                version=1,
                builtin=True,
            )
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
        graph = WorkflowGraph.model_validate(spec["definition"])
        row = repo.get_workflow(
            project_id=project_id,
            workflow_key=str(spec["workflow_key"]),
            enabled_only=False,
        )
        if row is None:
            row = AgentWorkflowDefinition(
                project_id=project_id,
                user_id=user_id,
                workflow_key=spec["workflow_key"],
                version=1,
                builtin=True,
            )
        row.name = spec["name"]
        row.description = spec["description"]
        row.definition = graph.model_dump()
        row.enabled = True
        db.add(row)
    db.commit()
