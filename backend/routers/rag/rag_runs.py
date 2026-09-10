from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import User
from modules.rag_eval.services.rag_run_route_service import RagRunRouteService
from schemas.rag.rag_eval import (
    RagEvalRunCreate,
    RagEvalRunOut,
    RagEvalRunStatusResponse,
    RagEvalSamplesPage,
)

router = APIRouter(tags=["RAG Eval Runs"])


@router.post("/rag/eval/run")
def create_rag_eval_run(
    payload: RagEvalRunCreate,
    project_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, result = RagRunRouteService(db).create_run(
        user_id=current_user.id,
        project_id=project_id,
        dataset_id=payload.dataset_id,
        config=payload.config,
        run_name=payload.run_name,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status.startswith("error:"):
        raise HTTPException(status_code=400, detail=status.split(":", 1)[1])
    return result


@router.post("/rag/eval/run/{run_id}/stop", response_model=RagEvalRunOut)
def stop_rag_eval_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    status, result = RagRunRouteService(db).stop_run(run_id=run_id, user_id=current_user.id)
    if status.startswith("error:"):
        raise HTTPException(status_code=404, detail=status.split(":", 1)[1])
    return result


@router.post("/rag/eval/run/{run_id}/resume", response_model=RagEvalRunOut)
def resume_rag_eval_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    status, result = RagRunRouteService(db).resume_run(run_id=run_id, user_id=current_user.id)
    if status.startswith("error:"):
        raise HTTPException(status_code=400, detail=status.split(":", 1)[1])
    return result


@router.get("/rag/eval/run/compare")
def compare_rag_eval_runs(
    run_a: int = Query(..., ge=1),
    run_b: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, result = RagRunRouteService(db).compare_runs(run_a=run_a, run_b=run_b, user_id=current_user.id)
    if status == "same_run":
        raise HTTPException(status_code=400, detail="run_a and run_b must be different")
    if status.startswith("error:"):
        raise HTTPException(status_code=404, detail=status.split(":", 1)[1])
    return result


@router.get("/rag/eval/run/{run_id}", response_model=RagEvalRunStatusResponse)
def get_rag_eval_run_status(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    status, result = RagRunRouteService(db).get_run_status(run_id=run_id, user_id=current_user.id)
    if status == "run_not_found":
        raise HTTPException(status_code=404, detail="Run not found")
    return RagEvalRunStatusResponse(**(result or {}))


@router.get("/rag/eval/run/{run_id}/samples", response_model=RagEvalSamplesPage)
def get_rag_eval_run_samples(
    run_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    tag: str | None = Query(default=None),
    failure_reason: str | None = Query(default=None),
    answer_correct: bool | None = Query(default=None),
    correctness: str | None = Query(default=None, description="correct/incorrect"),
    sample_ids: str | None = Query(default=None, description="逗号分隔 sample_id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, page_result = RagRunRouteService(db).get_run_samples(
        run_id=run_id,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        tag=tag,
        failure_reason=failure_reason,
        answer_correct=answer_correct,
        correctness=correctness,
        sample_ids_text=sample_ids,
    )
    if status == "run_not_found":
        raise HTTPException(status_code=404, detail="Run not found")
    return page_result
