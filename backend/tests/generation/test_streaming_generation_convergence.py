from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_generation_convergence import (
    derive_convergence_reason_state,
    derive_final_coverage_convergence_inputs,
    resolve_completion_reason_lists,
)


def test_derive_final_coverage_convergence_inputs_records_gap_and_missing_types() -> None:
    state = derive_final_coverage_convergence_inputs(
        pre_priority_coverage={
            "missing_rules": ["MUST_COVER_PAYMENT"],
            "rule_diagnostics": [
                {"rule": "MUST_COVER_PAYMENT", "missing_types": ["boundary"]},
            ],
        },
        reference_count_effective=12,
        final_count=9,
        gap_remaining_after_attempts=3,
        gap_attempts=3,
        gap_stopped_by_provider_error=True,
    )

    assert state["coverage"]["kind"] == "coverage_check"
    assert state["converged"] is False
    assert state["reference_gap"] == 3
    assert state["reasons"] == [
        "coverage_gap_still_exists",
        "gap_attempt_limit_reached",
        "gap_stopped_by_provider_error",
        "coverage_missing_rules",
        "coverage_missing_types",
    ]


def test_derive_convergence_reason_state_counts_filter_and_dedup_signals() -> None:
    state = derive_convergence_reason_state(
        reasons=["quality_converged_before_reference_count"],
        converged=True,
        reference_gap=2,
        post_review_dedup_drop=2,
        final_description_dedup_drop_signatures={"a", "b"},
        low_quality_drop_details=[{"case_id": "LQ-1"}, "ignored", {"case_id": "LQ-2"}],
        low_quality_dropped_total=1,
        semantic_dedup_dropped_total=3,
        governance_hard_drop_total=4,
        postprocess_filter_drop_total=5,
        append_cap_drop_total=6,
        flow_governance_summary={
            "scenario_duplicate_pruned_count": 7,
            "flow_reordered": True,
        },
        review_selected_count=8,
    )

    assert state["reasons"] == [
        "quality_converged_before_reference_count",
        "dedup_reduced_count_stop",
        "no_backfill_after_dedup",
        "final_description_dedup_reduced_count",
        "low_quality_filtered",
        "semantic_dedup_reduced_count",
        "governance_hard_drop_applied",
        "append_target_cap_applied",
        "flow_scenario_duplicate_pruned",
        "flow_structure_reordered",
    ]
    assert state["final_description_dedup_drop"] == 2
    assert state["total_dedup_drop"] == 4
    assert state["effective_low_quality_dropped_total"] == 2
    assert state["duplication_rate_estimate"] == 0.5
    assert state["summary_stop_reason"] == [
        "coverage_satisfied",
        "stopped_due_to_diminishing_returns",
        "optimal_case_set_reached",
    ]
    assert state["quality_assessment"] == "medium"


def test_resolve_completion_reason_lists_appends_flags_without_mutating_inputs() -> None:
    reasons = ["quality_converged_before_reference_count"]
    summary_stop_reason = ["coverage_satisfied"]

    state = resolve_completion_reason_lists(
        reasons=reasons,
        summary_stop_reason=summary_stop_reason,
        underfilled=True,
        target_warning=True,
    )

    assert state["reasons"] == [
        "quality_converged_before_reference_count",
        "underfilled",
        "target_count_warning",
    ]
    assert state["summary_stop_reason"] == [
        "coverage_satisfied",
        "underfilled",
        "target_count_warning",
    ]
    assert reasons == ["quality_converged_before_reference_count"]
    assert summary_stop_reason == ["coverage_satisfied"]
