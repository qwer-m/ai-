from __future__ import annotations

from typing import Any

from .case_access import (
    case_priority as _case_priority,
    case_text_field as _case_text_field,
)
from .execution_plan_validator import (
    main_chain_action_support_conflict_reason,
    materialize_final_case_state_fields,
    validate_main_smoke_semantic_alignment,
    validate_main_smoke_state_chain,
)
from .streaming_case_keys import (
    case_signature as _signature,
    review_case_id as _review_case_id,
)
from .streaming_execution_plan_helpers import (
    contains_any_token as _any,
    default_group_setup_map,
    default_group_teardown_map,
    default_main_chain_exclusion_token_sets as _default_main_chain_exclusion_token_sets,
    derive_workflow_blueprint_from_current_cases as _derive_workflow_blueprint_from_current_cases,
    empty_execution_plan_summary as _empty_execution_plan_summary,
    execution_case_text as _case_text,
    infer_group as _infer_group,
    infer_workflow_stage_kind as _workflow_stage_kind_from_text,
    MainChainExclusionRecorder,
    main_chain_closure_status as _main_chain_closure_status_helper,
    main_chain_exclusion_reason as _main_chain_exclusion_reason_helper,
    main_chain_stages_from_blueprints as _main_chain_stages_from_blueprints,
    materialize_workflow_contract_case as _materialize_workflow_contract_case,
    normalize_actor_role_value as _normalize_actor_role,
    pattern_match_score as _pattern_match_score,
    priority_rank as _priority_rank,
    selected_stage_state_conflicts as _selected_stage_state_conflicts_helper,
    workflow_blueprint_source_label as _workflow_blueprint_source_label,
    workflow_bridge_case as _workflow_bridge_case,
)
from .streaming_execution_plan_metadata_helpers import (
    annotate_execution_plan_cases as _annotate_execution_plan_cases,
)
from .streaming_execution_plan_ordering import order_execution_plan_cases as _order_execution_plan_cases
from .streaming_execution_plan_summary import (
    build_execution_plan_metadata_summary as _build_execution_plan_metadata_summary,
)
from .streaming_postprocess_utils import _clip_text, _dict_case_copies
from .streaming_reasoning_quality import reasoning_leakage_hits as _reasoning_leakage_hits


def apply_execution_plan_metadata(
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    coverage_mode: str = "",
    workflow_blueprints: list[dict[str, Any]] | None = None,
    trusted_workflow_contracts: list[dict[str, Any]] | None = None,
    current_requirement_workflow_blueprints: list[dict[str, Any]] | None = None,
    authoritative_workflow_blueprints: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_cases = _dict_case_copies(cases)
    if not candidate_cases:
        return [], _empty_execution_plan_summary()

    workflow_blueprints = list(workflow_blueprints or [])
    trusted_workflow_contracts = list(trusted_workflow_contracts or [])
    current_requirement_workflow_blueprints = list(current_requirement_workflow_blueprints or [])
    authoritative_workflow_blueprints = list(authoritative_workflow_blueprints or [])

    workflow_stage_meta_by_key: dict[str, dict[str, Any]] = {}
    workflow_stage_output_state: dict[str, str] = {}
    plan_workflow_blueprints = list(workflow_blueprints)

    low_chain_tokens = (
        "失败",
        "异常",
        "超时",
        "错误",
        "拒绝",
        "不通过",
        "不可点击",
        "置灰",
        "空状态",
        "无数据",
        "上限",
        "下限",
        "格式",
        "大小",
        "边界",
        "failure",
        "failed",
        "timeout",
        "error",
        "invalid",
        "empty",
        "limit",
        "boundary",
    )

    main_chain_token_sets = _default_main_chain_exclusion_token_sets()
    analytics_tokens = main_chain_token_sets["analytics_tokens"]
    destructive_action_tokens = main_chain_token_sets["destructive_action_tokens"]
    blocking_negative_tokens = main_chain_token_sets["blocking_negative_tokens"]
    boundary_capacity_tokens = main_chain_token_sets["boundary_capacity_tokens"]
    display_only_tokens = main_chain_token_sets["display_only_tokens"]
    downstream_visibility_tokens = main_chain_token_sets["downstream_visibility_tokens"]
    main_chain_excluded_candidates: list[dict[str, str]] = []
    main_chain_incomplete_reason = ""
    derived_workflow_debug: dict[str, Any] = {
        "candidate_total": int(len(candidate_cases)),
        "action_state_candidate_count": 0,
        "primary_candidate_count": 0,
        "fallback_candidate_count": 0,
        "selected_candidate_count": 0,
        "closure_reason": "",
    }

    _record_main_chain_exclusion = MainChainExclusionRecorder(
        main_chain_excluded_candidates,
        signature_fn=_signature,
        case_id_fn=_review_case_id,
        description_fn=lambda item: _case_text_field(item, "description"),
    )
    main_chain_exclusion_reason_kwargs = {
        "workflow_blueprints_present": bool(workflow_blueprints),
        "analytics_tokens": analytics_tokens,
        "destructive_action_tokens": destructive_action_tokens,
        "boundary_capacity_tokens": boundary_capacity_tokens,
        "blocking_negative_tokens": blocking_negative_tokens,
        "display_only_tokens": display_only_tokens,
        "downstream_visibility_tokens": downstream_visibility_tokens,
        "reasoning_leakage_fn": _reasoning_leakage_hits,
        "semantic_alignment_fn": validate_main_smoke_semantic_alignment,
        "action_support_conflict_fn": main_chain_action_support_conflict_reason,
    }

    if not plan_workflow_blueprints:
        derived_workflow_result = _derive_workflow_blueprint_from_current_cases(
            candidate_cases,
            exclusion_reason_fn=lambda item: _main_chain_exclusion_reason_helper(
                item,
                **main_chain_exclusion_reason_kwargs,
            ),
            record_exclusion_fn=_record_main_chain_exclusion,
            case_id_fn=_review_case_id,
            stage_meta_by_key=workflow_stage_meta_by_key,
            closure_status_fn=_main_chain_closure_status_helper,
        )
        derived_workflow_debug = dict(derived_workflow_result.get("debug") or derived_workflow_debug)
        if derived_workflow_result.get("incomplete_reason"):
            main_chain_incomplete_reason = str(derived_workflow_result.get("incomplete_reason") or "")
        derived_blueprint = derived_workflow_result.get("blueprint")
        if derived_blueprint is not None:
            plan_workflow_blueprints = [derived_blueprint]

    (
        main_chain_stages,
        workflow_stage_meta_by_key,
        workflow_stage_output_state,
    ) = _main_chain_stages_from_blueprints(plan_workflow_blueprints)
    workflow_blueprint_source = _workflow_blueprint_source_label(
        workflow_blueprints,
        plan_workflow_blueprints,
    )

    selected_by_stage: list[tuple[str, str, dict[str, Any]]] = []
    selected_signatures: set[str] = set()
    strict_blueprint_semantic_filter = bool(workflow_blueprints)
    semantic_filter_main_chain = bool(plan_workflow_blueprints)
    for stage_key, stage_label, patterns in main_chain_stages:
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for index, item in enumerate(candidate_cases):
            signature = _signature(item)
            if not signature or signature in selected_signatures:
                continue
            text = _case_text(item)
            match_score = _pattern_match_score(text, patterns)
            if match_score <= 0:
                continue
            exclusion_reason = _main_chain_exclusion_reason_helper(
                item,
                step_meta=workflow_stage_meta_by_key.get(stage_key) or {},
                stage_label=stage_label,
                **main_chain_exclusion_reason_kwargs,
            )
            if exclusion_reason:
                _record_main_chain_exclusion(item, exclusion_reason, stage_key=stage_key)
                continue
            step_meta = workflow_stage_meta_by_key.get(stage_key) or {}
            if bool(step_meta.get("exclude_failure_steps")) and _any(text, low_chain_tokens):
                _record_main_chain_exclusion(item, "failure_step_excluded_by_blueprint", stage_key=stage_key)
                continue
            expected_stage_kind = str(step_meta.get("stage_kind") or "").strip().lower()
            if not expected_stage_kind:
                expected_stage_kind = _workflow_stage_kind_from_text(
                    " ".join(
                        [
                            str(stage_label or ""),
                            str(step_meta.get("label") or ""),
                            str(step_meta.get("action") or ""),
                            str(step_meta.get("assertion") or ""),
                            str(step_meta.get("state_out") or ""),
                            str(step_meta.get("state_out") or "").replace("_", " "),
                        ]
                    )
                )
            candidate_stage_kind = _workflow_stage_kind_from_text(text)
            if semantic_filter_main_chain:
                semantic_probe = dict(item)
                semantic_probe["execution_group"] = "main_smoke"
                semantic_probe["main_chain_stage_kind"] = expected_stage_kind
                semantic_probe["main_chain_stage_label"] = str(
                    step_meta.get("label") or stage_label or ""
                ).strip()
                semantic_probe["action"] = str(
                    step_meta.get("action") or stage_label or ""
                ).strip()
                semantic_probe["role"] = _normalize_actor_role(
                    step_meta.get("actor") or item.get("role"),
                    fallback_text=text,
                )
                semantic_conflicts = validate_main_smoke_semantic_alignment([semantic_probe])
                if semantic_conflicts:
                    first_reason = str(semantic_conflicts[0].get("reason") or "main_chain_semantic_conflict")
                    _record_main_chain_exclusion(item, first_reason, stage_key=stage_key)
                    continue
                action_support_reason = main_chain_action_support_conflict_reason(semantic_probe)
                if action_support_reason:
                    _record_main_chain_exclusion(item, action_support_reason, stage_key=stage_key)
                    continue
            score = _priority_rank(item) + min(80, match_score)
            if expected_stage_kind in {"commit", "downstream_visibility"}:
                if candidate_stage_kind == expected_stage_kind:
                    score += 30
                elif candidate_stage_kind in {"commit", "downstream_visibility", "consume", "completion_sync"}:
                    score -= 45
            if _any(text, low_chain_tokens):
                score -= 35
            state_out = str(step_meta.get("state_out") or "").lower()
            assertion = str(step_meta.get("assertion") or "").lower()
            if state_out and state_out in text:
                score += 8
            if assertion and _clip_text(assertion, 40) in text:
                score += 8
            ranked.append((score, -index, item))
        if not ranked:
            continue
        ranked.sort(reverse=True)
        best = dict(ranked[0][2])
        selected_signature = _signature(best)
        selected_signatures.add(selected_signature)
        selected_by_stage.append((stage_key, stage_label, best))

    selected_stage_keys = {stage_key for stage_key, _stage_label, _item in selected_by_stage}
    stage_label_by_key = {stage_key: stage_label for stage_key, stage_label, _patterns in main_chain_stages}

    if selected_by_stage or authoritative_workflow_blueprints:
        bridged_by_stage: list[tuple[str, str, dict[str, Any]]] = []
        current_selected = {stage_key for stage_key, _label, _item in selected_by_stage}
        selected_by_stage_map = {stage_key: (stage_label, item) for stage_key, stage_label, item in selected_by_stage}
        allow_contract_materialization = bool(strict_blueprint_semantic_filter and authoritative_workflow_blueprints)
        for stage_key, stage_label, _patterns in main_chain_stages:
            existing = selected_by_stage_map.get(stage_key)
            if existing:
                current_selected.add(stage_key)
                selected_stage_keys.add(stage_key)
                bridged_by_stage.append((stage_key, existing[0], existing[1]))
                continue
            if not strict_blueprint_semantic_filter:
                continue
            if allow_contract_materialization:
                bridge_candidate = _workflow_bridge_case(
                    stage_key,
                    stage_meta_by_key=workflow_stage_meta_by_key,
                    main_chain_stages=main_chain_stages,
                    selected_stage_keys=selected_stage_keys,
                    available_stage_keys=current_selected,
                )
                bridge = (
                    _materialize_workflow_contract_case(
                        stage_key,
                        workflow_stage_meta_by_key.get(stage_key) or {},
                    )
                    if bridge_candidate is not None
                    else None
                )
            else:
                bridge = _workflow_bridge_case(
                    stage_key,
                    stage_meta_by_key=workflow_stage_meta_by_key,
                    main_chain_stages=main_chain_stages,
                    selected_stage_keys=selected_stage_keys,
                    available_stage_keys=current_selected,
                )
            if bridge is not None:
                if strict_blueprint_semantic_filter:
                    if not bool(bridge.get("workflow_contract_materialized_case")):
                        _record_main_chain_exclusion(bridge, "bridge_case_not_public_final_case", stage_key=stage_key)
                        continue
                current_selected.add(stage_key)
                selected_stage_keys.add(stage_key)
                bridged_by_stage.append((stage_key, stage_label_by_key.get(stage_key, stage_label), bridge))
        selected_by_stage = bridged_by_stage

    selected_by_stage_source = workflow_blueprint_source
    main_chain_stage_kinds: list[str] = []
    selected_stage_state_conflicts: list[dict[str, Any]] = []
    if selected_by_stage and strict_blueprint_semantic_filter:
        selected_stage_state_conflicts = _selected_stage_state_conflicts_helper(
            selected_by_stage,
            stage_meta_by_key=workflow_stage_meta_by_key,
            case_id_fn=_review_case_id,
        )
        if selected_stage_state_conflicts:
            main_chain_incomplete_reason = "state_chain_conflict"
            conflicted_stage_keys = {
                str(conflict.get("prev_stage_key") or "")
                for conflict in selected_stage_state_conflicts
                if str(conflict.get("prev_stage_key") or "")
            } | {
                str(conflict.get("curr_stage_key") or "")
                for conflict in selected_stage_state_conflicts
                if str(conflict.get("curr_stage_key") or "")
            }
            for excluded_stage_key, _excluded_stage_label, excluded_item in selected_by_stage:
                if str(excluded_stage_key) in conflicted_stage_keys:
                    _record_main_chain_exclusion(
                        excluded_item,
                        "state_bridge_missing",
                        stage_key=excluded_stage_key,
                    )
            selected_by_stage = []
            selected_signatures.clear()
    if selected_by_stage:
        closure_ok, closure_reason, main_chain_stage_kinds = _main_chain_closure_status_helper(
            selected_by_stage,
            stage_meta_by_key=workflow_stage_meta_by_key,
            source=selected_by_stage_source,
        )
        if not closure_ok:
            main_chain_incomplete_reason = closure_reason
            for excluded_stage_key, _excluded_stage_label, excluded_item in selected_by_stage:
                _record_main_chain_exclusion(
                    excluded_item,
                    "state_bridge_missing",
                    stage_key=excluded_stage_key,
                )
            selected_by_stage = []
            selected_signatures.clear()

    group_setup_map = default_group_setup_map()
    group_teardown_map = default_group_teardown_map()

    ordered_cases = _order_execution_plan_cases(
        candidate_cases,
        selected_by_stage,
        selected_signatures,
        _signature,
        _infer_group,
        _case_priority,
        _case_text_field,
    )

    annotated = _annotate_execution_plan_cases(
        ordered_cases,
        start_id=start_id,
        selected_by_stage=selected_by_stage,
        selected_by_stage_source=selected_by_stage_source,
        workflow_stage_meta_by_key=workflow_stage_meta_by_key,
        workflow_stage_output_state=workflow_stage_output_state,
        workflow_blueprints=workflow_blueprints,
        group_setup_map=group_setup_map,
        group_teardown_map=group_teardown_map,
        analytics_tokens=analytics_tokens,
        destructive_action_tokens=destructive_action_tokens,
        blocking_negative_tokens=blocking_negative_tokens,
        boundary_capacity_tokens=boundary_capacity_tokens,
    )

    annotated = materialize_final_case_state_fields(annotated)
    state_conflicts = validate_main_smoke_state_chain(annotated)
    semantic_conflicts = validate_main_smoke_semantic_alignment(annotated)
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
        derived_workflow_debug=derived_workflow_debug,
        main_chain_excluded_candidates=main_chain_excluded_candidates,
        state_conflicts=state_conflicts,
        selected_stage_state_conflicts=selected_stage_state_conflicts,
        semantic_conflicts=semantic_conflicts,
        group_setup_map=group_setup_map,
        group_teardown_map=group_teardown_map,
    )
    return annotated, summary


__all__ = ["apply_execution_plan_metadata"]
