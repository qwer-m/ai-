"""
多路召回管道（RAG 检索治理）。

目标：
1. 在现有 Chroma 索引基础上实现 original/rewrite + raw/summary 多路召回；
2. 对召回结果做可解释合并与去重，并补充调试信息；
3. 让原始 query 路由优先于 rewrite 路由，降低小语料噪音。
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Optional

from sqlalchemy.orm import Session

from modules.knowledge_base_components.adapters.chroma_vector_store import get_vector_store
from modules.knowledge_base_components.query.query_rewriter import rewrite_query
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from modules.knowledge_base_components.retrieval.retrieval_hybrid import (
    apply_hybrid_scores,
    build_keyword_candidates,
)

logger = logging.getLogger(__name__)

from .recall_pipeline_helpers import (
    _sanitize_doc_types,
    _search_lane,
    _build_lane_where,
    _build_project_where,
    _apply_min_max_scores,
    _dedupe_chunks_exact,
    _dedupe_chunks_by_containment,
    _expand_related_by_biz_key,
)

def recall_chunks(
    question: str,
    project_id: int,
    top_k: int = 6,
    rewrite_count: int = 1,
    db: Optional[Session] = None,
    retrieval_mode: str = "hybrid",
    enable_query_rewrite: bool = True,
    vector_weight: float = 0.6,
    keyword_weight: float = 0.25,
    title_weight: float = 0.15,
    doc_types: Optional[list[str]] = None,
    enable_biz_key_expansion: bool = True,
    related_top_k: int = 5,
    vector_store=None,
    document_repository=None,
) -> dict:
    """
    执行检索治理层的多路召回。

    返回：
    - chunks: 合并去重后的 chunk 列表
    - debug: 召回侧可观测信息
    """
    mode = str(retrieval_mode or "hybrid").strip().lower()
    if mode not in {"vector", "keyword", "hybrid", "bm25"}:
        mode = "hybrid"
    active_vector_store = vector_store or get_vector_store()
    active_doc_repository = document_repository or (KnowledgeDocumentRepository(db) if db is not None else None)
    sanitized_doc_types = _sanitize_doc_types(doc_types)

    if enable_query_rewrite:
        all_queries = rewrite_query(question, max_queries=max(1, int(rewrite_count) + 1))
    else:
        all_queries = [str(question or "").strip()]

    if not all_queries:
        return {
            "chunks": [],
            "debug": {
                "original_query": "",
                "rewrite_queries": [],
                "lane_counts": {
                    "original_raw": 0,
                    "original_summary": 0,
                    "rewrite_raw": 0,
                    "rewrite_summary": 0,
                },
                "lane_reasons": {
                    "original_raw": "disabled",
                    "original_summary": "disabled",
                    "rewrite_raw": "disabled",
                    "rewrite_summary": "disabled",
                },
                "lane_topk": {
                    "original_raw": 0,
                    "original_summary": 0,
                    "rewrite_raw": 0,
                    "rewrite_summary": 0,
                },
                "query_embedding_status": "failed",
                "query_embedding_error": "empty_query_after_rewrite",
                "recall_lanes": {},
                "merge_stage": {
                    "before_merge_count": 0,
                    "after_merge_count": 0,
                    "after_dedup_count": 0,
                },
                "merged_count": 0,
                "deduped_count": 0,
            },
        }

    original_query = all_queries[0]
    rewrite_queries = all_queries[1 : 1 + max(0, int(rewrite_count))]

    base_top_k = max(1, int(top_k))
    lane_plan: list[tuple[str, str, str, int]] = [
        (original_query, "original", "raw", base_top_k * 2),
        (original_query, "original", "summary", base_top_k),
    ]
    for rq in rewrite_queries:
        lane_plan.append((rq, "rewrite", "raw", base_top_k))
        lane_plan.append((rq, "rewrite", "summary", max(1, base_top_k // 2)))

    lane_counts: dict[str, int] = {
        "original_raw": 0,
        "original_summary": 0,
        "rewrite_raw": 0,
        "rewrite_summary": 0,
        "keyword_docs": 0,
    }
    lane_topk: dict[str, int] = {
        "original_raw": base_top_k * 2,
        "original_summary": base_top_k,
        "rewrite_raw": base_top_k,
        "rewrite_summary": max(1, base_top_k // 2),
        "keyword_docs": max(4, base_top_k * 2),
    }
    lane_reason_sets: dict[str, set[str]] = {
        "original_raw": set(),
        "original_summary": set(),
        "rewrite_raw": set(),
        "rewrite_summary": set(),
        "keyword_docs": set(),
    }
    recall_lanes: dict[str, dict] = {}

    if not rewrite_queries:
        lane_reason_sets["rewrite_raw"].add("disabled")
        lane_reason_sets["rewrite_summary"].add("disabled")

    merged_chunks: list[dict] = []
    for q, query_source, chunk_source, lane_k in lane_plan:
        lane_key = f"{query_source}_{chunk_source}"
        lane_output = _search_lane(
            query=q,
            project_id=project_id,
            top_k=lane_k,
            chunk_source=chunk_source,
            query_source=query_source,
            vector_store=active_vector_store,
            doc_types=sanitized_doc_types,
        )
        lane_chunks = lane_output.get("chunks") or []
        lane_reason = str(lane_output.get("reason") or "no_hit")
        recall_lanes[lane_key] = lane_output.get("lane_debug") or {
            "executed": True,
            "where_filter": _build_lane_where(project_id=project_id, chunk_source=chunk_source, doc_types=sanitized_doc_types),
            "fallback_where_filter": _build_project_where(project_id=project_id, doc_types=sanitized_doc_types),
            "raw_result_count": len(lane_chunks),
            "usable_result_count": len(lane_chunks),
            "error": str(lane_output.get("error") or ""),
            "error_stage": "",
            "fallback_used": False,
            "query_embedding_status": "unknown",
        }

        lane_counts[lane_key] += len(lane_chunks)
        lane_reason_sets[lane_key].add(lane_reason)
        merged_chunks.extend(lane_chunks)

    if mode in {"keyword", "hybrid", "bm25"}:
        keyword_candidates = build_keyword_candidates(
            query=original_query,
            project_id=project_id,
            db=db,
            doc_repository=active_doc_repository,
            query_source="original",
            top_docs=max(4, base_top_k * 2),
            per_doc_chunks=2,
            doc_types=sanitized_doc_types,
        )
        lane_counts["keyword_docs"] = len(keyword_candidates)
        lane_reason_sets["keyword_docs"].add("ok" if keyword_candidates else "no_hit")
        merged_chunks.extend(keyword_candidates)
    else:
        lane_reason_sets["keyword_docs"].add("disabled")

    merge_before_count = len(merged_chunks)
    _apply_min_max_scores(merged_chunks)
    apply_hybrid_scores(
        chunks=merged_chunks,
        query=original_query,
        retrieval_mode=mode,
        vector_weight=vector_weight,
        keyword_weight=keyword_weight,
        title_weight=title_weight,
    )

    dedup_exact_chunks = _dedupe_chunks_exact(merged_chunks)
    merge_after_count = len(dedup_exact_chunks)
    deduped_before_relation = _dedupe_chunks_by_containment(dedup_exact_chunks)
    deduped_chunks = list(deduped_before_relation)

    relation_debug = {"expanded_count": 0, "biz_keys": []}
    if enable_biz_key_expansion:
        deduped_chunks, relation_debug = _expand_related_by_biz_key(
            question=original_query,
            project_id=project_id,
            chunks=deduped_chunks,
            top_k_per_biz=max(1, int(related_top_k)),
            vector_store=active_vector_store,
        )

    for lane_key in ("rewrite_raw", "rewrite_summary"):
        if lane_key in recall_lanes:
            continue
        recall_lanes[lane_key] = {
            "executed": False,
            "where_filter": {},
            "fallback_where_filter": {},
            "raw_result_count": 0,
            "usable_result_count": 0,
            "error": "",
            "error_stage": "",
            "fallback_used": False,
            "query_embedding_status": "disabled",
        }

    vector_lane_keys = ("original_raw", "original_summary", "rewrite_raw", "rewrite_summary")
    vector_statuses: list[str] = []
    vector_errors: list[str] = []
    for lane_key in vector_lane_keys:
        lane_info = recall_lanes.get(lane_key) or {}
        status = str(lane_info.get("query_embedding_status") or "")
        if status:
            vector_statuses.append(status)
        err = str(lane_info.get("error") or "").strip()
        if err:
            vector_errors.append(err)

    if any(status == "success" for status in vector_statuses):
        query_embedding_status = "success"
    elif any(status == "fallback" for status in vector_statuses):
        query_embedding_status = "fallback"
    elif any(status == "failed" for status in vector_statuses):
        query_embedding_status = "failed"
    else:
        query_embedding_status = "failed"
    query_embedding_error = " | ".join(dict.fromkeys(vector_errors))

    lane_reasons: dict[str, Any] = {}
    for lane_key, reasons in lane_reason_sets.items():
        if not reasons:
            lane_reasons[lane_key] = "no_hit"
        elif len(reasons) == 1:
            lane_reasons[lane_key] = next(iter(reasons))
        else:
            lane_reasons[lane_key] = sorted(reasons)

    return {
        "chunks": deduped_chunks,
        "debug": {
            "original_query": original_query,
            "rewrite_queries": rewrite_queries,
            "lane_counts": lane_counts,
            "lane_reasons": lane_reasons,
            "lane_topk": lane_topk,
            "query_embedding_status": query_embedding_status,
            "query_embedding_error": query_embedding_error,
            "recall_lanes": recall_lanes,
            "merge_stage": {
                "before_merge_count": int(merge_before_count),
                "after_merge_count": int(merge_after_count),
                "after_dedup_count": int(len(deduped_before_relation)),
            },
            "merged_count": len(merged_chunks),
            "deduped_count": len(deduped_chunks),
            "doc_type_filter": sanitized_doc_types,
            "biz_relation_expand": relation_debug,
            "retrieval_mode": mode,
            "fusion_weights": {
                "vector_weight": float(vector_weight),
                "keyword_weight": float(keyword_weight),
                "title_weight": float(title_weight),
            },
        },
    }

