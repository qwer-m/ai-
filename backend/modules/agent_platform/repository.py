from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.db.model_defs import (
    AgentApproval,
    AgentDefinition,
    AgentNodeRun,
    AgentRun,
    AgentRunEvent,
    AgentToolBinding,
    AgentToolDefinition,
    AgentWorkflowDefinition,
    Project,
)


class AgentPlatformRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def get_agent(
        self,
        *,
        project_id: int,
        agent_key: str,
        enabled_only: bool = True,
    ) -> AgentDefinition | None:
        query = self.db.query(AgentDefinition).filter(
            AgentDefinition.project_id == project_id,
            AgentDefinition.agent_key == agent_key,
        )
        if enabled_only:
            query = query.filter(AgentDefinition.enabled.is_(True))
        return query.order_by(AgentDefinition.version.desc()).first()

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

    def list_agents(self, *, project_id: int) -> list[AgentDefinition]:
        return (
            self.db.query(AgentDefinition)
            .filter(
                AgentDefinition.project_id == project_id,
                AgentDefinition.enabled.is_(True),
            )
            .order_by(AgentDefinition.agent_key.asc(), AgentDefinition.version.desc())
            .all()
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

    def list_agent_tools(self, agent_definition_id: int) -> list[AgentToolDefinition]:
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

    def get_run(self, *, run_id: int) -> AgentRun | None:
        return self.db.query(AgentRun).filter(AgentRun.id == run_id).first()

    def get_owned_run(self, *, run_id: int, user_id: int) -> AgentRun | None:
        return (
            self.db.query(AgentRun)
            .filter(AgentRun.id == run_id, AgentRun.user_id == user_id)
            .first()
        )

    def list_runs(
        self,
        *,
        project_id: int,
        user_id: int,
        limit: int,
    ) -> list[AgentRun]:
        # 先只排序轻量主键，避免 MySQL 对包含大 run_context JSON 的整行做 filesort。
        ordered_ids = [
            int(row[0])
            for row in (
                self.db.query(AgentRun.id)
                .filter(
                    AgentRun.project_id == project_id,
                    AgentRun.user_id == user_id,
                )
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .limit(limit)
                .all()
            )
        ]
        if not ordered_ids:
            return []
        rows = (
            self.db.query(AgentRun)
            .filter(AgentRun.id.in_(ordered_ids))
            .all()
        )
        by_id = {int(row.id): row for row in rows}
        return [by_id[run_id] for run_id in ordered_ids if run_id in by_id]

    def list_node_runs(self, *, run_id: int) -> list[AgentNodeRun]:
        # JSON 输入/输出可能很大，让 MySQL 对整行做 filesort 会耗尽 sort_buffer。
        # 利用 run_id 索引无排序读取，再在 Python 中按主键稳定排序。
        rows = (
            self.db.query(AgentNodeRun)
            .filter(AgentNodeRun.run_id == run_id)
            .all()
        )
        return sorted(rows, key=lambda item: int(item.id or 0))

    def latest_node_run(self, *, run_id: int, node_key: str) -> AgentNodeRun | None:
        # 只对轻量主键排序，避免历史节点的大 JSON 进入 MySQL filesort。
        latest_id = (
            self.db.query(AgentNodeRun.id)
            .filter(AgentNodeRun.run_id == run_id, AgentNodeRun.node_key == node_key)
            .order_by(AgentNodeRun.attempt.desc(), AgentNodeRun.id.desc())
            .limit(1)
            .scalar()
        )
        if latest_id is None:
            return None
        return self.db.get(AgentNodeRun, int(latest_id))

    def next_node_attempt(self, *, run_id: int, node_key: str) -> int:
        maximum = (
            self.db.query(func.max(AgentNodeRun.attempt))
            .filter(AgentNodeRun.run_id == run_id, AgentNodeRun.node_key == node_key)
            .scalar()
        )
        return int(maximum or 0) + 1

    def list_events(self, *, run_id: int, limit: int) -> list[AgentRunEvent]:
        return (
            self.db.query(AgentRunEvent)
            .filter(AgentRunEvent.run_id == run_id)
            .order_by(AgentRunEvent.sequence.asc())
            .limit(limit)
            .all()
        )

    def list_approvals(self, *, run_id: int) -> list[AgentApproval]:
        return (
            self.db.query(AgentApproval)
            .filter(AgentApproval.run_id == run_id)
            .order_by(AgentApproval.id.asc())
            .all()
        )

    def append_event(
        self,
        *,
        run_id: int,
        event_type: str,
        payload: dict[str, Any],
        node_run_id: int | None = None,
    ) -> AgentRunEvent:
        # 同一个 Run 的 Worker、取消和重试请求可能来自不同事务。
        # 先锁定 Run 行，再分配连续序号，避免并发读取到相同的 max(sequence)。
        locked_run_id = (
            self.db.query(AgentRun.id)
            .filter(AgentRun.id == run_id)
            .with_for_update()
            .scalar()
        )
        if locked_run_id is None:
            raise ValueError(f"agent_run_not_found:{run_id}")
        # 聚合 MAX 在 MySQL REPEATABLE READ 下仍可能读取事务旧快照。
        # 对最新事件做锁定读，确保等待并发事务提交后看到当前序号。
        latest_sequence = (
            self.db.query(AgentRunEvent.sequence)
            .filter(AgentRunEvent.run_id == run_id)
            .order_by(AgentRunEvent.sequence.desc())
            .limit(1)
            .with_for_update()
            .scalar()
        )
        sequence = int(latest_sequence or 0) + 1
        event = AgentRunEvent(
            run_id=run_id,
            node_run_id=node_run_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
        )
        self.db.add(event)
        self.db.flush()
        return event

    def latest_approval(
        self,
        *,
        run_id: int,
        node_key: str,
    ) -> AgentApproval | None:
        return (
            self.db.query(AgentApproval)
            .join(AgentNodeRun, AgentNodeRun.id == AgentApproval.node_run_id)
            .filter(
                AgentApproval.run_id == run_id,
                AgentNodeRun.node_key == node_key,
            )
            .order_by(AgentApproval.id.desc())
            .first()
        )

    def get_owned_approval(
        self,
        *,
        approval_id: int,
        user_id: int,
    ) -> AgentApproval | None:
        return (
            self.db.query(AgentApproval)
            .join(AgentRun, AgentRun.id == AgentApproval.run_id)
            .filter(AgentApproval.id == approval_id, AgentRun.user_id == user_id)
            .first()
        )

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, value: Any) -> None:
        self.db.refresh(value)
