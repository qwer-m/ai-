from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .case_access import case_priority
from .streaming_case_keys import candidate_identity_key, case_signature


@dataclass(frozen=True)
class ReviewDecisionTableContext:
    selection_candidate_keys: set[str]
    trace_decisions: dict[str, Any]
    selected_gate_candidate_keys: set[str]
    dedup_drop_candidate_keys: set[str]
    final_candidate_keys: set[str]
    final_priority_by_candidate_key: dict[str, str]
    structure_rows_by_index: dict[int, dict[str, Any]]


@dataclass(frozen=True)
class ReviewPriorityFields:
    model_priority_value: str
    legacy_priority_value: str
    priority_final_value: str
    priority_decision_state_value: str
    priority_decision_source_value: str
    priority_confidence_value: str
    priority_conflict_reason_value: str
    priority_resolution_reason_value: str
    unresolved_priority_decision: bool


@dataclass(frozen=True)
class ReviewPrioritySummaryFlags:
    priority_conflict_count: int
    priority_undetermined_count: int
    priority_optional_count: int
    needs_priority_review: bool


@dataclass(frozen=True)
class ReviewCandidateDropDecision:
    selected_by_review_llm: bool
    selected_by_constraint_guard: bool
    review_llm_drop_reason_raw: str
    review_llm_drop_reason: str
    review_llm_drop_reason_source: str
    review_llm_drop_reason_evidence: dict[str, Any]
    has_positive_evidence: bool
    has_coverage_signal: bool
    has_high_signal: bool
    has_competition_signal: bool
    review_constraint_reason: str
    dropped_stage: str
    dropped_reason: str


@dataclass(frozen=True)
class ReviewRowCoverageRetentionFields:
    has_coverage_value_for_row: bool
    retained_reason_value: str


_VALID_PRIORITY_VALUES = {"P0", "P1", "P2"}
_ALLOWED_PRIORITY_DECISION_STATES = {"decided", "conflict", "undetermined", "optional", "invalid"}


def build_review_decision_table_context(
    *,
    review_selection_input: list[Any],
    review_gate_trace: dict[str, Any],
    parsed_result: list[Any],
    review_case_structure: dict[str, Any],
) -> ReviewDecisionTableContext:
    selection_candidate_keys = {
        candidate_identity_key(item)
        for item in review_selection_input
        if isinstance(item, dict)
    }
    trace_decisions = dict((review_gate_trace.get("decisions") or {}))
    selected_gate_candidate_keys = set(
        str(item)
        for item in (
            review_gate_trace.get("selected_candidate_keys")
            or review_gate_trace.get("selected_signatures")
            or []
        )
    )
    dedup_drop_candidate_keys = set(
        str(item)
        for item in (
            review_gate_trace.get("dedup_dropped_candidate_keys")
            or review_gate_trace.get("dedup_dropped_signatures")
            or []
        )
    )
    final_candidate_keys = {
        candidate_identity_key(item) for item in parsed_result if isinstance(item, dict)
    }
    final_priority_by_candidate_key = {
        candidate_identity_key(item): case_priority(item)
        for item in parsed_result
        if isinstance(item, dict)
    }
    structure_rows_by_index = {
        int(item.get("candidate_index") or 0): dict(item)
        for item in (review_case_structure.get("rows") or [])
        if isinstance(item, dict)
    }
    return ReviewDecisionTableContext(
        selection_candidate_keys=selection_candidate_keys,
        trace_decisions=trace_decisions,
        selected_gate_candidate_keys=selected_gate_candidate_keys,
        dedup_drop_candidate_keys=dedup_drop_candidate_keys,
        final_candidate_keys=final_candidate_keys,
        final_priority_by_candidate_key=final_priority_by_candidate_key,
        structure_rows_by_index=structure_rows_by_index,
    )


def resolve_review_priority_fields(
    *,
    case: dict[str, Any],
    signature: str,
    retained: bool,
    final_priority_by_signature: dict[str, str],
) -> ReviewPriorityFields:
    model_priority_value = str(
        case.get("model_priority_current")
        or case.get("model_priority")
        or ""
    ).strip().upper()
    legacy_priority_value = str(case.get("legacy_priority") or case.get("priority") or "").strip().upper()
    priority_final_value = str(case.get("priority_final") or "").strip().upper()
    priority_decision_state_value = str(case.get("priority_decision_state") or "").strip().lower()
    priority_decision_source_value = str(case.get("priority_decision_source") or "").strip() or "insufficient_evidence"
    priority_confidence_value = str(case.get("priority_confidence") or "").strip() or "low"
    priority_conflict_reason_value = str(case.get("priority_conflict_reason") or "").strip()
    priority_resolution_reason_value = str(case.get("priority_resolution_reason") or "").strip()
    unresolved_priority_decision = bool(
        priority_decision_state_value in {"conflict", "undetermined", "invalid"}
        and priority_final_value not in _VALID_PRIORITY_VALUES
    )
    if retained and not unresolved_priority_decision:
        planned_priority_value = str(final_priority_by_signature.get(signature) or "").strip().upper()
        if planned_priority_value in _VALID_PRIORITY_VALUES:
            priority_changed_by_plan = planned_priority_value != priority_final_value
            priority_final_value = planned_priority_value
            if priority_changed_by_plan:
                priority_resolution_reason_value = "priority_final_reflected_from_execution_plan"
                priority_decision_source_value = "execution_plan_final_priority"
            elif not priority_decision_source_value or priority_decision_source_value == "insufficient_evidence":
                priority_decision_source_value = "execution_plan_final_priority"
    if priority_decision_state_value not in _ALLOWED_PRIORITY_DECISION_STATES:
        if priority_final_value in _VALID_PRIORITY_VALUES:
            priority_decision_state_value = "decided"
        else:
            priority_decision_state_value = "undetermined"
    if priority_final_value not in _VALID_PRIORITY_VALUES:
        if priority_decision_state_value == "decided" and legacy_priority_value in _VALID_PRIORITY_VALUES:
            priority_final_value = legacy_priority_value
            if not priority_resolution_reason_value:
                priority_resolution_reason_value = "priority_final_backfilled_from_legacy_priority"
        else:
            priority_decision_state_value = "invalid"
            if not priority_decision_source_value or priority_decision_source_value == "insufficient_evidence":
                priority_decision_source_value = "priority_final_missing_after_semantic_resolve"
            if not priority_resolution_reason_value:
                priority_resolution_reason_value = "missing_priority_final_after_semantic_resolve"
            priority_final_value = ""
    return ReviewPriorityFields(
        model_priority_value=model_priority_value,
        legacy_priority_value=legacy_priority_value,
        priority_final_value=priority_final_value,
        priority_decision_state_value=priority_decision_state_value,
        priority_decision_source_value=priority_decision_source_value,
        priority_confidence_value=priority_confidence_value,
        priority_conflict_reason_value=priority_conflict_reason_value,
        priority_resolution_reason_value=priority_resolution_reason_value,
        unresolved_priority_decision=unresolved_priority_decision,
    )


def resolve_review_priority_summary_flags(
    review_decision_summary: dict[str, Any] | None,
) -> ReviewPrioritySummaryFlags:
    summary = review_decision_summary or {}
    priority_conflict_count = int(summary.get("priority_conflict_count") or 0)
    priority_undetermined_count = int(summary.get("priority_undetermined_count") or 0)
    priority_optional_count = int(summary.get("priority_optional_count") or 0)
    needs_priority_review = bool(
        summary.get("needs_priority_review")
        or priority_conflict_count > 0
        or priority_undetermined_count > 0
    )
    return ReviewPrioritySummaryFlags(
        priority_conflict_count=priority_conflict_count,
        priority_undetermined_count=priority_undetermined_count,
        priority_optional_count=priority_optional_count,
        needs_priority_review=needs_priority_review,
    )


def build_review_candidate_row_base_fields(
    *,
    index: int,
    case: dict[str, Any],
    signature: str,
    structure_row: dict[str, Any],
    gate_info: dict[str, Any],
    rule_keys: list[Any],
    bucket: str,
    adds_rule: bool,
    adds_bucket: bool,
    high_signal: bool,
    has_coverage_value: bool,
    retained_reason: str,
    review_case_id_fn: Callable[[dict[str, Any]], str],
    case_text_field_fn: Callable[[dict[str, Any], str], str],
    focus_score_fn: Callable[[dict[str, Any]], int],
) -> dict[str, Any]:
    return {
        "candidate_index": int(index),
        "signature": signature,
        "case_id": review_case_id_fn(case),
        "description": case_text_field_fn(case, "description"),
        "test_module": case_text_field_fn(case, "test_module"),
        "expected_result": case_text_field_fn(case, "expected_result"),
        "flow_stage": str(structure_row.get("flow_stage") or "unknown"),
        "flow_stage_label": str(
            structure_row.get("flow_stage_label")
            or structure_row.get("flow_stage")
            or "unknown"
        ),
        "flow_rank": structure_row.get("flow_rank"),
        "cross_cutting": [str(item) for item in (structure_row.get("cross_cutting") or [])],
        "scenario_key": str(structure_row.get("scenario_key") or ""),
        "is_scenario_duplicate": bool(structure_row.get("is_scenario_duplicate")),
        "duplicate_cluster_id": str(structure_row.get("duplicate_cluster_id") or ""),
        "duplicate_cluster_size": int(structure_row.get("duplicate_cluster_size") or 0),
        "duplicate_of_case_id": str(structure_row.get("duplicate_of_case_id") or ""),
        "misordered_against_requirement_flow": bool(
            structure_row.get("misordered_against_requirement_flow")
        ),
        "expected_result_quality": str(case.get("expected_result_quality") or ""),
        "expected_result_quality_reason": str(case.get("expected_result_quality_reason") or ""),
        "expected_result_alignment_warning": bool(case.get("expected_result_alignment_warning")),
        "truncated_text_detected": bool(case.get("truncated_text_detected")),
        "case_quality": str(case.get("case_quality") or "valid_case"),
        "invalid_case_reason": str(case.get("invalid_case_reason") or ""),
        "invalid_case_signals": [str(item) for item in (case.get("invalid_case_signals") or [])],
        "rule_keys": rule_keys,
        "bucket": bucket,
        "adds_rule": bool(adds_rule),
        "adds_bucket": bool(adds_bucket),
        "high_signal": bool(high_signal),
        "has_coverage_value": bool(has_coverage_value),
        "retained_reason": retained_reason,
        "rerank_rank": int(gate_info.get("rank") or 0),
        "focus_score": int(gate_info.get("focus_score") or focus_score_fn(case)),
    }


def build_review_candidate_row_diagnostic_fields(
    *,
    model_priority_value: str,
    legacy_priority_value: str,
    priority_final_value: str,
    priority_decision_state_value: str,
    priority_decision_source_value: str,
    priority_confidence_value: str,
    priority_conflict_reason_value: str,
    priority_resolution_reason_value: str,
    score_profile: dict[str, Any],
    selected_by_review_llm: bool,
    selected_by_review_constraints: bool,
    review_constraint_reason: str,
    review_llm_drop_reason_raw: str,
    review_llm_drop_reason: str,
    review_llm_drop_reason_source: str,
    review_llm_drop_reason_evidence: dict[str, Any],
    has_positive_evidence: bool,
    has_coverage_signal: bool,
    has_high_signal: bool,
    has_competition_signal: bool,
    review_llm_applied: bool,
    signature: str,
    selected_gate_signatures: set[str],
    retained: bool,
    dropped_stage: str,
    dropped_reason: str,
    hit_must_cover_rule: bool,
    violates_forbidden_pattern: bool,
    hits_soft_constraint: bool,
    satisfies_quality_hint: bool,
) -> dict[str, Any]:
    return {
        "model_priority_current": model_priority_value,
        "model_priority": model_priority_value,
        "legacy_priority": legacy_priority_value,
        "priority_final": priority_final_value,
        "priority_decision_state": priority_decision_state_value,
        "priority_decision_source": priority_decision_source_value,
        "priority_confidence": priority_confidence_value,
        "priority_conflict_reason": priority_conflict_reason_value,
        "priority_resolution_reason": priority_resolution_reason_value,
        "priority_score": int(score_profile.get("priority_score") or 0),
        "suggested_priority": str(score_profile.get("suggested_priority") or "").strip().upper(),
        "priority_reasons": [
            str(item)
            for item in (score_profile.get("reasons") or [])
            if str(item).strip()
        ],
        "selected_by_review_llm": bool(selected_by_review_llm),
        "selected_by_review_constraints": bool(selected_by_review_constraints),
        "review_constraint_reason": review_constraint_reason,
        "review_llm_drop_reason_raw": review_llm_drop_reason_raw,
        "review_llm_drop_reason": review_llm_drop_reason,
        "review_llm_drop_reason_resolved": review_llm_drop_reason,
        "review_llm_drop_reason_source": review_llm_drop_reason_source,
        "review_llm_drop_reason_evidence": review_llm_drop_reason_evidence,
        "has_positive_evidence": bool(has_positive_evidence),
        "has_coverage_signal": bool(has_coverage_signal),
        "has_high_signal": bool(has_high_signal),
        "has_competition_signal": bool(has_competition_signal),
        "review_llm_filter_applied": bool(review_llm_applied),
        "selected_by_review_gate": bool(signature in selected_gate_signatures),
        "retained_final": bool(retained),
        "dropped_stage": dropped_stage,
        "dropped_reason": dropped_reason,
        "covered_rule_ids": [str(item) for item in (score_profile.get("covered_rule_ids") or [])],
        "missing_rule_hits": [str(item) for item in (score_profile.get("missing_rule_hits") or [])],
        "core_rule_hits": [str(item) for item in (score_profile.get("core_rule_hits") or [])],
        "coverage_gain_score": int(score_profile.get("coverage_gain_score") or 0),
        "reuse_risk_hit": bool(score_profile.get("reuse_risk_hit")),
        "hit_must_cover_rule": bool(hit_must_cover_rule),
        "violates_forbidden_pattern": bool(violates_forbidden_pattern),
        "hits_soft_constraint": bool(hits_soft_constraint),
        "satisfies_quality_hint": bool(satisfies_quality_hint),
    }


def resolve_review_candidate_drop_decision(
    *,
    signature: str,
    candidate_key: str,
    review_llm_applied: bool,
    review_llm_selected_signatures: set[str],
    review_constraint_retained_signatures: set[str],
    review_constraint_reason_map: dict[str, Any],
    review_llm_drop_reason_raw_map: dict[str, Any],
    review_llm_drop_reason_map: dict[str, Any],
    review_llm_drop_reason_source_map: dict[str, Any],
    review_llm_drop_reason_evidence_map: dict[str, Any],
    selection_candidate_keys: set[str],
    append_cap_drop_signatures: set[str],
    final_description_dedup_drop_signatures: set[str],
    dedup_drop_candidate_keys: set[str],
    selected_gate_candidate_keys: set[str],
    final_candidate_keys: set[str],
    gate_reason: str,
) -> ReviewCandidateDropDecision:
    selected_by_review_llm = signature in review_llm_selected_signatures if review_llm_applied else True
    selected_by_constraint_guard = signature in review_constraint_retained_signatures
    review_llm_drop_reason_raw = str(review_llm_drop_reason_raw_map.get(signature) or "")
    review_llm_drop_reason = str(review_llm_drop_reason_map.get(signature) or "")
    review_llm_drop_reason_source = str(review_llm_drop_reason_source_map.get(signature) or "")
    review_llm_drop_reason_evidence = review_llm_drop_reason_evidence_map.get(signature)
    if not isinstance(review_llm_drop_reason_evidence, dict):
        review_llm_drop_reason_evidence = {}
    has_coverage_signal = bool(review_llm_drop_reason_evidence.get("has_coverage_signal"))
    has_high_signal = bool(review_llm_drop_reason_evidence.get("has_high_signal"))
    has_competition_signal = bool(review_llm_drop_reason_evidence.get("has_competition_signal"))
    has_positive_evidence = bool(
        review_llm_drop_reason_evidence.get("has_positive_evidence")
        or has_coverage_signal
        or has_high_signal
        or has_competition_signal
    )
    review_constraint_reason = str(review_constraint_reason_map.get(signature) or "")
    retained = candidate_key in final_candidate_keys
    dropped_stage = ""
    dropped_reason = ""
    if (
        review_llm_applied
        and (not selected_by_review_llm)
        and (not selected_by_constraint_guard)
    ):
        dropped_stage = "review_llm"
        dropped_reason = "drop_not_selected_by_review_llm"
        if review_llm_drop_reason:
            dropped_reason = f"drop_not_selected_by_review_llm:{review_llm_drop_reason}"
    elif candidate_key not in selection_candidate_keys:
        dropped_stage = "review_selector"
        if review_constraint_reason == "dropped_by_target_max":
            dropped_reason = "drop_outside_target_window"
        elif review_llm_applied and selected_by_review_llm:
            dropped_reason = "drop_after_llm_selection"
        else:
            dropped_reason = "drop_selector_fallback"
    elif signature in append_cap_drop_signatures:
        dropped_stage = "append_target_cap"
        dropped_reason = "drop_exceeds_append_target_count"
    elif signature in final_description_dedup_drop_signatures:
        dropped_stage = "post_review_dedup_or_reorder"
        dropped_reason = "drop_final_description_duplicate"
    elif candidate_key in dedup_drop_candidate_keys:
        dropped_stage = "review_dedup_pre_gate"
        dropped_reason = "drop_dedup_pre_gate"
    elif candidate_key in selection_candidate_keys and candidate_key not in selected_gate_candidate_keys:
        dropped_stage = "review_gate"
        dropped_reason = gate_reason or "drop_review_gate"
    elif candidate_key in selected_gate_candidate_keys and candidate_key not in final_candidate_keys:
        dropped_stage = "post_review_dedup_or_reorder"
        dropped_reason = "drop_post_review_dedup_or_reorder"
    elif retained:
        dropped_stage = "retained"
        dropped_reason = "retained"
    return ReviewCandidateDropDecision(
        selected_by_review_llm=selected_by_review_llm,
        selected_by_constraint_guard=selected_by_constraint_guard,
        review_llm_drop_reason_raw=review_llm_drop_reason_raw,
        review_llm_drop_reason=review_llm_drop_reason,
        review_llm_drop_reason_source=review_llm_drop_reason_source,
        review_llm_drop_reason_evidence=review_llm_drop_reason_evidence,
        has_positive_evidence=has_positive_evidence,
        has_coverage_signal=has_coverage_signal,
        has_high_signal=has_high_signal,
        has_competition_signal=has_competition_signal,
        review_constraint_reason=review_constraint_reason,
        dropped_stage=dropped_stage,
        dropped_reason=dropped_reason,
    )


def resolve_review_row_coverage_retention_fields(
    *,
    gate_info: dict[str, Any],
    score_profile: dict[str, Any],
    retained: bool,
    adds_rule: bool,
    adds_bucket: bool,
) -> ReviewRowCoverageRetentionFields:
    score_has_coverage_value = bool(
        score_profile.get("missing_rule_hits")
        or score_profile.get("core_rule_hits")
        or score_profile.get("unique_coverage_hits")
        or int(score_profile.get("coverage_gain_score") or 0) > 0
    )
    has_coverage_value_for_row = bool(
        (bool(gate_info.get("has_coverage_value")) if gate_info else False)
        or score_has_coverage_value
    )
    retained_reason_value = str(gate_info.get("retained_reason") or "")
    if (
        not retained_reason_value
        and retained
        and has_coverage_value_for_row
        and not adds_rule
        and not adds_bucket
        and not bool(score_profile.get("reuse_risk_hit"))
    ):
        retained_reason_value = "retained_due_to_coverage_value"
    return ReviewRowCoverageRetentionFields(
        has_coverage_value_for_row=has_coverage_value_for_row,
        retained_reason_value=retained_reason_value,
    )
