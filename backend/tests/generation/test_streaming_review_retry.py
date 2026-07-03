from __future__ import annotations

import json

from modules.test_generation_components.postprocess.json_processing import (
    clean_and_parse_json,
    normalize_json_structure,
)
from modules.test_generation_components.postprocess.streaming_case_keys import case_signature
from modules.test_generation_components.postprocess.streaming_review_retry import (
    analyze_review_retry_payload,
    build_compact_review_retry_prompt,
    build_review_protocol_repair_prompt,
    count_review_dropped_reason_payload,
    default_review_llm_runtime_debug,
    normalize_review_payload_invalid_reason,
    resolve_review_fallback_models,
    review_payload_has_selection_signal,
    review_retry_payload_debug_counts,
)


def _case(case_id: str, description: str, *, priority: str = "P1") -> dict[str, str]:
    return {
        "id": case_id,
        "test_module": "Course",
        "description": description,
        "expected_result": f"{description} succeeds",
        "test_input": description,
        "priority": priority,
        "priority_final": priority,
    }


def test_default_review_llm_runtime_debug_returns_independent_mutable_fields() -> None:
    first = default_review_llm_runtime_debug()
    second = default_review_llm_runtime_debug()

    assert first["invoked"] is False
    assert first["final_source"] == "review_selector"
    assert first["final_payload_consistent"] is True
    assert first["retry_attempts"] == []
    assert first["primary_response_metadata"] == {}
    assert first["reason_repair_response_metadata"] == {}

    first["retry_attempts"].append({"model": "deepseek-chat"})
    first["final_selected_and_dropped_overlap_case_ids"].append("TC-001")
    first["primary_response_metadata"]["request_id"] = "REQ-1"
    first["reason_repair_response_metadata"]["request_id"] = "REQ-2"

    assert second["retry_attempts"] == []
    assert second["final_selected_and_dropped_overlap_case_ids"] == []
    assert second["primary_response_metadata"] == {}
    assert second["reason_repair_response_metadata"] == {}
    assert first["retry_attempts"] is not second["retry_attempts"]
    assert first["primary_response_metadata"] is not second["primary_response_metadata"]


def test_analyze_review_retry_payload_maps_selection_and_counts_alias_drop_ids() -> None:
    first = _case("TC-001", "create a course", priority="P0")
    second = _case("TC-002", "delete a course")
    payload = {
        "kept_case_ids": ["TC-001"],
        "dropped": [
            {"caseId": "TC-002", "reason": "duplicate"},
            {"case_id": "TC-MISSING", "reason": "low_value"},
            {"case_id": "TC-EMPTY-REASON", "reason": ""},
        ],
    }

    result = analyze_review_retry_payload(
        json.dumps(payload),
        candidate_cases=[first, second],
        parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        reason_origin="fallback_llm",
    )

    assert result["invalid_reason"] == ""
    assert result["parse_success"] is True
    assert result["mapped"] == [first]
    assert result["mapped_signatures"] == {case_signature(first)}
    assert result["dropped_reason_map"] == {case_signature(second): "duplicate"}
    assert result["dropped_reason_origin_map"] == {case_signature(second): "fallback_llm"}
    assert result["dropped_reason_payload_count"] == 2
    assert result["dropped_reason_unmapped_count"] == 1
    assert result["payload_has_selection_signal"] is True
    assert count_review_dropped_reason_payload(result["payload"]) == 2
    assert review_payload_has_selection_signal(result["payload"]) is True
    assert review_retry_payload_debug_counts(result)["mapped_count"] == 1


def test_resolve_review_fallback_models_deduplicates_deepseek_chain() -> None:
    class _Client:
        model = "deepseek-reasoner"
        turbo_model = "deepseek-chat"

    assert resolve_review_fallback_models(
        client=_Client(),
        primary_model_name="deepseek-reasoner",
    ) == ["deepseek-chat", "deepseek-reasoner"]


def test_build_review_protocol_repair_prompt_limits_candidate_ids_and_reasons() -> None:
    prompt = build_review_protocol_repair_prompt(
        review_prompt="ORIGINAL REVIEW PROMPT",
        candidate_cases=[
            _case("TC-001", "create a course"),
            _case("TC-002", "delete a course"),
        ],
        drop_reasons=("duplicate", "coverage_redundant"),
        max_candidates=1,
    )

    assert "ORIGINAL REVIEW PROMPT" in prompt
    assert "PROTOCOL FIX (MANDATORY)" in prompt
    assert '"kept_case_ids"' in prompt
    assert '"TC-001"' in prompt
    assert '"TC-002"' not in prompt
    assert '"duplicate","coverage_redundant"' in prompt


def test_analyze_review_retry_payload_keeps_scalar_id_list_mappable() -> None:
    first = _case("TC-001", "create a course")
    second = _case("TC-002", "delete a course")

    result = analyze_review_retry_payload(
        json.dumps(["TC-002", "TC-002", "TC-MISSING"]),
        candidate_cases=[first, second],
        parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
    )

    assert result["invalid_reason"] == ""
    assert result["parsed_type"] == "list"
    assert result["parsed_len"] == 3
    assert result["mapped"] == [second]
    assert result["mapped_signatures"] == {case_signature(second)}
    assert result["payload_has_selection_signal"] is False


def test_analyze_review_retry_payload_unwraps_generic_selection_container() -> None:
    first = _case("TC-001", "create a course")
    second = _case("TC-002", "delete a course")
    payload = {
        "document_type": "spreadsheet",
        "result": {
            "kept_case_ids": ["TC-002"],
            "dropped": [{"case_id": "TC-001", "reason": "duplicate"}],
        },
    }

    result = analyze_review_retry_payload(
        json.dumps(payload),
        candidate_cases=[first, second],
        parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
    )

    assert result["invalid_reason"] == ""
    assert result["payload"] == payload["result"]
    assert result["parsed_type"] == "dict"
    assert result["parsed_len"] == 2
    assert result["mapped"] == [second]
    assert result["dropped_reason_map"] == {case_signature(first): "duplicate"}
    assert result["payload_has_selection_signal"] is True


def test_analyze_review_retry_payload_classifies_empty_error_and_unmapped_payloads() -> None:
    candidate = _case("TC-001", "create a course")

    empty_result = analyze_review_retry_payload(
        "",
        candidate_cases=[candidate],
        parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
    )
    assert empty_result["invalid_reason"] == "empty_response"
    assert empty_result["parse_success"] is False

    error_result = analyze_review_retry_payload(
        "Error: Empty response from model",
        candidate_cases=[candidate],
        parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
    )
    assert error_result["invalid_reason"] == "error_response"
    assert error_result["parse_success"] is False

    unmapped_result = analyze_review_retry_payload(
        json.dumps({"kept_case_ids": ["TC-MISSING"]}),
        candidate_cases=[candidate],
        parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
    )
    assert unmapped_result["invalid_reason"] == "no_mapped_ids"
    assert unmapped_result["payload_has_selection_signal"] is True


def test_normalize_review_payload_invalid_reason_handles_schema_and_no_signal() -> None:
    assert (
        normalize_review_payload_invalid_reason(
            "plain text",
            "plain text",
            parsed_type="str",
            mapped_count=0,
            payload_has_selection_signal=False,
        )
        == "schema_not_dict_or_list"
    )
    assert (
        normalize_review_payload_invalid_reason(
            "{}",
            {},
            parsed_type="dict",
            mapped_count=0,
            payload_has_selection_signal=False,
        )
        == "no_mapped_and_no_selection_signal"
    )


def test_build_compact_review_retry_prompt_uses_candidate_facts_and_limits_ids() -> None:
    first = _case("TC-001", "create a course", priority="P0")
    second = _case("TC-002", "delete a course")

    prompt = build_compact_review_retry_prompt(
        [first, second],
        target_min_count=2,
        target_max_count=1,
        drop_reasons=("duplicate", "low_value"),
        max_candidates=1,
    )

    assert "REVIEW COMPACT RETRY." in prompt
    assert "Return STRICT compact JSON only" in prompt
    assert "Keep between 2 and 2 cases when possible." in prompt
    assert "Allowed reasons: duplicate, low_value." in prompt
    assert '"TC-001"' in prompt
    assert "delete a course" not in prompt
    assert '"id":"TC-001"' in prompt
    assert '"module":"Course"' in prompt
    assert '"expected_result":"create a course succeeds"' in prompt
    assert '"priority":"P0"' in prompt


def test_build_compact_review_retry_prompt_uses_shared_aliases_and_structured_fields() -> None:
    case = {
        "caseId": "TC-ALIAS",
        "testModule": "Learning",
        "title": "resume unfinished recommendation",
        "precondition": "student has unfinished task",
        "testSteps": [{"step": "open recommendation"}, {"text": "resume answer"}],
        "testData": {"student_id": "S-100", "wrong_question_id": "W-200"},
        "assertion": "existing answer is preserved",
        "finalPriority": "P0",
    }

    prompt = build_compact_review_retry_prompt(
        [case],
        target_min_count=1,
        target_max_count=1,
        max_candidates=5,
    )

    assert '"id":"TC-ALIAS"' in prompt
    assert '"module":"Learning"' in prompt
    assert '"description":"resume unfinished recommendation"' in prompt
    assert '"preconditions":"student has unfinished task"' in prompt
    assert '"steps":["open recommendation","resume answer"]' in prompt
    assert '"test_input":"S-100 W-200"' in prompt
    assert '"expected_result":"existing answer is preserved"' in prompt
    assert '"priority":"P0"' in prompt
