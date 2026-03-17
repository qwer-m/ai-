"""
知识库上下文检索编排实现。

职责：
1. 编排 query rewrite / recall / rerank / compress 这四层能力；
2. 对可恢复外部错误做轻量重试；
3. 对低相关结果做最小拦截，避免弱相关硬顶。
"""

from __future__ import annotations

import time
from typing import Optional

from sqlalchemy.orm import Session

from core.models import KnowledgeDocument
from modules.knowledge_base_components.context_compressor import compress_context
from modules.knowledge_base_components.recall_pipeline import recall_chunks
from modules.knowledge_base_components.reranker import rerank_chunks
from modules.knowledge_base_components.retrieval_retry import (
    STABILITY_CONFIG,
    build_final_chunk_debug,
    build_rerank_top,
    calc_low_relevance,
    flatten_lane_reasons,
    is_retryable_exception,
    now_iso,
    should_retry,
)


def _format_context_chunks(chunks: list[dict]) -> str:
    """将最终片段格式化为历史兼容的上下文块。"""
    blocks: list[str] = []
    for chunk in chunks:
        text = str(chunk.get("chunk_text") or "").strip()
        if not text:
            continue
        filename = chunk.get("filename") or "Unknown"
        doc_type = chunk.get("doc_type") or "Unknown"
        blocks.append(f"--- Relevant Knowledge: {filename} ({doc_type}) ---\n{text}")
    return "\n\n".join(blocks).strip() + ("\n\n" if blocks else "")


def _run_retrieval_once(question: str, project_id: int, limit: int, max_tokens: int) -> dict:
    """执行一次检索编排（不含重试控制）。"""
    recall_result = recall_chunks(
        question=question,
        project_id=project_id,
        top_k=max(limit * 3, 6),
        rewrite_count=1,
    )
    recalled_chunks = recall_result.get("chunks") or []

    reranked_chunks = rerank_chunks(
        chunks=recalled_chunks,
        question=question,
        top_k=max(limit * 4, 8),
    )

    low_filtered, low_reason, low_threshold = calc_low_relevance(reranked_chunks)
    if low_filtered:
        selected_chunks: list[dict] = []
        compressed = {
            "selected_chunks": [],
            "stats": {
                "input_count": len(reranked_chunks),
                "deduped_count": len(reranked_chunks),
                "dropped_noisy": 0,
                "dropped_over_budget": 0,
                "dropped_over_budget_chunks": [],
                "kept_by_original_priority": 0,
            },
        }
        context_text = ""
    else:
        compressed = compress_context(
            chunks=reranked_chunks,
            max_tokens=max_tokens,
            keep_original_top_n=2,
        )
        selected_chunks = (compressed.get("selected_chunks") or [])[: max(1, int(limit))]
        context_text = _format_context_chunks(selected_chunks)

    return {
        "recall_result": recall_result,
        "reranked_chunks": reranked_chunks,
        "compressed": compressed,
        "selected_chunks": selected_chunks,
        "context_text": context_text,
        "low_relevance_filtered": low_filtered,
        "low_relevance_reason": low_reason,
        "low_relevance_threshold": low_threshold,
    }


def get_relevant_context_impl(
    module,
    query: str,
    project_id: int,
    limit: int = 5,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
    debug: bool = False,
    max_tokens: int = 1800,
) -> str | dict:
    """
    语义检索上下文（治理版）。

    兼容约定：
    - debug=False：返回字符串（保持历史行为）
    - debug=True：返回 {"context": "...", "debug": {...}}
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
                outcome = _run_retrieval_once(question, project_id, limit, max_tokens)
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

        if not debug:
            return context_text

        final_status = "success"
        final_failure_reason = ""
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
                "reranked_count": len(reranked_chunks),
                "compressed_count": len(selected_chunks),
                "max_tokens": int(max_tokens),
                "compressor_stats": compressed.get("stats") or {},
                "rerank_top": build_rerank_top(reranked_chunks, limit=limit),
                "final_chunks": build_final_chunk_debug(selected_chunks),
                "attempt_count": len(attempt_records),
                "attempts": attempt_records,
                "final_status": final_status,
                "final_failure_reason": final_failure_reason,
                "low_relevance_filtered": low_relevance_filtered,
                "low_relevance_reason": low_relevance_reason,
                "low_relevance_threshold": low_relevance_threshold,
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
                "low_relevance_reason": "",
                "low_relevance_threshold": {
                    "enabled": bool(STABILITY_CONFIG.low_rel_filter_enabled),
                    "top1_threshold": float(STABILITY_CONFIG.low_rel_top1_threshold),
                    "topk_avg_threshold": float(STABILITY_CONFIG.low_rel_topk_avg_threshold),
                    "topk": int(STABILITY_CONFIG.low_rel_topk),
                },
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
    """全量上下文聚合，主要用于全局分析场景。"""
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
