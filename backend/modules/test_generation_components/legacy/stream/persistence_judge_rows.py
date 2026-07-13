from __future__ import annotations

from typing import Any

from .persistence_diagnostics import _judge_status_key


def judge_status_key(row: dict[str, Any]) -> str:
    return _judge_status_key(row)


def cluster_judge_reject_reasons(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    clusters: dict[str, int] = {}
    rejected_total = 0
    for row in rows or []:
        if not isinstance(row, dict) or judge_status_key(row) != "REJECT":
            continue
        rejected_total += 1
        reason = str(row.get("reject_reason") or "").strip().lower()
        signals = row.get("signals") if isinstance(row.get("signals"), dict) else {}
        if reason.startswith("semantic_duplicate") or bool(signals.get("is_semantic_duplicate")):
            key = "semantic_duplicate"
        elif bool(signals.get("violates_confirmed_fact")) or "fact" in reason or "事实" in reason:
            key = "fact_conflict"
        elif "role" in reason or "角色" in reason or "session" in reason:
            key = "role_mismatch"
        elif "precondition" in reason or "前置" in reason:
            key = "invalid_precondition"
        elif "assert" in reason or "断言" in reason or "non_assertable" in reason:
            key = "non_assertable"
        elif "duplicate" in reason or "重复" in reason:
            key = "duplicate_other"
        elif reason:
            key = reason.split(":", 1)[0][:80]
        else:
            key = "unspecified"
        clusters[key] = int(clusters.get(key) or 0) + 1
    ordered = dict(sorted(clusters.items(), key=lambda item: (-int(item[1]), item[0])))
    return {
        "rejected_total": int(rejected_total),
        "reason_clusters": ordered,
        "dominant_reason": next(iter(ordered), ""),
    }


__all__ = [
    "cluster_judge_reject_reasons",
    "judge_status_key",
]
