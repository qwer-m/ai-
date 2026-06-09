from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..control.workflow_blueprint_repository import (
    is_trusted_workflow_contract,
)


_STATE_FIELD_NAMES = (
    "workflow_id",
    "source_state",
    "action",
    "target_state",
    "path_type",
    "blocking",
    "destructive",
    "can_advance_main_flow",
    "state_transition_confidence",
)
_ROLE_SESSION_KEYS = {
    "admin": "admin_review_session",
    "supervisor": "supervisor_session",
    "teacher": "supervisor_session",
    "member": "member_student_session",
    "student_free": "free_student_session",
    "student": "student_session",
}
_COMMIT_ACTION_TOKENS = (
    "保存",
    "提交",
    "发布",
    "确认",
    "save",
    "submit",
    "publish",
    "commit",
    "confirm",
)
_DOWNSTREAM_VISIBILITY_TOKENS = (
    "同步",
    "生效",
    "展示",
    "显示",
    "visible",
    "display",
    "sync",
    "reflect",
)
_COMPLETION_SYNC_TOKENS = (
    "完成",
    "进度",
    "状态",
    "complete",
    "completion",
    "progress",
    "status",
)
_CONSUME_TOKENS = (
    "进入",
    "打开",
    "查看",
    "学习",
    "enter",
    "open",
    "view",
    "learn",
    "consume",
)
_CONFIGURE_TOKENS = (
    "配置",
    "设置",
    "选择",
    "编辑",
    "configure",
    "set",
    "select",
    "edit",
)


@dataclass(frozen=True)
class ExecutionPlanValidationPolicy:
    min_main_smoke_count: int = 6
    min_p0_count: int = 6
    min_state_field_coverage: float = 0.8
    max_workflow_id_missing_rate: float = 0.2
    reject_untrusted_blueprint_source: bool = True


def _text(value: Any) -> str:
    return str(value or "").strip()


def _token_hit(text: str, tokens: tuple[str, ...]) -> bool:
    haystack = _text(text).lower()
    if not haystack:
        return False
    for token in tokens:
        needle = _text(token).lower()
        if not needle:
            continue
        if needle.isascii() and re.search(r"[a-z0-9]", needle):
            if re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", haystack):
                return True
            continue
        if needle in haystack:
            return True
    return False


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _transition_payload(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("workflow_transition")
    return dict(nested) if isinstance(nested, dict) else {}


def _state_value(case: dict[str, Any], field: str) -> Any:
    value = case.get(field)
    if value not in (None, ""):
        return value
    return _transition_payload(case).get(field)


def _case_order(case: dict[str, Any], fallback: int) -> tuple[int, int]:
    try:
        main_step = int(case.get("main_chain_step") or 0)
    except (TypeError, ValueError):
        main_step = 0
    try:
        sequence = int(case.get("execution_sequence") or fallback)
    except (TypeError, ValueError):
        sequence = fallback
    return (main_step if main_step > 0 else 100000 + sequence, sequence)


def _stage_kind(case: dict[str, Any]) -> str:
    explicit = _text(_state_value(case, "stage_kind") or case.get("main_chain_stage_kind")).lower()
    if explicit:
        return explicit
    action_text = _text(_state_value(case, "action")).lower()
    target_state_text = _text(_state_value(case, "target_state")).lower()
    description_text = _text(case.get("description")).lower()
    text = " ".join(
        [
            action_text,
            description_text,
            _text(case.get("expected_result")),
            target_state_text,
        ]
    ).lower()
    action_target_description = " ".join([action_text, target_state_text, description_text])
    if _token_hit(action_target_description, _COMMIT_ACTION_TOKENS):
        return "commit"
    if _token_hit(text, _DOWNSTREAM_VISIBILITY_TOKENS):
        return "downstream_visibility"
    if _token_hit(text, _COMPLETION_SYNC_TOKENS):
        return "completion_sync"
    if _token_hit(text, _CONSUME_TOKENS):
        return "consume"
    if _token_hit(text, _CONFIGURE_TOKENS):
        return "configure"
    return "unknown"


def materialize_final_case_state_fields(cases: Any) -> Any:
    """Promote the workflow transition contract into persisted final-case fields."""
    if not isinstance(cases, list):
        return cases
    normalized: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        case = dict(item)
        transition = _transition_payload(case)
        for field in _STATE_FIELD_NAMES:
            value = case.get(field)
            if value in (None, "") and transition.get(field) not in (None, ""):
                case[field] = transition[field]
        normalized.append(case)
    return normalized


def _main_smoke_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        dict(item)
        for item in cases
        if isinstance(item, dict) and _text(item.get("execution_group")).lower() == "main_smoke"
    ]
    return [
        item
        for _, item in sorted(
            enumerate(selected, start=1),
            key=lambda row: _case_order(row[1], row[0]),
        )
    ]


def validate_main_smoke_state_chain(cases: Any) -> list[dict[str, Any]]:
    """Validate state continuity and session safety inside the ordered main chain."""
    normalized = materialize_final_case_state_fields(cases)
    final_cases = [dict(item) for item in normalized if isinstance(item, dict)] if isinstance(normalized, list) else []
    main_cases = _main_smoke_cases(final_cases)
    conflicts: list[dict[str, Any]] = []

    for index, case in enumerate(main_cases):
        case_id = _text(case.get("id")) or f"main-smoke-{index + 1}"
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
        expected_session = _ROLE_SESSION_KEYS.get(role)
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


def _closure_metrics(main_cases: list[dict[str, Any]]) -> dict[str, Any]:
    stage_kinds = [_stage_kind(item) for item in main_cases]
    commit_indexes = [index for index, kind in enumerate(stage_kinds) if kind == "commit"]
    downstream_indexes = [
        index
        for index, kind in enumerate(stage_kinds)
        if kind in {"downstream_visibility", "consume", "completion_sync"}
    ]
    closed_loop = bool(
        commit_indexes
        and downstream_indexes
        and any(downstream_index > commit_index for commit_index in commit_indexes for downstream_index in downstream_indexes)
    )
    return {
        "main_chain_stage_kinds": stage_kinds,
        "commit_step_count": int(len(commit_indexes)),
        "downstream_or_completion_step_count": int(len(downstream_indexes)),
        "commit_downstream_completion_closed": bool(closed_loop),
    }


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
    closure = _closure_metrics(main_cases)
    resolved_execution_plan = dict(execution_plan or {})
    blueprint_source = _text(resolved_execution_plan.get("workflow_blueprint_source")).lower()
    resolved_blueprints = [dict(item) for item in (workflow_blueprints or []) if isinstance(item, dict)]
    trusted_workflow_contracts = [
        item for item in resolved_blueprints if is_trusted_workflow_contract(item)
    ]
    blueprint_count = len(resolved_blueprints)
    trusted_workflow_contract_count = len(trusted_workflow_contracts)
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
    if not bool(closure.get("commit_downstream_completion_closed")):
        failure_reasons.append("commit_downstream_completion_missing")
    if trusted_workflow_contract_count <= 0:
        failure_reasons.append("workflow_contract_missing")
    if resolved_policy.reject_untrusted_blueprint_source and blueprint_source == "current_generation_cases":
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
            "linear_executable": bool(
                len(main_cases) >= int(resolved_policy.min_main_smoke_count)
                and not state_conflicts
                and closure.get("commit_downstream_completion_closed")
            ),
            "workflow_blueprint_count": int(blueprint_count),
            "trusted_workflow_contract_count": int(trusted_workflow_contract_count),
            "untrusted_workflow_blueprint_count": int(blueprint_count - trusted_workflow_contract_count),
            "workflow_contract_source_types": sorted(
                {
                    _text(item.get("source_type")).lower()
                    for item in trusted_workflow_contracts
                    if _text(item.get("source_type"))
                }
            ),
            "workflow_blueprint_source": blueprint_source or "none",
            **closure,
        },
        "state_conflicts": state_conflicts[:100],
        "cases": cases,
    }


__all__ = [
    "ExecutionPlanValidationPolicy",
    "materialize_final_case_state_fields",
    "validate_execution_plan",
    "validate_main_smoke_state_chain",
]
