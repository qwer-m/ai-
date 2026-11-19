"""Run62真实已验收前沿的CPU重演；不恢复旧Run、不重写其失败记录或额度。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.retry_policy import RetryAttemptState


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).with_name("fixtures") / "run62_transient_progress_real.json.gz"
    value = json.loads(gzip.decompress(path.read_bytes()))
    assert action_state_hash({key: item for key, item in value.items() if key != "data_sha256"}) == value["data_sha256"]
    assert value["provenance"]["run_id"] == 62
    assert [frame["validated_call_count"] for frame in value["frontiers"]] == [24, 43, 66, 66]
    for frame in value["frontiers"]:
        assert action_state_hash({"state_hash": frame["state_hash"], "validated_calls": frame["validated_calls"]}) == frame["progress_token"]
    return value


def failure(state, *, strict=False):
    state.attempt += 1
    return state.record_failure(failure_kind="rate_limit", retryable=True, is_content_error=False,
        configured_max_attempts=1, stop_on_failure=strict)


def test_new_policy_replays_actual_four_frontiers_without_losing_cumulative_history_or_budget(real):
    before = action_state_hash(real)
    budgets = deepcopy([frame["budget"] for frame in real["frontiers"]])
    budget_hash = action_state_hash(budgets)
    state = RetryAttemptState()
    local, cumulative, can_retry = [], [], []
    observed_progress = []
    # 从新策略初态重演真实前沿，不把旧Run62控制元数据伪装成新协议检查点。
    for frame in real["frontiers"]:
        observed_progress.append(state.observe_progress(frame["progress_token"]))
        decision = failure(state)
        local.append(state.transient_failure_streak)
        cumulative.append(state.transient_failure_count)
        can_retry.append(decision.can_retry)
        state = RetryAttemptState.restore({"item_attempt": state.attempt, **state.checkpoint()})
    assert observed_progress == [False, True, True, False]
    assert local == [1, 1, 1, 2]
    assert cumulative == [1, 2, 3, 4]
    assert can_retry == [True, True, True, True]
    assert state.route_health_failure_count == 4
    assert not state.retry_exhausted
    assert [frame["historical_retry_state"]["retry_exhausted"] for frame in real["frontiers"]] == [False, False, False, True]
    assert action_state_hash(budgets) == budget_hash
    assert budgets == [frame["budget"] for frame in real["frontiers"]]
    assert action_state_hash(real) == before


def test_four_failures_without_new_accepted_progress_still_stop(real):
    state = RetryAttemptState()
    token = real["frontiers"][-1]["progress_token"]
    assert not state.observe_progress(token)
    decisions = []
    for _ in range(4):
        assert not state.observe_progress(token)
        decisions.append(failure(state).can_retry)
        state = RetryAttemptState.restore({"item_attempt": state.attempt, **state.checkpoint()})
    assert decisions == [True, True, True, False]
    assert state.transient_failure_count == state.transient_failure_streak == 4
    with pytest.raises(RuntimeError, match="耗尽重试预算"):
        state.require_retry_budget(configured_max_attempts=1)


def test_progress_cannot_clear_exhaustion_content_routehealth_or_capability_state(real):
    first, next_ = (frame["progress_token"] for frame in real["frontiers"][:2])
    state = RetryAttemptState(attempt=4, content_failure_counts={"output_validation": 1},
        transient_failure_count=3, transient_failure_streak=3, transient_progress_token=first,
        route_health_failure_count=7, capability_fallback_count=1, server_output_schema_disabled=True,
        last_failure_kind="output_validation", retry_exhausted=True)
    before = state.checkpoint()
    assert state.observe_progress(next_)
    after = state.checkpoint()
    assert after["transient_failure_streak"] == 0
    assert after["transient_progress_token"] == next_
    assert {key: value for key, value in after.items() if key not in {"transient_failure_streak", "transient_progress_token"}} == {
        key: value for key, value in before.items() if key not in {"transient_failure_streak", "transient_progress_token"}}
    with pytest.raises(RuntimeError, match="耗尽重试预算"):
        state.require_retry_budget(configured_max_attempts=1)


def test_first_progress_stamp_does_not_refund_existing_transport_attempts(real):
    state = RetryAttemptState()
    for _ in range(3):
        assert failure(state).can_retry
    assert not state.observe_progress(real["frontiers"][0]["progress_token"])
    assert state.transient_failure_count == state.transient_failure_streak == 3
    assert not failure(state).can_retry
    assert state.retry_exhausted


def test_strict_execution_still_stops_at_first_failure_even_with_prior_progress(real):
    state = RetryAttemptState()
    state.observe_progress(real["frontiers"][0]["progress_token"])
    state.observe_progress(real["frontiers"][1]["progress_token"])
    assert not failure(state, strict=True).can_retry
    assert state.transient_failure_count == state.transient_failure_streak == 1
    assert state.retry_exhausted
    assert state.observe_progress(real["frontiers"][2]["progress_token"])
    with pytest.raises(RuntimeError, match="耗尽重试预算"):
        state.require_retry_budget(configured_max_attempts=1)


def test_reordered_frontier_proof_and_empty_fork_are_the_same_progress(real):
    first, fork = real["frontiers"][2:]
    assert first["state_hash"] == fork["state_hash"]
    assert first["validated_calls"] == fork["validated_calls"]
    assert first["node_id"] != fork["node_id"]
    reordered = dict(reversed(list(first["validated_calls"].items())))
    token = action_state_hash({"validated_calls": reordered, "state_hash": first["state_hash"]})
    assert token == first["progress_token"] == fork["progress_token"]
    state = RetryAttemptState()
    state.observe_progress(token)
    failure(state)
    assert not state.observe_progress(fork["progress_token"])
    assert state.transient_failure_streak == 1
