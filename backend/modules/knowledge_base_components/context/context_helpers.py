from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from modules.knowledge_base_components.context.context_compressor import compress_context
from modules.knowledge_base_components.retrieval.pipeline.recall_pipeline import recall_chunks
from modules.knowledge_base_components.retrieval.retrieval_config import RetrievalConfig
from modules.knowledge_base_components.retrieval.reranker import rerank_chunks
from modules.knowledge_base_components.retrieval.retrieval_retry import calc_low_relevance
from modules.knowledge_base_components.retrieval.retrieval_selection import (
    build_doc_hit_stats,
    build_dominance_warning,
    infer_multi_doc_query,
    select_diverse_chunks,
)


def _normalize_retrieval_options(limit: int, retrieval_options: Optional[dict]) -> dict:
    """Normalize retrieval tuning options into a single dict."""
    tuning = RetrievalConfig.from_raw(limit=limit, values=retrieval_options)
    return tuning.to_dict()


def _format_context_chunks(chunks: list[dict]) -> str:
    """Render selected chunks into the context text format."""
    blocks: list[str] = []
    for chunk in chunks:
        text = str(chunk.get("chunk_text") or "").strip()
        if not text:
            continue
        filename = chunk.get("filename") or "Unknown"
        doc_type = chunk.get("doc_type") or "Unknown"
        blocks.append(f"--- Relevant Knowledge: {filename} ({doc_type}) ---\n{text}")
    return "\n\n".join(blocks).strip() + ("\n\n" if blocks else "")


def _prepare_rerank_candidates(chunks: list[dict]) -> tuple[list[dict], dict]:
    """
    在 rerank 前做最小结构体检，输出过滤统计。

    设计说明：
    - 仅过滤“结构不可用”候选（非 dict、缺失文本、空文本）；
    - 缺分数与 metadata 异常仅记录，不阻断主流程，避免过度改变现有行为。
    """
    reasons = {
        "missing_text": 0,
        "missing_score": 0,
        "invalid_metadata": 0,
        "empty_content": 0,
        "schema_incompatible": 0,
    }
    valid_chunks: list[dict] = []
    total = len(chunks or [])

    for raw in (chunks or []):
        if not isinstance(raw, dict):
            reasons["schema_incompatible"] += 1
            continue

        if "chunk_text" not in raw:
            reasons["missing_text"] += 1
            reasons["schema_incompatible"] += 1
            continue

        chunk_text = str(raw.get("chunk_text") or "")
        if not chunk_text.strip():
            reasons["empty_content"] += 1
            continue

        metadata = raw.get("metadata")
        item = dict(raw)
        if metadata is not None and not isinstance(metadata, dict):
            reasons["invalid_metadata"] += 1
            # 中文注释：metadata 非 dict 时归一化，避免下游访问异常。
            item["metadata"] = {}

        has_score = False
        for key in ("final_score", "fusion_score", "score", "vector_score"):
            value = item.get(key)
            if value is None:
                continue
            try:
                float(value)
                has_score = True
                break
            except Exception:
                continue
        if not has_score:
            reasons["missing_score"] += 1

        valid_chunks.append(item)

    filtered_out = max(0, total - len(valid_chunks))
    return valid_chunks, {
        "input_candidate_count": int(total),
        "filtered_out_count": int(filtered_out),
        "filtered_out_reasons": reasons,
    }


def _run_retrieval_once(
    question: str,
    project_id: int,
    limit: int,
    max_tokens: int,
    db: Optional[Session] = None,
    retrieval_options: Optional[dict] = None,
    recall_fn=None,
    rerank_fn=None,
) -> dict:
    """Run one retrieval and compression pass without retry control."""
    tuning = _normalize_retrieval_options(limit, retrieval_options)
    recall_callable = recall_fn or recall_chunks
    rerank_callable = rerank_fn or rerank_chunks

    recall_result = recall_callable(
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
        doc_types=tuning["doc_types"],
        enable_biz_key_expansion=tuning["enable_biz_key_expansion"],
        related_top_k=tuning["related_top_k"],
    )
    recalled_chunks = recall_result.get("chunks") or []
    rerank_candidates, rerank_stage = _prepare_rerank_candidates(recalled_chunks)

    if tuning["enable_rerank"]:
        reranked_chunks = rerank_callable(
            chunks=rerank_candidates,
            question=question,
            top_k=tuning["rerank_top_n"],
        )
    else:
        # Keep a score-based fallback when rerank is disabled.
        reranked_chunks = sorted(
            rerank_candidates,
            key=lambda x: float(x.get("fusion_score") or x.get("score") or 0.0),
            reverse=True,
        )[: tuning["rerank_top_n"]]

    low_warning, low_reason, low_threshold = calc_low_relevance(reranked_chunks)
    low_gate_pre_candidate_count = len(reranked_chunks)
    low_gate_post_candidate_count = len(reranked_chunks)

    multi_doc_query = infer_multi_doc_query(question)
    effective_min_docs = int(tuning["min_docs"])
    if multi_doc_query:
        effective_min_docs = max(2, effective_min_docs)

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
            "message": f"This question looks like a flow/rule query; cover at least {effective_min_docs} documents, but current coverage is only {diversity_stats.get('doc_count') or 0}.",
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

    compressed_stats = dict(compressed.get("stats") or {})
    empty_context_reason = ""
    if not selected_chunks:
        if not rerank_candidates:
            empty_context_reason = "no_valid_recall_candidates"
        elif not reranked_chunks:
            empty_context_reason = "no_reranked_candidates"
        elif not diverse_candidates:
            empty_context_reason = "no_diverse_candidates"
        elif int(compressed_stats.get("deduped_count") or 0) == 0:
            empty_context_reason = "all_candidates_rejected_by_compressor"
        else:
            empty_context_reason = "compressor_selected_no_context"

    # 中文注释：压缩器拒绝候选时保持空上下文，诊断原因由实际阶段计数推导，不回填低相关片段。
    compressed_stats["empty_context"] = not bool(selected_chunks)
    compressed_stats["empty_context_reason"] = empty_context_reason
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
        "rerank_stage": rerank_stage,
    }

