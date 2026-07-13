from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from modules.domain.knowledge_base import knowledge_base
from modules.knowledge_base_components.retrieval.retrieval_config import (
    build_retrieval_from_eval_config,
)


def run_retrieval_debug(
    *,
    query: str,
    project_id: int,
    db: Session,
    user_id: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute one observable RAG retrieval call.
    Reuses the main retrieve-context pipeline.
    """
    top_k, max_tokens, retrieval_tuning = build_retrieval_from_eval_config(config)
    retrieval_options = retrieval_tuning.to_dict()

    started = time.perf_counter()
    result = knowledge_base.get_relevant_context(
        query=query,
        project_id=project_id,
        limit=max(1, min(20, top_k)),
        db=db,
        user_id=user_id,
        debug=True,
        max_tokens=max(128, min(8000, max_tokens)),
        retrieval_options=retrieval_options,
    )
    retrieval_latency_ms = (time.perf_counter() - started) * 1000

    debug = (result or {}).get("debug") or {}
    final_chunks = debug.get("final_chunks") or []
    rerank_top = debug.get("rerank_top") or []
    lane_counts = debug.get("lane_counts") or {}
    lane_reasons = debug.get("lane_reasons") or {}
    doc_hit_stats = debug.get("doc_hit_stats") or []
    dominance_warning = debug.get("dominance_warning")
    multi_doc_hint = debug.get("multi_doc_hint")
    context_text = str((result or {}).get("context") or "")

    reranked_chunk_ids = [
        str(x.get("chunk_id") or "")
        for x in rerank_top
        if str(x.get("chunk_id") or "").strip()
    ]
    retrieved_chunk_ids = [
        str(x.get("chunk_id") or "")
        for x in final_chunks
        if str(x.get("chunk_id") or "").strip()
    ]

    return {
        "query": query,
        "context": context_text,
        "debug": debug,
        "final_status": debug.get("final_status") or "unknown",
        "lane_counts": lane_counts,
        "lane_reasons": lane_reasons,
        "doc_hit_stats": doc_hit_stats,
        "dominance_warning": dominance_warning,
        "multi_doc_hint": multi_doc_hint,
        "retrieved_chunks": final_chunks,
        "reranked_chunks": rerank_top,
        "retrieved_chunk_ids": retrieved_chunk_ids,
        "reranked_chunk_ids": reranked_chunk_ids,
        "retrieval_latency_ms": retrieval_latency_ms,
        "retrieval_options": retrieval_options,
    }
