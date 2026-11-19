"""真实 Run60 限流与 Run59 成功回执驱动并发策略，不调用或替代模型。"""

import asyncio
import gzip
import json
from pathlib import Path

import pytest

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.retry_policy import AdaptiveConcurrency
from modules.agent_platform.sdk_adapter import _ManagedSessionConcurrency


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).with_name("fixtures") / "run60_concurrency_signals_real.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    expected = value.pop("data_sha256")
    assert action_state_hash(value) == expected
    assert value["provenance"]["failed_run_id"] == 60
    assert value["provenance"]["successful_run_id"] == 59
    return value


def policy(checkpoint=None, *, strict=False):
    return AdaptiveConcurrency.restore(max_concurrency=1 if strict else 6,
        min_concurrency=1 if strict else 2, pressure_threshold=2,
        recovery_successes=6, checkpoint=checkpoint)


def test_actual_rate_limit_signals_lower_same_model_scope_across_actions(real):
    routes = {}
    changes = []
    for signal in real["rate_limit_signals"]:
        current = routes.setdefault(signal["scope_key"], policy())
        adjustment = current.record_failure(signal["failure_kind"])
        if adjustment:
            changes.append({"node_id": signal["node_id"], "sequence": signal["sequence"], **adjustment})
    assert len(real["rate_limit_signals"]) == 5
    assert sum(item["reason"] == "upstream_pressure" for item in changes) == 2
    assert all(item["from"] == 6 and item["to"] == 5 for item in changes)
    assert len(routes) == 2
    assert all(current.effective_concurrency == 5 for current in routes.values())


def test_json_restoration_preserves_actual_pressure_across_each_event(real):
    uninterrupted = policy()
    restored = policy()
    for signal in real["rate_limit_signals"]:
        assert restored.record_failure(signal["failure_kind"]) == uninterrupted.record_failure(signal["failure_kind"])
        restored = policy(json.loads(json.dumps(restored.snapshot())))
        assert restored.snapshot() == uninterrupted.snapshot()


def test_actual_interleaved_successes_do_not_erase_repeat_rate_limits(real):
    routes = {}
    adjustments = []
    for signal in real["interleaved_session_signals"]:
        current = routes.setdefault(signal["scope_key"], policy())
        adjustment = current.record_success() if signal["status"] == "validated" else current.record_failure(signal["failure_kind"])
        if adjustment:
            adjustments.append({"node_id": signal["node_id"], "source_sequence": signal["source_sequence"], **adjustment})
    down = [item for item in adjustments if item["reason"] == "upstream_pressure"]
    assert down == [{"node_id": 405, "source_sequence": 18, "from": 6, "to": 5,
                     "reason": "upstream_pressure", "failure_kind": "rate_limit"}]
    assert any(item["reason"] == "success_recovery" and item["from"] == 5 and item["to"] == 6 for item in adjustments)


def test_real_interleaved_control_survives_json_storage_at_each_step(real):
    uninterrupted = {}
    restored = {}
    for signal in real["interleaved_session_signals"]:
        key = signal["scope_key"]
        left = uninterrupted.setdefault(key, policy())
        right = restored.setdefault(key, policy())
        if signal["status"] == "validated":
            assert left.record_success() == right.record_success()
        else:
            assert left.record_failure(signal["failure_kind"]) == right.record_failure(signal["failure_kind"])
        restored[key] = policy(json.loads(json.dumps(right.snapshot())))
        assert restored[key].snapshot() == left.snapshot()


def test_real_successes_without_pressure_keep_original_configured_concurrency(real):
    current = policy()
    assert len(real["validated_success_signals"]) > 6
    for signal in real["validated_success_signals"]:
        assert signal["model_requests"] >= 1
        assert current.record_success() is None
        assert current.effective_concurrency == 6


def test_real_successful_sessions_restore_one_level_after_actual_pressure(real):
    current = policy()
    for signal in real["rate_limit_signals"][:2]:
        current.record_failure(signal["failure_kind"])
    assert current.effective_concurrency == 5
    for signal in real["validated_success_signals"][:5]:
        assert current.record_success() is None
    adjustment = current.record_success()
    assert adjustment == {"from": 5, "to": 6, "reason": "success_recovery", "failure_kind": ""}


def test_strict_execution_keeps_one_permit_for_all_actual_failure_signals(real):
    current = policy(strict=True)
    for signal in real["rate_limit_signals"]:
        assert current.record_failure(signal["failure_kind"]) is None
        assert current.effective_concurrency == 1


def test_cancelled_unknown_requests_are_not_concurrency_pressure(real):
    current = policy()
    before = current.snapshot()
    assert len(real["cancelled_unknown_signals"]) == 4
    for signal in real["cancelled_unknown_signals"]:
        assert signal["error_type"] == "CancelledError"
        assert current.record_failure(None) is None
    assert current.snapshot() == before


def test_gate_bounds_new_sessions_using_replayed_actual_pressure_policy(real):
    async def exercise():
        current = policy()
        for signal in real["rate_limit_signals"][:2]:
            current.record_failure(signal["failure_kind"])
        assert current.effective_concurrency == 5
        changes = []
        gate = _ManagedSessionConcurrency(policy=current, on_change=lambda snapshot, adjustment: changes.append((snapshot, adjustment)))
        release = asyncio.Event()
        all_admitted = asyncio.Event()
        active = peak = 0
        seen = []

        async def session(signal):
            nonlocal active, peak
            async with gate.session():
                active += 1
                peak = max(peak, active)
                seen.append(signal["input_hash"])
                if len(seen) == 5:
                    all_admitted.set()
                await release.wait()
                active -= 1

        tasks = [asyncio.create_task(session(signal)) for signal in real["validated_success_signals"][:6]]
        await asyncio.wait_for(all_admitted.wait(), timeout=2)
        await asyncio.sleep(0)
        assert len(seen) == 5 and peak == 5
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        assert len(seen) == 6 and peak == 5 and gate._active == 0
        assert len(changes) == 6

    asyncio.run(exercise())
