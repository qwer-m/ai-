"""Recovery scanner for expired pipeline run leases."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_

from modules.orchestration_components.pipeline_runtime.dispatcher import start_pipeline_worker
from modules.orchestration_components.pipeline_runtime.schemas import STAGE_ORDER

PIPELINE_RUN_RECOVERY_STALE_SECONDS = int(os.getenv("PIPELINE_RUN_LEASE_SECONDS", "3900"))


def SessionLocal():
    from core.db.database import SessionLocal as real_session_local

    return real_session_local()


def ensure_pipeline_table() -> None:
    from modules.orchestration_components.pipeline_runtime.schema_compat import (
        ensure_pipeline_table as real_ensure_pipeline_table,
    )

    real_ensure_pipeline_table()


def _pipeline_run_model():
    from core.db.models import PipelineRun

    return PipelineRun


def _expired_running_filter(pipeline_run_model, now: datetime, stale_before: datetime):
    return or_(
        and_(
            pipeline_run_model.lease_expires_at.is_not(None),
            pipeline_run_model.lease_expires_at < now,
        ),
        and_(
            pipeline_run_model.lease_expires_at.is_(None),
            pipeline_run_model.heartbeat_at.is_not(None),
            pipeline_run_model.heartbeat_at < stale_before,
        ),
        and_(
            pipeline_run_model.lease_expires_at.is_(None),
            pipeline_run_model.heartbeat_at.is_(None),
            pipeline_run_model.started_at.is_not(None),
            pipeline_run_model.started_at < stale_before,
        ),
    )


def _reset_expired_run_to_pending(
    db,
    *,
    pipeline_run_model,
    run_id: int,
    stage: str,
    now: datetime,
    stale_before: datetime,
) -> bool:
    updated = (
        db.query(pipeline_run_model)
        .filter(
            pipeline_run_model.id == run_id,
            pipeline_run_model.status == "running",
            pipeline_run_model.current_stage == stage,
            _expired_running_filter(pipeline_run_model, now, stale_before),
        )
        .update(
            {
                "status": "pending",
                "task_id": None,
                "claim_token": None,
                "heartbeat_at": None,
                "lease_expires_at": None,
                "finished_at": None,
                "error_message": "requeued_after_expired_pipeline_lease",
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return updated == 1


def recover_expired_pipeline_runs(*, limit: int = 20) -> dict[str, Any]:
    """Requeue expired running pipeline runs through the governed dispatcher."""

    ensure_pipeline_table()
    safe_limit = max(1, int(limit or 1))
    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=max(60, int(PIPELINE_RUN_RECOVERY_STALE_SECONDS or 0)))
    PipelineRun = _pipeline_run_model()
    db = SessionLocal()
    report: dict[str, Any] = {
        "checked": 0,
        "requeued": 0,
        "skipped": 0,
        "failed": 0,
        "run_ids": [],
        "failures": [],
    }
    try:
        rows = (
            db.query(PipelineRun.id, PipelineRun.current_stage)
            .filter(
                PipelineRun.status == "running",
                PipelineRun.current_stage.is_not(None),
                _expired_running_filter(PipelineRun, now, stale_before),
            )
            .order_by(PipelineRun.lease_expires_at.asc(), PipelineRun.id.asc())
            .limit(safe_limit)
            .all()
        )
        report["checked"] = len(rows)
        for run_id, current_stage in rows:
            stage = str(current_stage or "")
            if stage not in STAGE_ORDER:
                report["skipped"] += 1
                continue
            if not _reset_expired_run_to_pending(
                db,
                pipeline_run_model=PipelineRun,
                run_id=int(run_id),
                stage=stage,
                now=now,
                stale_before=stale_before,
            ):
                report["skipped"] += 1
                continue
            try:
                start_pipeline_worker(int(run_id), stage)
            except Exception as exc:
                report["failed"] += 1
                report["failures"].append(
                    {
                        "run_id": int(run_id),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            report["requeued"] += 1
            report["run_ids"].append(int(run_id))
        return report
    finally:
        db.close()
