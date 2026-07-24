import json

from modules.test_generation_components.legacy.stream import batches as stream_batches_module
from modules.test_generation_components.legacy.stream.batches import (
    LegacyGenerationStreamBatchesMixin,
    _public_case_batch_json,
)
from modules.test_generation_components.legacy.stream.batch_diagnostics import (
    build_case_semantic_retry_instruction,
    build_required_stage_coverage_instruction,
    build_case_signature,
    build_stream_batch_token_usage,
    build_stream_coverage_plan_lite,
    extract_requirement_semantics_payload,
    is_non_assertable_expected_result,
    is_retryable_provider_error,
)
from modules.test_generation_components.legacy.stream.batch_parallel_shards import (
    ParallelShardConfig,
    build_coverage_shard_plan,
    build_parallel_shard_instruction,
    merge_parallel_shard_cases,
    parallel_shard_config_from_env,
    should_use_parallel_shards,
)
from modules.test_generation_components.legacy.stream.batch_prompt_runtime import (
    append_history_to_testcase_context,
    build_functional_architecture_instruction,
    build_recent_history_context,
    build_stream_batch_system_prompt,
)
from modules.test_generation_components.legacy.stream.batch_flow_control import resolve_stream_batch_plan


class _Client:
    model = "fallback-model"

    def __init__(self, metadata: dict | None = None) -> None:
        self.last_response_metadata = metadata or {}


class _SemanticBatchStreamClient:
    model = "integration-stream-model"
    last_response_metadata: dict = {}

    def __init__(self, case: dict, retry_case: dict | None = None) -> None:
        self.raw_payloads = [json.dumps([case], ensure_ascii=False)]
        if retry_case is not None:
            self.raw_payloads.append(json.dumps([retry_case], ensure_ascii=False))
        self.raw_payload = self.raw_payloads[0]
        self.call_count = 0
        self.prompts: list[str] = []

    def generate_response_stream(self, *args, **_kwargs):
        self.prompts.append(str(args[1]) if len(args) > 1 else "")
        payload = self.raw_payloads[min(self.call_count, len(self.raw_payloads) - 1)]
        self.call_count += 1
        yield payload


class _BatchStreamHarness(LegacyGenerationStreamBatchesMixin):
    def analyze_requirement_context(self, *_args, **_kwargs) -> dict:
        return {
            "system_type": "business_system",
            "complexity": "low",
            "suggested_ratios": {},
        }

    def _default_strategy_plan(self) -> dict:
        return {}

    def _is_active_db_session(self, _db) -> bool:
        return False

    def _emit_final_context_trace(self, **_kwargs) -> None:
        return None


def _semantic_requirement_control_state() -> dict:
    contract = {
        "semantic_contract_version": "requirement-semantic-v1",
        "status": "applied_independent_only",
        "semantic_compile_success": True,
        "workflow_declaration_status": "applied_independent_only",
        "workflow_absence_declared": True,
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "order",
                    "module_name": "订单处理",
                    "scope_status": "in_scope",
                    "evidence_verified": True,
                    "confidence": 0.9,
                },
                {
                    "module_key": "notice",
                    "module_name": "Notice",
                    "scope_status": "in_scope",
                    "evidence_verified": True,
                    "confidence": 0.9,
                },
            ],
            "module_interactions": [],
        },
        "workflow_blueprints": [],
    }
    return {
        "workflow_blueprints": [],
        "source_meta": {
            "semantic_compile_success": True,
            "workflow_declaration_status": "applied_independent_only",
            "workflow_absence_declared": True,
            "requirement_semantic_contract": contract,
        },
    }


def _valid_semantic_case() -> dict:
    return {
        "id": "TC-001",
        "description": "创建订单并核对订单状态",
        "test_module": "订单处理",
        "preconditions": ["用户已登录"],
        "steps": ["填写订单信息", "提交订单"],
        "test_input": "有效订单数据",
        "expected_result": "订单状态字段变为已创建",
        "priority": "P1",
        "_semantic": {
            "module_candidates": [
                {
                    "module_key": "order",
                    "module_name": "订单处理",
                    "role": "primary",
                    "confidence": 0.9,
                    "evidence": ["订单处理"],
                }
            ],
            "interaction_ids": [],
            "workflow_stage_candidates": [],
            "precondition_states": [],
            "produced_states": [],
        },
    }


def _run_batch_stream(
    monkeypatch,
    case: dict,
    retry_case: dict | None = None,
) -> tuple[list[str], dict, _SemanticBatchStreamClient]:
    control_state = _semantic_requirement_control_state()
    client = _SemanticBatchStreamClient(case, retry_case=retry_case)

    monkeypatch.setattr(
        stream_batches_module,
        "build_structured_prompt_context",
        lambda **kwargs: {
            "requirement_context": kwargs.get("requirement") or "",
            "requirement_semantics_context": "",
            "testcase_context": "",
            "supplement_context": "",
            "control_context": "",
            "current_biz_key": "order",
            "project_profile": {
                "functional_architecture": (
                    control_state["source_meta"]["requirement_semantic_contract"]["functional_architecture"]
                )
            },
            "feedback_control_state": control_state,
        },
    )
    monkeypatch.setattr(stream_batches_module, "build_closed_loop_base_prompt", lambda *_args, **_kwargs: "PROMPT")
    monkeypatch.setattr(stream_batches_module, "build_stream_batch_system_prompt", lambda **_kwargs: "SYSTEM")
    monkeypatch.setattr(stream_batches_module, "execution_side_suite_order_text", lambda: "independent")
    monkeypatch.setattr(stream_batches_module, "build_functional_architecture_instruction", lambda **_kwargs: "")
    monkeypatch.setattr(stream_batches_module, "_build_stream_coverage_plan_lite", lambda *_args, **_kwargs: ("", []))
    monkeypatch.setattr(
        stream_batches_module,
        "parallel_shard_config_from_settings",
        lambda _settings: ParallelShardConfig(
            enabled=False,
            max_workers=1,
            min_expected_count=60,
            min_coverage_rules=8,
            duplicate_rate_abort=0.25,
            min_unique_ratio=0.45,
        ),
    )
    monkeypatch.setattr(stream_batches_module, "_emit_biz_key_diag_payload", lambda **_kwargs: False)
    monkeypatch.setattr(stream_batches_module, "_emit_prompt_context_intake_diag_payload", lambda **_kwargs: None)
    monkeypatch.setattr(stream_batches_module, "_emit_stream_gen_diag_payload", lambda **_kwargs: None)
    monkeypatch.setattr(stream_batches_module, "_emit_stream_batch_quality_diag_payload", lambda **_kwargs: None)
    monkeypatch.setattr(stream_batches_module, "_emit_stream_batch_token_usage_diag_payload", lambda **_kwargs: None)

    state = {
        "client": client,
        "requirement": "用户可以创建订单并查看创建后的状态",
        "original_requirement": "用户可以创建订单并查看创建后的状态",
        "project_id": 2,
        "db": None,
        "doc_type": "requirement",
        "expected_count": 1,
        "batch_size": 1,
        "append": False,
        "user_id": 1,
        "request_id": "stream-public-projection",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "context_result": {},
        "gate_debug": {},
        "feedback_control_state": control_state,
        "only_current_biz": True,
        "compress": False,
        "multi_pass": False,
        "generation_mode": "single_pass",
    }
    chunks = list(_BatchStreamHarness()._stream_run_batches_phase(state=state))
    return chunks, state, client


def _streamed_case_payloads(chunks: list[str]) -> list[list[dict]]:
    payloads: list[list[dict]] = []
    for chunk in chunks:
        try:
            payload = json.loads(str(chunk or "").strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            payloads.append(payload)
    return payloads


def test_public_case_batch_json_removes_internal_semantic_fields() -> None:
    payload = json.loads(_public_case_batch_json([_valid_semantic_case()]))

    assert len(payload) == 1
    assert payload[0]["description"] == "创建订单并核对订单状态"
    assert payload[0]["priority_final"] == "P1"
    assert "_semantic" not in payload[0]


def test_serial_batch_stream_emits_only_strictly_valid_public_cases(monkeypatch) -> None:
    chunks, state, client = _run_batch_stream(monkeypatch, _valid_semantic_case())
    streamed_payloads = _streamed_case_payloads(chunks)

    assert client.raw_payload not in chunks
    assert len(streamed_payloads) == 1
    assert streamed_payloads[0][0]["description"] == "创建订单并核对订单状态"
    assert "_semantic" not in streamed_payloads[0][0]
    assert '"_semantic"' in state["full_content"]
    assert state["case_semantic_rejection_count"] == 0
    assert state["stream_batch_acceptance_summaries"][0]["source"] == "serial_batch"
    assert state["stream_batch_acceptance_summaries"][0]["accepted_count"] == 1


def test_serial_batch_stream_never_emits_case_rejected_by_semantic_gate(monkeypatch) -> None:
    invalid_case = _valid_semantic_case()
    invalid_case.pop("_semantic")

    chunks, state, client = _run_batch_stream(monkeypatch, invalid_case)

    assert client.raw_payload not in chunks
    assert _streamed_case_payloads(chunks) == []
    assert state["full_content"] == "[]\n"
    assert client.call_count == 3
    assert state["case_semantic_rejection_count"] == 3
    assert state["case_semantic_contract_failed"] is True


def test_serial_batch_stream_filters_module_conflict_before_counting(monkeypatch) -> None:
    conflicting_case = _valid_semantic_case()
    conflicting_case["test_module"] = "Notice"
    conflicting_case["_semantic"]["module_candidates"][0]["evidence"] = [
        "创建订单"
    ]

    chunks, state, client = _run_batch_stream(monkeypatch, conflicting_case)

    assert _streamed_case_payloads(chunks) == []
    assert state["full_content"] == "[]\n"
    assert state["case_semantic_rejection_count"] == 0
    assert state["case_semantic_accepted_count"] == 0
    assert client.call_count == 3
    summaries = state["stream_batch_acceptance_summaries"]
    assert len(summaries) == 3
    assert all(item["source"] == "serial_batch" for item in summaries)
    assert all(item["module_rejected_case_count"] == 1 for item in summaries)


def test_serial_batch_stream_regenerates_from_field_level_semantic_feedback(monkeypatch) -> None:
    invalid_case = _valid_semantic_case()
    invalid_case["_semantic"]["module_candidates"][0].pop("evidence")
    invalid_case["_semantic"]["module_candidates"][0].pop("confidence")

    chunks, state, client = _run_batch_stream(
        monkeypatch,
        invalid_case,
        retry_case=_valid_semantic_case(),
    )
    streamed_payloads = _streamed_case_payloads(chunks)

    assert client.call_count == 2
    assert "CASE SEMANTIC CONTRACT RETRY" in client.prompts[1]
    assert '"evidence":1' in client.prompts[1]
    assert '"confidence":1' in client.prompts[1]
    assert len(streamed_payloads) == 1
    assert state["case_semantic_rejection_count"] == 1
    assert state["case_semantic_accepted_count"] == 1
    assert state["case_semantic_contract_failed"] is False


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


def test_case_semantic_retry_instruction_contains_only_aggregate_field_feedback() -> None:
    prompt = build_case_semantic_retry_instruction(
        [
            {
                "case_id": "TC-001",
                "description": "sensitive case body",
                "rejection_reasons": ["module_candidate:item_schema_invalid"],
                "rejected_semantic_items": [
                    {
                        "item_type": "module_candidate",
                        "reason": "item_schema_invalid",
                        "missing_or_invalid_fields": ["evidence", "confidence"],
                    }
                ],
            }
        ]
    )

    assert "CASE SEMANTIC CONTRACT RETRY" in prompt
    assert '"evidence":1' in prompt
    assert '"confidence":1' in prompt
    assert "sensitive case body" not in prompt
    assert "TC-001" not in prompt


def test_required_stage_coverage_instruction_uses_only_exact_contract_ids() -> None:
    prompt = build_required_stage_coverage_instruction(
        {
            "active": True,
            "source_generation_allowed": True,
            "missing_required_stages": [
                {
                    "workflow_id": "publish_flow",
                    "stage_id": "visible",
                    "stage_kind": "downstream_visibility",
                    "stage_order": 3,
                }
            ],
        }
    )

    assert "REQUIRED WORKFLOW STAGE COVERAGE" in prompt
    assert '"workflow_id":"publish_flow"' in prompt
    assert '"stage_id":"visible"' in prompt
    assert "Each stage needs its own executable candidate" in prompt
    assert "case body" not in prompt


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


def test_stream_batch_prompt_runtime_keeps_complete_history_catalog() -> None:
    history = [f"TC-{index:03d}: payment scenario {index}" for index in range(1, 55)]

    history_context = build_recent_history_context(history)
    testcase_context = append_history_to_testcase_context("existing context", history)

    assert "TC-001: payment scenario 1" in history_context
    assert "TC-005: payment scenario 5" in history_context
    assert "TC-054: payment scenario 54" in history_context
    assert "[本轮已生成摘要]" in testcase_context
    assert "TC-001: payment scenario 1" in testcase_context
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
    assert "Optimize coverage across the complete verified architecture" in prompt
    assert "must all be present and non-empty" in prompt
    assert "the backend will reject incomplete cases instead of filling them with templates" in prompt
    assert "main, visual" in prompt


def test_stream_batch_plan_uses_global_count_and_requested_batch_size() -> None:
    plan = resolve_stream_batch_plan(
        expected_count=37,
        batch_size=10,
        append=False,
        start_id=1,
        existing_unique_count=0,
    )

    assert plan == {
        "expected_count": 37,
        "batch_size": 10,
        "generation_target_count": 37,
        "total_batches": 4,
        "auto_extended": False,
    }

    append_plan = resolve_stream_batch_plan(
        expected_count=42,
        batch_size=10,
        append=True,
        start_id=31,
        existing_unique_count=30,
    )
    assert append_plan["generation_target_count"] == 12
    assert append_plan["batch_size"] == 10
    assert append_plan["total_batches"] == 2


def test_global_architecture_prompt_keeps_unbalanced_modules_in_one_decision_space() -> None:
    prompt = build_functional_architecture_instruction(
        project_profile={
            "functional_architecture": {
                "functional_modules": [
                    {
                        "module_key": "order",
                        "module_name": "订单处理",
                        "scope_status": "in_scope",
                        "features": ["创建订单", "修改订单", "取消订单", "退款", "状态恢复"],
                        "evidence": ["订单支持创建、修改、取消、退款和失败恢复"],
                        "evidence_verified": True,
                    },
                    {
                        "module_key": "notice",
                        "module_name": "消息提醒",
                        "scope_status": "in_scope",
                        "features": ["查看提醒"],
                        "evidence": ["用户可以查看订单状态提醒"],
                        "evidence_verified": True,
                    },
                    {
                        "module_key": "draft",
                        "module_name": "未核验草稿区",
                        "scope_status": "in_scope",
                        "features": ["未知功能"],
                        "evidence": ["未核验文本"],
                        "evidence_verified": False,
                    },
                ],
                "module_interactions": [
                    {
                        "interaction_id": "order_notice",
                        "source_module_key": "order",
                        "target_module_key": "notice",
                        "source_module": "订单处理",
                        "target_module": "消息提醒",
                        "trigger": "订单状态变化后发送提醒",
                        "evidence": ["订单状态变化后发送提醒"],
                        "evidence_verified": True,
                    }
                ],
            }
        }
    )

    assert "订单处理" in prompt
    assert "状态恢复" in prompt
    assert "消息提醒" in prompt
    assert "order_notice" in prompt
    assert "未核验草稿区" not in prompt
    assert "CURRENT TARGET MODULE" not in prompt
    assert "target_count" not in prompt
    assert "Generate cases ONLY" not in prompt


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
