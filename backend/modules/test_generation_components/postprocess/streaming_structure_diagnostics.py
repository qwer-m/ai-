from __future__ import annotations

from typing import Any, Callable


AnalyzeCaseStructureFn = Callable[..., dict[str, Any]]
CaseExecutionGroupFn = Callable[[dict[str, Any]], str]
DictCaseItemsFn = Callable[[Any], list[dict[str, Any]]]


def resolve_final_case_structures(
    *,
    requirement: str,
    parsed_result: list[Any],
    project_profile: dict[str, Any],
    analyze_case_structure_fn: AnalyzeCaseStructureFn,
    dict_case_items_fn: DictCaseItemsFn,
    case_execution_group_fn: CaseExecutionGroupFn,
) -> dict[str, dict[str, Any]]:
    try:
        final_case_structure = analyze_case_structure_fn(
            requirement,
            dict_case_items_fn(parsed_result),
            project_profile=project_profile,
        )
        final_independent_case_structure = analyze_case_structure_fn(
            requirement,
            [
                item
                for item in parsed_result
                if isinstance(item, dict) and case_execution_group_fn(item) != "main_smoke"
            ],
            project_profile=project_profile,
        )
    except Exception:
        final_case_structure = {}
        final_independent_case_structure = {}

    return {
        "final_case_structure": final_case_structure,
        "final_independent_case_structure": final_independent_case_structure,
    }


def resolve_review_case_structure(
    *,
    requirement: str,
    review_candidate_cases: list[Any],
    project_profile: dict[str, Any],
    analyze_case_structure_fn: AnalyzeCaseStructureFn,
    dict_case_items_fn: DictCaseItemsFn,
) -> dict[str, Any]:
    try:
        return analyze_case_structure_fn(
            requirement,
            dict_case_items_fn(review_candidate_cases),
            project_profile=project_profile,
        )
    except Exception:
        return {}


__all__ = [
    "resolve_final_case_structures",
    "resolve_review_case_structure",
]
