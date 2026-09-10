"""Debug payload builders for context retrieval."""

from __future__ import annotations

from typing import Any, Optional

from modules.knowledge_base_components.context.context_helpers import _normalize_retrieval_options
from modules.knowledge_base_components.retrieval.retrieval_retry import (
    STABILITY_CONFIG,
    build_final_chunk_debug,
    build_rerank_top,
    flatten_lane_reasons,
)


def _default_outcome(limit: int, retrieval_options: Optional[dict]) -> dict[str, Any]:
    return {
        "recall_result": {"debug": {}},
        "reranked_chunks": [],
        "compressed": {"selected_chunks": [], "stats": {}},
        "selected_chunks": [],
        "context_text": "",
        "low_relevance_filtered": False,
        "low_relevance_reason": "",
        "low_relevance_threshold": {
            "enabled": bool(STABILITY_CONFIG.low_rel_filter_enabled),
            "top1_threshold": float(STABILITY_CONFIG.low_rel_top1_threshold),
            "topk_avg_threshold": float(STABILITY_CONFIG.low_rel_topk_avg_threshold),
            "topk": int(STABILITY_CONFIG.low_rel_topk),
        },
        "low_relevance_gate_stats": {
            "mode": "soft",
            "pre_candidate_count": 0,
            "post_candidate_count": 0,
            "warning": False,
            "reason": "",
            "title_keyword_relaxed": False,
        },
        "doc_hit_stats": [],
        "dominance_warning": None,
        "multi_doc_hint": None,
        "diversity_stats": {},
        "retrieval_tuning": _normalize_retrieval_options(limit, retrieval_options),
        "diverse_candidates": [],
        "rerank_stage": {
            "input_candidate_count": 0,
            "filtered_out_count": 0,
            "filtered_out_reasons": {
                "missing_text": 0,
                "missing_score": 0,
                "invalid_metadata": 0,
                "empty_content": 0,
                "schema_incompatible": 0,
            },
        },
    }


def _resolve_final_status(
    *,
    selected_chunks: list[dict[str, Any]],
    low_relevance_filtered: bool,
    recall_result: dict[str, Any],
    attempt_records: list[dict[str, Any]],
    last_error: str,
) -> tuple[str, str]:
    final_status = "success"
    final_failure_reason = ""
    if selected_chunks and low_relevance_filtered:
        final_status = "success_with_low_relevance_warning"
    if selected_chunks:
        return final_status, final_failure_reason

    reason_tokens = flatten_lane_reasons(recall_result.get("debug", {}).get("lane_reasons") or {})
    if low_relevance_filtered:
        return "degraded_empty_context", "low_relevance_filtered"
    if reason_tokens.intersection(STABILITY_CONFIG.retryable_reasons) and (
        len(attempt_records) >= STABILITY_CONFIG.max_retrieve_attempts
    ):
        return "failed_after_retry", "retryable_errors_exhausted"
    if last_error:
        return "failed_after_retry", f"exception:{last_error}"
    return "degraded_empty_context", "no_relevant_chunks"


def build_success_payload(
    *,
    question: str,
    limit: int,
    max_tokens: int,
    retrieval_options: Optional[dict],
    attempt_records: list[dict[str, Any]],
    last_error: str,
    last_outcome: Optional[dict[str, Any]],
) -> dict[str, Any]:
    normalized = dict(last_outcome or _default_outcome(limit, retrieval_options))

    recall_result = normalized.get("recall_result") or {}
    reranked_chunks = normalized.get("reranked_chunks") or []
    compressed = normalized.get("compressed") or {"selected_chunks": [], "stats": {}}
    selected_chunks = normalized.get("selected_chunks") or []
    context_text = str(normalized.get("context_text") or "")
    low_relevance_filtered = bool(normalized.get("low_relevance_filtered"))
    low_relevance_reason = str(normalized.get("low_relevance_reason") or "")
    low_relevance_threshold = normalized.get("low_relevance_threshold") or {}
    low_relevance_gate_stats = normalized.get("low_relevance_gate_stats") or {}
    doc_hit_stats = normalized.get("doc_hit_stats") or []
    dominance_warning = normalized.get("dominance_warning")
    multi_doc_hint = normalized.get("multi_doc_hint")
    diversity_stats = normalized.get("diversity_stats") or {}
    retrieval_tuning = normalized.get("retrieval_tuning") or _normalize_retrieval_options(limit, retrieval_options)
    diverse_candidates = normalized.get("diverse_candidates") or []
    rerank_stage = normalized.get("rerank_stage") or _default_outcome(limit, retrieval_options)["rerank_stage"]

    final_status, final_failure_reason = _resolve_final_status(
        selected_chunks=selected_chunks,
        low_relevance_filtered=low_relevance_filtered,
        recall_result=recall_result,
        attempt_records=attempt_records,
        last_error=last_error,
    )

    return {
        "context": context_text,
        "debug": {
            "original_query": recall_result.get("debug", {}).get("original_query"),
            "rewrite_queries": recall_result.get("debug", {}).get("rewrite_queries") or [],
            "lane_counts": recall_result.get("debug", {}).get("lane_counts") or {},
            "lane_reasons": recall_result.get("debug", {}).get("lane_reasons") or {},
            "lane_topk": recall_result.get("debug", {}).get("lane_topk") or {},
            "query_embedding_status": recall_result.get("debug", {}).get("query_embedding_status") or "failed",
            "query_embedding_error": recall_result.get("debug", {}).get("query_embedding_error") or "",
            "recall_lanes": recall_result.get("debug", {}).get("recall_lanes") or {},
            "merge_stage": recall_result.get("debug", {}).get("merge_stage") or {},
            "merged_count": int(recall_result.get("debug", {}).get("merged_count") or 0),
            "deduped_count": int(recall_result.get("debug", {}).get("deduped_count") or 0),
            "dedup_chunks": build_final_chunk_debug(recall_result.get("chunks") or []),
            "reranked_count": len(reranked_chunks),
            "rerank_stage": rerank_stage,
            "diverse_count": len(diverse_candidates),
            "compressed_count": len(selected_chunks),
            "max_tokens": int(max_tokens),
            "compressor_stats": compressed.get("stats") or {},
            "rerank_top": build_rerank_top(
                reranked_chunks,
                limit=max(int(limit), int(retrieval_tuning.get("rerank_top_n") or limit)),
            ),
            "diverse_chunks": build_final_chunk_debug(diverse_candidates),
            "final_chunks": build_final_chunk_debug(selected_chunks),
            "doc_hit_stats": doc_hit_stats,
            "dominance_warning": dominance_warning,
            "multi_doc_hint": multi_doc_hint,
            "diversity_stats": diversity_stats,
            "retrieval_tuning": retrieval_tuning,
            "attempt_count": len(attempt_records),
            "attempts": attempt_records,
            "final_status": final_status,
            "final_failure_reason": final_failure_reason,
            "low_relevance_filtered": low_relevance_filtered,
            "low_relevance_warning": low_relevance_filtered,
            "low_relevance_reason": low_relevance_reason,
            "low_relevance_threshold": low_relevance_threshold,
            "low_relevance_gate": low_relevance_gate_stats,
            "gate_before_candidate_count": int(low_relevance_gate_stats.get("pre_candidate_count") or 0),
            "gate_after_candidate_count": int(low_relevance_gate_stats.get("post_candidate_count") or 0),
            "per_doc_selected_chunk_counts": diversity_stats.get("per_doc_counts") or {},
            "doc_coverage_triggered": bool(diversity_stats.get("doc_coverage_triggered")),
        },
    }


def build_error_payload(
    *,
    question: str,
    limit: int,
    retrieval_options: Optional[dict],
    attempt_records: list[dict[str, Any]],
    error: Exception,
) -> dict[str, Any]:
    error_text = str(error)
    return {
        "context": "",
        "debug": {
            "error": f"RAG retrieval failed: {error_text}",
            "query_embedding_status": "failed",
            "query_embedding_error": error_text,
            "recall_lanes": {},
            "merge_stage": {
                "before_merge_count": 0,
                "after_merge_count": 0,
                "after_dedup_count": 0,
            },
            "attempt_count": len(attempt_records),
            "attempts": attempt_records,
            "final_status": "failed_after_retry",
            "final_failure_reason": error_text,
            "low_relevance_filtered": False,
            "low_relevance_warning": False,
            "low_relevance_reason": "",
            "low_relevance_threshold": {
                "enabled": bool(STABILITY_CONFIG.low_rel_filter_enabled),
                "top1_threshold": float(STABILITY_CONFIG.low_rel_top1_threshold),
                "topk_avg_threshold": float(STABILITY_CONFIG.low_rel_topk_avg_threshold),
                "topk": int(STABILITY_CONFIG.low_rel_topk),
            },
            "low_relevance_gate": {
                "mode": "soft",
                "pre_candidate_count": 0,
                "post_candidate_count": 0,
                "warning": False,
                "reason": "",
                "title_keyword_relaxed": False,
            },
            "gate_before_candidate_count": 0,
            "gate_after_candidate_count": 0,
            "per_doc_selected_chunk_counts": {},
            "doc_coverage_triggered": False,
            "rerank_stage": _default_outcome(limit, retrieval_options)["rerank_stage"],
            "retrieval_tuning": _normalize_retrieval_options(limit, retrieval_options),
        },
    }

