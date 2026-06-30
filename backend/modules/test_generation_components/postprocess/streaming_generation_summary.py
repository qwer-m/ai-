from __future__ import annotations

from typing import Any, Callable


def _int_or_zero(value: Any) -> int:
    return int(value or 0)


def _dict_item_count(items: Any) -> int:
    return len([item for item in items if isinstance(item, dict)])


def resolve_append_reference_counts(
    *,
    append: Any = False,
    expected_count: Any = 0,
    existing_unique_count: Any = 0,
) -> dict[str, int]:
    append_target_count = 0
    if append:
        append_target_count = max(0, int(expected_count or 0) - int(existing_unique_count or 0))
    reference_count_effective = max(
        1,
        int(append_target_count or 0) if append and int(append_target_count or 0) > 0 else int(expected_count or 1),
    )
    append_final_cap_count = int(append_target_count or 0) if append and int(append_target_count or 0) > 0 else 0
    return {
        "append_target_count": int(append_target_count),
        "reference_count_effective": int(reference_count_effective),
        "append_final_cap_count": int(append_final_cap_count),
    }


def resolve_final_stage_pruning_counts(
    *,
    effective_low_quality_dropped_total: Any = 0,
    governance_hard_drop_total: Any = 0,
    judge_summary_payload: dict[str, Any] | None = None,
    review_selected_count: Any = 0,
    final_count: Any = 0,
    flow_governance_summary: dict[str, Any] | None = None,
    final_description_dedup_drop: Any = 0,
    drop_by_review_llm_count: Any = 0,
    review_decision_summary: dict[str, Any] | None = None,
    total_dedup_drop: Any = 0,
    semantic_dedup_dropped_total: Any = 0,
    postprocess_filter_drop_total: Any = 0,
) -> dict[str, int]:
    judge_summary_payload = judge_summary_payload or {}
    flow_governance_summary = flow_governance_summary or {}
    review_decision_summary = review_decision_summary or {}

    quality_rejected_count = int(effective_low_quality_dropped_total or 0) + int(
        governance_hard_drop_total or 0
    )
    try:
        quality_rejected_count += int(judge_summary_payload.get("reject_count") or 0)
    except Exception:
        pass

    try:
        judge_reject_count = int(
            judge_summary_payload.get("rejected_out_count")
            or judge_summary_payload.get("reject_count")
            or 0
        )
    except Exception:
        judge_reject_count = 0
    judge_pending_count = int(
        judge_summary_payload.get("pending_out_count")
        or judge_summary_payload.get("pending_count")
        or 0
    )
    judge_pass_count = int(
        judge_summary_payload.get("confirmed_pass_out_count")
        or judge_summary_payload.get("pass_count")
        or 0
    ) + int(judge_summary_payload.get("repaired_pass_out_count") or 0)
    final_input_count = int(judge_pass_count or review_selected_count or 0)
    final_non_judge_drop_count = max(0, int(final_input_count or 0) - int(final_count or 0))
    scenario_duplicate_pruned_count = int(
        flow_governance_summary.get("scenario_duplicate_pruned_count") or 0
    )
    post_review_dedup_reorder_drop_count = max(
        0,
        int(final_non_judge_drop_count or 0)
        - int(scenario_duplicate_pruned_count or 0)
        - int(final_description_dedup_drop or 0),
    )
    review_selector_pruned_count = int(drop_by_review_llm_count or 0) + int(
        review_decision_summary.get("drop_by_review_gate_count") or 0
    )
    duplicate_pruned_count = int(total_dedup_drop or 0) + int(
        semantic_dedup_dropped_total or 0
    )
    invalid_pruned_count = int(postprocess_filter_drop_total or 0) + int(
        governance_hard_drop_total or 0
    )

    return {
        "quality_rejected_count": quality_rejected_count,
        "judge_reject_count": judge_reject_count,
        "judge_pending_count": judge_pending_count,
        "judge_pass_count": judge_pass_count,
        "final_input_count": final_input_count,
        "final_non_judge_drop_count": final_non_judge_drop_count,
        "scenario_duplicate_pruned_count": scenario_duplicate_pruned_count,
        "post_review_dedup_reorder_drop_count": post_review_dedup_reorder_drop_count,
        "review_selector_pruned_count": review_selector_pruned_count,
        "duplicate_pruned_count": duplicate_pruned_count,
        "invalid_pruned_count": invalid_pruned_count,
    }


def derive_final_coverage_convergence_inputs(
    *,
    pre_priority_coverage: dict[str, Any] | None = None,
    reference_count_effective: Any = 0,
    final_count: Any = 0,
    gap_remaining_after_attempts: Any = 0,
    gap_attempts: Any = 0,
    gap_stopped_by_provider_error: Any = False,
) -> dict[str, Any]:
    coverage = {
        **dict(pre_priority_coverage or {}),
        "kind": "coverage_check",
    }
    missing_rules_final = list(coverage.get("missing_rules") or [])
    missing_types_final = any(
        bool(item.get("missing_types"))
        for item in (coverage.get("rule_diagnostics") or [])
        if isinstance(item, dict)
    )
    reference_gap = max(0, int(reference_count_effective or 0) - int(final_count or 0))
    has_missing_coverage = bool(missing_rules_final) or bool(missing_types_final)
    explicit_converged = coverage.get("converged")
    if explicit_converged is None:
        converged = not has_missing_coverage
    else:
        converged = bool(explicit_converged) and not has_missing_coverage

    reasons: list[str] = []
    gap_remaining_count = int(gap_remaining_after_attempts or 0)
    if not converged:
        if gap_remaining_count > 0:
            reasons.append("coverage_gap_still_exists")
        if int(gap_attempts or 0) >= 3 and gap_remaining_count > 0:
            reasons.append("gap_attempt_limit_reached")
        if gap_stopped_by_provider_error:
            reasons.append("gap_stopped_by_provider_error")
        if missing_rules_final:
            reasons.append("coverage_missing_rules")
        if missing_types_final:
            reasons.append("coverage_missing_types")
        if not reasons:
            reasons.append("coverage_not_converged")
    elif reference_gap > 0:
        reasons.append("quality_converged_before_reference_count")

    return {
        "coverage": coverage,
        "missing_rules_final": missing_rules_final,
        "missing_types_final": missing_types_final,
        "reference_gap": reference_gap,
        "converged": converged,
        "reasons": reasons,
    }


def derive_convergence_reason_state(
    *,
    reasons: list[str] | None = None,
    converged: Any = False,
    reference_gap: Any = 0,
    post_review_dedup_drop: Any = 0,
    final_description_dedup_drop_signatures: Any = None,
    low_quality_drop_details: Any = None,
    low_quality_dropped_total: Any = 0,
    semantic_dedup_dropped_total: Any = 0,
    governance_hard_drop_total: Any = 0,
    postprocess_filter_drop_total: Any = 0,
    append_cap_drop_total: Any = 0,
    flow_governance_summary: dict[str, Any] | None = None,
    review_selected_count: Any = 0,
) -> dict[str, Any]:
    derived_reasons = list(reasons or [])
    flow_governance_summary = flow_governance_summary or {}

    final_description_dedup_drop = int(len(final_description_dedup_drop_signatures or set()))
    post_review_dedup_drop_count = int(post_review_dedup_drop or 0)
    total_dedup_drop = post_review_dedup_drop_count + int(final_description_dedup_drop or 0)
    low_quality_dropped_total_count = int(low_quality_dropped_total or 0)
    semantic_dedup_dropped_total_count = int(semantic_dedup_dropped_total or 0)
    governance_hard_drop_total_count = int(governance_hard_drop_total or 0)
    postprocess_filter_drop_total_count = int(postprocess_filter_drop_total or 0)
    append_cap_drop_total_count = int(append_cap_drop_total or 0)
    reference_gap_count = int(reference_gap or 0)
    review_selected_count_value = int(review_selected_count or 0)
    effective_low_quality_dropped_total = max(
        low_quality_dropped_total_count,
        _dict_item_count(low_quality_drop_details or []),
    )

    if post_review_dedup_drop_count > 0:
        derived_reasons.append("dedup_reduced_count_stop")
        derived_reasons.append("no_backfill_after_dedup")
    if final_description_dedup_drop > 0:
        derived_reasons.append("final_description_dedup_reduced_count")

    if effective_low_quality_dropped_total > 0:
        derived_reasons.append("low_quality_filtered")
    if semantic_dedup_dropped_total_count > 0:
        derived_reasons.append("semantic_dedup_reduced_count")
    if governance_hard_drop_total_count > 0:
        derived_reasons.append("governance_hard_drop_applied")
    if append_cap_drop_total_count > 0:
        derived_reasons.append("append_target_cap_applied")
    if int(flow_governance_summary.get("scenario_duplicate_pruned_count") or 0) > 0:
        derived_reasons.append("flow_scenario_duplicate_pruned")
    if bool(flow_governance_summary.get("flow_reordered")):
        derived_reasons.append("flow_structure_reordered")

    duplication_rate_estimate = 0.0
    if review_selected_count_value > 0:
        duplication_rate_estimate = float(total_dedup_drop or 0) / float(review_selected_count_value or 1)

    summary_stop_reason: list[str] = []
    if converged:
        summary_stop_reason.append("coverage_satisfied")

    diminishing_returns = bool(
        reference_gap_count > 0
        or post_review_dedup_drop_count > 0
        or duplication_rate_estimate > 0.5
        or "quality_converged_before_reference_count" in derived_reasons
    )
    if diminishing_returns or (not converged and derived_reasons):
        summary_stop_reason.append("stopped_due_to_diminishing_returns")

    if converged:
        summary_stop_reason.append("optimal_case_set_reached")

    if converged and effective_low_quality_dropped_total <= 0 and duplication_rate_estimate <= 0.5:
        quality_assessment = "high"
    elif effective_low_quality_dropped_total <= 2 and duplication_rate_estimate <= 0.6:
        quality_assessment = "medium"
    else:
        quality_assessment = "low"

    return {
        "reasons": derived_reasons,
        "final_description_dedup_drop": final_description_dedup_drop,
        "total_dedup_drop": total_dedup_drop,
        "low_quality_drop_details": low_quality_drop_details,
        "low_quality_dropped_total": low_quality_dropped_total_count,
        "semantic_dedup_dropped_total": semantic_dedup_dropped_total_count,
        "governance_hard_drop_total": governance_hard_drop_total_count,
        "postprocess_filter_drop_total": postprocess_filter_drop_total_count,
        "effective_low_quality_dropped_total": effective_low_quality_dropped_total,
        "duplication_rate_estimate": duplication_rate_estimate,
        "summary_stop_reason": summary_stop_reason,
        "quality_assessment": quality_assessment,
    }


def resolve_completion_reason_lists(
    *,
    reasons: list[str] | None = None,
    summary_stop_reason: list[str] | None = None,
    underfilled: Any = False,
    target_warning: Any = False,
) -> dict[str, list[str]]:
    completion_reasons = list(reasons or [])
    completion_summary_stop_reason = list(summary_stop_reason or [])

    if underfilled:
        if "underfilled" not in completion_reasons:
            completion_reasons.append("underfilled")
        if "underfilled" not in completion_summary_stop_reason:
            completion_summary_stop_reason.append("underfilled")
    if target_warning:
        if "target_count_warning" not in completion_reasons:
            completion_reasons.append("target_count_warning")
        if "target_count_warning" not in completion_summary_stop_reason:
            completion_summary_stop_reason.append("target_count_warning")

    return {
        "reasons": completion_reasons,
        "summary_stop_reason": completion_summary_stop_reason,
    }


def resolve_generation_target_satisfaction(
    *,
    generation_target_case_range: dict[str, Any] | None = None,
    expected_count: Any = 0,
    reference_count_effective: Any = 0,
    generation_coverage_mode: Any = None,
    resolved_full_regression_floor: Any = 0,
    candidate_count_before_review: Any = 0,
    post_review_dedup_drop: Any = 0,
    final_description_dedup_drop: Any = 0,
    semantic_dedup_dropped_total: Any = 0,
    flow_governance_summary: dict[str, Any] | None = None,
    effective_low_quality_dropped_total: Any = 0,
    governance_hard_drop_total: Any = 0,
    final_count: Any = 0,
) -> dict[str, Any]:
    generation_target_case_range = generation_target_case_range or {}
    flow_governance_summary = flow_governance_summary or {}

    target_min = (
        generation_target_case_range.get("min")
        if isinstance(generation_target_case_range, dict)
        else None
    )
    target_max = (
        generation_target_case_range.get("max")
        if isinstance(generation_target_case_range, dict)
        else None
    )
    recommended_range = (
        f"{int(target_min)}-{int(target_max)}"
        if target_min is not None and target_max is not None
        else "30-50"
    )
    try:
        target_min_count = int(target_min or 0)
    except Exception:
        target_min_count = 0
    try:
        target_max_count = int(target_max or 0)
    except Exception:
        target_max_count = 0

    expected_count_explicit = bool(int(expected_count or 0) > 0)
    target_final_count = (
        int(expected_count or reference_count_effective or 0)
        if expected_count_explicit
        else int(reference_count_effective or 0)
    )
    if target_final_count <= 0 and target_min is not None and target_max is not None:
        target_final_count = int(round((int(target_min) + int(target_max)) / 2))

    soft_min_count = int(round(float(target_final_count or 0) * 0.80)) if target_final_count > 0 else 0
    hard_min_count = int(round(float(target_final_count or 0) * 0.70)) if target_final_count > 0 else 0
    if str(generation_coverage_mode or "") == "full_functional_regression":
        hard_min_count = max(int(hard_min_count or 0), int(resolved_full_regression_floor or 0))

    valid_unique_candidate_count = int(candidate_count_before_review or 0)
    postprocess_pruned_count = (
        int(post_review_dedup_drop or 0)
        + int(final_description_dedup_drop or 0)
        + int(semantic_dedup_dropped_total or 0)
        + int(flow_governance_summary.get("scenario_duplicate_pruned_count") or 0)
        + int(effective_low_quality_dropped_total or 0)
        + int(governance_hard_drop_total or 0)
    )
    recommended_floor_underfilled = bool(
        expected_count_explicit
        and str(generation_coverage_mode or "") != "full_functional_regression"
        and target_min_count > 0
        and int(valid_unique_candidate_count or 0) >= int(target_min_count)
        and int(final_count or 0) < int(target_min_count)
        and int(postprocess_pruned_count or 0) > 0
    )

    if expected_count_explicit and target_final_count > 0:
        if str(generation_coverage_mode or "") == "full_functional_regression":
            min_acceptable_final = hard_min_count
        elif valid_unique_candidate_count >= int(round(float(target_final_count) * 0.90)):
            min_acceptable_final = soft_min_count
        else:
            min_acceptable_final = min(valid_unique_candidate_count, hard_min_count)
        if recommended_floor_underfilled:
            min_acceptable_final = max(int(min_acceptable_final or 0), int(target_min_count or 0))
    else:
        min_acceptable_final = 0

    target_satisfaction_denominator = int(target_final_count or 0)
    if recommended_floor_underfilled and target_max_count > 0:
        target_satisfaction_denominator = max(
            int(target_satisfaction_denominator or 0),
            int(target_max_count or 0),
        )
    target_satisfaction_ratio = (
        round(
            float(final_count or 0) / float(target_satisfaction_denominator or 1),
            4,
        )
        if target_satisfaction_denominator > 0
        else 1.0
    )
    target_warning = bool(
        expected_count_explicit
        and target_final_count > 0
        and int(final_count or 0) < soft_min_count
    )
    underfilled = bool(
        expected_count_explicit
        and min_acceptable_final > 0
        and int(final_count or 0) < int(min_acceptable_final)
    )

    return {
        "target_min": target_min,
        "target_max": target_max,
        "recommended_range": recommended_range,
        "target_min_count": target_min_count,
        "target_max_count": target_max_count,
        "expected_count_explicit": expected_count_explicit,
        "target_final_count": target_final_count,
        "soft_min_count": soft_min_count,
        "hard_min_count": hard_min_count,
        "valid_unique_candidate_count": valid_unique_candidate_count,
        "postprocess_pruned_count": postprocess_pruned_count,
        "recommended_floor_underfilled": recommended_floor_underfilled,
        "min_acceptable_final": min_acceptable_final,
        "target_satisfaction_denominator": target_satisfaction_denominator,
        "target_satisfaction_ratio": target_satisfaction_ratio,
        "target_warning": target_warning,
        "underfilled": underfilled,
    }


def resolve_underfill_diagnostics(
    *,
    underfilled: Any = False,
    valid_unique_candidate_count: Any = 0,
    hard_min_count: Any = 0,
    scenario_duplicate_pruned_count: Any = 0,
    review_selector_pruned_count: Any = 0,
    duplicate_pruned_count: Any = 0,
    quality_rejected_count: Any = 0,
    final_non_judge_drop_count: Any = 0,
    min_acceptable_final: Any = 0,
    final_count: Any = 0,
) -> dict[str, str]:
    if not underfilled:
        underfill_reason = ""
    elif int(valid_unique_candidate_count or 0) < int(hard_min_count or 0):
        underfill_reason = "valid_candidate_insufficient"
    elif (
        int(scenario_duplicate_pruned_count or 0) > 0
        and int(scenario_duplicate_pruned_count or 0) >= int(review_selector_pruned_count or 0)
        and int(duplicate_pruned_count or 0) <= 0
    ):
        underfill_reason = "scenario_cap_over_pruned"
    elif int(review_selector_pruned_count or 0) > int(duplicate_pruned_count or 0) and int(
        review_selector_pruned_count or 0
    ) >= int(quality_rejected_count or 0):
        underfill_reason = "review_selector_over_pruned"
    elif int(duplicate_pruned_count or 0) >= int(quality_rejected_count or 0):
        underfill_reason = "duplicate_pruned_under_target"
    elif int(quality_rejected_count or 0) > 0:
        underfill_reason = "quality_rejected_under_target"
    else:
        underfill_reason = "final_count_below_expected_target"

    if not underfilled:
        underfill_root_cause = ""
    elif underfill_reason == "scenario_cap_over_pruned":
        underfill_root_cause = "final_stage_over_pruning"
    elif (
        underfill_reason in {"duplicate_pruned_under_target", "quality_rejected_under_target"}
        and int(final_non_judge_drop_count or 0) > int(review_selector_pruned_count or 0)
    ):
        underfill_root_cause = "final_stage_over_pruning"
    elif underfill_reason == "review_selector_over_pruned":
        underfill_root_cause = "review_stage_over_pruning"
    elif underfill_reason == "valid_candidate_insufficient":
        underfill_root_cause = "candidate_insufficient"
    else:
        underfill_root_cause = "target_not_satisfied"

    if not underfilled:
        underfill_level = ""
    else:
        shortfall = max(0, int(min_acceptable_final or 0) - int(final_count or 0))
        if shortfall <= 5:
            underfill_level = "mild"
        elif shortfall <= 20:
            underfill_level = "moderate"
        else:
            underfill_level = "severe"

    return {
        "underfill_reason": underfill_reason,
        "underfill_root_cause": underfill_root_cause,
        "underfill_level": underfill_level,
    }


def build_generation_summary(
    *,
    recommended_range: Any = None,
    generation_coverage_mode: Any = None,
    generation_mode: Any = None,
    effective_generation_coverage_mode_source: Any = None,
    explicit_generation_mode_override: Any = False,
    explicit_expected_count_floor_preserved: Any = False,
    expected_count: Any = 0,
    expected_count_explicit: Any = False,
    target_min: Any = None,
    target_max: Any = None,
    target_final_count: Any = 0,
    soft_min_count: Any = 0,
    hard_min_count: Any = 0,
    min_acceptable_final: Any = 0,
    target_satisfaction_ratio: Any = 0.0,
    underfilled: Any = False,
    underfill_level: Any = "",
    underfill_reason: Any = "",
    underfill_root_cause: Any = "",
    final_count: Any = 0,
    converged: Any = False,
    summary_stop_reason: Any = None,
    quality_assessment: Any = None,
    needs_priority_review: Any = False,
    priority_conflict_count: Any = 0,
    priority_undetermined_count: Any = 0,
    priority_optional_count: Any = 0,
    final_case_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_case_summary = final_case_summary or {}

    return {
        "recommended_range": recommended_range,
        "generation_coverage_mode": str(generation_coverage_mode or "core_smoke"),
        "requested_generation_mode": str(generation_mode or ""),
        "effective_generation_coverage_mode_source": str(effective_generation_coverage_mode_source or ""),
        "explicit_generation_mode_override": bool(explicit_generation_mode_override),
        "explicit_expected_count_floor_preserved": bool(explicit_expected_count_floor_preserved),
        "expected_count": _int_or_zero(expected_count),
        "expected_count_explicit": bool(expected_count_explicit),
        "recommended_min": _int_or_zero(target_min) if target_min is not None else 0,
        "recommended_max": _int_or_zero(target_max) if target_max is not None else 0,
        "target_final_count": _int_or_zero(target_final_count),
        "soft_min_count": _int_or_zero(soft_min_count),
        "hard_min_count": _int_or_zero(hard_min_count),
        "min_acceptable_final": _int_or_zero(min_acceptable_final),
        "target_satisfaction_ratio": float(target_satisfaction_ratio),
        "underfilled": bool(underfilled),
        "underfill_level": str(underfill_level),
        "underfill_reason": str(underfill_reason),
        "underfill_root_cause": str(underfill_root_cause),
        "final_count": _int_or_zero(final_count),
        "status": "completed_underfilled"
        if underfilled
        else ("completed_with_quality_stop" if not converged else "completed_with_optimal_set"),
        "stop_reason": summary_stop_reason,
        "quality_assessment": quality_assessment,
        "needs_priority_review": bool(needs_priority_review),
        "priority_conflict_count": int(priority_conflict_count),
        "priority_undetermined_count": int(priority_undetermined_count),
        "priority_optional_count": int(priority_optional_count),
        "final_priority_breakdown": dict(final_case_summary.get("final_priority_breakdown") or {}),
        "final_execution_group_breakdown": dict(
            final_case_summary.get("final_execution_group_breakdown") or {}
        ),
        "final_module_breakdown_top": dict(final_case_summary.get("final_module_breakdown_top") or {}),
        "final_display_case_count": _int_or_zero(final_case_summary.get("final_display_case_count")),
        "final_display_ratio": float(final_case_summary.get("final_display_ratio") or 0.0),
        "final_high_priority_ratio": float(final_case_summary.get("final_high_priority_ratio") or 0.0),
    }


def build_convergence_count_debug_fields(
    *,
    reference_count_effective: Any = 0,
    final_count: Any = 0,
    reference_gap: Any = 0,
    converged: Any = False,
    duplication_rate_estimate: Any = 0.0,
    stage_counts: dict[str, Any] | None = None,
    candidate_count_before_review: Any = 0,
    review_selected_count: Any = 0,
    post_review_dedup_drop: Any = 0,
    judge_reject_count: Any = 0,
    judge_pending_count: Any = 0,
    judge_pass_count: Any = 0,
    final_input_count: Any = 0,
    final_non_judge_drop_count: Any = 0,
    scenario_duplicate_pruned_count: Any = 0,
    post_review_dedup_reorder_drop_count: Any = 0,
    final_description_dedup_drop: Any = 0,
    total_dedup_drop: Any = 0,
    gap_attempts: Any = 0,
    gap_remaining_after_attempts: Any = 0,
    missing_rules_final: Any = None,
    missing_types_final: Any = None,
) -> dict[str, Any]:
    stage_counts = stage_counts or {}

    return {
        "suggested_count": _int_or_zero(reference_count_effective),
        "final_count": _int_or_zero(final_count),
        "reference_gap": _int_or_zero(reference_gap),
        "converged": bool(converged),
        "duplication_rate_estimate": float(duplication_rate_estimate),
        "primary_count": _int_or_zero(stage_counts.get("primary")),
        "gap_count": _int_or_zero(stage_counts.get("gap")),
        "review_count": _int_or_zero(stage_counts.get("review")),
        "candidate_count_before_review": _int_or_zero(candidate_count_before_review),
        "review_selected_count": _int_or_zero(review_selected_count),
        "post_review_dedup_drop": _int_or_zero(post_review_dedup_drop),
        "judge_reject_count": _int_or_zero(judge_reject_count),
        "judge_pending_count": _int_or_zero(judge_pending_count),
        "judge_pass_count": _int_or_zero(judge_pass_count),
        "final_input_count": _int_or_zero(final_input_count),
        "final_output_count": _int_or_zero(final_count),
        "final_non_judge_drop_count": _int_or_zero(final_non_judge_drop_count),
        "scenario_duplicate_pruned_count": _int_or_zero(scenario_duplicate_pruned_count),
        "post_review_dedup_reorder_drop_count": _int_or_zero(post_review_dedup_reorder_drop_count),
        "final_description_dedup_drop_count": _int_or_zero(final_description_dedup_drop),
        "total_dedup_drop_count": _int_or_zero(total_dedup_drop),
        "gap_attempts": _int_or_zero(gap_attempts),
        "gap_remaining_after_attempts": _int_or_zero(gap_remaining_after_attempts),
        "missing_rules_count": int(len(missing_rules_final or [])),
        "missing_types_exists": bool(missing_types_final),
    }


def build_convergence_pruning_debug_fields(
    *,
    effective_low_quality_dropped_total: Any = 0,
    low_quality_drop_details: Any = None,
    postprocess_filter_drop_total: Any = 0,
    semantic_dedup_dropped_total: Any = 0,
    governance_hard_drop_total: Any = 0,
    duplicate_pruned_count: Any = 0,
    invalid_pruned_count: Any = 0,
    quality_rejected_count: Any = 0,
    review_selector_pruned_count: Any = 0,
    valid_unique_candidate_count: Any = 0,
) -> dict[str, Any]:
    return {
        "low_quality_dropped_count": _int_or_zero(effective_low_quality_dropped_total),
        "low_quality_dropped_examples": [
            dict(item)
            for item in (low_quality_drop_details or [])[:10]
            if isinstance(item, dict)
        ],
        "postprocess_filter_drop_total": _int_or_zero(postprocess_filter_drop_total),
        "semantic_dedup_dropped_count": _int_or_zero(semantic_dedup_dropped_total),
        "governance_hard_drop_count": _int_or_zero(governance_hard_drop_total),
        "duplicate_pruned_count": _int_or_zero(duplicate_pruned_count),
        "invalid_pruned_count": _int_or_zero(invalid_pruned_count),
        "quality_rejected_count": _int_or_zero(quality_rejected_count),
        "review_selector_pruned_count": _int_or_zero(review_selector_pruned_count),
        "valid_unique_candidate_count": _int_or_zero(valid_unique_candidate_count),
    }


def build_convergence_target_debug_fields(
    *,
    generation_coverage_mode: Any = None,
    generation_mode: Any = None,
    effective_generation_coverage_mode_source: Any = None,
    explicit_generation_mode_override: Any = False,
    explicit_expected_count_floor_preserved: Any = False,
    expected_count: Any = 0,
    expected_count_explicit: Any = False,
    target_min: Any = None,
    target_max: Any = None,
    target_final_count: Any = 0,
    soft_min_count: Any = 0,
    hard_min_count: Any = 0,
    min_acceptable_final: Any = 0,
    target_satisfaction_ratio: Any = 0.0,
    underfilled: Any = False,
    underfill_level: Any = "",
    underfill_reason: Any = "",
    underfill_root_cause: Any = "",
    append_target_count: Any = 0,
    append_final_cap_count: Any = 0,
    append_cap_drop_total: Any = 0,
    flow_governance_summary: Any = None,
    needs_priority_review: Any = False,
    priority_conflict_count: Any = 0,
    priority_undetermined_count: Any = 0,
    priority_optional_count: Any = 0,
    reasons: Any = None,
    generation_target_case_range: Any = None,
) -> dict[str, Any]:
    return {
        "generation_coverage_mode": str(generation_coverage_mode or "core_smoke"),
        "requested_generation_mode": str(generation_mode or ""),
        "effective_generation_coverage_mode_source": str(effective_generation_coverage_mode_source or ""),
        "explicit_generation_mode_override": bool(explicit_generation_mode_override),
        "explicit_expected_count_floor_preserved": bool(explicit_expected_count_floor_preserved),
        "expected_count": _int_or_zero(expected_count),
        "expected_count_explicit": bool(expected_count_explicit),
        "recommended_min": _int_or_zero(target_min) if target_min is not None else 0,
        "recommended_max": _int_or_zero(target_max) if target_max is not None else 0,
        "target_final_count": _int_or_zero(target_final_count),
        "soft_min_count": _int_or_zero(soft_min_count),
        "hard_min_count": _int_or_zero(hard_min_count),
        "min_acceptable_final": _int_or_zero(min_acceptable_final),
        "target_satisfaction_ratio": float(target_satisfaction_ratio),
        "underfilled": bool(underfilled),
        "underfill_level": str(underfill_level),
        "underfill_reason": str(underfill_reason),
        "underfill_root_cause": str(underfill_root_cause),
        "append_target_count": _int_or_zero(append_target_count),
        "append_final_cap_count": _int_or_zero(append_final_cap_count),
        "append_cap_drop_count": _int_or_zero(append_cap_drop_total),
        "flow_governance": dict(flow_governance_summary or {}),
        "needs_priority_review": bool(needs_priority_review),
        "priority_conflict_count": int(priority_conflict_count),
        "priority_undetermined_count": int(priority_undetermined_count),
        "priority_optional_count": int(priority_optional_count),
        "reasons": reasons,
        "generation_target_case_range": dict(generation_target_case_range or {}),
    }


def build_convergence_debug(
    *,
    reference_count_effective: Any = 0,
    final_count: Any = 0,
    reference_gap: Any = 0,
    converged: Any = False,
    duplication_rate_estimate: Any = 0.0,
    stage_counts: dict[str, Any] | None = None,
    candidate_count_before_review: Any = 0,
    review_selected_count: Any = 0,
    post_review_dedup_drop: Any = 0,
    judge_reject_count: Any = 0,
    judge_pending_count: Any = 0,
    judge_pass_count: Any = 0,
    final_input_count: Any = 0,
    final_non_judge_drop_count: Any = 0,
    scenario_duplicate_pruned_count: Any = 0,
    post_review_dedup_reorder_drop_count: Any = 0,
    final_description_dedup_drop: Any = 0,
    total_dedup_drop: Any = 0,
    gap_attempts: Any = 0,
    gap_remaining_after_attempts: Any = 0,
    missing_rules_final: Any = None,
    missing_types_final: Any = None,
    effective_low_quality_dropped_total: Any = 0,
    low_quality_drop_details: Any = None,
    postprocess_filter_drop_total: Any = 0,
    semantic_dedup_dropped_total: Any = 0,
    governance_hard_drop_total: Any = 0,
    duplicate_pruned_count: Any = 0,
    invalid_pruned_count: Any = 0,
    quality_rejected_count: Any = 0,
    review_selector_pruned_count: Any = 0,
    valid_unique_candidate_count: Any = 0,
    generation_coverage_mode: Any = None,
    generation_mode: Any = None,
    effective_generation_coverage_mode_source: Any = None,
    explicit_generation_mode_override: Any = False,
    explicit_expected_count_floor_preserved: Any = False,
    expected_count: Any = 0,
    expected_count_explicit: Any = False,
    target_min: Any = None,
    target_max: Any = None,
    target_final_count: Any = 0,
    soft_min_count: Any = 0,
    hard_min_count: Any = 0,
    min_acceptable_final: Any = 0,
    target_satisfaction_ratio: Any = 0.0,
    underfilled: Any = False,
    underfill_level: Any = "",
    underfill_reason: Any = "",
    underfill_root_cause: Any = "",
    append_target_count: Any = 0,
    append_final_cap_count: Any = 0,
    append_cap_drop_total: Any = 0,
    flow_governance_summary: Any = None,
    needs_priority_review: Any = False,
    priority_conflict_count: Any = 0,
    priority_undetermined_count: Any = 0,
    priority_optional_count: Any = 0,
    reasons: Any = None,
    generation_target_case_range: Any = None,
) -> dict[str, Any]:
    return {
        **build_convergence_count_debug_fields(
            reference_count_effective=reference_count_effective,
            final_count=final_count,
            reference_gap=reference_gap,
            converged=converged,
            duplication_rate_estimate=duplication_rate_estimate,
            stage_counts=stage_counts,
            candidate_count_before_review=candidate_count_before_review,
            review_selected_count=review_selected_count,
            post_review_dedup_drop=post_review_dedup_drop,
            judge_reject_count=judge_reject_count,
            judge_pending_count=judge_pending_count,
            judge_pass_count=judge_pass_count,
            final_input_count=final_input_count,
            final_non_judge_drop_count=final_non_judge_drop_count,
            scenario_duplicate_pruned_count=scenario_duplicate_pruned_count,
            post_review_dedup_reorder_drop_count=post_review_dedup_reorder_drop_count,
            final_description_dedup_drop=final_description_dedup_drop,
            total_dedup_drop=total_dedup_drop,
            gap_attempts=gap_attempts,
            gap_remaining_after_attempts=gap_remaining_after_attempts,
            missing_rules_final=missing_rules_final,
            missing_types_final=missing_types_final,
        ),
        **build_convergence_pruning_debug_fields(
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
        ),
        **build_convergence_target_debug_fields(
            generation_coverage_mode=generation_coverage_mode,
            generation_mode=generation_mode,
            effective_generation_coverage_mode_source=effective_generation_coverage_mode_source,
            explicit_generation_mode_override=explicit_generation_mode_override,
            explicit_expected_count_floor_preserved=explicit_expected_count_floor_preserved,
            expected_count=expected_count,
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
            append_target_count=append_target_count,
            append_final_cap_count=append_final_cap_count,
            append_cap_drop_total=append_cap_drop_total,
            flow_governance_summary=flow_governance_summary,
            needs_priority_review=needs_priority_review,
            priority_conflict_count=priority_conflict_count,
            priority_undetermined_count=priority_undetermined_count,
            priority_optional_count=priority_optional_count,
            reasons=reasons,
            generation_target_case_range=generation_target_case_range,
        ),
    }


def build_stream_postprocess_result_payload(
    *,
    cases: Any,
    stage_counts: Any,
    coverage: Any,
    convergence_debug: Any,
    generation_summary: Any,
    review_decision_summary: Any,
    review_decision_table: Any,
    judge_summary: Any,
    judge_decision_table: Any,
    feedback_control_debug_builder_fn: Callable[..., Any],
    control_state: Any,
    generation_coverage_mode: Any = None,
    generation_target_case_range: Any = None,
    fact_profile: Any = None,
    project_profile: Any = None,
    manual_quality_profile: Any = None,
) -> dict[str, Any]:
    return {
        "cases": cases,
        "stage_counts": stage_counts,
        "coverage": coverage,
        "convergence_debug": convergence_debug,
        "generation_summary": generation_summary,
        "review_decision_summary": review_decision_summary,
        "review_decision_table": review_decision_table,
        "judge_summary": judge_summary,
        "judge_decision_table": judge_decision_table,
        "feedback_control_debug": feedback_control_debug_builder_fn(
            control_state=control_state,
            generation_coverage_mode=str(generation_coverage_mode or "core_smoke"),
            generation_target_case_range=generation_target_case_range,
            fact_profile=fact_profile,
            project_profile=project_profile,
            manual_quality_profile=manual_quality_profile,
        ),
    }


__all__ = [
    "build_convergence_debug",
    "build_convergence_count_debug_fields",
    "build_convergence_pruning_debug_fields",
    "build_convergence_target_debug_fields",
    "build_generation_summary",
    "build_stream_postprocess_result_payload",
    "derive_convergence_reason_state",
    "derive_final_coverage_convergence_inputs",
    "resolve_append_reference_counts",
    "resolve_completion_reason_lists",
    "resolve_final_stage_pruning_counts",
    "resolve_generation_target_satisfaction",
    "resolve_underfill_diagnostics",
]
