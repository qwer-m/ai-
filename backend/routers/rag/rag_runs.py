from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import Project, RagDatasetSample, RagEvalRun, RagEvalSampleResult, User
from modules.rag_eval.services.rag_eval_compare_service import compare_runs
from modules.rag_eval.services.rag_eval_service import resume_eval_run, start_eval_run, stop_eval_run
from modules.rag_eval.repositories.rag_eval_repo import get_run, list_run_sample_results
from schemas.rag.rag_eval import (
    RagEvalRunCreate,
    RagEvalRunOut,
    RagEvalRunStatusResponse,
    RagEvalSampleResultOut,
    RagEvalSamplesPage,
)

router = APIRouter(tags=["RAG Eval Runs"])


def _get_owned_project(project_id: int, db: Session, user_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/rag/eval/run")
def create_rag_eval_run(
    payload: RagEvalRunCreate,
    project_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)
    try:
        run = start_eval_run(
            db=db,
            user_id=current_user.id,
            project_id=project_id,
            dataset_id=payload.dataset_id,
            config=payload.config,
            run_name=payload.run_name,
        )
        return {"run_id": run.id, "status": run.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/rag/eval/run/{run_id}/stop", response_model=RagEvalRunOut)
def stop_rag_eval_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        run = stop_eval_run(db=db, user_id=current_user.id, run_id=run_id)
        return RagEvalRunOut.model_validate(run)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/rag/eval/run/{run_id}/resume", response_model=RagEvalRunOut)
def resume_rag_eval_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """断点续跑：从 cursor 继续执行当前 run。"""
    try:
        run = resume_eval_run(db=db, user_id=current_user.id, run_id=run_id)
        return RagEvalRunOut.model_validate(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/rag/eval/run/compare")
def compare_rag_eval_runs(
    run_a: int = Query(..., ge=1),
    run_b: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """运行对比：run_b - run_a。"""
    if run_a == run_b:
        raise HTTPException(status_code=400, detail="run_a and run_b must be different")
    try:
        return compare_runs(db=db, run_a_id=run_a, run_b_id=run_b, user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/rag/eval/run/{run_id}", response_model=RagEvalRunStatusResponse)
def get_rag_eval_run_status(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = get_run(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    total = int(run.total_samples or 0)
    finished = int(run.finished_samples or 0)
    progress = {
        "total_samples": total,
        "finished_samples": finished,
        "progress_pct": (finished / total * 100.0) if total > 0 else 0.0,
        "status": run.status,
        "cursor": int(run.cursor or 0),
    }

    rows = db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id == run.id).all()
    sample_stats = {
        "failure_reason_counts": dict(Counter([str(x.failure_reason) for x in rows if x.failure_reason])),
        "correct_count": sum(1 for x in rows if x.answer_correct),
        "incorrect_count": sum(1 for x in rows if not x.answer_correct),
    }

    return RagEvalRunStatusResponse(
        run=RagEvalRunOut.model_validate(run),
        progress=progress,
        metrics=(run.metrics_json or {}),
        sample_stats=sample_stats,
    )


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
    run = get_run(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if answer_correct is None and correctness:
        correctness_norm = correctness.strip().lower()
        if correctness_norm in {"correct", "true", "1", "yes"}:
            answer_correct = True
        elif correctness_norm in {"incorrect", "false", "0", "no"}:
            answer_correct = False

    selected_sample_ids: list[int] = []
    if sample_ids:
        selected_sample_ids = [int(x.strip()) for x in sample_ids.split(",") if x.strip().isdigit()]

    rows, total = list_run_sample_results(
        db,
        run_id,
        page=page,
        page_size=page_size,
        tag=tag,
        failure_reason=failure_reason,
        answer_correct=answer_correct,
        sample_ids=selected_sample_ids or None,
    )
    # 中文注释：补齐样本业务字段，避免前端只能看到 ID 无法定位问题。
    sample_ids = [int(x.sample_id) for x in rows]
    sample_map = (
        {
            x.id: x
            for x in db.query(RagDatasetSample)
            .filter(RagDatasetSample.id.in_(sample_ids))
            .all()
        }
        if sample_ids
        else {}
    )

    items: list[RagEvalSampleResultOut] = []
    for row in rows:
        sample = sample_map.get(int(row.sample_id))
        payload: dict[str, Any] = {
            **row.__dict__,
            "sample_query": sample.query if sample else None,
            "gold_docs": list(sample.gold_docs or []) if sample else [],
            "gold_chunks": [str(x) for x in (sample.gold_chunks or [])] if sample else [],
            "expected_answer": sample.gold_answer if sample else None,
            "answer_points": list(sample.answer_points or []) if sample else [],
            "tags": list(sample.tags or []) if sample else [],
            "difficulty": sample.difficulty if sample else None,
        }
        items.append(RagEvalSampleResultOut.model_validate(payload))

    return RagEvalSamplesPage(items=items, page=page, page_size=page_size, total=total)
