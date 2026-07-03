from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .json_generation_dependencies import (
    _build_case_signature,
    analyze_coverage,
    apply_existing_execution_group_ordering,
    build_requirement_semantics_payload,
    case_access_id,
    case_text_field,
    deduplicate_test_cases,
    judge_cases,
    reorder_cases_by_closed_loop,
    repair_cases,
    training_gate,
)
from .json_generation_persist_diagnostics import EMPTY_GENERATED_RESULT_MESSAGE


@dataclass(frozen=True)
class JsonFallbackReviewResult:
    result: Any
    candidate_cases_before_judge: list[dict[str, Any]]
    candidate_total_before_judge: int
    final_cases_after_judge: list[dict[str, Any]]
    final_case_count: int
    empty_result_guard_triggered: bool
    empty_result_stage: str
    coverage_check_payload: dict[str, Any]
    review_decision_summary_payload: dict[str, Any]
    generation_summary_payload: dict[str, Any]
    convergence_payload: dict[str, Any]
    judge_summary_payload: dict[str, Any]
    judge_decision_table_payload: list[dict[str, Any]]
    review_decision_table_payload: list[dict[str, Any]]


def _stage_case_counts(stage_logs: list[dict[str, Any]]) -> tuple[int, int, int]:
    stage_primary = 0
    stage_gap = 0
    stage_review = 0
    for stage_log in stage_logs:
        if not isinstance(stage_log, dict):
            continue
        if str(stage_log.get("kind") or "").strip() != "generation_stage":
            continue
        stage = str(stage_log.get("stage") or "").strip().lower()
        count = int(stage_log.get("case_count") or 0)
        if stage == "primary":
            stage_primary = count
        elif stage == "gap":
            stage_gap = count
        elif stage == "review":
            stage_review = count
    return stage_primary, stage_gap, stage_review


def _build_review_decision_summary_payload(
    *,
    candidate_total: int,
    retained_total: int,
    stage_primary: int,
    stage_gap: int,
    empty_result_guard_triggered: bool,
    empty_result_stage: str,
) -> dict[str, Any]:
    dropped_total = max(0, int(candidate_total) - int(retained_total))
    return {
        "candidate_total": int(candidate_total),
        "retained_total": int(retained_total),
        "dropped_total": int(dropped_total),
        "drop_by_review_llm_count": 0,
        "drop_by_review_selector_count": 0,
        "drop_by_review_gate_count": int(dropped_total),
        "drop_by_pre_gate_dedup_count": 0,
        "drop_by_post_review_dedup_count": 0,
        "drop_no_new_signal_count": 0,
        "drop_rule_cap_count": 0,
        "review_input_size": int(candidate_total),
        "review_output_size": int(retained_total),
        "review_llm_filter_applied": False,
        "review_decision_summary_available": True,
        "review_skipped_reason": "",
        "reason_source_breakdown": {"primary": 0, "fallback": 0, "backfill": 0},
        "review_llm_drop_reason_source_breakdown": {"llm": 0, "fallback_llm": 0, "deterministic_backfill": 0},
        "priority_decision_state_breakdown": {"decided": 0, "conflict": 0, "undetermined": 0, "optional": 0, "invalid": 0},
        "priority_final_breakdown": {"P0": 0, "P1": 0, "P2": 0, "null": 0},
        "legacy_priority_breakdown": {"P0": 0, "P1": 0, "P2": 0, "UNKNOWN": 0},
        "priority_conflict_count": 0,
        "priority_undetermined_count": 0,
        "priority_optional_count": 0,
        "priority_invalid_count": 0,
        "needs_priority_review": False,
        "candidate_primary": int(stage_primary),
        "candidate_gap": int(stage_gap),
        "final_case_count": int(retained_total),
        "empty_result_guard_triggered": bool(empty_result_guard_triggered),
        "empty_result_stage": str(empty_result_stage),
        "llm_reason_coverage_ratio": 0.0,
        "deterministic_backfill_ratio": 0.0,
        "primary_reason_incomplete": True,
        "primary_dropped_reason_count": 0,
        "primary_dropped_reason_payload_count": 0,
        "primary_reason_coverage_ratio": 0.0,
        "fallback_reason_incomplete": False,
        "fallback_dropped_reason_count": 0,
        "fallback_dropped_reason_mapped_count": 0,
        "fallback_dropped_reason_unmapped_count": 0,
        "fallback_reason_coverage_ratio": 0.0,
        "review_llm_runtime_debug": {
            "invoked": False,
            "mapped_count": 0,
            "dropped_reason_count": 0,
            "payload_has_selection_signal": False,
            "applied_reason": "judge_training_gate_path",
            "primary_model": "",
            "primary_invalid_reason": "review_not_invoked_generate_tests_json",
            "retry_invoked": False,
            "retry_reason": "",
            "retry_model": "",
            "retry_parse_success": False,
            "retry_mapped_count": 0,
            "retry_payload_has_selection_signal": False,
            "final_source": "review_selector",
            "final_dropped_reason_count": 0,
            "final_dropped_reason_payload_count": 0,
            "final_dropped_reason_unmapped_count": 0,
            "final_reason_incomplete": False,
        },
    }


def _update_priority_breakdowns(
    review_decision_summary_payload: dict[str, Any],
    final_cases_after_judge: list[dict[str, Any]],
) -> None:
    for case_item in final_cases_after_judge:
        if not isinstance(case_item, dict):
            continue
        priority_state = str(case_item.get("priority_decision_state") or "undetermined").strip().lower()
        if priority_state not in {"decided", "conflict", "undetermined", "optional", "invalid"}:
            priority_state = "undetermined"
        review_decision_summary_payload["priority_decision_state_breakdown"][priority_state] += 1

        priority_final = case_text_field(case_item, "priority_final").upper()
        if priority_final not in {"P0", "P1", "P2"}:
            priority_final = "null"
        review_decision_summary_payload["priority_final_breakdown"][priority_final] += 1

        legacy_priority = str(case_item.get("legacy_priority") or case_text_field(case_item, "priority") or "").strip().upper()
        if legacy_priority not in {"P0", "P1", "P2"}:
            legacy_priority = "UNKNOWN"
        review_decision_summary_payload["legacy_priority_breakdown"][legacy_priority] += 1

    review_decision_summary_payload["priority_conflict_count"] = int(
        review_decision_summary_payload["priority_decision_state_breakdown"].get("conflict") or 0
    )
    review_decision_summary_payload["priority_undetermined_count"] = int(
        review_decision_summary_payload["priority_decision_state_breakdown"].get("undetermined") or 0
    )
    review_decision_summary_payload["priority_optional_count"] = int(
        review_decision_summary_payload["priority_decision_state_breakdown"].get("optional") or 0
    )
    review_decision_summary_payload["priority_invalid_count"] = int(
        review_decision_summary_payload["priority_decision_state_breakdown"].get("invalid") or 0
    )
    review_decision_summary_payload["needs_priority_review"] = bool(
        int(review_decision_summary_payload["priority_conflict_count"] or 0) > 0
        or int(review_decision_summary_payload["priority_undetermined_count"] or 0) > 0
    )


def _build_judge_table_payload(repaired: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for judged_item in repaired.cases or []:
        signal_set = judged_item.signals
        before_case = judged_item.before_case if isinstance(judged_item.before_case, dict) else {}
        after_case = judged_item.after_case if isinstance(judged_item.after_case, dict) else {}
        signals_payload = {
            "violates_confirmed_fact": bool(signal_set.violates_confirmed_fact),
            "missing_core_flow": bool(signal_set.missing_core_flow),
            "missing_reuse_risk": bool(signal_set.missing_reuse_risk),
            "contains_pending_logic": bool(signal_set.contains_pending_logic),
            "confirmed_fact_hits": [str(item) for item in (signal_set.confirmed_fact_hits or [])],
            "confirmed_fact_violations": [
                str(item) for item in (signal_set.confirmed_fact_violations or [])
            ],
            "reuse_risk_hits": [str(item) for item in (signal_set.reuse_risk_hits or [])],
            "pending_hits": [str(item) for item in (signal_set.pending_hits or [])],
            "vague_or_unconfirmed_hits": [
                str(item) for item in (getattr(signal_set, "vague_or_unconfirmed_hits", []) or [])
            ],
        }
        rows.append(
            {
                "case_id": str(judged_item.case_id or ""),
                "status": str(getattr(judged_item.status, "value", judged_item.status)),
                "reject_reason": str(judged_item.reject_reason or ""),
                "pending_reason": str(judged_item.pending_reason or ""),
                "signals": signals_payload,
                "violates_confirmed_fact": bool(signals_payload.get("violates_confirmed_fact")),
                "missing_core_flow": bool(signals_payload.get("missing_core_flow")),
                "missing_reuse_risk": bool(signals_payload.get("missing_reuse_risk")),
                "contains_pending_logic": bool(signals_payload.get("contains_pending_logic")),
                "confirmed_fact_hits": list(signals_payload.get("confirmed_fact_hits") or []),
                "confirmed_fact_violations": list(signals_payload.get("confirmed_fact_violations") or []),
                "reuse_risk_hits": list(signals_payload.get("reuse_risk_hits") or []),
                "pending_hits": list(signals_payload.get("pending_hits") or []),
                "vague_or_unconfirmed_hits": list(
                    signals_payload.get("vague_or_unconfirmed_hits") or []
                ),
                "before_case_snapshot": dict(before_case),
                "after_case_snapshot": dict(after_case),
            }
        )
    return rows


def _build_review_decision_table_payload(
    *,
    candidate_cases_before_judge: list[dict[str, Any]],
    final_cases_after_judge: list[dict[str, Any]],
    judge_decision_table_payload: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    final_signature_counts: dict[str, int] = {}
    for case_payload in final_cases_after_judge:
        signature = _build_case_signature(case_payload)
        if not signature:
            continue
        final_signature_counts[signature] = int(final_signature_counts.get(signature) or 0) + 1

    judge_by_case_id: dict[str, dict[str, Any]] = {}
    judge_reject_pending_by_signature: dict[str, list[dict[str, Any]]] = {}
    for judge_row in judge_decision_table_payload:
        if not isinstance(judge_row, dict):
            continue
        judge_case_id = str(judge_row.get("case_id") or "").strip()
        if judge_case_id:
            judge_by_case_id[judge_case_id] = judge_row
        judge_status = str(judge_row.get("status") or judge_row.get("judge_status") or "").strip().upper()
        if judge_status not in {"REJECT", "PENDING"}:
            continue
        before_case_snapshot = judge_row.get("before_case_snapshot")
        if not isinstance(before_case_snapshot, dict):
            before_case_snapshot = {}
        signature = _build_case_signature(before_case_snapshot)
        if not signature:
            continue
        judge_reject_pending_by_signature.setdefault(signature, []).append(judge_row)

    review_rows: list[dict[str, Any]] = []
    dropped_by_gate_count = 0
    dropped_by_post_dedup_count = 0
    for candidate_index, case_item in enumerate(candidate_cases_before_judge, start=1):
        if not isinstance(case_item, dict):
            continue
        candidate_signature = _build_case_signature(case_item)
        retained_final = False
        if candidate_signature and int(final_signature_counts.get(candidate_signature) or 0) > 0:
            retained_final = True
            final_signature_counts[candidate_signature] = int(final_signature_counts.get(candidate_signature) or 0) - 1

        case_id = case_access_id(case_item) or f"ROW-{candidate_index:03d}"
        model_priority = str(
            case_item.get("model_priority_current")
            or case_item.get("model_priority")
            or case_text_field(case_item, "priority")
            or ""
        ).strip().upper()
        legacy_priority = str(case_item.get("legacy_priority") or case_text_field(case_item, "priority") or "").strip().upper()
        priority_state = str(case_item.get("priority_decision_state") or "undetermined").strip().lower()
        if priority_state not in {"decided", "conflict", "undetermined", "optional", "invalid"}:
            priority_state = "undetermined"
        priority_final = case_text_field(case_item, "priority_final").upper()
        if priority_final not in {"P0", "P1", "P2"}:
            priority_final = ""

        dropped_stage = "retained"
        dropped_reason = "retained"
        retained_reason = "retained_after_judge"
        judge_ref: dict[str, Any] = {}
        if not retained_final:
            judge_ref = dict(judge_by_case_id.get(case_id) or {})
            if not judge_ref and candidate_signature:
                signature_rows = judge_reject_pending_by_signature.get(candidate_signature) or []
                if signature_rows:
                    judge_ref = dict(signature_rows.pop(0) or {})
            judge_status = str(judge_ref.get("status") or judge_ref.get("judge_status") or "").strip().upper()
            if judge_status in {"REJECT", "PENDING"}:
                dropped_stage = "review_gate"
                dropped_reason = str(
                    judge_ref.get("reject_reason")
                    or judge_ref.get("pending_reason")
                    or judge_status.lower()
                ).strip() or judge_status.lower()
                dropped_by_gate_count += 1
            else:
                dropped_stage = "post_review_dedup"
                dropped_reason = "post_judge_dedup_or_merge"
                dropped_by_post_dedup_count += 1
            retained_reason = ""

        review_rows.append(
            {
                "candidate_index": int(candidate_index),
                "case_id": case_id,
                "description": case_text_field(case_item, "description"),
                "test_module": case_text_field(case_item, "test_module"),
                "model_priority": model_priority,
                "model_priority_current": model_priority,
                "legacy_priority": legacy_priority if legacy_priority in {"P0", "P1", "P2"} else "UNKNOWN",
                "priority_final": priority_final or "",
                "priority_decision_state": priority_state,
                "priority_decision_source": str(case_item.get("priority_decision_source") or "").strip(),
                "priority_confidence": str(case_item.get("priority_confidence") or "").strip(),
                "priority_conflict_reason": str(case_item.get("priority_conflict_reason") or "").strip(),
                "priority_score": case_item.get("priority_score"),
                "suggested_priority": str(case_item.get("suggested_priority") or "").strip(),
                "priority_reasons": case_item.get("priority_reasons") if isinstance(case_item.get("priority_reasons"), list) else [],
                "selected_by_review_llm": False,
                "selected_by_review_must_keep": False,
                "selected_by_review_constraints": False,
                "selected_by_review_gate": bool(not retained_final and dropped_stage == "review_gate"),
                "retained_final": bool(retained_final),
                "dropped_stage": dropped_stage,
                "dropped_reason": dropped_reason,
                "review_llm_drop_reason_raw": "",
                "review_llm_drop_reason": "",
                "review_llm_drop_reason_source": "",
                "review_llm_drop_reason_evidence": {},
                "has_positive_evidence": False,
                "has_coverage_signal": False,
                "has_high_signal": False,
                "has_competition_signal": False,
                "review_constraint_reason": "",
                "bucket": str(case_item.get("bucket") or "").strip(),
                "rule_keys": list(case_item.get("rule_keys") or []) if isinstance(case_item.get("rule_keys"), list) else [],
                "adds_rule": bool(case_item.get("adds_rule")),
                "adds_bucket": bool(case_item.get("adds_bucket")),
                "high_signal": bool(case_item.get("high_signal")),
                "has_coverage_value": bool(case_item.get("has_coverage_value")),
                "retained_reason": retained_reason,
                "rerank_rank": case_item.get("rerank_rank") or "",
                "focus_score": case_item.get("focus_score") or "",
                "covered_rule_ids": list(case_item.get("covered_rule_ids") or []) if isinstance(case_item.get("covered_rule_ids"), list) else [],
                "missing_rule_hits": list(case_item.get("missing_rule_hits") or []) if isinstance(case_item.get("missing_rule_hits"), list) else [],
                "core_rule_hits": list(case_item.get("core_rule_hits") or []) if isinstance(case_item.get("core_rule_hits"), list) else [],
                "coverage_gain_score": case_item.get("coverage_gain_score") or "",
                "signature": candidate_signature,
            }
        )

    return review_rows, dropped_by_gate_count, dropped_by_post_dedup_count


def run_json_fallback_review(
    *,
    result: list[dict[str, Any]],
    prompt_context: dict[str, Any],
    requirement: str,
    feedback_control_state: dict[str, Any],
    stage_logs: list[dict[str, Any]],
    expected_count: int,
    start_id: int,
    judge_cases_fn: Any = None,
    repair_cases_fn: Any = None,
    training_gate_fn: Any = None,
    deduplicate_test_cases_fn: Any = None,
    reorder_cases_by_closed_loop_fn: Any = None,
    apply_existing_execution_group_ordering_fn: Any = None,
) -> JsonFallbackReviewResult:
    judge_cases_runner = judge_cases_fn or judge_cases
    repair_cases_runner = repair_cases_fn or repair_cases
    training_gate_runner = training_gate_fn or training_gate
    deduplicate_runner = deduplicate_test_cases_fn or deduplicate_test_cases
    reorder_runner = reorder_cases_by_closed_loop_fn or reorder_cases_by_closed_loop
    execution_group_order_runner = (
        apply_existing_execution_group_ordering_fn or apply_existing_execution_group_ordering
    )

    candidate_cases_before_judge = [item for item in result if isinstance(item, dict)]
    candidate_total_before_judge = int(len(candidate_cases_before_judge))
    requirement_semantics_payload = build_requirement_semantics_payload(prompt_context)
    judged = judge_cases_runner(
        cases=candidate_cases_before_judge,
        requirement_semantics_context=requirement_semantics_payload,
        control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
    )
    repaired = repair_cases_runner(
        judged=judged,
        requirement_semantics_context=requirement_semantics_payload,
        control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
        strategy="rule_first_llm_fallback",
    )
    confirmed_pass_cases, repaired_pass_cases, rejected_cases, pending_cases = training_gate_runner(repaired)
    result_value: Any = deduplicate_runner([*confirmed_pass_cases, *repaired_pass_cases])
    result_value = reorder_runner(
        result_value,
        start_id=start_id,
        renumber_ids=True,
    )
    result_value = execution_group_order_runner(
        result_value,
        start_id=start_id,
        renumber_ids=True,
    )
    final_cases_after_judge = [item for item in result_value if isinstance(item, dict)]
    final_case_count = int(len(final_cases_after_judge))
    empty_result_guard_triggered = False
    empty_result_stage = ""
    if final_case_count <= 0:
        empty_result_guard_triggered = True
        empty_result_stage = "post_judge_training_gate"
        result_value = {
            "error": "EMPTY_GENERATED_RESULT",
            "error_code": "EMPTY_GENERATED_RESULT",
            "error_message": EMPTY_GENERATED_RESULT_MESSAGE,
            "status": "failed",
            "final_status": "empty_result_failed",
            "empty_result_guard_triggered": True,
            "empty_result_stage": empty_result_stage,
            "candidate_total": int(candidate_total_before_judge),
            "review_input_size": int(candidate_total_before_judge),
            "review_output_size": 0,
            "final_case_count": 0,
        }

    coverage_check_payload = {
        "kind": "coverage_check",
        **analyze_coverage(
            prompt_context.get("requirement_context") or requirement,
            result_value if isinstance(result_value, list) else [],
        ),
    }
    stage_primary, stage_gap, stage_review = _stage_case_counts(stage_logs)
    if stage_primary <= 0:
        stage_primary = int(candidate_total_before_judge)
    candidate_total = int(candidate_total_before_judge or stage_review or stage_primary)
    retained_total = int(final_case_count if not empty_result_guard_triggered else 0)
    review_decision_summary_payload = _build_review_decision_summary_payload(
        candidate_total=candidate_total,
        retained_total=retained_total,
        stage_primary=stage_primary,
        stage_gap=stage_gap,
        empty_result_guard_triggered=empty_result_guard_triggered,
        empty_result_stage=empty_result_stage,
    )
    _update_priority_breakdowns(review_decision_summary_payload, final_cases_after_judge)
    generation_summary_payload = {
        "status": "failed_empty_result" if empty_result_guard_triggered else "completed",
        "final_status": "empty_result_failed" if empty_result_guard_triggered else "success",
        "final_count": int(retained_total),
        "expected_count": int(expected_count or 0),
        "candidate_total": int(candidate_total),
        "review_input_size": int(candidate_total),
        "review_output_size": int(retained_total),
        "needs_priority_review": bool(review_decision_summary_payload.get("needs_priority_review")),
        "review_decision_summary_available": True,
        "review_skipped_reason": "",
        "empty_result_guard_triggered": bool(empty_result_guard_triggered),
        "empty_result_stage": str(empty_result_stage),
    }
    if empty_result_guard_triggered:
        generation_summary_payload["error_code"] = "EMPTY_GENERATED_RESULT"
        generation_summary_payload["error_message"] = EMPTY_GENERATED_RESULT_MESSAGE
    convergence_payload = {
        "primary_count": int(stage_primary),
        "gap_count": int(stage_gap),
        "review_count": int(stage_review or candidate_total),
        "candidate_count_before_review": int(candidate_total),
        "review_selected_count": int(retained_total),
        "final_count": int(retained_total),
        "expected_count": int(expected_count or 0),
        "empty_result_guard_triggered": bool(empty_result_guard_triggered),
        "empty_result_stage": str(empty_result_stage),
    }
    judge_summary_payload = {
        "pass_count": int(repaired.pass_count or 0),
        "repairable_count": int(repaired.repairable_count or 0),
        "reject_count": int(repaired.reject_count or 0),
        "pending_count": int(repaired.pending_count or 0),
        "repaired_case_count": int(repaired.repaired_case_count or 0),
        "appended_case_count": int(repaired.appended_case_count or 0),
        "confirmed_pass_out_count": int(len(confirmed_pass_cases)),
        "repaired_pass_out_count": int(len(repaired_pass_cases)),
        "rejected_out_count": int(len(rejected_cases)),
        "pending_out_count": int(len(pending_cases)),
        "core_flow_covered": bool(repaired.core_flow_covered),
        "reuse_risk_covered": bool(repaired.reuse_risk_covered),
    }
    judge_decision_table_payload = _build_judge_table_payload(repaired)
    review_decision_table_payload, dropped_by_gate_count, dropped_by_post_dedup_count = (
        _build_review_decision_table_payload(
            candidate_cases_before_judge=candidate_cases_before_judge,
            final_cases_after_judge=final_cases_after_judge,
            judge_decision_table_payload=judge_decision_table_payload,
        )
    )
    review_decision_summary_payload["drop_by_review_gate_count"] = int(dropped_by_gate_count)
    review_decision_summary_payload["drop_by_post_review_dedup_count"] = int(dropped_by_post_dedup_count)

    return JsonFallbackReviewResult(
        result=result_value,
        candidate_cases_before_judge=candidate_cases_before_judge,
        candidate_total_before_judge=candidate_total_before_judge,
        final_cases_after_judge=final_cases_after_judge,
        final_case_count=final_case_count,
        empty_result_guard_triggered=empty_result_guard_triggered,
        empty_result_stage=empty_result_stage,
        coverage_check_payload=coverage_check_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        generation_summary_payload=generation_summary_payload,
        convergence_payload=convergence_payload,
        judge_summary_payload=judge_summary_payload,
        judge_decision_table_payload=judge_decision_table_payload,
        review_decision_table_payload=review_decision_table_payload,
    )
