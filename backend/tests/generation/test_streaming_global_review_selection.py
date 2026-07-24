from __future__ import annotations

from typing import Any

from modules.test_generation_components.postprocess.json_processing import deduplicate_test_cases
from modules.test_generation_components.postprocess.streaming_case_keys import (
    candidate_identity_key,
    case_signature,
)
from modules.test_generation_components.postprocess.streaming_global_review_selection import (
    finalize_global_review_selection,
)


def _case(case_id: str, description: str, *, test_input: str = "input") -> dict[str, Any]:
    return {
        "id": case_id,
        "test_module": "module",
        "description": description,
        "preconditions": ["prepared"],
        "steps": ["execute"],
        "test_input": test_input,
        "expected_result": "observable result",
        "priority": "P1",
    }


def test_global_review_selection_preserves_model_selected_order_without_rule_caps() -> None:
    cases = [
        _case("TC-001", "same requirement positive path", test_input="A"),
        _case("TC-002", "same requirement boundary path", test_input="B"),
        _case("TC-003", "same requirement state path", test_input="C"),
    ]

    selected, trace = finalize_global_review_selection(
        cases,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        include_trace=True,
    )

    assert [item["id"] for item in selected] == ["TC-001", "TC-002", "TC-003"]
    assert trace["summary"]["selection_policy"] == "global_review_then_deterministic_dedup"
    assert trace["summary"]["drop_rule_cap_count"] == 0
    assert trace["summary"]["selected_count"] == 3
    assert all(item["drop_reason"] == "retained_global_selection" for item in trace["decisions"].values())


def test_global_review_selection_only_removes_deterministic_duplicates() -> None:
    original = _case("TC-101", "duplicate case")
    cases = [original, {**original, "id": "TC-101-DUP"}, _case("TC-102", "independent case")]

    selected, trace = finalize_global_review_selection(
        cases,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        include_trace=True,
    )

    assert [item["id"] for item in selected] == ["TC-101", "TC-102"]
    assert trace["summary"]["input_count"] == 3
    assert trace["summary"]["dedup_input_count"] == 2
    assert trace["summary"]["dedup_drop_count"] == 1
    assert trace["summary"]["dropped_count"] == 0
    assert len(trace["dedup_dropped_signatures"]) == 1


def test_global_review_trace_distinguishes_exact_duplicates_by_candidate_key() -> None:
    first = _case("TC-TRACE-001", "same behavior")
    duplicate = {**first, "id": "TC-TRACE-002"}

    selected, trace = finalize_global_review_selection(
        [first, duplicate],
        deduplicate_test_cases_fn=deduplicate_test_cases,
        include_trace=True,
    )

    first_key = candidate_identity_key(first)
    duplicate_key = candidate_identity_key(duplicate)
    assert case_signature(first) == case_signature(duplicate)
    assert first_key != duplicate_key
    assert selected == [first]
    assert trace["selected_candidate_keys"] == [first_key]
    assert trace["dedup_dropped_candidate_keys"] == [duplicate_key]
    assert trace["decisions"][first_key]["exact_signature"] == case_signature(first)


def test_global_review_selection_does_not_restore_candidates_when_selection_is_empty() -> None:
    selected, trace = finalize_global_review_selection(
        [],
        deduplicate_test_cases_fn=deduplicate_test_cases,
        include_trace=True,
    )

    assert selected == []
    assert trace["summary"]["selected_count"] == 0
    assert trace["selected_signatures"] == []


def test_deterministic_dedup_keeps_same_text_with_different_state_and_stage_contracts() -> None:
    first = _case("TC-201", "shared visible description")
    second = _case("TC-202", "shared visible description")
    first["preconditions"] = ["record is draft"]
    second["preconditions"] = ["record is published"]
    first["_semantic"] = {
        "workflow_stage_candidates": [
            {
                "workflow_id": "record_flow",
                "stage_id": "edit",
                "stage_kind": "configure",
                "confidence": 0.9,
                "evidence_verified": True,
            }
        ]
    }
    second["_semantic"] = {
        "workflow_stage_candidates": [
            {
                "workflow_id": "record_flow",
                "stage_id": "publish",
                "stage_kind": "commit",
                "confidence": 0.9,
                "evidence_verified": True,
            }
        ]
    }

    selected = finalize_global_review_selection(
        [first, second],
        deduplicate_test_cases_fn=deduplicate_test_cases,
    )

    assert [item["id"] for item in selected] == ["TC-201", "TC-202"]
