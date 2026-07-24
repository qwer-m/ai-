from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .streaming_case_quality import filter_low_quality_cases_with_stats
from .streaming_postprocess_utils import _dict_case_items, _rule_diagnostics_payload
from .streaming_review_retry import build_review_llm_preflight_debug_fields
from .streaming_review_selection import build_review_selection_constraints


@dataclass(frozen=True)
class ReviewPreflightState:
    candidate_cases: list[dict[str, Any]]
    review_filter_stats: dict[str, Any]
    candidate_count_before_review: int
    review_candidate_cases: list[dict[str, Any]]
    review_candidate_coverage_context: dict[str, Any]
    review_candidate_rule_diagnostics: dict[str, Any]
    llm_pool_cases: list[dict[str, Any]]
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
    reference_count_effective: int,
    generation_coverage_profile: dict[str, Any],
    append_target_count: int,
    append_final_cap_count: int,
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
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
    # 全量有效结构候选必须在同一次 Review 中竞争，前置阶段不锁定候选。
    llm_pool_cases = list(candidate_cases)

    review_llm_pool_count = int(len(llm_pool_cases))
    review_constraints = build_review_selection_constraints(
        llm_pool_cases,
        reference_count=int(reference_count_effective or len(llm_pool_cases) or 1),
        generation_profile=generation_coverage_profile,
    )
    review_target_min_count = int(review_constraints.get("target_min_count") or 1)
    review_target_max_count = int(review_constraints.get("target_max_count") or review_target_min_count)
    selected_from_llm_pool: list[dict[str, Any]] = list(llm_pool_cases)

    noop_preflight_selected_count = int(len(llm_pool_cases))
    noop_preflight_dropped_by_max = False
    noop_preflight_signature_unchanged = bool(llm_pool_cases)
    noop_preflight_within_target_window = bool(
        int(len(llm_pool_cases)) <= int(review_target_max_count or 0)
    )
    skip_review_llm_by_noop = False
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
        llm_pool_cases=llm_pool_cases,
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
