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

    # 中文注释：默认要求生成前必须拿到 ready 的 snapshot。
    require_snapshot_ready: bool = _env_bool("RAG_GENERATION_REQUIRE_SNAPSHOT_READY", True)
    # 中文注释：最大等待时间（秒）。
    wait_timeout_sec: int = _env_int("RAG_GENERATION_SNAPSHOT_WAIT_TIMEOUT_SEC", 30, 1)
    # 中文注释：轮询间隔（毫秒）。
    poll_interval_ms: int = _env_int("RAG_GENERATION_SNAPSHOT_POLL_INTERVAL_MS", 500, 100)
    # 中文注释：超时策略，仅允许 fail_fast / fallback_rag。
    timeout_strategy: str = os.getenv(
        "RAG_GENERATION_SNAPSHOT_TIMEOUT_STRATEGY",
        "fail_fast",
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
    生成前 gate：
    1. 检查 ready；
    2. 必要时触发重建；
    3. 轮询等待；
    4. 超时后按策略 fail_fast 或 fallback_rag。
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

    timeout_sec = cfg.wait_timeout_sec
    poll_sec = cfg.poll_interval_ms / 1000.0
    last_status = first_status
    while True:
        elapsed = time.monotonic() - started
        gate_debug["snapshot_wait_elapsed_ms"] = int(elapsed * 1000)
        if elapsed >= timeout_sec:
            break

        gate_debug["snapshot_wait_poll_count"] = int(gate_debug["snapshot_wait_poll_count"]) + 1
        last_status = get_status_fn() or {}
        gate_debug["snapshot_status_after_wait"] = str(last_status.get("snapshot_status") or "unknown")
        if is_snapshot_ready_for_generation(last_status):
            gate_debug["snapshot_wait_result"] = "ready_then_proceed"
            return {"proceed": True, "error_code": "", "error_message": "", "gate_debug": gate_debug}
        time.sleep(poll_sec)

    gate_debug["snapshot_wait_timeout"] = True
    gate_debug["snapshot_status_after_wait"] = str(last_status.get("snapshot_status") or "unknown")
    if strategy == "fallback_rag":
        gate_debug["snapshot_wait_result"] = "timeout_fallback_rag"
        return {"proceed": True, "error_code": "", "error_message": "", "gate_debug": gate_debug}

    gate_debug["snapshot_wait_result"] = "timeout_fail_fast"
    return {
        "proceed": False,
        "error_code": "SNAPSHOT_NOT_READY_TIMEOUT",
        "error_message": "snapshot 等待超时，未达到可用于生成的 ready 状态。",
        "gate_debug": gate_debug,
    }

