from __future__ import annotations

from typing import Any, Callable

from .case_access import case_text_field as _case_text_field
from .streaming_case_keys import (
    case_coverage_bucket as _coverage_bucket,
    case_focus_score as _focus_score,
    case_signature as _signature,
    review_case_id as _review_case_id,
)
from .streaming_review_decision_table import (
    build_review_candidate_row_base_fields as _build_review_candidate_row_base_fields,
    build_review_candidate_row_diagnostic_fields as _build_review_candidate_row_diagnostic_fields,
    build_review_decision_table_context as _build_review_decision_table_context,
    resolve_review_candidate_drop_decision as _resolve_review_candidate_drop_decision,
    resolve_review_priority_fields as _resolve_review_priority_fields,
    resolve_review_row_coverage_retention_fields as _resolve_review_row_coverage_retention_fields,
)
from .streaming_rule_keys import extract_rule_keys as _extract_rule_keys


def build_review_decision_table_rows(
    *,
    review_candidate_cases: list[Any],
    review_selection_input: list[Any],
    review_gate_trace: dict[str, Any],
    parsed_result: list[Any],
    review_case_structure: dict[str, Any],
    review_candidate_coverage_context: dict[str, Any],
    review_candidate_rule_diagnostics: dict[str, Any],
    review_llm_applied: bool,
    review_llm_selected_signatures: set[str],
    review_must_keep_signatures: set[str],
    review_constraint_retained_signatures: set[str],
    review_constraint_reason_map: dict[str, Any],
    review_llm_drop_reason_raw_map: dict[str, Any],
    review_llm_drop_reason_map: dict[str, Any],
    review_llm_drop_reason_source_map: dict[str, Any],
    review_llm_drop_reason_evidence_map: dict[str, Any],
    append_cap_drop_signatures: set[str],
    final_description_dedup_drop_signatures: set[str],
    must_cover_rule_set: set[str],
    review_must_keep_reason_map: dict[str, Any],
    score_case_priority_fn: Callable[..., dict[str, Any]],
    hit_must_cover_rule_fn: Callable[..., bool],
    is_high_signal_fn: Callable[[dict[str, Any]], bool],
    violates_forbidden_pattern_fn: Callable[[dict[str, Any]], bool],
    hits_soft_constraint_fn: Callable[[dict[str, Any]], bool],
    satisfies_quality_hint_fn: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    review_decision_context = _build_review_decision_table_context(
        review_selection_input=review_selection_input,
        review_gate_trace=review_gate_trace,
        parsed_result=parsed_result,
        review_case_structure=review_case_structure,
    )
    selection_signatures = review_decision_context.selection_signatures
    trace_decisions = review_decision_context.trace_decisions
    selected_gate_signatures = review_decision_context.selected_gate_signatures
    dedup_drop_signatures = review_decision_context.dedup_drop_signatures
    final_signatures = review_decision_context.final_signatures
    final_priority_by_signature = review_decision_context.final_priority_by_signature
    structure_rows_by_index = review_decision_context.structure_rows_by_index

    review_decision_table: list[dict[str, Any]] = []
    for index, case in enumerate(review_candidate_cases, start=1):
        if not isinstance(case, dict):
            continue
        signature = _signature(case)
        structure_row = dict(structure_rows_by_index.get(int(index)) or {})
        gate_info = dict(trace_decisions.get(signature) or {})
        rule_keys = list(gate_info.get("rule_keys") or _extract_rule_keys(case))
        bucket = str(gate_info.get("bucket") or _coverage_bucket(case))
        high_signal = bool(gate_info.get("high_signal")) if gate_info else bool(is_high_signal_fn(case))
        adds_rule = bool(gate_info.get("adds_rule")) if gate_info else False
        adds_bucket = bool(gate_info.get("adds_bucket")) if gate_info else False
        gate_reason = str(gate_info.get("drop_reason") or "")
        retained = signature in final_signatures
        drop_decision = _resolve_review_candidate_drop_decision(
            signature=signature,
            review_llm_applied=review_llm_applied,
            review_llm_selected_signatures=review_llm_selected_signatures,
            review_must_keep_signatures=review_must_keep_signatures,
            review_constraint_retained_signatures=review_constraint_retained_signatures,
            review_constraint_reason_map=review_constraint_reason_map,
            review_llm_drop_reason_raw_map=review_llm_drop_reason_raw_map,
            review_llm_drop_reason_map=review_llm_drop_reason_map,
            review_llm_drop_reason_source_map=review_llm_drop_reason_source_map,
            review_llm_drop_reason_evidence_map=review_llm_drop_reason_evidence_map,
            selection_signatures=selection_signatures,
            append_cap_drop_signatures=append_cap_drop_signatures,
            final_description_dedup_drop_signatures=final_description_dedup_drop_signatures,
            dedup_drop_signatures=dedup_drop_signatures,
            selected_gate_signatures=selected_gate_signatures,
            final_signatures=final_signatures,
            gate_reason=gate_reason,
        )
        score_profile = score_case_priority_fn(
            case,
            coverage_context=review_candidate_coverage_context,
            rule_diagnostics=review_candidate_rule_diagnostics,
        )
        priority_fields = _resolve_review_priority_fields(
            case=case,
            signature=signature,
            retained=retained,
            final_priority_by_signature=final_priority_by_signature,
        )
        hit_must_cover_rule = bool(
            hit_must_cover_rule_fn(
                rule_keys,
                score_profile,
                must_cover_rule_set=must_cover_rule_set,
            )
        )
        retention_fields = _resolve_review_row_coverage_retention_fields(
            gate_info=gate_info,
            score_profile=score_profile,
            retained=retained,
            adds_rule=adds_rule,
            adds_bucket=adds_bucket,
        )
        row = _build_review_candidate_row_base_fields(
            index=index,
            case=case,
            signature=signature,
            structure_row=structure_row,
            gate_info=gate_info,
            rule_keys=rule_keys,
            bucket=bucket,
            adds_rule=adds_rule,
            adds_bucket=adds_bucket,
            high_signal=high_signal,
            has_coverage_value=retention_fields.has_coverage_value_for_row,
            retained_reason=retention_fields.retained_reason_value,
            review_case_id_fn=_review_case_id,
            case_text_field_fn=_case_text_field,
            focus_score_fn=_focus_score,
        )
        row.update(
            _build_review_candidate_row_diagnostic_fields(
                model_priority_value=priority_fields.model_priority_value,
                legacy_priority_value=priority_fields.legacy_priority_value,
                priority_final_value=priority_fields.priority_final_value,
                priority_decision_state_value=priority_fields.priority_decision_state_value,
                priority_decision_source_value=priority_fields.priority_decision_source_value,
                priority_confidence_value=priority_fields.priority_confidence_value,
                priority_conflict_reason_value=priority_fields.priority_conflict_reason_value,
                priority_resolution_reason_value=priority_fields.priority_resolution_reason_value,
                score_profile=score_profile,
                selected_by_review_llm=drop_decision.selected_by_review_llm,
                selected_by_review_must_keep=drop_decision.selected_by_review_must_keep,
                selected_by_review_constraints=drop_decision.selected_by_constraint_guard,
                review_constraint_reason=drop_decision.review_constraint_reason,
                review_llm_drop_reason_raw=drop_decision.review_llm_drop_reason_raw,
                review_llm_drop_reason=drop_decision.review_llm_drop_reason,
                review_llm_drop_reason_source=drop_decision.review_llm_drop_reason_source,
                review_llm_drop_reason_evidence=drop_decision.review_llm_drop_reason_evidence,
                has_positive_evidence=drop_decision.has_positive_evidence,
                has_coverage_signal=drop_decision.has_coverage_signal,
                has_high_signal=drop_decision.has_high_signal,
                has_competition_signal=drop_decision.has_competition_signal,
                review_llm_applied=review_llm_applied,
                signature=signature,
                review_must_keep_signatures=review_must_keep_signatures,
                review_must_keep_reason_map=review_must_keep_reason_map,
                selected_gate_signatures=selected_gate_signatures,
                retained=retained,
                dropped_stage=drop_decision.dropped_stage,
                dropped_reason=drop_decision.dropped_reason,
                hit_must_cover_rule=hit_must_cover_rule,
                violates_forbidden_pattern=bool(violates_forbidden_pattern_fn(case)),
                hits_soft_constraint=bool(hits_soft_constraint_fn(case)),
                satisfies_quality_hint=bool(satisfies_quality_hint_fn(case)),
            )
        )
        review_decision_table.append(row)
    return review_decision_table


__all__ = ["build_review_decision_table_rows"]
