from __future__ import annotations

from typing import Any

from .priority_anchor_rules import has_explicit_blocking_or_critical
from .streaming_case_keys import candidate_identity_key as _candidate_identity_key
from .streaming_execution_plan_helpers import (
    fixture_for_case as _fixture_for_case,
    infer_data_state as _infer_data_state,
    infer_group as _infer_group,
    infer_role as _infer_role,
    normalize_actor_role_value as _normalize_actor_role,
    session_key_for_role as _session_key_for_role,
    setup_hint as _setup_hint,
    workflow_transition_for_case as _workflow_transition_for_case_helper,
)


def annotate_execution_plan_cases(
    ordered_cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    selected_by_stage: list[tuple[str, str, dict[str, Any]]] | None = None,
    workflow_stage_meta_by_key: dict[str, dict[str, Any]] | None = None,
    workflow_stage_output_state: dict[str, str] | None = None,
    workflow_blueprints: list[dict[str, Any]] | None = None,
    group_setup_map: dict[str, str] | None = None,
    group_teardown_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    safe_start = max(1, int(start_id or 1))
    stage_meta_by_key = dict(workflow_stage_meta_by_key or {})
    stage_output_state = dict(workflow_stage_output_state or {})
    setup_map = dict(group_setup_map or {})
    teardown_map = dict(group_teardown_map or {})
    selected = list(selected_by_stage or [])

    annotated: list[dict[str, Any]] = []
    previous_main_id = ""
    previous_main_result = ""
    previous_main_role = ""
    main_chain_stage_by_candidate = {
        _candidate_identity_key(item): (stage_key, stage_label, index + 1)
        for index, (stage_key, stage_label, item) in enumerate(selected)
    }
    for offset, item in enumerate(ordered_cases):
        updated = dict(item)
        candidate_key = _candidate_identity_key(updated)
        new_id = f"TC-{safe_start + offset:03d}"
        updated["id"] = new_id
        stage_info = main_chain_stage_by_candidate.get(candidate_key)
        in_main_chain = bool(stage_info)
        stage_key = str(stage_info[0]) if stage_info else ""
        group = _infer_group(updated, in_main_chain=in_main_chain)
        step_meta_for_role = stage_meta_by_key.get(stage_key) or {}
        role = (
            _normalize_actor_role(step_meta_for_role.get("actor"))
            if in_main_chain and str(step_meta_for_role.get("actor") or "").strip()
            else _infer_role(updated)
        )
        if in_main_chain and str(step_meta_for_role.get("source_actor_role") or "").strip():
            updated["source_actor_role"] = str(step_meta_for_role.get("source_actor_role") or "").strip()
        depends_on = [previous_main_id] if in_main_chain and previous_main_id else []
        data_state = _infer_data_state(
            updated,
            stage_key=stage_key,
            stage_output_state=stage_output_state,
        )
        fixture = _fixture_for_case(updated, group, data_state)
        updated["execution_group"] = group
        updated["execution_sequence"] = int(offset + 1)
        updated["chain_id"] = "main_smoke_chain" if in_main_chain else f"{group}_independent"
        updated["depends_on"] = depends_on
        updated["role"] = role
        updated["session_key"] = _session_key_for_role(role)
        role_changed = bool(in_main_chain and previous_main_role and role != previous_main_role)
        updated["role_switch_strategy"] = (
            "switch_to_dedicated_role_session" if role_changed else "reuse_role_session"
        )
        updated["data_state"] = data_state
        updated["isolation_required"] = bool(not in_main_chain)
        updated["fixture_key"] = fixture["fixture_key"]
        updated["fixture_builder"] = fixture["fixture_builder"]
        updated["cleanup_policy"] = fixture["cleanup_policy"]
        updated["group_setup"] = setup_map.get(group, "seed_case_dataset()")
        updated["group_teardown"] = teardown_map.get(group, "cleanup_case_dataset()")
        if group == "permission" and fixture["fixture_key"] in {"browser_permission_state", "permission_state_dataset"}:
            updated["group_setup"] = fixture["fixture_builder"]
            updated["group_teardown"] = (
                "reset_browser_permissions()"
                if fixture["fixture_key"] == "browser_permission_state"
                else "reset_permission_state_dataset()"
            )
        updated["setup_hint"] = _setup_hint(
            updated,
            in_main_chain=in_main_chain,
            previous_id=previous_main_id,
            previous_result=previous_main_result,
        )
        if in_main_chain and not depends_on:
            updated["setup_hint"] = f"组级准备：{updated['group_setup']}；然后执行本主链路首个用例"
        updated["teardown_hint"] = (
            "主链路末尾执行清理；中间步骤不清理，供下一条用例复用状态"
            if in_main_chain
            else "执行后恢复本用例改动的数据，避免影响其他独立用例"
        )
        if stage_info:
            updated["main_chain_stage"] = str(stage_info[0])
            updated["main_chain_stage_label"] = str(stage_info[1])
            updated["main_chain_step"] = int(stage_info[2])
            step_meta = stage_meta_by_key.get(str(stage_info[0]) or "") or {}
            transition = _workflow_transition_for_case_helper(
                updated,
                step_meta=step_meta,
                stage_label=str(stage_info[1]),
                workflow_blueprints_present=bool(workflow_blueprints),
            )
            expected_stage_kind = str(step_meta.get("stage_kind") or "").strip().lower()
            if expected_stage_kind:
                transition = dict(transition)
                transition["stage_kind"] = expected_stage_kind
            updated["workflow_transition"] = transition
            for transition_field in (
                "workflow_id",
                "source_state",
                "action",
                "target_state",
                "path_type",
                "blocking",
                "critical",
                "destructive",
                "can_advance_main_flow",
                "state_transition_confidence",
            ):
                if transition.get(transition_field) not in (None, ""):
                    updated[transition_field] = transition[transition_field]
            updated["main_chain_stage_kind"] = str(transition.get("stage_kind") or "").strip()
            declared_critical = bool(
                step_meta.get("critical") is True
                or step_meta.get("blocking") is True
                or step_meta.get("destructive") is True
            )
            if declared_critical and bool(step_meta.get("main_path_step", True)):
                updated["priority"] = "P0"
                updated["priority_final"] = "P0"
                updated["priority_decision_state"] = "overridden"
                updated["priority_decision_source"] = "execution_plan_declared_critical_p0"
            elif (
                str(updated.get("priority") or "").strip().upper() == "P0"
                and not has_explicit_blocking_or_critical(updated)
            ):
                updated["priority"] = "P1"
                updated["priority_final"] = "P1"
                updated["priority_decision_state"] = "overridden"
                updated["priority_decision_source"] = "execution_plan_unstructured_p0_demoted"
        elif (
            str(updated.get("priority") or "").strip().upper() == "P0"
            and not has_explicit_blocking_or_critical(updated)
        ):
            updated["priority"] = "P1"
            updated["priority_final"] = "P1"
            updated["priority_decision_state"] = "overridden"
            updated["priority_decision_source"] = "execution_plan_non_main_p0_demoted"
        annotated.append(updated)
        if in_main_chain:
            previous_main_id = new_id
            previous_main_result = str(updated.get("expected_result") or "")
            previous_main_role = role

    return annotated


__all__ = ["annotate_execution_plan_cases"]
