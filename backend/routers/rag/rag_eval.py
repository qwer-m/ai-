from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from modules.rag_eval.services.rag_debug_route_service import RagDebugRouteService
from schemas.rag.rag_eval import RagSamplePromoteRequest, RagSamplePromoteResponse

router = APIRouter(tags=["RAG Eval"])


class RagSingleDebugRequest(BaseModel):
    project_id: int
    query: str = Field(..., min_length=1)
    limit: int = 5
    max_tokens: int = 1800
    llm_model: str | None = None
    retrieval_mode: str = "hybrid"
    recall_top_k: int | None = None
    rerank_top_n: int | None = None
    max_chunks_per_doc: int = 2
    min_docs: int = 2
    enable_query_rewrite: bool = True
    enable_rerank: bool = True
    title_weight: float = 0.15
    keyword_weight: float = 0.25
    vector_weight: float = 0.6
    redundancy_threshold: float = 0.88


@router.post("/rag/eval/sample/{sample_id}/promote", response_model=RagSamplePromoteResponse)
def promote_sample_to_dataset(
    sample_id: int,
    payload: RagSamplePromoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, result = RagDebugRouteService(db).promote_sample_to_dataset(
        sample_id=sample_id,
        target_dataset_type=payload.target_dataset_type,
        user_id=current_user.id,
    )
    if status == "invalid_target_type":
        raise HTTPException(status_code=400, detail="target_dataset_type must be challenge/regression")
    if status == "sample_not_found":
        raise HTTPException(status_code=404, detail="Sample not found")
    return RagSamplePromoteResponse(**(result or {}))


@router.post("/rag/eval/debug/single")
def rag_single_debug(
    req: RagSingleDebugRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, result = RagDebugRouteService(db).rag_single_debug(
        payload=req.model_dump(),
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result
