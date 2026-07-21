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
    min_main_smoke_count: int = 6
    min_p0_count: int = 6
    min_state_field_coverage: float = 0.8
    max_workflow_id_missing_rate: float = 0.2
    reject_untrusted_blueprint_source: bool = True
    allow_candidate_blueprint_without_contract: bool = True


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
        if bool(_state_value(case, "blocking")):
            conflicts.append({"case_id": case_id, "reason": "blocking_case_in_main_smoke"})
        if bool(_state_value(case, "destructive")) and index < len(main_cases) - 1:
            conflicts.append({"case_id": case_id, "reason": "destructive_case_before_terminal"})
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
    resolved_policy = policy or ExecutionPlanValidationPolicy()
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
    semantic_conflicts = validate_main_smoke_semantic_alignment(cases)
    order_conflicts = validate_execution_group_order(cases)
    closure = _closure_metrics(main_cases)
    resolved_execution_plan = dict(execution_plan or {})
    blueprint_source = _text(resolved_execution_plan.get("workflow_blueprint_source")).lower()
    resolved_blueprints = [dict(item) for item in (workflow_blueprints or []) if isinstance(item, dict)]
    trusted_workflow_contracts = [
        item for item in resolved_blueprints if is_trusted_workflow_contract(item)
    ]
    current_requirement_blueprints = [
        item for item in resolved_blueprints if _is_current_requirement_workflow_blueprint(item)
    ]
    blueprint_count = len(resolved_blueprints)
    trusted_workflow_contract_count = len(trusted_workflow_contracts)
    current_requirement_blueprint_count = len(current_requirement_blueprints)
    state_field_coverage = _ratio(populated_state_fields, state_field_slots)
    workflow_id_missing_rate = _ratio(workflow_id_missing_count, len(main_cases))

    failure_reasons: list[str] = []
    if len(main_cases) < int(resolved_policy.min_main_smoke_count):
        failure_reasons.append("main_smoke_count_below_threshold")
    if p0_count < int(resolved_policy.min_p0_count):
        failure_reasons.append("p0_count_below_threshold")
    if state_field_coverage < float(resolved_policy.min_state_field_coverage):
        failure_reasons.append("state_field_coverage_below_threshold")
    if workflow_id_missing_rate > float(resolved_policy.max_workflow_id_missing_rate):
        failure_reasons.append("workflow_id_missing_rate_above_threshold")
    if state_conflicts:
        failure_reasons.append("state_chain_conflict")
    if semantic_conflicts:
        failure_reasons.append("main_smoke_semantic_conflict")
    if order_conflicts:
        failure_reasons.append("execution_group_order_conflict")
    if not bool(closure.get("commit_downstream_completion_closed")):
        failure_reasons.append("commit_downstream_completion_missing")
    candidate_blueprint_without_contract = bool(
        trusted_workflow_contract_count <= 0
        and blueprint_count <= 0
        and blueprint_source == "current_generation_cases"
        and resolved_policy.allow_candidate_blueprint_without_contract
    )
    current_requirement_blueprint_allowed = bool(
        current_requirement_blueprint_count > 0
        and blueprint_source == "current_requirement_blueprint"
    )
    if (
        trusted_workflow_contract_count <= 0
        and not current_requirement_blueprint_allowed
        and not candidate_blueprint_without_contract
    ):
        failure_reasons.append("workflow_contract_missing")
    if (
        resolved_policy.reject_untrusted_blueprint_source
        and blueprint_source == "current_generation_cases"
        and not candidate_blueprint_without_contract
    ):
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
            "execution_group_order_conflict_count": int(len(order_conflicts)),
            "linear_executable": bool(
                len(main_cases) >= int(resolved_policy.min_main_smoke_count)
                and not state_conflicts
                and not semantic_conflicts
                and not order_conflicts
                and closure.get("commit_downstream_completion_closed")
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
            "current_requirement_blueprint_allowed": bool(current_requirement_blueprint_allowed),
            "candidate_blueprint_without_contract_allowed": bool(candidate_blueprint_without_contract),
            **closure,
        },
        "state_conflicts": state_conflicts[:100],
        "semantic_conflicts": semantic_conflicts[:100],
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
