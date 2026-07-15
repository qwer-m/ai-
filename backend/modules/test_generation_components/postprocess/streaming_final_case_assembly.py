from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..coverage.coverage_case_complexity import case_complexity_profile
from .priority_anchor_rules import enforce_main_path_p0_anchors
from .streaming_case_keys import case_signature
from .streaming_case_quality import filter_final_quality_cases, strip_case_meta_list
from .streaming_case_source_metadata import (
    annotate_case_source_metadata,
    apply_case_source_metadata,
)
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

_DEMOTED_MODEL_P0_SOURCES = frozenset(
    {
        "model_p0_guard_downgrade",
        "main_path_anchor_demoted_non_blocking",
    }
)


@dataclass(frozen=True)
class FinalCaseAssemblyResult:
    cases: list[dict[str, Any]]
    execution_plan_summary: dict[str, Any]
    final_order_flow_governance_summary: dict[str, Any]
    final_case_structure: dict[str, Any]
    final_independent_case_structure: dict[str, Any]
    final_count: int
    post_review_dedup_drop: int
    final_quality_drop_total: int
    final_quality_drop_details: list[dict[str, Any]]
    append_duplicate_pruned_count: int


def _restore_demoted_model_p0_candidates(
    cases: list[dict[str, Any]],
    review_candidate_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = _dict_case_items(cases)
    existing_signatures = {case_signature(item) for item in output if case_signature(item)}
    for source_case in _dict_case_items(review_candidate_cases):
        signature = case_signature(source_case)
        if not signature or signature in existing_signatures:
            continue
        decision_source = str(source_case.get("priority_decision_source") or "").strip()
        model_priority = str(
            source_case.get("model_priority_current") or source_case.get("model_priority") or source_case.get("priority")
        ).strip().upper()
        final_priority = str(source_case.get("priority_final") or source_case.get("priority") or "").strip().upper()
        if decision_source not in _DEMOTED_MODEL_P0_SOURCES:
            continue
        if model_priority != "P0" or final_priority not in {"P1", "P2"}:
            continue
        restored = dict(source_case)
        restored["priority"] = final_priority
        restored["priority_final"] = final_priority
        restored["priority_decision_state"] = "overridden"
        restored["priority_decision_source"] = "review_model_p0_demotion_preserved"
        output.append(restored)
        existing_signatures.add(signature)
    return output


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
    append: bool = False,
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    govern_cases_by_flow_structure_fn: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
    analyze_case_structure_fn: Callable[..., dict[str, Any]],
) -> FinalCaseAssemblyResult:
    source_order_cases = annotate_case_source_metadata(
        review_candidate_cases,
        source_stage="review_candidate",
        set_candidate_index=True,
    )
    source_seed = [*source_order_cases, *_dict_case_items(parsed_result)]
    parsed_result = apply_case_source_metadata(
        parsed_result,
        source_cases=source_seed,
    )
    parsed_result = _restore_demoted_model_p0_candidates(
        _dict_case_items(parsed_result),
        _dict_case_items(review_candidate_cases),
    )
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
    cases = apply_case_source_metadata(
        cases,
        source_cases=source_seed,
    )
    append_duplicate_pruned_count = 0
    if append:
        try:
            pruned_cases, prune_summary = govern_cases_by_flow_structure_fn(
                requirement,
                _dict_case_items(cases),
                start_id=start_id,
                renumber_ids=True,
                max_per_scenario=2,
                project_profile=flow_project_profile,
            )
            pruned_case_items = _dict_case_items(pruned_cases)
            if pruned_case_items and len(pruned_case_items) < len(_dict_case_items(cases)):
                cases = pruned_case_items
                append_duplicate_pruned_count = int(
                    prune_summary.get("scenario_duplicate_pruned_count")
                    if isinstance(prune_summary, dict)
                    else len(_dict_case_items(cases)) - len(pruned_case_items)
                )
        except Exception:
            append_duplicate_pruned_count = 0
    final_quality_drop_details: list[dict[str, Any]] = []
    cases, final_quality_drop_total = filter_final_quality_cases(
        _dict_case_items(cases),
        final_quality_drop_details,
        stage="final_assembly_quality_filter",
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
        final_quality_drop_total=int(final_quality_drop_total or 0),
        final_quality_drop_details=final_quality_drop_details,
        append_duplicate_pruned_count=int(append_duplicate_pruned_count or 0),
    )


__all__ = [
    "FinalCaseAssemblyResult",
    "assemble_final_cases",
]
