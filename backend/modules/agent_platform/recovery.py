from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from .dispatcher import start_agent_run_worker
from .repository import AgentPlatformRepository


def recover_expired_agent_runs(*, limit: int = 20) -> dict[str, Any]:
    db = SessionLocal()
    repo = AgentPlatformRepository(db)
    recovered: list[int] = []
    try:
        now = datetime.utcnow()
        stale_before = now - timedelta(seconds=3900)
        rows = (
            db.query(AgentRun)
            .filter(
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
            .all()
        )
        for run in rows:
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
        return {"status": "completed", "recovered_run_ids": recovered}
    finally:
        db.close()
