from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .streaming_case_keys import (
    candidate_identity_key as _candidate_identity_key,
    case_signature as _signature,
)
from .streaming_execution_plan_metadata import (
    evaluate_required_stage_candidate_coverage as _evaluate_required_stage_candidate_coverage,
)
from .streaming_case_quality import (
    diagnose_final_quality_cases as _diagnose_final_quality_cases,
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
    final_quality_diagnostic_total: int
    final_quality_drop_total: int
    append_cap_diagnostics: dict[str, Any]


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
    workflow_blueprints: list[dict[str, Any]] | None = None,
) -> FinalPruningResult:
    cases, final_quality_diagnostic_total = _diagnose_final_quality_cases(
        parsed_result,
        low_quality_drop_details,
        stage="post_judge_quality_diagnostic",
    )
    cases, pre_priority_coverage = _coverage_priority_semantics_result(
        requirement,
        cases,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    cases = _strip_case_meta_list(cases)
    final_description_dedup_drop_signatures: set[str] = set()
    pre_cap_stage_coverage = _evaluate_required_stage_candidate_coverage(
        _dict_case_items(cases),
        workflow_blueprints=workflow_blueprints,
    )
    protected_candidate_keys = {
        str(item.get("candidate_key") or "")
        for item in (pre_cap_stage_coverage.get("selected_required_candidates") or [])
        if pre_cap_stage_coverage.get("required_stage_coverage_complete") is True
        and str(item.get("candidate_key") or "")
    }
    append_cap_diagnostics: dict[str, Any] = {
        "pre_cap_required_stage_coverage": pre_cap_stage_coverage,
    }
    cases, append_cap_drop_signatures, append_cap_drop_total = _apply_append_target_cap(
        requirement=requirement,
        parsed_cases=cases,
        append_final_cap_count=append_final_cap_count,
        analyze_coverage_fn=analyze_coverage_fn,
        rule_diagnostics_fn=_rule_diagnostics_payload,
        rank_case_fn=rank_case_fn,
        signature_fn=_signature,
        protected_candidate_keys=protected_candidate_keys,
        candidate_key_fn=_candidate_identity_key,
        diagnostics_out=append_cap_diagnostics,
    )
    append_cap_diagnostics["post_cap_required_stage_coverage"] = (
        _evaluate_required_stage_candidate_coverage(
            _dict_case_items(cases),
            workflow_blueprints=workflow_blueprints,
        )
    )
    return FinalPruningResult(
        cases=_dict_case_items(cases),
        pre_priority_coverage=dict(pre_priority_coverage or {}),
        final_description_dedup_drop_signatures=set(final_description_dedup_drop_signatures),
        append_cap_drop_signatures=set(append_cap_drop_signatures),
        append_cap_drop_total=int(append_cap_drop_total or 0),
        final_quality_diagnostic_total=int(final_quality_diagnostic_total or 0),
        final_quality_drop_total=0,
        append_cap_diagnostics=dict(append_cap_diagnostics),
    )
