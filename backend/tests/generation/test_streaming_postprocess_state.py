from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_postprocess_state import (
    init_stream_postprocess_state,
)


def test_init_stream_postprocess_state_resolves_non_append_defaults() -> None:
    state = init_stream_postprocess_state(
        parsed_result=[{"id": "TC-001"}, "ignored"],
        append=False,
        expected_count=12,
        existing_unique_count=5,
        generation_mode="",
        generation_coverage_mode="standard_regression",
        generation_target_case_range={},
    )

    assert state.stage_counts == {"primary": 2, "gap": 0, "review": 0}
    assert state.candidate_count_before_review == 1
    assert state.gap_attempts == 0
    assert state.gap_remaining_after_attempts == 0
    assert state.gap_stopped_by_provider_error is False
    assert state.append_target_count == 0
    assert state.reference_count_effective == 12
    assert state.append_final_cap_count == 0
    assert state.expected_count_value == 12
    assert state.generation_coverage_mode == "standard_regression"


def test_init_stream_postprocess_state_preserves_append_and_coverage_floor() -> None:
    state = init_stream_postprocess_state(
        parsed_result=[{"id": "TC-001"}],
        append=True,
        expected_count=90,
        existing_unique_count=70,
        generation_mode="",
        generation_coverage_mode="expanded_regression",
        generation_target_case_range={"min": 96, "max": 120},
    )

    assert state.append_target_count == 20
    assert state.reference_count_effective == 20
    assert state.append_final_cap_count == 20
    assert state.expected_count_value == 90
    assert state.effective_generation_coverage_mode == "expanded_regression"
    assert state.generation_coverage_mode == "expanded_regression"
    assert state.resolved_full_regression_floor == 96
    assert state.explicit_expected_count_floor_preserved is False
