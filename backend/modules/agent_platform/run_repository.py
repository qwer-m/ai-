from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from core.db.model_defs import (
    AgentApproval,
    AgentNodeRun,
    AgentRun,
    AgentRunEvent,
    AgentWorkflowDefinition,
    KnowledgeDocument,
    Project,
)
from .definition_repository import AgentDefinitionRepository
from .lifecycle import ACTIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES
from .retention import terminal_run_ids_to_delete
from .sources import (
    RunSourceRecord, SourceSnapshot, historical_run_source_key, source_snapshot_columns,
)


class AgentRunRepository:
    """运行、节点、事件和审批的持久化边界，共享调用方事务。"""

    def __init__(self, db: Session) -> None:
        self.db: Session = db
        self._definitions: AgentDefinitionRepository = AgentDefinitionRepository(db)

    def lock_run_creation(self, *, project_id: int, user_id: int) -> None:
        """创建与续跑共用项目行锁，跨工作流版本也只能原子地创建一个同源运行。"""
        self.db.query(Project.id).filter(
            Project.id == project_id, Project.user_id == user_id,
        ).with_for_update().one()

    def get_run(self, *, run_id: int) -> AgentRun | None:
        return self.db.query(AgentRun).filter(AgentRun.id == run_id).first()

    def get_run_for_update(self, *, run_id: int, user_id: int | None = None) -> AgentRun | None:
        """状态修改先锁定运行并读取最新值，统一与审批、取消和执行器串行化。"""
        query = self.db.query(AgentRun).filter(AgentRun.id == run_id)
        if user_id is not None:
            query = query.filter(AgentRun.user_id == user_id)
        return query.populate_existing().with_for_update().first()

    def get_owned_run(self, *, run_id: int, user_id: int) -> AgentRun | None:
        return (
            self.db.query(AgentRun)
            .filter(AgentRun.id == run_id, AgentRun.user_id == user_id)
            .first()
        )

    def get_project_document(
        self,
        *,
        project_id: int,
        document_id: int,
    ) -> KnowledgeDocument | None:
        return (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.project_id == project_id,
            )
            .first()
        )

    def resolve_source_snapshot(self, *, project_id: int, input_payload: dict[str, Any]) -> SourceSnapshot | None:
        document_id = input_payload.get("requirement_doc_id")
        if document_id is not None:
            document = self.get_project_document(project_id=project_id, document_id=int(document_id))
            if document is None:
                raise ValueError("需求文档不存在或无权访问")
            return SourceSnapshot.from_document(document)
        requirement = str(input_payload.get("requirement") or "").strip()
        return SourceSnapshot.from_text(requirement) if requirement else None

    def list_run_sources(
        self,
        *,
        project_id: int,
        user_id: int,
        workflow_definition_ids: list[int],
        statuses: set[str] | frozenset[str],
        for_update: bool = False,
    ) -> list[RunSourceRecord]:
        """三条来源链路共用快照投影，只读取标识和小型来源 JSON，不加载整份运行产物。"""
        query = self.db.query(
            AgentRun.id, AgentRun.finished_at, AgentRun.input_payload, AgentRun.status,
            *source_snapshot_columns(AgentRun),
        ).filter(
            AgentRun.project_id == project_id,
            AgentRun.user_id == user_id,
            AgentRun.workflow_definition_id.in_(workflow_definition_ids),
            AgentRun.status.in_(statuses),
        )
        if for_update:
            # REPEATABLE READ 普通查询可能仍看到等待创建锁前的旧快照。
            query = query.with_for_update()
        rows = query.all()
        resolved = []
        for run_id, finished_at, input_payload, status, *sources in rows:
            payload = dict(input_payload or {})
            resolved.append(RunSourceRecord(
                int(run_id), finished_at, historical_run_source_key(payload, *sources), str(status),
            ))
        return resolved

    def get_active_run_for_source(
        self,
        *,
        project_id: int,
        user_id: int,
        workflow_key: str,
        source: SourceSnapshot | None,
    ) -> AgentRun | None:
        """查找同一来源尚未结束的运行，避免重复请求占满串行队列。"""

        workflow_definition_ids = self._definitions.list_workflow_definition_ids(
            project_id=project_id,
            workflow_key=workflow_key,
        )
        if not workflow_definition_ids:
            return None
        active_rows = self.list_run_sources(
            project_id=project_id, user_id=user_id,
            workflow_definition_ids=workflow_definition_ids,
            statuses=ACTIVE_RUN_STATUSES,
            for_update=True,
        )
        if not active_rows:
            return None

        target_key = source.key if source else "workflow"
        matching_id = min(
            (
                row.run_id for row in active_rows if row.source_key == target_key
            ),
            default=None,
        )
        return self.get_run_for_update(run_id=matching_id) if matching_id is not None else None

    def list_runs(
        self,
        *,
        project_id: int,
        user_id: int,
        limit: int,
        workflow_key: str | None = None,
    ) -> list[AgentRun]:
        # 先只排序轻量主键，避免 MySQL 对包含大 run_context JSON 的整行做 filesort。
        query = self.db.query(AgentRun.id).filter(
            AgentRun.project_id == project_id,
            AgentRun.user_id == user_id,
        )
        if workflow_key is not None:
            workflow_definition_ids = self._definitions.list_workflow_definition_ids(
                project_id=project_id,
                workflow_key=workflow_key,
            )
            if not workflow_definition_ids:
                return []
            query = query.filter(
                AgentRun.workflow_definition_id.in_(workflow_definition_ids)
            )
        ordered_ids = [
            int(row[0])
            for row in (
                query
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

    def prune_terminal_run_history(
        self,
        *,
        project_id: int,
        user_id: int,
        workflow_definition_id: int,
        keep_run_id: int,
        limit: int,
    ) -> list[int]:
        """删除同一需求来源的旧终态运行，其他文档的生成结果不受影响。"""

        # 运行产物包含大体积 JSON；清理历史时只读取轻量标识，避免 MySQL
        # 为整行结果排序而耗尽 sort buffer。
        workflow_definition_ids = [int(workflow_definition_id)]
        workflow = self.db.get(AgentWorkflowDefinition, workflow_definition_id)
        if workflow is not None:
            workflow_definition_ids = self._definitions.list_workflow_definition_ids(
                project_id=project_id,
                workflow_key=workflow.workflow_key,
            ) or workflow_definition_ids

        terminal_run_rows = sorted(
            self.list_run_sources(
                project_id=project_id, user_id=user_id,
                workflow_definition_ids=workflow_definition_ids,
                statuses=TERMINAL_RUN_STATUSES,
            ),
            key=lambda row: (row.finished_at or datetime.min, row.run_id), reverse=True,
        )
        delete_ids = terminal_run_ids_to_delete(
            terminal_run_rows,
            keep_run_id=keep_run_id,
            limit=limit,
        )
        if not delete_ids:
            return []

        self.db.query(AgentRun).filter(
            AgentRun.parent_run_id.in_(delete_ids)
        ).update({AgentRun.parent_run_id: None}, synchronize_session=False)
        self.db.query(AgentApproval).filter(
            AgentApproval.run_id.in_(delete_ids)
        ).delete(synchronize_session=False)
        self.db.query(AgentRunEvent).filter(
            AgentRunEvent.run_id.in_(delete_ids)
        ).delete(synchronize_session=False)
        self.db.query(AgentNodeRun).filter(
            AgentNodeRun.run_id.in_(delete_ids)
        ).delete(synchronize_session=False)
        self.db.query(AgentRun).filter(AgentRun.id.in_(delete_ids)).delete(
            synchronize_session=False
        )
        self.db.flush()
        return delete_ids

    def get_active_run(self, *, project_id: int, user_id: int) -> AgentRun | None:
        """返回工作台当前应展示的活动运行，不读取任何节点大字段。"""

        rows = (
            self.db.query(AgentRun)
            .options(
                load_only(
                    AgentRun.id,
                    AgentRun.project_id,
                    AgentRun.user_id,
                    AgentRun.workflow_definition_id,
                    AgentRun.status,
                    AgentRun.current_node_key,
                    AgentRun.input_payload,
                    AgentRun.error_message,
                    AgentRun.parent_run_id,
                    AgentRun.task_id,
                    AgentRun.created_at,
                    AgentRun.started_at,
                    AgentRun.finished_at,
                )
            )
            .filter(
                AgentRun.project_id == project_id,
                AgentRun.user_id == user_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
            .all()
        )
        if not rows:
            return None
        running = [row for row in rows if row.status == "running"]
        if running:
            return max(running, key=lambda row: int(row.id or 0))
        waiting = [row for row in rows if row.status == "waiting_approval"]
        if waiting:
            return max(waiting, key=lambda row: int(row.id or 0))
        pending = [row for row in rows if row.status == "pending"]
        return min(pending, key=lambda row: int(row.id or 0)) if pending else None

    def list_node_runs(self, *, run_id: int) -> list[AgentNodeRun]:
        # JSON 输入/输出可能很大，让 MySQL 对整行做 filesort 会耗尽 sort_buffer。
        # 利用 run_id 索引无排序读取，再在 Python 中按主键稳定排序。
        rows = (
            self.db.query(AgentNodeRun)
            .filter(AgentNodeRun.run_id == run_id)
            .all()
        )
        return sorted(rows, key=lambda item: int(item.id or 0))

    def finalize_unfinished_node_runs(
        self,
        *,
        run_id: int,
        status: str,
        finished_at: datetime,
        error_message: str,
    ) -> int:
        """运行终止时批量结束未完成节点，只查询主键，不读取节点大 JSON。"""
        if status not in {"failed", "cancelled"}:
            raise ValueError("未完成节点只能由失败或取消的运行结束")
        # 先保存当前事务的节点检查点，再更新状态，避免稍后的 ORM 刷新覆盖批量终态。
        self.db.flush()
        return self.db.query(AgentNodeRun).filter(
            AgentNodeRun.run_id == run_id,
            AgentNodeRun.status.in_(ACTIVE_RUN_STATUSES),
        ).update(
            {
                AgentNodeRun.status: status,
                AgentNodeRun.finished_at: finished_at,
                AgentNodeRun.error_message: error_message,
            },
            synchronize_session="fetch",
        )

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

    def list_reusable_node_run_ids(
        self,
        *,
        run: AgentRun,
        node_key: str,
        node_type: str,
        agent_definition_id: int,
        limit: int = 20,
    ) -> list[int]:
        """返回同一项目与工作流中可作为复用候选的成功节点主键。

        查询阶段只读取主键，防止节点输入输出的大 JSON 参与数据库排序。
        候选运行必须整轮成功，失败或取消运行的中间结果不得复用。
        """

        rows = (
            self.db.query(AgentNodeRun.id)
            .join(AgentRun, AgentRun.id == AgentNodeRun.run_id)
            .filter(
                AgentRun.id < run.id,
                AgentRun.user_id == run.user_id,
                AgentRun.project_id == run.project_id,
                AgentRun.workflow_definition_id == run.workflow_definition_id,
                AgentRun.status == "success",
                AgentNodeRun.node_key == node_key,
                AgentNodeRun.node_type == node_type,
                AgentNodeRun.agent_definition_id == agent_definition_id,
                AgentNodeRun.status == "success",
            )
            .order_by(AgentNodeRun.id.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        return [int(row[0]) for row in rows]

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
