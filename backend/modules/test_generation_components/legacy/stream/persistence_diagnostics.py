from collections.abc import Callable
from typing import Any
import json


_MAX_GEN_DIAG_MESSAGE_BYTES = 60000


def _judge_status_key(row: dict[str, Any]) -> str:
    status = str((row or {}).get("judge_status") or (row or {}).get("status") or "").strip().upper()
    return status


def _safe_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _build_judge_signal_payload(row: dict[str, Any]) -> dict[str, Any]:
    signals_raw = row.get("signals") if isinstance(row.get("signals"), dict) else {}
    return {
        "violates_confirmed_fact": bool(
            signals_raw.get("violates_confirmed_fact", row.get("violates_confirmed_fact"))
        ),
        "missing_core_flow": bool(
            signals_raw.get("missing_core_flow", row.get("missing_core_flow"))
        ),
        "missing_reuse_risk": bool(
            signals_raw.get("missing_reuse_risk", row.get("missing_reuse_risk"))
        ),
        "contains_pending_logic": bool(
            signals_raw.get("contains_pending_logic", row.get("contains_pending_logic"))
        ),
        "confirmed_fact_hits": _safe_list(
            signals_raw.get("confirmed_fact_hits", row.get("confirmed_fact_hits"))
        ),
        "confirmed_fact_violations": _safe_list(
            signals_raw.get("confirmed_fact_violations", row.get("confirmed_fact_violations"))
        ),
        "reuse_risk_hits": _safe_list(signals_raw.get("reuse_risk_hits", row.get("reuse_risk_hits"))),
        "pending_hits": _safe_list(signals_raw.get("pending_hits", row.get("pending_hits"))),
        "vague_or_unconfirmed_hits": _safe_list(
            signals_raw.get("vague_or_unconfirmed_hits", row.get("vague_or_unconfirmed_hits"))
        ),
    }


def _normalize_judge_row(
    row: dict[str, Any],
    *,
    generation_id: int,
    request_id: str,
) -> dict[str, Any]:
    signals_payload = _build_judge_signal_payload(row)
    before_case = row.get("before_case_snapshot")
    if not isinstance(before_case, dict):
        before_case = row.get("before_case")
    if not isinstance(before_case, dict):
        before_case = {}
    after_case = row.get("after_case_snapshot")
    if not isinstance(after_case, dict):
        after_case = row.get("after_case")
    if not isinstance(after_case, dict):
        after_case = {}

    return {
        "generation_id": int(generation_id),
        "request_id": str(request_id or "").strip(),
        "case_id": str(row.get("case_id") or "").strip(),
        "judge_status": _judge_status_key(row),
        "reject_reason": str(row.get("reject_reason") or "").strip(),
        "pending_reason": str(row.get("pending_reason") or "").strip(),
        "signals": signals_payload,
        "violates_confirmed_fact": bool(signals_payload.get("violates_confirmed_fact")),
        "missing_core_flow": bool(signals_payload.get("missing_core_flow")),
        "missing_reuse_risk": bool(signals_payload.get("missing_reuse_risk")),
        "contains_pending_logic": bool(signals_payload.get("contains_pending_logic")),
        "confirmed_fact_hits": list(signals_payload.get("confirmed_fact_hits") or []),
        "confirmed_fact_violations": list(signals_payload.get("confirmed_fact_violations") or []),
        "reuse_risk_hits": list(signals_payload.get("reuse_risk_hits") or []),
        "pending_hits": list(signals_payload.get("pending_hits") or []),
        "vague_or_unconfirmed_hits": list(signals_payload.get("vague_or_unconfirmed_hits") or []),
        "before_case_snapshot": dict(before_case),
        "after_case_snapshot": dict(after_case),
    }


def _normalize_review_compact_rows(
    rows: list[dict[str, Any]],
    *,
    generation_id: int,
    request_id: str,
) -> list[dict[str, Any]]:
    compact_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("dropped_stage") or "") != "review_llm":
            continue
        evidence = row.get("review_llm_drop_reason_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        compact_rows.append(
            {
                "generation_id": int(generation_id),
                "request_id": str(request_id or "").strip(),
                "candidate_index": int(row.get("candidate_index") or 0),
                "case_id": str(row.get("case_id") or "").strip(),
                "test_module": str(row.get("test_module") or "").strip(),
                "flow_stage": str(row.get("flow_stage") or "").strip(),
                "flow_stage_label": str(row.get("flow_stage_label") or "").strip(),
                "scenario_key": str(row.get("scenario_key") or "").strip(),
                "is_scenario_duplicate": bool(row.get("is_scenario_duplicate")),
                "duplicate_cluster_id": str(row.get("duplicate_cluster_id") or "").strip(),
                "misordered_against_requirement_flow": bool(row.get("misordered_against_requirement_flow")),
                "model_priority_current": str(row.get("model_priority_current") or "").strip(),
                "bucket": str(row.get("bucket") or "").strip(),
                "dropped_stage": "review_llm",
                "dropped_reason": str(row.get("dropped_reason") or "").strip(),
                "review_llm_drop_reason_raw": str(row.get("review_llm_drop_reason_raw") or "").strip(),
                "review_llm_drop_reason": str(row.get("review_llm_drop_reason") or "").strip(),
                "review_llm_drop_reason_source": str(row.get("review_llm_drop_reason_source") or "").strip(),
                "high_signal": bool(row.get("high_signal")),
                "has_coverage_value": bool(row.get("has_coverage_value")),
                "has_positive_evidence": bool(row.get("has_positive_evidence")),
                "has_coverage_signal": bool(row.get("has_coverage_signal")),
                "has_high_signal": bool(row.get("has_high_signal")),
                "has_competition_signal": bool(row.get("has_competition_signal")),
                "focus_score": int(row.get("focus_score") or 0),
                "evidence": {
                    "selected_case_ids": list(evidence.get("selected_case_ids") or [])[:3],
                    "selected_count_in_bucket": int(evidence.get("selected_count_in_bucket") or 0),
                    "coverage_gain_score": int(evidence.get("coverage_gain_score") or 0),
                    "missing_rule_hits_count": int(len(evidence.get("missing_rule_hits") or [])),
                    "core_rule_hits_count": int(len(evidence.get("core_rule_hits") or [])),
                    "unique_coverage_hits_count": int(len(evidence.get("unique_coverage_hits") or [])),
                    "similarity": float(evidence.get("similarity") or 0.0),
                    "duplicate_of_case_id": str(evidence.get("duplicate_of_case_id") or "").strip(),
                },
            }
        )
    return compact_rows


def _fit_table_diag_payload_size(
    payload: dict[str, Any],
    *,
    max_bytes: int = _MAX_GEN_DIAG_MESSAGE_BYTES,
) -> dict[str, Any]:
    fitted = dict(payload or {})
    rows = [item for item in (fitted.get("rows") or []) if isinstance(item, dict)]
    fitted["rows"] = rows
    fitted["row_count"] = int(len(rows))
    fitted.setdefault("row_count_total", int(len(rows)))

    def _payload_size_bytes(obj: dict[str, Any]) -> int:
        return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    if _payload_size_bytes(fitted) <= max_bytes:
        return fitted

    sampled = list(rows)
    while sampled:
        candidate = dict(fitted)
        candidate["rows"] = sampled
        candidate["row_count"] = int(len(sampled))
        candidate["row_count_total"] = int(len(rows))
        candidate["rows_scope"] = "sampled_due_to_size"
        if _payload_size_bytes(candidate) <= max_bytes:
            return candidate
        if len(sampled) <= 1:
            break
        sampled = sampled[: max(1, int(len(sampled) // 2))]

    fallback = dict(fitted)
    fallback["rows"] = []
    fallback["row_count"] = 0
    fallback["row_count_total"] = int(len(rows))
    fallback["rows_scope"] = "summary_only_due_to_size"
    return fallback


def _with_run_context(
    payload: dict[str, Any],
    *,
    request_id: str,
    project_id: int,
    multi_pass: bool,
    generation_mode: str,
) -> dict[str, Any]:
    enriched = dict(payload or {})
    if request_id:
        enriched["request_id"] = request_id
    enriched["project_id"] = int(project_id)
    enriched["multi_pass"] = bool(multi_pass)
    enriched["generation_mode"] = str(generation_mode or "")
    return enriched


def _build_pre_persistence_failure_diagnostics(
    *,
    build_quality_ledger_payload: Callable[..., dict[str, Any]],
    generation_id: int | None,
    request_id: str,
    project_id: int,
    mode: str,
    multi_pass: bool,
    expected_count: int,
    stage_counts: dict[str, Any],
    coverage_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    review_decision_table_payload: list[dict[str, Any]],
    judge_summary_payload: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]],
    feedback_control_debug_payload: dict[str, Any],
    compression_diag_payload: dict[str, Any],
    context_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Persist enough diagnostics to debug a run that is blocked before insertion."""
    diagnostics: list[dict[str, Any]] = []
    generation_id_int = int(generation_id or 0)

    def add(payload: dict[str, Any]) -> None:
        if not payload:
            return
        diagnostics.append(
            _with_run_context(
                payload,
                request_id=request_id,
                project_id=project_id,
                multi_pass=multi_pass,
                generation_mode=mode,
            )
        )

    if generation_summary_payload:
        add({"kind": "generation_summary", **generation_summary_payload})
    if convergence_payload:
        add(
            {
                "kind": "generation_convergence",
                **convergence_payload,
                "expected_count": int(expected_count or 0),
            }
        )
    if review_decision_summary_payload:
        add({"kind": "review_decision_summary", **review_decision_summary_payload})
    if feedback_control_debug_payload:
        add({"kind": "feedback_control_state", **feedback_control_debug_payload})
    if judge_summary_payload:
        add(
            {
                "kind": "judge_summary",
                **judge_summary_payload,
                "generation_id": generation_id_int,
            }
        )

    if judge_summary_payload or judge_decision_table_payload:
        normalized_rows = [
            _normalize_judge_row(
                item,
                generation_id=generation_id_int,
                request_id=request_id,
            )
            for item in judge_decision_table_payload
            if isinstance(item, dict)
        ]
        reject_pending_rows = [
            row
            for row in normalized_rows
            if str(row.get("judge_status") or "").upper() in {"REJECT", "PENDING"}
        ]
        rows_to_persist = reject_pending_rows or normalized_rows
        judge_table_diag = {
            "kind": "judge_decision_table",
            "generation_id": generation_id_int,
            "rows": rows_to_persist,
            "row_count": int(len(rows_to_persist)),
            "row_count_total": int(len(normalized_rows)),
            "row_count_reject_pending": int(len(reject_pending_rows)),
            "rows_scope": "reject_pending_only" if reject_pending_rows else "all_when_no_reject_pending",
            "row_evidence_incomplete": bool(
                int(judge_summary_payload.get("rejected_out_count") or 0)
                + int(judge_summary_payload.get("pending_out_count") or 0) > 0
                and len(reject_pending_rows) == 0
            ),
        }
        add(_fit_table_diag_payload_size(judge_table_diag))

    if review_decision_table_payload:
        review_table_diag = {
            "kind": "review_decision_table",
            "generation_id": generation_id_int,
            "rows": review_decision_table_payload,
            "row_count": int(len(review_decision_table_payload)),
        }
        add(_fit_table_diag_payload_size(review_table_diag))
        compact_rows = _normalize_review_compact_rows(
            review_decision_table_payload,
            generation_id=generation_id_int,
            request_id=request_id,
        )
        if compact_rows:
            compact_diag = {
                "kind": "review_decision_table_compact",
                "generation_id": generation_id_int,
                "rows": compact_rows,
                "row_count": int(len(compact_rows)),
            }
            add(_fit_table_diag_payload_size(compact_diag))

    quality_ledger_payload = build_quality_ledger_payload(
        generation_id=generation_id,
        request_id=request_id,
        mode=mode,
        stage_counts=stage_counts,
        coverage_payload=coverage_payload,
        convergence_payload=convergence_payload,
        generation_summary_payload=generation_summary_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        judge_summary_payload=judge_summary_payload,
        feedback_control_debug_payload=feedback_control_debug_payload,
        compression_diag_payload=compression_diag_payload,
        context_result=context_result,
        judge_decision_table_payload=judge_decision_table_payload,
    )
    add(quality_ledger_payload)
    case_quality_gate_payload = dict(quality_ledger_payload.get("case_quality_gate") or {})
    if case_quality_gate_payload:
        case_quality_gate_payload["generation_id"] = generation_id_int
        add(case_quality_gate_payload)
    if coverage_payload:
        add(dict(coverage_payload))
    return diagnostics
