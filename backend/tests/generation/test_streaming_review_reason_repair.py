from __future__ import annotations

import json

from modules.test_generation_components.postprocess.json_processing import clean_and_parse_json
from modules.test_generation_components.postprocess.streaming_case_keys import case_signature
from modules.test_generation_components.postprocess.streaming_review_reason_repair import (
    analyze_reason_repair_payload,
    build_compact_reason_repair_prompt,
    build_reason_repair_candidates,
    reason_repair_payload_debug_counts,
)


def _case(case_id: str, description: str, *, priority: str = "P1") -> dict[str, str]:
    return {
        "id": case_id,
        "test_module": "Course",
        "description": description,
        "expected_result": f"{description} succeeds",
        "test_input": description,
        "priority": "P2",
        "priority_final": priority,
    }


def test_build_compact_reason_repair_prompt_uses_shared_brief_for_all_candidates() -> None:
    first = _case("TC-001", "create a course", priority="P0")
    second = _case("TC-002", "delete a course")

    prompt = build_compact_reason_repair_prompt(
        [first, second],
        drop_reasons=("duplicate", "low_value"),
    )

    assert "REVIEW REASON REPAIR ONLY." in prompt
    assert "Do NOT select or rewrite cases." in prompt
    assert "Allowed reasons: duplicate, low_value." in prompt
    assert '"TC-001"' in prompt
    assert '"TC-002"' in prompt
    assert '"id":"TC-001"' in prompt
    assert '"module":"Course"' in prompt
    assert '"description":"create a course"' in prompt
    assert '"expected_result":"create a course succeeds"' in prompt
    assert '"priority":"P0"' in prompt
    assert "delete a course" in prompt


def test_build_reason_repair_candidates_uses_case_aliases_and_skips_missing_ids() -> None:
    alias_case = {
        "caseId": "TC-ALIAS",
        "testModule": "Learning",
        "title": "resume recommendation",
        "assertion": "recommendation resumes",
        "testData": "student S-100",
        "finalPriority": "P0",
    }
    missing_id = {
        "test_module": "Learning",
        "description": "no id",
        "expected_result": "not included",
    }

    candidates = build_reason_repair_candidates([alias_case, missing_id])

    assert candidates == [
        {
            "id": "TC-ALIAS",
            "module": "Learning",
            "description": "resume recommendation",
            "expected_result": "recommendation resumes",
            "priority": "P0",
        }
    ]


def test_analyze_reason_repair_payload_maps_allowed_reasons_and_counts_unmapped() -> None:
    first = _case("TC-001", "create a course", priority="P0")
    second = _case("TC-002", "delete a course")
    payload = {
        "dropped": [
            {"case_id": "TC-001", "reason": "duplicate"},
            {"caseId": "TC-002", "reason": "coverage_protected_omitted"},
            {"case_id": "TC-MISSING", "reason": "low_value"},
            {"case_id": "TC-EMPTY-REASON", "reason": ""},
            "bad item",
        ]
    }

    result = analyze_reason_repair_payload(
        json.dumps(payload),
        missing_reason_cases=[first, second],
        parse_json_fn=clean_and_parse_json,
        reason_origin="fallback_llm",
    )

    assert result["invalid_reason"] == ""
    assert result["parse_success"] is True
    assert result["parsed_type"] == "dict"
    assert result["parsed_len"] == 1
    assert result["mapped_count"] == 1
    assert result["dropped_reason_map"] == {case_signature(first): "duplicate"}
    assert result["dropped_reason_origin_map"] == {case_signature(first): "fallback_llm"}
    assert result["dropped_reason_payload_count"] == 3
    assert result["dropped_reason_unmapped_count"] == 2
    assert result["invalid_reason_payload_count"] == 1
    assert result["unknown_case_id_count"] == 1
    assert result["missing_field_payload_count"] == 2
    assert reason_repair_payload_debug_counts(result)["mapped_count"] == 1


def test_analyze_reason_repair_payload_does_not_override_existing_reason_map() -> None:
    first = _case("TC-001", "create a course")
    second = _case("TC-002", "delete a course")
    existing = {case_signature(first): "coverage_redundant"}
    payload = {
        "dropped": [
            {"case_id": "TC-001", "reason": "duplicate"},
            {"case_id": "TC-002", "reason": "selection_tradeoff_omitted"},
        ]
    }

    result = analyze_reason_repair_payload(
        json.dumps(payload),
        missing_reason_cases=[first, second],
        parse_json_fn=clean_and_parse_json,
        existing_drop_reason_map=existing,
    )

    assert result["invalid_reason"] == ""
    assert result["mapped_count"] == 1
    assert result["dropped_reason_map"] == {
        case_signature(second): "selection_tradeoff_omitted"
    }
    assert result["dropped_reason_origin_map"] == {case_signature(second): "llm"}
    assert result["skipped_existing_count"] == 1
    assert result["dropped_reason_unmapped_count"] == 1


def test_analyze_reason_repair_payload_classifies_empty_schema_and_unmapped_payloads() -> None:
    candidate = _case("TC-001", "create a course")

    empty_result = analyze_reason_repair_payload(
        "",
        missing_reason_cases=[candidate],
        parse_json_fn=clean_and_parse_json,
    )
    assert empty_result["invalid_reason"] == "empty_response"
    assert empty_result["parse_success"] is False

    list_result = analyze_reason_repair_payload(
        json.dumps([{"case_id": "TC-001", "reason": "duplicate"}]),
        missing_reason_cases=[candidate],
        parse_json_fn=clean_and_parse_json,
    )
    assert list_result["invalid_reason"] == "schema_not_dict"
    assert list_result["parsed_type"] == "list"
    assert list_result["parsed_len"] == 1

    unmapped_result = analyze_reason_repair_payload(
        json.dumps({"dropped": [{"case_id": "TC-MISSING", "reason": "duplicate"}]}),
        missing_reason_cases=[candidate],
        parse_json_fn=clean_and_parse_json,
    )
    assert unmapped_result["invalid_reason"] == "no_mapped_reasons"
    assert unmapped_result["dropped_reason_payload_count"] == 1
    assert unmapped_result["dropped_reason_unmapped_count"] == 1
    assert unmapped_result["unknown_case_id_count"] == 1
