from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import LogEntry, PipelineRun, User
from .pipeline_runtime import _get_owned_project, _start_worker
from routers.pipeline_routes.schemas import PipelineRetryRequest, PipelineRunRequest, STAGE_ORDER, StageKey
from routers.pipeline_routes.support import (
    _default_stage_states,
    _find_resume_stage,
    _now_iso,
    _parse_workflow_trace,
    _serialize_run,
)

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])


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

