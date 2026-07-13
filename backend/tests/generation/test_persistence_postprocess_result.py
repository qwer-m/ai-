from __future__ import annotations

from modules.test_generation_components.legacy.stream.persistence_postprocess_result import (
    unpack_stream_postprocess_result,
)


def test_unpack_stream_postprocess_result_preserves_structured_payloads() -> None:
    timing_events = [{"stage": "prepare", "duration_ms": 3}]

    payload = unpack_stream_postprocess_result(
        {
            "cases": [{"id": "TC-001", "title": "登录成功"}],
            "stage_counts": {"review": 1},
            "coverage": {"covered": 1},
            "convergence_debug": {"status": "stable"},
            "generation_summary": {"status": "complete"},
            "review_decision_summary": {"execution_plan": {"steps": 2}},
            "review_decision_table": [{"case_id": "TC-001"}, "invalid"],
            "judge_decision_table": [{"case_id": "TC-001", "passed": True}, None],
            "feedback_control_debug": {"biz": "login"},
            "judge_summary": {"passed": 1},
            "timing_events": [{"stage": "postprocess", "duration_ms": 5}],
        },
        generation_timing_events=timing_events,
        sanitize_timing_events_fn=lambda events: [
            item for item in events if isinstance(item, dict)
        ],
    )

    assert payload.parsed_result == [{"id": "TC-001", "title": "登录成功"}]
    assert payload.stage_counts == {"review": 1}
    assert payload.coverage_payload == {"covered": 1}
    assert payload.convergence_payload == {"status": "stable"}
    assert payload.generation_summary_payload == {"status": "complete"}
    assert payload.review_decision_summary_payload == {"execution_plan": {"steps": 2}}
    assert payload.review_decision_table_payload == [{"case_id": "TC-001"}]
    assert payload.judge_decision_table_payload == [{"case_id": "TC-001", "passed": True}]
    assert payload.feedback_control_debug_payload == {"biz": "login"}
    assert payload.judge_summary_payload == {"passed": 1}
    assert payload.generation_timing_events is timing_events
    assert payload.generation_timing_events[-1] == {
        "stage": "postprocess",
        "duration_ms": 5,
    }


def test_unpack_stream_postprocess_result_accepts_legacy_list_result() -> None:
    cases = [{"id": "TC-002", "title": "登录失败"}]

    payload = unpack_stream_postprocess_result(
        cases,
        generation_timing_events=[],
        sanitize_timing_events_fn=lambda events: [],
    )

    assert payload.parsed_result is cases
    assert payload.stage_counts == {}
    assert payload.review_decision_table_payload == []
