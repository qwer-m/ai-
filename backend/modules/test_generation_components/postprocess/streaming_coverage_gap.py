from __future__ import annotations

from typing import Any, Callable


def resolve_coverage_gap_state(coverage_result: dict[str, Any]) -> dict[str, Any]:
    missing_rules = list(coverage_result.get("missing_rules") or [])
    diagnostics = [
        item
        for item in (coverage_result.get("rule_diagnostics") or [])
        if isinstance(item, dict)
    ]
    has_missing_types = any(bool(item.get("missing_types")) for item in diagnostics)
    return {
        "missing_rules": missing_rules,
        "has_missing_types": bool(has_missing_types),
        "gap_count": int(len(missing_rules) + (1 if has_missing_types else 0)),
    }


def diagnose_candidate_set_coverage_gain(
    *,
    requirement: str,
    base_cases: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    resolve_coverage_gap_state_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    valid_base_cases = [item for item in base_cases if isinstance(item, dict)]
    valid_candidate_cases = [item for item in candidate_cases if isinstance(item, dict)]
    before_coverage = analyze_coverage_fn(requirement, valid_base_cases)
    before_state = resolve_coverage_gap_state_fn(before_coverage)
    after_coverage = analyze_coverage_fn(
        requirement,
        [*valid_base_cases, *valid_candidate_cases],
    )
    after_state = resolve_coverage_gap_state_fn(after_coverage)
    gap_count_before = int(before_state.get("gap_count") or 0)
    gap_count_after = int(after_state.get("gap_count") or 0)
    return {
        "coverage_gain_candidate_count": int(len(valid_candidate_cases)),
        "coverage_gain_forwarded_count": int(len(valid_candidate_cases)),
        "coverage_gain_kept_count": int(len(valid_candidate_cases)),
        "coverage_gain_dropped_count": 0,
        "coverage_gain_gap_count_before": int(gap_count_before),
        "coverage_gain_remaining_gap_count": int(gap_count_after),
        "coverage_gain_gap_reduction": max(0, int(gap_count_before) - int(gap_count_after)),
        "coverage_gain_detected": bool(gap_count_after < gap_count_before),
    }


__all__ = [
    "diagnose_candidate_set_coverage_gain",
    "resolve_coverage_gap_state",
]
