from __future__ import annotations

import json
from typing import Any

from modules.test_generation_components.postprocess.streaming_final_recovery_stage import (
    run_final_recovery_stage,
)
from modules.test_generation_components.postprocess.streaming_final_floor_recovery import (
    recover_final_floor_after_conflict_filter,
)


def _case(case_id: str, description: str) -> dict[str, Any]:
    return {
        "id": case_id,
        "test_module": "course_schedule",
        "description": description,
        "preconditions": ["teacher is logged in with schedule permission"],
        "steps": ["open schedule page", "fill schedule data", "save schedule"],
        "test_input": f"schedule data for {case_id}",
        "expected_result": f"{description} is saved and visible in schedule list",
        "priority": "P1",
    }


def _drain(gen):
    chunks: list[str] = []
    while True:
        try:
            chunks.append(next(gen))
        except StopIteration as stop:
            return chunks, stop.value


class _SupplementClient:
    def generate_response(self, *_args, **_kwargs):
        return json.dumps(
            [
                _case(
                    "TC-002",
                    "schedule save failure exposes validation reason",
                )
            ],
            ensure_ascii=False,
        )


def test_final_recovery_stage_runs_shortfall_supplement_and_records_timing() -> None:
    timing_events: list[dict[str, Any]] = []
    quality_details: list[dict[str, Any]] = []

    def record_timing(stage: str, _started_at: float, **fields: Any) -> dict[str, Any]:
        payload = {"stage": stage, **fields}
        timing_events.append(payload)
        return payload

    chunks, result = _drain(
        run_final_recovery_stage(
            client=_SupplementClient(),
            db=None,
            requirement="course schedule must support save success and save failure validation",
            kb_context="",
            parsed_result=[_case("TC-001", "schedule save success creates record")],
            flow_governance_summary={"applied": True},
            final_target_floor_count=2,
            review_candidate_cases=[],
            review_selection_input=[],
            candidate_cases=[],
            candidate_count_before_review=1,
            expected_count=2,
            expected_count_value=2,
            effective_generation_coverage_mode="standard_regression",
            resolved_full_regression_floor=0,
            append=False,
            project_profile={},
            flow_project_profile={},
            start_id=1,
            feedback_control_state={},
            requirement_semantics_context={},
            fact_profile={},
            low_quality_drop_details=quality_details,
            clean_and_parse_json_fn=json.loads,
            normalize_json_structure_fn=lambda payload: payload,
            deduplicate_test_cases_fn=lambda cases: list(cases),
            reorder_cases_by_closed_loop_fn=lambda cases, **_: list(cases),
            analyze_case_structure_fn=lambda *_args, **_kwargs: {"rows": []},
            analyze_coverage_fn=lambda *_args, **_kwargs: {},
            govern_cases_by_flow_structure_fn=lambda _requirement, cases, **_kwargs: (
                list(cases),
                {"applied": True},
            ),
            record_timing_event_fn=record_timing,
        )
    )

    assert any("Final shortfall supplement started" in chunk for chunk in chunks)
    assert any("Final shortfall supplement added 1 cases" in chunk for chunk in chunks)
    assert result.final_shortfall_supplement_attempted is True
    assert result.final_shortfall_supplement_applied is True
    assert result.final_shortfall_supplement_count == 1
    assert result.final_floor_recovery_applied is True
    assert result.final_floor_recovery_reason == "final_shortfall_supplement_generated"
    assert len(result.cases) == 2
    assert timing_events[-1]["stage"] == "final_shortfall_supplement"
    assert timing_events[-1]["applied"] is True
    assert quality_details == []


def test_post_conflict_floor_recovery_rechecks_semantic_duplicates() -> None:
    retained = _case("TC-001", "schedule save success creates record")
    duplicate_candidate = {
        **_case("TC-002", "schedule save success creates record"),
        "test_module": "schedule management",
        "test_input": "valid schedule for duplicate candidate",
        "expected_result": "new schedule is persisted and visible on the list",
    }

    result = recover_final_floor_after_conflict_filter(
        requirement="course schedule must support save success and save failure validation",
        kb_context="",
        parsed_result=[retained],
        flow_governance_summary={"applied": True},
        final_target_floor_count=2,
        final_floor_recovered_count=0,
        effective_generation_coverage_mode="full_functional_regression",
        review_candidate_cases=[duplicate_candidate],
        review_selection_input=[],
        candidate_cases=[],
        fact_profile={},
        flow_project_profile={},
        start_id=1,
        feedback_control_state={},
        requirement_semantics_context={},
        analyze_coverage_fn=lambda *_args, **_kwargs: {},
        filter_conflicting_cases_fn=lambda cases, **_kwargs: (list(cases), 0),
        govern_cases_by_flow_structure_fn=lambda _requirement, cases, **_kwargs: (
            list(cases),
            {"applied": True},
        ),
        deduplicate_test_cases_fn=lambda cases: list(cases),
        reorder_cases_by_closed_loop_fn=lambda cases, **_kwargs: list(cases),
        rank_case_fn=lambda *_args, **_kwargs: (1,),
    )

    assert result.applied is False
    assert result.reason == "post_conflict_recovery_rejected_by_judge"
    assert [case["id"] for case in result.cases] == ["TC-001"]
