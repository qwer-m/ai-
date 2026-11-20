"""Repository for project-related persistence operations."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.orm import Session

from core.db.model_defs import (
    AgentApproval,
    AgentContextRecord,
    AgentDefinition,
    AgentNodeRun,
    AgentRun,
    AgentRunEvent,
    AgentToolBinding,
    AgentToolDefinition,
    AgentWorkflowDefinition,
    Project,
)


class ProjectRepository:
    """项目持久化仓储。"""

    def __init__(self, db: Session):
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Optional[Project]:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def list_owned_projects(self, *, user_id: int) -> list[Project]:
        return (
            self.db.query(Project)
            .filter(Project.user_id == user_id)
            .order_by(Project.created_at.desc())
            .all()
        )

    def find_duplicate_name(
        self,
        *,
        user_id: int,
        name: str,
        parent_id: int | None,
        exclude_project_id: int | None = None,
    ) -> Optional[Project]:
        query = self.db.query(Project).filter(
            Project.user_id == user_id,
            Project.name == name,
            Project.parent_id == parent_id,
        )
        if exclude_project_id is not None:
            query = query.filter(Project.id != exclude_project_id)
        return query.first()

    def add(self, entity: object) -> None:
        self.db.add(entity)

    def delete(self, entity: object) -> None:
        self.db.delete(entity)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def refresh(self, entity: object) -> None:
        self.db.refresh(entity)

    def delete_project_scoped(self, model, *, project_id: int) -> int:
        return int(
            self.db.query(model)
            .filter(model.project_id == project_id)
            .delete(synchronize_session=False)
        )

    def delete_retired_project_rows(self, *, project_id: int) -> None:
        """仅在显式删除项目时处理历史外键，新安装无需创建这些表。"""
        connection = self.db.connection()
        existing = set(inspect(connection).get_table_names())
        metadata = MetaData()
        if "rag_eval_runs" in existing:
            runs = Table("rag_eval_runs", metadata, autoload_with=connection, resolve_fks=False)
            if "rag_eval_sample_results" in existing:
                results = Table("rag_eval_sample_results", metadata, autoload_with=connection, resolve_fks=False)
                self.db.execute(results.delete().where(
                    results.c.run_id.in_(select(runs.c.id).where(runs.c.project_id == project_id))
                ))
            self.db.execute(runs.delete().where(runs.c.project_id == project_id))

        for name in ("ui_error_operations", "ui_executions", "api_executions", "recall_metrics",
                     "standard_interfaces", "ui_test_cases"):
            if name not in existing:
                continue
            table = Table(name, metadata, autoload_with=connection, resolve_fks=False)
            belongs_to_project = table.c.project_id == project_id
            if "parent_id" in table.c:
                self.db.execute(table.update().where(belongs_to_project).values(parent_id=None))
            self.db.execute(table.delete().where(belongs_to_project))

    def delete_agent_platform(self, *, project_id: int) -> None:
        """按外键依赖顺序删除项目下的 Agent 平台数据。"""

        run_ids = self.db.query(AgentRun.id).filter(AgentRun.project_id == project_id)
        node_ids = self.db.query(AgentNodeRun.id).filter(
            AgentNodeRun.run_id.in_(run_ids)
        )
        agent_ids = self.db.query(AgentDefinition.id).filter(
            AgentDefinition.project_id == project_id
        )
        tool_ids = self.db.query(AgentToolDefinition.id).filter(
            AgentToolDefinition.project_id == project_id
        )
        context_records = self.db.query(AgentContextRecord).filter(AgentContextRecord.project_id == project_id)
        context_records.filter(AgentContextRecord.replaced_by_id.is_not(None)).update(
            {AgentContextRecord.replaced_by_id: None}, synchronize_session=False,
        )
        context_records.delete(synchronize_session=False)
        self.db.query(AgentApproval).filter(
            AgentApproval.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        self.db.query(AgentRunEvent).filter(
            AgentRunEvent.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        self.db.query(AgentNodeRun).filter(
            AgentNodeRun.id.in_(node_ids)
        ).delete(synchronize_session=False)
        self.db.query(AgentRun).filter(
            AgentRun.id.in_(run_ids)
        ).delete(synchronize_session=False)
        self.db.query(AgentToolBinding).filter(
            (AgentToolBinding.agent_definition_id.in_(agent_ids))
            | (AgentToolBinding.tool_definition_id.in_(tool_ids))
        ).delete(synchronize_session=False)
        self.db.query(AgentWorkflowDefinition).filter(
            AgentWorkflowDefinition.project_id == project_id
        ).delete(synchronize_session=False)
        self.db.query(AgentDefinition).filter(
            AgentDefinition.project_id == project_id
        ).delete(synchronize_session=False)
        self.db.query(AgentToolDefinition).filter(
            AgentToolDefinition.project_id == project_id
        ).delete(synchronize_session=False)

