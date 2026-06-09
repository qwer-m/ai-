from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from modules.rag_eval.services.rag_eval_candidate_service import (
    approve_candidate,
    build_candidate_draft,
    generate_candidates_from_run,
    list_candidates,
    reject_candidate,
)
from schemas.rag.rag_candidate import (
    RagCandidateApproveRequest,
    RagCandidateApproveResponse,
    RagCandidateDraftResponse,
    RagCandidateDraftUpdate,
    RagCandidateGenerateRequest,
    RagCandidateListPage,
    RagCandidateOut,
    RagCandidateRejectRequest,
    RagCandidateRejectResponse,
)

router = APIRouter(tags=["RAG Eval Candidates"])


@router.post("/rag/eval/candidates/generate")
def generate_rag_eval_candidates(
    payload: RagCandidateGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从评测运行结果批量生成候选回流样本。"""
    try:
        return generate_candidates_from_run(
            db=db,
            user_id=current_user.id,
            run_id=payload.run_id,
            filters=payload.filters.model_dump(),
            target_dataset_type=payload.target_dataset_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/rag/eval/candidates", response_model=RagCandidateListPage)
def list_rag_eval_candidates(
    status: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    failure_reason: str | None = Query(default=None),
    suggested_dataset_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """分页查询候选回流列表。"""
    items, total = list_candidates(
        db=db,
        user_id=current_user.id,
        status=status,
        source_type=source_type,
        failure_reason=failure_reason,
        suggested_dataset_type=suggested_dataset_type,
        page=page,
        page_size=page_size,
    )
    return RagCandidateListPage(
        items=[RagCandidateOut.model_validate(x) for x in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/rag/eval/candidates/{candidate_id}/draft", response_model=RagCandidateDraftResponse)
def draft_rag_eval_candidate(
    candidate_id: int,
    payload: RagCandidateDraftUpdate | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成或更新候选的评测样本草稿。"""
    try:
        _, draft = build_candidate_draft(
            db=db,
            user_id=current_user.id,
            candidate_id=candidate_id,
            draft_payload=payload.model_dump(exclude_unset=True) if payload else None,
        )
        return RagCandidateDraftResponse(candidate_id=candidate_id, draft=draft)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/rag/eval/candidates/{candidate_id}/approve", response_model=RagCandidateApproveResponse)
def approve_rag_eval_candidate(
    candidate_id: int,
    payload: RagCandidateApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审核通过候选并写入 challenge/regression 数据集。"""
    try:
        result = approve_candidate(
            db=db,
            user_id=current_user.id,
            candidate_id=candidate_id,
            target_dataset_type=payload.target_dataset_type,
            draft_payload=payload.draft.model_dump(exclude_unset=True) if payload.draft else None,
        )
        return RagCandidateApproveResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/rag/eval/candidates/{candidate_id}/reject", response_model=RagCandidateRejectResponse)
def reject_rag_eval_candidate(
    candidate_id: int,
    payload: RagCandidateRejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审核拒绝候选。"""
    try:
        row = reject_candidate(db=db, user_id=current_user.id, candidate_id=candidate_id, notes=payload.notes)
        return RagCandidateRejectResponse(success=True, candidate_id=int(row.id), status=row.status)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
