from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session, load_only

from core.db.model_defs import (
    AgentDefinition,
    AgentToolBinding,
    AgentToolDefinition,
    AgentNodeRun,
    AgentRun,
    AgentWorkflowDefinition,
)


class AgentDefinitionRepository:
    """管理定义、项目覆盖与工具绑定；事务由调用方提交。"""

    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_agent(
        self,
        *,
        project_id: int,
        agent_key: str,
        enabled_only: bool = True,
    ) -> AgentDefinition | None:
        # 项目覆盖优先；没有覆盖时读取全局内置模板。
        project_query = self.db.query(AgentDefinition).filter(
            AgentDefinition.project_id == project_id,
            AgentDefinition.agent_key == agent_key,
        )
        if enabled_only:
            project_query = project_query.filter(AgentDefinition.enabled.is_(True))
        project_row = project_query.order_by(AgentDefinition.version.desc()).first()
        if project_row is not None:
            return project_row

        global_query = self.db.query(AgentDefinition).filter(
            AgentDefinition.project_id.is_(None),
            AgentDefinition.agent_key == agent_key,
            AgentDefinition.builtin.is_(True),
        )
        if enabled_only:
            global_query = global_query.filter(AgentDefinition.enabled.is_(True))
        return global_query.order_by(AgentDefinition.version.desc()).first()

    def get_tool(
        self,
        *,
        project_id: int,
        tool_key: str,
        enabled_only: bool = True,
    ) -> AgentToolDefinition | None:
        query = self.db.query(AgentToolDefinition).filter(
            AgentToolDefinition.project_id == project_id,
            AgentToolDefinition.tool_key == tool_key,
        )
        if enabled_only:
            query = query.filter(AgentToolDefinition.enabled.is_(True))
        return query.first()

    def get_workflow(
        self,
        *,
        project_id: int,
        workflow_key: str,
        enabled_only: bool = True,
    ) -> AgentWorkflowDefinition | None:
        query = self.db.query(AgentWorkflowDefinition).filter(
            AgentWorkflowDefinition.project_id == project_id,
            AgentWorkflowDefinition.workflow_key == workflow_key,
        )
        if enabled_only:
            query = query.filter(AgentWorkflowDefinition.enabled.is_(True))
        return query.order_by(AgentWorkflowDefinition.version.desc()).first()

    def list_workflow_definition_ids(
        self, *, project_id: int, workflow_key: str,
    ) -> list[int]:
        return [
            int(row[0])
            for row in (
                self.db.query(AgentWorkflowDefinition.id)
                .filter(
                    AgentWorkflowDefinition.project_id == project_id,
                    AgentWorkflowDefinition.workflow_key == workflow_key,
                )
                .all()
            )
        ]

    def list_agents(self, *, project_id: int) -> list[AgentDefinition]:
        project_rows = (
            self.db.query(AgentDefinition)
            .filter(
                AgentDefinition.project_id == project_id,
                AgentDefinition.enabled.is_(True),
            )
            .order_by(AgentDefinition.agent_key.asc(), AgentDefinition.version.desc())
            .all()
        )
        overridden_keys = {str(row.agent_key) for row in project_rows}
        global_rows = (
            self.db.query(AgentDefinition)
            .filter(
                AgentDefinition.project_id.is_(None),
                AgentDefinition.builtin.is_(True),
                AgentDefinition.enabled.is_(True),
                ~AgentDefinition.agent_key.in_(overridden_keys or {""}),
            )
            .order_by(AgentDefinition.agent_key.asc(), AgentDefinition.version.desc())
            .all()
        )
        return sorted(
            [*project_rows, *global_rows],
            key=lambda row: (str(row.agent_key), -int(row.version or 1)),
        )

    def list_tools(self, *, project_id: int) -> list[AgentToolDefinition]:
        return (
            self.db.query(AgentToolDefinition)
            .filter(
                AgentToolDefinition.project_id == project_id,
                AgentToolDefinition.enabled.is_(True),
            )
            .order_by(AgentToolDefinition.tool_key.asc())
            .all()
        )

    def list_workflows(self, *, project_id: int) -> list[AgentWorkflowDefinition]:
        return (
            self.db.query(AgentWorkflowDefinition)
            .filter(
                AgentWorkflowDefinition.project_id == project_id,
                AgentWorkflowDefinition.enabled.is_(True),
            )
            .order_by(
                AgentWorkflowDefinition.workflow_key.asc(),
                AgentWorkflowDefinition.version.desc(),
            )
            .all()
        )

    def list_agent_tools(
        self,
        agent_definition_id: int,
        *,
        project_id: int | None = None,
    ) -> list[AgentToolDefinition]:
        definition = self.db.get(AgentDefinition, agent_definition_id)
        if definition is not None and definition.project_id is None:
            # 全局模板的工具按执行项目解析，不绑定其他项目的工具行。
            tool_keys = list((definition.runtime_config or {}).get("tool_keys") or [])
            if not tool_keys or project_id is None:
                return []
            return (
                self.db.query(AgentToolDefinition)
                .filter(
                    AgentToolDefinition.project_id == project_id,
                    AgentToolDefinition.tool_key.in_([str(key) for key in tool_keys]),
                    AgentToolDefinition.enabled.is_(True),
                )
                .order_by(AgentToolDefinition.tool_key.asc())
                .all()
            )
        return (
            self.db.query(AgentToolDefinition)
            .join(
                AgentToolBinding,
                AgentToolBinding.tool_definition_id == AgentToolDefinition.id,
            )
            .filter(
                AgentToolBinding.agent_definition_id == agent_definition_id,
                AgentToolBinding.enabled.is_(True),
                AgentToolDefinition.enabled.is_(True),
            )
            .order_by(AgentToolDefinition.tool_key.asc())
            .all()
        )

    def get_project_agent_version(
        self, *, project_id: int, agent_key: str, version: int,
    ) -> AgentDefinition | None:
        return (
            self.db.query(AgentDefinition)
            .filter(
                AgentDefinition.project_id == project_id,
                AgentDefinition.agent_key == agent_key,
                AgentDefinition.version == version,
            )
            .first()
        )

    def get_workflow_version(
        self, *, project_id: int, workflow_key: str, version: int,
    ) -> AgentWorkflowDefinition | None:
        return (
            self.db.query(AgentWorkflowDefinition)
            .filter(
                AgentWorkflowDefinition.project_id == project_id,
                AgentWorkflowDefinition.workflow_key == workflow_key,
                AgentWorkflowDefinition.version == version,
            )
            .first()
        )

    def create_agent(
        self,
        *,
        user_id: int,
        project_id: int,
        agent_key: str,
        name: str,
        description: str,
        instructions: str,
        model: str,
        output_schema: dict[str, Any],
        runtime_config: dict[str, Any],
        version: int,
    ) -> AgentDefinition:
        row = AgentDefinition(
            user_id=user_id,
            project_id=project_id,
            agent_key=agent_key,
            name=name,
            description=description,
            instructions=instructions,
            model=model,
            output_schema=deepcopy(output_schema),
            runtime_config=deepcopy(runtime_config),
            version=version,
            enabled=True,
            builtin=False,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def create_workflow(
        self,
        *,
        user_id: int,
        project_id: int,
        workflow_key: str,
        name: str,
        description: str,
        definition: dict[str, Any],
        version: int,
    ) -> AgentWorkflowDefinition:
        row = AgentWorkflowDefinition(
            user_id=user_id,
            project_id=project_id,
            workflow_key=workflow_key,
            name=name,
            description=description,
            definition=deepcopy(definition),
            version=version,
            enabled=True,
            builtin=False,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def bind_tool_to_agent(
        self,
        *,
        agent: AgentDefinition,
        tool: AgentToolDefinition,
        project_id: int,
        user_id: int,
    ) -> AgentDefinition:
        if agent.project_id is None:
            inherited_tools = self.list_agent_tools(agent.id, project_id=project_id)
            # 首次覆盖同时继承已有工具，避免新增绑定时丢失模板原有能力。
            agent = self.create_agent(
                user_id=user_id,
                project_id=project_id,
                agent_key=agent.agent_key,
                name=agent.name,
                description=agent.description,
                instructions=agent.instructions,
                model=agent.model,
                output_schema=agent.output_schema,
                runtime_config=agent.runtime_config,
                version=agent.version,
            )
            tool_ids = {int(item.id) for item in inherited_tools}
            tool_ids.add(int(tool.id))
            self.db.add_all([
                AgentToolBinding(
                    agent_definition_id=agent.id,
                    tool_definition_id=tool_id,
                    enabled=True,
                )
                for tool_id in sorted(tool_ids)
            ])
        else:
            binding = (
                self.db.query(AgentToolBinding)
                .filter(
                    AgentToolBinding.agent_definition_id == agent.id,
                    AgentToolBinding.tool_definition_id == tool.id,
                )
                .first()
            )
            if binding is None:
                binding = AgentToolBinding(
                    agent_definition_id=agent.id,
                    tool_definition_id=tool.id,
                )
            binding.enabled = True
            self.db.add(binding)
        self.db.flush()
        return agent

    def disable_obsolete_tools(self, *, project_id: int, active_keys: set[str]) -> None:
        obsolete_tools = (
            self.db.query(AgentToolDefinition)
            .filter(
                AgentToolDefinition.project_id == project_id,
                AgentToolDefinition.builtin.is_(True),
                AgentToolDefinition.tool_key.notin_(active_keys),
            )
            .all()
        )
        if not obsolete_tools:
            return
        for tool in obsolete_tools:
            tool.enabled = False
        bindings = (
            self.db.query(AgentToolBinding)
            .filter(AgentToolBinding.tool_definition_id.in_([tool.id for tool in obsolete_tools]))
            .all()
        )
        for binding in bindings:
            binding.enabled = False

    def sync_builtin_tool(
        self, *, project_id: int, user_id: int, spec: dict[str, Any],
    ) -> None:
        row = self.get_tool(
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
        row.input_schema = deepcopy(spec["input_schema"])
        row.output_schema = deepcopy(spec["output_schema"])
        row.risk_level = spec["risk_level"]
        row.requires_approval = bool(spec["requires_approval"])
        row.enabled = True
        self.db.add(row)

    def disable_obsolete_agents(self, *, active_keys: set[str]) -> None:
        for agent in (
            self.db.query(AgentDefinition)
            .filter(
                AgentDefinition.project_id.is_(None),
                AgentDefinition.builtin.is_(True),
                AgentDefinition.agent_key.notin_(active_keys),
            )
            .all()
        ):
            agent.enabled = False

    def sync_builtin_agent(self, *, user_id: int, spec: dict[str, Any]) -> None:
        agent_key = str(spec["agent_key"])
        target_version = int(spec.get("version") or 1)
        existing_versions = (
            self.db.query(AgentDefinition)
            .filter(
                AgentDefinition.project_id.is_(None),
                AgentDefinition.agent_key == agent_key,
                AgentDefinition.builtin.is_(True),
            )
            .all()
        )
        row = next(
            (existing for existing in existing_versions if self._agent_matches_spec(existing, spec)),
            None,
        )
        if row is not None:
            # 相同内容的发布已经存在；只恢复目标版本的启用状态，避免每次
            # 启动都生成新的版本记录。
            for existing in existing_versions:
                existing.enabled = existing.id == row.id
            row.enabled = True
            self.db.add(row)
            return
        row = next(
            (
                existing for existing in existing_versions
                if int(existing.version or 1) == target_version
            ),
            None,
        )
        if row is not None and self._agent_is_referenced(row):
            # 已被历史节点引用的版本不可原地修改。使用递增版本保留旧
            # definition，运行级契约快照会继续绑定创建时的版本内容。
            target_version = max(
                target_version,
                max((int(item.version or 0) for item in existing_versions), default=0) + 1,
            )
            row = None
        if row is None:
            row = AgentDefinition(
                project_id=None,
                user_id=user_id,
                agent_key=agent_key,
                version=target_version,
                builtin=True,
            )
        for existing in existing_versions:
            existing.enabled = False
        row.name = spec["name"]
        row.description = spec["description"]
        row.instructions = spec["instructions"]
        row.model = spec["model"]
        row.output_schema = deepcopy(spec["output_schema"])
        row.runtime_config = deepcopy(spec["runtime_config"])
        row.enabled = True
        self.db.add(row)

    def _agent_is_referenced(self, row: AgentDefinition) -> bool:
        if self.db.query(AgentNodeRun.id).filter(
            AgentNodeRun.agent_definition_id == row.id,
        ).first() is not None:
            return True
        # pending/running/waiting_approval 运行可能还没有节点行，但已经把
        # Agent 写进执行契约；seed 不能在这段窗口原地改写定义。
        active_runs = self.db.query(AgentRun).options(
            load_only(AgentRun.id, AgentRun.run_context),
        ).filter(
            AgentRun.status.in_(("pending", "running", "waiting_approval")),
        ).all()
        for run in active_runs:
            contract = (getattr(run, "run_context", {}) or {}).get("execution_contract")
            for snapshot in (contract or {}).get("agents", []) if isinstance(contract, dict) else []:
                if isinstance(snapshot, dict) and int(snapshot.get("id") or 0) == int(row.id):
                    return True
        return False

    @staticmethod
    def _agent_matches_spec(row: AgentDefinition, spec: dict[str, Any]) -> bool:
        return (
            str(row.name or "") == str(spec["name"] or "")
            and str(row.description or "") == str(spec["description"] or "")
            and str(row.instructions or "") == str(spec["instructions"] or "")
            and str(row.model or "") == str(spec["model"] or "")
            and (row.output_schema or {}) == (spec.get("output_schema") or {})
            and (row.runtime_config or {}) == (spec.get("runtime_config") or {})
        )

    def disable_obsolete_workflows(self, *, project_id: int, active_keys: set[str]) -> None:
        for workflow in (
            self.db.query(AgentWorkflowDefinition)
            .filter(
                AgentWorkflowDefinition.project_id == project_id,
                AgentWorkflowDefinition.builtin.is_(True),
                AgentWorkflowDefinition.workflow_key.notin_(active_keys),
            )
            .all()
        ):
            workflow.enabled = False

    def sync_builtin_workflow(
        self,
        *,
        project_id: int,
        user_id: int,
        spec: dict[str, Any],
        definition: dict[str, Any],
    ) -> None:
        workflow_key = str(spec["workflow_key"])
        target_version = int(spec.get("version") or 1)
        existing_versions = (
            self.db.query(AgentWorkflowDefinition)
            .filter(
                AgentWorkflowDefinition.project_id == project_id,
                AgentWorkflowDefinition.workflow_key == workflow_key,
                AgentWorkflowDefinition.builtin.is_(True),
            )
            .all()
        )
        row = next(
            (
                existing for existing in existing_versions
                if self._workflow_matches_definition(existing, spec, definition)
            ),
            None,
        )
        if row is not None:
            for existing in existing_versions:
                existing.enabled = existing.id == row.id
            row.enabled = True
            self.db.add(row)
            return
        row = next(
            (
                existing for existing in existing_versions
                if int(existing.version or 1) == target_version
            ),
            None,
        )
        if row is not None and self._workflow_is_referenced(row):
            target_version = max(
                target_version,
                max((int(item.version or 0) for item in existing_versions), default=0) + 1,
            )
            row = None
        if row is None:
            row = AgentWorkflowDefinition(
                project_id=project_id,
                user_id=user_id,
                workflow_key=workflow_key,
                version=target_version,
                builtin=True,
            )
        for existing in existing_versions:
            existing.enabled = False
        row.name = spec["name"]
        row.description = spec["description"]
        row.definition = deepcopy(definition)
        row.enabled = True
        self.db.add(row)

    def _workflow_is_referenced(self, row: AgentWorkflowDefinition) -> bool:
        return self.db.query(AgentRun.id).filter(
            AgentRun.workflow_definition_id == row.id,
        ).first() is not None

    @staticmethod
    def _workflow_matches_definition(
        row: AgentWorkflowDefinition,
        spec: dict[str, Any],
        definition: dict[str, Any],
    ) -> bool:
        return (
            str(row.name or "") == str(spec["name"] or "")
            and str(row.description or "") == str(spec["description"] or "")
            and (row.definition or {}) == (definition or {})
        )
