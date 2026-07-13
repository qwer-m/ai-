from __future__ import annotations

from typing import Any, Callable, Iterable

from .result_postprocess_priority_semantics import apply_priority_semantics_to_cases
from .streaming_postprocess_utils import _dict_case_items, _rule_diagnostics_payload
from .streaming_uncertain_requirement import enforce_uncertain_priority_floor


def coverage_priority_semantics_result(
    requirement: str,
    cases: Iterable[Any],
    *,
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    case_items = _dict_case_items(cases)
    coverage_context = analyze_coverage_fn(requirement, case_items)
    prioritized = apply_priority_semantics_to_cases(
        case_items,
        attach_debug=False,
        coverage_context=coverage_context,
        rule_diagnostics=_rule_diagnostics_payload(coverage_context),
    )
    return enforce_uncertain_priority_floor(_dict_case_items(prioritized)), coverage_context


def apply_coverage_priority_semantics(
    requirement: str,
    cases: Iterable[Any],
    *,
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
) -> list[dict[str, Any]]:
    prioritized, _coverage_context = coverage_priority_semantics_result(
        requirement,
        cases,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    return prioritized
