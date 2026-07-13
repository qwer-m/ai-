from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from modules.orchestration_components.pipeline_runtime.schemas import (
    PipelineRetryRequest,
    PipelineRunRequest,
    STAGE_ORDER,
    StageKey,
)
from modules.orchestration_components.pipeline_runtime.support import _serialize_run
from modules.orchestration_components.services.pipeline_run_service import PipelineRunService
from routers.orchestration.pipeline_runtime import _start_worker

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])


@router.post("/runs")
def create_pipeline_run(
    req: PipelineRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = PipelineRunService(db, _start_worker).create_run(
        payload=req.model_dump(),
        project_id=req.project_id,
        user_id=current_user.id,
    )
    if not run:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"run": _serialize_run(run)}


@router.get("/runs")
def list_pipeline_runs(
    project_id: int = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = PipelineRunService(db, _start_worker).list_runs(
        project_id=project_id,
        user_id=current_user.id,
        limit=limit,
    )
    if rows is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"items": [_serialize_run(row) for row in rows]}


@router.get("/runs/{run_id}")
def get_pipeline_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = PipelineRunService(db, _start_worker).get_run(run_id=run_id, user_id=current_user.id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": _serialize_run(run)}


@router.post("/runs/{run_id}/resume")
def resume_pipeline_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run, status = PipelineRunService(db, _start_worker).resume_run(
        run_id=run_id,
        user_id=current_user.id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if status == "already_running":
        raise HTTPException(status_code=409, detail="Run is already running")
    if status == "no_resumable_stage":
        return {"run": _serialize_run(run), "message": "No resumable stage found."}

    resume_stage = str(run.current_stage or "")
    return {"run": _serialize_run(run), "message": f"Resumed from stage {resume_stage}."}


@router.post("/runs/{run_id}/retry")
def retry_pipeline_run(
    run_id: int,
    req: PipelineRetryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    start_stage: StageKey = req.from_stage or "test_generation"
    if start_stage not in STAGE_ORDER:
        raise HTTPException(status_code=400, detail="Invalid retry stage")

    run, status = PipelineRunService(db, _start_worker).retry_run(
        run_id=run_id,
        user_id=current_user.id,
        start_stage=start_stage,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    stage = status.split(":", 1)[-1] if ":" in status else str(start_stage)
    return {"run": _serialize_run(run), "message": f"Retry started from stage {stage}."}


@router.get("/runs/{run_id}/traces")
def get_pipeline_run_traces(
    run_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = PipelineRunService(db, _start_worker).list_run_traces(
        run_id=run_id,
        user_id=current_user.id,
        limit=limit,
    )
    if items is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"items": items}
