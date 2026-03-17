"""
检索稳定性辅助工具。

该模块聚焦两类能力：
1. 轻量重试判定（仅针对可恢复外部错误）；
2. 低相关拦截判定（最小阈值版本）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class RetrievalStabilityConfig:
    """检索稳定性参数集合，全部可通过环境变量覆盖。"""

    max_retrieve_attempts: int = max(1, int(os.getenv("RAG_RETRIEVE_MAX_ATTEMPTS", "2")))
    retry_backoff_ms: int = max(50, int(os.getenv("RAG_RETRY_BACKOFF_MS", "180")))
    retryable_reasons: frozenset[str] = frozenset(
        {"network_error", "embedding_failed", "ssl_eof", "connect_timeout", "read_timeout"}
    )
    low_rel_filter_enabled: bool = os.getenv("RAG_LOW_REL_FILTER_ENABLED", "1") != "0"
    low_rel_top1_threshold: float = float(os.getenv("RAG_LOW_REL_TOP1_THRESHOLD", "0.85"))
    low_rel_topk_avg_threshold: float = float(os.getenv("RAG_LOW_REL_TOPK_AVG_THRESHOLD", "0.68"))
    low_rel_topk: int = max(1, int(os.getenv("RAG_LOW_REL_TOPK", "3")))


STABILITY_CONFIG = RetrievalStabilityConfig()


def now_iso() -> str:
    """统一 attempt 级时间戳格式，便于追踪重试轨迹。"""
    return datetime.now().isoformat()


def flatten_lane_reasons(lane_reasons: dict) -> set[str]:
    """把 lane_reasons（字符串或数组）打平成 token 集合。"""
    tokens: set[str] = set()
    for value in (lane_reasons or {}).values():
        if isinstance(value, list):
            for item in value:
                if item:
                    tokens.add(str(item).strip().lower())
        elif value:
            tokens.add(str(value).strip().lower())
    return tokens


def is_retryable_exception(error: Exception) -> tuple[bool, str]:
    """
    判断异常是否可重试。

    仅网络/SSL/超时/embedding 类错误允许重试，业务错误不重试。
    """
    msg = str(error or "").lower()
    if any(sig in msg for sig in ("network_error", "embedding_failed", "ssl", "unexpected_eof")):
        return True, "network_or_ssl_error"
    if any(sig in msg for sig in ("connect timeout", "read timeout", "timed out", "timeout")):
        return True, "timeout_error"
    if any(sig in msg for sig in ("httpsconnectionpool", "max retries exceeded")):
        return True, "http_retryable_error"
    return False, "non_retryable_exception"


def should_retry(
    lane_reasons: dict,
    has_context: bool,
    attempt_no: int,
    low_relevance_filtered: bool,
    config: RetrievalStabilityConfig = STABILITY_CONFIG,
) -> tuple[bool, Optional[str]]:
    """
    依据当前 attempt 的召回结果判断是否继续重试。

    规则：
    - 已命中上下文：不重试；
    - 低相关拦截：不重试；
    - 未到次数上限且含可恢复错误：重试；
    - no_hit/disabled 等非可恢复场景：不重试。
    """
    if has_context:
        return False, None
    if low_relevance_filtered:
        return False, "low_relevance_filtered"
    if attempt_no >= config.max_retrieve_attempts:
        return False, "retry_exhausted"

    tokens = flatten_lane_reasons(lane_reasons)
    retryable = tokens.intersection(config.retryable_reasons)
    if retryable:
        return True, f"retryable_lane_reason:{','.join(sorted(retryable))}"
    return False, "non_retryable_lane_reason"


def calc_low_relevance(
    reranked_chunks: list[dict],
    config: RetrievalStabilityConfig = STABILITY_CONFIG,
) -> tuple[bool, str, dict]:
    """
    低相关拦截判定。

    使用 top1 与 topK 平均 final_score 作为最小阈值规则。
    """
    threshold_info = {
        "enabled": bool(config.low_rel_filter_enabled),
        "top1_threshold": float(config.low_rel_top1_threshold),
        "topk_avg_threshold": float(config.low_rel_topk_avg_threshold),
        "topk": int(config.low_rel_topk),
    }
    if not config.low_rel_filter_enabled or not reranked_chunks:
        return False, "", threshold_info

    top_chunks = reranked_chunks[: config.low_rel_topk]
    scores: list[float] = []
    for chunk in top_chunks:
        scores.append(
            float(
                chunk.get("final_score")
                or chunk.get("rerank_score")
                or chunk.get("score")
                or 0.0
            )
        )
    if not scores:
        return False, "", threshold_info

    top1 = scores[0]
    avg_topk = sum(scores) / len(scores)
    threshold_info["top1_score"] = float(top1)
    threshold_info["avg_topk_score"] = float(avg_topk)

    if top1 < config.low_rel_top1_threshold or avg_topk < config.low_rel_topk_avg_threshold:
        reason = (
            f"low_relevance_score(top1={top1:.4f},avg_top{len(scores)}={avg_topk:.4f},"
            f"threshold_top1={config.low_rel_top1_threshold:.4f},"
            f"threshold_avg={config.low_rel_topk_avg_threshold:.4f})"
        )
        return True, reason, threshold_info
    return False, "", threshold_info


def build_rerank_top(chunks: list[dict], limit: int) -> list[dict]:
    """构造 rerank TopN 调试结构。"""
    result: list[dict] = []
    for chunk in chunks[: max(1, int(limit))]:
        result.append(
            {
                "doc_id": chunk.get("doc_id"),
                "filename": chunk.get("filename"),
                "query_source": chunk.get("query_source"),
                "chunk_source": chunk.get("chunk_source"),
                "score": float(chunk.get("score") or 0.0),
                "base_score": float(chunk.get("base_score") or chunk.get("score") or 0.0),
                "bonus_score": float(chunk.get("bonus_score") or 0.0),
                "final_score": float(
                    chunk.get("final_score")
                    or chunk.get("rerank_score")
                    or chunk.get("score")
                    or 0.0
                ),
            }
        )
    return result


def build_final_chunk_debug(chunks: list[dict]) -> list[dict]:
    """构造最终返回片段调试结构。"""
    result: list[dict] = []
    for chunk in chunks:
        # 融合检索需要片段正文，调试里带上裁剪后的 chunk_text。
        chunk_text = str(chunk.get("chunk_text") or "").strip()
        result.append(
            {
                "chunk_source": chunk.get("chunk_source"),
                "query_source": chunk.get("query_source"),
                "score": float(chunk.get("score") or 0.0),
                "base_score": float(chunk.get("base_score") or chunk.get("score") or 0.0),
                "bonus_score": float(chunk.get("bonus_score") or 0.0),
                "final_score": float(
                    chunk.get("final_score")
                    or chunk.get("rerank_score")
                    or chunk.get("score")
                    or 0.0
                ),
                "doc_id": chunk.get("doc_id"),
                "filename": chunk.get("filename"),
                "doc_type": chunk.get("doc_type"),
                "kept_reason": chunk.get("kept_reason"),
                "recall_routes": chunk.get("recall_routes") or [],
                "chunk_text": chunk_text[:1200],
            }
        )
    return result
