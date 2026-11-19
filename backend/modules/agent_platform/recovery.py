from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentRunDispatch
from core.settings.config import settings
from .dispatcher import start_agent_run_worker, prepare_agent_run_dispatch, recover_pending_agent_dispatches
from .run_repository import AgentRunRepository
from .lifecycle import transition_run


def recover_expired_agent_runs(*, limit: int = 20) -> dict[str, Any]:
    db = SessionLocal()
    repo = AgentRunRepository(db)
    recovered: list[int] = []
    expired: list[int] = []
    try:
        now = datetime.utcnow()
        stale_before = now - timedelta(seconds=int(settings.AGENT_RUN_LEASE_SECONDS))
        expired_run_ids = db.scalars(
            select(AgentRun.id)
            .where(
                AgentRun.status == "running",
                # 部署不回填旧运行的调度授权，也不自动启动无显式意图的 QA 任务。
                select(AgentRunDispatch.id).where(
                    AgentRunDispatch.run_id == AgentRun.id,
                    AgentRunDispatch.task_id == AgentRun.task_id,
                ).exists(),
                (
                    (AgentRun.lease_expires_at.is_not(None) & (AgentRun.lease_expires_at < now))
                    | (
                        AgentRun.lease_expires_at.is_(None)
                        & AgentRun.heartbeat_at.is_not(None)
                        & (AgentRun.heartbeat_at < stale_before)
                    )
                ),
            )
            .order_by(AgentRun.lease_expires_at.asc(), AgentRun.id.asc())
            .limit(max(1, int(limit)))
        ).all()
        for run_id in expired_run_ids:
            run = repo.get_run_for_update(run_id=int(run_id))
            if run is None or run.status != "running":
                db.rollback()
                continue
            # 选出候选后执行器可能已续租，必须在行锁内再次确认。
            if run.lease_expires_at is not None and run.lease_expires_at >= now:
                db.rollback()
                continue
            if run.lease_expires_at is None and (run.heartbeat_at is None or run.heartbeat_at >= stale_before):
                db.rollback()
                continue
            run_context = dict(run.run_context or {})
            if run_context.get("execution_policy", {}).get("stop_on_failure"):
                # 首次失败即停的运行不得被租约恢复隐式执行第二次。
                transition_run(repo, run, "failed", now=now,
                    error_message="执行租约已失效，按本次运行策略停止并保留诊断，不自动续跑",
                    event_type="run_recovery_stopped", payload={"reason": "stop_on_failure"})
                db.commit()
                expired.append(run.id)
                continue
            raw_deadline = str(run_context.get("deadline_at") or "").strip()
            deadline = datetime.fromisoformat(raw_deadline) if raw_deadline else None
            if deadline is not None and deadline <= now:
                run.task_id = None
                transition_run(
                    repo, run, "failed", now=now,
                    error_message="Agent Run 在执行器失联期间已超过总执行时限",
                    event_type="run_recovery_deadline_expired",
                    payload={"reason": "deadline_expired"},
                )
                db.add(run)
                db.commit()
                expired.append(run.id)
                continue
            # 接管只结束失权执行器的未完成尝试，保留检查点与已消耗预算供新尝试继续。
            repo.finalize_unfinished_node_runs(
                run_id=run.id,
                status="failed",
                finished_at=now,
                error_message="执行租约已过期，本次节点尝试中断并交由恢复器接管",
            )
            transition_run(
                repo, run, "pending", now=now,
                event_type="run_recovered",
                payload={"reason": "expired_lease"},
            )
            db.add(run)
            prepare_agent_run_dispatch(db, run, reason="expired_lease", new_cycle=True)
            db.commit()
            start_agent_run_worker(run.id)
            recovered.append(run.id)
        pending_recovered = recover_pending_agent_dispatches(limit=limit)
        return {
            "status": "completed",
            "recovered_run_ids": recovered,
            "expired_run_ids": expired,
            "pending_dispatched_run_ids": pending_recovered,
        }
    finally:
        db.close()
