from __future__ import annotations

from typing import Any

from .case_access import (
    case_priority as _case_priority,
    case_text_field as _case_text_field,
)
from .execution_plan_validator import (
    materialize_final_case_state_fields,
    validate_main_smoke_state_chain,
)
from .execution_plan_case_state import (
    main_chain_precondition_conflict_reason,
    typed_state_contract_conflicts,
    unknown_precondition_source_count,
)
from .streaming_case_keys import (
    candidate_identity_key as _candidate_identity_key,
    case_signature as _signature,
    review_case_id as _review_case_id,
)
from .streaming_execution_plan_helpers import (
    contains_any_token as _any,
    default_group_setup_map,
    default_group_teardown_map,
    empty_execution_plan_summary as _empty_execution_plan_summary,
    evaluate_declared_workflow_closure as _evaluate_declared_workflow_closure,
    infer_group as _infer_group,
    MainChainExclusionRecorder,
    main_chain_stages_from_blueprints as _main_chain_stages_from_blueprints,
    pattern_match_score as _pattern_match_score,
    priority_rank as _priority_rank,
    resolve_primary_workflow_blueprint as _resolve_primary_workflow_blueprint,
    selected_stage_state_conflicts as _selected_stage_state_conflicts_helper,
    workflow_blueprint_source_label as _workflow_blueprint_source_label,
)
from .streaming_execution_plan_assignment import maximum_weight_stage_assignment
from .streaming_execution_plan_metadata_helpers import (
    annotate_execution_plan_cases as _annotate_execution_plan_cases,
)
from .streaming_execution_plan_ordering import order_execution_plan_cases as _order_execution_plan_cases
from .streaming_execution_plan_summary import (
    build_execution_plan_metadata_summary as _build_execution_plan_metadata_summary,
)
from .streaming_postprocess_utils import _dict_case_copies


_STRUCTURED_EDGE_REASON_PENALTIES = {
    "multiple_verified_workflow_stage_candidates": 15,
    "precondition_state_source_unknown": 8,
    "multiple_verified_module_candidates": 8,
}


def _stage_required_by_blueprint(
    stage_key: str,
    step_meta: dict[str, Any],
    plan_workflow_blueprints: list[dict[str, Any]],
) -> bool | None:
    if isinstance(step_meta.get("required"), bool):
        return bool(step_meta.get("required"))
    if isinstance(step_meta.get("optional"), bool):
        return not bool(step_meta.get("optional"))
    blueprint_id = str(step_meta.get("blueprint_id") or "").strip()
    blueprint = next(
        (
            item
            for item in plan_workflow_blueprints
            if isinstance(item, dict)
            and str(item.get("id") or "").strip() == blueprint_id
        ),
        {},
    )
    required_value = (
        blueprint.get("required_stage_ids")
        if "required_stage_ids" in blueprint
        else blueprint.get("required_stages")
    )
    required_ids = {
        str(item.get("id") if isinstance(item, dict) else item).strip()
        for item in (required_value or [])
        if str(item.get("id") if isinstance(item, dict) else item).strip()
    }
    optional_ids = {
        str(item.get("id") if isinstance(item, dict) else item).strip()
        for item in (blueprint.get("optional_stage_ids") or blueprint.get("optional_stages") or [])
        if str(item.get("id") if isinstance(item, dict) else item).strip()
    }
    if isinstance(required_value, (list, tuple, set)):
        return stage_key in required_ids
    if stage_key in optional_ids:
        return False
    return None


def _workflow_blueprint_contract_complete(
    plan_workflow_blueprints: list[dict[str, Any]],
) -> bool:
    blueprint = next(
        (
            item
            for item in plan_workflow_blueprints
            if isinstance(item, dict) and isinstance(item.get("steps"), list)
        ),
        {},
    )
    if blueprint.get("closure_declaration_complete") is False:
        return False
    if not blueprint or not str(blueprint.get("initial_state") or "").strip():
        return False
    required_ids = blueprint.get("required_stage_ids")
    terminal_states = blueprint.get("terminal_states")
    if not isinstance(required_ids, list) or not any(str(item or "").strip() for item in required_ids):
        return False
    if not isinstance(terminal_states, list) or not any(str(item or "").strip() for item in terminal_states):
        return False
    steps = [step for step in (blueprint.get("steps") or []) if isinstance(step, dict)]
    step_ids = {str(step.get("id") or "").strip() for step in steps}
    if not all(str(stage_id or "").strip() in step_ids for stage_id in required_ids):
        return False
    for step in steps:
        if any(
            not str(step.get(field) or "").strip()
            for field in ("id", "action", "state_in", "state_out", "stage_kind", "path_type")
        ):
            return False
        if any(
            not isinstance(step.get(field), bool)
            for field in (
                "required",
                "terminal",
                "critical",
                "blocking",
                "destructive",
                "can_advance_main_flow",
            )
        ):
            return False
    return True


def _optional_branch_state_conflicts(
    plan_workflow_blueprints: list[dict[str, Any]],
    *,
    required_stage_ids: list[str],
) -> list[dict[str, Any]]:
    """按状态图验证可选分支，避免把分支误拼成必选线性主链。"""
    blueprint = next(
        (
            item
            for item in plan_workflow_blueprints
            if isinstance(item, dict) and isinstance(item.get("steps"), list)
        ),
        {},
    )
    if not blueprint:
        return []
    required = {str(item or "").strip() for item in required_stage_ids if str(item or "").strip()}
    steps = [dict(item) for item in (blueprint.get("steps") or []) if isinstance(item, dict)]
    reachable_states = {
        str(blueprint.get("initial_state") or "").strip(),
        *(
            str(step.get(field) or "").strip()
            for step in steps
            if str(step.get("id") or "").strip() in required
            for field in ("state_in", "state_out")
        ),
    }
    reachable_states.discard("")
    pending = [
        step
        for step in steps
        if str(step.get("id") or "").strip() not in required
    ]
    changed = True
    while changed and pending:
        changed = False
        remaining: list[dict[str, Any]] = []
        for step in pending:
            state_in = str(step.get("state_in") or "").strip()
            state_out = str(step.get("state_out") or "").strip()
            if state_in and state_out and state_in in reachable_states:
                reachable_states.add(state_out)
                changed = True
            else:
                remaining.append(step)
        pending = remaining
    return [
        {
            "stage_id": str(step.get("id") or "").strip(),
            "source_state": str(step.get("state_in") or "").strip(),
            "target_state": str(step.get("state_out") or "").strip(),
            "reason": "optional_branch_source_state_not_reachable",
        }
        for step in pending
    ]


def _positive_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _verified_workflow_stage_candidates(
    item: dict[str, Any],
    *,
    workflow_id: str,
    stage_key: str = "",
) -> list[dict[str, Any]]:
    semantic = item.get("_semantic")
    if not isinstance(semantic, dict) or not workflow_id:
        return []
    matched: list[dict[str, Any]] = []
    for raw_candidate in (semantic.get("workflow_stage_candidates") or []):
        if not isinstance(raw_candidate, dict):
            continue
        candidate_workflow_id = str(raw_candidate.get("workflow_id") or "").strip()
        candidate_stage_id = str(raw_candidate.get("stage_id") or "").strip()
        confidence = _positive_confidence(raw_candidate.get("confidence"))
        if candidate_workflow_id != workflow_id or not candidate_stage_id:
            continue
        if stage_key and candidate_stage_id != stage_key:
            continue
        if raw_candidate.get("evidence_verified") is not True or confidence <= 0.0:
            continue
        matched.append({**raw_candidate, "confidence": confidence})
    return sorted(
        matched,
        key=lambda candidate: (
            -float(candidate.get("confidence") or 0.0),
            str(candidate.get("stage_id") or ""),
            str(candidate.get("stage_kind") or ""),
        ),
    )


def _verified_module_keys(item: dict[str, Any]) -> set[str]:
    semantic = item.get("_semantic")
    if not isinstance(semantic, dict):
        return set()
    return {
        str(candidate.get("module_key") or "").strip()
        for candidate in (semantic.get("module_candidates") or [])
        if isinstance(candidate, dict)
        and str(candidate.get("module_key") or "").strip()
        and candidate.get("evidence_verified") is True
        and _positive_confidence(candidate.get("confidence")) > 0.0
    }


def _declared_module_keys(step_meta: dict[str, Any]) -> set[str]:
    return {
        str(candidate.get("module_key") or "").strip()
        for candidate in (step_meta.get("module_candidates") or [])
        if isinstance(candidate, dict) and str(candidate.get("module_key") or "").strip()
    }


def _semantic_interaction_ids(item: dict[str, Any]) -> set[str]:
    semantic = item.get("_semantic")
    if not isinstance(semantic, dict):
        return set()
    return {
        str(interaction_id or "").strip()
        for interaction_id in (semantic.get("interaction_ids") or [])
        if str(interaction_id or "").strip()
    }


def _declared_interaction_ids(step_meta: dict[str, Any]) -> set[str]:
    return {
        str(interaction_id or "").strip()
        for interaction_id in (step_meta.get("interaction_ids") or [])
        if str(interaction_id or "").strip()
    }


def _structured_candidate_contract_conflicts(
    item: dict[str, Any],
    *,
    step_meta: dict[str, Any],
    candidate: dict[str, Any],
) -> list[str]:
    expected_stage_kind = str(step_meta.get("stage_kind") or "").strip().lower()
    candidate_stage_kind = str(candidate.get("stage_kind") or "").strip().lower()
    conflicts: list[str] = []
    if not candidate_stage_kind:
        conflicts.append("workflow_stage_kind_missing")
    elif candidate_stage_kind != expected_stage_kind:
        conflicts.append("workflow_stage_kind_mismatch")
    declared_modules = _declared_module_keys(step_meta)
    actual_modules = _verified_module_keys(item)
    if declared_modules and (
        not actual_modules or not declared_modules.issubset(actual_modules)
    ):
        conflicts.append("workflow_stage_module_contract_mismatch")
    declared_interactions = _declared_interaction_ids(step_meta)
    actual_interactions = _semantic_interaction_ids(item)
    if declared_interactions and not declared_interactions.issubset(actual_interactions):
        conflicts.append("workflow_stage_interaction_contract_missing")
    if actual_interactions and not actual_interactions.issubset(declared_interactions):
        conflicts.append("workflow_stage_interaction_contract_mismatch")
    conflicts.extend(
        str(conflict.get("reason") or "workflow_typed_state_contract_mismatch")
        for conflict in typed_state_contract_conflicts(item, step_meta=step_meta)
    )
    return list(dict.fromkeys(conflicts))


def evaluate_required_stage_candidate_coverage(
    cases: list[dict[str, Any]],
    *,
    workflow_blueprints: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """用执行计划的同一套精确语义口径，评估候选集是否已覆盖必选阶段。

    这里只消费已核验的 workflow/stage ID、模块、交互和状态契约，
    不从用例正文反推阶段。全局一对一分配可防止同一候选虚假覆盖多个必选阶段。
    """
    candidate_cases = _dict_case_copies(cases)
    declared_blueprints = [
        dict(item)
        for item in (workflow_blueprints or [])
        if isinstance(item, dict) and isinstance(item.get("steps"), list)
    ]
    plan_blueprints, resolution = _resolve_primary_workflow_blueprint(declared_blueprints)
    if not plan_blueprints:
        declaration_blocks_main_chain = bool(declared_blueprints)
        return {
            "active": False,
            "resolution": dict(resolution or {}),
            "workflow_id": "",
            "required_stage_ids": [],
            "covered_required_stage_ids": [],
            "missing_required_stage_ids": [],
            "missing_required_stages": [],
            "selected_required_candidates": [],
            "candidate_edge_count": 0,
            "assignment_required_stage_coverage_complete": not declaration_blocks_main_chain,
            "required_stage_coverage_complete": not declaration_blocks_main_chain,
            "publishable_main_chain": False,
            "workflow_blueprint_contract_complete": False,
            "workflow_blueprint_closure": {},
            "candidate_workflow_closure": {},
            "selected_stage_state_conflicts": [],
            "source_generation_allowed": False,
            "actionable_stage_ids": [],
            "failure_reason": (
                str(resolution.get("status") or "workflow_blueprint_invalid")
                if declaration_blocks_main_chain
                else "workflow_blueprint_missing"
            ),
        }

    main_chain_stages, stage_meta_by_key, _stage_output_state = (
        _main_chain_stages_from_blueprints(plan_blueprints)
    )
    stage_required_by_key = {
        stage_key: _stage_required_by_blueprint(
            stage_key,
            stage_meta_by_key.get(stage_key) or {},
            plan_blueprints,
        )
        for stage_key, _stage_label, _patterns in main_chain_stages
    }
    stage_rows = [
        {
            "stage_key": stage_key,
            "stage_label": stage_label,
            "stage_order": index + 1,
            "required": bool(stage_required_by_key.get(stage_key)),
        }
        for index, (stage_key, stage_label, _patterns) in enumerate(main_chain_stages)
    ]
    required_stage_ids = [
        str(row.get("stage_key") or "")
        for row in stage_rows
        if row.get("required") is True and str(row.get("stage_key") or "")
    ]
    required_stage_id_set = set(required_stage_ids)
    case_by_candidate_key: dict[str, dict[str, Any]] = {}
    assignment_edges: list[dict[str, Any]] = []
    stable_candidates = sorted(
        candidate_cases,
        key=lambda item: (
            _candidate_identity_key(item),
            _review_case_id(item),
            _case_text_field(item, "description"),
        ),
    )
    for item in stable_candidates:
        candidate_key = _candidate_identity_key(item)
        if candidate_key:
            case_by_candidate_key.setdefault(candidate_key, dict(item))

    for stage_key, stage_label, _patterns in main_chain_stages:
        step_meta = stage_meta_by_key.get(stage_key) or {}
        workflow_id = str(step_meta.get("workflow_id") or "").strip()
        for item in stable_candidates:
            candidate_key = _candidate_identity_key(item)
            if not candidate_key:
                continue
            stage_candidates = _verified_workflow_stage_candidates(
                item,
                workflow_id=workflow_id,
                stage_key=stage_key,
            )
            if not stage_candidates:
                continue
            stage_candidate = stage_candidates[0]
            if _structured_candidate_contract_conflicts(
                item,
                step_meta=step_meta,
                candidate=stage_candidate,
            ):
                continue
            penalties: list[dict[str, Any]] = []
            verified_stage_ids = {
                str(candidate.get("stage_id") or "").strip()
                for candidate in _verified_workflow_stage_candidates(
                    item,
                    workflow_id=workflow_id,
                )
            }
            cross_stage_candidate_count = max(0, len(verified_stage_ids) - 1)
            if cross_stage_candidate_count:
                penalties.append(
                    {
                        "reason": "multiple_verified_workflow_stage_candidates",
                        "points": int(
                            min(
                                30,
                                cross_stage_candidate_count
                                * _STRUCTURED_EDGE_REASON_PENALTIES[
                                    "multiple_verified_workflow_stage_candidates"
                                ],
                            )
                        ),
                    }
                )
            unknown_source_count = unknown_precondition_source_count(item)
            if unknown_source_count:
                penalties.append(
                    {
                        "reason": "precondition_state_source_unknown",
                        "points": int(
                            min(
                                24,
                                unknown_source_count
                                * _STRUCTURED_EDGE_REASON_PENALTIES[
                                    "precondition_state_source_unknown"
                                ],
                            )
                        ),
                    }
                )
            module_candidate_count = len(_verified_module_keys(item))
            if module_candidate_count > 1:
                penalties.append(
                    {
                        "reason": "multiple_verified_module_candidates",
                        "points": int(
                            min(
                                24,
                                (module_candidate_count - 1)
                                * _STRUCTURED_EDGE_REASON_PENALTIES[
                                    "multiple_verified_module_candidates"
                                ],
                            )
                        ),
                    }
                )
            score = (
                100
                + _priority_rank(item)
                + int(float(stage_candidate.get("confidence") or 0.0) * 100)
                - sum(int(penalty.get("points") or 0) for penalty in penalties)
            )
            assignment_edges.append(
                {
                    "stage_key": stage_key,
                    "stage_label": stage_label,
                    "candidate_key": candidate_key,
                    "case_signature": _signature(item),
                    "case_id": _review_case_id(item),
                    "score": int(score),
                    "model_stage_confidence": float(
                        stage_candidate.get("confidence") or 0.0
                    ),
                    "penalties": penalties,
                }
            )

    assignment = maximum_weight_stage_assignment(stage_rows, assignment_edges)
    best_selected_by_stage = [
        (
            str(row.get("stage_key") or ""),
            str(row.get("stage_label") or row.get("stage_key") or ""),
            dict(case_by_candidate_key.get(str(row.get("candidate_key") or "")) or {}),
        )
        for row in (assignment.get("selected") or [])
        if str(row.get("candidate_key") or "") in case_by_candidate_key
    ]
    required_selected_by_stage = [
        item for item in best_selected_by_stage if item[0] in required_stage_id_set
    ]
    selected_stage_state_conflicts: list[dict[str, Any]] = []
    if required_selected_by_stage:
        selected_stage_state_conflicts = _selected_stage_state_conflicts_helper(
            required_selected_by_stage,
            stage_meta_by_key=stage_meta_by_key,
            case_id_fn=_review_case_id,
        )
        for index in range(1, len(required_selected_by_stage)):
            previous_stage_key, _previous_label, previous_case = required_selected_by_stage[index - 1]
            current_stage_key, _current_label, current_case = required_selected_by_stage[index]
            continuity_reason = main_chain_precondition_conflict_reason(
                previous_case,
                current_case,
                previous_step_meta=stage_meta_by_key.get(previous_stage_key) or {},
                current_step_meta=stage_meta_by_key.get(current_stage_key) or {},
            )
            if continuity_reason:
                selected_stage_state_conflicts.append(
                    {
                        "prev_stage_key": previous_stage_key,
                        "curr_stage_key": current_stage_key,
                        "prev_case_id": _review_case_id(previous_case),
                        "curr_case_id": _review_case_id(current_case),
                        "reason": continuity_reason,
                    }
                )

    def _provisional_cases(
        selected: list[tuple[str, str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        return [
            {
                **dict(item),
                "execution_group": "main_smoke",
                "main_chain_stage": stage_key,
                "main_chain_step": index,
                "workflow_id": str(
                    (stage_meta_by_key.get(stage_key) or {}).get("workflow_id") or ""
                ),
                "source_state": str(
                    (stage_meta_by_key.get(stage_key) or {}).get("state_in") or ""
                ),
                "target_state": str(
                    (stage_meta_by_key.get(stage_key) or {}).get("state_out") or ""
                ),
            }
            for index, (stage_key, _stage_label, item) in enumerate(selected, start=1)
        ]

    candidate_workflow_closure = _evaluate_declared_workflow_closure(
        _provisional_cases(required_selected_by_stage),
        workflow_blueprints=plan_blueprints,
    )
    blueprint_selected_by_stage = [
        (
            stage_key,
            stage_label,
            {"id": f"blueprint:{stage_key}"},
        )
        for stage_key, stage_label, _patterns in main_chain_stages
        if stage_key in required_stage_id_set
    ]
    workflow_blueprint_closure = _evaluate_declared_workflow_closure(
        _provisional_cases(blueprint_selected_by_stage),
        workflow_blueprints=plan_blueprints,
    )
    workflow_blueprint_contract_complete = _workflow_blueprint_contract_complete(
        plan_blueprints
    ) and all(value is not None for value in stage_required_by_key.values())
    optional_branch_state_conflicts = _optional_branch_state_conflicts(
        plan_blueprints,
        required_stage_ids=required_stage_ids,
    )
    source_generation_allowed = bool(
        workflow_blueprint_contract_complete
        and workflow_blueprint_closure.get("closure_satisfied") is True
        and not optional_branch_state_conflicts
    )
    selected_required_candidates = [
        {
            "workflow_id": str(
                (stage_meta_by_key.get(str(row.get("stage_key") or "")) or {}).get(
                    "workflow_id"
                )
                or ""
            ),
            "stage_id": str(row.get("stage_key") or ""),
            "stage_kind": str(
                (stage_meta_by_key.get(str(row.get("stage_key") or "")) or {}).get(
                    "stage_kind"
                )
                or ""
            ),
            "candidate_key": str(row.get("candidate_key") or ""),
            "case_signature": str(row.get("case_signature") or ""),
            "case_id": str(row.get("case_id") or ""),
        }
        for row in (assignment.get("selected") or [])
        if row.get("required") is True and str(row.get("stage_key") or "")
    ]
    covered_required_stage_ids = [
        str(item.get("stage_id") or "")
        for item in selected_required_candidates
        if str(item.get("stage_id") or "")
    ]
    covered_set = set(covered_required_stage_ids)
    missing_required_stage_ids = [
        stage_id for stage_id in required_stage_ids if stage_id not in covered_set
    ]
    required_gap_count = int(assignment.get("required_gap_count") or 0)
    publishable_main_chain = bool(
        required_selected_by_stage
        and workflow_blueprint_contract_complete
        and workflow_blueprint_closure.get("closure_satisfied") is True
        and not optional_branch_state_conflicts
        and required_gap_count == 0
        and not selected_stage_state_conflicts
        and candidate_workflow_closure.get("closure_satisfied") is True
    )
    failure_reason = ""
    if not workflow_blueprint_contract_complete:
        failure_reason = "workflow_blueprint_incomplete"
    elif workflow_blueprint_closure.get("closure_satisfied") is not True:
        failure_reason = "workflow_blueprint_closure_invalid"
    elif optional_branch_state_conflicts:
        failure_reason = "optional_branch_state_unreachable"
    elif required_gap_count:
        failure_reason = "required_stage_gap"
    elif selected_stage_state_conflicts:
        failure_reason = "state_chain_conflict"
    elif candidate_workflow_closure.get("closure_satisfied") is not True:
        closure_reasons = [
            str(item).strip()
            for item in (candidate_workflow_closure.get("failure_reasons") or [])
            if str(item).strip()
        ]
        failure_reason = closure_reasons[0] if closure_reasons else "workflow_closure_invalid"

    actionable_stage_ids: list[str] = list(missing_required_stage_ids)
    if source_generation_allowed:
        for conflict in selected_stage_state_conflicts:
            for key in ("curr_stage_key", "stage_key"):
                stage_id = str(conflict.get(key) or "").strip()
                if stage_id and stage_id not in actionable_stage_ids:
                    actionable_stage_ids.append(stage_id)
        for conflict in candidate_workflow_closure.get("state_conflicts") or []:
            if not isinstance(conflict, dict):
                continue
            stage_id = str(conflict.get("stage_id") or "").strip()
            if stage_id and stage_id not in actionable_stage_ids:
                actionable_stage_ids.append(stage_id)
    else:
        actionable_stage_ids = []
    primary_workflow_id = str(resolution.get("primary_workflow_id") or "")
    return {
        "active": bool(required_stage_ids),
        "resolution": dict(resolution or {}),
        "workflow_id": primary_workflow_id,
        "required_stage_ids": required_stage_ids,
        "covered_required_stage_ids": covered_required_stage_ids,
        "missing_required_stage_ids": missing_required_stage_ids,
        "missing_required_stages": [
            {
                "workflow_id": primary_workflow_id,
                "stage_id": stage_id,
                "stage_kind": str(
                    (stage_meta_by_key.get(stage_id) or {}).get("stage_kind") or ""
                ),
                "stage_order": int(
                    (stage_meta_by_key.get(stage_id) or {}).get("step_index") or 0
                ),
            }
            for stage_id in actionable_stage_ids
        ],
        "selected_required_candidates": selected_required_candidates,
        "candidate_edge_count": int(assignment.get("candidate_edge_count") or 0),
        "assignment_required_stage_coverage_complete": not bool(
            missing_required_stage_ids
        ),
        "required_stage_coverage_complete": bool(publishable_main_chain),
        "publishable_main_chain": bool(publishable_main_chain),
        "workflow_blueprint_contract_complete": bool(
            workflow_blueprint_contract_complete
        ),
        "workflow_blueprint_closure": dict(workflow_blueprint_closure or {}),
        "candidate_workflow_closure": dict(candidate_workflow_closure or {}),
        "selected_stage_state_conflicts": list(selected_stage_state_conflicts),
        "optional_branch_state_conflicts": list(optional_branch_state_conflicts),
        "source_generation_allowed": bool(source_generation_allowed),
        "actionable_stage_ids": actionable_stage_ids,
        "failure_reason": failure_reason,
    }


def retain_required_stage_assignment(
    source_cases: list[dict[str, Any]],
    selected_cases: list[dict[str, Any]],
    *,
    workflow_blueprints: list[dict[str, Any]] | None,
    target_max_count: int = 0,
    require_complete_source: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """保留候选池全局分配选中的必选阶段候选，并在可能时维持数量窗口。"""
    source = _dict_case_copies(source_cases)
    selected = _dict_case_copies(selected_cases)
    selected_coverage = evaluate_required_stage_candidate_coverage(
        selected,
        workflow_blueprints=workflow_blueprints,
    )
    if (
        selected_coverage.get("active") is True
        and selected_coverage.get("required_stage_coverage_complete") is True
    ):
        selected_protected_keys = sorted(
            {
                str(item.get("candidate_key") or "")
                for item in (
                    selected_coverage.get("selected_required_candidates") or []
                )
                if str(item.get("candidate_key") or "")
            }
        )
        return selected, {
            "applied": False,
            "reason": "selected_required_stage_assignment_already_complete",
            "source_coverage": selected_coverage,
            "final_coverage": selected_coverage,
            "protected_candidate_keys": selected_protected_keys,
            "restored_candidate_keys": [],
            "replaced_candidate_keys": [],
            "target_max_count": int(target_max_count or 0),
            "within_target_max": not bool(
                int(target_max_count or 0) > 0
                and len(selected) > int(target_max_count or 0)
            ),
        }
    coverage = evaluate_required_stage_candidate_coverage(
        source,
        workflow_blueprints=workflow_blueprints,
    )
    source_complete = bool(coverage.get("required_stage_coverage_complete"))
    protected_keys = {
        str(item.get("candidate_key") or "")
        for item in (coverage.get("selected_required_candidates") or [])
        if str(item.get("candidate_key") or "")
    }
    if (
        not protected_keys
        or (require_complete_source and not source_complete)
    ):
        return selected, {
            "applied": False,
            "reason": (
                "source_required_stage_assignment_incomplete"
                if protected_keys and not source_complete
                else "source_required_stage_assignment_absent"
            ),
            "source_coverage": coverage,
            "protected_candidate_keys": sorted(protected_keys),
            "restored_candidate_keys": [],
            "replaced_candidate_keys": [],
            "target_max_count": int(target_max_count or 0),
            "within_target_max": not bool(
                int(target_max_count or 0) > 0
                and len(selected) > int(target_max_count or 0)
            ),
        }

    source_by_key = {
        _candidate_identity_key(item): dict(item)
        for item in source
        if _candidate_identity_key(item)
    }
    source_keys_by_case_id: dict[str, list[str]] = {}
    for key, item in source_by_key.items():
        case_id = _review_case_id(item)
        if case_id:
            source_keys_by_case_id.setdefault(case_id, []).append(key)

    def _source_key_for_case(item: dict[str, Any]) -> str:
        direct_key = _candidate_identity_key(item)
        if direct_key in source_by_key:
            return direct_key
        case_id_matches = source_keys_by_case_id.get(_review_case_id(item)) or []
        if len(case_id_matches) == 1:
            return case_id_matches[0]
        return direct_key

    selected_keys = {
        _source_key_for_case(item)
        for item in selected
        if _source_key_for_case(item)
    }
    restored_keys = [
        key
        for key in protected_keys
        if key not in selected_keys and key in source_by_key
    ]
    restored_keys.sort(
        key=lambda key: next(
            (
                int(index)
                for index, item in enumerate(source)
                if _candidate_identity_key(item) == key
            ),
            len(source),
        )
    )
    combined = [*selected, *(source_by_key[key] for key in restored_keys)]

    target_max = max(0, int(target_max_count or 0))
    replaced_keys: list[str] = []
    if target_max and len(combined) > target_max:
        excess = len(combined) - target_max
        removable_indices = [
            index
            for index in range(len(combined) - 1, -1, -1)
            if _source_key_for_case(combined[index]) not in protected_keys
        ]
        remove_indices = set(removable_indices[:excess])
        replaced_keys = [
            _source_key_for_case(combined[index])
            for index in sorted(remove_indices)
            if _source_key_for_case(combined[index])
        ]
        combined = [
            item for index, item in enumerate(combined) if index not in remove_indices
        ]

    final_coverage = evaluate_required_stage_candidate_coverage(
        combined,
        workflow_blueprints=workflow_blueprints,
    )
    return combined, {
        "applied": bool(restored_keys or replaced_keys),
        "reason": "required_stage_assignment_retained",
        "source_coverage": coverage,
        "final_coverage": final_coverage,
        "protected_candidate_keys": sorted(protected_keys),
        "restored_candidate_keys": restored_keys,
        "replaced_candidate_keys": replaced_keys,
        "target_max_count": target_max,
        "within_target_max": not bool(target_max and len(combined) > target_max),
    }


def apply_execution_plan_metadata(
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    coverage_mode: str = "",
    workflow_blueprints: list[dict[str, Any]] | None = None,
    trusted_workflow_contracts: list[dict[str, Any]] | None = None,
    current_requirement_workflow_blueprints: list[dict[str, Any]] | None = None,
    workflow_absence_declared: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_cases = _dict_case_copies(cases)
    if not candidate_cases:
        summary = _empty_execution_plan_summary()
        summary["workflow_absence_declared"] = bool(workflow_absence_declared)
        summary["independent_suite_executable"] = False
        if workflow_absence_declared:
            summary["main_chain_incomplete_reason"] = "workflow_absence_declared"
        return [], summary

    workflow_blueprints = list(workflow_blueprints or [])
    trusted_workflow_contracts = list(trusted_workflow_contracts or [])
    current_requirement_workflow_blueprints = list(current_requirement_workflow_blueprints or [])
    workflow_stage_meta_by_key: dict[str, dict[str, Any]] = {}
    workflow_stage_output_state: dict[str, str] = {}
    plan_workflow_blueprints, primary_workflow_resolution = _resolve_primary_workflow_blueprint(
        workflow_blueprints
    )
    main_chain_excluded_candidates: list[dict[str, str]] = []
    main_chain_incomplete_reason = ""
    _record_main_chain_exclusion = MainChainExclusionRecorder(
        main_chain_excluded_candidates,
        signature_fn=_signature,
        case_id_fn=_review_case_id,
        description_fn=lambda item: _case_text_field(item, "description"),
    )
    if not plan_workflow_blueprints:
        resolution_status = str(primary_workflow_resolution.get("status") or "")
        main_chain_incomplete_reason = (
            resolution_status
            if workflow_blueprints
            else "workflow_absence_declared"
            if workflow_absence_declared
            else "workflow_blueprint_missing"
        )

    (
        main_chain_stages,
        workflow_stage_meta_by_key,
        workflow_stage_output_state,
    ) = _main_chain_stages_from_blueprints(plan_workflow_blueprints)
    workflow_blueprint_source = _workflow_blueprint_source_label(
        workflow_blueprints,
    )

    stage_requirement_by_key = {
        stage_key: _stage_required_by_blueprint(
            stage_key,
            workflow_stage_meta_by_key.get(stage_key) or {},
            plan_workflow_blueprints,
        )
        for stage_key, _stage_label, _patterns in main_chain_stages
    }
    workflow_blueprint_contract_complete = _workflow_blueprint_contract_complete(
        plan_workflow_blueprints
    ) and all(value is not None for value in stage_requirement_by_key.values())
    stage_assignment_rows = [
        {
            "stage_key": stage_key,
            "stage_label": stage_label,
            "stage_order": stage_index + 1,
            "required": bool(stage_requirement_by_key.get(stage_key)),
        }
        for stage_index, (stage_key, stage_label, _patterns) in enumerate(main_chain_stages)
    ]
    case_by_candidate_key: dict[str, dict[str, Any]] = {}
    case_by_assignment_edge: dict[tuple[str, str], dict[str, Any]] = {}
    assignment_edges: list[dict[str, Any]] = []
    stable_candidates = sorted(
        candidate_cases,
        key=lambda item: (
            _candidate_identity_key(item),
            _review_case_id(item),
            _case_text_field(item, "description"),
        ),
    )
    for item in stable_candidates:
        candidate_key = _candidate_identity_key(item)
        if candidate_key:
            case_by_candidate_key.setdefault(candidate_key, dict(item))

    for stage_index, (stage_key, stage_label, patterns) in enumerate(main_chain_stages):
        step_meta = workflow_stage_meta_by_key.get(stage_key) or {}
        workflow_id = str(step_meta.get("workflow_id") or "").strip()
        for item in stable_candidates:
            signature = _signature(item)
            candidate_key = _candidate_identity_key(item)
            if not candidate_key:
                continue
            stage_candidates = _verified_workflow_stage_candidates(
                item,
                workflow_id=workflow_id,
                stage_key=stage_key,
            )
            if not stage_candidates:
                continue
            stage_candidate = stage_candidates[0]
            contract_conflicts = _structured_candidate_contract_conflicts(
                item,
                step_meta=step_meta,
                candidate=stage_candidate,
            )
            if contract_conflicts:
                _record_main_chain_exclusion(
                    item,
                    contract_conflicts[0],
                    stage_key=stage_key,
                )
                continue
            penalties: list[dict[str, Any]] = []
            verified_stage_ids = {
                str(candidate.get("stage_id") or "").strip()
                for candidate in _verified_workflow_stage_candidates(
                    item,
                    workflow_id=workflow_id,
                )
            }
            cross_stage_candidate_count = max(0, len(verified_stage_ids) - 1)
            if cross_stage_candidate_count:
                penalties.append(
                    {
                        "reason": "multiple_verified_workflow_stage_candidates",
                        "points": int(
                            min(
                                30,
                                cross_stage_candidate_count
                                * _STRUCTURED_EDGE_REASON_PENALTIES[
                                    "multiple_verified_workflow_stage_candidates"
                                ],
                            )
                        ),
                    }
                )
            unknown_source_count = unknown_precondition_source_count(item)
            if unknown_source_count:
                penalties.append(
                    {
                        "reason": "precondition_state_source_unknown",
                        "points": int(
                            min(
                                24,
                                unknown_source_count
                                * _STRUCTURED_EDGE_REASON_PENALTIES[
                                    "precondition_state_source_unknown"
                                ],
                            )
                        ),
                    }
                )
            module_candidate_count = len(_verified_module_keys(item))
            if module_candidate_count > 1:
                penalties.append(
                    {
                        "reason": "multiple_verified_module_candidates",
                        "points": int(
                            min(
                                24,
                                (module_candidate_count - 1)
                                * _STRUCTURED_EDGE_REASON_PENALTIES[
                                    "multiple_verified_module_candidates"
                                ],
                            )
                        ),
                    }
                )
            model_stage_confidence = float(stage_candidate.get("confidence") or 0.0)
            score = 100 + _priority_rank(item) + int(model_stage_confidence * 100)
            score -= sum(int(penalty.get("points") or 0) for penalty in penalties)
            diagnostic_text = " ".join(
                str(item.get(field) or "")
                for field in (
                    "test_module",
                    "description",
                    "preconditions",
                    "steps",
                    "test_input",
                    "expected_result",
                )
            ).lower()
            assignment_edges.append(
                {
                    "stage_key": stage_key,
                    "candidate_key": candidate_key,
                    "case_signature": signature,
                    "case_id": _review_case_id(item),
                    "score": int(score),
                    "text_diagnostic_match_score": int(
                        _pattern_match_score(diagnostic_text, patterns)
                    ),
                    "model_stage_confidence": float(model_stage_confidence),
                    "semantic_stage_kind": str(stage_candidate.get("stage_kind") or ""),
                    "penalties": penalties,
                }
            )
            case_by_assignment_edge[(stage_key, candidate_key)] = dict(item)

    stage_assignment = maximum_weight_stage_assignment(stage_assignment_rows, assignment_edges)
    best_selected_by_stage = [
        (
            str(row.get("stage_key") or ""),
            str(row.get("stage_label") or row.get("stage_key") or ""),
            dict(
                case_by_assignment_edge.get(
                    (
                        str(row.get("stage_key") or ""),
                        str(row.get("candidate_key") or ""),
                    )
                )
                or case_by_candidate_key[str(row.get("candidate_key") or "")]
            ),
        )
        for row in (stage_assignment.get("selected") or [])
        if str(row.get("candidate_key") or "") in case_by_candidate_key
    ]
    required_stage_id_set = {
        str(row.get("stage_key") or "")
        for row in stage_assignment_rows
        if row.get("required") is True and str(row.get("stage_key") or "")
    }
    required_selected_by_stage = [
        item for item in best_selected_by_stage if item[0] in required_stage_id_set
    ]
    main_chain_stage_kinds: list[str] = []
    selected_stage_state_conflicts: list[dict[str, Any]] = []
    if required_selected_by_stage:
        selected_stage_state_conflicts = _selected_stage_state_conflicts_helper(
            required_selected_by_stage,
            stage_meta_by_key=workflow_stage_meta_by_key,
            case_id_fn=_review_case_id,
        )
        for index in range(1, len(required_selected_by_stage)):
            previous_stage_key, _previous_label, previous_case = required_selected_by_stage[index - 1]
            current_stage_key, _current_label, current_case = required_selected_by_stage[index]
            continuity_reason = main_chain_precondition_conflict_reason(
                previous_case,
                current_case,
                previous_step_meta=workflow_stage_meta_by_key.get(previous_stage_key) or {},
                current_step_meta=workflow_stage_meta_by_key.get(current_stage_key) or {},
            )
            if continuity_reason:
                selected_stage_state_conflicts.append(
                    {
                        "prev_stage_key": previous_stage_key,
                        "curr_stage_key": current_stage_key,
                        "prev_case_id": _review_case_id(previous_case),
                        "curr_case_id": _review_case_id(current_case),
                        "reason": continuity_reason,
                    }
                )

    provisional_main_cases = [
        {
            **dict(item),
            "execution_group": "main_smoke",
            "main_chain_stage": stage_key,
            "main_chain_step": index,
            "workflow_id": str((workflow_stage_meta_by_key.get(stage_key) or {}).get("workflow_id") or ""),
            "source_state": str((workflow_stage_meta_by_key.get(stage_key) or {}).get("state_in") or ""),
            "target_state": str((workflow_stage_meta_by_key.get(stage_key) or {}).get("state_out") or ""),
        }
        for index, (stage_key, _stage_label, item) in enumerate(required_selected_by_stage, start=1)
    ]
    provisional_closure = _evaluate_declared_workflow_closure(
        provisional_main_cases,
        workflow_blueprints=plan_workflow_blueprints,
    )
    closure_ok = bool(provisional_closure.get("closure_satisfied"))
    closure_reasons = [
        str(reason).strip()
        for reason in (provisional_closure.get("failure_reasons") or [])
        if str(reason).strip()
    ]
    closure_reason = closure_reasons[0] if closure_reasons else ""
    for stage_key, _stage_label, _item in required_selected_by_stage:
        step_meta = workflow_stage_meta_by_key.get(stage_key) or {}
        explicit_kind = str(step_meta.get("stage_kind") or "").strip().lower()
        main_chain_stage_kinds.append(explicit_kind)
    required_gap_count = int(stage_assignment.get("required_gap_count") or 0)
    optional_branch_state_conflicts = _optional_branch_state_conflicts(
        plan_workflow_blueprints,
        required_stage_ids=sorted(
            required_stage_id_set,
            key=lambda stage_key: int(
                (workflow_stage_meta_by_key.get(stage_key) or {}).get("step_index") or 0
            ),
        ),
    )
    publishable_main_chain = bool(
        required_selected_by_stage
        and workflow_blueprint_contract_complete
        and required_gap_count == 0
        and not selected_stage_state_conflicts
        and not optional_branch_state_conflicts
        and closure_ok
    )
    if plan_workflow_blueprints and not workflow_blueprint_contract_complete:
        main_chain_incomplete_reason = "workflow_blueprint_incomplete"
    elif required_gap_count:
        main_chain_incomplete_reason = "required_stage_gap"
    elif selected_stage_state_conflicts:
        main_chain_incomplete_reason = "state_chain_conflict"
    elif optional_branch_state_conflicts:
        main_chain_incomplete_reason = "optional_branch_state_unreachable"
    elif required_selected_by_stage and not closure_ok:
        main_chain_incomplete_reason = closure_reason
    selected_by_stage = list(required_selected_by_stage) if publishable_main_chain else []
    selected_signatures = {
        _candidate_identity_key(item) for _key, _label, item in selected_by_stage
    }

    group_setup_map = default_group_setup_map()
    group_teardown_map = default_group_teardown_map()

    ordered_cases = _order_execution_plan_cases(
        candidate_cases,
        selected_by_stage,
        selected_signatures,
        _candidate_identity_key,
        _infer_group,
        _case_priority,
        _case_text_field,
    )

    annotated = _annotate_execution_plan_cases(
        ordered_cases,
        start_id=start_id,
        selected_by_stage=selected_by_stage,
        workflow_stage_meta_by_key=workflow_stage_meta_by_key,
        workflow_stage_output_state=workflow_stage_output_state,
        workflow_blueprints=workflow_blueprints,
        group_setup_map=group_setup_map,
        group_teardown_map=group_teardown_map,
    )

    annotated = materialize_final_case_state_fields(annotated)
    state_conflicts = validate_main_smoke_state_chain(annotated)
    semantic_conflicts: list[dict[str, Any]] = []
    summary = _build_execution_plan_metadata_summary(
        annotated,
        coverage_mode=coverage_mode,
        workflow_blueprints=workflow_blueprints,
        trusted_workflow_contracts=trusted_workflow_contracts,
        current_requirement_workflow_blueprints=current_requirement_workflow_blueprints,
        plan_workflow_blueprints=plan_workflow_blueprints,
        workflow_blueprint_source=workflow_blueprint_source,
        main_chain_stage_kinds=main_chain_stage_kinds,
        main_chain_incomplete_reason=main_chain_incomplete_reason,
        main_chain_excluded_candidates=main_chain_excluded_candidates,
        state_conflicts=state_conflicts,
        selected_stage_state_conflicts=selected_stage_state_conflicts,
        semantic_conflicts=semantic_conflicts,
        group_setup_map=group_setup_map,
        group_teardown_map=group_teardown_map,
    )
    assignment_selected_diagnostic = [
        {
            key: value
            for key, value in row.items()
            if key not in {"case_signature", "candidate_key"}
        }
        for row in (stage_assignment.get("selected") or [])
    ]
    assignment_soft_warnings = [
        {
            "stage_key": str(row.get("stage_key") or ""),
            "case_id": str(row.get("case_id") or ""),
            "reason": str(penalty.get("reason") or ""),
            "points": int(penalty.get("points") or 0),
        }
        for row in (stage_assignment.get("selected") or [])
        for penalty in (row.get("penalties") or [])
        if isinstance(penalty, dict) and str(penalty.get("reason") or "")
    ]
    candidate_soft_warnings = [
        {
            "stage_key": str(row.get("stage_key") or ""),
            "case_id": str(row.get("case_id") or ""),
            "reason": str(penalty.get("reason") or ""),
            "points": int(penalty.get("points") or 0),
        }
        for row in assignment_edges
        for penalty in (row.get("penalties") or [])
        if isinstance(penalty, dict) and str(penalty.get("reason") or "")
    ]
    summary["global_stage_assignment"] = {
        "algorithm": str(stage_assignment.get("algorithm") or ""),
        "stage_count": int(stage_assignment.get("stage_count") or 0),
        "candidate_edge_count": int(stage_assignment.get("candidate_edge_count") or 0),
        "selected_case_count": int(stage_assignment.get("selected_case_count") or 0),
        "required_gap_count": int(stage_assignment.get("required_gap_count") or 0),
        "optional_gap_count": int(stage_assignment.get("optional_gap_count") or 0),
        "total_score": int(stage_assignment.get("total_score") or 0),
        "selected": assignment_selected_diagnostic,
        "gaps": list(stage_assignment.get("gaps") or []),
        "soft_warnings": assignment_soft_warnings,
        "candidate_soft_warnings": candidate_soft_warnings[:100],
    }
    summary["best_assignment"] = assignment_selected_diagnostic
    summary["best_assignment_state_conflicts"] = list(selected_stage_state_conflicts)
    summary["optional_branch_state_conflicts"] = list(optional_branch_state_conflicts)
    summary["publishable_main_chain"] = bool(publishable_main_chain)
    summary["workflow_blueprint_contract_complete"] = bool(workflow_blueprint_contract_complete)
    summary["primary_workflow_resolution"] = dict(primary_workflow_resolution)
    summary["primary_workflow_id"] = str(
        primary_workflow_resolution.get("primary_workflow_id") or ""
    )
    summary["non_primary_workflow_ids"] = list(
        primary_workflow_resolution.get("non_primary_workflow_ids") or []
    )
    summary["workflow_absence_declared"] = bool(workflow_absence_declared and not plan_workflow_blueprints)
    summary["independent_suite_executable"] = bool(
        workflow_absence_declared
        and not plan_workflow_blueprints
        and not any(item.get("execution_group") == "main_smoke" for item in annotated)
        and bool(annotated)
    )
    return annotated, summary


__all__ = [
    "apply_execution_plan_metadata",
    "evaluate_required_stage_candidate_coverage",
    "retain_required_stage_assignment",
]
