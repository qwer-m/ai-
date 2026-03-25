from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.db.database import SessionLocal, engine
from core.db.models import PipelineRun, Project
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from .pipeline_routes.agent_ops import (
    _aggregate_reviewer_decision,
    _run_stage_executor_agent,
    _run_stage_planner_agent,
    _run_stage_reviewer_agent,
    _save_agent_learning_snapshot,
    _upsert_agent_artifact,
)
from .pipeline_routes.schemas import RunStatus, STAGE_ORDER, StageKey
from .pipeline_routes.stage_ops import _execute_stage_once
from .pipeline_routes.support import _default_stage_states, _mark_stage, _persist_run

STAGE_WORKFLOW_KIND: dict[StageKey, WorkflowKind] = {
    "test_generation": WorkflowKind.TEST_GENERATION,
    "ui_automation": WorkflowKind.UI_AUTOMATION,
    "api_automation": WorkflowKind.API_AUTOMATION,
    "evaluation": WorkflowKind.EVALUATION,
}

STAGE_WORKFLOW_STAGE: dict[StageKey, WorkflowStage] = {
    "test_generation": WorkflowStage.GENERATE,
    "ui_automation": WorkflowStage.EXECUTE,
    "api_automation": WorkflowStage.EXECUTE,
    "evaluation": WorkflowStage.EVALUATE,
}

_worker_lock = threading.Lock()
_worker_threads: dict[int, threading.Thread] = {}


def _ensure_pipeline_table() -> None:
    """Create the pipeline table on startup without failing the app."""
    try:
        PipelineRun.__table__.create(bind=engine, checkfirst=True)
    except Exception:
        pass


_ensure_pipeline_table()


def _get_owned_project(project_id: int, db: Session, user_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _log_stage_trace(
    db: Session,
    project_id: int,
    user_id: int,
    run_id: int,
    stage: StageKey,
    action: str,
    **extra: Any,
) -> None:
    details = {"action": action, "stage": stage, "run_id": run_id, **extra}
    log_workflow_trace(
        db,
        project_id,
        user_id,
        STAGE_WORKFLOW_KIND[stage],
        STAGE_WORKFLOW_STAGE[stage],
        details,
    )


def _start_worker(run_id: int, start_stage: StageKey) -> None:
    with _worker_lock:
        worker = _worker_threads.get(run_id)
        if worker and worker.is_alive():
            return
        thread = threading.Thread(
            target=_run_pipeline_worker,
            args=(run_id, start_stage),
            daemon=True,
            name=f"pipeline-run-{run_id}",
        )
        _worker_threads[run_id] = thread
        thread.start()


def _run_pipeline_worker(run_id: int, start_stage: StageKey) -> None:
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            return

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
        )

        start_index = STAGE_ORDER.index(start_stage)
        any_stage_failed = False

        for stage in STAGE_ORDER[start_index:]:
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            if not run:
                return
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
                        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
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

        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run:
            final_status: RunStatus = "failed" if any_stage_failed else "success"
            _persist_run(
                db,
                run,
                status=final_status,
                current_stage=None,
                error_message=run.error_message if final_status == "failed" else "",
                finished_at=datetime.utcnow(),
            )
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
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
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run:
            _persist_run(
                db,
                run,
                status="failed",
                current_stage=run.current_stage,
                error_message=f"{type(worker_error).__name__}: {worker_error}",
                finished_at=datetime.utcnow(),
            )
    finally:
        db.close()
        with _worker_lock:
            _worker_threads.pop(run_id, None)
