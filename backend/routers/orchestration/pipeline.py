from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import SessionLocal, engine, get_db
from core.db.models import LogEntry, PipelineRun, Project, User
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from .pipeline_routes.agent_ops import (
    _aggregate_reviewer_decision,
    _run_stage_executor_agent,
    _run_stage_planner_agent,
    _run_stage_reviewer_agent,
    _save_agent_learning_snapshot,
    _upsert_agent_artifact,
)
from .pipeline_routes.schemas import (
    PipelineRetryRequest,
    PipelineRunRequest,
    RunStatus,
    STAGE_ORDER,
    StageKey,
)
from .pipeline_routes.stage_ops import _execute_stage_once
from .pipeline_routes.support import (
    _default_stage_states,
    _find_resume_stage,
    _mark_stage,
    _now_iso,
    _parse_workflow_trace,
    _persist_run,
    _serialize_run,
)

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])

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
    """启动时兜底创建表；失败不阻断应用，写入时再显式报错。"""
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


@router.post("/runs")
def create_pipeline_run(
    req: PipelineRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payload = req.model_dump()
    project_id = req.project_id
    _get_owned_project(project_id, db, current_user.id)

    stage_states = _default_stage_states()
    run = PipelineRun(
        user_id=current_user.id,
        project_id=project_id,
        status="pending",
        current_stage="test_generation",
        request_payload=payload,
        stage_states=stage_states,
        artifacts={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    _start_worker(run.id, "test_generation")
    return {"run": _serialize_run(run)}


@router.get("/runs")
def list_pipeline_runs(
    project_id: int = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)
    rows = (
        db.query(PipelineRun)
        .filter(PipelineRun.project_id == project_id, PipelineRun.user_id == current_user.id)
        .order_by(desc(PipelineRun.created_at), desc(PipelineRun.id))
        .limit(limit)
        .all()
    )
    return {"items": [_serialize_run(row) for row in rows]}


@router.get("/runs/{run_id}")
def get_pipeline_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == current_user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": _serialize_run(run)}


@router.post("/runs/{run_id}/resume")
def resume_pipeline_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == current_user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Run is already running")

    stage_states = dict(run.stage_states or _default_stage_states())
    resume_stage = _find_resume_stage(stage_states)
    if not resume_stage:
        return {"run": _serialize_run(run), "message": "No resumable stage found."}

    run.status = "pending"
    run.current_stage = resume_stage
    run.error_message = ""
    run.finished_at = None
    db.add(run)
    db.commit()
    db.refresh(run)

    _start_worker(run.id, resume_stage)
    return {"run": _serialize_run(run), "message": f"Resumed from stage {resume_stage}."}


@router.post("/runs/{run_id}/retry")
def retry_pipeline_run(
    run_id: int,
    req: PipelineRetryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    base_run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == current_user.id).first()
    if not base_run:
        raise HTTPException(status_code=404, detail="Run not found")

    start_stage: StageKey = req.from_stage or "test_generation"
    start_index = STAGE_ORDER.index(start_stage)

    new_stage_states = _default_stage_states()
    new_artifacts: dict[str, Any] = {}
    if start_index > 0:
        old_states = dict(base_run.stage_states or {})
        old_artifacts = dict(base_run.artifacts or {})
        for stage in STAGE_ORDER[:start_index]:
            prev = dict(old_states.get(stage) or {})
            prev["status"] = "success"
            prev["message"] = f"Reused from run #{base_run.id}"
            prev["started_at"] = prev.get("started_at") or _now_iso()
            prev["ended_at"] = prev.get("ended_at") or _now_iso()
            new_stage_states[stage] = prev
            if stage in old_artifacts:
                new_artifacts[stage] = old_artifacts[stage]

    new_run = PipelineRun(
        user_id=current_user.id,
        project_id=base_run.project_id,
        status="pending",
        current_stage=start_stage,
        request_payload=base_run.request_payload or {},
        stage_states=new_stage_states,
        artifacts=new_artifacts,
        retry_of_run_id=base_run.id,
    )
    db.add(new_run)
    db.commit()
    db.refresh(new_run)

    _start_worker(new_run.id, start_stage)
    return {"run": _serialize_run(new_run), "message": f"Retry started from stage {start_stage}."}


@router.get("/runs/{run_id}/traces")
def get_pipeline_run_traces(
    run_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == current_user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    rows = (
        db.query(LogEntry)
        .filter(
            LogEntry.project_id == run.project_id,
            or_(LogEntry.user_id == current_user.id, LogEntry.user_id.is_(None)),
            LogEntry.message.like("WORKFLOW_TRACE:%"),
        )
        .order_by(desc(LogEntry.created_at), desc(LogEntry.id))
        .limit(limit)
        .all()
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        payload = _parse_workflow_trace(row.message or "")
        if not payload:
            continue

        details = payload.get("details") or {}
        if int(details.get("run_id") or 0) != run_id:
            continue

        items.append(
            {
                "id": row.id,
                "created_at": row.created_at,
                "kind": str(payload.get("kind") or ""),
                "stage": str(payload.get("stage") or ""),
                "action": str(details.get("action") or ""),
                "details": details,
            }
        )

    items.sort(key=lambda item: (item.get("created_at"), item.get("id")))
    return {"items": items}
