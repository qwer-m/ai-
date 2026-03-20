"""检索画像（Retrieval Profile）结构化构建。"""
from __future__ import annotations

import re
from typing import Any

from modules.stage25_switches import STAGE25_SWITCHES


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _score_band(scores: list[float]) -> dict[str, int]:
    bands = {"gte_0_9": 0, "0_8_to_0_9": 0, "0_7_to_0_8": 0, "lt_0_7": 0}
    for score in scores:
        if score >= 0.9:
            bands["gte_0_9"] += 1
        elif score >= 0.8:
            bands["0_8_to_0_9"] += 1
        elif score >= 0.7:
            bands["0_7_to_0_8"] += 1
        else:
            bands["lt_0_7"] += 1
    return bands


def _classify_query_type(question: str) -> str:
    text = str(question or "").strip().lower()
    if not text:
        return "unknown"

    if re.search(r"(步骤|流程|如何|怎么|怎样|先|后|when|how|process|流程图)", text):
        return "process"
    if re.search(r"(必须|不得|禁止|规则|约束|权限|should|must|rule|policy)", text):
        return "rule"
    if re.search(r"(边界|上限|下限|最大|最小|范围|临界|threshold|boundary|max|min|^\d+[-~至]\d+)", text):
        return "boundary"
    if re.search(r"(枚举|状态|类型|选项|列表|哪些|包括|enum|status|type)", text):
        return "enum"
    if re.search(r"(是什么|是否|多久|多少|why|what|who|where|when)", text):
        return "fact"
    return "unknown"


def _score_from_chunk(chunk: dict[str, Any]) -> float:
    return _safe_float(
        chunk.get("final_score")
        or chunk.get("rerank_score")
        or chunk.get("fusion_score")
        or chunk.get("score")
        or 0.0
    )


def _top_scores(chunks: list[dict[str, Any]], topk: int) -> list[float]:
    scores = sorted((_score_from_chunk(chunk) for chunk in chunks), reverse=True)
    return [round(float(x), 6) for x in scores[: max(1, int(topk))]]


def _doc_type_distribution(chunks: list[dict[str, Any]]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for chunk in chunks or []:
        key = str(chunk.get("doc_type") or "unknown").strip().lower() or "unknown"
        dist[key] = int(dist.get(key, 0)) + 1
    return dist


def _count_chars(chunks: list[dict[str, Any]]) -> int:
    return sum(len(str(chunk.get("chunk_text") or "")) for chunk in chunks or [])


def _has_snapshot_related_hit(chunks: list[dict[str, Any]]) -> bool:
    for chunk in chunks or []:
        filename = str(chunk.get("filename") or "").lower()
        doc_type = str(chunk.get("doc_type") or "").lower()
        text = str(chunk.get("chunk_text") or "").lower()
        if "snapshot" in filename or "snapshot" in doc_type:
            return True
        if "快照" in text or "snapshot" in text:
            return True
    return False


def build_retrieval_profile(
    *,
    question: str,
    recall_debug: dict[str, Any],
    reranked_chunks: list[dict[str, Any]],
    selected_chunks: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    final_status: str,
    final_failure_reason: str,
    raw_chunks: list[dict[str, Any]] | None = None,
    compressor_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    构建检索阶段结构化画像。
    说明：
    - 纯规则方案，不引入额外 ML；
    - 字段兼容已有 profile，同时补充评测面板所需关键指标。
    """
    raw_chunks = list(raw_chunks or [])
    compressor_stats = dict(compressor_stats or {})
    lane_counts = recall_debug.get("lane_counts") or {}
    lane_reasons = recall_debug.get("lane_reasons") or {}
    lane_topk = recall_debug.get("lane_topk") or {}
    topk = max(1, int(STAGE25_SWITCHES.retrieval_profile_topk or 10))

    rerank_scores = [_score_from_chunk(chunk) for chunk in (reranked_chunks or [])]
    selected_scores = [_score_from_chunk(chunk) for chunk in (selected_chunks or [])]

    kept_by_query_source = {"original": 0, "rewrite": 0, "unknown": 0}
    kept_by_chunk_source = {"raw": 0, "summary": 0, "unknown": 0}
    for chunk in selected_chunks or []:
        query_source = str(chunk.get("query_source") or "unknown").strip().lower()
        chunk_source = str(chunk.get("chunk_source") or "unknown").strip().lower()
        kept_by_query_source[query_source if query_source in kept_by_query_source else "unknown"] += 1
        kept_by_chunk_source[chunk_source if chunk_source in kept_by_chunk_source else "unknown"] += 1

    retry_triggered_count = 0
    retry_reasons: list[str] = []
    for attempt in attempts or []:
        if bool(attempt.get("retry_triggered")):
            retry_triggered_count += 1
            reason = str(attempt.get("retry_reason") or "").strip()
            if reason:
                retry_reasons.append(reason)

    compressed_before_chars = int(
        compressor_stats.get("input_chars")
        or _count_chars(raw_chunks)
        or _count_chars(reranked_chunks)
    )
    compressed_after_chars = int(
        compressor_stats.get("output_chars")
        or _count_chars(selected_chunks)
    )

    return {
        "profile_version": "2.5",
        # 新增核心字段（任务A要求）
        "query_type": _classify_query_type(question),
        "query_length": len(question or ""),
        "rewrite_count": len(recall_debug.get("rewrite_queries") or []),
        "recall_lane_hits": lane_counts,
        "raw_topk_scores": _top_scores(raw_chunks or (reranked_chunks or []), topk=topk),
        "rerank_top_scores": _top_scores(reranked_chunks or [], topk=topk),
        "final_chunk_count": len(selected_chunks or []),
        "final_doc_type_distribution": _doc_type_distribution(selected_chunks or []),
        "compressed_before_chars": compressed_before_chars,
        "compressed_after_chars": compressed_after_chars,
        "snapshot_related_hit": _has_snapshot_related_hit(selected_chunks or []),
        # 兼容旧字段
        "question_length": len(question or ""),
        "lane_health": {
            "counts": lane_counts,
            "reasons": lane_reasons,
            "topk": lane_topk,
            "merged_count": int(recall_debug.get("merged_count") or 0),
            "deduped_count": int(recall_debug.get("deduped_count") or 0),
        },
        "score_profile": {
            "rerank_top1": _safe_float(rerank_scores[0] if rerank_scores else 0.0),
            "rerank_topk_avg": _safe_float(sum(rerank_scores) / len(rerank_scores) if rerank_scores else 0.0),
            "selected_top1": _safe_float(selected_scores[0] if selected_scores else 0.0),
            "selected_topk_avg": _safe_float(sum(selected_scores) / len(selected_scores) if selected_scores else 0.0),
            "rerank_score_bands": _score_band(rerank_scores),
            "selected_score_bands": _score_band(selected_scores),
        },
        "selection_profile": {
            "selected_count": len(selected_chunks or []),
            "kept_by_query_source": kept_by_query_source,
            "kept_by_chunk_source": kept_by_chunk_source,
        },
        "stability": {
            "attempt_count": len(attempts or []),
            "retry_triggered_count": retry_triggered_count,
            "retry_reasons": retry_reasons[:10],
            "final_status": final_status,
            "final_failure_reason": final_failure_reason,
        },
    }
