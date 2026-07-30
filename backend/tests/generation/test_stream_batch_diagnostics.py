import json
from types import SimpleNamespace

from modules.test_generation_components.legacy.stream import batches as stream_batches_module
from modules.test_generation_components.legacy.stream import (
    batch_parallel_shards as parallel_shards_module,
)
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
    build_parallel_gap_repair_requests,
    build_coverage_shard_plan,
    build_parallel_shard_instruction,
    execute_parallel_shard_requests,
    merge_parallel_shard_attempts,
    merge_parallel_shard_cases,
    normalize_and_accept_parallel_shard_results,
    parallel_shard_config_from_env,
    should_use_parallel_shards,
)
from modules.test_generation_components.legacy.stream.batch_prompt_runtime import (
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
    STREAM_HEARTBEAT_CHUNK = "\x00test_stream_heartbeat\x00"

    def __init__(
        self,
        case: dict,
        retry_case: dict | None = None,
        *,
        emit_heartbeat: bool = False,
    ) -> None:
        self.raw_payloads = [json.dumps([case], ensure_ascii=False)]
        if retry_case is not None:
            self.raw_payloads.append(json.dumps([retry_case], ensure_ascii=False))
        self.raw_payload = self.raw_payloads[0]
        self.call_count = 0
        self.prompts: list[str] = []
        self.kwargs_inputs: list[dict] = []
        self.emit_heartbeat = bool(emit_heartbeat)

    @classmethod
    def is_stream_heartbeat(cls, value: object) -> bool:
        return value == cls.STREAM_HEARTBEAT_CHUNK

    def generate_response_stream(self, *args, **kwargs):
        self.prompts.append(str(args[1]) if len(args) > 1 else "")
        self.kwargs_inputs.append(dict(kwargs))
        payload = self.raw_payloads[min(self.call_count, len(self.raw_payloads) - 1)]
        self.call_count += 1
        if self.emit_heartbeat:
            yield self.STREAM_HEARTBEAT_CHUNK
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
    *,
    emit_heartbeat: bool = False,
    parallel_config: ParallelShardConfig | None = None,
    coverage_rules: list[dict] | None = None,
    expected_count: int = 1,
    batch_size: int = 1,
    multi_pass: bool = False,
    parallel_shard_client_factory=None,
) -> tuple[list[str], dict, _SemanticBatchStreamClient]:
    control_state = _semantic_requirement_control_state()
    client = _SemanticBatchStreamClient(
        case,
        retry_case=retry_case,
        emit_heartbeat=emit_heartbeat,
    )

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
    monkeypatch.setattr(
        stream_batches_module,
        "_build_stream_coverage_plan_lite",
        lambda *_args, **_kwargs: ("", list(coverage_rules or [])),
    )
    resolved_parallel_config = parallel_config or ParallelShardConfig(
        enabled=False,
        max_workers=1,
        min_expected_count=60,
        min_coverage_rules=8,
        duplicate_rate_abort=0.25,
        min_unique_ratio=0.45,
    )
    monkeypatch.setattr(
        stream_batches_module,
        "parallel_shard_config_from_settings",
        lambda _settings: resolved_parallel_config,
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
        "expected_count": int(expected_count),
        "batch_size": int(batch_size),
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
        "multi_pass": bool(multi_pass),
        "generation_mode": "multi_pass" if multi_pass else "single_pass",
    }
    if callable(parallel_shard_client_factory):
        state["parallel_shard_client_factory"] = parallel_shard_client_factory
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


def test_serial_batch_stream_forwards_heartbeat_without_polluting_model_json(
    monkeypatch,
) -> None:
    chunks, state, client = _run_batch_stream(
        monkeypatch,
        _valid_semantic_case(),
        emit_heartbeat=True,
    )

    assert any("模型仍在生成" in chunk for chunk in chunks)
    assert client.STREAM_HEARTBEAT_CHUNK not in chunks
    assert client.STREAM_HEARTBEAT_CHUNK not in state["full_content"]
    assert client.kwargs_inputs[0]["request_timeout_seconds"] == 180.0
    assert client.kwargs_inputs[0]["heartbeat_interval_seconds"] == 15.0
    assert client.kwargs_inputs[0]["reasoning_effort"] == "low"
    assert client.kwargs_inputs[0]["disable_thinking"] is True
    assert len(_streamed_case_payloads(chunks)) == 1


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
    assert any("明确记录缺口 1 条" in chunk for chunk in chunks)
    assert state["stream_batch_underfill_summaries"] == [
        {
            "batch_index": 1,
            "requested_count": 1,
            "accepted_case_count": 0,
            "underfill_count": 1,
            "attempt_count": 3,
            "source_incomplete_case_count": 3,
            "underfill_detected": True,
        }
    ]


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
    assert '"module_candidates":[{' in prompt
    assert '"interaction_ids":[]' in prompt
    assert '"workflow_stage_candidates":[]' in prompt
    assert '"precondition_states":[]' in prompt
    assert '"produced_states":[]' in prompt
    assert "module_candidates` must remain non-empty" in prompt
    assert "one complete public-field value copied verbatim" in prompt
    assert "prefer description, steps, or expected_result" in prompt
    assert "Do not shorten, summarize, or paraphrase evidence" in prompt
    assert "copy workflow_id, stage_id, and stage_kind exactly" in prompt
    assert "Never replace stage evidence with a summary" in prompt
    assert "validate every regenerated case separately" in prompt


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
    assert (
        "Copy workflow_id, stage_id, and stage_kind exactly"
    ) in prompt
    assert (
        "copy the declared module_key, module_name, and role values exactly while citing "
        "evidence and confidence from the current case"
    ) in prompt
    assert "copy interaction_ids exactly" in prompt
    assert "Each required stage needs its own executable candidate." in prompt
    assert (
        "`_semantic.precondition_states` and `_semantic.produced_states` may be empty; the "
        "execution plan inherits authoritative required_states and produced_states from the "
        "matching workflow step."
    ) in prompt
    assert "Do not copy the catalog's typed-state arrays into the candidate." in prompt
    assert (
        "Declare an additional typed state only when the current case's public fields provide "
        "exact evidence for it."
    ) in prompt
    assert "do not conflict with the matching workflow step's authoritative states" in prompt
    assert "Map required_states to `_semantic.precondition_states`" not in prompt
    assert "never translate internal state identifiers" not in prompt
    assert "case body" not in prompt


def test_retryable_provider_error_keeps_quota_and_policy_errors_fatal() -> None:
    assert is_retryable_provider_error("Exception occurred: incomplete chunked read") is True
    assert is_retryable_provider_error("504 gateway timeout") is True
    assert is_retryable_provider_error("[额度耗尽] insufficient_quota") is False
    assert is_retryable_provider_error("content policy forbidden") is False


def test_build_stream_batch_token_usage_prefers_provider_usage() -> None:
    payload = build_stream_batch_token_usage(
        client=_Client(
            {
                "input_tokens": 11,
                "output_tokens": 7,
                "model": "provider-model",
                "reasoning_chars": 31,
                "first_reasoning_ms": 120.5,
                "first_content_ms": 340.25,
                "total_duration_ms": 980.75,
            }
        ),
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
    assert payload["reasoning_chars"] == 31
    assert payload["first_reasoning_ms"] == 120.5
    assert payload["first_content_ms"] == 340.25
    assert payload["provider_total_duration_ms"] == 980.75


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


def test_stream_batch_prompt_runtime_keeps_single_complete_history_catalog() -> None:
    history = [f"TC-{index:03d}: payment scenario {index}" for index in range(1, 55)]

    history_context = build_recent_history_context(history)

    assert "TC-001: payment scenario 1" in history_context
    assert "TC-005: payment scenario 5" in history_context
    assert "TC-054: payment scenario 54" in history_context


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
    assert "Accepted batch target: exactly 4 additional contract-valid, non-duplicate cases." in prompt
    assert "the backend will report an explicit underfill" in prompt
    assert "Optimize coverage across the complete verified architecture" in prompt
    assert "must all be present and non-empty" in prompt
    assert "the backend will reject incomplete cases instead of filling them with templates" in prompt
    assert "main, visual" in prompt


def test_stream_batch_system_prompt_places_shard_contract_near_output_tail() -> None:
    shard_instruction = build_parallel_shard_instruction(
        {
            "shard_id": "SHARD-02",
            "shard_index": 2,
            "total_shards": 3,
            "target_count": 8,
            "rule_ids": ["RULE-002"],
            "rule_texts": ["validate lesson submission"],
        }
    )

    prompt = build_stream_batch_system_prompt(
        base_prompt="BASE PROMPT",
        coverage_instruction="COVERAGE INSTRUCTION",
        history_context="HISTORY",
        coverage_plan_lite="PLAN-LITE",
        side_suite_order="main, visual",
        batch_index=1,
        total_batches=3,
        current_id=8,
        generated_in_batch=0,
        need=8,
        shard_instruction=shard_instruction,
        architecture_instruction="ACTIVE ARCHITECTURE",
    )

    assert prompt.rfind("FINAL SHARD OUTPUT CONTRACT") > prompt.rfind(
        "Every case must pass these gates"
    )
    assert prompt.rfind('"module_candidates":[{') > prompt.rfind(
        "ACTIVE ARCHITECTURE"
    )
    assert prompt.rfind("FINAL SHARD OUTPUT CONTRACT") < prompt.rfind(
        "Return ONLY the JSON array"
    )
    assert prompt.rfind("FINAL PER-CASE STRUCTURE CHECK") > prompt.rfind(
        "FINAL SHARD OUTPUT CONTRACT"
    )
    assert "one complete public-field value from that same case verbatim" in prompt
    assert "Never return a case with `_semantic` missing or partially populated" in prompt
    for field in (
        "module_candidates",
        "interaction_ids",
        "workflow_stage_candidates",
        "precondition_states",
        "produced_states",
    ):
        assert f"_semantic.{field}" in prompt


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
                        "role": "owner",
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
    assert '"role":"owner"' in prompt
    assert "未核验草稿区" not in prompt
    assert "CURRENT TARGET MODULE" not in prompt
    assert "target_count" not in prompt
    assert "Generate cases ONLY" not in prompt


def test_parallel_shard_config_defaults_to_public_batch_size_threshold() -> None:
    config = parallel_shard_config_from_env({})

    allowed, reason = should_use_parallel_shards(
        expected_count=25,
        append=False,
        multi_pass=True,
        total_batches=1,
        coverage_rule_count=10,
        config=config,
    )

    assert config.enabled is True
    assert config.min_expected_count == 25
    assert allowed is True
    assert reason == "enabled"
    assert should_use_parallel_shards(
        expected_count=24,
        append=False,
        multi_pass=True,
        total_batches=1,
        coverage_rule_count=10,
        config=config,
    ) == (False, "expected_count_below_min")


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
    assert should_use_parallel_shards(
        expected_count=25,
        append=False,
        multi_pass=True,
        total_batches=1,
        coverage_rule_count=8,
        config=config,
    ) == (False, "expected_count_below_min")


def test_build_coverage_shard_plan_splits_rules_in_stable_order() -> None:
    rules = [
        {
            "rule_id": f"RULE-{index:03d}",
            "rule_text": f"rule text {index}",
            "facts": [{"fact_id": f"FACT-{index:03d}", "statement": f"fact statement {index}"}],
        }
        for index in range(1, 9)
    ]

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
    assert "FINAL SHARD OUTPUT CONTRACT" in instruction
    assert '"module_candidates":[{' in instruction
    assert '"interaction_ids":[]' in instruction
    assert '"workflow_stage_candidates":[]' in instruction
    assert '"precondition_states":[]' in instruction
    assert '"produced_states":[]' in instruction
    assert "module_candidates may never be empty" in instruction
    assert "one complete public-field value from that same case verbatim" in instruction
    assert "Do not shorten, summarize, or paraphrase it" in instruction
    assert "Shard output target: exactly 40 additional" in instruction
    assert "This is the authoritative count for this shard request" in instruction
    assert '"fact_id":"FACT-001"' in instruction
    assert '"statement":"fact statement 1"' in instruction
    assert "Target count: about" not in instruction
    assert "not a quota" not in instruction

    bounded_shards = build_coverage_shard_plan(
        rules,
        expected_count=80,
        max_workers=2,
        max_cases_per_worker=13,
        max_shards=7,
    )
    assert len(bounded_shards) == 7
    assert max(int(shard["target_count"]) for shard in bounded_shards) <= 12


def test_main_chain_shard_instruction_reserves_workflow_closure() -> None:
    instruction = build_parallel_shard_instruction(
        {
            "shard_id": "MAIN-CHAIN",
            "shard_kind": "main_chain",
            "shard_index": 1,
            "total_shards": 3,
            "target_count": 6,
        }
    )

    assert "PRIMARY WORKFLOW SHARD" in instruction
    assert "every required stage" in instruction
    assert "initial-state to terminal-state closure" in instruction
    assert "Do not generate independent permission" in instruction
    assert "required states, and produced states" not in instruction
    assert "Map stage.required_states" not in instruction
    assert "Do not copy the catalog's typed-state arrays into the case" in instruction
    assert "FINAL SHARD OUTPUT CONTRACT" in instruction
    assert '"workflow_stage_candidates":[{' in instruction
    assert "one complete value of this case's description, steps, or expected_result verbatim" in instruction
    assert "never use a summary or paraphrase" in instruction
    assert "Never output a case with `_semantic` missing" in instruction


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


def test_parallel_shard_worker_only_parses_raw_json_without_semantic_normalization(
    monkeypatch,
) -> None:
    class _RawShardClient:
        STREAM_HEARTBEAT_CHUNK = "\x00parallel-heartbeat\x00"

        def __init__(self, payload: list[dict]) -> None:
            self.payload = payload
            self.kwargs_inputs: list[dict] = []
            self.last_response_metadata = {"model": "glm-5.1"}

        def is_stream_heartbeat(self, value: object) -> bool:
            return value == self.STREAM_HEARTBEAT_CHUNK

        def generate_response_stream(self, *_args, **kwargs):
            self.kwargs_inputs.append(dict(kwargs))
            yield self.STREAM_HEARTBEAT_CHUNK
            yield json.dumps(self.payload, ensure_ascii=False)

    def _sequential_governed_map(*, items, worker, **_kwargs):
        item_list = list(items)
        for index, item in enumerate(item_list):
            try:
                item_result = SimpleNamespace(
                    item=item,
                    result=worker(item),
                    exception=None,
                )
            except Exception as exc:  # pragma: no cover - 断言失败时保留真实异常
                item_result = SimpleNamespace(
                    item=item,
                    result=None,
                    exception=exc,
                )
            yield SimpleNamespace(
                kind="item",
                completed_count=index + 1,
                total_count=len(item_list),
                item_result=item_result,
            )

    monkeypatch.setattr(
        parallel_shards_module,
        "iter_governed_threadpool_map",
        _sequential_governed_map,
    )
    raw_case = {
        "caseId": "RAW-001",
        "expectedResult": "保持模型原始字段",
    }
    client = _RawShardClient([raw_case])

    results = execute_parallel_shard_requests(
        requests=[
            {
                "request_id": "parallel-worker-contract",
                "shard": {
                    "shard_id": "SHARD-01",
                    "merge_order": 1,
                    "target_count": 1,
                },
                "client": client,
                "system_prompt": "SYSTEM",
                "request_timeout_seconds": 180,
                "heartbeat_interval_seconds": 15,
            }
        ],
        requirement="真实需求",
        clean_and_parse_json_fn=json.loads,
        max_workers=2,
    )

    assert results[0]["status"] == "parsed"
    assert results[0]["cases"] == [raw_case]
    assert "id" not in results[0]["cases"][0]
    assert "expected_result" not in results[0]["cases"][0]
    assert results[0]["raw_parsed_case_count"] == 1
    assert results[0]["normalized_case_count"] == 0
    assert results[0]["semantic_rejection_count"] == 0
    assert client.kwargs_inputs[0]["reasoning_effort"] == "low"
    assert client.kwargs_inputs[0]["disable_thinking"] is True


def test_parallel_shards_are_normalized_on_main_thread_in_merge_order_with_metrics() -> None:
    semantic_rejections: list[dict] = []
    normalization_order: list[list[str]] = []
    acceptance_start_ids: list[int] = []

    def _normalize(raw_cases: list[dict]) -> list[dict]:
        case_ids = [str(case.get("caseId") or "") for case in raw_cases]
        normalization_order.append(case_ids)
        if "A-REJECT" in case_ids:
            semantic_rejections.append(
                {
                    "case_id": "A-REJECT",
                    "rejection_reasons": ["module_candidate:evidence_missing"],
                }
            )
        return [
            {
                **case,
                "id": str(case.get("caseId") or ""),
                "expected_result": str(case.get("expectedResult") or ""),
            }
            for case in raw_cases
            if case.get("caseId") != "A-REJECT"
        ]

    def _accept(cases: list[dict], *, limit: int, start_id: int):
        acceptance_start_ids.append(start_id)
        accepted = []
        for offset, case in enumerate(cases[:limit]):
            accepted.append({**case, "id": f"TC-{start_id + offset:03d}"})
        return SimpleNamespace(
            cases=accepted,
            incomplete_rows=[],
            module_contract_summary={},
        )

    normalized = normalize_and_accept_parallel_shard_results(
        [
            {
                "shard": {
                    "shard_id": "SHARD-02",
                    "merge_order": 2,
                    "target_count": 1,
                },
                "status": "parsed",
                "cases": [{"caseId": "B-001", "expectedResult": "B"}],
            },
            {
                "shard": {
                    "shard_id": "SHARD-01",
                    "merge_order": 1,
                    "target_count": 2,
                },
                "status": "parsed",
                "cases": [
                    {"caseId": "A-001", "expectedResult": "A"},
                    {"caseId": "A-REJECT", "expectedResult": "reject"},
                ],
            },
        ],
        normalize_json_structure_fn=_normalize,
        accept_candidates_fn=_accept,
        semantic_rejections=semantic_rejections,
        start_id=10,
    )

    assert normalization_order == [["A-001", "A-REJECT"], ["B-001"]]
    assert acceptance_start_ids == [10, 12]
    assert [item["shard"]["shard_id"] for item in normalized] == [
        "SHARD-01",
        "SHARD-02",
    ]
    assert normalized[0]["normalized_case_count"] == 1
    assert normalized[0]["semantic_rejection_count"] == 1
    assert normalized[0]["semantic_rejection_codes"] == [
        "module_candidate:evidence_missing"
    ]
    assert semantic_rejections[0]["source_shard_id"] == "SHARD-01"
    assert semantic_rejections[0]["source_shard_attempt"] == 1
    assert normalized[0]["accepted_case_count"] == 1
    assert normalized[0]["gap_count"] == 1
    assert normalized[0]["status"] == "underfilled"
    assert normalized[1]["normalized_case_count"] == 1
    assert normalized[1]["semantic_rejection_count"] == 0
    assert normalized[1]["accepted_case_count"] == 1
    assert normalized[1]["gap_count"] == 0
    assert normalized[1]["status"] == "accepted"


def test_parallel_gap_repair_preserves_successful_shard_and_targets_only_failure() -> None:
    requests = [
        {
            "shard": {
                "shard_id": "SHARD-01",
                "merge_order": 1,
                "target_count": 1,
            },
            "system_prompt": "SYSTEM-1",
        },
        {
            "shard": {
                "shard_id": "SHARD-02",
                "merge_order": 2,
                "target_count": 1,
            },
            "system_prompt": "SYSTEM-2",
        },
    ]
    successful_case = {"id": "S1-001", "description": "成功分片用例"}
    initial_results = [
        {
            "shard": dict(requests[0]["shard"]),
            "status": "parsed",
            "cases": [successful_case],
            "error_codes": [],
        },
        {
            "shard": dict(requests[1]["shard"]),
            "status": "provider_error",
            "cases": [],
            "error_codes": ["provider_error"],
        },
    ]

    repairs = build_parallel_gap_repair_requests(
        requests=requests,
        accepted_results=initial_results,
    )

    assert len(repairs) == 1
    assert repairs[0]["shard"]["repair_of_shard_id"] == "SHARD-02"
    assert repairs[0]["shard"]["target_count"] == 1
    assert "SHARD-01" not in repairs[0]["system_prompt"]
    assert "Only repair shard SHARD-02" in repairs[0]["system_prompt"]

    merged = merge_parallel_shard_attempts(
        initial_results,
        [
            {
                "shard": dict(repairs[0]["shard"]),
                "status": "parsed",
                "cases": [{"id": "S2-REPAIR", "description": "局部补生成用例"}],
                "error_codes": [],
                "raw_parsed_case_count": 1,
            }
        ],
    )

    assert [item["shard"]["shard_id"] for item in merged] == [
        "SHARD-01",
        "SHARD-02",
    ]
    assert merged[0]["cases"] == [successful_case]
    assert merged[0].get("repair_attempt_count", 0) == 0
    assert merged[1]["cases"] == [
        {"id": "S2-REPAIR", "description": "局部补生成用例"}
    ]
    assert merged[1]["repair_status"] == "parsed"
    assert merged[1]["repair_attempt_count"] == 1


def test_parallel_gap_repair_can_target_main_chain_contract_gap_without_count_gap() -> None:
    requests = [
        {
            "shard": {
                "shard_id": "MAIN-CHAIN",
                "shard_kind": "main_chain",
                "merge_order": 1,
                "target_count": 2,
            },
            "system_prompt": "SYSTEM",
        }
    ]
    accepted_results = [
        {
            "shard": dict(requests[0]["shard"]),
            "status": "accepted",
            "cases": [
                {"id": "TC-001", "description": "stage one"},
                {"id": "TC-002", "description": "stage two"},
            ],
            "repair_target_count": 1,
            "repair_instruction": "REQUIRED WORKFLOW STAGE COVERAGE",
        }
    ]

    repairs = build_parallel_gap_repair_requests(
        requests=requests,
        accepted_results=accepted_results,
    )

    assert len(repairs) == 1
    assert repairs[0]["shard"]["repair_of_shard_id"] == "MAIN-CHAIN"
    assert repairs[0]["shard"]["target_count"] == 1
    assert "REQUIRED WORKFLOW STAGE COVERAGE" in repairs[0]["system_prompt"]


def test_parallel_failed_shard_is_repaired_without_whole_batch_serial_fallback(
    monkeypatch,
) -> None:
    real_stream_parallel_shard_requests = (
        stream_batches_module.stream_parallel_shard_requests
    )
    parallel_stream_call_count = 0

    def _stream_with_repair_heartbeat(**kwargs):
        nonlocal parallel_stream_call_count
        parallel_stream_call_count += 1
        if parallel_stream_call_count == 2:
            yield {
                "kind": "heartbeat",
                "completed_count": 0,
                "total_count": len(kwargs.get("requests") or []),
            }
        return (yield from real_stream_parallel_shard_requests(**kwargs))

    monkeypatch.setattr(
        stream_batches_module,
        "stream_parallel_shard_requests",
        _stream_with_repair_heartbeat,
    )

    class _ParallelSequenceClient:
        model = "glm-5.1"
        last_response_metadata: dict = {}

        def __init__(self, responses: list[str]) -> None:
            self.responses = list(responses)
            self.call_count = 0

        def generate_response_stream(self, *_args, **_kwargs):
            index = min(self.call_count, len(self.responses) - 1)
            self.call_count += 1
            yield self.responses[index]

    successful_case = _valid_semantic_case()
    successful_case["id"] = "SHARD-01-RAW"
    successful_case["description"] = "成功分片保留创建订单用例"
    repaired_case = _valid_semantic_case()
    repaired_case["id"] = "SHARD-02-REPAIR"
    repaired_case["description"] = "失败分片局部补生成订单状态校验用例"
    repaired_case["expected_result"] = "订单状态字段更新且状态记录可查询"

    successful_client = _ParallelSequenceClient(
        [json.dumps([successful_case], ensure_ascii=False)]
    )
    repair_client = _ParallelSequenceClient(
        [
            "Exception occurred: gateway timeout",
            json.dumps([repaired_case], ensure_ascii=False),
        ]
    )
    clients = {
        1: successful_client,
        2: repair_client,
    }

    chunks, state, serial_client = _run_batch_stream(
        monkeypatch,
        _valid_semantic_case(),
        parallel_config=ParallelShardConfig(
            enabled=True,
            max_workers=2,
            min_expected_count=2,
            min_coverage_rules=2,
            duplicate_rate_abort=0.5,
            min_unique_ratio=0.5,
        ),
        coverage_rules=[
            {"rule_id": "RULE-001", "rule_text": "创建订单"},
            {"rule_id": "RULE-002", "rule_text": "订单状态"},
        ],
        expected_count=2,
        batch_size=2,
        multi_pass=True,
        parallel_shard_client_factory=lambda shard: clients[
            int(shard.get("shard_index") or 0)
        ],
    )

    summary = state["stream_parallel_shard_result"]
    assert state["stream_parallel_shards_used"] is True
    assert summary["status"] == "accepted"
    assert summary["fallback_reason"] == ""
    assert summary["accepted_case_count"] == 2
    assert summary["gap_count"] == 0
    assert summary["repair_shard_count"] == 1
    assert successful_client.call_count == 1
    assert repair_client.call_count == 2
    assert serial_client.call_count == 0
    assert parallel_stream_call_count == 2
    assert "局部补生成仍在执行 (0/1)，连接保持活跃" in "".join(chunks)
    assert "回退串行批次生成" not in "".join(chunks)

    payloads = _streamed_case_payloads(chunks)
    streamed_descriptions = {
        str(case.get("description") or "")
        for payload in payloads
        for case in payload
    }
    assert successful_case["description"] in streamed_descriptions
    assert repaired_case["description"] in streamed_descriptions
