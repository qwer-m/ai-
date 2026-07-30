from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .streaming_final_case_summary import (
    final_dedup_priority_summary_fields as _final_dedup_priority_summary_fields,
    review_flow_structure_summary_fields as _review_flow_structure_summary_fields,
    summarize_final_description_dedup_and_priority_breakdown as _summarize_final_description_dedup_and_priority_breakdown,
)
from .streaming_postprocess_utils import _dict_case_items, _rule_diagnostics_payload
from .streaming_review_decision_table import (
    resolve_review_priority_summary_flags as _resolve_review_priority_summary_flags,
)
from .streaming_review_decision_table_rows import (
    build_review_decision_table_rows as _build_review_decision_table_rows,
)
from .streaming_review_selection_summary import (
    build_review_decision_summary_payload as _build_review_decision_summary_payload,
    summarize_review_decision_counts as _summarize_review_decision_counts,
    summarize_review_llm_drop_diagnostics as _summarize_review_llm_drop_diagnostics,
    review_llm_drop_summary_fields as _review_llm_drop_summary_fields,
)
from .streaming_structure_diagnostics import (
    resolve_review_case_structure as _resolve_review_case_structure,
)


@dataclass(frozen=True)
class ReviewSummaryAssemblyResult:
    review_decision_table: list[dict[str, Any]]
    final_description_dedup_drop_signatures: set[str]
    priority_conflict_count: int
    priority_undetermined_count: int
    priority_optional_count: int
    needs_priority_review: bool
    drop_by_review_llm_count: int
    drop_by_review_selector_count: int
    review_decision_summary: dict[str, Any]


def assemble_review_summary_state(
    *,
    requirement: str,
    review_candidate_cases: list[dict[str, Any]],
    review_selection_input: list[dict[str, Any]],
    review_gate_trace: dict[str, Any],
    parsed_result: list[dict[str, Any]],
    project_profile: dict[str, Any],
    flow_project_profile: dict[str, Any],
    flow_governance_summary: dict[str, Any],
    final_case_structure: dict[str, Any],
    final_independent_case_structure: dict[str, Any],
    final_order_flow_governance_summary: dict[str, Any],
    fact_profile: dict[str, Any],
    execution_plan_summary: dict[str, Any],
    final_semantic_diagnostics: dict[str, Any],
    ui_like_ratio_postprocess_drop_count: int,
    final_description_dedup_drop_signatures: set[str],
    review_llm_applied: bool,
    review_llm_selected_signatures: set[str],
    review_llm_omitted_signatures: set[str],
    review_llm_runtime_debug: dict[str, Any],
    review_llm_drop_reason_raw_map: dict[str, Any],
    review_llm_drop_reason_map: dict[str, Any],
    review_llm_drop_reason_source_map: dict[str, Any],
    review_llm_drop_reason_evidence_map: dict[str, Any],
    review_constraint_retained_signatures: set[str],
    review_constraint_reason_map: dict[str, Any],
    append_cap_drop_signatures: set[str],
    must_cover_rule_set: set[str],
    review_selected_count: int,
    review_target_min_count: int,
    review_target_max_count: int,
    review_shortfall_detected: bool,
    review_shortfall_before_count: int,
    generation_mode: str,
    effective_generation_coverage_mode_source: str,
    explicit_generation_mode_override: bool,
    explicit_expected_count_floor_preserved: bool,
    review_llm_pool_count: int,
    stage_counts: dict[str, Any],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    analyze_case_structure_fn: Callable[..., dict[str, Any]],
    summarize_duplicate_excess_by_policy_fn: Callable[..., dict[str, Any]],
    score_case_priority_fn: Callable[..., dict[str, Any]],
    hit_must_cover_rule_fn: Callable[..., bool],
    is_high_signal_fn: Callable[..., bool],
    violates_forbidden_pattern_fn: Callable[[dict[str, Any]], bool],
    hits_soft_constraint_fn: Callable[[dict[str, Any]], bool],
    satisfies_quality_hint_fn: Callable[[dict[str, Any]], bool],
    reasoning_leakage_hits_fn: Callable[[Any], list[str]],
    dict_case_count_fn: Callable[[Any], int],
) -> ReviewSummaryAssemblyResult:
    review_candidate_coverage_context = analyze_coverage_fn(
        requirement,
        _dict_case_items(review_candidate_cases),
    )
    review_candidate_rule_diagnostics = _rule_diagnostics_payload(review_candidate_coverage_context)
    review_case_structure = _resolve_review_case_structure(
        requirement=requirement,
        review_candidate_cases=review_candidate_cases,
        project_profile=project_profile,
        analyze_case_structure_fn=analyze_case_structure_fn,
        dict_case_items_fn=_dict_case_items,
    )
    review_decision_table = _build_review_decision_table_rows(
        review_candidate_cases=review_candidate_cases,
        review_selection_input=review_selection_input,
        review_gate_trace=review_gate_trace,
        parsed_result=parsed_result,
        review_case_structure=review_case_structure,
        review_candidate_coverage_context=review_candidate_coverage_context,
        review_candidate_rule_diagnostics=review_candidate_rule_diagnostics,
        review_llm_applied=review_llm_applied,
        review_llm_selected_signatures=review_llm_selected_signatures,
        review_constraint_retained_signatures=review_constraint_retained_signatures,
        review_constraint_reason_map=review_constraint_reason_map,
        review_llm_drop_reason_raw_map=review_llm_drop_reason_raw_map,
        review_llm_drop_reason_map=review_llm_drop_reason_map,
        review_llm_drop_reason_source_map=review_llm_drop_reason_source_map,
        review_llm_drop_reason_evidence_map=review_llm_drop_reason_evidence_map,
        append_cap_drop_signatures=append_cap_drop_signatures,
        final_description_dedup_drop_signatures=final_description_dedup_drop_signatures,
        must_cover_rule_set=must_cover_rule_set,
        score_case_priority_fn=score_case_priority_fn,
        hit_must_cover_rule_fn=hit_must_cover_rule_fn,
        is_high_signal_fn=is_high_signal_fn,
        violates_forbidden_pattern_fn=violates_forbidden_pattern_fn,
        hits_soft_constraint_fn=hits_soft_constraint_fn,
        satisfies_quality_hint_fn=satisfies_quality_hint_fn,
    )

    dropped_rows = [row for row in review_decision_table if not bool(row.get("retained_final"))]
    final_dedup_priority_summary = _summarize_final_description_dedup_and_priority_breakdown(
        review_decision_table,
    )
    resolved_final_description_dedup_drop_signatures = set(
        final_dedup_priority_summary.get("final_description_dedup_drop_signatures") or set()
    )
    priority_summary_fields = _final_dedup_priority_summary_fields(final_dedup_priority_summary)
    priority_summary_flags = _resolve_review_priority_summary_flags(priority_summary_fields)
    review_llm_drop_diagnostics = _summarize_review_llm_drop_diagnostics(
        review_llm_applied=bool(review_llm_applied),
        review_llm_omitted_signatures=review_llm_omitted_signatures,
        dropped_rows=dropped_rows,
        review_llm_drop_reason_map=review_llm_drop_reason_map,
        review_llm_drop_reason_raw_map=review_llm_drop_reason_raw_map,
        review_llm_drop_reason_source_map=review_llm_drop_reason_source_map,
        review_llm_drop_reason_evidence_map=review_llm_drop_reason_evidence_map,
        review_llm_runtime_debug=review_llm_runtime_debug,
    )
    review_llm_runtime_debug.update(dict(review_llm_drop_diagnostics.get("runtime_debug_updates") or {}))
    review_llm_summary_fields = _review_llm_drop_summary_fields(
        review_llm_drop_diagnostics,
        review_llm_runtime_debug,
    )
    drop_by_review_llm_count = int(review_llm_drop_diagnostics.get("drop_by_review_llm_count") or 0)
    drop_by_review_selector_count = int(review_llm_drop_diagnostics.get("drop_by_review_selector_count") or 0)
    final_duplicate_excess = summarize_duplicate_excess_by_policy_fn(
        final_independent_case_structure,
        project_profile=flow_project_profile,
        default_max=2,
    )
    review_flow_summary_fields = _review_flow_structure_summary_fields(
        review_case_structure=review_case_structure,
        final_independent_case_structure=final_independent_case_structure,
        final_duplicate_excess=final_duplicate_excess,
        final_case_structure=final_case_structure,
        final_order_flow_governance_summary=final_order_flow_governance_summary,
        fact_profile=fact_profile,
        project_profile=project_profile,
        flow_governance_summary=flow_governance_summary,
        execution_plan_summary=execution_plan_summary,
    )
    review_flow_summary_fields.update(dict(final_semantic_diagnostics or {}))
    review_decision_counts = _summarize_review_decision_counts(
        review_decision_table,
        dropped_rows,
        ui_like_ratio_postprocess_drop_count=ui_like_ratio_postprocess_drop_count,
        final_description_dedup_drop_signatures=resolved_final_description_dedup_drop_signatures,
        drop_by_review_llm_count=drop_by_review_llm_count,
        drop_by_review_selector_count=drop_by_review_selector_count,
    )
    review_decision_summary = _build_review_decision_summary_payload(
        review_decision_table=review_decision_table,
        dropped_rows=dropped_rows,
        review_flow_summary_fields=review_flow_summary_fields,
        parsed_result=parsed_result,
        reasoning_leakage_hits_fn=reasoning_leakage_hits_fn,
        priority_summary_fields=priority_summary_fields,
        needs_priority_review=priority_summary_flags.needs_priority_review,
        review_llm_applied=review_llm_applied,
        review_selection_input=review_selection_input,
        dict_case_count_fn=dict_case_count_fn,
        review_selected_count=review_selected_count,
        review_target_min_count=review_target_min_count,
        review_target_max_count=review_target_max_count,
        review_shortfall_detected=review_shortfall_detected,
        review_shortfall_before_count=review_shortfall_before_count,
        generation_mode=generation_mode,
        effective_generation_coverage_mode_source=effective_generation_coverage_mode_source,
        explicit_generation_mode_override=explicit_generation_mode_override,
        explicit_expected_count_floor_preserved=explicit_expected_count_floor_preserved,
        review_llm_selected_signatures=review_llm_selected_signatures,
        review_llm_runtime_debug=review_llm_runtime_debug,
        review_constraint_retained_signatures=review_constraint_retained_signatures,
        review_llm_summary_fields=review_llm_summary_fields,
        review_llm_pool_count=review_llm_pool_count,
        stage_counts=stage_counts,
        review_decision_counts=review_decision_counts,
    )
    return ReviewSummaryAssemblyResult(
        review_decision_table=review_decision_table,
        final_description_dedup_drop_signatures=resolved_final_description_dedup_drop_signatures,
        priority_conflict_count=priority_summary_flags.priority_conflict_count,
        priority_undetermined_count=priority_summary_flags.priority_undetermined_count,
        priority_optional_count=priority_summary_flags.priority_optional_count,
        needs_priority_review=priority_summary_flags.needs_priority_review,
        drop_by_review_llm_count=drop_by_review_llm_count,
        drop_by_review_selector_count=drop_by_review_selector_count,
        review_decision_summary=review_decision_summary,
    )
