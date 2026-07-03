from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_generation_target import (
    resolve_generation_target_satisfaction,
    resolve_underfill_diagnostics,
)


def test_resolve_generation_target_satisfaction_flags_underfilled_target() -> None:
    state = resolve_generation_target_satisfaction(
        generation_target_case_range={"min": 80, "max": 100},
        expected_count=90,
        reference_count_effective=90,
        generation_coverage_mode="standard_regression",
        candidate_count_before_review=88,
        post_review_dedup_drop=4,
        final_count=70,
    )

    assert state["recommended_range"] == "80-100"
    assert state["target_final_count"] == 90
    assert state["soft_min_count"] == 72
    assert state["underfilled"] is True
    assert state["target_warning"] is True


def test_resolve_underfill_diagnostics_identifies_final_stage_over_pruning() -> None:
    state = resolve_underfill_diagnostics(
        underfilled=True,
        valid_unique_candidate_count=90,
        hard_min_count=70,
        scenario_duplicate_pruned_count=12,
        review_selector_pruned_count=4,
        duplicate_pruned_count=0,
        quality_rejected_count=1,
        final_non_judge_drop_count=18,
        min_acceptable_final=80,
        final_count=68,
    )

    assert state == {
        "underfill_reason": "scenario_cap_over_pruned",
        "underfill_root_cause": "final_stage_over_pruning",
        "underfill_level": "moderate",
    }
