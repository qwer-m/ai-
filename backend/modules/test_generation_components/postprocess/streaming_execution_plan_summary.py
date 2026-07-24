from __future__ import annotations

from typing import Any

from .execution_plan_semantic_alignment import analyze_main_smoke_semantic_alignment
from .streaming_execution_plan_helpers import evaluate_declared_workflow_closure
from .streaming_execution_plan_ordering import build_execution_orchestration_plan


MAIN_SMOKE_GROUP = "main_smoke"


def _execution_group(item: dict[str, Any]) -> str:
    return str(item.get("execution_group") or "")


def _fixture_key(item: dict[str, Any]) -> str:
    return str(item.get("fixture_key") or "")


def execution_plan_counts(annotated: list[dict[str, Any]]) -> dict[str, Any]:
    cases = list(annotated)
    main_chain_count = sum(1 for item in cases if _execution_group(item) == MAIN_SMOKE_GROUP)
    independent_count = max(0, len(cases) - main_chain_count)
    isolation_count = sum(1 for item in cases if bool(item.get("isolation_required")))
    broken_dependency_count = sum(
        1
        for item in cases
        if _execution_group(item) == MAIN_SMOKE_GROUP
        and int(item.get("main_chain_step") or 0) > 1
        and not item.get("depends_on")
    )

    role_switch_count = 0
    previous_role = ""
    for role in [str(item.get("role") or "") for item in cases if _execution_group(item) == MAIN_SMOKE_GROUP]:
        if previous_role and role != previous_role:
            role_switch_count += 1
        previous_role = role

    groups = [_execution_group(item) for item in cases]
    execution_group_breakdown = {
        group: sum(1 for current_group in groups if current_group == group)
        for group in sorted(set(groups))
    }

    return {
        "role_switch_count": int(role_switch_count),
        "main_chain_count": int(main_chain_count),
        "independent_count": int(independent_count),
        "isolation_count": int(isolation_count),
        "broken_dependency_count": int(broken_dependency_count),
        "execution_group_breakdown": execution_group_breakdown,
        "fixture_keys": sorted({_fixture_key(item) for item in cases if _fixture_key(item)}),
    }


def build_execution_plan_metadata_summary(
    annotated: list[dict[str, Any]] | None = None,
    *,
    coverage_mode: str = "",
    workflow_blueprints: list[dict[str, Any]] | None = None,
    trusted_workflow_contracts: list[dict[str, Any]] | None = None,
    current_requirement_workflow_blueprints: list[dict[str, Any]] | None = None,
    plan_workflow_blueprints: list[dict[str, Any]] | None = None,
    workflow_blueprint_source: str = "",
    main_chain_stage_kinds: list[str] | None = None,
    main_chain_incomplete_reason: str = "",
    main_chain_excluded_candidates: list[dict[str, Any]] | None = None,
    state_conflicts: list[dict[str, Any]] | None = None,
    selected_stage_state_conflicts: list[dict[str, Any]] | None = None,
    semantic_conflicts: list[dict[str, Any]] | None = None,
    group_setup_map: dict[str, str] | None = None,
    group_teardown_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    cases = list(annotated or [])
    workflow_blueprint_items = list(workflow_blueprints or [])
    trusted_contract_items = list(trusted_workflow_contracts or [])
    current_requirement_blueprint_items = list(current_requirement_workflow_blueprints or [])
    plan_workflow_blueprint_items = list(plan_workflow_blueprints or [])
    stage_kinds = list(main_chain_stage_kinds or [])
    excluded_candidates = list(main_chain_excluded_candidates or [])
    state_conflict_items = list(state_conflicts or [])
    selected_stage_state_conflict_items = list(selected_stage_state_conflicts or [])
    semantic_conflict_items = list(semantic_conflicts or [])
    setup_map = dict(group_setup_map or {})
    teardown_map = dict(group_teardown_map or {})

    plan_counts = execution_plan_counts(cases)
    orchestration_plan = build_execution_orchestration_plan(cases)
    semantic_analysis = analyze_main_smoke_semantic_alignment(cases)
    seen_semantic_conflicts = {
        (str(item.get("case_id") or ""), str(item.get("reason") or ""))
        for item in semantic_conflict_items
    }
    for item in semantic_analysis.get("conflicts") or []:
        key = (str(item.get("case_id") or ""), str(item.get("reason") or ""))
        if key not in seen_semantic_conflicts:
            semantic_conflict_items.append(dict(item))
            seen_semantic_conflicts.add(key)
    semantic_warning_items = [dict(item) for item in (semantic_analysis.get("warnings") or [])]
    workflow_closure = evaluate_declared_workflow_closure(
        cases,
        workflow_blueprints=plan_workflow_blueprint_items,
    )
    main_chain_count = int(plan_counts.get("main_chain_count") or 0)
    independent_count = int(plan_counts.get("independent_count") or 0)
    isolation_count = int(plan_counts.get("isolation_count") or 0)
    role_switch_count = int(plan_counts.get("role_switch_count") or 0)
    broken_dependency_count = int(plan_counts.get("broken_dependency_count") or 0)
    execution_groups = sorted({str(item.get("execution_group") or "") for item in cases})

    return {
        "applied": True,
        "coverage_mode": str(coverage_mode or ""),
        "workflow_blueprint_count": int(len(workflow_blueprint_items)),
        "trusted_workflow_contract_count": int(len(trusted_contract_items)),
        "current_requirement_blueprint_count": int(len(current_requirement_blueprint_items)),
        "plan_workflow_blueprint_count": int(len(plan_workflow_blueprint_items)),
        "workflow_blueprint_source": workflow_blueprint_source,
        "linear_executable": bool(
            workflow_closure.get("closure_satisfied")
            and broken_dependency_count == 0
            and not state_conflict_items
        ),
        "linear_scope": "main_smoke_chain_only",
        "main_chain_case_count": int(main_chain_count),
        "main_chain_stage_order": [
            str(item.get("main_chain_stage") or "")
            for item in cases
            if str(item.get("execution_group") or "") == MAIN_SMOKE_GROUP
        ],
        "main_chain_stage_kinds": stage_kinds,
        "main_chain_incomplete_reason": str(main_chain_incomplete_reason or ""),
        "workflow_closure": workflow_closure,
        "required_stage_ids": list(workflow_closure.get("required_stage_ids") or []),
        "covered_stage_ids": list(workflow_closure.get("covered_stage_ids") or []),
        "missing_required_stage_ids": list(workflow_closure.get("missing_required_stage_ids") or []),
        "required_stage_coverage_complete": not bool(workflow_closure.get("missing_required_stage_ids")),
        "terminal_state_reachable": bool(workflow_closure.get("terminal_state_reachable")),
        "workflow_closure_satisfied": bool(workflow_closure.get("closure_satisfied")),
        "main_chain_excluded_candidates": [
            {key: value for key, value in item.items() if key != "signature"}
            for item in excluded_candidates[:50]
        ],
        "independent_case_count": int(independent_count),
        "isolation_case_count": int(isolation_count),
        "role_switch_count": int(role_switch_count),
        "broken_dependency_count": int(broken_dependency_count),
        "state_conflict_count": int(len(state_conflict_items)),
        "state_conflicts": state_conflict_items[:50],
        "selected_stage_state_conflicts": selected_stage_state_conflict_items[:50],
        "semantic_conflict_count": int(len(semantic_conflict_items)),
        "semantic_conflicts": semantic_conflict_items[:50],
        "semantic_warning_count": int(len(semantic_warning_items)),
        "semantic_warnings": semantic_warning_items[:50],
        "semantic_diagnostics_only": True,
        "execution_group_breakdown": dict(plan_counts.get("execution_group_breakdown") or {}),
        "execution_group_order": list(orchestration_plan.get("execution_group_order") or []),
        "execution_orchestration_plan": orchestration_plan,
        "group_setup": {
            group: setup_map.get(group, "seed_case_dataset()")
            for group in execution_groups
        },
        "group_teardown": {
            group: teardown_map.get(group, "cleanup_case_dataset()")
            for group in execution_groups
        },
        "fixture_keys": list(plan_counts.get("fixture_keys") or []),
    }


__all__ = ["build_execution_plan_metadata_summary", "execution_plan_counts"]
