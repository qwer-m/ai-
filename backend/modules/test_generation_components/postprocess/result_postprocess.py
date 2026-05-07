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

def normalize_final_case_priorities(result: Any, *, requirement_text: str = "") -> Any:
    """Re-apply priority semantics to public final cases before persistence."""
    if not isinstance(result, list):
        return result
    cases = [dict(item) for item in result if isinstance(item, dict)]
    if not cases:
        return []
    try:
        from ..coverage.coverage_analyzer import analyze_coverage
    except Exception:
        from modules.testing.test_generation_components.coverage.coverage_analyzer import analyze_coverage

    coverage_context = analyze_coverage(str(requirement_text or ""), cases)
    return apply_priority_semantics_to_cases(
        cases,
        attach_debug=False,
        coverage_context=coverage_context,
        rule_diagnostics={"rule_diagnostics": coverage_context.get("rule_diagnostics") or []},
    )


def strip_case_meta_fields(result: Any) -> Any:
    """Remove debug/meta payload from case outputs."""
    if not isinstance(result, list):
        return result
    debug_fields = {
        "meta",
        "displayPriority",
        "rawPriority",
        "finalPriority",
        "model_priority_current",
        "model_priority",
        "legacy_priority",
        "priority_final",
        "priority_decision_state",
        "priority_decision_source",
        "priority_confidence",
        "priority_conflict_reason",
        "priority_resolution_reason",
        "priority_score",
        "suggested_priority",
        "priority_reasons",
    }
    cleaned: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        case = dict(item)
        final_priority = str(case.get("priority_final") or "").strip().upper()
        if final_priority in {"P0", "P1", "P2"}:
            case["priority"] = final_priority
        for field in debug_fields:
            case.pop(field, None)
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

