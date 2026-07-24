from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..control.workflow_blueprint_repository import (
    is_trusted_workflow_contract,
)
from .case_access import (
    case_id as case_access_id,
)
from .execution_plan_action_support import main_chain_action_support_conflict_reason
from .execution_plan_case_state import (
    _main_smoke_cases,
    _state_value,
    _text,
    materialize_final_case_state_fields,
)
from .execution_plan_semantic_alignment import (
    _closure_metrics,
    analyze_main_smoke_semantic_alignment,
    main_chain_blueprint_semantic_conflict_reason,
    validate_main_smoke_semantic_alignment,
)
from ..control.actor_roles import session_key_for_role
from .streaming_execution_plan_ordering import execution_group_order_rank


def _is_current_requirement_workflow_blueprint(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    repository_source = _text(payload.get("repository_source") or payload.get("source")).lower()
    source_type = _text(payload.get("source_type")).lower()
    return bool(
        repository_source == "current_requirement_blueprint"
        or source_type == "current_requirement_extracted"
    )


@dataclass(frozen=True)
class ExecutionPlanValidationPolicy:
    pass


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def validate_execution_group_order(cases: Any) -> list[dict[str, Any]]:
    """Validate that the final JSON array itself follows execution-plan order."""
    normalized = materialize_final_case_state_fields(cases)
    final_cases = [dict(item) for item in normalized if isinstance(item, dict)] if isinstance(normalized, list) else []
    conflicts: list[dict[str, Any]] = []
    seen_side_suite = False
    previous_side_rank = -1
    previous_side_group = ""

    for index, case in enumerate(final_cases, start=1):
        group = _text(case.get("execution_group")).strip().lower()
        if not group:
            continue
        case_id = case_access_id(case) or f"case-{index}"
        sequence_raw = case.get("execution_sequence")
        if sequence_raw not in (None, ""):
            try:
                sequence_value = int(sequence_raw)
            except Exception:
                sequence_value = 0
            if sequence_value != index:
                conflicts.append(
                    {
                        "case_id": case_id,
                        "index": int(index),
                        "execution_sequence": int(sequence_value),
                        "execution_group": group,
                        "reason": "execution_sequence_mismatch",
                    }
                )
        if group == "main_smoke":
            if seen_side_suite:
                conflicts.append(
                    {
                        "case_id": case_id,
                        "index": int(index),
                        "execution_group": group,
                        "reason": "main_smoke_after_independent_suite",
                    }
                )
            continue

        seen_side_suite = True
        rank = execution_group_order_rank(group)
        if previous_side_rank >= 0 and rank < previous_side_rank:
            conflicts.append(
                {
                    "case_id": case_id,
                    "index": int(index),
                    "execution_group": group,
                    "previous_execution_group": previous_side_group,
                    "rank": int(rank),
                    "previous_rank": int(previous_side_rank),
                    "reason": "side_suite_rank_decreased",
                }
            )
        previous_side_rank = rank
        previous_side_group = group

    return conflicts


def validate_main_smoke_state_chain(cases: Any) -> list[dict[str, Any]]:
    """Validate state continuity and session safety inside the ordered main chain."""
    normalized = materialize_final_case_state_fields(cases)
    final_cases = [dict(item) for item in normalized if isinstance(item, dict)] if isinstance(normalized, list) else []
    main_cases = _main_smoke_cases(final_cases)
    conflicts: list[dict[str, Any]] = []

    for index, case in enumerate(main_cases):
        case_id = case_access_id(case) or f"main-smoke-{index + 1}"
        source_state = _text(_state_value(case, "source_state"))
        target_state = _text(_state_value(case, "target_state"))
        if not source_state or not target_state:
            conflicts.append(
                {
                    "case_id": case_id,
                    "reason": "missing_state_transition_fields",
                    "source_state": source_state,
                    "target_state": target_state,
                }
            )
        if _state_value(case, "can_advance_main_flow") is not True:
            conflicts.append({"case_id": case_id, "reason": "non_advancing_case_in_main_smoke"})

        role = _text(case.get("role")).lower()
        session_key = _text(case.get("session_key"))
        if not role or not session_key:
            conflicts.append(
                {
                    "case_id": case_id,
                    "reason": "missing_role_session_fields",
                    "role": role,
                    "session_key": session_key,
                }
            )
        expected_session = session_key_for_role(role) if role else ""
        if expected_session and session_key and session_key != expected_session:
            conflicts.append(
                {
                    "case_id": case_id,
                    "reason": "role_session_mismatch",
                    "role": role,
                    "session_key": session_key,
                    "expected_session_key": expected_session,
                }
            )

        if index <= 0:
            continue
        previous = main_cases[index - 1]
        previous_id = _text(previous.get("id")) or f"main-smoke-{index}"
        previous_target = _text(_state_value(previous, "target_state"))
        if previous_target and source_state and previous_target != source_state:
            conflicts.append(
                {
                    "prev_case_id": previous_id,
                    "curr_case_id": case_id,
                    "prev_target_state": previous_target,
                    "curr_source_state": source_state,
                    "reason": "state_not_connected",
                }
            )
        previous_role = _text(previous.get("role")).lower()
        previous_session = _text(previous.get("session_key"))
        if previous_role and role and previous_role != role and previous_session and previous_session == session_key:
            conflicts.append(
                {
                    "prev_case_id": previous_id,
                    "curr_case_id": case_id,
                    "session_key": session_key,
                    "reason": "role_switch_reuses_same_session",
                }
            )
    return conflicts


def validate_execution_plan(
    final_cases: Any,
    *,
    workflow_blueprints: list[dict[str, Any]] | None = None,
    execution_plan: dict[str, Any] | None = None,
    generation_mode: str = "",
    policy: ExecutionPlanValidationPolicy | None = None,
) -> dict[str, Any]:
    """Return deterministic execution-plan validation diagnostics for persistence."""
    _ = policy or ExecutionPlanValidationPolicy()
    normalized = materialize_final_case_state_fields(final_cases)
    cases = [dict(item) for item in normalized if isinstance(item, dict)] if isinstance(normalized, list) else []
    main_cases = _main_smoke_cases(cases)
    p0_count = sum(1 for item in cases if _text(item.get("priority")).upper() == "P0")
    state_fields = ("source_state", "target_state", "path_type", "blocking", "destructive", "can_advance_main_flow")
    state_field_slots = len(main_cases) * len(state_fields)
    populated_state_fields = sum(
        1
        for item in main_cases
        for field in state_fields
        if _state_value(item, field) not in (None, "")
    )
    workflow_id_missing_count = sum(1 for item in main_cases if not _text(_state_value(item, "workflow_id")))
    state_conflicts = validate_main_smoke_state_chain(cases)
    semantic_analysis = analyze_main_smoke_semantic_alignment(cases)
    semantic_diagnostics = list(semantic_analysis.get("conflicts") or [])
    semantic_conflicts = [
        item
        for item in semantic_diagnostics
        if str(item.get("reason") or "") == "generated_bridge_case_in_final_main_smoke"
    ]
    semantic_warnings = [
        *list(semantic_analysis.get("warnings") or []),
        *[
            {**item, "diagnostic_only": True}
            for item in semantic_diagnostics
            if item not in semantic_conflicts
        ],
    ]
    order_conflicts = validate_execution_group_order(cases)
    resolved_execution_plan = dict(execution_plan or {})
    blueprint_source = _text(resolved_execution_plan.get("workflow_blueprint_source")).lower()
    workflow_absence_declared = resolved_execution_plan.get("workflow_absence_declared") is True
    input_blueprints = [dict(item) for item in (workflow_blueprints or []) if isinstance(item, dict)]
    primary_workflow_id = _text(resolved_execution_plan.get("primary_workflow_id"))
    declared_primary_blueprints = [item for item in input_blueprints if item.get("primary") is True]
    resolved_blueprints = [
        item
        for item in declared_primary_blueprints
        if not primary_workflow_id
        or _text(item.get("workflow_id") or item.get("id")) == primary_workflow_id
    ]
    primary_workflow_resolution_error = ""
    if input_blueprints and len(input_blueprints) > 1:
        primary_workflow_resolution_error = "multiple_workflows_not_supported"
        resolved_blueprints = []
    elif input_blueprints and len(resolved_blueprints) != 1:
        primary_workflow_resolution_error = "primary_workflow_not_declared"
        resolved_blueprints = []
    closure = _closure_metrics(main_cases, workflow_blueprints=resolved_blueprints)
    workflow_closure = dict(closure.get("workflow_closure") or {})
    trusted_workflow_contracts = [
        item for item in resolved_blueprints if is_trusted_workflow_contract(item)
    ]
    current_requirement_blueprints = [
        item for item in resolved_blueprints if _is_current_requirement_workflow_blueprint(item)
    ]
    blueprint_count = len(input_blueprints)
    trusted_workflow_contract_count = len(trusted_workflow_contracts)
    current_requirement_blueprint_count = len(current_requirement_blueprints)
    state_field_coverage = _ratio(populated_state_fields, state_field_slots)
    workflow_id_missing_rate = _ratio(workflow_id_missing_count, len(main_cases))

    failure_reasons: list[str] = []
    if state_conflicts:
        failure_reasons.append("state_chain_conflict")
    if semantic_conflicts:
        failure_reasons.append("main_smoke_semantic_conflict")
    if order_conflicts:
        failure_reasons.append("execution_group_order_conflict")
    independent_suite_executable = bool(
        workflow_absence_declared
        and not input_blueprints
        and not main_cases
        and cases
    )
    if not independent_suite_executable:
        failure_reasons.extend(
            str(item).strip()
            for item in (workflow_closure.get("failure_reasons") or [])
            if str(item).strip()
        )
    if workflow_absence_declared and input_blueprints:
        failure_reasons.append("workflow_absence_conflicts_with_blueprint")
    if workflow_absence_declared and main_cases:
        failure_reasons.append("workflow_absence_conflicts_with_main_smoke")
    if workflow_absence_declared and not cases:
        failure_reasons.append("workflow_absence_independent_suite_empty")
    if primary_workflow_resolution_error and not workflow_absence_declared:
        failure_reasons.append(primary_workflow_resolution_error)
    current_requirement_blueprint_allowed = bool(
        current_requirement_blueprint_count > 0
        and blueprint_source == "current_requirement_blueprint"
    )
    if (
        trusted_workflow_contract_count <= 0
        and not current_requirement_blueprint_allowed
        and not independent_suite_executable
    ):
        failure_reasons.append("workflow_contract_missing")
    if blueprint_source == "current_generation_cases":
        failure_reasons.append("untrusted_candidate_derived_blueprint")

    return {
        "passed": not bool(failure_reasons),
        "failure_code": "" if not failure_reasons else "execution_plan_failed",
        "failure_reasons": list(dict.fromkeys(failure_reasons)),
        "generation_mode": _text(generation_mode),
        "metrics": {
            "final_case_count": int(len(cases)),
            "main_smoke_count": int(len(main_cases)),
            "p0_count": int(p0_count),
            "state_field_coverage": float(state_field_coverage),
            "workflow_id_missing_count": int(workflow_id_missing_count),
            "workflow_id_missing_rate": float(workflow_id_missing_rate),
            "state_conflict_count": int(len(state_conflicts)),
            "semantic_conflict_count": int(len(semantic_conflicts)),
            "semantic_diagnostic_count": int(len(semantic_diagnostics)),
            "semantic_warning_count": int(len(semantic_warnings)),
            "execution_group_order_conflict_count": int(len(order_conflicts)),
            "linear_executable": bool(
                workflow_closure.get("closure_satisfied")
                and not state_conflicts
                and not semantic_conflicts
                and not order_conflicts
            ),
            "workflow_blueprint_count": int(blueprint_count),
            "trusted_workflow_contract_count": int(trusted_workflow_contract_count),
            "current_requirement_blueprint_count": int(current_requirement_blueprint_count),
            "untrusted_workflow_blueprint_count": int(blueprint_count - trusted_workflow_contract_count),
            "workflow_contract_source_types": sorted(
                {
                    _text(item.get("source_type")).lower()
                    for item in trusted_workflow_contracts
                    if _text(item.get("source_type"))
                }
            ),
            "workflow_blueprint_source": blueprint_source or "none",
            "primary_workflow_id": primary_workflow_id,
            "primary_workflow_resolution_error": primary_workflow_resolution_error,
            "workflow_absence_declared": bool(workflow_absence_declared),
            "independent_suite_executable": bool(independent_suite_executable),
            "current_requirement_blueprint_allowed": bool(current_requirement_blueprint_allowed),
            **closure,
        },
        "state_conflicts": state_conflicts[:100],
        "semantic_conflicts": semantic_conflicts[:100],
        "semantic_warnings": semantic_warnings[:100],
        "execution_group_order_conflicts": order_conflicts[:100],
        "cases": cases,
    }


__all__ = [
    "ExecutionPlanValidationPolicy",
    "main_chain_action_support_conflict_reason",
    "main_chain_blueprint_semantic_conflict_reason",
    "materialize_final_case_state_fields",
    "validate_execution_plan",
    "validate_execution_group_order",
    "validate_main_smoke_state_chain",
    "validate_main_smoke_semantic_alignment",
]
