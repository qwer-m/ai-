from __future__ import annotations

from typing import Any, Callable, Iterable

from .streaming_postprocess_utils import _dict_case_items


def summarize_review_decision_counts(
    review_decision_table: Iterable[Any] | None,
    dropped_rows: Iterable[Any] | None,
    *,
    ui_like_ratio_postprocess_drop_count: int = 0,
    final_description_dedup_drop_signatures: Iterable[Any] | None = None,
    drop_by_review_llm_count: int = 0,
    drop_by_review_selector_count: int = 0,
) -> dict[str, int]:
    review_rows = [row for row in (review_decision_table or []) if isinstance(row, dict)]
    dropped_items = [row for row in (dropped_rows or []) if isinstance(row, dict)]
    return {
        **summarize_review_must_keep_and_signal_counts(review_rows),
        **summarize_review_drop_stage_counts(
            dropped_items,
            drop_by_review_llm_count=drop_by_review_llm_count,
            drop_by_review_selector_count=drop_by_review_selector_count,
        ),
        **summarize_review_drop_reason_counts(
            dropped_items,
            ui_like_ratio_postprocess_drop_count=ui_like_ratio_postprocess_drop_count,
            final_description_dedup_drop_signatures=final_description_dedup_drop_signatures,
        ),
    }


def summarize_review_must_keep_and_signal_counts(
    review_decision_table: Iterable[Any] | None,
) -> dict[str, int]:
    review_rows = [row for row in (review_decision_table or []) if isinstance(row, dict)]
    return {
        "must_keep_candidate_count": int(sum(1 for row in review_rows if bool(row.get("must_keep_candidate")))),
        "must_keep_retained_count": int(
            sum(
                1
                for row in review_rows
                if bool(row.get("must_keep_candidate")) and bool(row.get("retained_final"))
            )
        ),
        "must_keep_dropped_count": int(
            sum(
                1
                for row in review_rows
                if bool(row.get("must_keep_candidate")) and not bool(row.get("retained_final"))
            )
        ),
        "retained_due_to_coverage_value_count": int(
            sum(
                1
                for row in review_rows
                if str(row.get("retained_reason") or "") == "retained_due_to_coverage_value"
            )
        ),
        "must_cover_rule_hit_count": int(sum(1 for row in review_rows if bool(row.get("hit_must_cover_rule")))),
        "forbidden_pattern_violation_count": int(
            sum(1 for row in review_rows if bool(row.get("violates_forbidden_pattern")))
        ),
        "soft_constraint_hit_count": int(sum(1 for row in review_rows if bool(row.get("hits_soft_constraint")))),
        "quality_hint_satisfied_count": int(
            sum(1 for row in review_rows if bool(row.get("satisfies_quality_hint")))
        ),
    }


def summarize_review_drop_stage_counts(
    dropped_rows: Iterable[Any] | None,
    *,
    drop_by_review_llm_count: int = 0,
    drop_by_review_selector_count: int = 0,
) -> dict[str, int]:
    dropped_items = [row for row in (dropped_rows or []) if isinstance(row, dict)]
    return {
        "drop_by_review_llm_count": int(drop_by_review_llm_count),
        "drop_by_review_selector_count": int(drop_by_review_selector_count),
        "drop_by_review_gate_count": int(
            sum(1 for row in dropped_items if row.get("dropped_stage") == "review_gate")
        ),
        "drop_by_pre_gate_dedup_count": int(
            sum(1 for row in dropped_items if row.get("dropped_stage") == "review_dedup_pre_gate")
        ),
        "drop_by_post_review_dedup_count": int(
            sum(1 for row in dropped_items if row.get("dropped_stage") == "post_review_dedup_or_reorder")
        ),
    }


def summarize_review_drop_reason_counts(
    dropped_rows: Iterable[Any] | None,
    *,
    ui_like_ratio_postprocess_drop_count: int = 0,
    final_description_dedup_drop_signatures: Iterable[Any] | None = None,
) -> dict[str, int]:
    dropped_items = [row for row in (dropped_rows or []) if isinstance(row, dict)]
    final_description_duplicate_count = (
        sum(1 for _item in final_description_dedup_drop_signatures)
        if final_description_dedup_drop_signatures is not None
        else 0
    )

    return {
        "drop_no_new_signal_count": int(
            sum(
                1
                for row in dropped_items
                if row.get("dropped_reason") == "drop_no_new_rule_no_new_bucket_no_high_signal"
            )
        ),
        "drop_rule_cap_count": int(
            sum(1 for row in dropped_items if row.get("dropped_reason") == "drop_rule_cap")
        ),
        "drop_ui_like_redundant_count": int(
            sum(1 for row in dropped_items if row.get("dropped_reason") == "drop_ui_like_redundant_case")
        ),
        "drop_ui_like_ratio_cap_count": int(
            sum(1 for row in dropped_items if row.get("dropped_reason") == "drop_ui_like_ratio_cap")
        ),
        "drop_outside_target_window_count": int(
            sum(1 for row in dropped_items if row.get("dropped_reason") == "drop_outside_target_window")
        ),
        "drop_by_rerank_low_signal_count": int(
            sum(
                1
                for row in dropped_items
                if row.get("dropped_reason") == "drop_no_new_rule_no_new_bucket_no_high_signal"
            )
        ),
        "dropped_model_priority_p0_p1_count": int(
            sum(1 for row in dropped_items if str(row.get("model_priority_current") or "").upper() in {"P0", "P1"})
        ),
        "dropped_core_rule_hit_count": int(sum(1 for row in dropped_items if bool(row.get("core_rule_hits")))),
        "dropped_missing_rule_hit_count": int(sum(1 for row in dropped_items if bool(row.get("missing_rule_hits")))),
        "dropped_high_signal_count": int(sum(1 for row in dropped_items if bool(row.get("high_signal")))),
        "dropped_has_coverage_value_count": int(
            sum(1 for row in dropped_items if bool(row.get("has_coverage_value")))
        ),
        "drop_ui_like_ratio_postprocess_count": int(ui_like_ratio_postprocess_drop_count),
        "drop_final_description_duplicate_count": int(final_description_duplicate_count),
    }


def summarize_review_llm_drop_diagnostics(
    *,
    review_llm_applied: bool,
    review_llm_omitted_signatures: Iterable[Any] | None,
    dropped_rows: list[dict[str, Any]] | None,
    review_llm_drop_reason_map: dict[str, str] | None,
    review_llm_drop_reason_raw_map: dict[str, str] | None,
    review_llm_drop_reason_source_map: dict[str, str] | None,
    review_llm_drop_reason_evidence_map: dict[str, Any] | None,
    review_llm_runtime_debug: dict[str, Any] | None,
) -> dict[str, Any]:
    runtime_debug = dict(review_llm_runtime_debug or {})
    dropped_items = _dict_case_items(dropped_rows or [])
    reason_map = dict(review_llm_drop_reason_map or {})
    raw_reason_map = dict(review_llm_drop_reason_raw_map or {})
    source_map = dict(review_llm_drop_reason_source_map or {})
    evidence_map = dict(review_llm_drop_reason_evidence_map or {})

    review_llm_omitted_for_summary = {
        str(signature or "").strip()
        for signature in (review_llm_omitted_signatures or [])
        if str(signature or "").strip()
    }
    if review_llm_applied and not review_llm_omitted_for_summary:
        review_llm_omitted_for_summary = {
            str(row.get("signature") or "").strip()
            for row in dropped_items
            if str(row.get("dropped_stage") or "") == "review_llm"
            and str(row.get("signature") or "").strip()
        }

    review_llm_drop_reason_counts: dict[str, int] = {}
    review_llm_drop_reason_raw_counts: dict[str, int] = {}
    review_llm_drop_reason_source_counts: dict[str, int] = {}
    fallback_with_positive_evidence_count = 0
    fallback_without_positive_evidence_count = 0
    for signature in review_llm_omitted_for_summary:
        reason_key = str(reason_map.get(signature) or "").strip() or "unspecified"
        review_llm_drop_reason_counts[reason_key] = int(review_llm_drop_reason_counts.get(reason_key, 0)) + 1
        raw_reason_key = str(raw_reason_map.get(signature) or "").strip() or "unspecified"
        review_llm_drop_reason_raw_counts[raw_reason_key] = int(
            review_llm_drop_reason_raw_counts.get(raw_reason_key, 0)
        ) + 1
        source_key = str(source_map.get(signature) or "").strip() or "unresolved"
        review_llm_drop_reason_source_counts[source_key] = int(
            review_llm_drop_reason_source_counts.get(source_key, 0)
        ) + 1
        if reason_key == "fallback_unspecified":
            evidence = evidence_map.get(signature)
            if not isinstance(evidence, dict):
                evidence = {}
            if bool(evidence.get("has_positive_evidence")):
                fallback_with_positive_evidence_count += 1
            else:
                fallback_without_positive_evidence_count += 1

    drop_by_review_llm_count = int(len(review_llm_omitted_for_summary))
    drop_by_review_selector_count = int(
        sum(1 for row in dropped_items if row.get("dropped_stage") == "review_selector")
    )
    if (
        str(runtime_debug.get("final_source") or "") == "review_selector"
        and not review_llm_applied
        and dropped_items
    ):
        drop_by_review_selector_count = max(drop_by_review_selector_count, int(len(dropped_items)))

    fallback_dropped_reason_count = int(
        (
            runtime_debug.get("final_dropped_reason_payload_count")
            if str(runtime_debug.get("final_source") or "") == "fallback_llm"
            else 0
        )
        or 0
    )
    fallback_dropped_reason_mapped_count = int(review_llm_drop_reason_source_counts.get("fallback_llm", 0))
    if str(runtime_debug.get("final_source") or "") == "fallback_llm":
        fallback_dropped_reason_mapped_count = int(runtime_debug.get("final_dropped_reason_count") or 0)
    fallback_dropped_reason_unmapped_count = int(
        (
            runtime_debug.get("final_dropped_reason_unmapped_count")
            if str(runtime_debug.get("final_source") or "") == "fallback_llm"
            else 0
        )
        or 0
    )
    llm_reason_total = int(review_llm_drop_reason_source_counts.get("llm", 0)) + int(
        review_llm_drop_reason_source_counts.get("fallback_llm", 0)
    )
    deterministic_backfill_total = int(review_llm_drop_reason_source_counts.get("deterministic_backfill", 0))
    fallback_reason_incomplete = bool(
        str(runtime_debug.get("final_source") or "") == "fallback_llm"
        and fallback_dropped_reason_count <= 0
    )
    fallback_reason_coverage_ratio = round(
        float(fallback_dropped_reason_mapped_count) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 0.0
    llm_reason_coverage_ratio = round(
        float(llm_reason_total) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 0.0
    deterministic_backfill_ratio = round(
        float(deterministic_backfill_total) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 0.0
    final_reason_incomplete = bool(drop_by_review_llm_count > 0 and llm_reason_total <= 0)
    final_reason_coverage_ratio = round(
        float(llm_reason_total) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 1.0

    runtime_debug_updates: dict[str, Any] = {
        "fallback_reason_incomplete": bool(fallback_reason_incomplete),
        "final_reason_incomplete": bool(final_reason_incomplete),
        "final_reason_coverage_ratio": float(final_reason_coverage_ratio),
    }
    if final_reason_incomplete and str(runtime_debug.get("applied_reason") or "") == "mapped_valid_payload":
        runtime_debug_updates["applied_reason"] = "mapped_valid_payload_reason_incomplete"

    reason_source_breakdown = {
        "primary": int(review_llm_drop_reason_source_counts.get("llm", 0)),
        "fallback": int(review_llm_drop_reason_source_counts.get("fallback_llm", 0)),
        "backfill": int(review_llm_drop_reason_source_counts.get("deterministic_backfill", 0)),
    }
    return {
        "review_llm_drop_reason_breakdown": dict(review_llm_drop_reason_counts),
        "review_llm_drop_reason_raw_breakdown": dict(review_llm_drop_reason_raw_counts),
        "review_llm_drop_reason_source_breakdown": dict(review_llm_drop_reason_source_counts),
        "fallback_reason_incomplete": bool(fallback_reason_incomplete),
        "final_reason_incomplete": bool(final_reason_incomplete),
        "final_reason_coverage_ratio": float(final_reason_coverage_ratio),
        "fallback_dropped_reason_count": int(fallback_dropped_reason_count),
        "fallback_dropped_reason_mapped_count": int(fallback_dropped_reason_mapped_count),
        "fallback_dropped_reason_unmapped_count": int(fallback_dropped_reason_unmapped_count),
        "fallback_reason_coverage_ratio": float(fallback_reason_coverage_ratio),
        "llm_reason_coverage_ratio": float(llm_reason_coverage_ratio),
        "deterministic_backfill_ratio": float(deterministic_backfill_ratio),
        "reason_source_breakdown": dict(reason_source_breakdown),
        "fallback_with_positive_evidence_count": int(fallback_with_positive_evidence_count),
        "fallback_without_positive_evidence_count": int(fallback_without_positive_evidence_count),
        "drop_by_review_llm_count": int(drop_by_review_llm_count),
        "drop_by_review_selector_count": int(drop_by_review_selector_count),
        "runtime_debug_updates": dict(runtime_debug_updates),
    }


def review_llm_drop_summary_fields(
    review_llm_drop_diagnostics: dict[str, Any] | None,
    review_llm_runtime_debug: dict[str, Any] | None,
) -> dict[str, Any]:
    diagnostics = dict(review_llm_drop_diagnostics or {})
    runtime_debug = dict(review_llm_runtime_debug or {})
    return {
        "review_llm_drop_reason_breakdown": dict(
            diagnostics.get("review_llm_drop_reason_breakdown") or {}
        ),
        "review_llm_drop_reason_raw_breakdown": dict(
            diagnostics.get("review_llm_drop_reason_raw_breakdown") or {}
        ),
        "review_llm_drop_reason_source_breakdown": dict(
            diagnostics.get("review_llm_drop_reason_source_breakdown") or {}
        ),
        "fallback_reason_incomplete": bool(diagnostics.get("fallback_reason_incomplete")),
        "final_reason_incomplete": bool(diagnostics.get("final_reason_incomplete")),
        "final_reason_coverage_ratio": float(diagnostics.get("final_reason_coverage_ratio") or 0.0),
        "fallback_dropped_reason_count": int(diagnostics.get("fallback_dropped_reason_count") or 0),
        "fallback_dropped_reason_mapped_count": int(
            diagnostics.get("fallback_dropped_reason_mapped_count") or 0
        ),
        "fallback_dropped_reason_unmapped_count": int(
            diagnostics.get("fallback_dropped_reason_unmapped_count") or 0
        ),
        "fallback_reason_coverage_ratio": float(diagnostics.get("fallback_reason_coverage_ratio") or 0.0),
        "llm_reason_coverage_ratio": float(diagnostics.get("llm_reason_coverage_ratio") or 0.0),
        "deterministic_backfill_ratio": float(diagnostics.get("deterministic_backfill_ratio") or 0.0),
        "reason_source_breakdown": dict(diagnostics.get("reason_source_breakdown") or {}),
        "primary_reason_incomplete": bool(runtime_debug.get("primary_reason_incomplete")),
        "primary_dropped_reason_count": int(runtime_debug.get("primary_dropped_reason_count") or 0),
        "primary_dropped_reason_payload_count": int(
            runtime_debug.get("primary_dropped_reason_payload_count") or 0
        ),
        "primary_reason_coverage_ratio": float(runtime_debug.get("primary_reason_coverage_ratio") or 0.0),
        "fallback_with_positive_evidence_count": int(
            diagnostics.get("fallback_with_positive_evidence_count") or 0
        ),
        "fallback_without_positive_evidence_count": int(
            diagnostics.get("fallback_without_positive_evidence_count") or 0
        ),
    }


def build_review_decision_summary_payload(
    *,
    review_decision_table: Iterable[Any] | None,
    dropped_rows: Iterable[Any] | None,
    review_flow_summary_fields: dict[str, Any] | None,
    parsed_result: Iterable[Any] | None,
    reasoning_leakage_hits_fn: Callable[[dict[str, Any]], bool],
    priority_summary_fields: dict[str, Any] | None,
    needs_priority_review: bool,
    review_llm_applied: bool,
    review_selection_input: Any,
    dict_case_count_fn: Callable[[Any], int],
    review_selected_count: int | None,
    review_target_min_count: int | None,
    review_target_max_count: int | None,
    review_shortfall_detected: bool,
    review_shortfall_before_count: int,
    review_shortfall_recovered_count: int,
    review_post_rerank_floor_count: int | None,
    review_post_rerank_recovered_count: int | None,
    final_target_floor_count: int | None,
    final_floor_recovery_attempted: bool,
    final_floor_recovery_applied: bool,
    final_floor_recovered_count: int | None,
    final_floor_recovery_reason: str | None,
    final_confirmed_conflict_drop_count: int | None,
    final_shortfall_supplement_attempted: bool,
    final_shortfall_supplement_applied: bool,
    final_shortfall_supplement_count: int | None,
    final_shortfall_supplement_reason: str | None,
    final_shortfall_supplement_debug: dict[str, Any] | None,
    generation_mode: str | None,
    effective_generation_coverage_mode_source: str | None,
    explicit_generation_mode_override: bool,
    explicit_expected_count_floor_preserved: bool,
    review_fill_source: str | None,
    review_llm_selected_signatures: Iterable[Any] | None,
    review_llm_runtime_debug: dict[str, Any] | None,
    review_constraint_retained_signatures: Iterable[Any] | None,
    review_llm_summary_fields: dict[str, Any] | None,
    review_llm_pool_count: int,
    stage_counts: dict[str, Any] | None,
    review_decision_counts: dict[str, Any] | None,
) -> dict[str, Any]:
    review_rows = [row for row in (review_decision_table or []) if isinstance(row, dict)]
    dropped_items = [row for row in (dropped_rows or []) if isinstance(row, dict)]
    parsed_items = parsed_result or []
    stages = dict(stage_counts or {})

    return {
        "candidate_total": int(len(review_rows)),
        "retained_total": int(sum(1 for row in review_rows if bool(row.get("retained_final")))),
        "dropped_total": int(len(dropped_items)),
        **dict(review_flow_summary_fields or {}),
        "final_reasoning_leakage_case_count": int(
            sum(
                1
                for item in parsed_items
                if isinstance(item, dict) and reasoning_leakage_hits_fn(item)
            )
        ),
        **dict(priority_summary_fields or {}),
        "invalid_case_count": int(
            sum(1 for row in review_rows if str(row.get("case_quality") or "") == "invalid_case")
        ),
        "reasoning_leakage_case_count": int(
            sum(1 for row in review_rows if str(row.get("invalid_case_reason") or "") == "reasoning_leakage")
        ),
        "needs_priority_review": bool(needs_priority_review),
        "review_llm_filter_applied": bool(review_llm_applied),
        "review_input_size": int(dict_case_count_fn(review_selection_input)),
        "review_output_size": int(review_selected_count or 0),
        "review_target_min_count": int(review_target_min_count or 1),
        "review_target_max_count": int(review_target_max_count or review_target_min_count or 1),
        "review_shortfall_detected": bool(review_shortfall_detected),
        "review_shortfall_before_count": int(review_shortfall_before_count),
        "review_shortfall_recovered_count": int(review_shortfall_recovered_count),
        "review_post_rerank_floor_count": int(review_post_rerank_floor_count or 1),
        "review_post_rerank_recovered_count": int(review_post_rerank_recovered_count or 0),
        "final_target_floor_count": int(final_target_floor_count or 0),
        "final_floor_recovery_attempted": bool(final_floor_recovery_attempted),
        "final_floor_recovery_applied": bool(final_floor_recovery_applied),
        "final_floor_recovered_count": int(final_floor_recovered_count or 0),
        "final_floor_recovery_reason": str(final_floor_recovery_reason or ""),
        "final_confirmed_conflict_drop_count": int(final_confirmed_conflict_drop_count or 0),
        "final_shortfall_supplement_attempted": bool(final_shortfall_supplement_attempted),
        "final_shortfall_supplement_applied": bool(final_shortfall_supplement_applied),
        "final_shortfall_supplement_count": int(final_shortfall_supplement_count or 0),
        "final_shortfall_supplement_reason": str(final_shortfall_supplement_reason or ""),
        "final_shortfall_supplement_debug": dict(final_shortfall_supplement_debug or {}),
        "requested_generation_mode": str(generation_mode or ""),
        "effective_generation_coverage_mode_source": str(effective_generation_coverage_mode_source or ""),
        "explicit_generation_mode_override": bool(explicit_generation_mode_override),
        "explicit_expected_count_floor_preserved": bool(explicit_expected_count_floor_preserved),
        "review_fill_source": str(review_fill_source or "none"),
        "review_llm_selected_count": int(len(list(review_llm_selected_signatures or []))),
        "review_llm_runtime_debug": dict(review_llm_runtime_debug or {}),
        "review_constraint_selected_count": int(len(list(review_constraint_retained_signatures or []))),
        **dict(review_llm_summary_fields or {}),
        "review_llm_pool_count": int(review_llm_pool_count),
        "candidate_by_pass": {
            "primary": int(stages.get("primary") or 0),
            "gap": int(stages.get("gap") or 0),
        },
        **dict(review_decision_counts or {}),
    }
