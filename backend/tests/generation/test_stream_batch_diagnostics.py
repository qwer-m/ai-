from modules.test_generation_components.legacy.stream.batch_diagnostics import (
    build_case_signature,
    build_stream_batch_token_usage,
    build_stream_coverage_plan_lite,
    extract_requirement_semantics_payload,
    is_non_assertable_expected_result,
    is_retryable_provider_error,
)
from modules.test_generation_components.legacy.stream.batch_parallel_shards import (
    build_coverage_shard_plan,
    build_parallel_shard_instruction,
    merge_parallel_shard_cases,
    parallel_shard_config_from_env,
    should_use_parallel_shards,
)
from modules.test_generation_components.legacy.stream.batch_prompt_runtime import (
    append_history_to_testcase_context,
    build_recent_history_context,
    build_stream_batch_system_prompt,
)


class _Client:
    model = "fallback-model"

    def __init__(self, metadata: dict | None = None) -> None:
        self.last_response_metadata = metadata or {}


def test_extract_requirement_semantics_payload_normalizes_lists() -> None:
    payload = extract_requirement_semantics_payload(
        {
            "confirmed_facts": [" paid ", "", None, "created"],
            "scoped_rules": "not-a-list",
            "reuse_risks": ["legacy return"],
        }
    )

    assert payload["confirmed_facts"] == ["paid", "created"]
    assert payload["scoped_rules"] == []
    assert payload["reuse_risks"] == ["legacy return"]
    assert payload["pending_items"] == []


def test_retryable_provider_error_keeps_quota_and_policy_errors_fatal() -> None:
    assert is_retryable_provider_error("Exception occurred: incomplete chunked read") is True
    assert is_retryable_provider_error("504 gateway timeout") is True
    assert is_retryable_provider_error("[额度耗尽] insufficient_quota") is False
    assert is_retryable_provider_error("content policy forbidden") is False


def test_build_stream_batch_token_usage_prefers_provider_usage() -> None:
    payload = build_stream_batch_token_usage(
        client=_Client({"input_tokens": 11, "output_tokens": 7, "model": "provider-model"}),
        project_id=3,
        request_id="req-1",
        current_biz_key="payment",
        multi_pass=True,
        generation_mode="",
        batch_index=2,
        total_batches=4,
        attempt=1,
        need=5,
        output_text="abc",
        duration_ms=12,
        attempt_status="parsed",
    )

    assert payload["input_tokens"] == 11
    assert payload["output_tokens"] == 7
    assert payload["total_tokens"] == 18
    assert payload["token_source"] == "provider"
    assert payload["generation_mode"] == "multi_pass"
    assert payload["model"] == "provider-model"


def test_build_stream_batch_token_usage_marks_estimated_usage_unavailable() -> None:
    payload = build_stream_batch_token_usage(
        client=_Client({"prompt_tokens": 10, "completion_tokens": 5, "token_estimate_method": "chars"}),
        project_id=3,
        request_id="req-2",
        current_biz_key="",
        multi_pass=False,
        generation_mode="single_pass",
        batch_index=1,
        total_batches=1,
        attempt=2,
        need=1,
        output_text="abcdef",
        provider_error="x" * 260,
    )

    assert payload["input_tokens"] is None
    assert payload["output_tokens"] is None
    assert payload["token_source"] == "unavailable"
    assert payload["token_unavailable_reason"] == "provider_usage_estimated"
    assert payload["response_chars"] == 6
    assert len(payload["provider_error"]) == 200


def test_case_signature_and_non_assertable_expected_result_are_stable() -> None:
    left = {
        "test_module": "Pay",
        "description": " Submit order ",
        "test_input": "cart",
        "expected_result": "订单状态=已支付",
        "steps": [" open ", " submit "],
    }
    right = {
        "test_module": " pay ",
        "description": "submit  order",
        "test_input": "cart",
        "expected_result": "订单状态=已支付",
        "steps": ["open", "submit"],
    }

    assert build_case_signature(left) == build_case_signature(right)
    assert is_non_assertable_expected_result("执行成功") is True
    assert is_non_assertable_expected_result("订单状态字段等于 PAID") is False


def test_build_stream_coverage_plan_lite_uses_rule_diagnostics() -> None:
    def analyze_coverage_fn(requirement: str, cases: list[dict]) -> dict:
        assert requirement == "REQ"
        assert cases == []
        return {
            "rule_diagnostics": [
                {"rule_id": "RULE-1", "rule_text": "payment must be blocked without subscription"},
                {"rule_id": "RULE-2", "rule_text": ""},
                {"rule_text": "refund requires audit trail"},
            ]
        }

    prompt, rules = build_stream_coverage_plan_lite(
        "REQ",
        analyze_coverage_fn=analyze_coverage_fn,
    )

    assert len(rules) == 2
    assert "COVERAGE PLAN-LITE" in prompt
    assert "RULE-1: payment must be blocked without subscription" in prompt
    assert "RULE-002: refund requires audit trail" in prompt


def test_stream_batch_prompt_runtime_uses_recent_history_window() -> None:
    history = [f"TC-{index:03d}: payment scenario {index}" for index in range(1, 55)]

    history_context = build_recent_history_context(history)
    testcase_context = append_history_to_testcase_context("existing context", history)

    assert "TC-001: payment scenario 1" not in history_context
    assert "TC-005: payment scenario 5" in history_context
    assert "TC-054: payment scenario 54" in history_context
    assert "[本轮已生成摘要]" in testcase_context
    assert "TC-001: payment scenario 1" not in testcase_context
    assert "TC-054: payment scenario 54" in testcase_context


def test_build_stream_batch_system_prompt_keeps_quality_first_contract() -> None:
    prompt = build_stream_batch_system_prompt(
        base_prompt="BASE PROMPT",
        coverage_instruction="COVERAGE INSTRUCTION",
        history_context="HISTORY",
        coverage_plan_lite="PLAN-LITE",
        side_suite_order="main, visual",
        batch_index=1,
        total_batches=3,
        current_id=8,
        generated_in_batch=2,
        need=4,
    )

    assert "BASE PROMPT" in prompt
    assert "COVERAGE INSTRUCTION" in prompt
    assert "HISTORY" in prompt
    assert "PLAN-LITE" in prompt
    assert "This is batch 2 of 3." in prompt
    assert "Start the Test Case IDs from 10 (e.g., TC-010)." in prompt
    assert "Reference count: about 4 cases. This is NOT a quota." in prompt
    assert "Keep closed-loop continuity in current module first" in prompt
    assert "must all be present and non-empty" in prompt
    assert "the backend will reject incomplete cases instead of filling them with templates" in prompt
    assert "main, visual" in prompt


def test_parallel_shard_config_and_gate_default_to_serial() -> None:
    config = parallel_shard_config_from_env({})

    allowed, reason = should_use_parallel_shards(
        expected_count=100,
        append=False,
        multi_pass=True,
        total_batches=4,
        coverage_rule_count=10,
        config=config,
    )

    assert config.enabled is False
    assert allowed is False
    assert reason == "disabled_by_flag"


def test_parallel_shard_gate_requires_parallel_worthwhile_shape() -> None:
    config = parallel_shard_config_from_env(
        {
            "GENERATION_STREAM_COVERAGE_SHARDS_ENABLED": "true",
            "GENERATION_STREAM_COVERAGE_SHARD_MAX_WORKERS": "2",
            "GENERATION_STREAM_COVERAGE_SHARD_MIN_EXPECTED_COUNT": "60",
            "GENERATION_STREAM_COVERAGE_SHARD_MIN_RULES": "8",
        }
    )

    assert should_use_parallel_shards(
        expected_count=80,
        append=True,
        multi_pass=True,
        total_batches=4,
        coverage_rule_count=8,
        config=config,
    ) == (False, "append_mode")
    assert should_use_parallel_shards(
        expected_count=30,
        append=False,
        multi_pass=True,
        total_batches=2,
        coverage_rule_count=8,
        config=config,
    ) == (False, "expected_count_below_min")
    assert should_use_parallel_shards(
        expected_count=80,
        append=False,
        multi_pass=True,
        total_batches=4,
        coverage_rule_count=3,
        config=config,
    ) == (False, "insufficient_coverage_rules")


def test_build_coverage_shard_plan_splits_rules_in_stable_order() -> None:
    rules = [{"rule_id": f"RULE-{index:03d}", "rule_text": f"rule text {index}"} for index in range(1, 9)]

    shards = build_coverage_shard_plan(rules, expected_count=80, max_workers=2, max_cases_per_worker=25)
    instruction = build_parallel_shard_instruction(shards[0])

    assert [shard["shard_id"] for shard in shards] == ["SHARD-01", "SHARD-02"]
    assert shards[0]["target_count"] == 40
    assert shards[1]["target_count"] == 40
    assert shards[0]["rule_ids"] == ["RULE-001", "RULE-002", "RULE-003", "RULE-004"]
    assert shards[1]["rule_ids"] == ["RULE-005", "RULE-006", "RULE-007", "RULE-008"]
    assert "PARALLEL COVERAGE SHARD" in instruction
    assert "Generate validation goals only for the assigned rules" in instruction
    assert "RULE-005" in instruction


def test_merge_parallel_shard_cases_deduplicates_and_renumbers_dependencies() -> None:
    shard_results = [
        {
            "shard": {"shard_id": "SHARD-01", "merge_order": 1},
            "cases": [
                {
                    "id": "S1-001",
                    "test_module": "Forum",
                    "description": "create post",
                    "steps": ["open", "submit"],
                    "test_input": "valid title",
                    "expected_result": "post is visible",
                    "depends_on": [],
                },
                {
                    "id": "S1-002",
                    "test_module": "Forum",
                    "description": "reply post",
                    "steps": ["open", "reply"],
                    "test_input": "valid reply",
                    "expected_result": "reply is visible",
                    "depends_on": ["S1-001"],
                },
            ],
        },
        {
            "shard": {"shard_id": "SHARD-02", "merge_order": 2},
            "cases": [
                {
                    "id": "S2-001",
                    "test_module": "Forum",
                    "description": "create post",
                    "steps": ["open", "submit"],
                    "test_input": "valid title",
                    "expected_result": "post is visible",
                },
                {
                    "id": "S2-002",
                    "test_module": "Forum",
                    "description": "delete post",
                    "steps": ["open", "delete"],
                    "test_input": "own post",
                    "expected_result": "post is removed",
                },
            ],
        },
    ]

    merged = merge_parallel_shard_cases(
        shard_results,
        build_case_signature_fn=build_case_signature,
        start_id=7,
        expected_count=10,
    )

    cases = merged["cases"]
    assert [case["id"] for case in cases] == ["TC-007", "TC-008", "TC-009"]
    assert cases[1]["depends_on"] == ["TC-007"]
    assert merged["input_case_count"] == 4
    assert merged["unique_case_count"] == 3
    assert merged["duplicate_count"] == 1
