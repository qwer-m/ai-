from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_generation_summary import (
    build_convergence_debug,
    build_convergence_count_debug_fields,
    build_convergence_pruning_debug_fields,
    build_convergence_target_debug_fields,
    build_generation_summary,
    build_stream_postprocess_result_payload,
    derive_convergence_reason_state,
    derive_final_coverage_convergence_inputs,
    resolve_append_reference_counts,
    resolve_completion_reason_lists,
    resolve_final_stage_pruning_counts,
    resolve_generation_target_satisfaction,
    resolve_underfill_diagnostics,
)
from modules.test_generation_components.postprocess.streaming_generation_report import (
    FinalGenerationReportInputs,
    build_final_generation_report,
)


def test_derive_final_coverage_inputs_marks_quality_converged_before_reference_count() -> None:
    state = derive_final_coverage_convergence_inputs(
        pre_priority_coverage={"missing_rules": [], "rule_diagnostics": []},
        reference_count_effective=10,
        final_count=8,
    )

    assert state["converged"] is True
    assert state["reference_gap"] == 2
    assert state["missing_rules_final"] == []
    assert state["missing_types_final"] is False
    assert state["reasons"] == ["quality_converged_before_reference_count"]


def test_build_final_generation_report_returns_summary_and_debug_payloads() -> None:
    report = build_final_generation_report(
        FinalGenerationReportInputs(
            parsed_result=[
                {
                    "id": "TC-001",
                    "test_module": "课程排课",
                    "description": "保存课程排课成功",
                    "expected_result": "系统保存排课并展示成功提示",
                    "priority": "P0",
                    "execution_group": "main_smoke",
                },
                {
                    "id": "TC-002",
                    "test_module": "课程排课",
                    "description": "保存课程排课失败",
                    "expected_result": "系统阻止保存并展示失败原因",
                    "priority": "P1",
                    "execution_group": "negative",
                },
            ],
            pre_priority_coverage={"missing_rules": [], "rule_diagnostics": []},
            reference_count_effective=5,
            final_count=2,
            gap_remaining_after_attempts=0,
            gap_attempts=0,
            gap_stopped_by_provider_error=False,
            post_review_dedup_drop=1,
            final_description_dedup_drop_signatures={"dup-signature"},
            low_quality_drop_details=[{"case_id": "TC-LOW", "reason": "non_assertable_expected_result"}],
            low_quality_dropped_total=0,
            semantic_dedup_dropped_total=1,
            governance_hard_drop_total=0,
            postprocess_filter_drop_total=0,
            append_cap_drop_total=0,
            flow_governance_summary={},
            review_selected_count=4,
            review_decision_summary={"priority_conflict_count": 1},
            generation_target_case_range={},
            expected_count=5,
            generation_coverage_mode="core_smoke",
            resolved_full_regression_floor=0,
            candidate_count_before_review=5,
            judge_summary_payload={},
            drop_by_review_llm_count=1,
            stage_counts={"primary_count": 5},
            append_target_count=0,
            append_final_cap_count=0,
            generation_mode="",
            effective_generation_coverage_mode_source="default",
            explicit_generation_mode_override=False,
            explicit_expected_count_floor_preserved=True,
        )
    )

    assert report.coverage["kind"] == "coverage_check"
    assert report.generation_summary["final_count"] == 2
    assert report.generation_summary["underfilled"] is True
    assert report.generation_summary["needs_priority_review"] is True
    assert report.generation_summary["final_priority_breakdown"] == {"P0": 1, "P1": 1}
    assert report.convergence_debug["final_count"] == 2
    assert report.convergence_debug["quality_rejected_count"] == 1
    assert report.convergence_debug["duplicate_pruned_count"] == 3


def test_derive_final_coverage_inputs_collects_gap_provider_and_missing_reasons() -> None:
    state = derive_final_coverage_convergence_inputs(
        pre_priority_coverage={
            "missing_rules": ["MUST_COVER_PAY"],
            "rule_diagnostics": [{"rule": "MUST_COVER_PAY", "missing_types": ["boundary"]}],
        },
        reference_count_effective=12,
        final_count=9,
        gap_remaining_after_attempts=3,
        gap_attempts=3,
        gap_stopped_by_provider_error=True,
    )

    assert state["converged"] is False
    assert state["reference_gap"] == 3
    assert state["missing_rules_final"] == ["MUST_COVER_PAY"]
    assert state["missing_types_final"] is True
    assert state["reasons"] == [
        "coverage_gap_still_exists",
        "gap_attempt_limit_reached",
        "gap_stopped_by_provider_error",
        "coverage_missing_rules",
        "coverage_missing_types",
    ]


def test_derive_final_coverage_inputs_adds_default_reason_when_not_converged_without_detail() -> None:
    state = derive_final_coverage_convergence_inputs(
        pre_priority_coverage={"converged": False, "missing_rules": [], "rule_diagnostics": []},
        reference_count_effective=5,
        final_count=5,
        gap_remaining_after_attempts=0,
        gap_attempts=0,
    )

    assert state["converged"] is False
    assert state["reasons"] == ["coverage_not_converged"]


def test_derive_final_coverage_inputs_merges_coverage_payload_with_fixed_kind() -> None:
    state = derive_final_coverage_convergence_inputs(
        pre_priority_coverage={
            "kind": "priority_semantics",
            "missing_rules": ["MUST_COVER_SUBMIT"],
            "rule_diagnostics": [],
            "extra": {"source": "pre_priority"},
        },
        reference_count_effective=3,
        final_count=3,
    )

    assert state["coverage"] == {
        "kind": "coverage_check",
        "missing_rules": ["MUST_COVER_SUBMIT"],
        "rule_diagnostics": [],
        "extra": {"source": "pre_priority"},
    }


def test_resolve_append_reference_counts_for_non_append_generation() -> None:
    assert resolve_append_reference_counts(
        append=False,
        expected_count=30,
        existing_unique_count=12,
    ) == {
        "append_target_count": 0,
        "reference_count_effective": 30,
        "append_final_cap_count": 0,
    }


def test_resolve_append_reference_counts_caps_append_gap() -> None:
    assert resolve_append_reference_counts(
        append=True,
        expected_count=30,
        existing_unique_count=12,
    ) == {
        "append_target_count": 18,
        "reference_count_effective": 18,
        "append_final_cap_count": 18,
    }


def test_resolve_append_reference_counts_handles_existing_count_over_target() -> None:
    assert resolve_append_reference_counts(
        append=True,
        expected_count=8,
        existing_unique_count=12,
    ) == {
        "append_target_count": 0,
        "reference_count_effective": 8,
        "append_final_cap_count": 0,
    }


def test_resolve_final_stage_pruning_counts_with_complete_judge_payload() -> None:
    counts = resolve_final_stage_pruning_counts(
        effective_low_quality_dropped_total=3,
        governance_hard_drop_total=2,
        judge_summary_payload={
            "reject_count": 4,
            "rejected_out_count": 5,
            "pending_out_count": 6,
            "confirmed_pass_out_count": 20,
            "repaired_pass_out_count": 3,
        },
        review_selected_count=18,
        final_count=17,
        flow_governance_summary={"scenario_duplicate_pruned_count": 2},
        final_description_dedup_drop=1,
        drop_by_review_llm_count=7,
        review_decision_summary={"drop_by_review_gate_count": 8},
        total_dedup_drop=9,
        semantic_dedup_dropped_total=10,
        postprocess_filter_drop_total=11,
    )

    assert counts == {
        "quality_rejected_count": 9,
        "judge_reject_count": 5,
        "judge_pending_count": 6,
        "judge_pass_count": 23,
        "final_input_count": 23,
        "final_non_judge_drop_count": 6,
        "scenario_duplicate_pruned_count": 2,
        "post_review_dedup_reorder_drop_count": 3,
        "review_selector_pruned_count": 15,
        "duplicate_pruned_count": 19,
        "invalid_pruned_count": 13,
    }


def test_resolve_final_stage_pruning_counts_falls_back_without_judge_payload() -> None:
    counts = resolve_final_stage_pruning_counts(
        effective_low_quality_dropped_total=1,
        governance_hard_drop_total=2,
        judge_summary_payload={},
        review_selected_count=12,
        final_count=9,
        flow_governance_summary={},
        final_description_dedup_drop=0,
        drop_by_review_llm_count=0,
        review_decision_summary={},
        total_dedup_drop=3,
        semantic_dedup_dropped_total=4,
        postprocess_filter_drop_total=5,
    )

    assert counts["quality_rejected_count"] == 3
    assert counts["judge_reject_count"] == 0
    assert counts["judge_pending_count"] == 0
    assert counts["judge_pass_count"] == 0
    assert counts["final_input_count"] == 12
    assert counts["final_non_judge_drop_count"] == 3
    assert counts["duplicate_pruned_count"] == 7
    assert counts["invalid_pruned_count"] == 7


def test_resolve_final_stage_pruning_counts_ignores_non_numeric_reject_count_for_quality() -> None:
    counts = resolve_final_stage_pruning_counts(
        effective_low_quality_dropped_total=2,
        governance_hard_drop_total=1,
        judge_summary_payload={
            "reject_count": "not-a-number",
            "pending_count": 2,
            "pass_count": 6,
        },
        review_selected_count=10,
        final_count=5,
        flow_governance_summary={},
        final_description_dedup_drop=0,
        drop_by_review_llm_count=0,
        review_decision_summary={},
        total_dedup_drop=0,
        semantic_dedup_dropped_total=0,
        postprocess_filter_drop_total=0,
    )

    assert counts["quality_rejected_count"] == 3
    assert counts["judge_reject_count"] == 0
    assert counts["judge_pending_count"] == 2
    assert counts["judge_pass_count"] == 6


def test_resolve_final_stage_pruning_counts_clamps_post_review_reorder_drop() -> None:
    counts = resolve_final_stage_pruning_counts(
        effective_low_quality_dropped_total=0,
        governance_hard_drop_total=0,
        judge_summary_payload={"pass_count": 10},
        review_selected_count=10,
        final_count=9,
        flow_governance_summary={"scenario_duplicate_pruned_count": 2},
        final_description_dedup_drop=3,
        drop_by_review_llm_count=0,
        review_decision_summary={},
        total_dedup_drop=0,
        semantic_dedup_dropped_total=0,
        postprocess_filter_drop_total=0,
    )

    assert counts["final_non_judge_drop_count"] == 1
    assert counts["post_review_dedup_reorder_drop_count"] == 0


def test_resolve_generation_target_satisfaction_uses_explicit_expected_target() -> None:
    state = resolve_generation_target_satisfaction(
        generation_target_case_range={"min": 24, "max": 36, "source": "requirement_profile"},
        expected_count=30,
        reference_count_effective=42,
        generation_coverage_mode="core_smoke",
        resolved_full_regression_floor=0,
        candidate_count_before_review=29,
        post_review_dedup_drop=0,
        final_description_dedup_drop=0,
        semantic_dedup_dropped_total=0,
        flow_governance_summary={"scenario_duplicate_pruned_count": 0},
        effective_low_quality_dropped_total=0,
        governance_hard_drop_total=0,
        final_count=27,
    )

    assert state["target_min"] == 24
    assert state["target_max"] == 36
    assert state["recommended_range"] == "24-36"
    assert state["expected_count_explicit"] is True
    assert state["target_final_count"] == 30
    assert state["soft_min_count"] == 24
    assert state["hard_min_count"] == 21
    assert state["valid_unique_candidate_count"] == 29
    assert state["min_acceptable_final"] == 24
    assert state["target_satisfaction_denominator"] == 30
    assert state["target_satisfaction_ratio"] == 0.9
    assert state["target_warning"] is False
    assert state["underfilled"] is False


def test_resolve_generation_target_satisfaction_applies_full_regression_floor() -> None:
    state = resolve_generation_target_satisfaction(
        generation_target_case_range={"min": 60, "max": 90, "source": "workflow_blueprint"},
        expected_count=60,
        reference_count_effective=48,
        generation_coverage_mode="full_functional_regression",
        resolved_full_regression_floor=50,
        candidate_count_before_review=45,
        post_review_dedup_drop=3,
        final_description_dedup_drop=1,
        semantic_dedup_dropped_total=2,
        flow_governance_summary={"scenario_duplicate_pruned_count": 4},
        effective_low_quality_dropped_total=2,
        governance_hard_drop_total=1,
        final_count=49,
    )

    assert state["recommended_range"] == "60-90"
    assert state["expected_count_explicit"] is True
    assert state["target_final_count"] == 60
    assert state["soft_min_count"] == 48
    assert state["hard_min_count"] == 50
    assert state["postprocess_pruned_count"] == 13
    assert state["recommended_floor_underfilled"] is False
    assert state["min_acceptable_final"] == 50
    assert state["target_satisfaction_ratio"] == 0.8167
    assert state["target_warning"] is False
    assert state["underfilled"] is True


def test_resolve_generation_target_satisfaction_flags_recommended_floor_underfilled() -> None:
    state = resolve_generation_target_satisfaction(
        generation_target_case_range={"min": 40, "max": 60, "source": "requirement_profile"},
        expected_count=45,
        reference_count_effective=45,
        generation_coverage_mode="core_smoke",
        resolved_full_regression_floor=0,
        candidate_count_before_review=43,
        post_review_dedup_drop=2,
        final_description_dedup_drop=1,
        semantic_dedup_dropped_total=1,
        flow_governance_summary={"scenario_duplicate_pruned_count": 2},
        effective_low_quality_dropped_total=3,
        governance_hard_drop_total=1,
        final_count=38,
    )

    assert state["target_min_count"] == 40
    assert state["target_max_count"] == 60
    assert state["postprocess_pruned_count"] == 10
    assert state["recommended_floor_underfilled"] is True
    assert state["soft_min_count"] == 36
    assert state["hard_min_count"] == 31
    assert state["min_acceptable_final"] == 40
    assert state["target_satisfaction_denominator"] == 60
    assert state["target_satisfaction_ratio"] == 0.6333
    assert state["target_warning"] is False
    assert state["underfilled"] is True


def test_resolve_generation_target_satisfaction_defaults_range_without_target_range() -> None:
    state = resolve_generation_target_satisfaction(
        generation_target_case_range={},
        expected_count=0,
        reference_count_effective=32,
        generation_coverage_mode="core_smoke",
        resolved_full_regression_floor=0,
        candidate_count_before_review=32,
        post_review_dedup_drop=0,
        final_description_dedup_drop=0,
        semantic_dedup_dropped_total=0,
        flow_governance_summary=[],
        effective_low_quality_dropped_total=0,
        governance_hard_drop_total=0,
        final_count=32,
    )

    assert state["target_min"] is None
    assert state["target_max"] is None
    assert state["recommended_range"] == "30-50"
    assert state["expected_count_explicit"] is False
    assert state["target_final_count"] == 32
    assert state["soft_min_count"] == 26
    assert state["hard_min_count"] == 22
    assert state["min_acceptable_final"] == 0
    assert state["target_satisfaction_ratio"] == 1.0
    assert state["target_warning"] is False
    assert state["underfilled"] is False


def test_resolve_underfill_diagnostics_returns_empty_when_not_underfilled() -> None:
    assert resolve_underfill_diagnostics(
        underfilled=False,
        valid_unique_candidate_count=18,
        hard_min_count=21,
        min_acceptable_final=24,
        final_count=19,
    ) == {
        "underfill_reason": "",
        "underfill_root_cause": "",
        "underfill_level": "",
    }


def test_resolve_underfill_diagnostics_valid_candidate_insufficient() -> None:
    assert resolve_underfill_diagnostics(
        underfilled=True,
        valid_unique_candidate_count=20,
        hard_min_count=21,
        min_acceptable_final=24,
        final_count=19,
    ) == {
        "underfill_reason": "valid_candidate_insufficient",
        "underfill_root_cause": "candidate_insufficient",
        "underfill_level": "mild",
    }


def test_resolve_underfill_diagnostics_scenario_cap_over_pruned() -> None:
    assert resolve_underfill_diagnostics(
        underfilled=True,
        valid_unique_candidate_count=42,
        hard_min_count=28,
        scenario_duplicate_pruned_count=9,
        review_selector_pruned_count=5,
        duplicate_pruned_count=0,
        quality_rejected_count=2,
        final_non_judge_drop_count=10,
        min_acceptable_final=34,
        final_count=22,
    ) == {
        "underfill_reason": "scenario_cap_over_pruned",
        "underfill_root_cause": "final_stage_over_pruning",
        "underfill_level": "moderate",
    }


def test_resolve_underfill_diagnostics_review_selector_over_pruned() -> None:
    assert resolve_underfill_diagnostics(
        underfilled=True,
        valid_unique_candidate_count=45,
        hard_min_count=28,
        scenario_duplicate_pruned_count=1,
        review_selector_pruned_count=12,
        duplicate_pruned_count=4,
        quality_rejected_count=6,
        final_non_judge_drop_count=7,
        min_acceptable_final=42,
        final_count=24,
    ) == {
        "underfill_reason": "review_selector_over_pruned",
        "underfill_root_cause": "review_stage_over_pruning",
        "underfill_level": "moderate",
    }


def test_resolve_underfill_diagnostics_duplicate_pruned_under_target() -> None:
    assert resolve_underfill_diagnostics(
        underfilled=True,
        valid_unique_candidate_count=52,
        hard_min_count=35,
        scenario_duplicate_pruned_count=0,
        review_selector_pruned_count=4,
        duplicate_pruned_count=11,
        quality_rejected_count=7,
        final_non_judge_drop_count=9,
        min_acceptable_final=48,
        final_count=23,
    ) == {
        "underfill_reason": "duplicate_pruned_under_target",
        "underfill_root_cause": "final_stage_over_pruning",
        "underfill_level": "severe",
    }


def test_resolve_underfill_diagnostics_quality_rejected_under_target() -> None:
    assert resolve_underfill_diagnostics(
        underfilled=True,
        valid_unique_candidate_count=38,
        hard_min_count=27,
        scenario_duplicate_pruned_count=0,
        review_selector_pruned_count=3,
        duplicate_pruned_count=0,
        quality_rejected_count=8,
        final_non_judge_drop_count=5,
        min_acceptable_final=31,
        final_count=25,
    ) == {
        "underfill_reason": "quality_rejected_under_target",
        "underfill_root_cause": "final_stage_over_pruning",
        "underfill_level": "moderate",
    }


def test_derive_convergence_reason_state_counts_filters_and_flow_reason_order() -> None:
    initial_reasons = ["quality_converged_before_reference_count"]
    low_quality_drop_details = [
        {"case_id": "LQ-1", "reason": "expected_result_not_assertable"},
        "ignored-non-dict",
        {"case_id": "LQ-2", "reason": "missing_assertion"},
    ]

    state = derive_convergence_reason_state(
        reasons=initial_reasons,
        converged=True,
        reference_gap=3,
        post_review_dedup_drop=2,
        final_description_dedup_drop_signatures={"case-a", "case-b"},
        low_quality_drop_details=low_quality_drop_details,
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

    assert initial_reasons == ["quality_converged_before_reference_count"]
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
    assert state["low_quality_drop_details"] is low_quality_drop_details
    assert state["low_quality_dropped_total"] == 1
    assert state["semantic_dedup_dropped_total"] == 3
    assert state["governance_hard_drop_total"] == 4
    assert state["postprocess_filter_drop_total"] == 5
    assert state["effective_low_quality_dropped_total"] == 2
    assert state["duplication_rate_estimate"] == 0.5
    assert state["summary_stop_reason"] == [
        "coverage_satisfied",
        "stopped_due_to_diminishing_returns",
        "optimal_case_set_reached",
    ]
    assert state["quality_assessment"] == "medium"


def test_derive_convergence_reason_state_high_quality_without_underfill_reasons() -> None:
    state = derive_convergence_reason_state(
        reasons=[],
        converged=True,
        reference_gap=0,
        post_review_dedup_drop=0,
        final_description_dedup_drop_signatures=set(),
        low_quality_drop_details=[],
        low_quality_dropped_total=0,
        semantic_dedup_dropped_total=0,
        governance_hard_drop_total=0,
        postprocess_filter_drop_total=0,
        append_cap_drop_total=0,
        flow_governance_summary={},
        review_selected_count=10,
    )

    assert state["reasons"] == []
    assert state["duplication_rate_estimate"] == 0.0
    assert state["summary_stop_reason"] == [
        "coverage_satisfied",
        "optimal_case_set_reached",
    ]
    assert "underfilled" not in state["summary_stop_reason"]
    assert "target_count_warning" not in state["summary_stop_reason"]
    assert state["quality_assessment"] == "high"


def test_derive_convergence_reason_state_low_quality_for_duplicate_heavy_stop() -> None:
    state = derive_convergence_reason_state(
        reasons=["coverage_missing_rules"],
        converged=False,
        reference_gap=0,
        post_review_dedup_drop=4,
        final_description_dedup_drop_signatures={"case-a"},
        low_quality_drop_details=[],
        low_quality_dropped_total=3,
        semantic_dedup_dropped_total=0,
        governance_hard_drop_total=0,
        postprocess_filter_drop_total=0,
        append_cap_drop_total=0,
        flow_governance_summary={},
        review_selected_count=5,
    )

    assert state["reasons"] == [
        "coverage_missing_rules",
        "dedup_reduced_count_stop",
        "no_backfill_after_dedup",
        "final_description_dedup_reduced_count",
        "low_quality_filtered",
    ]
    assert state["total_dedup_drop"] == 5
    assert state["effective_low_quality_dropped_total"] == 3
    assert state["duplication_rate_estimate"] == 1.0
    assert state["summary_stop_reason"] == ["stopped_due_to_diminishing_returns"]
    assert state["quality_assessment"] == "low"


def test_resolve_completion_reason_lists_appends_underfilled_and_target_warning() -> None:
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


def test_resolve_completion_reason_lists_does_not_duplicate_existing_reasons() -> None:
    state = resolve_completion_reason_lists(
        reasons=[
            "underfilled",
            "target_count_warning",
            "quality_converged_before_reference_count",
        ],
        summary_stop_reason=[
            "coverage_satisfied",
            "underfilled",
            "target_count_warning",
        ],
        underfilled=True,
        target_warning=True,
    )

    assert state["reasons"] == [
        "underfilled",
        "target_count_warning",
        "quality_converged_before_reference_count",
    ]
    assert state["summary_stop_reason"] == [
        "coverage_satisfied",
        "underfilled",
        "target_count_warning",
    ]


def test_resolve_completion_reason_lists_copies_lists_when_no_flags_triggered() -> None:
    reasons = ["coverage_missing_rules"]
    summary_stop_reason = ["stopped_due_to_diminishing_returns"]

    state = resolve_completion_reason_lists(
        reasons=reasons,
        summary_stop_reason=summary_stop_reason,
        underfilled=False,
        target_warning=False,
    )

    assert state["reasons"] == ["coverage_missing_rules"]
    assert state["summary_stop_reason"] == ["stopped_due_to_diminishing_returns"]
    assert state["reasons"] is not reasons
    assert state["summary_stop_reason"] is not summary_stop_reason


def test_build_generation_summary_uses_final_case_summary_and_conversion_semantics() -> None:
    final_case_summary = {
        "final_priority_breakdown": {"P0": 3, "P1": 5},
        "final_execution_group_breakdown": {"main_smoke": 4, "permission": 2},
        "final_module_breakdown_top": {"course": 6, "exam": 2},
        "final_display_case_count": "3",
        "final_display_ratio": "0.1875",
        "final_high_priority_ratio": 0.5,
    }
    stop_reason = ["converged", "target_satisfied"]
    quality_assessment = {"accepted": 16, "warnings": ["low duplicate rate"]}

    summary = build_generation_summary(
        recommended_range={"min": 12, "max": 20},
        generation_coverage_mode="full_regression",
        generation_mode="manual_full",
        effective_generation_coverage_mode_source="explicit_generation_mode",
        explicit_generation_mode_override=1,
        explicit_expected_count_floor_preserved="yes",
        expected_count="18",
        expected_count_explicit=1,
        target_min="12",
        target_max=20.0,
        target_final_count="16",
        soft_min_count=14.0,
        hard_min_count="10",
        min_acceptable_final=9.0,
        target_satisfaction_ratio="0.8889",
        underfilled=0,
        underfill_level="",
        underfill_reason="",
        underfill_root_cause="",
        final_count="16",
        converged=True,
        summary_stop_reason=stop_reason,
        quality_assessment=quality_assessment,
        needs_priority_review=0,
        priority_conflict_count="2",
        priority_undetermined_count=3.0,
        priority_optional_count=0,
        final_case_summary=final_case_summary,
    )

    assert summary["generation_coverage_mode"] == "full_regression"
    assert summary["requested_generation_mode"] == "manual_full"
    assert summary["explicit_generation_mode_override"] is True
    assert summary["explicit_expected_count_floor_preserved"] is True
    assert summary["expected_count"] == 18
    assert summary["expected_count_explicit"] is True
    assert summary["recommended_min"] == 12
    assert summary["recommended_max"] == 20
    assert summary["target_final_count"] == 16
    assert summary["soft_min_count"] == 14
    assert summary["hard_min_count"] == 10
    assert summary["min_acceptable_final"] == 9
    assert summary["target_satisfaction_ratio"] == 0.8889
    assert summary["underfilled"] is False
    assert summary["final_count"] == 16
    assert summary["status"] == "completed_with_optimal_set"
    assert summary["stop_reason"] is stop_reason
    assert summary["quality_assessment"] is quality_assessment
    assert summary["needs_priority_review"] is False
    assert summary["priority_conflict_count"] == 2
    assert summary["priority_undetermined_count"] == 3
    assert summary["priority_optional_count"] == 0
    assert summary["final_priority_breakdown"] == {"P0": 3, "P1": 5}
    assert summary["final_priority_breakdown"] is not final_case_summary["final_priority_breakdown"]
    assert summary["final_execution_group_breakdown"] == {"main_smoke": 4, "permission": 2}
    assert summary["final_module_breakdown_top"] == {"course": 6, "exam": 2}
    assert summary["final_display_case_count"] == 3
    assert summary["final_display_ratio"] == 0.1875
    assert summary["final_high_priority_ratio"] == 0.5


def test_build_generation_summary_defaults_match_payload_defaults() -> None:
    summary = build_generation_summary()

    assert summary["generation_coverage_mode"] == "core_smoke"
    assert summary["requested_generation_mode"] == ""
    assert summary["effective_generation_coverage_mode_source"] == ""
    assert summary["expected_count"] == 0
    assert summary["recommended_min"] == 0
    assert summary["recommended_max"] == 0
    assert summary["target_final_count"] == 0
    assert summary["target_satisfaction_ratio"] == 0.0
    assert summary["underfilled"] is False
    assert summary["status"] == "completed_with_quality_stop"
    assert summary["final_priority_breakdown"] == {}
    assert summary["final_execution_group_breakdown"] == {}
    assert summary["final_module_breakdown_top"] == {}
    assert summary["final_display_case_count"] == 0
    assert summary["final_display_ratio"] == 0.0
    assert summary["final_high_priority_ratio"] == 0.0


def test_build_convergence_count_debug_fields_normalizes_counts_and_missing_signals() -> None:
    fields = build_convergence_count_debug_fields(
        reference_count_effective="21",
        final_count=17.0,
        reference_gap="4",
        converged=1,
        duplication_rate_estimate="0.125",
        stage_counts={"primary": "9", "gap": 5.0, "review": 7},
        candidate_count_before_review="30",
        review_selected_count=18.0,
        post_review_dedup_drop="2",
        judge_reject_count=1.0,
        judge_pending_count="3",
        judge_pass_count="14",
        final_input_count="19",
        final_non_judge_drop_count=2.0,
        scenario_duplicate_pruned_count="1",
        post_review_dedup_reorder_drop_count="2",
        final_description_dedup_drop=3.0,
        total_dedup_drop="6",
        gap_attempts="2",
        gap_remaining_after_attempts=1.0,
        missing_rules_final=["must_cover_login", "must_cover_submit"],
        missing_types_final={"boundary": 1},
    )

    assert fields == {
        "suggested_count": 21,
        "final_count": 17,
        "reference_gap": 4,
        "converged": True,
        "duplication_rate_estimate": 0.125,
        "primary_count": 9,
        "gap_count": 5,
        "review_count": 7,
        "candidate_count_before_review": 30,
        "review_selected_count": 18,
        "post_review_dedup_drop": 2,
        "judge_reject_count": 1,
        "judge_pending_count": 3,
        "judge_pass_count": 14,
        "final_input_count": 19,
        "final_output_count": 17,
        "final_non_judge_drop_count": 2,
        "scenario_duplicate_pruned_count": 1,
        "post_review_dedup_reorder_drop_count": 2,
        "final_description_dedup_drop_count": 3,
        "total_dedup_drop_count": 6,
        "gap_attempts": 2,
        "gap_remaining_after_attempts": 1,
        "missing_rules_count": 2,
        "missing_types_exists": True,
    }


def test_build_convergence_pruning_debug_fields_slices_examples_and_counts() -> None:
    low_quality_drop_details = [
        {"case_id": f"LQ-{index}", "reason": "expected_result_not_assertable"}
        for index in range(12)
    ]
    low_quality_drop_details.insert(2, "ignored-non-dict")

    fields = build_convergence_pruning_debug_fields(
        effective_low_quality_dropped_total="11",
        low_quality_drop_details=low_quality_drop_details,
        postprocess_filter_drop_total="5",
        semantic_dedup_dropped_total=4.0,
        governance_hard_drop_total="2",
        duplicate_pruned_count=6.0,
        invalid_pruned_count="1",
        quality_rejected_count=2.0,
        review_selector_pruned_count="3",
        valid_unique_candidate_count=24.0,
    )

    assert fields["low_quality_dropped_count"] == 11
    assert len(fields["low_quality_dropped_examples"]) == 9
    assert all(isinstance(item, dict) for item in fields["low_quality_dropped_examples"])
    assert fields["low_quality_dropped_examples"][-1]["case_id"] == "LQ-8"
    assert fields["postprocess_filter_drop_total"] == 5
    assert fields["semantic_dedup_dropped_count"] == 4
    assert fields["governance_hard_drop_count"] == 2
    assert fields["duplicate_pruned_count"] == 6
    assert fields["invalid_pruned_count"] == 1
    assert fields["quality_rejected_count"] == 2
    assert fields["review_selector_pruned_count"] == 3
    assert fields["valid_unique_candidate_count"] == 24


def test_build_convergence_target_debug_fields_copies_flow_and_range_payloads() -> None:
    reasons = ["underfilled"]
    flow_governance_summary = {"hard_drop_count": 2}
    generation_target_case_range = {"min": "12", "max": 20}

    fields = build_convergence_target_debug_fields(
        generation_coverage_mode="full_regression",
        generation_mode="manual_full",
        effective_generation_coverage_mode_source="explicit_generation_mode",
        explicit_generation_mode_override=1,
        explicit_expected_count_floor_preserved="true",
        expected_count="25",
        expected_count_explicit=1,
        target_min="12",
        target_max=20.0,
        target_final_count="17",
        soft_min_count=15.0,
        hard_min_count="10",
        min_acceptable_final=9.0,
        target_satisfaction_ratio="0.8095",
        underfilled=1,
        underfill_level="mild",
        underfill_reason="target_count_warning",
        underfill_root_cause="target_not_satisfied",
        append_target_count="8",
        append_final_cap_count=5.0,
        append_cap_drop_total="3",
        flow_governance_summary=flow_governance_summary,
        needs_priority_review=1,
        priority_conflict_count="2",
        priority_undetermined_count=3.0,
        priority_optional_count="1",
        reasons=reasons,
        generation_target_case_range=generation_target_case_range,
    )

    assert fields["generation_coverage_mode"] == "full_regression"
    assert fields["requested_generation_mode"] == "manual_full"
    assert fields["explicit_generation_mode_override"] is True
    assert fields["explicit_expected_count_floor_preserved"] is True
    assert fields["expected_count"] == 25
    assert fields["recommended_min"] == 12
    assert fields["recommended_max"] == 20
    assert fields["target_final_count"] == 17
    assert fields["soft_min_count"] == 15
    assert fields["hard_min_count"] == 10
    assert fields["min_acceptable_final"] == 9
    assert fields["target_satisfaction_ratio"] == 0.8095
    assert fields["underfilled"] is True
    assert fields["underfill_level"] == "mild"
    assert fields["append_target_count"] == 8
    assert fields["append_final_cap_count"] == 5
    assert fields["append_cap_drop_count"] == 3
    assert fields["flow_governance"] == flow_governance_summary
    assert fields["flow_governance"] is not flow_governance_summary
    assert fields["priority_conflict_count"] == 2
    assert fields["priority_undetermined_count"] == 3
    assert fields["priority_optional_count"] == 1
    assert fields["reasons"] is reasons
    assert fields["generation_target_case_range"] == generation_target_case_range
    assert fields["generation_target_case_range"] is not generation_target_case_range


def test_build_convergence_debug_replicates_stage_governance_priority_and_target_fields() -> None:
    reasons = ["underfilled", "target_count_warning"]
    low_quality_drop_details = [
        {"case_id": f"LQ-{index}", "reason": "expected_result_not_assertable"}
        for index in range(9)
    ] + [
        "ignored-non-dict",
        {"case_id": "LQ-outside-slice", "reason": "should_not_be_seen"},
    ]
    flow_governance_summary = {
        "hard_drop_count": 2,
        "policy": ["trusted_workflow_contract", "role_chain_preserved"],
    }
    generation_target_case_range = {"min": "12", "max": 20, "source": "requirement_profile"}

    debug = build_convergence_debug(
        reference_count_effective="21",
        final_count=17.0,
        reference_gap="4",
        converged=1,
        duplication_rate_estimate="0.125",
        stage_counts={"primary": "9", "gap": 5.0, "review": 7, "ignored": 99},
        candidate_count_before_review="30",
        review_selected_count=18.0,
        post_review_dedup_drop="2",
        judge_reject_count=1.0,
        judge_pending_count="3",
        judge_pass_count="14",
        final_input_count="19",
        final_non_judge_drop_count=2.0,
        scenario_duplicate_pruned_count="1",
        post_review_dedup_reorder_drop_count="2",
        final_description_dedup_drop=3.0,
        total_dedup_drop="6",
        gap_attempts="2",
        gap_remaining_after_attempts=1.0,
        missing_rules_final=["must_cover_login", "must_cover_submit"],
        missing_types_final={"boundary": 1},
        effective_low_quality_dropped_total="11",
        low_quality_drop_details=low_quality_drop_details,
        postprocess_filter_drop_total="5",
        semantic_dedup_dropped_total=4.0,
        governance_hard_drop_total="2",
        duplicate_pruned_count=6.0,
        invalid_pruned_count="1",
        quality_rejected_count=2.0,
        review_selector_pruned_count="3",
        valid_unique_candidate_count=24.0,
        generation_coverage_mode="full_regression",
        generation_mode="manual_full",
        effective_generation_coverage_mode_source="explicit_generation_mode",
        explicit_generation_mode_override=1,
        explicit_expected_count_floor_preserved="true",
        expected_count="25",
        expected_count_explicit=1,
        target_min="12",
        target_max=20.0,
        target_final_count="17",
        soft_min_count=15.0,
        hard_min_count="10",
        min_acceptable_final=9.0,
        target_satisfaction_ratio="0.8095",
        underfilled=1,
        underfill_level="mild",
        underfill_reason="target_count_warning",
        underfill_root_cause="target_not_satisfied",
        append_target_count="8",
        append_final_cap_count=5.0,
        append_cap_drop_total="3",
        flow_governance_summary=flow_governance_summary,
        needs_priority_review=1,
        priority_conflict_count="2",
        priority_undetermined_count=3.0,
        priority_optional_count="1",
        reasons=reasons,
        generation_target_case_range=generation_target_case_range,
    )

    assert debug["suggested_count"] == 21
    assert debug["final_count"] == 17
    assert debug["final_output_count"] == 17
    assert debug["reference_gap"] == 4
    assert debug["converged"] is True
    assert debug["duplication_rate_estimate"] == 0.125
    assert debug["primary_count"] == 9
    assert debug["gap_count"] == 5
    assert debug["review_count"] == 7
    assert debug["candidate_count_before_review"] == 30
    assert debug["review_selected_count"] == 18
    assert debug["post_review_dedup_drop"] == 2
    assert debug["judge_reject_count"] == 1
    assert debug["judge_pending_count"] == 3
    assert debug["judge_pass_count"] == 14
    assert debug["final_input_count"] == 19
    assert debug["final_non_judge_drop_count"] == 2
    assert debug["scenario_duplicate_pruned_count"] == 1
    assert debug["post_review_dedup_reorder_drop_count"] == 2
    assert debug["final_description_dedup_drop_count"] == 3
    assert debug["total_dedup_drop_count"] == 6
    assert debug["gap_attempts"] == 2
    assert debug["gap_remaining_after_attempts"] == 1
    assert debug["missing_rules_count"] == 2
    assert debug["missing_types_exists"] is True
    assert debug["low_quality_dropped_count"] == 11
    assert len(debug["low_quality_dropped_examples"]) == 9
    assert all(isinstance(item, dict) for item in debug["low_quality_dropped_examples"])
    assert debug["low_quality_dropped_examples"][-1]["case_id"] == "LQ-8"
    assert "LQ-outside-slice" not in {
        item["case_id"] for item in debug["low_quality_dropped_examples"]
    }
    assert debug["postprocess_filter_drop_total"] == 5
    assert debug["semantic_dedup_dropped_count"] == 4
    assert debug["governance_hard_drop_count"] == 2
    assert debug["duplicate_pruned_count"] == 6
    assert debug["invalid_pruned_count"] == 1
    assert debug["quality_rejected_count"] == 2
    assert debug["review_selector_pruned_count"] == 3
    assert debug["valid_unique_candidate_count"] == 24
    assert debug["generation_coverage_mode"] == "full_regression"
    assert list(debug).count("generation_coverage_mode") == 1
    assert debug["requested_generation_mode"] == "manual_full"
    assert debug["effective_generation_coverage_mode_source"] == "explicit_generation_mode"
    assert debug["explicit_generation_mode_override"] is True
    assert debug["explicit_expected_count_floor_preserved"] is True
    assert debug["expected_count"] == 25
    assert debug["expected_count_explicit"] is True
    assert debug["recommended_min"] == 12
    assert debug["recommended_max"] == 20
    assert debug["target_final_count"] == 17
    assert debug["soft_min_count"] == 15
    assert debug["hard_min_count"] == 10
    assert debug["min_acceptable_final"] == 9
    assert debug["target_satisfaction_ratio"] == 0.8095
    assert debug["underfilled"] is True
    assert debug["underfill_level"] == "mild"
    assert debug["underfill_reason"] == "target_count_warning"
    assert debug["underfill_root_cause"] == "target_not_satisfied"
    assert debug["append_target_count"] == 8
    assert debug["append_final_cap_count"] == 5
    assert debug["append_cap_drop_count"] == 3
    assert debug["flow_governance"] == flow_governance_summary
    assert debug["flow_governance"] is not flow_governance_summary
    assert debug["needs_priority_review"] is True
    assert debug["priority_conflict_count"] == 2
    assert debug["priority_undetermined_count"] == 3
    assert debug["priority_optional_count"] == 1
    assert debug["reasons"] is reasons
    assert debug["generation_target_case_range"] == generation_target_case_range
    assert debug["generation_target_case_range"] is not generation_target_case_range


def test_build_convergence_debug_defaults_match_payload_defaults() -> None:
    debug = build_convergence_debug()

    assert debug["suggested_count"] == 0
    assert debug["final_count"] == 0
    assert debug["reference_gap"] == 0
    assert debug["converged"] is False
    assert debug["duplication_rate_estimate"] == 0.0
    assert debug["primary_count"] == 0
    assert debug["gap_count"] == 0
    assert debug["review_count"] == 0
    assert debug["missing_rules_count"] == 0
    assert debug["missing_types_exists"] is False
    assert debug["low_quality_dropped_examples"] == []
    assert debug["generation_coverage_mode"] == "core_smoke"
    assert debug["requested_generation_mode"] == ""
    assert debug["expected_count"] == 0
    assert debug["recommended_min"] == 0
    assert debug["recommended_max"] == 0
    assert debug["append_target_count"] == 0
    assert debug["append_final_cap_count"] == 0
    assert debug["append_cap_drop_count"] == 0
    assert debug["flow_governance"] == {}
    assert debug["reasons"] is None
    assert debug["generation_target_case_range"] == {}


def test_build_stream_postprocess_result_payload_preserves_payload_and_injects_feedback_debug() -> None:
    cases = [{"case_id": "TC-001", "title": "学生提交作业后教师查看结果"}]
    stage_counts = {"primary": 1, "gap": 0, "review": 1}
    coverage = {"kind": "coverage_check", "missing_rules": []}
    convergence_debug = {"converged": True}
    generation_summary = {"status": "completed_with_optimal_set"}
    review_decision_summary = {"selected_count": 1}
    review_decision_table = [{"case_id": "TC-001", "decision": "keep"}]
    judge_summary = {"pass_count": 1}
    judge_decision_table = [{"case_id": "TC-001", "judge_decision": "pass"}]
    timing_events = [{"stage": "review_selection", "duration_ms": 123}]
    control_state = {"current_round": 2}
    generation_target_case_range = {"min": 1, "max": 3}
    fact_profile = {"roles": ["student", "teacher"]}
    project_profile = {"domain": "homework"}
    manual_quality_profile = {"priority": "P0"}
    feedback_debug = {"control": "debug"}
    feedback_builder_calls = []

    def build_feedback_debug(**kwargs):
        feedback_builder_calls.append(kwargs)
        return feedback_debug

    payload = build_stream_postprocess_result_payload(
        cases=cases,
        stage_counts=stage_counts,
        coverage=coverage,
        convergence_debug=convergence_debug,
        generation_summary=generation_summary,
        review_decision_summary=review_decision_summary,
        review_decision_table=review_decision_table,
        judge_summary=judge_summary,
        judge_decision_table=judge_decision_table,
        timing_events=timing_events,
        feedback_control_debug_builder_fn=build_feedback_debug,
        control_state=control_state,
        generation_coverage_mode=None,
        generation_target_case_range=generation_target_case_range,
        fact_profile=fact_profile,
        project_profile=project_profile,
        manual_quality_profile=manual_quality_profile,
    )

    assert list(payload) == [
        "cases",
        "stage_counts",
        "coverage",
        "convergence_debug",
        "generation_summary",
        "review_decision_summary",
        "review_decision_table",
        "judge_summary",
        "judge_decision_table",
        "timing_events",
        "feedback_control_debug",
    ]
    assert payload["cases"] is cases
    assert payload["review_decision_table"] is review_decision_table
    assert payload["judge_decision_table"] is judge_decision_table
    assert payload["timing_events"] is timing_events
    assert payload["feedback_control_debug"] is feedback_debug
    assert feedback_builder_calls == [
        {
            "control_state": control_state,
            "generation_coverage_mode": "core_smoke",
            "generation_target_case_range": generation_target_case_range,
            "fact_profile": fact_profile,
            "project_profile": project_profile,
            "manual_quality_profile": manual_quality_profile,
        }
    ]
