"""Context retrieval orchestration for the knowledge base."""

from __future__ import annotations

import time
from typing import Optional

from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument
from modules.knowledge_base_components.context.context_helpers import (
    _normalize_retrieval_options,
    _run_retrieval_once,
)
from modules.knowledge_base_components.retrieval.retrieval_profile import build_retrieval_profile
from modules.knowledge_base_components.retrieval.retrieval_retry import (
    STABILITY_CONFIG,
    build_final_chunk_debug,
    build_rerank_top,
    flatten_lane_reasons,
    is_retryable_exception,
    now_iso,
    should_retry,
)
from modules.domain.stage25_switches import STAGE25_SWITCHES

def get_relevant_context_impl(
    module,
    query: str,
    project_id: int,
    limit: int = 5,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
    debug: bool = False,
    max_tokens: int = 1800,
    retrieval_options: Optional[dict] = None,
) -> str | dict:
    """
    璇箟妫€绱笂涓嬫枃锛堟不鐞嗙増锛夈€?

    鍏煎绾﹀畾锛?
    - debug=False锛氳繑鍥炲瓧绗︿覆锛堜繚鎸佸巻鍙茶涓猴級
    - debug=True锛氳繑鍥?{"context": "...", "debug": {...}}
    """
    question = (query or "").strip()
    if not question:
        empty = {"context": "", "debug": {"original_query": "", "rewrite_queries": []}}
        return empty if debug else ""

    attempt_records: list[dict] = []
    last_outcome: Optional[dict] = None
    last_error: Optional[str] = None

    try:
        for attempt_no in range(1, STABILITY_CONFIG.max_retrieve_attempts + 1):
            started_at = now_iso()
            try:
                outcome = _run_retrieval_once(
                    question,
                    project_id,
                    limit,
                    max_tokens,
                    db=db,
                    retrieval_options=retrieval_options,
                )
                recall_debug = outcome.get("recall_result", {}).get("debug", {})
                lane_reasons = recall_debug.get("lane_reasons") or {}
                lane_counts = recall_debug.get("lane_counts") or {}
                has_context = bool(outcome.get("selected_chunks"))

                retry_triggered, retry_reason = should_retry(
                    lane_reasons=lane_reasons,
                    has_context=has_context,
                    attempt_no=attempt_no,
                    low_relevance_filtered=bool(outcome.get("low_relevance_filtered")),
                )
                attempt_records.append(
                    {
                        "attempt_no": attempt_no,
                        "started_at": started_at,
                        "lane_reasons": lane_reasons,
                        "lane_counts": lane_counts,
                        "result_chunk_count": len(outcome.get("selected_chunks") or []),
                        "retrieval_mode": str((outcome.get("retrieval_tuning") or {}).get("retrieval_mode") or ""),
                        "retry_triggered": bool(retry_triggered),
                        "retry_reason": retry_reason,
                    }
                )
                last_outcome = outcome

                if retry_triggered:
                    time.sleep(STABILITY_CONFIG.retry_backoff_ms / 1000.0)
                    continue
                break
            except Exception as attempt_error:
                last_error = str(attempt_error)
                retryable, retry_reason = is_retryable_exception(attempt_error)
                can_retry = retryable and attempt_no < STABILITY_CONFIG.max_retrieve_attempts
                attempt_records.append(
                    {
                        "attempt_no": attempt_no,
                        "started_at": started_at,
                        "lane_reasons": {},
                        "lane_counts": {},
                        "result_chunk_count": 0,
                        "retry_triggered": bool(can_retry),
                        "retry_reason": retry_reason,
                        "error": last_error,
                    }
                )
                if can_retry:
                    time.sleep(STABILITY_CONFIG.retry_backoff_ms / 1000.0)
                    continue
                break

        if last_outcome:
            recall_result = last_outcome.get("recall_result") or {}
            reranked_chunks = last_outcome.get("reranked_chunks") or []
            compressed = last_outcome.get("compressed") or {"selected_chunks": [], "stats": {}}
            selected_chunks = last_outcome.get("selected_chunks") or []
            context_text = str(last_outcome.get("context_text") or "")
            low_relevance_filtered = bool(last_outcome.get("low_relevance_filtered"))
            low_relevance_reason = str(last_outcome.get("low_relevance_reason") or "")
            low_relevance_threshold = last_outcome.get("low_relevance_threshold") or {}
            low_relevance_gate_stats = last_outcome.get("low_relevance_gate_stats") or {}
            doc_hit_stats = last_outcome.get("doc_hit_stats") or []
            dominance_warning = last_outcome.get("dominance_warning")
            multi_doc_hint = last_outcome.get("multi_doc_hint")
            diversity_stats = last_outcome.get("diversity_stats") or {}
            retrieval_tuning = last_outcome.get("retrieval_tuning") or {}
            diverse_candidates = last_outcome.get("diverse_candidates") or []
        else:
            recall_result = {"debug": {}}
            reranked_chunks = []
            compressed = {"selected_chunks": [], "stats": {}}
            selected_chunks = []
            context_text = ""
            low_relevance_filtered = False
            low_relevance_reason = ""
            low_relevance_threshold = {
                "enabled": bool(STABILITY_CONFIG.low_rel_filter_enabled),
                "top1_threshold": float(STABILITY_CONFIG.low_rel_top1_threshold),
                "topk_avg_threshold": float(STABILITY_CONFIG.low_rel_topk_avg_threshold),
                "topk": int(STABILITY_CONFIG.low_rel_topk),
            }
            low_relevance_gate_stats = {
                "mode": "soft",
                "pre_candidate_count": 0,
                "post_candidate_count": 0,
                "warning": False,
                "reason": "",
                "title_keyword_relaxed": False,
            }
            doc_hit_stats = []
            dominance_warning = None
            multi_doc_hint = None
            diversity_stats = {}
            retrieval_tuning = _normalize_retrieval_options(limit, retrieval_options)
            diverse_candidates = []

        if not debug:
            return context_text

        final_status = "success"
        final_failure_reason = ""
        if selected_chunks and low_relevance_filtered:
            final_status = "success_with_low_relevance_warning"
        if not selected_chunks:
            reason_tokens = flatten_lane_reasons(recall_result.get("debug", {}).get("lane_reasons") or {})
            if low_relevance_filtered:
                final_status = "degraded_empty_context"
                final_failure_reason = "low_relevance_filtered"
            elif reason_tokens.intersection(STABILITY_CONFIG.retryable_reasons) and (
                len(attempt_records) >= STABILITY_CONFIG.max_retrieve_attempts
            ):
                final_status = "failed_after_retry"
                final_failure_reason = "retryable_errors_exhausted"
            elif last_error:
                final_status = "failed_after_retry"
                final_failure_reason = f"exception:{last_error}"
            else:
                final_status = "degraded_empty_context"
                final_failure_reason = "no_relevant_chunks"

        return {
            "context": context_text,
            "debug": {
                "original_query": recall_result.get("debug", {}).get("original_query"),
                "rewrite_queries": recall_result.get("debug", {}).get("rewrite_queries") or [],
                "lane_counts": recall_result.get("debug", {}).get("lane_counts") or {},
                "lane_reasons": recall_result.get("debug", {}).get("lane_reasons") or {},
                "lane_topk": recall_result.get("debug", {}).get("lane_topk") or {},
                "merged_count": int(recall_result.get("debug", {}).get("merged_count") or 0),
                "deduped_count": int(recall_result.get("debug", {}).get("deduped_count") or 0),
                "dedup_chunks": build_final_chunk_debug(recall_result.get("chunks") or []),
                "reranked_count": len(reranked_chunks),
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
                "retrieval_profile": (
                    build_retrieval_profile(
                        question=question,
                        recall_debug=recall_result.get("debug", {}) or {},
                        reranked_chunks=reranked_chunks,
                        selected_chunks=selected_chunks,
                        raw_chunks=recall_result.get("chunks") or [],
                        compressor_stats=compressed.get("stats") or {},
                        attempts=attempt_records,
                        final_status=final_status,
                        final_failure_reason=final_failure_reason,
                    )
                    if STAGE25_SWITCHES.retrieval_profile_enabled
                    else {}
                ),
                "stage25_switches": STAGE25_SWITCHES.to_dict()
                if STAGE25_SWITCHES.include_switches_in_debug
                else {},
            },
        }
    except Exception as e:
        error_payload = {
            "context": "",
            "debug": {
                "error": f"RAG retrieval failed: {e}",
                "attempt_count": len(attempt_records),
                "attempts": attempt_records,
                "final_status": "failed_after_retry",
                "final_failure_reason": str(e),
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
                "retrieval_tuning": _normalize_retrieval_options(limit, retrieval_options),
                "retrieval_profile": (
                    build_retrieval_profile(
                        question=question,
                        recall_debug={},
                        reranked_chunks=[],
                        selected_chunks=[],
                        raw_chunks=[],
                        compressor_stats={},
                        attempts=attempt_records,
                        final_status="failed_after_retry",
                        final_failure_reason=str(e),
                    )
                    if STAGE25_SWITCHES.retrieval_profile_enabled
                    else {}
                ),
                "stage25_switches": STAGE25_SWITCHES.to_dict()
                if STAGE25_SWITCHES.include_switches_in_debug
                else {},
            },
        }
        return error_payload if debug else ""


def get_all_context_impl(
    module,
    db: Session,
    project_id: int,
    user_id: Optional[int] = None,
    max_docs: Optional[int] = 50,
) -> str:
    """Aggregate all document context for broad analysis scenarios."""
    query = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
    if max_docs:
        query = query.order_by(KnowledgeDocument.created_at.desc()).limit(max_docs)
    docs = query.all()
    context = ""
    for doc in docs:
        content_to_use = module._ensure_summary(doc, db, user_id)
        context += f"""--- Document: {doc.filename} ({doc.doc_type}) ---
{content_to_use}

"""
    return context


