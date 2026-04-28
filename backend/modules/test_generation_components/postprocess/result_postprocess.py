from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterator

from .result_postprocess_priority_semantics import (
    apply_priority_semantics_to_case,
    apply_priority_semantics_to_cases,
    resolve_case_priority_decision,
    resolve_case_priority,
    score_case_priority,
)

def strip_case_meta_fields(result: Any) -> Any:
    """Remove debug/meta payload from case outputs."""
    if not isinstance(result, list):
        return result
    cleaned: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        case = dict(item)
        case.pop("meta", None)
        case.pop("displayPriority", None)
        case.pop("rawPriority", None)
        case.pop("finalPriority", None)
        cleaned.append(case)
    return cleaned

def prepare_append_existing_cases(
    existing_generated_result: str | None,
    *,
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    count_unique_test_cases_fn: Callable[[list[dict[str, Any]]], int],
) -> tuple[list[dict[str, Any]], int, int]:
    """Load and normalize historical append cases before generation."""
    existing_cases: list[dict[str, Any]] = []
    existing_unique_count = 0
    start_id = 1

    if not existing_generated_result:
        return existing_cases, existing_unique_count, start_id

    try:
        parsed = json.loads(existing_generated_result)
        if isinstance(parsed, list):
            parsed = normalize_json_structure_fn(parsed)
            if not isinstance(parsed, list):
                parsed = []
            parsed = deduplicate_test_cases_fn(parsed)
            existing_cases = parsed
            existing_unique_count = count_unique_test_cases_fn(existing_cases)
            start_id = existing_unique_count + 1
    except Exception:
        pass

    return existing_cases, existing_unique_count, start_id


def finalize_generated_cases(
    generated_result: Any,
    *,
    start_id: int,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
) -> Any:
    """Parse, normalize, deduplicate, and reorder generated cases."""
    if isinstance(generated_result, (list, dict)):
        result: Any = generated_result
    else:
        result = clean_and_parse_json_fn(str(generated_result))

    if isinstance(result, list):
        result = normalize_json_structure_fn(result)
        result = deduplicate_test_cases_fn(result)
        result = reorder_cases_by_closed_loop_fn(
            result,
            start_id=start_id,
            renumber_ids=True,
        )
        result = strip_case_meta_fields(result)
    return result


def merge_cases_for_append(
    existing_cases: list[dict[str, Any]],
    new_cases: Any,
    *,
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
) -> Any:
    """Merge append-mode historical cases with new cases before persistence."""
    if not isinstance(new_cases, list):
        return new_cases

    merged_result: list[dict[str, Any]] = []
    if isinstance(existing_cases, list):
        merged_result.extend(existing_cases)
    merged_result.extend(new_cases)
    merged_result = deduplicate_test_cases_fn(merged_result)
    merged_result = reorder_cases_by_closed_loop_fn(
        merged_result,
        start_id=1,
        renumber_ids=True,
    )
    merged_result = strip_case_meta_fields(merged_result)
    return merged_result

from .result_postprocess_streaming import stream_postprocess_cases

