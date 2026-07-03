from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .streaming_case_keys import (
    case_signature as _signature,
    dedupe_by_final_description as _dedupe_by_final_description,
)
from .streaming_case_quality import (
    filter_final_quality_cases as _filter_final_quality_cases,
    strip_case_meta_list as _strip_case_meta_list,
)
from .streaming_postprocess_utils import _dict_case_items, _rule_diagnostics_payload
from .streaming_priority_semantics import coverage_priority_semantics_result as _coverage_priority_semantics_result
from .streaming_review_selection import apply_append_target_cap as _apply_append_target_cap


@dataclass(frozen=True)
class FinalPruningResult:
    cases: list[dict[str, Any]]
    pre_priority_coverage: dict[str, Any]
    final_description_dedup_drop_signatures: set[str]
    append_cap_drop_signatures: set[str]
    append_cap_drop_total: int
    final_quality_drop_total: int


def apply_post_judge_final_pruning(
    *,
    requirement: str,
    parsed_result: list[dict[str, Any]],
    low_quality_drop_details: list[dict[str, Any]],
    append_final_cap_count: int,
    start_id: int,
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    rank_case_fn: Callable[..., tuple[int, ...]],
) -> FinalPruningResult:
    cases, final_quality_drop_total = _filter_final_quality_cases(
        parsed_result,
        low_quality_drop_details,
        stage="post_judge_quality_filter",
    )
    cases, pre_priority_coverage = _coverage_priority_semantics_result(
        requirement,
        cases,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    cases = _strip_case_meta_list(cases)
    cases, final_description_dedup_drop_signatures = _dedupe_by_final_description(
        _dict_case_items(cases)
    )
    if final_description_dedup_drop_signatures:
        cases = reorder_cases_by_closed_loop_fn(
            cases,
            start_id=start_id,
            renumber_ids=True,
        )
    cases, append_cap_drop_signatures, append_cap_drop_total = _apply_append_target_cap(
        requirement=requirement,
        parsed_cases=cases,
        append_final_cap_count=append_final_cap_count,
        analyze_coverage_fn=analyze_coverage_fn,
        rule_diagnostics_fn=_rule_diagnostics_payload,
        rank_case_fn=rank_case_fn,
        signature_fn=_signature,
    )
    return FinalPruningResult(
        cases=_dict_case_items(cases),
        pre_priority_coverage=dict(pre_priority_coverage or {}),
        final_description_dedup_drop_signatures=set(final_description_dedup_drop_signatures),
        append_cap_drop_signatures=set(append_cap_drop_signatures),
        append_cap_drop_total=int(append_cap_drop_total or 0),
        final_quality_drop_total=int(final_quality_drop_total or 0),
    )
