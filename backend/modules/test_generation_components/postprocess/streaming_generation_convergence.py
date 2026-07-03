from __future__ import annotations

from typing import Any


def _dict_item_count(items: Any) -> int:
    return len([item for item in items if isinstance(item, dict)])


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
