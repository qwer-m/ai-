import json
from typing import Any

from modules.test_generation_components.postprocess.streaming_shortfall_supplement import (
    build_final_shortfall_supplement_prompt,
    resolve_final_shortfall_supplement_size,
    run_final_shortfall_supplement,
    should_attempt_final_shortfall_supplement,
)


def _case(case_id: str, description: str, module: str = "forum") -> dict[str, Any]:
    return {
        "id": case_id,
        "description": description,
        "test_module": module,
        "preconditions": ["user is logged in"],
        "steps": ["open forum", "perform action"],
        "test_input": description,
        "expected_result": f"{description} succeeds",
        "priority": "P1",
    }


class _BatchClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.last_response_metadata: dict[str, Any] = {}

    def generate_response(self, user_input: str, system_prompt: str = "", **kwargs: Any) -> str:
        self.calls.append(
            {
                "user_input": user_input,
                "system_prompt": system_prompt,
                "max_tokens": kwargs.get("max_tokens"),
                "task_type": kwargs.get("task_type"),
            }
        )
        idx = len(self.calls) - 1
        payload = self.responses[idx] if idx < len(self.responses) else []
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self.last_response_metadata = {
            "model": "glm-5.1",
            "http_status": 200,
            "finish_reason": "stop",
            "content_len": len(text),
            "max_tokens": kwargs.get("max_tokens"),
        }
        return text


def test_should_attempt_final_shortfall_supplement_requires_floor_and_shortfall() -> None:
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="full_functional_regression",
        expected_count_value=0,
        final_target_floor_count=40,
        append=False,
        current_count=12,
    ) is True
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="standard_regression",
        expected_count_value=30,
        final_target_floor_count=30,
        append=False,
        current_count=20,
    ) is True


def test_should_attempt_final_shortfall_supplement_allows_append_shortfall() -> None:
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="full_functional_regression",
        expected_count_value=0,
        final_target_floor_count=40,
        append=True,
        current_count=12,
    ) is True
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="full_functional_regression",
        expected_count_value=0,
        final_target_floor_count=40,
        append=False,
        current_count=40,
    ) is False
    assert should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode="standard_regression",
        expected_count_value=0,
        final_target_floor_count=40,
        append=False,
        current_count=12,
    ) is False


def test_resolve_final_shortfall_supplement_size_keeps_existing_buffer_policy() -> None:
    assert resolve_final_shortfall_supplement_size(current_count=38, target_floor_count=40) == {
        "shortfall": 2,
        "buffer": 3,
        "needed": 5,
    }
    assert resolve_final_shortfall_supplement_size(current_count=10, target_floor_count=40) == {
        "shortfall": 30,
        "buffer": 8,
        "needed": 30,
    }


def test_final_shortfall_prompt_includes_missing_rule_text_evidence() -> None:
    prompt = build_final_shortfall_supplement_prompt(
        requirement="论坛优化需求",
        final_cases=[_case("TC-001", "existing case")],
        current_count=1,
        target_floor_count=3,
        supplement_needed=2,
        analyze_coverage_fn=lambda *_args, **_kwargs: {
            "missing_rules": ["RULE-023"],
            "missing_types": {"boundary": ["RULE-039"]},
            "rule_diagnostics": [
                {
                    "rule_id": "RULE-023",
                    "rule_text": "浏览量不可点击",
                    "missing_types": ["happy"],
                    "covered": False,
                    "blocking": True,
                    "confidence": "high",
                    "source_type": "confirmed_requirement",
                },
                {
                    "rule_id": "RULE-039",
                    "rule_text": "超过3条信息，显示按钮：展开N条回复",
                    "missing_types": ["boundary"],
                    "covered": True,
                    "blocking": True,
                    "confidence": "high",
                    "source_type": "confirmed_requirement",
                },
            ],
        },
    )

    assert "missing_rule_evidence" in prompt
    assert "浏览量不可点击" in prompt
    assert "超过3条信息" in prompt


def test_run_final_shortfall_supplement_accepts_wrapper_payload_and_records_debug() -> None:
    client = _BatchClient(
        [
            {
                "cases": [
                    _case("TC-002", "reply notification covers nested comment", "message"),
                    _case("TC-003", "forum post syncs to profile feed", "profile"),
                ]
            }
        ]
    )

    result = run_final_shortfall_supplement(
        client=client,
        db=None,
        requirement="forum optimization requires reply notification and profile feed sync",
        current_shortfall_count=1,
        target_floor_count=3,
        supplement_needed=2,
        parsed_result=[_case("TC-001", "forum post creates visible detail", "forum")],
        kb_context="",
        fact_profile={},
        flow_project_profile={},
        effective_generation_coverage_mode="full_functional_regression",
        start_id=1,
        final_floor_recovered_count=0,
        clean_and_parse_json_fn=json.loads,
        normalize_json_structure_fn=lambda payload: payload,
        deduplicate_test_cases_fn=lambda cases: list(cases),
        analyze_coverage_fn=lambda *_args, **_kwargs: {"missing_rules": ["reply notification"]},
        govern_cases_by_flow_structure_fn=lambda _requirement, cases, **_kwargs: (
            list(cases),
            {"applied": True},
        ),
    )

    assert result.applied is True
    assert result.supplement_count == 2
    assert len(result.cases) == 3
    assert len(client.calls) == 1
    assert client.calls[0]["task_type"] == "generation"
    assert client.calls[0]["max_tokens"] == 2500
    assert "cases array" in client.calls[0]["system_prompt"]
    assert result.debug["batches"][0]["parsed_source"] == "dict.cases"
    assert result.debug["batches"][0]["parsed_count"] == 2
    assert result.debug["batches"][0]["response_preview"].startswith("{")
    assert result.debug["batches"][0]["metadata"]["model"] == "glm-5.1"
    supplement_by_origin = {
        item["origin_case_id"]: item
        for item in result.cases
        if item.get("origin_source_stage") == "final_shortfall_supplement"
    }
    assert supplement_by_origin["TC-002"]["origin_candidate_index"] == 1
    assert supplement_by_origin["TC-002"]["origin_batch_index"] == 1
    assert supplement_by_origin["TC-002"]["origin_batch_case_index"] == 1
    assert supplement_by_origin["TC-003"]["origin_candidate_index"] == 2
    assert "candidate_index" not in supplement_by_origin["TC-002"]


def test_run_final_shortfall_supplement_splits_large_request_and_keeps_failure_debug() -> None:
    client = _BatchClient(["Error: HTTP 504 - timeout", []])

    result = run_final_shortfall_supplement(
        client=client,
        db=None,
        requirement="forum optimization requires broad regression coverage",
        current_shortfall_count=10,
        target_floor_count=40,
        supplement_needed=22,
        parsed_result=[_case("TC-001", "forum post creates visible detail", "forum")],
        kb_context="",
        fact_profile={},
        flow_project_profile={},
        effective_generation_coverage_mode="full_functional_regression",
        start_id=1,
        final_floor_recovered_count=0,
        clean_and_parse_json_fn=lambda text: json.loads(text),
        normalize_json_structure_fn=lambda payload: payload,
        deduplicate_test_cases_fn=lambda cases: list(cases),
        analyze_coverage_fn=lambda *_args, **_kwargs: {},
        govern_cases_by_flow_structure_fn=lambda _requirement, cases, **_kwargs: (
            list(cases),
            {"applied": True},
        ),
    )

    assert result.applied is False
    assert result.reason in {"supplement_error_response", "supplement_empty_case_list"}
    assert result.debug["batch_plan"] == [10, 10, 2]
    assert len(client.calls) == 3
    assert all(call["max_tokens"] <= 7000 for call in client.calls)
    assert result.debug["batches"][0]["starts_error"] is True
    assert result.debug["batches"][0]["error_reason"] == "error_response"
    assert result.debug["batches"][0]["metadata"]["http_status"] == 200
