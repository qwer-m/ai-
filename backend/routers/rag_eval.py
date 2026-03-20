from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.ai_client import get_client_for_user
from core.auth import get_current_user
from core.database import get_db
from core.models import Project, RagDataset, RagDatasetSample, User
from modules.rag_eval.rag_retrieval_service import run_retrieval_debug
from schemas.rag_eval import RagSamplePromoteRequest, RagSamplePromoteResponse

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
    target_type = (payload.target_dataset_type or "").strip().lower()
    if target_type not in {"challenge", "regression"}:
        raise HTTPException(status_code=400, detail="target_dataset_type must be challenge/regression")

    sample = (
        db.query(RagDatasetSample)
        .join(RagDataset, RagDataset.id == RagDatasetSample.dataset_id)
        .filter(RagDatasetSample.id == sample_id, RagDataset.user_id == current_user.id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found")

    target_name = "自动回流-挑战集" if target_type == "challenge" else "自动回流-回归集"
    target_ds = db.query(RagDataset).filter(RagDataset.user_id == current_user.id, RagDataset.type == target_type, RagDataset.name == target_name).first()
    if not target_ds:
        target_ds = RagDataset(
            user_id=current_user.id,
            name=target_name,
            type=target_type,
            description="由评测失败样本自动回流生成",
        )
        db.add(target_ds)
        db.commit()
        db.refresh(target_ds)

    clone = RagDatasetSample(
        dataset_id=target_ds.id,
        query=sample.query,
        gold_docs=sample.gold_docs or [],
        gold_chunks=sample.gold_chunks or [],
        gold_answer=sample.gold_answer,
        answer_points=sample.answer_points or [],
        tags=sample.tags or [],
        difficulty=sample.difficulty,
        metadata_filters=sample.metadata_filters or {},
        expected_doc_version=sample.expected_doc_version,
        enabled=True,
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)

    return RagSamplePromoteResponse(success=True, target_dataset_id=target_ds.id, target_sample_id=clone.id)


@router.post("/rag/eval/debug/single")
def rag_single_debug(
    req: RagSingleDebugRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """单条调试扩展：返回检索、上下文、答案、耗时。"""
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    retrieval_started = time.perf_counter()
    retrieval_result = run_retrieval_debug(
        query=req.query.strip(),
        project_id=req.project_id,
        db=db,
        user_id=current_user.id,
        config={
            "retrieval": {
                "top_k": req.limit,
                "retrieval_mode": req.retrieval_mode,
                "recall_top_k": req.recall_top_k,
                "rerank_top_n": req.rerank_top_n,
                "max_chunks_per_doc": req.max_chunks_per_doc,
                "min_docs": req.min_docs,
                "title_weight": req.title_weight,
                "keyword_weight": req.keyword_weight,
                "vector_weight": req.vector_weight,
                "redundancy_threshold": req.redundancy_threshold,
            },
            "context": {"max_tokens": req.max_tokens},
            "advanced": {
                "enable_query_rewrite": req.enable_query_rewrite,
                "enable_rerank": req.enable_rerank,
            },
        },
    )
    retrieval_ms = (time.perf_counter() - retrieval_started) * 1000

    context_text = str(retrieval_result.get("context") or "")
    generation_started = time.perf_counter()
    answer = ""
    if context_text:
        client = get_client_for_user(current_user.id, db)
        answer = client.generate_response(
            user_input=f"问题：{req.query}\n\n上下文：\n{context_text}\n\n请仅基于上下文回答。",
            system_prompt="你是RAG调试助手，禁止编造。",
            db=db,
            model=req.llm_model or None,
            task_type="general",
        )
    generation_ms = (time.perf_counter() - generation_started) * 1000

    token_usage = {
        "input_tokens": int(max(1, len(context_text) / 4)),
        "output_tokens": int(max(0, len(answer) / 4)),
    }
    token_usage["total_tokens"] = token_usage["input_tokens"] + token_usage["output_tokens"]

    debug = retrieval_result.get("debug") or {}
    return {
        "query": req.query,
        "rewritten_queries": debug.get("rewrite_queries") or [],
        "raw_retrieved_chunks": debug.get("dedup_chunks") or [],
        "reranked_chunks": debug.get("rerank_top") or [],
        "final_context": context_text,
        "llm_output": answer,
        "token_usage": token_usage,
        "timing_ms": {
            "retrieval": retrieval_ms,
            "generation": generation_ms,
            "total": retrieval_ms + generation_ms,
        },
        "doc_hit_stats": retrieval_result.get("doc_hit_stats") or [],
        "dominance_warning": retrieval_result.get("dominance_warning"),
        "multi_doc_hint": retrieval_result.get("multi_doc_hint"),
        "retrieval_options": retrieval_result.get("retrieval_options") or {},
        "debug": debug,
    }
