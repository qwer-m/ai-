"""Durable worker runner for orchestration pipeline runs."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from modules.orchestration_components.pipeline_runtime.schemas import (
    RunStatus,
    STAGE_ORDER,
    StageKey,
)
from modules.orchestration_components.pipeline_runtime.stage_ops import _execute_stage_once

STAGE_WORKFLOW_KIND_NAME: dict[StageKey, str] = {
    "test_generation": "TEST_GENERATION",
    "ui_automation": "UI_AUTOMATION",
    "api_automation": "API_AUTOMATION",
    "evaluation": "EVALUATION",
}

STAGE_WORKFLOW_STAGE_NAME: dict[StageKey, str] = {
    "test_generation": "GENERATE",
    "ui_automation": "EXECUTE",
    "api_automation": "EXECUTE",
    "evaluation": "EVALUATE",
}

PIPELINE_RUN_LEASE_SECONDS = int(os.getenv("PIPELINE_RUN_LEASE_SECONDS", "3900"))


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


def _pipeline_run_model():
    from core.db.models import PipelineRun

    return PipelineRun


def _default_stage_states():
    from modules.orchestration_components.pipeline_runtime.support import (
        _default_stage_states as real_default_stage_states,
    )

    return real_default_stage_states()


def _mark_stage(*args, **kwargs):
    from modules.orchestration_components.pipeline_runtime.support import _mark_stage as real_mark_stage

    return real_mark_stage(*args, **kwargs)


def _persist_run(*args, **kwargs):
    from modules.orchestration_components.pipeline_runtime.support import _persist_run as real_persist_run

    return real_persist_run(*args, **kwargs)


def _aggregate_reviewer_decision(*args, **kwargs):
    from modules.orchestration_components.pipeline_runtime.agent_ops import (
        _aggregate_reviewer_decision as real_aggregate_reviewer_decision,
    )

    return real_aggregate_reviewer_decision(*args, **kwargs)


def _run_stage_executor_agent(*args, **kwargs):
    from modules.orchestration_components.pipeline_runtime.agent_ops import (
        _run_stage_executor_agent as real_run_stage_executor_agent,
    )

    return real_run_stage_executor_agent(*args, **kwargs)


def _run_stage_planner_agent(*args, **kwargs):
    from modules.orchestration_components.pipeline_runtime.agent_ops import (
        _run_stage_planner_agent as real_run_stage_planner_agent,
    )

    return real_run_stage_planner_agent(*args, **kwargs)


def _run_stage_reviewer_agent(*args, **kwargs):
    from modules.orchestration_components.pipeline_runtime.agent_ops import (
        _run_stage_reviewer_agent as real_run_stage_reviewer_agent,
    )

    return real_run_stage_reviewer_agent(*args, **kwargs)


def _save_agent_learning_snapshot(*args, **kwargs):
    from modules.orchestration_components.pipeline_runtime.agent_ops import (
        _save_agent_learning_snapshot as real_save_agent_learning_snapshot,
    )

    return real_save_agent_learning_snapshot(*args, **kwargs)


def _upsert_agent_artifact(*args, **kwargs):
    from modules.orchestration_components.pipeline_runtime.agent_ops import (
        _upsert_agent_artifact as real_upsert_agent_artifact,
    )

    return real_upsert_agent_artifact(*args, **kwargs)


def _log_stage_trace(
    db: Session,
    project_id: int,
    user_id: int,
    run_id: int,
    stage: StageKey,
    action: str,
    **extra: Any,
) -> None:
    from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace

    details = {"action": action, "stage": stage, "run_id": run_id, **extra}
    log_workflow_trace(
        db,
        project_id,
        user_id,
        getattr(WorkflowKind, STAGE_WORKFLOW_KIND_NAME[stage]),
        getattr(WorkflowStage, STAGE_WORKFLOW_STAGE_NAME[stage]),
        details,
    )


def claim_pending_run(
    db: Session,
    *,
    run_id: int,
    start_stage: StageKey,
    task_id: str | None = None,
    lease_seconds: int = PIPELINE_RUN_LEASE_SECONDS,
) -> Any | None:
    PipelineRun = _pipeline_run_model()
    now = datetime.utcnow()
    lease_expires_at = now + timedelta(seconds=max(60, int(lease_seconds or 0)))
    claim_token = task_id or f"pipeline-run-{run_id}-{int(now.timestamp())}"
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        return None
    stage_matches = str(run.current_stage or "") == str(start_stage)
    stale_running = (
        run.status == "running"
        and stage_matches
        and run.lease_expires_at is not None
        and run.lease_expires_at < now
    )
    same_task_redelivery = (
        run.status == "running"
        and stage_matches
        and bool(task_id)
        and str(run.task_id or "") == str(task_id)
    )
    if run.status != "pending" and not stale_running and not same_task_redelivery:
        return None
    if not stage_matches:
        return None

    claimed = (
        db.query(PipelineRun)
        .filter(
            PipelineRun.id == run_id,
            PipelineRun.current_stage == start_stage,
            or_(
                PipelineRun.status == "pending",
                and_(
                    PipelineRun.status == "running",
                    PipelineRun.lease_expires_at.is_not(None),
                    PipelineRun.lease_expires_at < now,
                ),
                and_(
                    PipelineRun.status == "running",
                    PipelineRun.task_id == task_id,
                ),
            ),
        )
        .update(
            {
                "status": "running",
                "started_at": run.started_at or now,
                "finished_at": None,
                "task_id": task_id or run.task_id,
                "claim_token": claim_token,
                "heartbeat_at": now,
                "lease_expires_at": lease_expires_at,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if claimed != 1:
        return None
    return db.query(PipelineRun).filter(PipelineRun.id == run_id).first()


def _extend_run_lease(
    db: Session,
    run: Any,
    *,
    claim_token: str | None,
    lease_seconds: int = PIPELINE_RUN_LEASE_SECONDS,
) -> None:
    now = datetime.utcnow()
    _persist_run(
        db,
        run,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=max(60, int(lease_seconds or 0))),
        claim_token=claim_token or run.claim_token,
    )


def run_pipeline_worker(run_id: int, start_stage: StageKey, task_id: str | None = None) -> None:
    ensure_pipeline_table()
    db = SessionLocal()
    repo = PipelineRuntimeRepository(db)
    try:
        run = claim_pending_run(db, run_id=run_id, start_stage=start_stage, task_id=task_id)
        if not run:
            return
        claim_token = str(run.claim_token or task_id or "")

        payload = dict(run.request_payload or {})
        stage_states = dict(run.stage_states or _default_stage_states())
        artifacts = dict(run.artifacts or {})
        user_id = run.user_id
        project_id = run.project_id
        agent_cfg = dict(payload.get("agent") or {})
        agent_enabled = bool(agent_cfg.get("enabled", True))
        auto_retry_enabled = agent_enabled and bool(agent_cfg.get("auto_retry_enabled", True))
        max_auto_retries = int(agent_cfg.get("max_auto_retries") or 1) if auto_retry_enabled else 0

        _persist_run(
            db,
            run,
            status="running",
            current_stage=start_stage,
            stage_states=stage_states,
            artifacts=artifacts,
            error_message="",
            started_at=run.started_at or datetime.utcnow(),
            finished_at=None,
            task_id=task_id or run.task_id,
            claim_token=claim_token,
            heartbeat_at=datetime.utcnow(),
            lease_expires_at=datetime.utcnow() + timedelta(seconds=PIPELINE_RUN_LEASE_SECONDS),
        )

        start_index = STAGE_ORDER.index(start_stage)
        any_stage_failed = False

        for stage in STAGE_ORDER[start_index:]:
            run = repo.get_run(run_id=run_id)
            if not run:
                return
            _extend_run_lease(db, run, claim_token=claim_token)
            stage_states = dict(run.stage_states or _default_stage_states())
            artifacts = dict(run.artifacts or {})

            _mark_stage(stage_states, stage, "running", "Stage started.")
            _persist_run(db, run, status="running", current_stage=stage, stage_states=stage_states, artifacts=artifacts)
            _log_stage_trace(db, project_id, user_id, run_id, stage, "pipeline_stage_start")

            if agent_enabled:
                planner_result = _run_stage_planner_agent(db, user_id, stage, payload, artifacts, agent_cfg)
                artifacts = _upsert_agent_artifact(artifacts, stage, "planner", planner_result)
                _log_stage_trace(
                    db,
                    project_id,
                    user_id,
                    run_id,
                    stage,
                    "agent_planner_ready",
                    llm_status=str(planner_result.get("llm_status") or ""),
                )

                executor_result = _run_stage_executor_agent(stage, payload, artifacts, agent_cfg)
                artifacts = _upsert_agent_artifact(artifacts, stage, "executor", executor_result)
                _log_stage_trace(
                    db,
                    project_id,
                    user_id,
                    run_id,
                    stage,
                    "agent_executor_ready",
                    warnings=int(executor_result.get("warnings") or 0),
                    workers=int(executor_result.get("workers") or 1),
                )
                _persist_run(db, run, status="running", current_stage=stage, stage_states=stage_states, artifacts=artifacts)

            attempt_index = 0
            while True:
                attempt_index += 1
                stage_result = _execute_stage_once(stage, payload, artifacts, db, project_id, user_id)
                stage_status = str(stage_result.get("status") or "failed")
                stage_message = str(stage_result.get("message") or "Unknown stage error.")
                stage_meta = dict(stage_result.get("meta") or {})
                artifacts = dict(stage_result.get("artifacts") or artifacts)

                if stage_status == "success":
                    _mark_stage(stage_states, stage, "success", stage_message)
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "pipeline_stage_success",
                        attempt=attempt_index,
                        **stage_meta,
                    )
                elif stage_status == "skipped":
                    _mark_stage(stage_states, stage, "skipped", stage_message)
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "pipeline_stage_skipped",
                        attempt=attempt_index,
                        reason=stage_message,
                    )
                else:
                    _mark_stage(stage_states, stage, "failed", stage_message)
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "pipeline_stage_failed",
                        attempt=attempt_index,
                        reason=stage_message,
                        **stage_meta,
                    )

                decision = {"should_retry": False, "reason": "agent_disabled"}
                if agent_enabled:
                    reviewer_result = _run_stage_reviewer_agent(
                        db,
                        user_id,
                        stage,
                        payload,
                        artifacts,
                        stage_status,
                        stage_message,
                        agent_cfg,
                    )
                    decision = _aggregate_reviewer_decision(
                        stage,
                        stage_status,
                        stage_message,
                        stage_meta,
                        reviewer_result,
                        attempt_index=attempt_index,
                        max_auto_retries=max_auto_retries,
                        auto_retry_enabled=auto_retry_enabled,
                        retry_policy=str(agent_cfg.get("retry_policy") or "balanced"),
                    )
                    reviewer_result["decision"] = decision
                    artifacts = _upsert_agent_artifact(artifacts, stage, "reviewer", reviewer_result)
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "agent_reviewer_ready",
                        verdict=str(reviewer_result.get("verdict") or ""),
                        llm_status=str(reviewer_result.get("llm_status") or ""),
                        should_retry=bool(decision.get("should_retry")),
                        decision_reason=str(decision.get("reason") or ""),
                        retryability=str(decision.get("retryability") or ""),
                        retry_policy=str(decision.get("retry_policy") or ""),
                    )

                if stage_status == "failed" and bool(decision.get("should_retry")):
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "pipeline_stage_auto_retry",
                        attempt=attempt_index,
                        reason=str(decision.get("reason") or ""),
                    )
                    _mark_stage(
                        stage_states,
                        stage,
                        "running",
                        f"Auto retry {attempt_index}/{max_auto_retries} triggered: {decision.get('reason')}",
                    )
                    _persist_run(
                        db,
                        run,
                        status="running",
                        current_stage=stage,
                        stage_states=stage_states,
                        artifacts=artifacts,
                    )
                    continue

                if stage_status == "failed":
                    any_stage_failed = True
                    _persist_run(
                        db,
                        run,
                        stage_states=stage_states,
                        artifacts=artifacts,
                        error_message=stage_message,
                    )
                    if stage == "test_generation":
                        run = repo.get_run(run_id=run_id)
                        if run:
                            _persist_run(
                                db,
                                run,
                                status="failed",
                                current_stage=stage,
                                stage_states=stage_states,
                                artifacts=artifacts,
                                error_message=stage_message,
                                finished_at=datetime.utcnow(),
                                claim_token=None,
                                heartbeat_at=datetime.utcnow(),
                                lease_expires_at=None,
                            )
                        return
                else:
                    _persist_run(
                        db,
                        run,
                        stage_states=stage_states,
                        artifacts=artifacts,
                        error_message="",
                    )
                break

        run = repo.get_run(run_id=run_id)
        if run:
            final_status: RunStatus = "failed" if any_stage_failed else "success"
            _persist_run(
                db,
                run,
                status=final_status,
                current_stage=None,
                error_message=run.error_message if final_status == "failed" else "",
                finished_at=datetime.utcnow(),
                claim_token=None,
                heartbeat_at=datetime.utcnow(),
                lease_expires_at=None,
            )
            run = repo.get_run(run_id=run_id)
            if run and agent_enabled:
                ok, status = _save_agent_learning_snapshot(db, run, dict(run.artifacts or {}))
                _log_stage_trace(
                    db,
                    project_id,
                    user_id,
                    run_id,
                    "evaluation",
                    "agent_learning_saved" if ok else "agent_learning_failed",
                    detail=status,
                )
    except Exception as worker_error:
        run = repo.get_run(run_id=run_id)
        if run:
            _persist_run(
                db,
                run,
                status="failed",
                current_stage=run.current_stage,
                error_message=f"{type(worker_error).__name__}: {worker_error}",
                finished_at=datetime.utcnow(),
                claim_token=None,
                heartbeat_at=datetime.utcnow(),
                lease_expires_at=None,
            )
    finally:
        db.close()


_claim_pending_run = claim_pending_run
_run_pipeline_worker = run_pipeline_worker
