from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .case_access import case_text_field
from .result_postprocess_priority_semantics import apply_priority_semantics_to_cases
from .streaming_case_keys import case_signature
from .streaming_case_quality import filter_low_quality_cases_with_stats
from .streaming_flow_conflicts import filter_cases_conflicting_with_confirmed_flow_facts
from .streaming_postprocess_utils import _dict_case_items, _json_for_prompt
from .streaming_postprocess_utils import _dict_case_count, _flow_profile_with_scenario_policy
from .streaming_priority_semantics import apply_coverage_priority_semantics
from .streaming_review_mapping import case_review_brief


@dataclass(frozen=True)
class FinalShortfallSupplementResult:
    cases: list[dict[str, Any]]
    flow_governance_summary: dict[str, Any]
    filter_stats: dict[str, Any]
    conflict_drop_count: int
    applied: bool
    supplement_count: int
    reason: str
    floor_recovery_applied: bool
    floor_recovered_count: int
    floor_recovery_reason: str


def should_attempt_final_shortfall_supplement(
    *,
    effective_generation_coverage_mode: str,
    expected_count_value: Any,
    final_target_floor_count: Any,
    append: bool,
    current_count: Any,
) -> bool:
    if not (
        effective_generation_coverage_mode == "full_functional_regression"
        or int(expected_count_value or 0) > 0
    ):
        return False
    if int(final_target_floor_count or 0) <= 0:
        return False
    if bool(append):
        return False
    return int(current_count or 0) < int(final_target_floor_count or 0)


def resolve_final_shortfall_supplement_size(
    *,
    current_count: Any,
    target_floor_count: Any,
    max_supplement_count: int = 30,
) -> dict[str, int]:
    shortfall = max(1, int(target_floor_count or 0) - int(current_count or 0))
    if shortfall <= 5:
        buffer = 3
    elif shortfall <= 20:
        buffer = 5
    else:
        buffer = max(5, int(round(float(shortfall) * 0.25)))

    return {
        "shortfall": int(shortfall),
        "buffer": int(buffer),
        "needed": min(int(max_supplement_count), int(shortfall + buffer)),
    }


def build_final_shortfall_supplement_prompt(
    *,
    requirement: Any,
    final_cases: Iterable[Any],
    current_count: Any,
    target_floor_count: Any,
    supplement_needed: Any,
    analyze_coverage_fn: Callable[[Any, list[dict[str, Any]]], dict[str, Any]],
) -> str:
    final_case_items = _dict_case_items(final_cases)
    existing_case_brief = [
        case_review_brief(item, id_key="id", require_id=False)
        for item in final_case_items[:140]
    ]
    supplement_coverage = analyze_coverage_fn(requirement, final_case_items)
    supplement_missing_rules = [
        str(item)
        for item in (supplement_coverage.get("missing_rules") or [])
        if str(item).strip()
    ][:30]
    supplement_missing_types_raw = supplement_coverage.get("missing_types")
    supplement_missing_types = {
        str(key): [str(item) for item in (value or []) if str(item).strip()][:20]
        for key, value in (
            dict(supplement_missing_types_raw or {}).items()
            if isinstance(supplement_missing_types_raw, dict)
            else []
        )
        if isinstance(value, list) and value
    }
    existing_module_counts: dict[str, int] = {}
    for item in final_case_items:
        module_key = case_text_field(item, "test_module") or "unknown"
        existing_module_counts[module_key] = int(existing_module_counts.get(module_key) or 0) + 1

    return f"""
FINAL_SHORTFALL_SUPPLEMENT:
- The current final set has {current_count} cases, below the final floor {int(target_floor_count or 0)}.
- Generate up to {int(supplement_needed or 0)} additional high-value, non-duplicate test cases.
- Focus only on the current requirement and the missing coverage evidence below.
- Prefer under-covered business modules, independent functional paths, boundaries, exceptions, and cross-module state synchronization.
- Do not add display-only, copy/toast-only, sorting-only, thumbnail-only, or popup-only cases unless they close a blocking business flow.
- Do not include legacy behavior that conflicts with confirmed current requirements.
- P0 only for blocking main-path closure; otherwise use P1/P2.
- Return ONLY a strict JSON array of test cases with fields: id, description, test_module, preconditions, steps, test_input, expected_result, priority.

MISSING_RULES:
{_json_for_prompt(supplement_missing_rules, limit=8000)}

MISSING_TYPES:
{_json_for_prompt(supplement_missing_types, limit=4000)}

EXISTING_MODULE_COUNTS:
{_json_for_prompt(existing_module_counts, limit=4000)}

EXISTING_FINAL_CASES_TO_AVOID_DUPLICATING:
{_json_for_prompt(existing_case_brief, limit=14000)}
"""


def run_final_shortfall_supplement(
    *,
    client: Any,
    db: Any,
    requirement: str,
    supplement_prompt: str,
    current_shortfall_count: int,
    parsed_result: list[dict[str, Any]],
    kb_context: str,
    fact_profile: dict[str, Any],
    flow_project_profile: dict[str, Any],
    effective_generation_coverage_mode: str,
    start_id: int,
    final_floor_recovered_count: int,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    govern_cases_by_flow_structure_fn: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
) -> FinalShortfallSupplementResult:
    result_cases = _dict_case_items(parsed_result)
    result_flow_summary: dict[str, Any] = {}
    filter_stats: dict[str, Any] = {}
    conflict_drop_count = 0

    supplement_raw = client.generate_response(
        requirement,
        supplement_prompt,
        db=db,
        task_type="generation",
    )
    supplement_parsed = clean_and_parse_json_fn(str(supplement_raw or ""))
    supplement_parsed = normalize_json_structure_fn(supplement_parsed)
    if not isinstance(supplement_parsed, list) or not supplement_parsed:
        return FinalShortfallSupplementResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            filter_stats=filter_stats,
            conflict_drop_count=conflict_drop_count,
            applied=False,
            supplement_count=0,
            reason="supplement_empty_response",
            floor_recovery_applied=False,
            floor_recovered_count=int(final_floor_recovered_count or 0),
            floor_recovery_reason="",
        )

    supplement_cases = deduplicate_test_cases_fn(_dict_case_items(supplement_parsed))
    supplement_cases = apply_priority_semantics_to_cases(
        _dict_case_items(supplement_cases),
        attach_debug=False,
    )
    supplement_cases, filter_stats = filter_low_quality_cases_with_stats(
        supplement_cases,
        requirement_text=requirement,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    supplement_cases, conflict_drop_count = filter_cases_conflicting_with_confirmed_flow_facts(
        _dict_case_items(supplement_cases),
        requirement=str(requirement or ""),
        kb_context=str(kb_context or ""),
        fact_profile=fact_profile,
    )
    existing_sigs = {case_signature(item) for item in result_cases if isinstance(item, dict)}
    unique_supplement: list[dict[str, Any]] = []
    for item in supplement_cases:
        sig = case_signature(item)
        if not sig or sig in existing_sigs:
            continue
        existing_sigs.add(sig)
        unique_supplement.append(dict(item))

    if not unique_supplement:
        return FinalShortfallSupplementResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            filter_stats=filter_stats,
            conflict_drop_count=int(conflict_drop_count or 0),
            applied=False,
            supplement_count=0,
            reason="supplement_empty_after_filter",
            floor_recovery_applied=False,
            floor_recovered_count=int(final_floor_recovered_count or 0),
            floor_recovery_reason="",
        )

    merged_shortfall = deduplicate_test_cases_fn([*result_cases, *unique_supplement])
    merged_shortfall = apply_coverage_priority_semantics(
        requirement,
        merged_shortfall,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    relaxed_flow_profile = _flow_profile_with_scenario_policy(
        flow_project_profile,
        coverage_mode=str(effective_generation_coverage_mode or ""),
        disable_scenario_pruning=True,
        intent_duplicate_cap=1,
        relaxed_for_floor_backfill=True,
    )
    supplemented_result, supplemented_summary = govern_cases_by_flow_structure_fn(
        requirement,
        _dict_case_items(merged_shortfall),
        start_id=start_id,
        renumber_ids=True,
        max_per_scenario=2,
        project_profile=relaxed_flow_profile,
    )
    if _dict_case_count(supplemented_result) <= current_shortfall_count:
        return FinalShortfallSupplementResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            filter_stats=filter_stats,
            conflict_drop_count=int(conflict_drop_count or 0),
            applied=False,
            supplement_count=0,
            reason="supplement_pruned_or_duplicate",
            floor_recovery_applied=False,
            floor_recovered_count=int(final_floor_recovered_count or 0),
            floor_recovery_reason="",
        )

    supplemented_summary["relaxed_for_floor_backfill"] = True
    supplement_count = max(0, _dict_case_count(supplemented_result) - current_shortfall_count)
    return FinalShortfallSupplementResult(
        cases=supplemented_result,
        flow_governance_summary=supplemented_summary,
        filter_stats=filter_stats,
        conflict_drop_count=int(conflict_drop_count or 0),
        applied=True,
        supplement_count=supplement_count,
        reason="",
        floor_recovery_applied=True,
        floor_recovered_count=max(
            int(final_floor_recovered_count or 0),
            int(supplement_count or 0),
        ),
        floor_recovery_reason="final_shortfall_supplement_generated",
    )


__all__ = [
    "FinalShortfallSupplementResult",
    "build_final_shortfall_supplement_prompt",
    "resolve_final_shortfall_supplement_size",
    "run_final_shortfall_supplement",
    "should_attempt_final_shortfall_supplement",
]
