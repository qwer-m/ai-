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

from core.db.models import KnowledgeDocument
from modules.knowledge_base_components.context.context_compressor import compress_context
from modules.knowledge_base_components.retrieval.retrieval_profile import build_retrieval_profile
from modules.knowledge_base_components.retrieval.pipeline.recall_pipeline import recall_chunks
from modules.knowledge_base_components.retrieval.reranker import rerank_chunks
from modules.knowledge_base_components.retrieval.retrieval_retry import (
    STABILITY_CONFIG,
    build_final_chunk_debug,
    build_rerank_top,
    calc_low_relevance,
    flatten_lane_reasons,
    is_retryable_exception,
    now_iso,
    should_retry,
)
from modules.knowledge_base_components.retrieval.retrieval_selection import (
    build_doc_hit_stats,
    build_dominance_warning,
    infer_multi_doc_query,
    select_diverse_chunks,
)
from modules.domain.stage25_switches import STAGE25_SWITCHES


def _safe_int(value: object, default: int, min_value: int, max_value: int) -> int:
    """安全整型转换并夹紧范围。"""
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except Exception:
        parsed = int(default)
    return max(min_value, min(max_value, parsed))


def _safe_float(value: object, default: float, min_value: float, max_value: float) -> float:
    """安全浮点转换并夹紧范围。"""
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except Exception:
        parsed = float(default)
    return max(min_value, min(max_value, parsed))


def _normalize_retrieval_options(limit: int, retrieval_options: Optional[dict]) -> dict:
    """
    归一化检索调优参数，供单条调试与批量评测共用。

    说明：
    - 不传参数时走稳定默认值；
    - 只做检索侧调优，不改业务生成主流程。
    """
    opts = dict(retrieval_options or {})
    return {
        "retrieval_mode": str(opts.get("retrieval_mode") or "hybrid").lower(),
        "recall_top_k": _safe_int(opts.get("recall_top_k"), max(limit * 5, 20), 6, 80),
        "rerank_top_n": _safe_int(opts.get("rerank_top_n"), max(limit * 4, 8), 4, 80),
        "max_chunks_per_doc": _safe_int(opts.get("max_chunks_per_doc"), 2, 1, 3),
        "min_docs": _safe_int(opts.get("min_docs"), 2, 1, 12),
        "enable_query_rewrite": bool(opts.get("enable_query_rewrite", True)),
        "enable_rerank": bool(opts.get("enable_rerank", True)),
        "vector_weight": _safe_float(opts.get("vector_weight"), 0.6, 0.0, 3.0),
        "keyword_weight": _safe_float(opts.get("keyword_weight"), 0.25, 0.0, 3.0),
        "title_weight": _safe_float(opts.get("title_weight"), 0.15, 0.0, 3.0),
        "redundancy_threshold": _safe_float(opts.get("redundancy_threshold"), 0.88, 0.5, 0.99),
    }


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


def _run_retrieval_once(
    question: str,
    project_id: int,
    limit: int,
    max_tokens: int,
    db: Optional[Session] = None,
    retrieval_options: Optional[dict] = None,
) -> dict:
    """执行一次检索编排（不含重试控制）。"""
    tuning = _normalize_retrieval_options(limit, retrieval_options)

    recall_result = recall_chunks(
        question=question,
        project_id=project_id,
        top_k=tuning["recall_top_k"],
        rewrite_count=1 if tuning["enable_query_rewrite"] else 0,
        db=db,
        retrieval_mode=tuning["retrieval_mode"],
        enable_query_rewrite=tuning["enable_query_rewrite"],
        vector_weight=tuning["vector_weight"],
        keyword_weight=tuning["keyword_weight"],
        title_weight=tuning["title_weight"],
    )
    recalled_chunks = recall_result.get("chunks") or []

    if tuning["enable_rerank"]:
        reranked_chunks = rerank_chunks(
            chunks=recalled_chunks,
            question=question,
            top_k=tuning["rerank_top_n"],
        )
    else:
        # 中文注释：关闭重排时仍按融合分排序，保持结果可解释。
        reranked_chunks = sorted(
            recalled_chunks,
            key=lambda x: float(x.get("fusion_score") or x.get("score") or 0.0),
            reverse=True,
        )[: tuning["rerank_top_n"]]

    # 中文注释：低相关从硬拦截改为软拦截，只做告警，不直接清空上下文。
    low_warning, low_reason, low_threshold = calc_low_relevance(reranked_chunks)
    low_gate_pre_candidate_count = len(reranked_chunks)
    low_gate_post_candidate_count = len(reranked_chunks)

    multi_doc_query = infer_multi_doc_query(question)
    effective_min_docs = int(tuning["min_docs"])
    # 中文注释：功能/流程类 query 默认至少覆盖 2 篇文档。
    if multi_doc_query:
        effective_min_docs = max(2, effective_min_docs)

    # 中文注释：先做文档覆盖与冗余控制，再进入上下文压缩，避免同文档霸榜。
    diverse_candidates, diversity_stats = select_diverse_chunks(
        reranked_chunks,
        final_top_n=max(limit * 3, effective_min_docs * 2),
        max_chunks_per_doc=tuning["max_chunks_per_doc"],
        min_docs=effective_min_docs,
        redundancy_threshold=tuning["redundancy_threshold"],
    )
    doc_hit_stats = build_doc_hit_stats(reranked_chunks)
    dominance_warning = build_dominance_warning(reranked_chunks, top_n=min(10, tuning["rerank_top_n"]))
    multi_doc_hint = None
    if multi_doc_query and int(diversity_stats.get("doc_count") or 0) < effective_min_docs:
        multi_doc_hint = {
            "message": f"该问题更像流程/规则类，建议至少覆盖 {effective_min_docs} 篇文档，当前仅覆盖 {diversity_stats.get('doc_count') or 0} 篇。",
            "expected_min_docs": effective_min_docs,
            "current_docs": int(diversity_stats.get("doc_count") or 0),
        }

    compressed = compress_context(
        chunks=diverse_candidates,
        max_tokens=max_tokens,
        keep_original_top_n=2,
    )
    selected_chunks = (compressed.get("selected_chunks") or [])[: max(1, int(limit))]
    context_text = _format_context_chunks(selected_chunks)

    if not selected_chunks and reranked_chunks:
        fallback_count = max(1, min(int(limit), 2))
        fallback_chunks = [dict(x) for x in reranked_chunks[:fallback_count]]
        for chunk in fallback_chunks:
            chunk["selection_reason"] = "soft_gate_fallback_round"
        selected_chunks = fallback_chunks
        context_text = _format_context_chunks(selected_chunks)
        compressed_stats = dict(compressed.get("stats") or {})
        compressed_stats["soft_gate_fallback_applied"] = True
        compressed_stats["soft_gate_fallback_count"] = len(fallback_chunks)
        compressed = {
            **compressed,
            "selected_chunks": selected_chunks,
            "stats": compressed_stats,
        }

    low_gate_stats = {
        "mode": "soft",
        "pre_candidate_count": int(low_gate_pre_candidate_count),
        "post_candidate_count": int(low_gate_post_candidate_count),
        "warning": bool(low_warning),
        "reason": str(low_reason or ""),
        "title_keyword_relaxed": bool((low_threshold or {}).get("title_keyword_relaxed")),
    }

    return {
        "recall_result": recall_result,
        "reranked_chunks": reranked_chunks,
        "compressed": compressed,
        "selected_chunks": selected_chunks,
        "context_text": context_text,
        "low_relevance_filtered": low_warning,
        "low_relevance_reason": low_reason,
        "low_relevance_threshold": low_threshold,
        "low_relevance_gate_stats": low_gate_stats,
        "doc_hit_stats": doc_hit_stats,
        "dominance_warning": dominance_warning,
        "multi_doc_hint": multi_doc_hint,
        "diversity_stats": diversity_stats,
        "retrieval_tuning": {**tuning, "min_docs_effective": effective_min_docs},
        "diverse_candidates": diverse_candidates,
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
    retrieval_options: Optional[dict] = None,
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
