from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..coverage.coverage_case_complexity import case_complexity_profile
from .priority_anchor_rules import enforce_main_path_p0_anchors
from .streaming_case_keys import case_signature
from .streaming_case_quality import strip_case_meta_list
from .streaming_execution_plan_metadata import apply_execution_plan_metadata
from .streaming_execution_plan_ordering import apply_final_independent_case_ordering
from .streaming_postprocess_utils import (
    _case_execution_group,
    _clip_text,
    _dict_case_count,
    _dict_case_items,
    _flow_profile_with_scenario_policy,
)
from .streaming_priority_rebuild import preserve_review_priority_demotions
from .streaming_structure_diagnostics import resolve_final_case_structures


@dataclass(frozen=True)
class FinalCaseAssemblyResult:
    cases: list[dict[str, Any]]
    execution_plan_summary: dict[str, Any]
    final_order_flow_governance_summary: dict[str, Any]
    final_case_structure: dict[str, Any]
    final_independent_case_structure: dict[str, Any]
    final_count: int
    post_review_dedup_drop: int


def assemble_final_cases(
    *,
    parsed_result: list[dict[str, Any]],
    requirement: str,
    start_id: int,
    effective_generation_coverage_mode: str,
    generation_coverage_mode: str,
    review_candidate_cases: list[dict[str, Any]],
    review_selected_count: int,
    workflow_blueprints: list[dict[str, Any]],
    trusted_workflow_contracts: list[dict[str, Any]],
    current_requirement_workflow_blueprints: list[dict[str, Any]],
    authoritative_workflow_blueprints: list[dict[str, Any]],
    flow_project_profile: dict[str, Any],
    project_profile: dict[str, Any],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    govern_cases_by_flow_structure_fn: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
    analyze_case_structure_fn: Callable[..., dict[str, Any]],
) -> FinalCaseAssemblyResult:
    cases = reorder_cases_by_closed_loop_fn(
        _dict_case_items(parsed_result),
        start_id=start_id,
        renumber_ids=True,
    )
    cases = enforce_main_path_p0_anchors(
        cases,
        coverage_mode=str(effective_generation_coverage_mode or generation_coverage_mode or ""),
        requirement_text=str(requirement or ""),
        case_signature_fn=case_signature,
        case_complexity_profile_fn=case_complexity_profile,
    )
    cases = preserve_review_priority_demotions(
        cases,
        review_candidate_cases,
        case_signature_fn=case_signature,
    )
    cases = reorder_cases_by_closed_loop_fn(
        _dict_case_items(cases),
        start_id=start_id,
        renumber_ids=True,
    )
    cases, execution_plan_summary = apply_execution_plan_metadata(
        _dict_case_items(cases),
        start_id=start_id,
        coverage_mode=str(effective_generation_coverage_mode or generation_coverage_mode or ""),
        workflow_blueprints=workflow_blueprints,
        trusted_workflow_contracts=trusted_workflow_contracts,
        current_requirement_workflow_blueprints=current_requirement_workflow_blueprints,
        authoritative_workflow_blueprints=authoritative_workflow_blueprints,
    )
    cases, final_order_flow_governance_summary = apply_final_independent_case_ordering(
        cases,
        requirement=str(requirement or ""),
        start_id=start_id,
        flow_project_profile=flow_project_profile,
        flow_profile_with_scenario_policy_fn=_flow_profile_with_scenario_policy,
        govern_cases_by_flow_structure_fn=govern_cases_by_flow_structure_fn,
        case_execution_group_fn=_case_execution_group,
        clip_text_fn=_clip_text,
    )
    final_structure_state = resolve_final_case_structures(
        requirement=requirement,
        parsed_result=cases,
        project_profile=project_profile,
        analyze_case_structure_fn=analyze_case_structure_fn,
        dict_case_items_fn=_dict_case_items,
        case_execution_group_fn=_case_execution_group,
    )
    cases = strip_case_meta_list(_dict_case_items(cases))
    final_count = _dict_case_count(cases)
    return FinalCaseAssemblyResult(
        cases=cases,
        execution_plan_summary=dict(execution_plan_summary or {}),
        final_order_flow_governance_summary=dict(final_order_flow_governance_summary or {}),
        final_case_structure=dict(final_structure_state.get("final_case_structure") or {}),
        final_independent_case_structure=dict(final_structure_state.get("final_independent_case_structure") or {}),
        final_count=int(final_count or 0),
        post_review_dedup_drop=max(0, int(review_selected_count or 0) - int(final_count or 0)),
    )


__all__ = [
    "FinalCaseAssemblyResult",
    "assemble_final_cases",
]
