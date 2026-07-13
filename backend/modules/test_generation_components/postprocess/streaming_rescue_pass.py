from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .result_postprocess_priority_semantics import apply_priority_semantics_to_cases
from .streaming_case_quality import filter_low_quality_cases_with_stats
from .streaming_postprocess_utils import _dict_case_items, _merged_unique_total


@dataclass(frozen=True)
class InitialRescuePassResult:
    cases: list[dict[str, Any]]
    current_total: int
    filter_stats: dict[str, Any]


def build_initial_rescue_prompt(base_prompt: str) -> str:
    return f"""
{base_prompt}

RESCUE INSTRUCTION:
- Quantity is reference-only; prioritize quality and coverage gain.
- Stop when additional cases add no new information.
- Return ONLY strict JSON array.
"""


def run_initial_rescue_pass(
    *,
    client: Any,
    requirement: str,
    base_prompt: str,
    db: Any,
    append: bool,
    existing_cases: list[dict[str, Any]],
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    count_unique_test_cases_fn: Callable[[list[dict[str, Any]]], int],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
) -> InitialRescuePassResult | None:
    rescue_raw = client.generate_response(
        requirement,
        build_initial_rescue_prompt(base_prompt),
        db=db,
        task_type="generation",
    )
    rescue_parsed = clean_and_parse_json_fn(str(rescue_raw or ""))
    rescue_parsed = normalize_json_structure_fn(rescue_parsed)
    if not isinstance(rescue_parsed, list) or not rescue_parsed:
        return None

    rescue_cases = deduplicate_test_cases_fn(rescue_parsed)
    rescue_cases = apply_priority_semantics_to_cases(
        _dict_case_items(rescue_cases),
        attach_debug=False,
    )
    rescue_cases, rescue_filter_stats = filter_low_quality_cases_with_stats(
        rescue_cases,
        requirement_text=requirement,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    current_total = _merged_unique_total(
        rescue_cases,
        append=append,
        existing_cases=existing_cases,
        count_unique_test_cases_fn=count_unique_test_cases_fn,
    )
    return InitialRescuePassResult(
        cases=rescue_cases,
        current_total=current_total,
        filter_stats=rescue_filter_stats,
    )


__all__ = [
    "InitialRescuePassResult",
    "build_initial_rescue_prompt",
    "run_initial_rescue_pass",
]
