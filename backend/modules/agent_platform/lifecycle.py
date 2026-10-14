"""运行状态转换的唯一入口；事务和行锁由调用方持有。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.db.model_defs import AgentRun

if TYPE_CHECKING:
    from .run_repository import AgentRunRepository


TERMINAL_RUN_STATUSES = frozenset({"success", "failed", "cancelled"})
ACTIVE_RUN_STATUSES = frozenset({"pending", "running", "waiting_approval"})
_ALLOWED_TRANSITIONS = {
    "pending": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"pending", "waiting_approval", "success", "failed", "cancelled"}),
    "waiting_approval": frozenset({"pending", "failed", "cancelled"}),
    **{status: frozenset() for status in TERMINAL_RUN_STATUSES},
}


class InvalidRunTransition(ValueError):
    """已结束或不处于预期阶段的运行不能被迟到操作恢复。"""


class RunLeaseLost(RuntimeError):
    """执行器已失去运行所有权，禁止继续续租或提交作用。"""


def new_claim_token() -> str:
    """每次认领单独生成所有权凭据，不能复用可能重复投递的任务 ID。"""

    return uuid4().hex


def assert_run_claim(
    run: AgentRun, expected_claim_token: str, *, now: datetime,
) -> None:
    """调用方须先锁定并刷新 Run；检查结果只在当前事务内有效。"""

    if (
        not expected_claim_token or run.status != "running"
        or run.claim_token != expected_claim_token
        or run.lease_expires_at is None or run.lease_expires_at <= now
    ):
        raise RunLeaseLost(f"Agent Run {run.id} 的执行租约已失效")


def release_run_lease(run: AgentRun) -> None:
    run.claim_token = None
    run.heartbeat_at = None
    run.lease_expires_at = None


def renew_run_lease(
    run: AgentRun, *, now: datetime, lease_seconds: int,
    expected_claim_token: str | None = None,
) -> bool:
    if expected_claim_token is not None:
        assert_run_claim(run, expected_claim_token, now=now)
    if run.status != "running":
        return False
    run.heartbeat_at = now
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    return True


def transition_run(
    repo: AgentRunRepository,
    run: AgentRun,
    target: str,
    *,
    event_type: str,
    payload: dict[str, Any],
    now: datetime | None = None,
    error_message: str = "",
    node_run_id: int | None = None,
    actor_user_id: int | None = None,
) -> bool:
    """统一更新状态、租约、终止时间、待审批记录和审计事件，不自行提交。"""
    current = str(run.status)
    if current == target:
        return False
    if target not in _ALLOWED_TRANSITIONS.get(current, ()):
        raise InvalidRunTransition(f"运行状态不能从 {current} 转换为 {target}")
    at = now or datetime.utcnow()
    run.status = target
    run.error_message = error_message
    run.finished_at = at if target in TERMINAL_RUN_STATUSES else None
    if target != "running":
        release_run_lease(run)
    if target == "pending":
        run.task_id = None
    elif target == "success":
        run.current_node_key = None
    if target in TERMINAL_RUN_STATUSES:
        for approval in repo.list_approvals(run_id=run.id):
            if approval.status != "pending":
                continue
            approval.status = "rejected"
            approval.decided_at = at
            approval.decided_by_user_id = actor_user_id
            approval.decision_payload = {"reason": "运行已结束，审批失效", "run_status": target}
            repo.db.add(approval)
    if target in {"failed", "cancelled"}:
        repo.finalize_unfinished_node_runs(
            run_id=run.id,
            status=target,
            finished_at=at,
            error_message=error_message or ("运行已取消，节点终止" if target == "cancelled" else "运行失败，节点终止"),
            node_run_id=node_run_id,
        )
    repo.db.add(run)
    repo.append_event(
        run_id=run.id, node_run_id=node_run_id, event_type=event_type, payload=payload,
    )
    return True
