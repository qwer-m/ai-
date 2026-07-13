"""snapshot + 轻量 RAG 融合上下文构建器。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _approx_tokens(text: str) -> int:
    """轻量 token 估算：按字符近似，避免引入重依赖。"""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _trim_by_tokens(text: str, max_tokens: int) -> str:
    """按近似 token 截断文本。"""
    if not text:
        return ""
    max_tokens = max(0, int(max_tokens))
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _env_bool(key: str, default: bool) -> bool:
    """环境变量布尔读取。"""
    value = os.getenv(key, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


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


@dataclass(frozen=True)
class HybridContextConfig:
    """融合上下文预算配置。"""

    snapshot_max_tokens: int = _env_int("RAG_HYBRID_SNAPSHOT_MAX_TOKENS", 2000, 600)
    rag_max_tokens: int = _env_int("RAG_HYBRID_RAG_MAX_TOKENS", 1000, 300)
    total_max_tokens: int = _env_int("RAG_HYBRID_TOTAL_MAX_TOKENS", 3200, 1000)
    rag_top_k: int = _env_int("RAG_HYBRID_RAG_TOP_K", 4, 3, 5)
    snapshot_insufficient_tokens: int = _env_int("RAG_HYBRID_SNAPSHOT_MIN_TOKENS", 800, 200)
    default_precision_mode: bool = _env_bool("RAG_HYBRID_DEFAULT_PRECISION_MODE", True)


HYBRID_CONFIG = HybridContextConfig()
_PRECISION_KEYWORDS = ("规则", "条件", "权限", "字段", "流程", "异常", "余额", "账单")


def should_use_rag(
    question: str,
    mode: str = "test_case_generation",
    snapshot_length: int = 0,
    precision_mode: bool = False,
    config: HybridContextConfig = HYBRID_CONFIG,
) -> tuple[bool, list[str]]:
    """
    判断是否启用 RAG 精度增强。

    触发条件：
    1. 显式 precision_mode=true；
    2. 任务模式是测试用例生成（默认开启）；
    3. 问题命中精度关键词；
    4. snapshot 内容过短（信息不足）。
    """
    reasons: list[str] = []
    q = (question or "").strip()
    if precision_mode:
        reasons.append("explicit_precision_mode")
    if mode == "test_case_generation" and config.default_precision_mode:
        reasons.append("generation_default_precision")
    if q and any(k in q for k in _PRECISION_KEYWORDS):
        reasons.append("keyword_triggered")
    if int(snapshot_length or 0) < config.snapshot_insufficient_tokens:
        reasons.append("snapshot_insufficient")
    return bool(reasons), reasons


def _collect_rag_chunks(rag_payload: dict[str, Any], rag_top_k: int, rag_max_tokens: int) -> tuple[list[dict], str]:
    """从 RAG debug 结果中提取可融合片段。"""
    if not isinstance(rag_payload, dict):
        return [], "rag_payload_invalid"
    rag_debug = rag_payload.get("debug") or {}
    final_chunks = rag_debug.get("final_chunks") or []
    if not final_chunks:
        return [], str(rag_debug.get("final_failure_reason") or "rag_no_chunk")

    rag_top_k = max(3, min(5, int(rag_top_k)))
    selected: list[dict] = []
    remaining_tokens = max(1, int(rag_max_tokens))
    for chunk in final_chunks:
        text = str(chunk.get("chunk_text") or "").strip()
        if not text:
            continue
        if len(selected) >= rag_top_k or remaining_tokens <= 0:
            break
        trimmed = _trim_by_tokens(text, remaining_tokens)
        token_cost = _approx_tokens(trimmed)
        if not trimmed or token_cost <= 0:
            continue
        remaining_tokens -= token_cost
        selected.append(
            {
                "filename": chunk.get("filename") or "Unknown",
                "doc_id": chunk.get("doc_id"),
                "final_score": float(chunk.get("final_score") or chunk.get("score") or 0.0),
                "chunk_text": trimmed,
            }
        )
    return selected, ""


def build_hybrid_context(
    question: str,
    snapshot_text: str,
    rag_payload: dict[str, Any] | None = None,
    mode: str = "test_case_generation",
    precision_mode: bool = False,
    config: HybridContextConfig = HYBRID_CONFIG,
) -> dict:
    """
    构建“snapshot 背景 + RAG 证据”的融合上下文。

    返回包含：
    - context（最终上下文）
    - debug（融合调试信息）
    """
    snapshot_part = _trim_by_tokens((snapshot_text or "").strip(), config.snapshot_max_tokens)
    snapshot_tokens = _approx_tokens(snapshot_part)
    use_rag, precision_reasons = should_use_rag(
        question=question,
        mode=mode,
        snapshot_length=snapshot_tokens,
        precision_mode=precision_mode,
        config=config,
    )

    rag_chunks: list[dict] = []
    rag_error = ""
    if use_rag and rag_payload:
        rag_chunks, rag_error = _collect_rag_chunks(
            rag_payload=rag_payload,
            rag_top_k=config.rag_top_k,
            rag_max_tokens=config.rag_max_tokens,
        )

    rag_lines: list[str] = []
    rag_top_scores: list[float] = []
    for idx, chunk in enumerate(rag_chunks, 1):
        rag_top_scores.append(float(chunk.get("final_score") or 0.0))
        rag_lines.append(f"{idx}. 文档{chunk.get('filename')}:\n{chunk.get('chunk_text')}")
    rag_part = "\n\n".join(rag_lines).strip()

    # 结构化拼接：先全局背景，再局部证据。
    context_blocks: list[str] = []
    if snapshot_part:
        context_blocks.append(f"【项目知识背景（snapshot）】\n{snapshot_part}")
    if rag_part:
        context_blocks.append(f"【当前问题相关知识片段（RAG）】\n{rag_part}")
    context_text = "\n\n".join(context_blocks).strip()

    # 二次总预算控制：优先保留 RAG 精确片段，再裁剪 snapshot。
    total_tokens = _approx_tokens(context_text)
    if total_tokens > config.total_max_tokens and snapshot_part:
        rag_section_tokens = _approx_tokens(f"【当前问题相关知识片段（RAG）】\n{rag_part}") if rag_part else 0
        keep_snapshot_tokens = max(200, config.total_max_tokens - rag_section_tokens - 30)
        snapshot_part = _trim_by_tokens(snapshot_part, keep_snapshot_tokens)
        context_blocks = [f"【项目知识背景（snapshot）】\n{snapshot_part}"] if snapshot_part else []
        if rag_part:
            context_blocks.append(f"【当前问题相关知识片段（RAG）】\n{rag_part}")
        context_text = "\n\n".join(context_blocks).strip()
        total_tokens = _approx_tokens(context_text)

    snapshot_used = bool(snapshot_part)
    rag_used = bool(rag_part)
    if snapshot_used and rag_used:
        fusion_mode = "snapshot+rag"
    elif snapshot_used:
        fusion_mode = "snapshot_only"
    elif rag_used:
        fusion_mode = "rag_only"
    else:
        fusion_mode = "empty"

    return {
        "context": context_text,
        "debug": {
            "snapshot_used": snapshot_used,
            "rag_used": rag_used,
            "rag_chunk_count": len(rag_chunks),
            "rag_top_scores": rag_top_scores,
            "fusion_mode": fusion_mode,
            "final_context_tokens": total_tokens,
            "precision_boost": use_rag,
            "precision_reasons": precision_reasons,
            "rag_error": rag_error,
            "token_budget": {
                "snapshot_max_tokens": config.snapshot_max_tokens,
                "rag_max_tokens": config.rag_max_tokens,
                "total_max_tokens": config.total_max_tokens,
            },
        },
    }
