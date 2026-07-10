from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .streaming_final_case_summary import final_case_breakdown as _final_case_breakdown
from .streaming_generation_convergence import (
    derive_convergence_reason_state as _derive_convergence_reason_state,
    derive_final_coverage_convergence_inputs as _derive_final_coverage_convergence_inputs,
    resolve_completion_reason_lists as _resolve_completion_reason_lists,
)
from .streaming_generation_summary import (
    build_convergence_debug as _build_convergence_debug,
    build_generation_summary as _build_generation_summary,
    resolve_final_stage_pruning_counts as _resolve_final_stage_pruning_counts,
)
from .streaming_generation_target import (
    resolve_generation_target_satisfaction as _resolve_generation_target_satisfaction,
    resolve_underfill_diagnostics as _resolve_underfill_diagnostics,
)
from .streaming_postprocess_utils import _dict_case_items
from .streaming_review_decision_table import (
    resolve_review_priority_summary_flags as _resolve_review_priority_summary_flags,
)


@dataclass(frozen=True)
class FinalGenerationReportInputs:
    parsed_result: list[dict[str, Any]]
    pre_priority_coverage: dict[str, Any]
    reference_count_effective: int
    final_count: int
    gap_remaining_after_attempts: int
    gap_attempts: int
    gap_stopped_by_provider_error: bool
    post_review_dedup_drop: int
    final_description_dedup_drop_signatures: set[str]
    low_quality_drop_details: list[dict[str, Any]]
    low_quality_dropped_total: int
    semantic_dedup_dropped_total: int
    governance_hard_drop_total: int
    postprocess_filter_drop_total: int
    append_cap_drop_total: int
    flow_governance_summary: dict[str, Any]
    review_selected_count: int
    review_decision_summary: dict[str, Any]
    generation_target_case_range: dict[str, Any]
    expected_count: int
    generation_coverage_mode: str
    resolved_full_regression_floor: int
    candidate_count_before_review: int
    judge_summary_payload: dict[str, Any]
    drop_by_review_llm_count: int
    stage_counts: dict[str, Any]
    append_target_count: int
    append_final_cap_count: int
    generation_mode: str
    effective_generation_coverage_mode_source: str
    explicit_generation_mode_override: bool
    explicit_expected_count_floor_preserved: bool
    final_coverage: dict[str, Any] | None = None


@dataclass(frozen=True)
class FinalGenerationReport:
    coverage: dict[str, Any]
    generation_summary: dict[str, Any]
    convergence_debug: dict[str, Any]


def build_final_generation_report(inputs: FinalGenerationReportInputs) -> FinalGenerationReport:
    coverage_source = inputs.final_coverage if inputs.final_coverage is not None else inputs.pre_priority_coverage
    final_coverage_convergence_inputs = _derive_final_coverage_convergence_inputs(
        pre_priority_coverage=coverage_source,
        reference_count_effective=inputs.reference_count_effective,
        final_count=inputs.final_count,
        gap_remaining_after_attempts=inputs.gap_remaining_after_attempts,
        gap_attempts=inputs.gap_attempts,
        gap_stopped_by_provider_error=inputs.gap_stopped_by_provider_error,
    )
    coverage = final_coverage_convergence_inputs["coverage"]
    missing_rules_final = final_coverage_convergence_inputs["missing_rules_final"]
    missing_types_final = final_coverage_convergence_inputs["missing_types_final"]
    reference_gap = final_coverage_convergence_inputs["reference_gap"]
    converged = final_coverage_convergence_inputs["converged"]
    reasons = final_coverage_convergence_inputs["reasons"]

    convergence_reason_state = _derive_convergence_reason_state(
        reasons=reasons,
        converged=converged,
        reference_gap=reference_gap,
        post_review_dedup_drop=inputs.post_review_dedup_drop,
        final_description_dedup_drop_signatures=inputs.final_description_dedup_drop_signatures,
        low_quality_drop_details=inputs.low_quality_drop_details,
        low_quality_dropped_total=inputs.low_quality_dropped_total,
        semantic_dedup_dropped_total=inputs.semantic_dedup_dropped_total,
        governance_hard_drop_total=inputs.governance_hard_drop_total,
        postprocess_filter_drop_total=inputs.postprocess_filter_drop_total,
        append_cap_drop_total=inputs.append_cap_drop_total,
        flow_governance_summary=inputs.flow_governance_summary,
        review_selected_count=inputs.review_selected_count,
    )
    reasons = convergence_reason_state["reasons"]
    final_description_dedup_drop = convergence_reason_state["final_description_dedup_drop"]
    total_dedup_drop = convergence_reason_state["total_dedup_drop"]
    low_quality_drop_details = convergence_reason_state["low_quality_drop_details"]
    semantic_dedup_dropped_total = convergence_reason_state["semantic_dedup_dropped_total"]
    governance_hard_drop_total = convergence_reason_state["governance_hard_drop_total"]
    postprocess_filter_drop_total = convergence_reason_state["postprocess_filter_drop_total"]
    effective_low_quality_dropped_total = convergence_reason_state["effective_low_quality_dropped_total"]
    duplication_rate_estimate = convergence_reason_state["duplication_rate_estimate"]
    summary_stop_reason = convergence_reason_state["summary_stop_reason"]
    quality_assessment = convergence_reason_state["quality_assessment"]
    priority_summary_flags = _resolve_review_priority_summary_flags(inputs.review_decision_summary)
    priority_conflict_count = priority_summary_flags.priority_conflict_count
    priority_undetermined_count = priority_summary_flags.priority_undetermined_count
    priority_optional_count = priority_summary_flags.priority_optional_count
    needs_priority_review = priority_summary_flags.needs_priority_review

    target_satisfaction_state = _resolve_generation_target_satisfaction(
        generation_target_case_range=inputs.generation_target_case_range,
        expected_count=inputs.expected_count,
        reference_count_effective=inputs.reference_count_effective,
        generation_coverage_mode=inputs.generation_coverage_mode,
        resolved_full_regression_floor=inputs.resolved_full_regression_floor,
        candidate_count_before_review=inputs.candidate_count_before_review,
        post_review_dedup_drop=inputs.post_review_dedup_drop,
        final_description_dedup_drop=final_description_dedup_drop,
        semantic_dedup_dropped_total=semantic_dedup_dropped_total,
        flow_governance_summary=inputs.flow_governance_summary,
        effective_low_quality_dropped_total=effective_low_quality_dropped_total,
        governance_hard_drop_total=governance_hard_drop_total,
        final_count=inputs.final_count,
    )
    target_min = target_satisfaction_state["target_min"]
    target_max = target_satisfaction_state["target_max"]
    recommended_range = target_satisfaction_state["recommended_range"]
    expected_count_explicit = target_satisfaction_state["expected_count_explicit"]
    target_final_count = target_satisfaction_state["target_final_count"]
    soft_min_count = target_satisfaction_state["soft_min_count"]
    hard_min_count = target_satisfaction_state["hard_min_count"]
    valid_unique_candidate_count = target_satisfaction_state["valid_unique_candidate_count"]
    min_acceptable_final = target_satisfaction_state["min_acceptable_final"]
    target_satisfaction_ratio = target_satisfaction_state["target_satisfaction_ratio"]
    target_warning = target_satisfaction_state["target_warning"]
    underfilled = target_satisfaction_state["underfilled"]
    final_stage_pruning_counts = _resolve_final_stage_pruning_counts(
        effective_low_quality_dropped_total=effective_low_quality_dropped_total,
        governance_hard_drop_total=governance_hard_drop_total,
        judge_summary_payload=inputs.judge_summary_payload,
        review_selected_count=inputs.review_selected_count,
        final_count=inputs.final_count,
        flow_governance_summary=inputs.flow_governance_summary,
        final_description_dedup_drop=final_description_dedup_drop,
        drop_by_review_llm_count=inputs.drop_by_review_llm_count,
        review_decision_summary=inputs.review_decision_summary,
        total_dedup_drop=total_dedup_drop,
        semantic_dedup_dropped_total=semantic_dedup_dropped_total,
        postprocess_filter_drop_total=postprocess_filter_drop_total,
    )
    quality_rejected_count = final_stage_pruning_counts["quality_rejected_count"]
    judge_reject_count = final_stage_pruning_counts["judge_reject_count"]
    judge_pending_count = final_stage_pruning_counts["judge_pending_count"]
    judge_pass_count = final_stage_pruning_counts["judge_pass_count"]
    final_input_count = final_stage_pruning_counts["final_input_count"]
    final_non_judge_drop_count = final_stage_pruning_counts["final_non_judge_drop_count"]
    scenario_duplicate_pruned_count = final_stage_pruning_counts["scenario_duplicate_pruned_count"]
    post_review_dedup_reorder_drop_count = final_stage_pruning_counts[
        "post_review_dedup_reorder_drop_count"
    ]
    review_selector_pruned_count = final_stage_pruning_counts["review_selector_pruned_count"]
    duplicate_pruned_count = final_stage_pruning_counts["duplicate_pruned_count"]
    invalid_pruned_count = final_stage_pruning_counts["invalid_pruned_count"]
    underfill_diagnostics = _resolve_underfill_diagnostics(
        underfilled=underfilled,
        valid_unique_candidate_count=valid_unique_candidate_count,
        hard_min_count=hard_min_count,
        scenario_duplicate_pruned_count=scenario_duplicate_pruned_count,
        review_selector_pruned_count=review_selector_pruned_count,
        duplicate_pruned_count=duplicate_pruned_count,
        quality_rejected_count=quality_rejected_count,
        final_non_judge_drop_count=final_non_judge_drop_count,
        min_acceptable_final=min_acceptable_final,
        final_count=inputs.final_count,
    )
    underfill_reason = underfill_diagnostics["underfill_reason"]
    underfill_root_cause = underfill_diagnostics["underfill_root_cause"]
    underfill_level = underfill_diagnostics["underfill_level"]
    completion_reason_lists = _resolve_completion_reason_lists(
        reasons=reasons,
        summary_stop_reason=summary_stop_reason,
        underfilled=underfilled,
        target_warning=target_warning,
    )
    reasons = completion_reason_lists["reasons"]
    summary_stop_reason = completion_reason_lists["summary_stop_reason"]

    final_case_summary = _final_case_breakdown(
        _dict_case_items(inputs.parsed_result),
        final_count=int(inputs.final_count or 0),
    )
    generation_summary = _build_generation_summary(
        recommended_range=recommended_range,
        generation_coverage_mode=inputs.generation_coverage_mode,
        generation_mode=inputs.generation_mode,
        effective_generation_coverage_mode_source=inputs.effective_generation_coverage_mode_source,
        explicit_generation_mode_override=inputs.explicit_generation_mode_override,
        explicit_expected_count_floor_preserved=inputs.explicit_expected_count_floor_preserved,
        expected_count=inputs.expected_count,
        expected_count_explicit=expected_count_explicit,
        target_min=target_min,
        target_max=target_max,
        target_final_count=target_final_count,
        soft_min_count=soft_min_count,
        hard_min_count=hard_min_count,
        min_acceptable_final=min_acceptable_final,
        target_satisfaction_ratio=target_satisfaction_ratio,
        underfilled=underfilled,
        underfill_level=underfill_level,
        underfill_reason=underfill_reason,
        underfill_root_cause=underfill_root_cause,
        final_count=inputs.final_count,
        converged=converged,
        summary_stop_reason=summary_stop_reason,
        quality_assessment=quality_assessment,
        needs_priority_review=needs_priority_review,
        priority_conflict_count=priority_conflict_count,
        priority_undetermined_count=priority_undetermined_count,
        priority_optional_count=priority_optional_count,
        final_case_summary=final_case_summary,
    )
    convergence_debug = _build_convergence_debug(
        reference_count_effective=inputs.reference_count_effective,
        final_count=inputs.final_count,
        reference_gap=reference_gap,
        converged=converged,
        duplication_rate_estimate=duplication_rate_estimate,
        stage_counts=inputs.stage_counts,
        candidate_count_before_review=inputs.candidate_count_before_review,
        review_selected_count=inputs.review_selected_count,
        post_review_dedup_drop=inputs.post_review_dedup_drop,
        judge_reject_count=judge_reject_count,
        judge_pending_count=judge_pending_count,
        judge_pass_count=judge_pass_count,
        final_input_count=final_input_count,
        final_non_judge_drop_count=final_non_judge_drop_count,
        scenario_duplicate_pruned_count=scenario_duplicate_pruned_count,
        post_review_dedup_reorder_drop_count=post_review_dedup_reorder_drop_count,
        final_description_dedup_drop=final_description_dedup_drop,
        total_dedup_drop=total_dedup_drop,
        gap_attempts=inputs.gap_attempts,
        gap_remaining_after_attempts=inputs.gap_remaining_after_attempts,
        missing_rules_final=missing_rules_final,
        missing_types_final=missing_types_final,
        effective_low_quality_dropped_total=effective_low_quality_dropped_total,
        low_quality_drop_details=low_quality_drop_details,
        postprocess_filter_drop_total=postprocess_filter_drop_total,
        semantic_dedup_dropped_total=semantic_dedup_dropped_total,
        governance_hard_drop_total=governance_hard_drop_total,
        duplicate_pruned_count=duplicate_pruned_count,
        invalid_pruned_count=invalid_pruned_count,
        quality_rejected_count=quality_rejected_count,
        review_selector_pruned_count=review_selector_pruned_count,
        valid_unique_candidate_count=valid_unique_candidate_count,
        generation_coverage_mode=inputs.generation_coverage_mode,
        generation_mode=inputs.generation_mode,
        effective_generation_coverage_mode_source=inputs.effective_generation_coverage_mode_source,
        explicit_generation_mode_override=inputs.explicit_generation_mode_override,
        explicit_expected_count_floor_preserved=inputs.explicit_expected_count_floor_preserved,
        expected_count=inputs.expected_count,
        expected_count_explicit=expected_count_explicit,
        target_min=target_min,
        target_max=target_max,
        target_final_count=target_final_count,
        soft_min_count=soft_min_count,
        hard_min_count=hard_min_count,
        min_acceptable_final=min_acceptable_final,
        target_satisfaction_ratio=target_satisfaction_ratio,
        underfilled=underfilled,
        underfill_level=underfill_level,
        underfill_reason=underfill_reason,
        underfill_root_cause=underfill_root_cause,
        append_target_count=inputs.append_target_count,
        append_final_cap_count=inputs.append_final_cap_count,
        append_cap_drop_total=inputs.append_cap_drop_total,
        flow_governance_summary=inputs.flow_governance_summary,
        needs_priority_review=needs_priority_review,
        priority_conflict_count=priority_conflict_count,
        priority_undetermined_count=priority_undetermined_count,
        priority_optional_count=priority_optional_count,
        reasons=reasons,
        generation_target_case_range=inputs.generation_target_case_range,
    )
    return FinalGenerationReport(
        coverage=dict(coverage or {}),
        generation_summary=dict(generation_summary or {}),
        convergence_debug=dict(convergence_debug or {}),
    )
