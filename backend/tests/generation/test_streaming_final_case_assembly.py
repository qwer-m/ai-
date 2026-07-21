from __future__ import annotations

from typing import Any

from modules.test_generation_components.postprocess.streaming_final_case_assembly import (
    assemble_final_cases,
)


def _case(case_id: str, description: str) -> dict[str, Any]:
    return {
        "id": case_id,
        "test_module": "course_schedule",
        "description": description,
        "preconditions": ["teacher is logged in"],
        "steps": ["open schedule page", "save course schedule"],
        "test_input": f"input for {case_id}",
        "expected_result": f"{description} completes with a verifiable result",
        "priority": "P1",
        "meta": {"source": "review"},
        "priority_conflict_reason": "debug-only",
    }


def _reorder(cases: list[dict[str, Any]], *, start_id: int, renumber_ids: bool) -> list[dict[str, Any]]:
    if not renumber_ids:
        return list(cases)
    output: list[dict[str, Any]] = []
    for offset, case in enumerate(cases):
        copied = dict(case)
        copied["id"] = f"TC-{start_id + offset:03d}"
        output.append(copied)
    return output


def test_assemble_final_cases_strips_meta_and_reports_final_count() -> None:
    result = assemble_final_cases(
        parsed_result=[
            _case("TC-010", "schedule save success creates record"),
            _case("TC-011", "schedule save failure exposes validation reason"),
        ],
        requirement="course schedule must support save success and save failure validation",
        start_id=10,
        effective_generation_coverage_mode="standard_regression",
        generation_coverage_mode="standard_regression",
        review_candidate_cases=[],
        review_selected_count=3,
        workflow_blueprints=[],
        trusted_workflow_contracts=[],
        current_requirement_workflow_blueprints=[],
        authoritative_workflow_blueprints=[],
        flow_project_profile={},
        project_profile={},
        reorder_cases_by_closed_loop_fn=_reorder,
        govern_cases_by_flow_structure_fn=lambda _requirement, cases, **_kwargs: (
            list(cases),
            {"applied": True, "flow_reordered": False},
        ),
        analyze_case_structure_fn=lambda _requirement, cases, **_kwargs: {
            "rows": [{"case_id": item.get("id")} for item in cases if isinstance(item, dict)]
        },
    )

    assert result.final_count == 2
    assert result.post_review_dedup_drop == 1
    assert result.final_order_flow_governance_summary["applied"] is True
    assert result.execution_plan_summary["functional_phase_coverage"]["applied"] is False
    assert result.final_case_structure["rows"]
    assert all("meta" not in item for item in result.cases)
    assert all("priority_conflict_reason" not in item for item in result.cases)


def test_assemble_final_cases_backfills_review_candidate_source_order_after_renumber() -> None:
    review_candidate = _case("TC-RAW-009", "schedule save success creates record")
    parsed_case = dict(review_candidate)
    parsed_case["id"] = "TC-TEMP"

    result = assemble_final_cases(
        parsed_result=[parsed_case],
        requirement="course schedule must support save success validation",
        start_id=20,
        effective_generation_coverage_mode="standard_regression",
        generation_coverage_mode="standard_regression",
        review_candidate_cases=[review_candidate],
        review_selected_count=1,
        workflow_blueprints=[],
        trusted_workflow_contracts=[],
        current_requirement_workflow_blueprints=[],
        authoritative_workflow_blueprints=[],
        flow_project_profile={},
        project_profile={},
        reorder_cases_by_closed_loop_fn=_reorder,
        govern_cases_by_flow_structure_fn=lambda _requirement, cases, **_kwargs: (
            list(cases),
            {"applied": True, "flow_reordered": False},
        ),
        analyze_case_structure_fn=lambda _requirement, cases, **_kwargs: {
            "rows": [{"case_id": item.get("id")} for item in cases if isinstance(item, dict)]
        },
    )

    assert result.cases[0]["id"] == "TC-020"
    assert result.cases[0]["candidate_index"] == 1
    assert result.cases[0]["origin_candidate_index"] == 1
    assert result.cases[0]["origin_case_id"] == "TC-RAW-009"
    assert result.cases[0]["origin_source_stage"] == "review_candidate"
