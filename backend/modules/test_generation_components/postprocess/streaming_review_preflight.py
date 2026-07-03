from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .streaming_case_keys import case_signature
from .streaming_case_quality import filter_low_quality_cases_with_stats
from .streaming_postprocess_utils import _dict_case_items, _rule_diagnostics_payload
from .streaming_review_retry import build_review_llm_preflight_debug_fields
from .streaming_review_selection import (
    build_review_selection_constraints,
    enforce_review_selection_constraints,
    rank_review_case_for_fill,
    review_must_keep_reasons,
    split_review_candidate_pool,
)


@dataclass(frozen=True)
class ReviewPreflightState:
    candidate_cases: list[dict[str, Any]]
    review_filter_stats: dict[str, Any]
    candidate_count_before_review: int
    review_candidate_cases: list[dict[str, Any]]
    review_candidate_coverage_context: dict[str, Any]
    review_candidate_rule_diagnostics: dict[str, Any]
    must_keep_cases: list[dict[str, Any]]
    llm_pool_cases: list[dict[str, Any]]
    review_must_keep_signatures: set[str]
    review_must_keep_reason_map: dict[str, list[str]]
    review_llm_pool_count: int
    review_constraints: dict[str, Any]
    review_target_min_count: int
    review_target_max_count: int
    selected_from_llm_pool: list[dict[str, Any]]
    skip_review_llm_by_noop: bool
    runtime_debug_fields: dict[str, Any]


def prepare_review_preflight(
    *,
    requirement: str,
    parsed_result: list[dict[str, Any]],
    must_cover_rule_set: set[str],
    reference_count_effective: int,
    generation_coverage_profile: dict[str, Any],
    append_target_count: int,
    append_final_cap_count: int,
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    score_case_priority_fn: Callable[..., dict[str, Any]],
) -> ReviewPreflightState:
    candidate_cases = _dict_case_items(parsed_result)
    candidate_cases, review_filter_stats = filter_low_quality_cases_with_stats(
        candidate_cases,
        requirement_text=requirement,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    candidate_count_before_review = len(candidate_cases)
    review_candidate_cases = list(candidate_cases)

    review_candidate_coverage_context = analyze_coverage_fn(requirement, candidate_cases)
    review_candidate_rule_diagnostics = _rule_diagnostics_payload(review_candidate_coverage_context)
    review_candidate_pool_split = split_review_candidate_pool(
        candidate_cases,
        coverage_context=review_candidate_coverage_context,
        rule_diagnostics=review_candidate_rule_diagnostics,
        must_cover_rule_set=must_cover_rule_set,
        score_case_priority_fn=score_case_priority_fn,
        must_keep_reasons_fn=review_must_keep_reasons,
        signature_fn=case_signature,
    )
    must_keep_cases = review_candidate_pool_split.must_keep_cases
    llm_pool_cases = review_candidate_pool_split.llm_pool_cases
    review_must_keep_signatures = set(review_candidate_pool_split.must_keep_signatures)
    review_must_keep_reason_map = dict(review_candidate_pool_split.must_keep_reason_map)

    review_llm_pool_count = int(len(llm_pool_cases))
    review_constraints = build_review_selection_constraints(
        llm_pool_cases,
        reference_count=int(reference_count_effective or len(llm_pool_cases) or 1),
        generation_profile=generation_coverage_profile,
    )
    review_target_min_count = int(review_constraints.get("target_min_count") or 1)
    review_target_max_count = int(review_constraints.get("target_max_count") or review_target_min_count)
    selected_from_llm_pool: list[dict[str, Any]] = list(llm_pool_cases)

    noop_preflight_selected_count = 0
    noop_preflight_dropped_by_max = False
    noop_preflight_signature_unchanged = False
    noop_preflight_within_target_window = bool(
        int(len(llm_pool_cases)) <= int(review_target_max_count or 0)
    )
    append_cap_requires_selection = bool(
        int(append_final_cap_count or 0) > 0
        and int(len(llm_pool_cases)) > int(append_final_cap_count or 0)
    )
    if llm_pool_cases:
        preflight_selected_cases, preflight_constraint_reasons = enforce_review_selection_constraints(
            selected_cases=_dict_case_items(llm_pool_cases),
            pool_cases=_dict_case_items(llm_pool_cases),
            constraints=review_constraints,
            coverage_context=review_candidate_coverage_context,
            rule_diagnostics=review_candidate_rule_diagnostics,
            rank_case_fn=rank_review_case_for_fill,
        )
        original_pool_signatures = {
            case_signature(item)
            for item in _dict_case_items(llm_pool_cases)
            if case_signature(item)
        }
        preflight_signatures = {
            case_signature(item)
            for item in _dict_case_items(preflight_selected_cases)
            if case_signature(item)
        }
        noop_preflight_selected_count = int(len(preflight_selected_cases))
        noop_preflight_dropped_by_max = any(
            str(reason or "") == "dropped_by_target_max"
            for reason in dict(preflight_constraint_reasons or {}).values()
        )
        noop_preflight_signature_unchanged = bool(
            preflight_signatures == original_pool_signatures
            and int(len(preflight_selected_cases)) == int(len(_dict_case_items(llm_pool_cases)))
        )
    skip_review_llm_by_noop = bool(
        llm_pool_cases
        and noop_preflight_within_target_window
        and noop_preflight_signature_unchanged
        and not noop_preflight_dropped_by_max
        and not append_cap_requires_selection
    )
    runtime_debug_fields = build_review_llm_preflight_debug_fields(
        llm_pool_count=len(llm_pool_cases),
        append_target_count=append_target_count,
        append_final_cap_count=append_final_cap_count,
        skip_review_llm_by_noop=skip_review_llm_by_noop,
        noop_preflight_selected_count=noop_preflight_selected_count,
        noop_preflight_within_target_window=noop_preflight_within_target_window,
        noop_preflight_signature_unchanged=noop_preflight_signature_unchanged,
        noop_preflight_dropped_by_max=noop_preflight_dropped_by_max,
    )
    return ReviewPreflightState(
        candidate_cases=candidate_cases,
        review_filter_stats=review_filter_stats,
        candidate_count_before_review=candidate_count_before_review,
        review_candidate_cases=review_candidate_cases,
        review_candidate_coverage_context=review_candidate_coverage_context,
        review_candidate_rule_diagnostics=review_candidate_rule_diagnostics,
        must_keep_cases=must_keep_cases,
        llm_pool_cases=llm_pool_cases,
        review_must_keep_signatures=review_must_keep_signatures,
        review_must_keep_reason_map=review_must_keep_reason_map,
        review_llm_pool_count=review_llm_pool_count,
        review_constraints=review_constraints,
        review_target_min_count=review_target_min_count,
        review_target_max_count=review_target_max_count,
        selected_from_llm_pool=selected_from_llm_pool,
        skip_review_llm_by_noop=skip_review_llm_by_noop,
        runtime_debug_fields=runtime_debug_fields,
    )


__all__ = [
    "ReviewPreflightState",
    "prepare_review_preflight",
]
