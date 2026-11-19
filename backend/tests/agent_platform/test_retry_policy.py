"""直接验证重试策略，不调用模型或构造业务数据。"""

from modules.agent_platform.retry_policy import RetryAttemptState, RetryDecision
import pytest


@pytest.mark.parametrize("failure_kind,is_content", [
    ("timeout", False), ("rate_limit", False), ("connection", False),
    ("json_syntax", True), ("output_degeneration", True),
    ("tool_arguments_validation", True), ("output_validation", True),
    ("server_schema_unsupported", False),
])
def test_strict_run_stops_first_failure_without_capability_or_content_retry(failure_kind, is_content):
    state = RetryAttemptState(attempt=1)
    decision = state.record_failure(
        failure_kind=failure_kind, retryable=True, is_content_error=is_content,
        configured_max_attempts=5, stop_on_failure=True,
    )
    assert decision.retryable and not decision.can_retry
    assert decision.attempt_limit == 1
    assert not decision.capability_changed
    assert not state.server_output_schema_disabled
    assert state.retry_exhausted
    with pytest.raises(RuntimeError, match="耗尽重试预算"):
        state.require_retry_budget(configured_max_attempts=5)


def test_content_budget_survives_checkpoint_restore() -> None:
    state = RetryAttemptState()
    state.attempt = 1
    first = state.record_failure(
        failure_kind="output_validation", retryable=True,
        is_content_error=True, configured_max_attempts=2,
    )
    assert first.can_retry
    restored = RetryAttemptState.restore({"item_attempt": state.attempt, **state.checkpoint()})
    restored.attempt += 1
    second = restored.record_failure(
        failure_kind="output_validation", retryable=True,
        is_content_error=True, configured_max_attempts=2,
    )
    assert restored.attempt == 2
    assert restored.content_failure_counts == {"output_validation": 2}
    assert not second.can_retry
    assert second.attempt_limit == 2


def test_failure_categories_have_independent_budgets() -> None:
    state = RetryAttemptState()
    decisions = []
    for failure_kind, is_content in [
        ("timeout", False), ("output_validation", True),
        ("postprocess_validation", True), ("output_validation", True),
    ]:
        state.attempt += 1
        decisions.append(state.record_failure(
            failure_kind=failure_kind, retryable=True,
            is_content_error=is_content, configured_max_attempts=2,
        ))
    assert [decision.can_retry for decision in decisions] == [True, True, True, False]
    assert state.transient_failure_count == 1
    assert state.content_failure_counts == {"output_validation": 2, "postprocess_validation": 1}


def test_capability_adjustment_survives_resume_and_is_not_content_failure() -> None:
    state = RetryAttemptState(attempt=1)
    first = state.record_failure(
        failure_kind="server_schema_unsupported", retryable=True,
        is_content_error=False, configured_max_attempts=1,
    )
    assert first.can_retry
    assert first.capability_changed
    restored = RetryAttemptState.restore({"item_attempt": 1, **state.checkpoint()})
    restored.attempt += 1
    second = restored.record_failure(
        failure_kind="server_schema_unsupported", retryable=True,
        is_content_error=False, configured_max_attempts=1,
    )
    assert not second.can_retry
    assert restored.server_output_schema_disabled
    assert restored.capability_fallback_count == 1
    assert restored.transient_failure_count == 0
    assert restored.content_failure_counts == {}


def test_empty_output_preserves_reasoning_and_uses_content_budget() -> None:
    state = RetryAttemptState(attempt=1)
    decision = state.record_failure(
        failure_kind="empty_output", retryable=True,
        is_content_error=True, configured_max_attempts=1,
    )
    assert not decision.can_retry
    assert not decision.capability_changed
    assert state.content_failure_counts == {"empty_output": 1}
    assert "model_thinking_disabled" not in state.checkpoint()


def test_repeated_invalid_output_escalation_respects_remaining_budget() -> None:
    available = RetryDecision("postprocess_validation", True, True, 3)
    exhausted = RetryDecision("postprocess_validation", True, False, 2)
    assert available.repeated_output_action("minimal_patch") == "full_regeneration"
    assert available.repeated_output_action("full_regeneration") == "stop"
    assert exhausted.repeated_output_action("minimal_patch") == "stop"


def test_structural_errors_retain_three_independent_attempts() -> None:
    state = RetryAttemptState()
    decisions = []
    for _ in range(3):
        state.attempt += 1
        decisions.append(state.record_failure(
            failure_kind="output_degeneration", retryable=True,
            is_content_error=True, configured_max_attempts=1,
        ))
    assert [decision.can_retry for decision in decisions] == [True, True, False]


def test_exhausted_checkpoint_cannot_gain_budget_by_resuming() -> None:
    state = RetryAttemptState(attempt=1)
    state.record_failure(
        failure_kind="postprocess_validation", retryable=True,
        is_content_error=True, configured_max_attempts=1,
    )
    restored = RetryAttemptState.restore(state.checkpoint())
    with pytest.raises(RuntimeError, match="续跑保留原预算"):
        restored.require_retry_budget(configured_max_attempts=1)
    RetryAttemptState().require_retry_budget(configured_max_attempts=1)


def test_checkpoint_without_route_health_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="缺少 route_health_failure_count"):
        RetryAttemptState.restore({
            "failure_kind": "output_validation",
            "content_failure_counts": {"output_validation": 2},
        })


@pytest.mark.parametrize("field", ["transient_failure_streak", "transient_progress_token"])
def test_current_checkpoint_requires_explicit_transient_progress_fields(field):
    checkpoint = RetryAttemptState().checkpoint()
    checkpoint.pop(field)
    with pytest.raises(ValueError, match="缺少当前暂态进度字段"):
        RetryAttemptState.restore(checkpoint)


@pytest.mark.parametrize("streak", [True, -1, 1.0, "1", 2])
def test_current_checkpoint_rejects_invalid_local_failure_count(streak):
    checkpoint = RetryAttemptState(transient_failure_count=1).checkpoint()
    checkpoint["transient_failure_streak"] = streak
    with pytest.raises(ValueError, match="局部失败次数"):
        RetryAttemptState.restore(checkpoint)


@pytest.mark.parametrize("token", [True, 1, "", "a" * 63, "A" * 64, "g" * 64])
def test_current_checkpoint_rejects_invalid_progress_digest(token):
    checkpoint = RetryAttemptState().checkpoint()
    checkpoint["transient_progress_token"] = token
    with pytest.raises(ValueError, match="完整的已验收内容摘要"):
        RetryAttemptState.restore(checkpoint)


def test_ordinary_candidate_without_progress_keeps_four_attempt_transport_limit():
    state = RetryAttemptState()
    retryable = []
    for _ in range(4):
        state.attempt += 1
        retryable.append(state.record_failure(failure_kind="rate_limit", retryable=True,
            is_content_error=False, configured_max_attempts=1).can_retry)
    assert retryable == [True, True, True, False]
    assert state.transient_failure_count == state.transient_failure_streak == 4
    assert state.transient_progress_token is None
    restored = RetryAttemptState.restore(state.checkpoint())
    with pytest.raises(RuntimeError, match="耗尽重试预算"):
        restored.require_retry_budget(configured_max_attempts=1)
