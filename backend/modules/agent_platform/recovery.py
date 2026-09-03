from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from core.settings.config import settings
from .dispatcher import start_agent_run_worker
from .repository import AgentPlatformRepository
from .retention import prune_terminal_run_history


def recover_expired_agent_runs(*, limit: int = 20) -> dict[str, Any]:
    db = SessionLocal()
    repo = AgentPlatformRepository(db)
    recovered: list[int] = []
    expired: list[int] = []
    try:
        now = datetime.utcnow()
        stale_before = now - timedelta(seconds=int(settings.AGENT_RUN_LEASE_SECONDS))
        expired_run_ids = db.scalars(
            select(AgentRun.id)
            .where(
                AgentRun.status == "running",
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
            run = db.get(AgentRun, int(run_id))
            if run is None or run.status != "running":
                continue
            run_context = dict(run.run_context or {})
            raw_deadline = str(run_context.get("deadline_at") or "").strip()
            deadline = datetime.fromisoformat(raw_deadline) if raw_deadline else None
            if deadline is not None and deadline <= now:
                run.status = "failed"
                run.task_id = None
                run.claim_token = None
                run.heartbeat_at = None
                run.lease_expires_at = None
                run.finished_at = now
                run.error_message = "Agent Run 在执行器失联期间已超过总执行时限"
                repo.append_event(
                    run_id=run.id,
                    event_type="run_recovery_deadline_expired",
                    payload={"reason": "deadline_expired"},
                )
                db.add(run)
                db.commit()
                prune_terminal_run_history(repo, run)
                expired.append(run.id)
                continue
            run.status = "pending"
            run.task_id = None
            run.claim_token = None
            run.heartbeat_at = None
            run.lease_expires_at = None
            run.error_message = ""
            repo.append_event(
                run_id=run.id,
                event_type="run_recovered",
                payload={"reason": "expired_lease"},
            )
            db.add(run)
            db.commit()
            start_agent_run_worker(run.id)
            recovered.append(run.id)
        return {
            "status": "completed",
            "recovered_run_ids": recovered,
            "expired_run_ids": expired,
        }
    finally:
        db.close()
