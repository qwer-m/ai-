from __future__ import annotations

from typing import Any

from modules.test_generation_components.postprocess.streaming_initial_parse_stage import (
    run_initial_parse_stage,
)


def _valid_case() -> dict[str, Any]:
    return {
        "id": "TC-001",
        "test_module": "Course scheduling",
        "description": "Save a course schedule with required fields",
        "expected_result": "System saves the course schedule and shows a success confirmation",
        "priority": "P1",
        "steps": ["Open the schedule form", "Fill required fields", "Save the schedule"],
        "preconditions": ["Teacher has logged in"],
        "test_input": "Course schedule values",
    }


def test_run_initial_parse_stage_filters_cases_and_records_timing() -> None:
    events: list[dict[str, Any]] = []
    raw_payload = "[case payload]"
    valid_case = _valid_case()
    invalid_case = {**valid_case, "id": "TC-002", "steps": []}

    def record_timing_event(stage: str, started_at: float, **fields: Any) -> dict[str, Any]:
        event = {"stage": stage, **fields}
        events.append(event)
        return event

    result = run_initial_parse_stage(
        full_content=raw_payload,
        requirement="Course scheduling should cover the save workflow",
        clean_and_parse_json_fn=lambda content: [valid_case, invalid_case],
        normalize_json_structure_fn=lambda value: value,
        deduplicate_test_cases_fn=lambda cases: cases,
        analyze_coverage_fn=lambda requirement, cases: {"rule_diagnostics": []},
        record_timing_event_fn=record_timing_event,
    )

    assert len(result.parsed_result) == 1
    assert result.parsed_result[0]["id"] == "TC-001"
    assert result.low_quality_filter_stats.postprocess_filter_drop_total == 1
    assert result.low_quality_filter_stats.low_quality_structural_dropped_total == 1
    assert events == [
        {
            "stage": "postprocess_initial_parse_filter",
            "primary_count": 1,
            "input_chars": len(raw_payload),
        }
    ]


def test_run_initial_parse_stage_treats_non_list_payload_as_empty() -> None:
    events: list[dict[str, Any]] = []

    result = run_initial_parse_stage(
        full_content="{not a case list}",
        requirement="Course scheduling",
        clean_and_parse_json_fn=lambda content: {"cases": []},
        normalize_json_structure_fn=lambda value: value,
        deduplicate_test_cases_fn=lambda cases: cases,
        analyze_coverage_fn=lambda requirement, cases: {"rule_diagnostics": []},
        record_timing_event_fn=lambda stage, started_at, **fields: events.append(
            {"stage": stage, **fields}
        )
        or events[-1],
    )

    assert result.parsed_result == []
    assert result.low_quality_filter_stats.postprocess_filter_drop_total == 0
    assert events == [
        {
            "stage": "postprocess_initial_parse_filter",
            "primary_count": 0,
            "input_chars": len("{not a case list}"),
        }
    ]
