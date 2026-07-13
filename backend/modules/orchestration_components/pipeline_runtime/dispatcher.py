"""Dispatch helpers for persisted pipeline runs."""

from __future__ import annotations

from datetime import datetime

from modules.orchestration.background_task_governance import (
    BackgroundTaskKind,
    submit_background_task,
)
from modules.orchestration_components.pipeline_runtime.schemas import StageKey
from modules.orchestration.task_dispatcher import TaskDispatchResult


def SessionLocal():
    from core.db.database import SessionLocal as real_session_local

    return real_session_local()


def PipelineRuntimeRepository(*args, **kwargs):
    from modules.orchestration_components.repositories.pipeline_runtime_repository import (
        PipelineRuntimeRepository as real_repository,
    )

    return real_repository(*args, **kwargs)


def ensure_pipeline_table() -> None:
    from modules.orchestration_components.pipeline_runtime.schema_compat import (
        ensure_pipeline_table as real_ensure_pipeline_table,
    )

    real_ensure_pipeline_table()


def _persist_run(*args, **kwargs):
    from modules.orchestration_components.pipeline_runtime.support import _persist_run as real_persist_run

    return real_persist_run(*args, **kwargs)


def start_pipeline_worker(run_id: int, start_stage: StageKey) -> TaskDispatchResult:
    """Queue a durable pipeline run and mark the run failed if enqueueing fails."""

    ensure_pipeline_table()
    try:
        queue_result = submit_background_task(
            BackgroundTaskKind.PIPELINE_RUN,
            kwargs={"run_id": run_id, "start_stage": start_stage},
            business_id=run_id,
        )
        db = SessionLocal()
        repo = PipelineRuntimeRepository(db)
        try:
            run = repo.get_run(run_id=run_id)
            if run:
                _persist_run(
                    db,
                    run,
                    task_id=queue_result.id,
                    claim_token=None,
                    heartbeat_at=None,
                    lease_expires_at=None,
                )
        finally:
            db.close()
        return queue_result
    except Exception as exc:
        db = SessionLocal()
        repo = PipelineRuntimeRepository(db)
        try:
            run = repo.get_run(run_id=run_id)
            if run:
                _persist_run(
                    db,
                    run,
                    status="failed",
                    current_stage=start_stage,
                    error_message=f"failed_to_queue_pipeline_run:{exc}",
                    finished_at=datetime.utcnow(),
                    claim_token=None,
                    heartbeat_at=None,
                    lease_expires_at=None,
                )
        finally:
            db.close()
        raise
