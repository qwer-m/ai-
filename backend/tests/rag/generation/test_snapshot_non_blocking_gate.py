from dataclasses import dataclass

import modules.testing.test_generation_components.context.snapshot_wait_gate as gate_mod


@dataclass(frozen=True)
class _Cfg:
    """中文注释：测试用 gate 配置桩，固定启用 gate 并验证非阻塞行为。"""

    require_snapshot_ready: bool = True
    wait_timeout_sec: int = 30
    poll_interval_ms: int = 500
    timeout_strategy: str = "fail_fast"

    def normalized_timeout_strategy(self) -> str:
        return self.timeout_strategy


def test_gate_ready_direct_proceed(monkeypatch):
    """场景1：snapshot ready，直接使用快照并继续。"""
    monkeypatch.setattr(gate_mod, "SNAPSHOT_WAIT_GATE_CONFIG", _Cfg())

    result = gate_mod.wait_snapshot_ready_gate(
        get_status_fn=lambda: {
            "snapshot_status": "success",
            "is_ready": True,
            "usable_for_generation": True,
            "needs_rebuild": False,
        },
        enqueue_rebuild_fn=lambda: {"queued": False, "reason": "no_need"},
        status_messages=[],
    )

    assert result["proceed"] is True
    assert result["error_code"] == ""
    assert result["gate_debug"]["snapshot_wait_result"] == "already_ready_proceed"


def test_gate_pending_no_wait_and_enqueue(monkeypatch):
    """场景2：snapshot pending，不等待，直接 fallback，并触发后台重建。"""
    monkeypatch.setattr(gate_mod, "SNAPSHOT_WAIT_GATE_CONFIG", _Cfg())
    enqueue_calls = {"n": 0}

    def _enqueue():
        enqueue_calls["n"] += 1
        return {"queued": True, "reason": "queued", "task_id": "task-1"}

    result = gate_mod.wait_snapshot_ready_gate(
        get_status_fn=lambda: {
            "snapshot_status": "pending",
            "is_ready": False,
            "usable_for_generation": False,
            "needs_rebuild": True,
        },
        enqueue_rebuild_fn=_enqueue,
        status_messages=[],
    )

    assert enqueue_calls["n"] == 1
    assert result["proceed"] is True
    assert result["error_code"] == ""
    assert result["gate_debug"]["snapshot_wait_result"] == "no_wait_fallback_rag"
    assert result["gate_debug"]["snapshot_wait_poll_count"] == 0
    assert result["gate_debug"]["snapshot_wait_queue_status"] == "queued"


def test_gate_failed_never_timeout_fail_fast(monkeypatch):
    """场景3：snapshot failed，也不能导致 SNAPSHOT_NOT_READY_TIMEOUT。"""
    monkeypatch.setattr(gate_mod, "SNAPSHOT_WAIT_GATE_CONFIG", _Cfg(timeout_strategy="fail_fast"))

    result = gate_mod.wait_snapshot_ready_gate(
        get_status_fn=lambda: {
            "snapshot_status": "failed",
            "is_ready": False,
            "usable_for_generation": False,
            "needs_rebuild": True,
        },
        enqueue_rebuild_fn=lambda: {"queued": False, "reason": "already_pending"},
        status_messages=[],
    )

    assert result["proceed"] is True
    assert result["error_code"] == ""
    assert result["gate_debug"]["snapshot_wait_result"] in {"no_wait_fallback_rag", "forced_non_blocking_fallback"}
