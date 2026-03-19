from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge_base import knowledge_base


def run_retrieval_debug(
    *,
    query: str,
    project_id: int,
    db: Session,
    user_id: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    """
    执行一次可观测的 RAG 检索。
    复用现有 retrieve-context 主链路，不重写算法。
    """
    retrieval_cfg = dict(config.get("retrieval") or {})
    context_cfg = dict(config.get("context") or {})
    top_k = int(retrieval_cfg.get("top_k") or 5)
    max_tokens = int(context_cfg.get("max_tokens") or 1800)

    started = time.perf_counter()
    result = knowledge_base.get_relevant_context(
        query=query,
        project_id=project_id,
        limit=max(1, min(20, top_k)),
        db=db,
        user_id=user_id,
        debug=True,
        max_tokens=max(128, min(8000, max_tokens)),
    )
    retrieval_latency_ms = (time.perf_counter() - started) * 1000

    debug = (result or {}).get("debug") or {}
    final_chunks = debug.get("final_chunks") or []
    rerank_top = debug.get("rerank_top") or []
    lane_counts = debug.get("lane_counts") or {}
    lane_reasons = debug.get("lane_reasons") or {}
    context_text = str((result or {}).get("context") or "")

    # 中文注释：从重排结果和最终结果提取统一 chunk_id，便于指标计算。
    reranked_chunk_ids = [str(x.get("chunk_id") or "") for x in rerank_top if str(x.get("chunk_id") or "").strip()]
    retrieved_chunk_ids = [str(x.get("chunk_id") or "") for x in final_chunks if str(x.get("chunk_id") or "").strip()]

    return {
        "query": query,
        "context": context_text,
        "debug": debug,
        "final_status": debug.get("final_status") or "unknown",
        "lane_counts": lane_counts,
        "lane_reasons": lane_reasons,
        "retrieved_chunks": final_chunks,
        "reranked_chunks": rerank_top,
        "retrieved_chunk_ids": retrieved_chunk_ids,
        "reranked_chunk_ids": reranked_chunk_ids,
        "retrieval_latency_ms": retrieval_latency_ms,
    }

