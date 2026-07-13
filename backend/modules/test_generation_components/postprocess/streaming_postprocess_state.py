from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .streaming_generation_summary import resolve_append_reference_counts
from .streaming_postprocess_utils import (
    _dict_case_count,
    resolve_generation_coverage_state,
)


@dataclass(frozen=True)
class StreamPostprocessInitialState:
    stage_counts: dict[str, int]
    gap_attempts: int
    gap_remaining_after_attempts: int
    gap_stopped_by_provider_error: bool
    candidate_count_before_review: int
    append_target_count: int
    reference_count_effective: int
    append_final_cap_count: int
    expected_count_value: int
    effective_generation_coverage_mode: str
    effective_generation_coverage_mode_source: str
    explicit_generation_mode_override: bool
    generation_coverage_mode: str
    explicit_expected_count_floor_preserved: bool
    resolved_full_regression_floor: int


def init_stream_postprocess_state(
    *,
    parsed_result: Any,
    append: bool,
    expected_count: int,
    existing_unique_count: int,
    generation_mode: str,
    generation_coverage_mode: str,
    generation_target_case_range: dict[str, Any],
) -> StreamPostprocessInitialState:
    stage_counts = {
        "primary": len(parsed_result) if isinstance(parsed_result, list) else 0,
        "gap": 0,
        "review": 0,
    }
    append_reference_counts = resolve_append_reference_counts(
        append=append,
        expected_count=expected_count,
        existing_unique_count=existing_unique_count,
    )
    coverage_state = resolve_generation_coverage_state(
        expected_count=expected_count,
        generation_mode=generation_mode,
        generation_coverage_mode=generation_coverage_mode,
        generation_target_case_range=generation_target_case_range,
    )
    return StreamPostprocessInitialState(
        stage_counts=stage_counts,
        gap_attempts=0,
        gap_remaining_after_attempts=0,
        gap_stopped_by_provider_error=False,
        candidate_count_before_review=_dict_case_count(parsed_result),
        append_target_count=int(append_reference_counts["append_target_count"]),
        reference_count_effective=int(append_reference_counts["reference_count_effective"]),
        append_final_cap_count=int(append_reference_counts["append_final_cap_count"]),
        expected_count_value=int(coverage_state["expected_count_value"]),
        effective_generation_coverage_mode=str(coverage_state["effective_generation_coverage_mode"]),
        effective_generation_coverage_mode_source=str(
            coverage_state["effective_generation_coverage_mode_source"]
        ),
        explicit_generation_mode_override=bool(coverage_state["explicit_generation_mode_override"]),
        generation_coverage_mode=str(coverage_state["generation_coverage_mode"]),
        explicit_expected_count_floor_preserved=bool(
            coverage_state["explicit_expected_count_floor_preserved"]
        ),
        resolved_full_regression_floor=int(coverage_state["resolved_full_regression_floor"]),
    )
