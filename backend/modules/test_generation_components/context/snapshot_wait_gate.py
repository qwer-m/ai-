"""生成链路的 snapshot readiness 门禁工具。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable


def _env_bool(key: str, default: bool) -> bool:
    """读取布尔环境变量。"""
    value = os.getenv(key, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int, minimum: int) -> int:
    """读取整数环境变量并做下限保护。"""
    try:
        return max(minimum, int(os.getenv(key, str(default))))
    except Exception:
        return max(minimum, int(default))


@dataclass(frozen=True)
class SnapshotWaitGateConfig:
    """生成前 snapshot readiness gate 配置。"""

    # 中文注释：阶段二改造后，默认不再把 snapshot 当成硬前置。
    require_snapshot_ready: bool = _env_bool("RAG_GENERATION_REQUIRE_SNAPSHOT_READY", False)
    # 中文注释：最大等待时间（秒）。
    wait_timeout_sec: int = _env_int("RAG_GENERATION_SNAPSHOT_WAIT_TIMEOUT_SEC", 30, 1)
    # 中文注释：轮询间隔（毫秒）。
    poll_interval_ms: int = _env_int("RAG_GENERATION_SNAPSHOT_POLL_INTERVAL_MS", 500, 100)
    # 中文注释：保留历史环境变量，但主链路已统一非阻塞（仅用于调试标记）。
    timeout_strategy: str = os.getenv(
        "RAG_GENERATION_SNAPSHOT_TIMEOUT_STRATEGY",
        "fallback_rag",
    ).strip().lower()

    def normalized_timeout_strategy(self) -> str:
        """标准化策略值，避免非法配置导致行为不确定。"""
        if self.timeout_strategy in {"fail_fast", "fallback_rag"}:
            return self.timeout_strategy
        return "fail_fast"


SNAPSHOT_WAIT_GATE_CONFIG = SnapshotWaitGateConfig()


def is_snapshot_ready_for_generation(status_payload: dict) -> bool:
    """统一判定 snapshot 是否可直接用于生成。"""
    return bool(
        status_payload.get("is_ready") is True
        and status_payload.get("usable_for_generation") is True
        and status_payload.get("needs_rebuild") is False
    )


def wait_snapshot_ready_gate(
    *,
    get_status_fn: Callable[[], dict],
    enqueue_rebuild_fn: Callable[[], dict],
    status_messages: list[str] | None = None,
) -> dict:
    """
    生成前 gate（非阻塞）：
    1. 仅做 snapshot 状态检查；
    2. 必要时触发异步重建；
    3. 不做轮询等待，不阻塞当前生成请求；
    4. 未就绪时直接返回“fallback_rag”路径继续生成。
    """
    cfg = SNAPSHOT_WAIT_GATE_CONFIG
    strategy = cfg.normalized_timeout_strategy()
    started = time.monotonic()

    first_status = get_status_fn() or {}
    first_snapshot_status = str(first_status.get("snapshot_status") or "unknown")
    gate_debug = {
        "snapshot_gate_enabled": bool(cfg.require_snapshot_ready),
        "snapshot_ready_before_generation": bool(is_snapshot_ready_for_generation(first_status)),
        "snapshot_wait_attempted": False,
        "snapshot_wait_triggered_rebuild": False,
        "snapshot_wait_trigger_rebuild_reason": "",
        "snapshot_wait_poll_count": 0,
        "snapshot_wait_elapsed_ms": 0,
        "snapshot_wait_timeout": False,
        "snapshot_wait_result": "gate_disabled",
        "snapshot_status_before_generation": first_snapshot_status,
        "snapshot_status_after_wait": first_snapshot_status,
        "snapshot_wait_timeout_strategy": strategy,
        "snapshot_wait_queue_status": "none",
        "snapshot_wait_queue_reason": "none",
        "snapshot_wait_queue_error": "",
    }

    if not cfg.require_snapshot_ready:
        gate_debug["snapshot_wait_result"] = "gate_disabled_proceed"
        return {"proceed": True, "error_code": "", "error_message": "", "gate_debug": gate_debug}

    if is_snapshot_ready_for_generation(first_status):
        gate_debug["snapshot_wait_result"] = "already_ready_proceed"
        return {"proceed": True, "error_code": "", "error_message": "", "gate_debug": gate_debug}

    gate_debug["snapshot_wait_attempted"] = True
    needs_rebuild = bool(first_status.get("needs_rebuild", True))

    # 中文注释：仅在 not_exists / stale / failed 或明确需要重建时触发入队。
    if first_snapshot_status in {"not_exists", "stale", "failed"} or needs_rebuild:
        enqueue_result = enqueue_rebuild_fn() or {}
        queue_reason = str(enqueue_result.get("reason") or "unknown")
        queue_status = "queued" if bool(enqueue_result.get("queued")) else "skipped"
        gate_debug["snapshot_wait_triggered_rebuild"] = bool(enqueue_result.get("queued"))
        gate_debug["snapshot_wait_trigger_rebuild_reason"] = queue_reason
        gate_debug["snapshot_wait_queue_status"] = queue_status
        gate_debug["snapshot_wait_queue_reason"] = queue_reason
        gate_debug["snapshot_wait_queue_error"] = str(enqueue_result.get("error") or "")
        if status_messages is not None:
            status_messages.append(
                f"snapshot gate: trigger rebuild queued={enqueue_result.get('queued', False)} reason={queue_reason}"
            )
    else:
        gate_debug["snapshot_wait_trigger_rebuild_reason"] = "already_pending_or_building"
        gate_debug["snapshot_wait_queue_status"] = "skipped"
        gate_debug["snapshot_wait_queue_reason"] = "already_pending_or_building"
        if status_messages is not None:
            status_messages.append(
                f"snapshot gate: status={first_snapshot_status}，不重复入队，直接等待 ready"
            )

    # 中文注释：改造后不再轮询等待，直接按实时 RAG 回退继续执行。
    elapsed = time.monotonic() - started
    gate_debug["snapshot_wait_elapsed_ms"] = int(max(0.0, elapsed) * 1000)
    gate_debug["snapshot_wait_timeout"] = False
    gate_debug["snapshot_wait_poll_count"] = 0
    gate_debug["snapshot_wait_result"] = "no_wait_fallback_rag"
    gate_debug["snapshot_status_after_wait"] = first_snapshot_status
    return {
        "proceed": True,
        "error_code": "",
        "error_message": "",
        "gate_debug": gate_debug,
    }

