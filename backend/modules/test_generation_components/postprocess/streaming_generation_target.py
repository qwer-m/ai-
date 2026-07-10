from __future__ import annotations

from typing import Any


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
            min_acceptable_final = int(resolved_full_regression_floor or hard_min_count or 0)
        elif valid_unique_candidate_count >= int(round(float(target_final_count) * 0.90)):
            min_acceptable_final = soft_min_count
        else:
            min_acceptable_final = min(valid_unique_candidate_count, hard_min_count)
    else:
        min_acceptable_final = 0

    target_satisfaction_denominator = int(target_final_count or 0)
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
