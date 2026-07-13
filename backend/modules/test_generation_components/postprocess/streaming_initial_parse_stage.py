from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .result_postprocess_priority_semantics import apply_priority_semantics_to_cases
from .streaming_case_quality import filter_low_quality_cases_with_stats
from .streaming_postprocess_utils import (
    LowQualityFilterStatsAccumulator,
    _dict_case_count,
    _dict_case_items,
)


@dataclass(frozen=True)
class InitialParseStageResult:
    parsed_result: list[dict[str, Any]]
    low_quality_filter_stats: LowQualityFilterStatsAccumulator


def run_initial_parse_stage(
    *,
    full_content: str,
    requirement: str,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[Any]], list[dict[str, Any]]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    record_timing_event_fn: Callable[..., dict[str, Any]],
) -> InitialParseStageResult:
    started_at = time.perf_counter()
    parsed_result = clean_and_parse_json_fn(full_content)
    parsed_result = normalize_json_structure_fn(parsed_result)
    if not isinstance(parsed_result, list):
        parsed_result = []
    parsed_result = deduplicate_test_cases_fn(parsed_result)
    parsed_result = apply_priority_semantics_to_cases(
        _dict_case_items(parsed_result),
        attach_debug=False,
    )
    parsed_result, initial_filter_stats = filter_low_quality_cases_with_stats(
        parsed_result,
        requirement_text=requirement,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    low_quality_filter_stats = LowQualityFilterStatsAccumulator(initial_filter_stats)
    record_timing_event_fn(
        "postprocess_initial_parse_filter",
        started_at,
        primary_count=int(_dict_case_count(parsed_result)),
        input_chars=int(len(full_content or "")),
    )
    return InitialParseStageResult(
        parsed_result=parsed_result,
        low_quality_filter_stats=low_quality_filter_stats,
    )
