from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

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
        # 项目覆盖优先；没有覆盖时回退到全局内置模板。
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
            # 全局模板的工具按当前项目解析，避免全局定义绑定某个项目的工具行。
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

    def get_run(self, *, run_id: int) -> AgentRun | None:
        return self.db.query(AgentRun).filter(AgentRun.id == run_id).first()

    def get_owned_run(self, *, run_id: int, user_id: int) -> AgentRun | None:
        return (
            self.db.query(AgentRun)
            .filter(AgentRun.id == run_id, AgentRun.user_id == user_id)
            .first()
        )

    def get_active_run_for_source(
        self,
        *,
        project_id: int,
        user_id: int,
        workflow_definition_id: int,
        requirement_doc_id: int | None,
        requirement: str,
    ) -> AgentRun | None:
        """查找同一来源尚未结束的运行，避免重复请求占满串行队列。"""

        active_ids = [
            int(row[0])
            for row in (
                self.db.query(AgentRun.id)
                .filter(
                    AgentRun.project_id == project_id,
                    AgentRun.user_id == user_id,
                    AgentRun.workflow_definition_id == workflow_definition_id,
                    AgentRun.status.in_({"pending", "running", "waiting_approval"}),
                )
                .order_by(AgentRun.id.asc())
                .all()
            )
        ]
        if not active_ids:
            return None
        rows = self.db.query(AgentRun).filter(AgentRun.id.in_(active_ids)).all()
        by_id = {int(row.id): row for row in rows}
        normalized_requirement = requirement.strip()
        for run_id in active_ids:
            run = by_id.get(run_id)
            if run is None:
                continue
            payload = dict(run.input_payload or {})
            existing_doc_id = payload.get("requirement_doc_id")
            if requirement_doc_id is not None:
                if existing_doc_id is not None and int(existing_doc_id) == requirement_doc_id:
                    return run
                continue
            if existing_doc_id is None and str(payload.get("requirement") or "").strip() == normalized_requirement:
                return run
        return None

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

    def prune_terminal_run_history(
        self,
        *,
        project_id: int,
        user_id: int,
        workflow_definition_id: int,
        keep_run_id: int,
        limit: int,
    ) -> list[int]:
        """删除更旧的终态运行及其明细，只保留当前运行和有限历史。"""

        # 运行产物包含大体积 JSON；清理历史时只读取轻量标识，避免 MySQL
        # 为整行结果排序而耗尽 sort buffer。
        terminal_run_rows = (
            self.db.query(AgentRun.id, AgentRun.finished_at)
            .filter(
                AgentRun.project_id == project_id,
                AgentRun.user_id == user_id,
                AgentRun.workflow_definition_id == workflow_definition_id,
                AgentRun.status.in_({"success", "failed", "cancelled"}),
            )
            .order_by(AgentRun.finished_at.desc(), AgentRun.id.desc())
            .all()
        )
        keep_ids = {int(keep_run_id)}
        for run_id, _finished_at in terminal_run_rows:
            if len(keep_ids) >= max(1, int(limit)):
                break
            keep_ids.add(int(run_id))
        delete_ids = [
            int(run_id)
            for run_id, _finished_at in terminal_run_rows
            if int(run_id) not in keep_ids
        ]
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
                AgentRun.status.in_({"pending", "running", "waiting_approval"}),
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

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, value: Any) -> None:
        self.db.refresh(value)
