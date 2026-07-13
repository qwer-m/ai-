from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_postprocess_result_payload import (
    build_stream_postprocess_result_payload,
)


def test_build_stream_postprocess_result_payload_injects_feedback_debug() -> None:
    payload = build_stream_postprocess_result_payload(
        cases=[{"id": "TC-001"}],
        stage_counts={"primary": 1},
        coverage={"kind": "coverage_check"},
        convergence_debug={"converged": True},
        generation_summary={"status": "completed"},
        review_decision_summary={"selected": 1},
        review_decision_table=[{"case_id": "TC-001"}],
        judge_summary={"pass_count": 1},
        judge_decision_table=[{"case_id": "TC-001", "decision": "pass"}],
        feedback_control_debug_builder_fn=lambda **kwargs: {
            "mode": kwargs["generation_coverage_mode"],
            "target": kwargs["generation_target_case_range"],
        },
        control_state={"enabled": True},
        generation_coverage_mode="expanded_regression",
        generation_target_case_range={"min": 20},
        timing_events="not-a-list",
    )

    assert payload["cases"] == [{"id": "TC-001"}]
    assert payload["timing_events"] == []
    assert payload["feedback_control_debug"] == {
        "mode": "expanded_regression",
        "target": {"min": 20},
    }
