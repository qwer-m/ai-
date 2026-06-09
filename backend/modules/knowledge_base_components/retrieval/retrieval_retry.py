"""
检索稳定性辅助工具。
该模块聚焦两类能力：
1. 轻量重试判定（仅针对可恢复外部错误）；
2. 低相关拦截判定（最小阈值版本）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


def _env_enabled(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _env_int(key: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = os.getenv(key)
    try:
        value = int(str(raw if raw is not None else default).strip())
    except (TypeError, ValueError):
        value = int(default)
    value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def _env_float(key: str, default: float, minimum: float, maximum: float | None = None) -> float:
    raw = os.getenv(key)
    try:
        value = float(str(raw if raw is not None else default).strip())
    except (TypeError, ValueError):
        value = float(default)
    value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return value


@dataclass(frozen=True)
class RetrievalStabilityConfig:
    """检索稳定性参数集合，全部可通过环境变量覆盖。"""

    max_retrieve_attempts: int = _env_int("RAG_RETRIEVE_MAX_ATTEMPTS", 2, 1)
    retry_backoff_ms: int = _env_int("RAG_RETRY_BACKOFF_MS", 180, 50)
    retryable_reasons: frozenset[str] = frozenset(
        {"network_error", "embedding_failed", "ssl_eof", "connect_timeout", "read_timeout"}
    )
    low_rel_filter_enabled: bool = _env_enabled("RAG_LOW_REL_FILTER_ENABLED", True)
    low_rel_top1_threshold: float = _env_float("RAG_LOW_REL_TOP1_THRESHOLD", 0.85, 0.0, 1.0)
    low_rel_topk_avg_threshold: float = _env_float("RAG_LOW_REL_TOPK_AVG_THRESHOLD", 0.68, 0.0, 1.0)
    low_rel_topk: int = _env_int("RAG_LOW_REL_TOPK", 3, 1)


STABILITY_CONFIG = RetrievalStabilityConfig()


def now_iso() -> str:
    """统一 attempt 级时间戳格式，便于追踪重试轨迹。"""
    return datetime.now().isoformat()


def flatten_lane_reasons(lane_reasons: dict) -> set[str]:
    """把 lane_reasons（字符串或数组）打平为 token 集合。"""
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

    def _effective_score(chunk: dict) -> float:
        # 中文注释：低相关判定优先看融合/重排后的综合分，避免语义分块后仅看向量分导致误杀。
        for key in ("final_score", "fusion_score", "rerank_score", "score", "vector_score"):
            value = chunk.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except Exception:
                continue
        return 0.0

    def _has_strong_lexical_hit(chunk: dict) -> bool:
        try:
            title_score = float(chunk.get("title_score") or 0.0)
        except Exception:
            title_score = 0.0
        try:
            keyword_score = float(chunk.get("keyword_score") or 0.0)
        except Exception:
            keyword_score = 0.0

        title_terms = list(chunk.get("title_hit_terms") or [])
        content_terms = list(chunk.get("content_hit_terms") or [])
        lexical_strength = (keyword_score * 0.65) + (title_score * 0.35)
        term_hit_count = len({str(x).strip().lower() for x in (title_terms + content_terms) if str(x).strip()})

        # 中文注释：命中词强时放宽阈值，避免“分块后分数偏移”把有效候选直接打掉。
        if lexical_strength >= 0.55:
            return True
        if lexical_strength >= 0.35 and term_hit_count >= 2:
            return True

        text = str(chunk.get("chunk_text") or "")
        if text:
            token_hits = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_]{1,}", text)
            if term_hit_count >= 1 and len(token_hits) >= 6:
                return True
        return False

    for chunk in top_chunks:
        scores.append(_effective_score(chunk))
    if not scores:
        return False, "", threshold_info

    lexical_relaxed = any(_has_strong_lexical_hit(chunk) for chunk in top_chunks)
    relaxed_top1 = float(config.low_rel_top1_threshold)
    relaxed_avg = float(config.low_rel_topk_avg_threshold)
    if lexical_relaxed:
        relaxed_top1 = max(0.0, relaxed_top1 - 0.20)
        relaxed_avg = max(0.0, relaxed_avg - 0.16)

    top1 = scores[0]
    avg_topk = sum(scores) / len(scores)
    threshold_info["top1_score"] = float(top1)
    threshold_info["avg_topk_score"] = float(avg_topk)
    threshold_info["effective_top1_threshold"] = float(relaxed_top1)
    threshold_info["effective_topk_avg_threshold"] = float(relaxed_avg)
    threshold_info["title_keyword_relaxed"] = bool(lexical_relaxed)

    if top1 < relaxed_top1 or avg_topk < relaxed_avg:
        reason = (
            f"low_relevance_score(top1={top1:.4f},avg_top{len(scores)}={avg_topk:.4f},"
            f"threshold_top1={relaxed_top1:.4f},"
            f"threshold_avg={relaxed_avg:.4f},"
            f"lexical_relaxed={int(lexical_relaxed)})"
        )
        return True, reason, threshold_info
    return False, "", threshold_info


def build_rerank_top(chunks: list[dict], limit: int) -> list[dict]:
    """构造 rerank TopN 调试结构。"""
    result: list[dict] = []
    for chunk in chunks[: max(1, int(limit))]:
        chunk_text = str(chunk.get("chunk_text") or "").strip()
        result.append(
            {
                "chunk_id": chunk.get("chunk_id"),
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
                "vector_score": float(chunk.get("vector_score") or chunk.get("score") or 0.0),
                "keyword_score": float(chunk.get("keyword_score") or 0.0),
                "title_score": float(chunk.get("title_score") or 0.0),
                "fusion_score": float(chunk.get("fusion_score") or chunk.get("final_score") or chunk.get("score") or 0.0),
                "title_hit_terms": chunk.get("title_hit_terms") or [],
                "content_hit_terms": chunk.get("content_hit_terms") or [],
                "selection_reason": chunk.get("selection_reason"),
                "chunk_text": chunk_text[:1200],
            }
        )
    return result


def build_final_chunk_debug(chunks: list[dict]) -> list[dict]:
    """构造最终返回片段调试结构。"""
    result: list[dict] = []
    for chunk in chunks:
        # 中文注释：融合检索调试里保留片段正文，便于前端直接预览。
        chunk_text = str(chunk.get("chunk_text") or "").strip()
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        result.append(
            {
                "chunk_id": chunk.get("chunk_id"),
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
                # 中文注释：透传业务域元数据，供上层提示词做 biz_key/module 约束。
                "biz_key": chunk.get("biz_key"),
                "module": chunk.get("module") or metadata.get("module"),
                "kept_reason": chunk.get("kept_reason"),
                "selection_reason": chunk.get("selection_reason"),
                "recall_routes": chunk.get("recall_routes") or [],
                "vector_score": float(chunk.get("vector_score") or chunk.get("score") or 0.0),
                "keyword_score": float(chunk.get("keyword_score") or 0.0),
                "title_score": float(chunk.get("title_score") or 0.0),
                "fusion_score": float(chunk.get("fusion_score") or chunk.get("final_score") or chunk.get("score") or 0.0),
                "title_hit_terms": chunk.get("title_hit_terms") or [],
                "content_hit_terms": chunk.get("content_hit_terms") or [],
                "chunk_text": chunk_text[:1200],
            }
        )
    return result
