from __future__ import annotations

from typing import Any

from .streaming_generation_convergence_debug import (
    build_convergence_count_debug_fields,
    build_convergence_debug,
    build_convergence_pruning_debug_fields,
    build_convergence_target_debug_fields,
)
from .streaming_generation_convergence import (
    derive_convergence_reason_state,
    derive_final_coverage_convergence_inputs,
    resolve_completion_reason_lists,
)
from .streaming_generation_target import (
    resolve_generation_target_satisfaction,
    resolve_underfill_diagnostics,
)
from .streaming_postprocess_result_payload import build_stream_postprocess_result_payload


def _int_or_zero(value: Any) -> int:
    return int(value or 0)


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
