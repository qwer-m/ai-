from __future__ import annotations

from typing import Any, Callable

from .streaming_case_keys import review_case_id
from .streaming_postprocess_utils import _fact_profile_debug_fields


def _status_text(value: Any) -> str:
    return str(getattr(value, "value", value))


def build_judge_summary_payload(
    *,
    repaired: Any,
    confirmed_pass_cases: list[Any],
    repaired_pass_cases: list[Any],
    rejected_cases: list[Any],
    pending_cases: list[Any],
    fact_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_repairable_count = int(repaired.repairable_count or 0)
    repaired_pass_out_count = int(len(repaired_pass_cases))
    unrepaired_repairable_count = max(0, raw_repairable_count - repaired_pass_out_count)
    fact_violation_count = int(
        sum(
            1
            for item in (repaired.cases or [])
            if _status_text(getattr(item, "status", "")).upper() == "REJECT"
            and bool(getattr(getattr(item, "signals", None), "violates_confirmed_fact", False))
        )
    )
    return {
        "pass_count": int(repaired.pass_count or 0),
        "repairable_count": raw_repairable_count,
        "raw_repairable_count": raw_repairable_count,
        "remaining_repairable_count": 0,
        "unrepaired_repairable_count": int(unrepaired_repairable_count),
        "reject_count": int(repaired.reject_count or 0),
        "pending_count": int(repaired.pending_count or 0),
        "repaired_case_count": int(repaired.repaired_case_count or 0),
        "appended_case_count": int(repaired.appended_case_count or 0),
        "confirmed_pass_out_count": int(len(confirmed_pass_cases)),
        "repaired_pass_out_count": repaired_pass_out_count,
        "rejected_out_count": int(len(rejected_cases)),
        "pending_out_count": int(len(pending_cases)),
        "fact_violation_count": fact_violation_count,
        "core_flow_covered": bool(repaired.core_flow_covered),
        "reuse_risk_covered": bool(repaired.reuse_risk_covered),
        **_fact_profile_debug_fields(fact_profile),
    }


def build_judge_decision_table_payload(
    *,
    repaired: Any,
    review_case_id_fn: Callable[[dict[str, Any]], str] = review_case_id,
) -> list[dict[str, Any]]:
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
            "is_semantic_duplicate": bool(getattr(signal_set, "is_semantic_duplicate", False)),
            "duplicate_of_case_id": str(getattr(signal_set, "duplicate_of_case_id", "") or ""),
            "duplicate_similarity": float(getattr(signal_set, "duplicate_similarity", 0.0) or 0.0),
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
                "status": _status_text(judged_item.status),
                "reject_reason": str(judged_item.reject_reason or ""),
                "pending_reason": str(judged_item.pending_reason or ""),
                "repaired": bool(judged_item.repaired),
                "repaired_pass": bool(judged_item.repaired_pass),
                "has_before_case": bool(before_case),
                "has_after_case": bool(after_case),
                "before_case_id": review_case_id_fn(before_case),
                "after_case_id": review_case_id_fn(after_case),
                "signals": signals_payload,
                "violates_confirmed_fact": bool(signals_payload.get("violates_confirmed_fact")),
                "contains_pending_logic": bool(signals_payload.get("contains_pending_logic")),
                "missing_core_flow": bool(signals_payload.get("missing_core_flow")),
                "missing_reuse_risk": bool(signals_payload.get("missing_reuse_risk")),
                "confirmed_fact_hits": list(signals_payload.get("confirmed_fact_hits") or []),
                "confirmed_fact_violations": list(signals_payload.get("confirmed_fact_violations") or []),
                "pending_hits": list(signals_payload.get("pending_hits") or []),
                "vague_or_unconfirmed_hits": list(signals_payload.get("vague_or_unconfirmed_hits") or []),
                "reuse_risk_hits": list(signals_payload.get("reuse_risk_hits") or []),
                "missing_reuse_risk_items": [
                    str(item) for item in (signal_set.missing_reuse_risk_items or [])
                ],
                "is_semantic_duplicate": bool(signals_payload.get("is_semantic_duplicate")),
                "duplicate_of_case_id": str(signals_payload.get("duplicate_of_case_id") or ""),
                "duplicate_similarity": signals_payload.get("duplicate_similarity") or 0,
                "before_case_snapshot": dict(before_case),
                "after_case_snapshot": dict(after_case),
                "notes": [str(item) for item in (signal_set.notes or [])],
            }
        )
    return rows
