"""hybrid 生成链路的空上下文兜底策略工具。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _env_bool(key: str, default: bool) -> bool:
    """读取布尔环境变量。"""
    value = os.getenv(key, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _safe_int(value: Any, default: int) -> int:
    """容错整数解析。"""
    try:
        return int(value)
    except Exception:
        return int(default)


@dataclass(frozen=True)
class HybridEmptyGuardConfig:
    """空上下文兜底策略配置。"""

    empty_context_strategy: str = os.getenv(
        "RAG_HYBRID_EMPTY_CONTEXT_STRATEGY",
        "sync_snapshot_retry_then_fail",
    ).strip().lower()
    sync_snapshot_retry_enabled: bool = _env_bool("RAG_SYNC_SNAPSHOT_RETRY_ENABLED", True)
    sync_snapshot_retry_timeout_sec: int = max(
        2,
        _safe_int(os.getenv("RAG_SYNC_SNAPSHOT_RETRY_TIMEOUT_SEC", "8"), 8),
    )

    def normalized_strategy(self) -> str:
        """标准化策略值，仅允许两个受控分支。"""
        if self.empty_context_strategy in {"fail_fast", "sync_snapshot_retry_then_fail"}:
            return self.empty_context_strategy
        return "fail_fast"


HYBRID_EMPTY_GUARD_CONFIG = HybridEmptyGuardConfig()


def parse_snapshot_queue_info(snapshot_result: dict[str, Any] | None) -> tuple[str, str, str]:
    """
    解析 snapshot 入队状态，避免只看到笼统的 skip 文案。

    返回：
    - queue_status: queued / skipped / none
    - queue_reason: queued / already_pending / enqueue_failed / none
    - queue_error: 入队失败错误明细
    """
    payload = snapshot_result or {}
    queue_result = payload.get("queue_result") or {}
    if not queue_result:
        return "none", "none", ""
    if queue_result.get("queued"):
        return "queued", str(queue_result.get("reason") or "queued"), ""
    return "skipped", str(queue_result.get("reason") or "unknown_skip"), str(queue_result.get("error") or "")


def detect_hybrid_empty_context(
    snapshot_text: str,
    kb_context: str,
    fusion_debug: dict[str, Any] | None,
    rag_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """统一判定是否处于 snapshot 空 + RAG 空的危险边界。"""
    fusion_debug = fusion_debug or {}
    rag_debug = (rag_payload or {}).get("debug") if isinstance(rag_payload, dict) else {}
    rag_debug = rag_debug or {}

    snapshot_empty = not (snapshot_text or "").strip()
    rag_chunk_count = _safe_int(fusion_debug.get("rag_chunk_count"), 0)
    if rag_chunk_count <= 0:
        final_chunks = rag_debug.get("final_chunks") or []
        rag_chunk_count = len(final_chunks)

    fusion_mode = str(fusion_debug.get("fusion_mode") or "").strip().lower()
    context_empty = not (kb_context or "").strip()

    # 中文注释：满足“snapshot 空 + rag 空 + 上下文空/融合模式 empty”即判定为空上下文风险。
    hybrid_empty_context = bool(
        snapshot_empty and rag_chunk_count == 0 and (context_empty or fusion_mode == "empty")
    )

    lane_counts = rag_debug.get("lane_counts") or {}
    lane_reasons = rag_debug.get("lane_reasons") or {}
    final_failure_reason = rag_debug.get("final_failure_reason") or fusion_debug.get("rag_error") or ""
    if not final_failure_reason and hybrid_empty_context:
        final_failure_reason = "snapshot_and_rag_both_empty"

    return {
        "snapshot_empty": snapshot_empty,
        "rag_chunk_count": rag_chunk_count,
        "fusion_mode": fusion_mode or "empty",
        "context_empty": context_empty,
        "hybrid_empty_context": hybrid_empty_context,
        "lane_counts": lane_counts,
        "lane_reasons": lane_reasons,
        "final_empty_reason": str(final_failure_reason or ""),
    }
