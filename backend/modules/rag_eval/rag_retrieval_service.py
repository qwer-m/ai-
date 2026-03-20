from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge_base import knowledge_base


def _pick(config: dict[str, Any], keys: list[str], default: Any) -> Any:
    """按候选 key 顺序取配置值，兼容老新两种参数结构。"""
    for key in keys:
        if key in config and config.get(key) is not None:
            return config.get(key)
    return default


def _build_retrieval_options(config: dict[str, Any]) -> tuple[int, int, dict[str, Any]]:
    """
    归一化 RAG 调优配置。

    兼容两种来源：
    - 单条调试：顶层直接传参；
    - 批量评测：config.retrieval / config.advanced / config.context。
    """
    retrieval_cfg = dict(config.get("retrieval") or {})
    context_cfg = dict(config.get("context") or {})
    advanced_cfg = dict(config.get("advanced") or {})

    top_k = int(_pick({**retrieval_cfg, **config}, ["top_k", "limit"], 5))
    max_tokens = int(_pick({**context_cfg, **config}, ["max_tokens"], 1800))

    options = {
        "retrieval_mode": str(_pick({**retrieval_cfg, **config}, ["retrieval_mode"], "hybrid")).lower(),
        "recall_top_k": int(_pick({**retrieval_cfg, **config}, ["recall_top_k"], max(top_k * 5, 20))),
        "rerank_top_n": int(_pick({**retrieval_cfg, **config}, ["rerank_top_n"], max(top_k * 4, 8))),
        "max_chunks_per_doc": int(_pick({**retrieval_cfg, **config}, ["max_chunks_per_doc"], 2)),
        "min_docs": int(_pick({**retrieval_cfg, **config}, ["min_docs"], 2)),
        "vector_weight": float(_pick({**retrieval_cfg, **config}, ["vector_weight"], 0.6)),
        "keyword_weight": float(_pick({**retrieval_cfg, **config}, ["keyword_weight"], 0.25)),
        "title_weight": float(_pick({**retrieval_cfg, **config}, ["title_weight"], 0.15)),
        "redundancy_threshold": float(
            _pick({**retrieval_cfg, **config}, ["redundancy_threshold"], 0.88)
        ),
        "enable_query_rewrite": bool(
            _pick({**advanced_cfg, **config}, ["enable_query_rewrite"], True)
        ),
        "enable_rerank": bool(_pick({**advanced_cfg, **config}, ["enable_rerank"], True)),
    }
    return top_k, max_tokens, options


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
    top_k, max_tokens, retrieval_options = _build_retrieval_options(config)

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
