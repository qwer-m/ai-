"""运行状态与投递意图同事务保存；队列回执不授予 Worker 执行权。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentRunDispatch
from core.settings.config import settings
from modules.orchestration.background_task_governance import BackgroundTaskKind, submit_background_task
from modules.orchestration.task_dispatcher import TaskDispatchResult
from modules.orchestration.task_names import TaskName
from .run_repository import AgentRunRepository
from .lifecycle import transition_run


_DELIVERABLE = ("pending", "publishing", "published")


def _latest_dispatch(db: Session, run_id: int) -> AgentRunDispatch | None:
    return db.query(AgentRunDispatch).filter_by(run_id=run_id).order_by(
        AgentRunDispatch.generation.desc(),
    ).populate_existing().with_for_update().first()


def prepare_agent_run_dispatch(db: Session, run: AgentRun, *, reason: str, new_cycle: bool = False) -> AgentRunDispatch:
    """持有 Run 行锁或新建事务时调用；与 pending 同时提交，不自行提交。

    dispatch=False 路径不创建意图，后台恢复不能擅自启动此类运行。
    审批或失租恢复创建新代际，旧队列消息由 Run 的预期 task_id 拒绝。
    """
    if run.status != "pending":
        raise ValueError("只有待运行状态可以记录投递意图")
    db.flush()
    previous = _latest_dispatch(db, run.id)
    if previous is not None and not new_cycle:
        if previous.task_id == run.task_id and previous.status in _DELIVERABLE:
            return previous
        raise ValueError("已有投递周期，恢复必须显式创建新代际")
    if previous is not None and previous.status in _DELIVERABLE:
        previous.status = "superseded"
        previous.publish_token = None
        previous.publish_lease_expires_at = None
        db.add(previous)
    now = datetime.utcnow()
    entry = AgentRunDispatch(
        run_id=run.id, generation=previous.generation + 1 if previous is not None else 1,
        task_id=str(uuid4()), reason=reason, status="pending", attempt_count=0,
        next_attempt_at=now, last_error="", created_at=now, updated_at=now,
    )
    run.task_id = entry.task_id
    db.add_all([entry, run])
    db.flush()
    return entry


def _delivery_result(entry: AgentRunDispatch | None, *, status: str, reason: str, queued: bool = False) -> TaskDispatchResult:
    return TaskDispatchResult(
        task_id=entry.task_id if entry is not None else "",
        task_name=TaskName.RUN_AGENT_WORKFLOW.value, queued=queued,
        status=status.upper(), reason=reason, business_type="agent_run",
        business_id=entry.run_id if entry is not None else None,
    )


def _can_record_dispatched_task(run: Any, task_id: str) -> bool:
    """迟到回执不能覆盖下一代际的预期任务身份。"""
    return run.status in {"pending", "running"} and run.task_id == task_id


def _finish_exhausted(repo: AgentRunRepository, run: AgentRun, entry: AgentRunDispatch) -> None:
    entry.status = "exhausted"
    entry.publish_token = None
    entry.publish_lease_expires_at = None
    transition_run(
        repo, run, "failed", event_type="run_dispatch_exhausted",
        error_message="任务投递未能在限定次数内获得执行器认领，已停止自动重投",
        payload={"dispatch_id": entry.id, "attempts": entry.attempt_count},
    )
    repo.db.add(entry)


def _claim_delivery(run_id: int) -> tuple[dict[str, Any] | None, TaskDispatchResult]:
    with SessionLocal() as db:
        repo = AgentRunRepository(db)
        run = repo.get_run_for_update(run_id=run_id)
        entry = _latest_dispatch(db, run_id)
        if run is None or entry is None:
            db.rollback()
            return None, _delivery_result(entry, status="pending", reason="dispatch_not_requested")
        now = datetime.utcnow()
        if run.status != "pending" or run.task_id != entry.task_id:
            if entry.status in _DELIVERABLE:
                entry.status = "consumed" if run.status == "running" and run.task_id == entry.task_id else "superseded"
                entry.publish_token = None
                entry.publish_lease_expires_at = None
                db.add(entry)
            result = _delivery_result(entry, status=run.status, reason="run_not_pending", queued=run.status == "running")
            db.commit()
            return None, result
        if entry.status not in _DELIVERABLE:
            result = _delivery_result(entry, status=run.status, reason="dispatch_closed")
            db.rollback()
            return None, result
        if entry.next_attempt_at > now:
            result = _delivery_result(entry, status=run.status, reason="dispatch_waiting", queued=entry.status == "published")
            db.rollback()
            return None, result
        if entry.status == "publishing" and entry.publish_lease_expires_at is not None and entry.publish_lease_expires_at > now:
            result = _delivery_result(entry, status=run.status, reason="dispatch_in_progress")
            db.rollback()
            return None, result
        if entry.attempt_count >= int(settings.AGENT_RUN_DISPATCH_MAX_ATTEMPTS):
            _finish_exhausted(repo, run, entry)
            result = _delivery_result(entry, status="failed", reason="dispatch_exhausted")
            db.commit()
            return None, result
        token = uuid4().hex
        entry.status = "publishing"
        entry.publish_token = token
        entry.attempt_count += 1
        entry.publish_lease_expires_at = now + timedelta(seconds=int(settings.AGENT_RUN_DISPATCH_PUBLISH_LEASE_SECONDS))
        entry.next_attempt_at = entry.publish_lease_expires_at
        db.add(entry)
        request = {"run_id": run.id, "dispatch_id": entry.id, "task_id": entry.task_id, "publish_token": token}
        result = _delivery_result(entry, status="pending", reason="dispatch_claimed")
        db.commit()
        return request, result


def _complete_delivery(request: dict[str, Any], *, error: Exception | None) -> TaskDispatchResult:
    with SessionLocal() as db:
        repo = AgentRunRepository(db)
        run = repo.get_run_for_update(run_id=request["run_id"])
        entry = db.query(AgentRunDispatch).filter_by(id=request["dispatch_id"]).populate_existing().with_for_update().one()
        if entry.publish_token != request["publish_token"]:
            result = _delivery_result(entry, status=run.status, reason="stale_dispatch_receipt")
            db.rollback()
            return result
        entry.publish_token = None
        entry.publish_lease_expires_at = None
        now = datetime.utcnow()
        if not _can_record_dispatched_task(run, entry.task_id) or run.status != "pending":
            entry.status = "consumed" if run.status == "running" and run.task_id == entry.task_id else "superseded"
            result = _delivery_result(entry, status=run.status, reason="run_already_observed", queued=run.status == "running")
        elif error is None:
            entry.status = "published"
            entry.published_at = now
            entry.last_error = ""
            entry.next_attempt_at = now + timedelta(seconds=int(settings.AGENT_RUN_DISPATCH_CLAIM_GRACE_SECONDS))
            result = _delivery_result(entry, status="pending", reason="queued", queued=True)
            repo.append_event(run_id=run.id, event_type="run_dispatch_confirmed", payload={
                "dispatch_id": entry.id, "task_id": entry.task_id, "attempt": entry.attempt_count,
            })
        elif entry.attempt_count >= int(settings.AGENT_RUN_DISPATCH_MAX_ATTEMPTS):
            entry.last_error = type(error).__name__
            _finish_exhausted(repo, run, entry)
            result = _delivery_result(entry, status="failed", reason="dispatch_exhausted")
        else:
            entry.status = "pending"
            entry.last_error = type(error).__name__
            delay = min(int(settings.AGENT_RUN_DISPATCH_RETRY_MAX_SECONDS),
                        int(settings.AGENT_RUN_DISPATCH_RETRY_BASE_SECONDS) * 2 ** (entry.attempt_count - 1))
            entry.next_attempt_at = now + timedelta(seconds=delay)
            result = _delivery_result(entry, status="pending", reason="dispatch_deferred")
            repo.append_event(run_id=run.id, event_type="run_dispatch_deferred", payload={
                "dispatch_id": entry.id, "attempt": entry.attempt_count,
                "error_type": type(error).__name__, "retry_at": entry.next_attempt_at.isoformat(),
            })
        db.add(entry)
        db.commit()
        return result


def start_agent_run_worker(run_id: int) -> TaskDispatchResult:
    """只发布已保存且到期的意图；响应丢失也复用同一个 Celery task_id。"""
    request, result = _claim_delivery(run_id)
    if request is None:
        return result
    error = None
    try:
        receipt = submit_background_task(
            BackgroundTaskKind.AGENT_RUN, kwargs={"run_id": run_id},
            business_id=run_id, task_id=request["task_id"],
        )
        if not receipt.queued or receipt.task_id != request["task_id"]:
            raise RuntimeError("后台投递器未保留已持久化的任务身份")
    except Exception as failure:
        error = failure
    return _complete_delivery(request, error=error)


def recover_pending_agent_dispatches(*, limit: int = 20) -> list[int]:
    """仅扫描显式意图，不把无投递意图的 pending 运行自动排队。"""
    with SessionLocal() as db:
        due_ids = db.query(AgentRunDispatch.run_id).join(
            AgentRun, AgentRun.id == AgentRunDispatch.run_id,
        ).filter(
            AgentRun.status == "pending", AgentRun.task_id == AgentRunDispatch.task_id,
            AgentRunDispatch.status.in_(_DELIVERABLE), AgentRunDispatch.next_attempt_at <= datetime.utcnow(),
        ).order_by(AgentRunDispatch.next_attempt_at, AgentRunDispatch.id).limit(max(1, int(limit))).all()
    recovered = []
    for (run_id,) in due_ids:
        result = start_agent_run_worker(int(run_id))
        if result.queued:
            recovered.append(int(run_id))
    return recovered
