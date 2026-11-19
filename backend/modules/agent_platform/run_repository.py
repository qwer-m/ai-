from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, load_only

from core.db.model_defs import (
    AgentApproval,
    AgentNodeRun,
    AgentRun,
    AgentRunEvent,
    AgentWorkflowDefinition,
    Project,
)
from modules.knowledge_base_components.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from .definition_repository import AgentDefinitionRepository
from .input_snapshots import pack_node_input, restore_node_input
from .lifecycle import ACTIVE_RUN_STATUSES
from .sources import (
    RunSourceRecord, SourceSnapshot, historical_run_source_key, source_snapshot_columns,
)
from .usage import interrupt_node_pending_requests


class AgentRunRepository:
    """运行、节点、事件和审批的持久化边界，共享调用方事务。"""

    def __init__(self, db: Session) -> None:
        self.db: Session = db
        self._definitions: AgentDefinitionRepository = AgentDefinitionRepository(db)
        self._documents = KnowledgeDocumentRepository(db)

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

    def get_run_control_for_update(self, *, run_id: int) -> AgentRun | None:
        """行动热事务只锁控制列，不装载完整运行产物。"""
        return self.db.query(AgentRun).options(load_only(
            AgentRun.id, AgentRun.user_id, AgentRun.project_id, AgentRun.status,
            AgentRun.claim_token, AgentRun.heartbeat_at, AgentRun.lease_expires_at,
        )).filter(AgentRun.id == run_id).populate_existing().with_for_update().first()

    def get_owned_run(self, *, run_id: int, user_id: int) -> AgentRun | None:
        return (
            self.db.query(AgentRun)
            .filter(AgentRun.id == run_id, AgentRun.user_id == user_id)
            .first()
        )

    def get_owned_run_summary(self, *, run_id: int, user_id: int) -> AgentRun | None:
        """只读取运行摘要字段，避免详情接口的 JSON 大字段进入轮询链路。"""

        query = (
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
            .filter(AgentRun.id == run_id, AgentRun.user_id == user_id)
        )

        # 摘要需要展示尝试身份和契约 hash，但不能因为读取它们而加载整份
        # run_context。MySQL JSON_EXTRACT 只投影这几个标量；测试替身没有
        # add_columns 时仍沿用上面的轻量查询。
        add_columns = getattr(query, "add_columns", None)
        if callable(add_columns):
            dialect = getattr(getattr(self.db, "bind", None), "dialect", None)
            dialect_name = str(getattr(dialect, "name", "") or "").lower()

            if dialect_name not in {"mysql", "sqlite"}:
                row = query.first()
                return row

            def _json_scalar(path: str):
                expression = func.json_extract(AgentRun.run_context, path)
                # MySQL 的 JSON_EXTRACT 对字符串返回带引号值；SQLite 已经
                # 返回标量。按方言拆开，避免 SQLite 测试库调用不存在的
                # JSON_UNQUOTE。
                return (
                    func.json_unquote(expression)
                    if dialect_name == "mysql"
                    else expression
                )

            query = query.add_columns(
                _json_scalar("$.run_attempt").label("_summary_run_attempt"),
                _json_scalar("$.input_hash").label("_summary_input_hash"),
                _json_scalar("$.logical_task_id").label("_summary_logical_task_id"),
                _json_scalar("$.attempt_id").label("_summary_attempt_id"),
                _json_scalar("$.checkpoint_schema_version").label("_summary_checkpoint_schema_version"),
                _json_scalar("$.execution_contract.hash").label("_summary_contract_hash"),
                _json_scalar("$.attempt_display_offset").label("_summary_attempt_display_offset"),
            )

        row = query.first()
        if row is None:
            return None
        # SQLAlchemy 2.x 返回 Row，旧测试替身和部分方言可能返回 tuple；
        # 两者都按位置读取，避免依赖 ORM 实体标签的方言差异。
        if isinstance(row, tuple) or hasattr(row, "_mapping"):
            values = tuple(row)
        else:
            return row
        if len(values) < 7:
            return values[0] if values else None
        run, run_attempt, input_hash, logical_task_id, attempt_id, checkpoint_schema_version, contract_hash = values[:7]

        def _text(value: Any) -> str | None:
            if value is None:
                return None
            text = str(value).strip()
            return text or None

        summary_context: dict[str, Any] = {
            "run_attempt": int(_text(run_attempt) or 1),
            "attempt_display_offset": int(_text(values[7] if len(values) > 7 else None) or 0),
            "input_hash": _text(input_hash),
            "logical_task_id": _text(logical_task_id),
            "attempt_id": _text(attempt_id),
            "checkpoint_schema_version": (
                int(_text(checkpoint_schema_version))
                if _text(checkpoint_schema_version) is not None
                else None
            ),
        }
        if _text(contract_hash):
            summary_context["execution_contract"] = {"hash": _text(contract_hash)}
        # 这是仅供 serialize_run_summary 使用的投影，不是运行上下文的替代品。
        setattr(run, "_summary_context", summary_context)
        return run

    def get_owned_run_metrics(self, *, run_id: int, user_id: int) -> AgentRun | None:
        """读取 metrics 所需字段，禁止把完整运行输入/产物带入评估查询。"""

        return (
            self.db.query(AgentRun)
            .options(
                load_only(
                    AgentRun.id,
                    AgentRun.user_id,
                    AgentRun.status,
                    AgentRun.run_context,
                    AgentRun.started_at,
                    AgentRun.finished_at,
                )
            )
            .filter(AgentRun.id == run_id, AgentRun.user_id == user_id)
            .first()
        )

    def resolve_source_snapshot(self, *, project_id: int, input_payload: dict[str, Any]) -> SourceSnapshot | None:
        document_id = input_payload.get("requirement_doc_id")
        if document_id is not None:
            document = self._documents.get_project_doc(project_id=project_id, doc_id=int(document_id))
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


    def get_active_run(
        self,
        *,
        project_id: int,
        user_id: int,
        workflow_key: str,
    ) -> AgentRun | None:
        """返回工作台当前应展示的活动运行，不读取任何节点大字段。

        工作台必须按业务工作流读取活动运行。过滤先解析工作流定义 id，再在 AgentRun 上执行，
        避免把大 JSON 运行字段带入关联排序。
        """

        workflow_definition_ids = self._definitions.list_workflow_definition_ids(
            project_id=project_id,
            workflow_key=workflow_key,
        )
        if not workflow_definition_ids:
            return None

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
        )
        rows = rows.filter(
            AgentRun.workflow_definition_id.in_(workflow_definition_ids)
        )
        rows = rows.all()
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

    def read_node_input(self, node_run: AgentNodeRun) -> dict[str, Any]:
        """运行时读取完整输入，引用仍保留在持久化字段中。"""
        return restore_node_input(node_run, lambda node_id: self.db.get(AgentNodeRun, node_id))

    def write_node_input(self, node_run: AgentNodeRun, payload: dict[str, Any]) -> None:
        """完整输入内容寻址去重，节点与重试只保存稳定引用。"""
        from .checkpoint_storage import store_node_input
        store_node_input(self.db, node_run, payload)

    def finalize_unfinished_node_runs(
        self,
        *,
        run_id: int,
        status: str,
        finished_at: datetime,
        error_message: str,
        node_run_id: int | None = None,
    ) -> int:
        """持有 Run 锁时收尾失权或终止尝试的预算，再结束未完成节点。"""
        if status not in {"failed", "cancelled"}:
            raise ValueError("未完成节点只能由失败或取消的运行结束")
        # 先保存当前事务的节点检查点，再更新状态，避免稍后的 ORM 刷新覆盖批量终态。
        self.db.flush()
        node_ids = self.db.query(AgentNodeRun.id).filter(
            AgentNodeRun.run_id == run_id,
            or_(
                AgentNodeRun.status.in_(ACTIVE_RUN_STATUSES),
                # 异常处理器可能已给当前节点设置终态；只收尾调用方明确指定的节点。
                (AgentNodeRun.id == node_run_id) & (AgentNodeRun.status == status),
            ),
        ).order_by(AgentNodeRun.id).all()
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise LookupError("终止节点对应的运行不存在")
        for (node_id,) in node_ids:
            # 只加载当前账本，不排序或读取输入、输出的大 JSON；锁顺序始终为 Run 再 Node。
            node = self.db.query(AgentNodeRun).options(load_only(AgentNodeRun.sdk_state)).filter(
                AgentNodeRun.id == node_id,
            ).populate_existing().with_for_update().one()
            interrupt_node_pending_requests(run, node)
            node.status = status
            node.finished_at = finished_at
            node.error_message = error_message
            self.db.add(node)
        return len(node_ids)

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

    def first_node_started_at(self, *, run_id: int, node_key: str) -> datetime | None:
        """阶段预算以第一次执行为起点；只读取时间标量，避免加载历史大 JSON。"""
        return self.db.query(func.min(AgentNodeRun.started_at)).filter(
            AgentNodeRun.run_id == run_id, AgentNodeRun.node_key == node_key,
        ).scalar()

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
        纯计算节点的复用不依赖父运行成败；准入方仍须核对当前完整语义并重新验收。
        """
        workflow = self.db.get(AgentWorkflowDefinition, run.workflow_definition_id)
        if workflow is None:
            return []
        workflow_ids = self._definitions.list_workflow_definition_ids(
            project_id=run.project_id, workflow_key=workflow.workflow_key,
        )
        rows = (
            self.db.query(AgentNodeRun.id)
            .join(AgentRun, AgentRun.id == AgentNodeRun.run_id)
            .filter(
                AgentRun.id < run.id,
                AgentRun.user_id == run.user_id,
                AgentRun.project_id == run.project_id,
                AgentRun.workflow_definition_id.in_(workflow_ids),
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

    def list_events_after(
        self,
        *,
        run_id: int,
        after_sequence: int,
        limit: int,
    ) -> list[AgentRunEvent]:
        """按持久序号读取增量事件，避免轮询重复传输历史 payload。"""

        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence 必须是非负整数")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit 必须是正整数")
        return (
            self.db.query(AgentRunEvent)
            .filter(
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.sequence > after_sequence,
            )
            .order_by(AgentRunEvent.sequence.asc())
            .limit(limit)
            .all()
        )

    def list_event_summaries(self, *, run_id: int, limit: int) -> list[AgentRunEvent]:
        """只读取轨迹计数所需字段，避免 metrics 接口加载事件大 payload。"""

        return (
            self.db.query(AgentRunEvent)
            .options(
                load_only(
                    AgentRunEvent.id,
                    AgentRunEvent.sequence,
                    AgentRunEvent.event_type,
                )
            )
            .filter(AgentRunEvent.run_id == run_id)
            .order_by(AgentRunEvent.sequence.asc())
            .limit(limit)
            .all()
        )

    def count_events(self, *, run_id: int) -> int:
        """返回事件总数，供轨迹摘要区分完整统计和截断样本。"""

        return int(
            self.db.query(func.count(AgentRunEvent.id))
            .filter(AgentRunEvent.run_id == run_id)
            .scalar()
            or 0
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
