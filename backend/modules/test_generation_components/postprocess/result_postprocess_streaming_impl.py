from __future__ import annotations

from typing import Any, Callable, Iterator

from ..coverage.coverage_analyzer import case_complexity_profile
from .execution_plan_validator import (
    main_chain_action_support_conflict_reason,
    materialize_final_case_state_fields,
    validate_main_smoke_semantic_alignment,
    validate_main_smoke_state_chain,
)
from .priority_anchor_rules import (
    enforce_main_path_p0_anchors as _enforce_main_path_p0_anchors_rule,
)
from .result_postprocess_priority_semantics import apply_priority_semantics_to_cases, score_case_priority
from .streaming_priority_semantics import (
    apply_coverage_priority_semantics as _apply_coverage_priority_semantics,
    coverage_priority_semantics_result as _coverage_priority_semantics_result,
)
from .streaming_postprocess_utils import (
    RETRYABLE_RESPONSE_ERROR_REASONS as _RETRYABLE_RESPONSE_ERROR_REASONS,
    _case_execution_group,
    _cases_and_trace_from_result,
    _client_response_metadata,
    _clip_text,
    _dict_case_count,
    _dict_case_copies,
    _dict_case_items,
    _flow_profile_with_scenario_policy,
    _json_for_prompt,
    LowQualityFilterStatsAccumulator,
    _merged_unique_total,
    _project_profile_debug_fields,
    _resolve_expected_min_floor_for_recovery,
    _rule_diagnostics_payload,
    _select_review_model,
    build_feedback_control_debug_payload as _build_feedback_control_debug_payload,
    build_flow_project_profile_for_governance as _build_flow_project_profile_for_governance,
    resolve_generation_coverage_state as _resolve_generation_coverage_state,
)
from .streaming_case_quality import (
    filter_final_quality_cases as _filter_final_quality_cases,
    filter_low_quality_cases_with_stats as _filter_low_quality_cases_with_stats,
    strip_case_meta_list as _strip_case_meta_list,
)
from .streaming_control_context import resolve_streaming_control_context
from .streaming_case_keys import (
    case_coverage_bucket as _coverage_bucket,
    case_focus_score as _focus_score,
    case_signature as _signature,
    dedupe_by_final_description as _dedupe_by_final_description,
    review_case_id as _review_case_id,
)
from .case_access import (
    case_priority as _case_priority,
    case_text_field as _case_text_field,
)
from .streaming_expected_result_quality import (
    is_non_assertable_expected_result as _is_non_assertable_expected_result,
    looks_truncated_text as _looks_truncated_text,
)
from .streaming_execution_plan_helpers import (
    contains_any_token as _any,
    default_group_setup_map,
    default_group_teardown_map,
    default_main_chain_exclusion_token_sets as _default_main_chain_exclusion_token_sets,
    derive_workflow_blueprint_from_current_cases as _derive_workflow_blueprint_from_current_cases,
    empty_execution_plan_summary as _empty_execution_plan_summary,
    execution_case_text as _case_text,
    fixture_for_case as _fixture_for_case,
    infer_data_state as _infer_data_state,
    infer_group as _infer_group,
    infer_role as _infer_role,
    infer_workflow_stage_kind as _workflow_stage_kind_from_text,
    is_core_result_output_anchor as _is_core_result_output_anchor,
    is_low_value_main_chain_p0 as _is_low_value_main_chain_p0,
    is_student_observation_projection as _is_student_observation_projection,
    MainChainExclusionRecorder,
    main_chain_state_overrides_for_current_generation as _main_chain_state_overrides_for_current_generation,
    main_chain_closure_status as _main_chain_closure_status_helper,
    main_chain_exclusion_reason as _main_chain_exclusion_reason_helper,
    main_chain_stages_from_blueprints as _main_chain_stages_from_blueprints,
    materialize_workflow_contract_case as _materialize_workflow_contract_case,
    normalize_actor_role_value as _normalize_actor_role,
    pattern_match_score as _pattern_match_score,
    priority_rank as _priority_rank,
    selected_stage_state_conflicts as _selected_stage_state_conflicts_helper,
    session_key_for_role as _session_key_for_role,
    setup_hint as _setup_hint,
    workflow_bridge_case as _workflow_bridge_case,
    workflow_transition_for_case as _workflow_transition_for_case_helper,
    workflow_blueprint_source_label as _workflow_blueprint_source_label,
)
from .streaming_execution_plan_ordering import (
    apply_final_independent_case_ordering as _apply_final_independent_case_ordering,
    order_execution_plan_cases as _order_execution_plan_cases,
)
from .streaming_execution_plan_summary import (
    build_execution_plan_metadata_summary as _build_execution_plan_metadata_summary,
)
from .streaming_flow_conflicts import (
    filter_cases_conflicting_with_confirmed_flow_facts as _filter_cases_conflicting_with_confirmed_flow_facts,
)
from .streaming_final_case_summary import (
    final_dedup_priority_summary_fields as _final_dedup_priority_summary_fields,
    final_case_breakdown as _final_case_breakdown,
    resolve_final_duplicate_project_profile as _resolve_final_duplicate_project_profile,
    review_flow_structure_summary_fields as _review_flow_structure_summary_fields,
    summarize_final_description_dedup_and_priority_breakdown as _summarize_final_description_dedup_and_priority_breakdown,
)
from .streaming_generation_summary import (
    build_convergence_debug as _build_convergence_debug,
    build_generation_summary as _build_generation_summary,
    build_stream_postprocess_result_payload as _build_stream_postprocess_result_payload,
    derive_convergence_reason_state as _derive_convergence_reason_state,
    derive_final_coverage_convergence_inputs as _derive_final_coverage_convergence_inputs,
    resolve_append_reference_counts as _resolve_append_reference_counts,
    resolve_completion_reason_lists as _resolve_completion_reason_lists,
    resolve_final_stage_pruning_counts as _resolve_final_stage_pruning_counts,
    resolve_generation_target_satisfaction as _resolve_generation_target_satisfaction,
    resolve_underfill_diagnostics as _resolve_underfill_diagnostics,
)
from .streaming_judge_summary import (
    build_judge_decision_table_payload as _build_judge_decision_table_payload,
    build_judge_summary_payload as _build_judge_summary_payload,
)
from .streaming_review_mapping import (
    REASON_REPAIR_DROP_REASONS as _REASON_REPAIR_DROP_REASONS,
    REVIEW_DROP_REASONS as _REVIEW_DROP_REASONS,
    case_review_brief as _case_review_brief,
    map_review_selection_with_reasons as _map_review_selection_with_reasons,
)
from .streaming_review_reason_repair import (
    analyze_reason_repair_payload as _analyze_reason_repair_payload,
    build_compact_reason_repair_prompt as _build_compact_reason_repair_prompt,
    build_reason_repair_candidates as _build_reason_repair_candidates,
)
from .streaming_review_retry import (
    analyze_review_retry_payload as _analyze_review_retry_payload,
    build_compact_review_retry_prompt as _build_compact_review_retry_prompt,
    default_review_llm_runtime_debug as _default_review_llm_runtime_debug,
    review_retry_payload_debug_counts as _review_payload_debug_counts,
)
from .streaming_review_decision_table import (
    build_review_candidate_row_base_fields as _build_review_candidate_row_base_fields,
    build_review_candidate_row_diagnostic_fields as _build_review_candidate_row_diagnostic_fields,
    build_review_decision_table_context as _build_review_decision_table_context,
    resolve_review_candidate_drop_decision as _resolve_review_candidate_drop_decision,
    resolve_review_priority_fields as _resolve_review_priority_fields,
    resolve_review_priority_summary_flags as _resolve_review_priority_summary_flags,
    resolve_review_row_coverage_retention_fields as _resolve_review_row_coverage_retention_fields,
)
from .streaming_review_selection import (
    apply_append_target_cap as _apply_append_target_cap,
    build_review_decision_summary_payload as _build_review_decision_summary_payload,
    build_review_selection_constraints as _build_review_selection_constraints,
    enforce_review_selection_constraints as _enforce_review_selection_constraints,
    hit_must_cover_rule as _hit_must_cover_rule,
    is_high_signal as _is_high_signal,
    merge_review_selection_candidates as _merge_review_selection_candidates,
    rank_review_case_for_fill as _rank_review_case_for_fill,
    recover_post_rerank_shortfall as _recover_post_rerank_shortfall,
    recover_review_selection_shortfall as _recover_review_selection_shortfall,
    resolve_review_llm_drop_reason_maps as _resolve_review_llm_drop_reason_maps,
    resolve_review_post_rerank_floor_count as _resolve_review_post_rerank_floor_count,
    review_must_keep_reasons as _review_must_keep_reasons,
    review_llm_drop_summary_fields as _review_llm_drop_summary_fields,
    split_review_candidate_pool as _split_review_candidate_pool,
    summarize_review_decision_counts as _summarize_review_decision_counts,
    summarize_review_llm_drop_diagnostics as _summarize_review_llm_drop_diagnostics,
)
from .streaming_priority_rebuild import preserve_review_priority_demotions as _preserve_review_priority_demotions
from .streaming_reasoning_quality import reasoning_leakage_hits as _reasoning_leakage_hits
from .streaming_rule_rerank import rerank_and_cap_by_rule as _rerank_and_cap_by_rule
from .streaming_rule_keys import extract_rule_keys as _extract_rule_keys
from .streaming_text_match import CaseGovernanceMatcher
from .streaming_ui_like import apply_ui_like_ratio_postprocess_cap as _apply_ui_like_ratio_postprocess_cap


def stream_postprocess_cases(
    *,
    client: Any,
    requirement: str,
    base_prompt: str,
    kb_context: str,
    full_content: str,
    expected_count: int,
    append: bool,
    existing_cases: list[dict[str, Any]],
    existing_unique_count: int,
    start_id: int,
    db: Any,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    count_unique_test_cases_fn: Callable[[list[dict[str, Any]]], int],
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
    build_supplement_closed_loop_instruction_fn: Callable[..., str],
    current_biz_key: str = "",
    multi_pass: bool = True,
    generation_mode: str = "",
    feedback_control_state: dict[str, Any] | None = None,
    requirement_semantics_context: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream postprocess: dedup + quality filtering + rerank + convergence diagnostics."""

    from ..coverage.coverage_analyzer import (
        analyze_case_structure,
        analyze_coverage,
        govern_cases_by_flow_structure,
        summarize_duplicate_excess_by_policy,
    )
    from ..prompting.prompt_orchestration import (
        build_gap_fill_prompt,
        build_review_select_prompt,
    )
    control_context = resolve_streaming_control_context(feedback_control_state)
    control_state = control_context.control_state
    generation_coverage_profile = control_context.generation_coverage_profile
    fact_profile = control_context.fact_profile
    project_profile = control_context.project_profile
    manual_quality_profile = control_context.manual_quality_profile
    generation_coverage_mode = control_context.generation_coverage_mode
    generation_target_case_range = control_context.generation_target_case_range
    priority_pool_redundant_scenario_caps = control_context.priority_pool_redundant_scenario_caps
    must_cover_rule_set = control_context.must_cover_rule_set
    forbidden_patterns = control_context.forbidden_patterns
    reuse_risks = control_context.reuse_risks
    soft_constraints = control_context.soft_constraints
    quality_fix_hints = control_context.quality_fix_hints
    workflow_blueprints = control_context.workflow_blueprints
    trusted_workflow_contracts = control_context.trusted_workflow_contracts
    current_requirement_workflow_blueprints = control_context.current_requirement_workflow_blueprints
    authoritative_workflow_blueprints = control_context.authoritative_workflow_blueprints

    case_governance_matcher = CaseGovernanceMatcher.from_raw(
        forbidden_patterns=forbidden_patterns,
        reuse_risks=reuse_risks,
        soft_constraints=soft_constraints,
        quality_fix_hints=quality_fix_hints,
    )
    _violates_forbidden_pattern = case_governance_matcher.violates_forbidden_pattern
    _hits_soft_constraint = case_governance_matcher.hits_soft_constraint
    _hits_reuse_risk = case_governance_matcher.hits_reuse_risk
    _satisfies_quality_hint = case_governance_matcher.satisfies_quality_hint

    def _apply_execution_plan_metadata(
        cases: list[dict[str, Any]],
        *,
        start_id: int = 1,
        coverage_mode: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        candidate_cases = _dict_case_copies(cases)
        if not candidate_cases:
            return [], _empty_execution_plan_summary()

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

        stage_output_state = dict(workflow_stage_output_state)

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

        safe_start = max(1, int(start_id or 1))
        annotated: list[dict[str, Any]] = []
        previous_main_id = ""
        previous_main_result = ""
        main_chain_stage_by_signature = {
            _signature(item): (stage_key, stage_label, index + 1)
            for index, (stage_key, stage_label, item) in enumerate(selected_by_stage)
        }
        main_chain_state_override_by_signature: dict[str, tuple[str, str]] = {}
        if selected_by_stage_source == "current_generation_cases":
            main_chain_state_override_by_signature = (
                _main_chain_state_overrides_for_current_generation(
                    selected_by_stage,
                    stage_meta_by_key=workflow_stage_meta_by_key,
                    signature_fn=_signature,
                )
            )
        role_sequence: list[str] = []
        for offset, item in enumerate(ordered_cases):
            updated = dict(item)
            signature = _signature(updated)
            new_id = f"TC-{safe_start + offset:03d}"
            updated["id"] = new_id
            stage_info = main_chain_stage_by_signature.get(signature)
            in_main_chain = bool(stage_info)
            stage_key = str(stage_info[0]) if stage_info else ""
            group = _infer_group(updated, in_main_chain=in_main_chain)
            step_meta_for_role = workflow_stage_meta_by_key.get(stage_key) or {}
            role = (
                _normalize_actor_role(step_meta_for_role.get("actor"), fallback_text=_case_text(updated))
                if in_main_chain and str(step_meta_for_role.get("actor") or "").strip()
                else _infer_role(updated)
            )
            student_observation_projection = _is_student_observation_projection(updated)
            if student_observation_projection:
                updated["source_actor_role"] = role
                role = "student"
                updated["student_observation_projection"] = True
            elif in_main_chain and str(step_meta_for_role.get("source_actor_role") or "").strip():
                updated["source_actor_role"] = str(step_meta_for_role.get("source_actor_role") or "").strip()
            role_sequence.append(role)
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
            updated["role_switch_strategy"] = (
                "switch_to_admin_session_then_return_student_session"
                if in_main_chain and role == "admin"
                else "reuse_group_session"
            )
            updated["data_state"] = data_state
            updated["isolation_required"] = bool(not in_main_chain)
            updated["fixture_key"] = fixture["fixture_key"]
            updated["fixture_builder"] = fixture["fixture_builder"]
            updated["cleanup_policy"] = fixture["cleanup_policy"]
            updated["group_setup"] = group_setup_map.get(group, "seed_case_dataset()")
            updated["group_teardown"] = group_teardown_map.get(group, "cleanup_case_dataset()")
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
                step_meta = workflow_stage_meta_by_key.get(str(stage_info[0]) or "") or {}
                transition = _workflow_transition_for_case_helper(
                    updated,
                    step_meta=step_meta,
                    stage_label=str(stage_info[1]),
                    workflow_blueprints_present=bool(workflow_blueprints),
                    destructive_action_tokens=destructive_action_tokens,
                    blocking_negative_tokens=blocking_negative_tokens,
                    boundary_capacity_tokens=boundary_capacity_tokens,
                    analytics_tokens=analytics_tokens,
                )
                state_override = main_chain_state_override_by_signature.get(signature)
                if state_override:
                    transition = dict(transition)
                    transition["source_state"] = state_override[0]
                    transition["target_state"] = state_override[1]
                    transition["state_transition_confidence"] = max(
                        float(transition.get("state_transition_confidence") or 0.0),
                        0.55,
                    )
                updated["workflow_transition"] = transition
                for transition_field in (
                    "workflow_id",
                    "source_state",
                    "action",
                    "target_state",
                    "path_type",
                    "blocking",
                    "destructive",
                    "can_advance_main_flow",
                    "state_transition_confidence",
                ):
                    if transition.get(transition_field) not in (None, ""):
                        updated[transition_field] = transition[transition_field]
                updated["main_chain_stage_kind"] = str(transition.get("stage_kind") or "").strip()
                if bool(step_meta.get("main_path_step", True)) and not _is_low_value_main_chain_p0(updated):
                    updated["priority"] = "P0"
                    updated["priority_final"] = "P0"
                else:
                    updated["priority"] = "P1"
                    updated["priority_final"] = "P1"
                    updated["priority_decision_state"] = "overridden"
                    updated["priority_decision_source"] = "execution_plan_main_support_step_demoted"
            elif str(updated.get("priority") or "").strip().upper() == "P0":
                decision_source = str(updated.get("priority_decision_source") or "").strip()
                non_blocking_detail = _any(
                    _case_text(updated),
                    (
                        "弹窗",
                        "提示文案",
                        "展示",
                        "排序",
                        "筛选",
                        "列表",
                        "详情",
                        "display",
                        "tooltip",
                        "badge",
                    ),
                )
                blocking_business_anchor = _any(
                    _case_text(updated),
                    (
                        "generate result",
                        "generated result",
                        "correction result",
                        "review result",
                        "four modules",
                        "feedback modules",
                        "upload",
                        "submit success",
                        "approval passed",
                        "review approved",
                        "上传",
                        "去批改",
                        "生成批改结果",
                        "批改结果",
                        "四部分",
                        "综合点评",
                        "全文润色",
                        "优化建议",
                        "提交成功",
                        "审核通过",
                        "已发布",
                        "作文圈",
                    ),
                )
                preserve_semantic_anchor = decision_source in {
                    "main_path_anchor_floor",
                    "hard_guard_promotion",
                    "preserved_priority_override",
                    "conflict_resolved_by_high_risk_business_rule",
                } and (blocking_business_anchor or not non_blocking_detail)
                demote_non_main = False
                if not preserve_semantic_anchor:
                    demote_non_main = group in {"boundary", "display", "exception"}
                if demote_non_main:
                    updated["priority"] = "P1"
                    updated["priority_final"] = "P1"
                    updated["priority_decision_state"] = "overridden"
                    updated["priority_decision_source"] = "execution_plan_non_main_p0_demoted"
            elif group == "display" and _is_core_result_output_anchor(updated):
                updated["priority"] = "P0"
                updated["priority_final"] = "P0"
                updated["priority_decision_state"] = "overridden"
                updated["priority_decision_source"] = "execution_plan_core_result_output_promoted"
            annotated.append(updated)
            if in_main_chain:
                previous_main_id = new_id
                previous_main_result = str(updated.get("expected_result") or "")

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


    parsed_result = clean_and_parse_json_fn(full_content)
    parsed_result = normalize_json_structure_fn(parsed_result)
    if not isinstance(parsed_result, list):
        parsed_result = []
    parsed_result = deduplicate_test_cases_fn(parsed_result)
    parsed_result = apply_priority_semantics_to_cases(_dict_case_items(parsed_result), attach_debug=False)
    parsed_result, initial_filter_stats = _filter_low_quality_cases_with_stats(
        parsed_result,
        requirement_text=requirement,
        analyze_coverage_fn=analyze_coverage,
    )
    low_quality_filter_stats = LowQualityFilterStatsAccumulator(initial_filter_stats)

    stage_counts = {
        "primary": len(parsed_result),
        "gap": 0,
        "review": 0,
    }
    gap_attempts = 0
    gap_remaining_after_attempts = 0
    gap_stopped_by_provider_error = False
    candidate_count_before_review = _dict_case_count(parsed_result)
    review_selected_count = _dict_case_count(parsed_result)
    append_reference_counts = _resolve_append_reference_counts(
        append=append,
        expected_count=expected_count,
        existing_unique_count=existing_unique_count,
    )
    append_target_count = append_reference_counts["append_target_count"]
    reference_count_effective = append_reference_counts["reference_count_effective"]
    append_final_cap_count = append_reference_counts["append_final_cap_count"]
    append_cap_drop_total = 0
    append_cap_drop_signatures: set[str] = set()
    final_description_dedup_drop_signatures: set[str] = set()
    flow_governance_summary: dict[str, Any] = {}
    execution_plan_summary: dict[str, Any] = {}
    review_candidate_cases: list[dict[str, Any]] = []
    review_selection_input: list[dict[str, Any]] = []
    review_gate_trace: dict[str, Any] = {}
    review_llm_applied = False
    review_llm_pool_count = 0
    review_llm_selected_signatures: set[str] = set()
    review_llm_omitted_signatures: set[str] = set()
    review_constraint_retained_signatures: set[str] = set()
    review_llm_drop_reason_raw_map: dict[str, str] = {}
    review_llm_drop_reason_raw_origin_map: dict[str, str] = {}
    review_llm_drop_reason_map: dict[str, str] = {}
    review_llm_drop_reason_source_map: dict[str, str] = {}
    review_llm_drop_reason_evidence_map: dict[str, Any] = {}
    review_constraint_reason_map: dict[str, str] = {}
    review_target_min_count = 1
    review_target_max_count = 1
    review_candidate_coverage_context: dict[str, Any] = {}
    review_candidate_rule_diagnostics: dict[str, Any] = {"rule_diagnostics": []}
    coverage_state = _resolve_generation_coverage_state(
        expected_count=expected_count,
        generation_mode=generation_mode,
        generation_coverage_mode=generation_coverage_mode,
        generation_target_case_range=generation_target_case_range,
    )
    coverage_profile = coverage_state["coverage_profile"]
    expected_count_value = coverage_state["expected_count_value"]
    full_regression_recommended_floor = coverage_state["full_regression_recommended_floor"]
    effective_generation_coverage_mode = coverage_state["effective_generation_coverage_mode"]
    effective_generation_coverage_mode_source = coverage_state["effective_generation_coverage_mode_source"]
    explicit_generation_mode_override = coverage_state["explicit_generation_mode_override"]
    generation_coverage_mode = coverage_state["generation_coverage_mode"]
    explicit_expected_count_floor_preserved = coverage_state["explicit_expected_count_floor_preserved"]
    resolved_full_regression_floor = coverage_state["resolved_full_regression_floor"]

    review_shortfall_detected = False
    review_shortfall_before_count = 0
    review_shortfall_recovered_count = 0
    review_post_rerank_floor_count = 1
    review_post_rerank_recovered_count = 0
    final_target_floor_count = 0
    final_floor_recovered_count = 0
    final_floor_recovery_applied = False
    final_floor_recovery_attempted = False
    final_floor_recovery_reason = ""
    final_confirmed_conflict_drop_count = 0
    final_shortfall_supplement_attempted = False
    final_shortfall_supplement_applied = False
    final_shortfall_supplement_count = 0
    final_shortfall_supplement_reason = ""
    final_order_flow_governance_summary: dict[str, Any] = {}
    final_case_structure: dict[str, Any] = {}
    final_independent_case_structure: dict[str, Any] = {}
    review_fill_source = "none"
    review_must_keep_signatures: set[str] = set()
    review_must_keep_reason_map: dict[str, list[str]] = {}
    review_decision_table: list[dict[str, Any]] = []
    review_decision_summary: dict[str, Any] = {}
    review_llm_runtime_debug: dict[str, Any] = _default_review_llm_runtime_debug()
    judge_summary_payload: dict[str, Any] = {}
    judge_decision_table_payload: list[dict[str, Any]] = []

    current_total = _merged_unique_total(
        parsed_result,
        append=append,
        existing_cases=existing_cases,
        count_unique_test_cases_fn=count_unique_test_cases_fn,
    )
    if current_total == 0 and int(expected_count or 0) > 0:
        yield "@@STATUS@@:Initial streaming result is empty, trying one non-stream rescue pass...\n"
        rescue_prompt = f"""
{base_prompt}

RESCUE INSTRUCTION:
- Quantity is reference-only; prioritize quality and coverage gain.
- Stop when additional cases add no new information.
- Return ONLY strict JSON array.
"""
        try:
            rescue_raw = client.generate_response(requirement, rescue_prompt, db=db, task_type="generation")
            rescue_parsed = clean_and_parse_json_fn(str(rescue_raw or ""))
            rescue_parsed = normalize_json_structure_fn(rescue_parsed)
            if isinstance(rescue_parsed, list) and rescue_parsed:
                rescue_parsed = deduplicate_test_cases_fn(rescue_parsed)
                rescue_parsed = apply_priority_semantics_to_cases(
                    _dict_case_items(rescue_parsed),
                    attach_debug=False,
                )
                rescue_parsed, rescue_filter_stats = _filter_low_quality_cases_with_stats(
                    rescue_parsed,
                    requirement_text=requirement,
                    analyze_coverage_fn=analyze_coverage,
                )
                low_quality_filter_stats.accumulate(rescue_filter_stats)
                parsed_result = rescue_parsed
                stage_counts["primary"] = len(parsed_result)
                current_total = _merged_unique_total(
                    parsed_result,
                    append=append,
                    existing_cases=existing_cases,
                    count_unique_test_cases_fn=count_unique_test_cases_fn,
                )
                yield f"@@STATUS@@:Rescue succeeded, recovered {len(parsed_result)} cases.\n"
        except Exception as rescue_err:
            yield f"@@STATUS@@:Rescue failed ({str(rescue_err)}), continue pipeline.\n"

    normalized_mode = str(generation_mode or "").strip().lower()
    if normalized_mode not in {"single_pass", "multi_pass", "biz_key_multi_pass"}:
        normalized_mode = "multi_pass" if bool(multi_pass) else "single_pass"

    if normalized_mode in {"multi_pass", "biz_key_multi_pass"} and isinstance(parsed_result, list):
        coverage_primary = analyze_coverage(requirement, _dict_case_items(parsed_result))
        missing_rules = list(coverage_primary.get("missing_rules") or [])
        diagnostics = [item for item in (coverage_primary.get("rule_diagnostics") or []) if isinstance(item, dict)]
        has_missing_types = any(bool(item.get("missing_types")) for item in diagnostics)

        need_gap = bool(missing_rules) or has_missing_types
        if need_gap:
            yield "@@STATUS@@:[multi-pass] Stage 2/3 gap supplement started...\n"
            before_gap = len(parsed_result)
            supplement_attempt = 0

            while supplement_attempt < 3 and (missing_rules or has_missing_types):
                supplement_attempt += 1
                yield f"@@STATUS@@:Gap supplement attempt #{supplement_attempt}...\n"

                supplement_source: list[dict[str, Any]] = []
                if append and isinstance(existing_cases, list):
                    supplement_source.extend(_dict_case_items(existing_cases))
                supplement_source.extend(_dict_case_items(parsed_result))

                closed_loop_instruction = build_supplement_closed_loop_instruction_fn(
                    all_cases=supplement_source,
                    requirement=requirement,
                    infer_case_kind_fn=infer_case_kind_fn,
                )
                gap_prompt = build_gap_fill_prompt(
                    requirement_context=requirement,
                    existing_cases=supplement_source,
                    coverage_result=coverage_primary,
                    missing_rules=missing_rules,
                    current_biz_key=current_biz_key,
                    pretty_json=False,
                )
                system_prompt = f"""
{gap_prompt}

CLOSED_LOOP_HINT:
{closed_loop_instruction}

APPEND_POLICY: only append if new cases add coverage gain; otherwise return [].
"""

                extra_content = ""
                extra_stream = client.generate_response_stream(requirement, system_prompt, task_type="generation")
                provider_error = None
                for chunk in extra_stream:
                    extra_content += chunk
                    yield chunk
                    if chunk.startswith("Error:") or chunk.startswith("[棰濆害鑰楀敖]") or chunk.startswith("Exception occurred:"):
                        provider_error = chunk
                        break
                if provider_error:
                    yield "\n@@STATUS@@:Generation failed\n"
                    yield f"{provider_error}\n"
                    gap_stopped_by_provider_error = True
                    break

                try:
                    extra_parsed = clean_and_parse_json_fn(extra_content)
                    extra_parsed = normalize_json_structure_fn(extra_parsed)
                    if isinstance(extra_parsed, list) and extra_parsed:
                        extra_parsed = deduplicate_test_cases_fn(extra_parsed)
                        extra_parsed = apply_priority_semantics_to_cases(
                            _dict_case_items(extra_parsed),
                            attach_debug=False,
                        )
                        extra_parsed, extra_filter_stats = _filter_low_quality_cases_with_stats(
                            extra_parsed,
                            requirement_text=requirement,
                            analyze_coverage_fn=analyze_coverage,
                        )
                        low_quality_filter_stats.accumulate(extra_filter_stats)
                        parsed_result.extend(_dict_case_items(extra_parsed))
                        parsed_result = normalize_json_structure_fn(parsed_result)
                        parsed_result = deduplicate_test_cases_fn(parsed_result)
                except Exception:
                    pass

                coverage_primary = analyze_coverage(requirement, parsed_result)
                missing_rules = list(coverage_primary.get("missing_rules") or [])
                diagnostics = [item for item in (coverage_primary.get("rule_diagnostics") or []) if isinstance(item, dict)]
                has_missing_types = any(bool(item.get("missing_types")) for item in diagnostics)
                if not missing_rules and not has_missing_types:
                    break

            gap_attempts = supplement_attempt
            gap_remaining_after_attempts = int(len(missing_rules) + (1 if has_missing_types else 0))
            stage_counts["gap"] = max(0, len(parsed_result) - before_gap)

        yield "@@STATUS@@:[multi-pass] Stage 3/3 review selection started...\n"

        candidate_cases = _dict_case_items(parsed_result)
        candidate_cases, review_filter_stats = _filter_low_quality_cases_with_stats(
            candidate_cases,
            requirement_text=requirement,
            analyze_coverage_fn=analyze_coverage,
        )
        low_quality_filter_stats.accumulate(review_filter_stats)
        candidate_count_before_review = len(candidate_cases)
        review_candidate_cases = list(candidate_cases)

        review_candidate_coverage_context = analyze_coverage(requirement, candidate_cases)
        review_candidate_rule_diagnostics = _rule_diagnostics_payload(review_candidate_coverage_context)
        review_candidate_pool_split = _split_review_candidate_pool(
            candidate_cases,
            coverage_context=review_candidate_coverage_context,
            rule_diagnostics=review_candidate_rule_diagnostics,
            must_cover_rule_set=must_cover_rule_set,
            score_case_priority_fn=score_case_priority,
            must_keep_reasons_fn=_review_must_keep_reasons,
            signature_fn=_signature,
        )
        must_keep_cases = review_candidate_pool_split.must_keep_cases
        llm_pool_cases = review_candidate_pool_split.llm_pool_cases
        review_must_keep_signatures = set(review_candidate_pool_split.must_keep_signatures)
        review_must_keep_reason_map = dict(review_candidate_pool_split.must_keep_reason_map)

        review_llm_pool_count = int(len(llm_pool_cases))
        review_constraints = _build_review_selection_constraints(
            llm_pool_cases,
            reference_count=int(reference_count_effective or len(llm_pool_cases) or 1),
            generation_profile=generation_coverage_profile,
        )
        review_target_min_count = int(review_constraints.get("target_min_count") or 1)
        review_target_max_count = int(review_constraints.get("target_max_count") or review_target_min_count)
        review_prompt = build_review_select_prompt(
            requirement_context=requirement,
            candidate_cases=llm_pool_cases,
            target_count=max(1, int(reference_count_effective or len(llm_pool_cases) or 1)),
            target_min_count=review_target_min_count,
            target_max_count=review_target_max_count,
            coverage_constraints=review_constraints,
            current_biz_key=current_biz_key,
            pretty_json=False,
        )
        selected_from_llm_pool: list[dict[str, Any]] = list(llm_pool_cases)
        review_llm_runtime_debug["pool_size"] = int(len(llm_pool_cases))
        review_llm_runtime_debug["pool_non_empty"] = bool(llm_pool_cases)
        review_llm_runtime_debug["prompt_chars"] = int(len(review_prompt or ""))
        review_llm_runtime_debug["prompt_est_tokens"] = int(round(len(review_prompt or "") / 4))
        review_llm_runtime_debug["candidate_count"] = int(len(llm_pool_cases))
        review_llm_runtime_debug["append_target_count"] = int(append_target_count or 0)
        review_llm_runtime_debug["append_final_cap_count"] = int(append_final_cap_count or 0)
        try:
            if llm_pool_cases:
                review_llm_runtime_debug["invoked"] = True

                review_llm_runtime_debug["primary_model"] = _select_review_model(client, review_prompt)
                review_response = client.generate_response(
                    review_prompt,
                    "You are a QA Auditor.",
                    db=db,
                    task_type="review",
                )
                review_response_text = str(review_response or "")
                review_llm_runtime_debug["primary_response_metadata"] = _client_response_metadata(client)
                primary_result = _analyze_review_retry_payload(
                    review_response_text,
                    candidate_cases=llm_pool_cases,
                    parse_json_fn=clean_and_parse_json_fn,
                    normalize_json_structure_fn=normalize_json_structure_fn,
                    reason_origin="llm",
                    map_selection_fn=_map_review_selection_with_reasons,
                )
                review_llm_runtime_debug["response_len"] = int(len(review_response_text))
                review_llm_runtime_debug["response_preview"] = _clip_text(review_response_text, 500)
                review_llm_runtime_debug["parsed_type"] = str(primary_result.get("parsed_type") or "")
                review_llm_runtime_debug["parsed_len"] = int(primary_result.get("parsed_len") or 0)
                primary_debug_counts = _review_payload_debug_counts(primary_result)
                review_llm_runtime_debug.update(primary_debug_counts)
                primary_mapped_count = primary_debug_counts["mapped_count"]
                primary_dropped_reason_count = primary_debug_counts["dropped_reason_count"]
                primary_dropped_reason_payload_count = primary_debug_counts["dropped_reason_payload_count"]
                review_llm_runtime_debug["primary_dropped_reason_count"] = int(primary_dropped_reason_count)
                review_llm_runtime_debug["primary_dropped_reason_payload_count"] = int(primary_dropped_reason_payload_count)
                review_llm_runtime_debug["primary_reason_incomplete"] = bool(
                    primary_mapped_count > 0 and primary_dropped_reason_count <= 0
                )
                review_llm_runtime_debug["primary_reason_coverage_ratio"] = (
                    round(float(primary_dropped_reason_count) / float(primary_dropped_reason_payload_count), 4)
                    if primary_dropped_reason_payload_count > 0
                    else 0.0
                )
                review_llm_runtime_debug["payload_has_selection_signal"] = bool(
                    primary_result.get("payload_has_selection_signal")
                )
                review_llm_runtime_debug["primary_invalid_reason"] = str(primary_result.get("invalid_reason") or "")

                final_result = dict(primary_result)
                final_source = "primary_llm"
                retry_reason = str(primary_result.get("invalid_reason") or "")
                if retry_reason:
                    review_llm_runtime_debug["retry_invoked"] = True
                    review_llm_runtime_debug["retry_reason"] = retry_reason
                    primary_model_for_retry = str(review_llm_runtime_debug.get("primary_model") or "").strip()
                    if (
                        retry_reason in _RETRYABLE_RESPONSE_ERROR_REASONS
                        and primary_model_for_retry
                        and str(review_response_text or "").startswith("Error: Empty response")
                    ):
                        review_llm_runtime_debug["primary_compact_retry_invoked"] = True
                        review_llm_runtime_debug["primary_compact_retry_model"] = primary_model_for_retry
                        compact_retry_text = str(
                            client.generate_response(
                                _build_compact_review_retry_prompt(
                                    llm_pool_cases,
                                    target_min_count=review_target_min_count,
                                    target_max_count=review_target_max_count,
                                    drop_reasons=_REVIEW_DROP_REASONS,
                                    max_candidates=200,
                                ),
                                "You are a QA Auditor. Return strict JSON only.",
                                db=db,
                                task_type="review",
                                model=primary_model_for_retry,
                                max_tokens=4096,
                            )
                            or ""
                        )
                        review_llm_runtime_debug["primary_compact_retry_response_len"] = int(len(compact_retry_text))
                        review_llm_runtime_debug["primary_compact_retry_response_metadata"] = _client_response_metadata(client)
                        compact_retry_result = _analyze_review_retry_payload(
                            compact_retry_text,
                            candidate_cases=llm_pool_cases,
                            parse_json_fn=clean_and_parse_json_fn,
                            normalize_json_structure_fn=normalize_json_structure_fn,
                            reason_origin="primary_compact_retry",
                            map_selection_fn=_map_review_selection_with_reasons,
                        )
                        compact_retry_invalid_reason = str(compact_retry_result.get("invalid_reason") or "")
                        review_llm_runtime_debug["primary_compact_retry_invalid_reason"] = compact_retry_invalid_reason
                        if not compact_retry_invalid_reason:
                            review_response_text = compact_retry_text
                            final_result = compact_retry_result
                            final_source = "primary_compact_retry"
                            retry_reason = ""

                if retry_reason:
                    fallback_models: list[str] = []
                    primary_model_name = str(review_llm_runtime_debug.get("primary_model") or "").strip().lower()
                    if "deepseek" in primary_model_name and primary_model_name != "deepseek-chat":
                        fallback_models.append("deepseek-chat")
                    for candidate in (
                        str(getattr(client, "model", "") or "").strip(),
                        str(getattr(client, "turbo_model", "") or "").strip(),
                    ):
                        if candidate:
                            fallback_models.append(candidate)

                    candidate_ids = [
                        _review_case_id(item)
                        for item in llm_pool_cases
                        if isinstance(item, dict) and _review_case_id(item)
                    ]
                    candidate_ids = candidate_ids[:200]
                    repair_prompt = (
                        f"{review_prompt}\n\n"
                        "PROTOCOL FIX (MANDATORY):\n"
                        "- Previous output was invalid for downstream selection mapping.\n"
                        "- Return STRICT JSON only; no prose, no markdown, no code fences.\n"
                        "- Schema MUST be:\n"
                        "{\n"
                        '  "kept_case_ids": ["<case_id>"],\n'
                        '  "dropped": [{"case_id": "<case_id>", "reason": "<reason>"}]\n'
                        "}\n"
                        "- `kept_case_ids` and `dropped[*].case_id` must come from this candidate id list only:\n"
                        f"{_json_for_prompt(candidate_ids)}\n"
                        "- Do not invent or rewrite case ids.\n"
                        "- `dropped[*].reason` must be ONE canonical key from:\n"
                        f"  {_json_for_prompt(_REVIEW_DROP_REASONS, compact=True)}\n"
                    )

                    seen_fallback_models: set[str] = set()
                    for fallback_model in fallback_models:
                        model_key = str(fallback_model or "").strip()
                        if not model_key:
                            continue
                        if model_key in seen_fallback_models:
                            continue
                        seen_fallback_models.add(model_key)
                        review_response_retry = client.generate_response(
                            repair_prompt,
                            "You are a QA Auditor.",
                            db=db,
                            task_type="review",
                            model=model_key,
                        )
                        retry_text = str(review_response_retry or "")
                        retry_result = _analyze_review_retry_payload(
                            retry_text,
                            candidate_cases=llm_pool_cases,
                            parse_json_fn=clean_and_parse_json_fn,
                            normalize_json_structure_fn=normalize_json_structure_fn,
                            reason_origin="fallback_llm",
                            map_selection_fn=_map_review_selection_with_reasons,
                        )
                        retry_invalid_reason = str(retry_result.get("invalid_reason") or "")
                        retry_debug_counts = _review_payload_debug_counts(retry_result)
                        review_llm_runtime_debug["retry_attempts"].append(
                            {
                                "model": model_key,
                                "response_len": int(len(retry_text)),
                                "is_error": bool(
                                    bool(retry_invalid_reason)
                                    and retry_invalid_reason in _RETRYABLE_RESPONSE_ERROR_REASONS
                                ),
                                "invalid_reason": retry_invalid_reason,
                                "parsed_type": str(retry_result.get("parsed_type") or ""),
                                "mapped_count": retry_debug_counts["mapped_count"],
                                "dropped_reason_count": retry_debug_counts["dropped_reason_count"],
                                "dropped_reason_payload_count": retry_debug_counts[
                                    "dropped_reason_payload_count"
                                ],
                                "dropped_reason_unmapped_count": retry_debug_counts[
                                    "dropped_reason_unmapped_count"
                                ],
                                "payload_has_selection_signal": bool(retry_result.get("payload_has_selection_signal")),
                            }
                        )
                        if retry_invalid_reason:
                            continue
                        review_response_text = retry_text
                        final_result = retry_result
                        final_source = "fallback_llm"
                        review_llm_runtime_debug["retry_model"] = model_key
                        break

                    review_llm_runtime_debug["retry_response_len"] = int(len(review_response_text))

                review_llm_runtime_debug["retry_parse_success"] = bool(
                    review_llm_runtime_debug.get("retry_invoked")
                    and bool(final_result.get("parse_success"))
                    and not bool(final_result.get("invalid_reason"))
                )
                review_llm_runtime_debug["retry_mapped_count"] = int(
                    len(final_result.get("mapped") or []) if bool(review_llm_runtime_debug.get("retry_invoked")) else 0
                )
                review_llm_runtime_debug["retry_payload_has_selection_signal"] = bool(
                    final_result.get("payload_has_selection_signal") if bool(review_llm_runtime_debug.get("retry_invoked")) else False
                )
                if bool(review_llm_runtime_debug.get("retry_invoked")):
                    final_debug_counts = _review_payload_debug_counts(final_result)
                    review_llm_runtime_debug["retry_dropped_reason_count"] = final_debug_counts["dropped_reason_count"]
                    review_llm_runtime_debug["retry_dropped_reason_payload_count"] = final_debug_counts[
                        "dropped_reason_payload_count"
                    ]
                    review_llm_runtime_debug["retry_dropped_reason_unmapped_count"] = final_debug_counts[
                        "dropped_reason_unmapped_count"
                    ]

                final_invalid_reason = str(final_result.get("invalid_reason") or "")
                review_llm_runtime_debug["final_source"] = (
                    str(final_source) if not final_invalid_reason else "review_selector"
                )
                if not final_invalid_reason:
                    selected_from_llm_pool = _dict_case_items(final_result.get("mapped") or [])
                    review_llm_selected_signatures = set(final_result.get("mapped_signatures") or set())
                    review_llm_drop_reason_raw_map = dict(final_result.get("dropped_reason_map") or {})
                    review_llm_drop_reason_raw_origin_map = dict(final_result.get("dropped_reason_origin_map") or {})
                    final_dropped_reason_count = int(len(review_llm_drop_reason_raw_map))
                    final_mapped_signatures = {
                        str(signature or "").strip()
                        for signature in set(final_result.get("mapped_signatures") or set())
                        if str(signature or "").strip()
                    }
                    final_dropped_signatures = {
                        str(signature or "").strip()
                        for signature in review_llm_drop_reason_raw_map.keys()
                        if str(signature or "").strip()
                    }
                    selected_and_dropped_overlap = final_mapped_signatures & final_dropped_signatures
                    signature_to_case_id = {
                        _signature(item): _review_case_id(item)
                        for item in llm_pool_cases
                        if isinstance(item, dict) and _signature(item)
                    }
                    overlap_case_ids = [
                        str(signature_to_case_id.get(signature) or "")
                        for signature in selected_and_dropped_overlap
                    ]
                    overlap_case_ids = [case_id for case_id in overlap_case_ids if case_id]
                    review_llm_runtime_debug["final_selected_and_dropped_overlap_count"] = int(
                        len(selected_and_dropped_overlap)
                    )
                    review_llm_runtime_debug["final_selected_and_dropped_overlap_case_ids"] = overlap_case_ids[:20]
                    review_llm_runtime_debug["final_payload_consistent"] = bool(
                        len(selected_and_dropped_overlap) == 0
                    )
                    review_llm_runtime_debug["final_dropped_reason_count"] = int(final_dropped_reason_count)
                    final_debug_counts = _review_payload_debug_counts(final_result)
                    review_llm_runtime_debug["final_dropped_reason_payload_count"] = final_debug_counts[
                        "dropped_reason_payload_count"
                    ]
                    review_llm_runtime_debug["final_dropped_reason_unmapped_count"] = final_debug_counts[
                        "dropped_reason_unmapped_count"
                    ]
                    review_llm_applied = True
                    review_llm_runtime_debug["applied"] = True
                    review_llm_runtime_debug["applied_reason"] = (
                        "mapped_valid_payload" if final_source == "primary_llm" else "retry_payload_valid"
                    )
                    review_llm_runtime_debug["fallback_reason_incomplete"] = bool(
                        final_source == "fallback_llm" and int(final_dropped_reason_count or 0) <= 0
                    )
                else:
                    review_llm_runtime_debug["applied"] = False
                    review_llm_runtime_debug["applied_reason"] = final_invalid_reason
            else:
                review_llm_runtime_debug["applied_reason"] = "empty_llm_pool"
        except Exception:
            review_llm_runtime_debug["exception"] = str(__import__("traceback").format_exc()[-1500:])

        selected_from_llm_pool, constraint_reason_map = _enforce_review_selection_constraints(
            selected_cases=_dict_case_items(selected_from_llm_pool),
            pool_cases=_dict_case_items(llm_pool_cases),
        constraints=review_constraints,
        coverage_context=review_candidate_coverage_context,
        rule_diagnostics=review_candidate_rule_diagnostics,
        rank_case_fn=_rank_review_case_for_fill,
    )
        review_constraint_reason_map = dict(constraint_reason_map or {})
        selected_signature_after_constraints = {
            _signature(item) for item in selected_from_llm_pool if isinstance(item, dict)
        }
        if review_llm_applied and llm_pool_cases:
            pool_by_signature = {
                _signature(item): item
                for item in llm_pool_cases
                if isinstance(item, dict) and _signature(item)
            }
            dropped_after_constraints = [
                item
                for signature, item in pool_by_signature.items()
                if signature and signature not in selected_signature_after_constraints
            ]
            missing_reason_cases = [
                item
                for item in dropped_after_constraints
                if _signature(item) and _signature(item) not in review_llm_drop_reason_raw_map
            ]
            if missing_reason_cases:
                repair_candidates = _build_reason_repair_candidates(missing_reason_cases, max_candidates=80)
                if repair_candidates:
                    repair_prompt = _build_compact_reason_repair_prompt(
                        missing_reason_cases,
                        drop_reasons=_REASON_REPAIR_DROP_REASONS,
                        max_candidates=80,
                    )
                    review_llm_runtime_debug["reason_repair_invoked"] = True
                    review_llm_runtime_debug["reason_repair_candidate_count"] = int(len(repair_candidates))
                    review_llm_runtime_debug["reason_repair_model"] = _select_review_model(client, repair_prompt)
                    repair_response_text = str(
                        client.generate_response(
                            repair_prompt,
                            "You are a QA Auditor. Return strict JSON only.",
                            db=db,
                            task_type="review",
                            max_tokens=2048,
                        )
                        or ""
                    )
                    review_llm_runtime_debug["reason_repair_response_len"] = int(len(repair_response_text))
                    review_llm_runtime_debug["reason_repair_response_metadata"] = _client_response_metadata(client)
                    repair_result = _analyze_reason_repair_payload(
                        repair_response_text,
                        missing_reason_cases=missing_reason_cases,
                        parse_json_fn=clean_and_parse_json_fn,
                        allowed_reasons=_REASON_REPAIR_DROP_REASONS,
                        reason_origin="llm",
                        existing_drop_reason_map=review_llm_drop_reason_raw_map,
                    )
                    mapped_reason_count = int(repair_result.get("mapped_count") or 0)
                    repair_invalid_reason = str(repair_result.get("invalid_reason") or "")
                    review_llm_drop_reason_raw_map.update(
                        dict(repair_result.get("dropped_reason_map") or {})
                    )
                    review_llm_drop_reason_raw_origin_map.update(
                        dict(repair_result.get("dropped_reason_origin_map") or {})
                    )
                    review_llm_runtime_debug["reason_repair_mapped_count"] = int(mapped_reason_count)
                    review_llm_runtime_debug["reason_repair_invalid_reason"] = str(repair_invalid_reason)
                    if mapped_reason_count > 0:
                        review_llm_runtime_debug["final_dropped_reason_count"] = int(
                            len(review_llm_drop_reason_raw_map)
                        )
                        review_llm_runtime_debug["final_dropped_reason_payload_count"] = int(
                            len(review_llm_drop_reason_raw_map)
                        )
                        review_llm_runtime_debug["final_dropped_reason_unmapped_count"] = 0
        review_llm_drop_reason_map, review_llm_drop_reason_source_map, review_llm_drop_reason_evidence_map = (
            _resolve_review_llm_drop_reason_maps(
                pool_cases=_dict_case_items(llm_pool_cases),
                selected_cases=_dict_case_items(selected_from_llm_pool),
                raw_drop_reason_map=review_llm_drop_reason_raw_map,
                raw_drop_reason_origin_map=review_llm_drop_reason_raw_origin_map,
                coverage_context=review_candidate_coverage_context,
                rule_diagnostics=review_candidate_rule_diagnostics,
            )
        )
        review_llm_omitted_signatures = (
            set(review_llm_drop_reason_map.keys()) if review_llm_applied else set()
        )
        review_constraint_retained_signatures = {
            signature
            for signature, reason in review_constraint_reason_map.items()
            if signature in selected_signature_after_constraints and str(reason or "").startswith("retained_by_constraint_")
        }
        if review_llm_applied:
            review_llm_selected_signatures = {
                signature
                for signature in review_llm_selected_signatures
                if signature and signature in {_signature(item) for item in selected_from_llm_pool if isinstance(item, dict)}
            }

        selection_input = _merge_review_selection_candidates(
            must_keep_cases,
            selected_from_llm_pool,
            signature_fn=_signature,
        )

        # If review output collapses below target_min_count, deterministically recover from
        # already-filtered candidate pool instead of accepting a 50->1 shortfall.
        review_shortfall_before_count = int(len(selection_input))
        if int(review_target_min_count or 1) > 0 and int(len(selection_input)) < int(review_target_min_count or 1):
            review_shortfall_detected = True
            review_fill_source = "constraint_fill"
            selection_input, review_constraint_reason_map, review_shortfall_recovered_count = (
                _recover_review_selection_shortfall(
                    selection_input=selection_input,
                    candidate_cases=candidate_cases,
                    target_min_count=review_target_min_count,
                    constraint_reason_map=review_constraint_reason_map,
                    domain_guard_active=bool(review_constraints.get("domain_guard_active")),
                    cross_domain_noise_fn=_is_cross_domain_noise,
                    coverage_context=review_candidate_coverage_context,
                    rule_diagnostics=review_candidate_rule_diagnostics,
                    rank_case_fn=_rank_review_case_for_fill,
                )
            )
        else:
            review_shortfall_before_count = int(len(selection_input))

        review_selection_input = _dict_case_items(selection_input)
        review_selection_coverage = analyze_coverage(requirement, review_selection_input)
        rerank_result = _rerank_and_cap_by_rule(
            review_selection_input,
            expected_count=expected_count,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            hits_reuse_risk_fn=_hits_reuse_risk,
            hits_soft_constraint_fn=_hits_soft_constraint,
            max_per_rule=3,
            include_trace=True,
            coverage_context=review_selection_coverage,
            rule_diagnostics=_rule_diagnostics_payload(review_selection_coverage),
            generation_profile=generation_coverage_profile,
        )
        parsed_result, review_gate_trace = _cases_and_trace_from_result(rerank_result)
        if not parsed_result and candidate_cases:
            fallback_coverage = analyze_coverage(requirement, candidate_cases)
            fallback_result = _rerank_and_cap_by_rule(
                candidate_cases,
                expected_count=expected_count,
                deduplicate_test_cases_fn=deduplicate_test_cases_fn,
                hits_reuse_risk_fn=_hits_reuse_risk,
                hits_soft_constraint_fn=_hits_soft_constraint,
                max_per_rule=3,
                include_trace=True,
                coverage_context=fallback_coverage,
                rule_diagnostics=_rule_diagnostics_payload(fallback_coverage),
                generation_profile=generation_coverage_profile,
            )
            parsed_result, review_gate_trace = _cases_and_trace_from_result(fallback_result)
            review_selection_input = list(candidate_cases)
            review_llm_applied = False
            review_llm_selected_signatures = set()
            review_constraint_retained_signatures = set()
            review_llm_drop_reason_raw_map = {}
            review_llm_drop_reason_raw_origin_map = {}
            review_llm_drop_reason_map = {}
            review_llm_drop_reason_source_map = {}
            review_llm_drop_reason_evidence_map = {}
            review_llm_omitted_signatures = set()
            review_constraint_reason_map = {}
            review_llm_runtime_debug["forced_reset_by_fallback"] = True
            review_llm_runtime_debug["final_source"] = "review_selector"
            review_llm_runtime_debug["applied"] = False
            review_llm_runtime_debug["applied_reason"] = "forced_reset_by_empty_rerank_result"
            review_llm_runtime_debug["fallback_reason_incomplete"] = False

        review_selected_count = len(parsed_result)
        stage_counts["review"] = len(parsed_result)
    else:
        if append and isinstance(existing_cases, list):
            reference_count_effective = max(1, int(expected_count or 1) - int(existing_unique_count or 0))
        candidate_cases = _dict_case_items(parsed_result)
        candidate_count_before_review = len(candidate_cases)
        review_candidate_cases = list(candidate_cases)
        review_selection_input = list(candidate_cases)
        review_candidate_coverage = analyze_coverage(requirement, candidate_cases)
        review_candidate_coverage_context = review_candidate_coverage
        review_candidate_rule_diagnostics = _rule_diagnostics_payload(review_candidate_coverage_context)
        rerank_result = _rerank_and_cap_by_rule(
            candidate_cases,
            expected_count=expected_count,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            hits_reuse_risk_fn=_hits_reuse_risk,
            hits_soft_constraint_fn=_hits_soft_constraint,
            max_per_rule=3,
            include_trace=True,
            coverage_context=review_candidate_coverage,
            rule_diagnostics=_rule_diagnostics_payload(review_candidate_coverage),
            generation_profile=generation_coverage_profile,
        )
        parsed_result, review_gate_trace = _cases_and_trace_from_result(rerank_result)
        review_selected_count = len(parsed_result)
        stage_counts["review"] = len(parsed_result)

    # Anti-collapse safeguard: avoid severe 50->1 style collapse after rerank when
    # candidate pool still has enough high-quality items.
    review_post_rerank_floor_count = _resolve_review_post_rerank_floor_count(
        candidate_count_before_review=candidate_count_before_review,
        reference_count_effective=reference_count_effective,
        generation_coverage_mode=generation_coverage_mode,
    )

    if int(len(parsed_result)) < int(review_post_rerank_floor_count or 1):
        review_shortfall_detected = True
        parsed_result, review_post_rerank_recovered_count = (
            _recover_post_rerank_shortfall(
                parsed_cases=parsed_result,
                review_selection_input=review_selection_input,
                candidate_cases=candidate_cases,
                floor_count=review_post_rerank_floor_count,
                coverage_context=review_candidate_coverage_context,
                rule_diagnostics=review_candidate_rule_diagnostics,
                rank_case_fn=_rank_review_case_for_fill,
            )
        )
        if review_post_rerank_recovered_count > 0:
            review_fill_source = (
                "post_rerank_recovery"
                if str(review_fill_source or "none") in {"", "none"}
                else f"{review_fill_source}+post_rerank_recovery"
            )

    parsed_result = normalize_json_structure_fn(parsed_result)
    parsed_result = deduplicate_test_cases_fn(parsed_result)
    parsed_result = reorder_cases_by_closed_loop_fn(parsed_result, start_id=start_id, renumber_ids=True)
    parsed_result = _apply_coverage_priority_semantics(
        requirement,
        parsed_result,
        analyze_coverage_fn=analyze_coverage,
    )
    ui_like_ratio_postprocess_drop_count = 0
    parsed_result, ui_like_ratio_postprocess_drop_count = _apply_ui_like_ratio_postprocess_cap(
        _dict_case_items(parsed_result),
        forbidden_patterns_active=bool(case_governance_matcher.forbidden_patterns),
        focus_score_fn=_focus_score,
    )
    if ui_like_ratio_postprocess_drop_count > 0:
        parsed_result = reorder_cases_by_closed_loop_fn(
            parsed_result,
            start_id=start_id,
            renumber_ids=True,
        )
    try:
        from ..judge.test_case_judge import judge_cases
        from ..judge.test_case_repairer import repair_cases
        from ..judge.training_gate import training_gate

        judged = judge_cases(
            cases=_dict_case_items(parsed_result),
            requirement_semantics_context=requirement_semantics_context or {},
            control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
        )
        repaired = repair_cases(
            judged=judged,
            requirement_semantics_context=requirement_semantics_context or {},
            control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
            strategy="rule_first_llm_fallback",
        )
        confirmed_pass_cases, repaired_pass_cases, rejected_cases, pending_cases = training_gate(repaired)
        parsed_result = [*confirmed_pass_cases, *repaired_pass_cases]
        parsed_result = deduplicate_test_cases_fn(_dict_case_items(parsed_result))
        parsed_result = reorder_cases_by_closed_loop_fn(
            parsed_result,
            start_id=start_id,
            renumber_ids=True,
        )
        judge_summary_payload = _build_judge_summary_payload(
            repaired=repaired,
            confirmed_pass_cases=confirmed_pass_cases,
            repaired_pass_cases=repaired_pass_cases,
            rejected_cases=rejected_cases,
            pending_cases=pending_cases,
            fact_profile=fact_profile,
        )
        judge_decision_table_payload = _build_judge_decision_table_payload(
            repaired=repaired,
            review_case_id_fn=_review_case_id,
        )
    except Exception:
        judge_summary_payload = {}
        judge_decision_table_payload = []
    final_quality_filtered_result, final_quality_drop_total = _filter_final_quality_cases(
        parsed_result,
        low_quality_filter_stats.low_quality_drop_details,
        stage="post_judge_quality_filter",
    )
    if final_quality_drop_total > 0:
        parsed_result = final_quality_filtered_result
        low_quality_filter_stats.add_postprocess_quality_drop(final_quality_drop_total)
    parsed_result, pre_priority_coverage = _coverage_priority_semantics_result(
        requirement,
        parsed_result,
        analyze_coverage_fn=analyze_coverage,
    )
    parsed_result = _strip_case_meta_list(parsed_result)
    parsed_result, final_description_dedup_drop_signatures = _dedupe_by_final_description(
        _dict_case_items(parsed_result)
    )
    if final_description_dedup_drop_signatures:
        parsed_result = reorder_cases_by_closed_loop_fn(
            parsed_result,
            start_id=start_id,
            renumber_ids=True,
        )
    parsed_result, append_cap_drop_signatures, append_cap_drop_total = _apply_append_target_cap(
        requirement=requirement,
        parsed_cases=parsed_result,
        append_final_cap_count=append_final_cap_count,
        analyze_coverage_fn=analyze_coverage,
        rule_diagnostics_fn=_rule_diagnostics_payload,
        rank_case_fn=_rank_review_case_for_fill,
        signature_fn=_signature,
    )
    flow_project_profile = dict(project_profile or {})
    feedback_redundant_caps = priority_pool_redundant_scenario_caps
    flow_project_profile = _build_flow_project_profile_for_governance(
        flow_project_profile,
        generation_coverage_mode=generation_coverage_mode,
        feedback_redundant_caps=feedback_redundant_caps,
    )
    try:
        parsed_result, flow_governance_summary = govern_cases_by_flow_structure(
            requirement,
            _dict_case_items(parsed_result),
            start_id=start_id,
            renumber_ids=True,
            max_per_scenario=2,
            project_profile=flow_project_profile,
        )
    except Exception as exc:
        flow_governance_summary = {
            "applied": False,
            "reason": "exception",
            "exception": _clip_text(exc, 200),
            "scenario_duplicate_pruned_count": 0,
            "flow_reordered": False,
        }

    recovery_pool_seed = [
        item
        for item in [*review_candidate_cases, *review_selection_input, *candidate_cases]
        if isinstance(item, dict)
    ]
    recovery_pool_unique_signature_count = int(
        len({_signature(item) for item in recovery_pool_seed if isinstance(item, dict) and _signature(item)})
    )
    final_floor_candidate_count = max(
        int(candidate_count_before_review or 0),
        int(recovery_pool_unique_signature_count or 0),
    )
    expected_min_floor_count = _resolve_expected_min_floor_for_recovery(
        expected_count_value=expected_count_value,
        effective_generation_coverage_mode=effective_generation_coverage_mode,
        valid_candidate_count=final_floor_candidate_count,
        full_regression_floor=resolved_full_regression_floor,
    )
    if int(expected_min_floor_count or 0) > 0 and not append:
        final_target_floor_count = max(
            int(final_target_floor_count or 0),
            int(expected_min_floor_count or 0),
        )
    if (
        int(expected_count or 0) > 0
        and effective_generation_coverage_mode in {"expanded_regression", "full_functional_regression"}
        and not append
    ):
        floor_ratio = 0.80 if effective_generation_coverage_mode == "expanded_regression" else 0.70
        final_target_floor_count = max(
            int(final_target_floor_count or 0),
            int(round(float(expected_count or 0) * floor_ratio)),
        )
        if effective_generation_coverage_mode == "full_functional_regression":
            full_regression_floor = resolved_full_regression_floor
            final_target_floor_count = max(int(full_regression_floor or 0), final_target_floor_count)
    if int(final_target_floor_count or 0) > 0 and not append:
        current_final_count = _dict_case_count(parsed_result)
        if current_final_count < final_target_floor_count:
            final_floor_recovery_attempted = True
            try:
                recovery_structure = analyze_case_structure(
                    requirement,
                    recovery_pool_seed,
                    project_profile=project_profile,
                )
                recovery_group_count = int(
                    len(
                        {
                            str(row.get("duplicate_group_key") or row.get("intent_signature") or row.get("scenario_key") or "")
                            for row in (recovery_structure.get("rows") or [])
                            if isinstance(row, dict)
                        }
                    )
                )
            except Exception:
                recovery_group_count = 0
            allow_relaxed_floor_recovery = bool(
                final_floor_candidate_count >= int(final_target_floor_count or 0)
                and int(expected_count_value or 0) > 0
            )
            if recovery_group_count >= final_target_floor_count or allow_relaxed_floor_recovery:
                final_signatures_before_recovery = {
                    _signature(item) for item in parsed_result if isinstance(item, dict)
                }
                recovery_seen_signatures = set(final_signatures_before_recovery)
                recovery_pool: list[dict[str, Any]] = []
                for source_case in recovery_pool_seed:
                    sig = _signature(source_case)
                    if not sig or sig in recovery_seen_signatures:
                        continue
                    recovery_seen_signatures.add(sig)
                    expected_text = _case_text_field(source_case, "expected_result")
                    expected_quality = str(source_case.get("expected_result_quality") or "").strip().lower()
                    if (
                        expected_quality in {"invalid_case", "non_assertable", "truncated"}
                        or _reasoning_leakage_hits(source_case)
                        or _looks_truncated_text(expected_text)
                        or _is_non_assertable_expected_result(expected_text)
                    ):
                        continue
                    recovery_pool.append(source_case)
                recovery_coverage = analyze_coverage(requirement, recovery_pool_seed)
                recovery_rule_diagnostics = _rule_diagnostics_payload(recovery_coverage)
                recovery_pool.sort(
                    key=lambda item: tuple(
                        [
                            -value
                            for value in _rank_review_case_for_fill(
                                item,
                                coverage_context=recovery_coverage,
                                rule_diagnostics=recovery_rule_diagnostics,
                            )
                        ]
                    )
                    + (_review_case_id(item),)
                )
                recovered: list[dict[str, Any]] = []
                for fill_case in recovery_pool:
                    if current_final_count + len(recovered) >= final_target_floor_count:
                        break
                    recovered.append(fill_case)
                if recovered:
                    merged_for_recovery = deduplicate_test_cases_fn(
                        [*parsed_result, *recovered]
                    )
                    merged_for_recovery = _apply_coverage_priority_semantics(
                        requirement,
                        merged_for_recovery,
                        analyze_coverage_fn=analyze_coverage,
                    )
                    try:
                        from ..judge.test_case_judge import judge_cases as recovery_judge_cases
                        from ..judge.test_case_repairer import repair_cases as recovery_repair_cases
                        from ..judge.training_gate import training_gate as recovery_training_gate

                        recovery_judged = recovery_judge_cases(
                            cases=_dict_case_items(merged_for_recovery),
                            requirement_semantics_context=requirement_semantics_context or {},
                            control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
                        )
                        recovery_repaired = recovery_repair_cases(
                            judged=recovery_judged,
                            requirement_semantics_context=requirement_semantics_context or {},
                            control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
                            strategy="rule_first_llm_fallback",
                        )
                        recovery_confirmed, recovery_repaired_pass, _recovery_rejected, _recovery_pending = recovery_training_gate(
                            recovery_repaired
                        )
                        merged_for_recovery = [*recovery_confirmed, *recovery_repaired_pass]
                    except Exception:
                        pass
                    parsed_result, flow_governance_summary = govern_cases_by_flow_structure(
                        requirement,
                        _dict_case_items(merged_for_recovery),
                        start_id=start_id,
                        renumber_ids=True,
                        max_per_scenario=2,
                        project_profile=flow_project_profile,
                    )
                    if _dict_case_count(parsed_result) < final_target_floor_count:
                        relaxed_flow_profile = _flow_profile_with_scenario_policy(
                            flow_project_profile,
                            coverage_mode=str(effective_generation_coverage_mode or ""),
                            disable_scenario_pruning=True,
                            intent_duplicate_cap=1,
                            relaxed_for_floor_backfill=True,
                        )
                        relaxed_result, relaxed_summary = govern_cases_by_flow_structure(
                            requirement,
                            _dict_case_items(merged_for_recovery),
                            start_id=start_id,
                            renumber_ids=True,
                            max_per_scenario=2,
                            project_profile=relaxed_flow_profile,
                        )
                        if _dict_case_count(relaxed_result) > _dict_case_count(parsed_result):
                            parsed_result = relaxed_result
                            flow_governance_summary = relaxed_summary
                            flow_governance_summary["relaxed_for_floor_backfill"] = True
                    final_floor_recovered_count = max(
                        0,
                        _dict_case_count(parsed_result) - current_final_count,
                    )
                    if int(final_floor_recovered_count or 0) > 0:
                        final_floor_recovery_applied = True
                        final_floor_recovery_reason = (
                            "recovered_with_relaxed_scenario_caps"
                            if bool(flow_governance_summary.get("relaxed_for_floor_backfill"))
                            else "recovered_to_explicit_expected_floor"
                        )
                    else:
                        final_floor_recovery_reason = "recovery_candidates_rejected_or_pruned"
                else:
                    final_floor_recovery_reason = "no_recoverable_candidates_after_quality_filter"
            else:
                final_floor_recovery_reason = "insufficient_diverse_candidate_groups"
    if (
        (
            effective_generation_coverage_mode == "full_functional_regression"
            or int(expected_count_value or 0) > 0
        )
        and int(final_target_floor_count or 0) > 0
        and not append
        and _dict_case_count(parsed_result) < int(final_target_floor_count or 0)
    ):
        current_shortfall_count = _dict_case_count(parsed_result)
        supplement_shortfall = max(1, int(final_target_floor_count or 0) - int(current_shortfall_count or 0))
        if supplement_shortfall <= 5:
            supplement_buffer = 3
        elif supplement_shortfall <= 20:
            supplement_buffer = 5
        else:
            supplement_buffer = max(5, int(round(float(supplement_shortfall) * 0.25)))
        supplement_needed = min(30, int(supplement_shortfall + supplement_buffer))
        final_shortfall_supplement_attempted = True
        existing_case_brief = [
            _case_review_brief(item, id_key="id", require_id=False)
            for item in _dict_case_items(parsed_result)[:140]
        ]
        supplement_coverage = analyze_coverage(
            requirement,
            _dict_case_items(parsed_result),
        )
        supplement_missing_rules = [
            str(item)
            for item in (supplement_coverage.get("missing_rules") or [])
            if str(item).strip()
        ][:30]
        supplement_missing_types_raw = supplement_coverage.get("missing_types")
        supplement_missing_types = {
            str(key): [str(item) for item in (value or []) if str(item).strip()][:20]
            for key, value in (
                dict(supplement_missing_types_raw or {}).items()
                if isinstance(supplement_missing_types_raw, dict)
                else []
            )
            if isinstance(value, list) and value
        }
        existing_module_counts: dict[str, int] = {}
        for item in _dict_case_items(parsed_result):
            module_key = _case_text_field(item, "test_module") or "unknown"
            existing_module_counts[module_key] = int(existing_module_counts.get(module_key) or 0) + 1
        supplement_prompt = f"""
FINAL_SHORTFALL_SUPPLEMENT:
- The current final set has {current_shortfall_count} cases, below the final floor {int(final_target_floor_count or 0)}.
- Generate up to {supplement_needed} additional high-value, non-duplicate test cases.
- Focus only on the current requirement and the missing coverage evidence below.
- Prefer under-covered business modules, independent functional paths, boundaries, exceptions, and cross-module state synchronization.
- Do not add display-only, copy/toast-only, sorting-only, thumbnail-only, or popup-only cases unless they close a blocking business flow.
- Do not include legacy behavior that conflicts with confirmed current requirements.
- P0 only for blocking main-path closure; otherwise use P1/P2.
- Return ONLY a strict JSON array of test cases with fields: id, description, test_module, preconditions, steps, test_input, expected_result, priority.

MISSING_RULES:
{_json_for_prompt(supplement_missing_rules, limit=8000)}

MISSING_TYPES:
{_json_for_prompt(supplement_missing_types, limit=4000)}

EXISTING_MODULE_COUNTS:
{_json_for_prompt(existing_module_counts, limit=4000)}

EXISTING_FINAL_CASES_TO_AVOID_DUPLICATING:
{_json_for_prompt(existing_case_brief, limit=14000)}
"""
        try:
            yield "@@STATUS@@:Final shortfall supplement started...\n"
            supplement_raw = client.generate_response(
                requirement,
                supplement_prompt,
                db=db,
                task_type="generation",
            )
            supplement_parsed = clean_and_parse_json_fn(str(supplement_raw or ""))
            supplement_parsed = normalize_json_structure_fn(supplement_parsed)
            if isinstance(supplement_parsed, list) and supplement_parsed:
                supplement_parsed = deduplicate_test_cases_fn(
                    _dict_case_items(supplement_parsed)
                )
                supplement_parsed = apply_priority_semantics_to_cases(
                    _dict_case_items(supplement_parsed),
                    attach_debug=False,
                )
                supplement_parsed, supplement_filter_stats = _filter_low_quality_cases_with_stats(
                    supplement_parsed,
                    requirement_text=requirement,
                    analyze_coverage_fn=analyze_coverage,
                )
                low_quality_filter_stats.accumulate(supplement_filter_stats)
                supplement_parsed, supplement_conflict_drop = _filter_cases_conflicting_with_confirmed_flow_facts(
                    _dict_case_items(supplement_parsed),
                    requirement=str(requirement or ""),
                    kb_context=str(kb_context or ""),
                    fact_profile=fact_profile,
                )
                final_confirmed_conflict_drop_count += int(supplement_conflict_drop or 0)
                existing_sigs = {_signature(item) for item in parsed_result if isinstance(item, dict)}
                unique_supplement: list[dict[str, Any]] = []
                for item in supplement_parsed:
                    sig = _signature(item)
                    if not sig or sig in existing_sigs:
                        continue
                    existing_sigs.add(sig)
                    unique_supplement.append(dict(item))
                if unique_supplement:
                    merged_shortfall = deduplicate_test_cases_fn([*parsed_result, *unique_supplement])
                    merged_shortfall = _apply_coverage_priority_semantics(
                        requirement,
                        merged_shortfall,
                        analyze_coverage_fn=analyze_coverage,
                    )
                    relaxed_flow_profile = _flow_profile_with_scenario_policy(
                        flow_project_profile,
                        coverage_mode=str(effective_generation_coverage_mode or ""),
                        disable_scenario_pruning=True,
                        intent_duplicate_cap=1,
                        relaxed_for_floor_backfill=True,
                    )
                    supplemented_result, supplemented_summary = govern_cases_by_flow_structure(
                        requirement,
                        _dict_case_items(merged_shortfall),
                        start_id=start_id,
                        renumber_ids=True,
                        max_per_scenario=2,
                        project_profile=relaxed_flow_profile,
                    )
                    if _dict_case_count(supplemented_result) > current_shortfall_count:
                        parsed_result = supplemented_result
                        flow_governance_summary = supplemented_summary
                        flow_governance_summary["relaxed_for_floor_backfill"] = True
                        final_shortfall_supplement_applied = True
                        final_shortfall_supplement_count = max(
                            0,
                            _dict_case_count(parsed_result) - current_shortfall_count,
                        )
                        final_floor_recovered_count = max(
                            int(final_floor_recovered_count or 0),
                            int(final_shortfall_supplement_count or 0),
                        )
                        final_floor_recovery_applied = True
                        final_floor_recovery_reason = "final_shortfall_supplement_generated"
                        yield f"@@STATUS@@:Final shortfall supplement added {final_shortfall_supplement_count} cases.\n"
                    else:
                        final_shortfall_supplement_reason = "supplement_pruned_or_duplicate"
                else:
                    final_shortfall_supplement_reason = "supplement_empty_after_filter"
            else:
                final_shortfall_supplement_reason = "supplement_empty_response"
        except Exception as supplement_err:
            final_shortfall_supplement_reason = f"exception:{_clip_text(supplement_err, 120)}"
    parsed_result, final_filter_conflict_drop_count = _filter_cases_conflicting_with_confirmed_flow_facts(
        _dict_case_items(parsed_result),
        requirement=str(requirement or ""),
        kb_context=str(kb_context or ""),
        fact_profile=fact_profile,
    )
    final_confirmed_conflict_drop_count += int(final_filter_conflict_drop_count or 0)
    if (
        int(final_target_floor_count or 0) > 0
        and effective_generation_coverage_mode in {"expanded_regression", "full_functional_regression"}
        and (
            effective_generation_coverage_mode == "full_functional_regression"
            or int(final_confirmed_conflict_drop_count or 0) > 0
        )
        and _dict_case_count(parsed_result) < int(final_target_floor_count or 0)
    ):
        current_after_conflict = _dict_case_count(parsed_result)
        recovery_pool_seed = [
            item
            for item in [*review_candidate_cases, *review_selection_input, *candidate_cases]
            if isinstance(item, dict)
        ]
        existing_after_conflict = {_signature(item) for item in parsed_result if isinstance(item, dict)}
        post_conflict_pool: list[dict[str, Any]] = []
        seen_after_conflict = set(existing_after_conflict)
        for source_case in recovery_pool_seed:
            sig = _signature(source_case)
            if not sig or sig in seen_after_conflict:
                continue
            seen_after_conflict.add(sig)
            expected_text = str(source_case.get("expected_result") or "").strip()
            expected_quality = str(source_case.get("expected_result_quality") or "").strip().lower()
            if (
                expected_quality in {"invalid_case", "non_assertable", "truncated"}
                or _reasoning_leakage_hits(source_case)
                or _looks_truncated_text(expected_text)
                or _is_non_assertable_expected_result(expected_text)
            ):
                continue
            post_conflict_pool.append(dict(source_case))
        post_conflict_pool, _post_conflict_pool_drop = _filter_cases_conflicting_with_confirmed_flow_facts(
            post_conflict_pool,
            requirement=str(requirement or ""),
            kb_context=str(kb_context or ""),
            fact_profile=fact_profile,
        )
        recovery_coverage = analyze_coverage(requirement, recovery_pool_seed)
        recovery_rule_diagnostics = _rule_diagnostics_payload(recovery_coverage)
        post_conflict_pool.sort(
            key=lambda item: tuple(
                [
                    -value
                    for value in _rank_review_case_for_fill(
                        item,
                        coverage_context=recovery_coverage,
                        rule_diagnostics=recovery_rule_diagnostics,
                    )
                ]
            )
            + (_review_case_id(item),)
        )
        recovered_after_conflict: list[dict[str, Any]] = []
        for fill_case in post_conflict_pool:
            if current_after_conflict + len(recovered_after_conflict) >= int(final_target_floor_count or 0):
                break
            recovered_after_conflict.append(fill_case)
        if recovered_after_conflict:
            merged_after_conflict = deduplicate_test_cases_fn([*parsed_result, *recovered_after_conflict])
            merged_after_conflict = reorder_cases_by_closed_loop_fn(
                _dict_case_items(merged_after_conflict),
                start_id=start_id,
                renumber_ids=True,
            )
            merged_after_conflict = _apply_coverage_priority_semantics(
                requirement,
                merged_after_conflict,
                analyze_coverage_fn=analyze_coverage,
            )
            if effective_generation_coverage_mode == "full_functional_regression":
                relaxed_flow_profile = _flow_profile_with_scenario_policy(
                    flow_project_profile,
                    coverage_mode=str(effective_generation_coverage_mode or ""),
                    disable_scenario_pruning=True,
                    intent_duplicate_cap=1,
                    relaxed_for_floor_backfill=True,
                )
                parsed_result, flow_governance_summary = govern_cases_by_flow_structure(
                    requirement,
                    _dict_case_items(merged_after_conflict),
                    start_id=start_id,
                    renumber_ids=True,
                    max_per_scenario=2,
                    project_profile=relaxed_flow_profile,
                )
                flow_governance_summary["relaxed_for_floor_backfill"] = True
            else:
                parsed_result, flow_governance_summary = govern_cases_by_flow_structure(
                    requirement,
                    _dict_case_items(merged_after_conflict),
                    start_id=start_id,
                    renumber_ids=True,
                    max_per_scenario=2,
                    project_profile=flow_project_profile,
                )
            final_floor_recovery_applied = True
            final_floor_recovery_reason = "recovered_after_confirmed_conflict_filter"
            final_floor_recovered_count = max(
                int(final_floor_recovered_count or 0),
                max(0, _dict_case_count(parsed_result) - current_after_conflict),
            )
    parsed_result, final_post_recovery_conflict_drop_count = _filter_cases_conflicting_with_confirmed_flow_facts(
        _dict_case_items(parsed_result),
        requirement=str(requirement or ""),
        kb_context=str(kb_context or ""),
        fact_profile=fact_profile,
    )
    final_confirmed_conflict_drop_count += int(final_post_recovery_conflict_drop_count or 0)
    final_invalid_quality_filtered_result, final_invalid_quality_drop_total = _filter_final_quality_cases(
        parsed_result,
        low_quality_filter_stats.low_quality_drop_details,
        stage="post_recovery_quality_filter",
    )
    if final_invalid_quality_drop_total > 0:
        parsed_result = final_invalid_quality_filtered_result
        low_quality_filter_stats.add_postprocess_quality_drop(final_invalid_quality_drop_total)
    parsed_result = reorder_cases_by_closed_loop_fn(
        _dict_case_items(parsed_result),
        start_id=start_id,
        renumber_ids=True,
    )
    parsed_result = _enforce_main_path_p0_anchors_rule(
        parsed_result,
        coverage_mode=str(effective_generation_coverage_mode or generation_coverage_mode or ""),
        requirement_text=str(requirement or ""),
        case_signature_fn=_signature,
        case_complexity_profile_fn=case_complexity_profile,
    )
    parsed_result = _preserve_review_priority_demotions(
        parsed_result,
        review_candidate_cases,
        case_signature_fn=_signature,
    )
    parsed_result = reorder_cases_by_closed_loop_fn(
        _dict_case_items(parsed_result),
        start_id=start_id,
        renumber_ids=True,
    )
    parsed_result, execution_plan_summary = _apply_execution_plan_metadata(
        _dict_case_items(parsed_result),
        start_id=start_id,
        coverage_mode=str(effective_generation_coverage_mode or generation_coverage_mode or ""),
    )
    parsed_result, final_order_flow_governance_summary = _apply_final_independent_case_ordering(
        parsed_result,
        requirement=str(requirement or ""),
        start_id=start_id,
        flow_project_profile=flow_project_profile,
        flow_profile_with_scenario_policy_fn=_flow_profile_with_scenario_policy,
        govern_cases_by_flow_structure_fn=govern_cases_by_flow_structure,
        case_execution_group_fn=_case_execution_group,
        clip_text_fn=_clip_text,
    )
    try:
        final_case_structure = analyze_case_structure(
            requirement,
            _dict_case_items(parsed_result),
            project_profile=project_profile,
        )
        final_independent_case_structure = analyze_case_structure(
            requirement,
            [
                x
                for x in parsed_result
                if isinstance(x, dict) and _case_execution_group(x) != "main_smoke"
            ],
            project_profile=project_profile,
        )
    except Exception:
        final_case_structure = {}
        final_independent_case_structure = {}
    parsed_result = _strip_case_meta_list(_dict_case_items(parsed_result))
    final_count = _dict_case_count(parsed_result)
    post_review_dedup_drop = max(0, int(review_selected_count or 0) - int(final_count or 0))

    review_candidate_coverage_context = analyze_coverage(
        requirement,
        _dict_case_items(review_candidate_cases),
    )
    review_candidate_rule_diagnostics = _rule_diagnostics_payload(review_candidate_coverage_context)
    try:
        review_case_structure = analyze_case_structure(
            requirement,
            _dict_case_items(review_candidate_cases),
            project_profile=project_profile,
        )
    except Exception:
        review_case_structure = {}
    review_decision_context = _build_review_decision_table_context(
        review_selection_input=review_selection_input,
        review_gate_trace=review_gate_trace,
        parsed_result=parsed_result,
        review_case_structure=review_case_structure,
    )
    selection_signatures = review_decision_context.selection_signatures
    trace_decisions = review_decision_context.trace_decisions
    selected_gate_signatures = review_decision_context.selected_gate_signatures
    dedup_drop_signatures = review_decision_context.dedup_drop_signatures
    final_signatures = review_decision_context.final_signatures
    final_priority_by_signature = review_decision_context.final_priority_by_signature
    structure_rows_by_index = review_decision_context.structure_rows_by_index

    for index, case in enumerate(review_candidate_cases, start=1):
        if not isinstance(case, dict):
            continue
        signature = _signature(case)
        structure_row = dict(structure_rows_by_index.get(int(index)) or {})
        gate_info = dict(trace_decisions.get(signature) or {})
        rule_keys = list(gate_info.get("rule_keys") or _extract_rule_keys(case))
        bucket = str(gate_info.get("bucket") or _coverage_bucket(case))
        high_signal = bool(gate_info.get("high_signal")) if gate_info else bool(_is_high_signal(case))
        adds_rule = bool(gate_info.get("adds_rule")) if gate_info else False
        adds_bucket = bool(gate_info.get("adds_bucket")) if gate_info else False
        gate_reason = str(gate_info.get("drop_reason") or "")
        retained = signature in final_signatures
        drop_decision = _resolve_review_candidate_drop_decision(
            signature=signature,
            review_llm_applied=review_llm_applied,
            review_llm_selected_signatures=review_llm_selected_signatures,
            review_must_keep_signatures=review_must_keep_signatures,
            review_constraint_retained_signatures=review_constraint_retained_signatures,
            review_constraint_reason_map=review_constraint_reason_map,
            review_llm_drop_reason_raw_map=review_llm_drop_reason_raw_map,
            review_llm_drop_reason_map=review_llm_drop_reason_map,
            review_llm_drop_reason_source_map=review_llm_drop_reason_source_map,
            review_llm_drop_reason_evidence_map=review_llm_drop_reason_evidence_map,
            selection_signatures=selection_signatures,
            append_cap_drop_signatures=append_cap_drop_signatures,
            final_description_dedup_drop_signatures=final_description_dedup_drop_signatures,
            dedup_drop_signatures=dedup_drop_signatures,
            selected_gate_signatures=selected_gate_signatures,
            final_signatures=final_signatures,
            gate_reason=gate_reason,
        )
        selected_by_review_llm = drop_decision.selected_by_review_llm
        selected_by_review_must_keep = drop_decision.selected_by_review_must_keep
        selected_by_review_constraints = drop_decision.selected_by_constraint_guard
        review_llm_drop_reason_raw = drop_decision.review_llm_drop_reason_raw
        review_llm_drop_reason = drop_decision.review_llm_drop_reason
        review_llm_drop_reason_source = drop_decision.review_llm_drop_reason_source
        review_llm_drop_reason_evidence = drop_decision.review_llm_drop_reason_evidence
        has_coverage_signal = drop_decision.has_coverage_signal
        has_high_signal = drop_decision.has_high_signal
        has_competition_signal = drop_decision.has_competition_signal
        has_positive_evidence = drop_decision.has_positive_evidence
        review_constraint_reason = drop_decision.review_constraint_reason
        dropped_stage = drop_decision.dropped_stage
        dropped_reason = drop_decision.dropped_reason

        score_profile = score_case_priority(
            case,
            coverage_context=review_candidate_coverage_context,
            rule_diagnostics=review_candidate_rule_diagnostics,
        )
        priority_fields = _resolve_review_priority_fields(
            case=case,
            signature=signature,
            retained=retained,
            final_priority_by_signature=final_priority_by_signature,
        )
        model_priority_value = priority_fields.model_priority_value
        legacy_priority_value = priority_fields.legacy_priority_value
        priority_final_value = priority_fields.priority_final_value
        priority_decision_state_value = priority_fields.priority_decision_state_value
        priority_decision_source_value = priority_fields.priority_decision_source_value
        priority_confidence_value = priority_fields.priority_confidence_value
        priority_conflict_reason_value = priority_fields.priority_conflict_reason_value
        priority_resolution_reason_value = priority_fields.priority_resolution_reason_value
        unresolved_priority_decision = priority_fields.unresolved_priority_decision
        hit_must_cover_rule = bool(
            _hit_must_cover_rule(
                rule_keys,
                score_profile,
                must_cover_rule_set=must_cover_rule_set,
            )
        )
        violates_forbidden_pattern = bool(_violates_forbidden_pattern(case))
        hits_soft_constraint = bool(_hits_soft_constraint(case))
        satisfies_quality_hint = bool(_satisfies_quality_hint(case))
        retention_fields = _resolve_review_row_coverage_retention_fields(
            gate_info=gate_info,
            score_profile=score_profile,
            retained=retained,
            adds_rule=adds_rule,
            adds_bucket=adds_bucket,
        )
        has_coverage_value_for_row = retention_fields.has_coverage_value_for_row
        retained_reason_value = retention_fields.retained_reason_value
        row = _build_review_candidate_row_base_fields(
            index=index,
            case=case,
            signature=signature,
            structure_row=structure_row,
            gate_info=gate_info,
            rule_keys=rule_keys,
            bucket=bucket,
            adds_rule=adds_rule,
            adds_bucket=adds_bucket,
            high_signal=high_signal,
            has_coverage_value=has_coverage_value_for_row,
            retained_reason=retained_reason_value,
            review_case_id_fn=_review_case_id,
            case_text_field_fn=_case_text_field,
            focus_score_fn=_focus_score,
        )
        row.update(_build_review_candidate_row_diagnostic_fields(
            model_priority_value=model_priority_value,
            legacy_priority_value=legacy_priority_value,
            priority_final_value=priority_final_value,
            priority_decision_state_value=priority_decision_state_value,
            priority_decision_source_value=priority_decision_source_value,
            priority_confidence_value=priority_confidence_value,
            priority_conflict_reason_value=priority_conflict_reason_value,
            priority_resolution_reason_value=priority_resolution_reason_value,
            score_profile=score_profile,
            selected_by_review_llm=selected_by_review_llm,
            selected_by_review_must_keep=selected_by_review_must_keep,
            selected_by_review_constraints=selected_by_review_constraints,
            review_constraint_reason=review_constraint_reason,
            review_llm_drop_reason_raw=review_llm_drop_reason_raw,
            review_llm_drop_reason=review_llm_drop_reason,
            review_llm_drop_reason_source=review_llm_drop_reason_source,
            review_llm_drop_reason_evidence=review_llm_drop_reason_evidence,
            has_positive_evidence=has_positive_evidence,
            has_coverage_signal=has_coverage_signal,
            has_high_signal=has_high_signal,
            has_competition_signal=has_competition_signal,
            review_llm_applied=review_llm_applied,
            signature=signature,
            review_must_keep_signatures=review_must_keep_signatures,
            review_must_keep_reason_map=review_must_keep_reason_map,
            selected_gate_signatures=selected_gate_signatures,
            retained=retained,
            dropped_stage=dropped_stage,
            dropped_reason=dropped_reason,
            hit_must_cover_rule=hit_must_cover_rule,
            violates_forbidden_pattern=violates_forbidden_pattern,
            hits_soft_constraint=hits_soft_constraint,
            satisfies_quality_hint=satisfies_quality_hint,
        ))
        review_decision_table.append(row)

    dropped_rows = [row for row in review_decision_table if not bool(row.get("retained_final"))]
    final_dedup_priority_summary = _summarize_final_description_dedup_and_priority_breakdown(
        review_decision_table,
    )
    final_description_dedup_drop_signatures = set(
        final_dedup_priority_summary.get("final_description_dedup_drop_signatures") or set()
    )
    priority_summary_fields = _final_dedup_priority_summary_fields(final_dedup_priority_summary)
    priority_summary_flags = _resolve_review_priority_summary_flags(priority_summary_fields)
    priority_conflict_count = priority_summary_flags.priority_conflict_count
    priority_undetermined_count = priority_summary_flags.priority_undetermined_count
    priority_optional_count = priority_summary_flags.priority_optional_count
    needs_priority_review = priority_summary_flags.needs_priority_review
    review_llm_drop_diagnostics = _summarize_review_llm_drop_diagnostics(
        review_llm_applied=bool(review_llm_applied),
        review_llm_omitted_signatures=review_llm_omitted_signatures,
        dropped_rows=dropped_rows,
        review_llm_drop_reason_map=review_llm_drop_reason_map,
        review_llm_drop_reason_raw_map=review_llm_drop_reason_raw_map,
        review_llm_drop_reason_source_map=review_llm_drop_reason_source_map,
        review_llm_drop_reason_evidence_map=review_llm_drop_reason_evidence_map,
        review_llm_runtime_debug=review_llm_runtime_debug,
    )
    review_llm_runtime_debug.update(dict(review_llm_drop_diagnostics.get("runtime_debug_updates") or {}))
    review_llm_summary_fields = _review_llm_drop_summary_fields(
        review_llm_drop_diagnostics,
        review_llm_runtime_debug,
    )
    drop_by_review_llm_count = int(review_llm_drop_diagnostics.get("drop_by_review_llm_count") or 0)
    drop_by_review_selector_count = int(review_llm_drop_diagnostics.get("drop_by_review_selector_count") or 0)
    final_duplicate_project_profile = _resolve_final_duplicate_project_profile(
        flow_project_profile=flow_project_profile,
        flow_governance_summary=flow_governance_summary,
        final_shortfall_supplement_applied=bool(final_shortfall_supplement_applied),
        effective_generation_coverage_mode=effective_generation_coverage_mode,
        flow_profile_with_scenario_policy_fn=_flow_profile_with_scenario_policy,
    )
    final_duplicate_excess = summarize_duplicate_excess_by_policy(
        final_case_structure,
        project_profile=final_duplicate_project_profile,
        default_max=2,
    )
    review_flow_summary_fields = _review_flow_structure_summary_fields(
        review_case_structure=review_case_structure,
        final_independent_case_structure=final_independent_case_structure,
        final_duplicate_excess=final_duplicate_excess,
        final_case_structure=final_case_structure,
        final_order_flow_governance_summary=final_order_flow_governance_summary,
        fact_profile=fact_profile,
        project_profile=project_profile,
        flow_governance_summary=flow_governance_summary,
        execution_plan_summary=execution_plan_summary,
    )
    review_decision_counts = _summarize_review_decision_counts(
        review_decision_table,
        dropped_rows,
        ui_like_ratio_postprocess_drop_count=ui_like_ratio_postprocess_drop_count,
        final_description_dedup_drop_signatures=final_description_dedup_drop_signatures,
        drop_by_review_llm_count=drop_by_review_llm_count,
        drop_by_review_selector_count=drop_by_review_selector_count,
    )
    review_decision_summary = _build_review_decision_summary_payload(
        review_decision_table=review_decision_table,
        dropped_rows=dropped_rows,
        review_flow_summary_fields=review_flow_summary_fields,
        parsed_result=parsed_result,
        reasoning_leakage_hits_fn=_reasoning_leakage_hits,
        priority_summary_fields=priority_summary_fields,
        needs_priority_review=needs_priority_review,
        review_llm_applied=review_llm_applied,
        review_selection_input=review_selection_input,
        dict_case_count_fn=_dict_case_count,
        review_selected_count=review_selected_count,
        review_target_min_count=review_target_min_count,
        review_target_max_count=review_target_max_count,
        review_shortfall_detected=review_shortfall_detected,
        review_shortfall_before_count=review_shortfall_before_count,
        review_shortfall_recovered_count=review_shortfall_recovered_count,
        review_post_rerank_floor_count=review_post_rerank_floor_count,
        review_post_rerank_recovered_count=review_post_rerank_recovered_count,
        final_target_floor_count=final_target_floor_count,
        final_floor_recovery_attempted=final_floor_recovery_attempted,
        final_floor_recovery_applied=final_floor_recovery_applied,
        final_floor_recovered_count=final_floor_recovered_count,
        final_floor_recovery_reason=final_floor_recovery_reason,
        final_confirmed_conflict_drop_count=final_confirmed_conflict_drop_count,
        final_shortfall_supplement_attempted=final_shortfall_supplement_attempted,
        final_shortfall_supplement_applied=final_shortfall_supplement_applied,
        final_shortfall_supplement_count=final_shortfall_supplement_count,
        final_shortfall_supplement_reason=final_shortfall_supplement_reason,
        generation_mode=generation_mode,
        effective_generation_coverage_mode_source=effective_generation_coverage_mode_source,
        explicit_generation_mode_override=explicit_generation_mode_override,
        explicit_expected_count_floor_preserved=explicit_expected_count_floor_preserved,
        review_fill_source=review_fill_source,
        review_llm_selected_signatures=review_llm_selected_signatures,
        review_llm_runtime_debug=review_llm_runtime_debug,
        review_constraint_retained_signatures=review_constraint_retained_signatures,
        review_llm_summary_fields=review_llm_summary_fields,
        review_llm_pool_count=review_llm_pool_count,
        stage_counts=stage_counts,
        review_decision_counts=review_decision_counts,
    )

    final_coverage_convergence_inputs = _derive_final_coverage_convergence_inputs(
        pre_priority_coverage=pre_priority_coverage,
        reference_count_effective=reference_count_effective,
        final_count=final_count,
        gap_remaining_after_attempts=gap_remaining_after_attempts,
        gap_attempts=gap_attempts,
        gap_stopped_by_provider_error=gap_stopped_by_provider_error,
    )
    coverage = final_coverage_convergence_inputs["coverage"]
    missing_rules_final = final_coverage_convergence_inputs["missing_rules_final"]
    missing_types_final = final_coverage_convergence_inputs["missing_types_final"]
    reference_gap = final_coverage_convergence_inputs["reference_gap"]
    converged = final_coverage_convergence_inputs["converged"]
    reasons = final_coverage_convergence_inputs["reasons"]

    convergence_reason_state = _derive_convergence_reason_state(
        reasons=reasons,
        converged=converged,
        reference_gap=reference_gap,
        post_review_dedup_drop=post_review_dedup_drop,
        final_description_dedup_drop_signatures=final_description_dedup_drop_signatures,
        low_quality_drop_details=low_quality_filter_stats.low_quality_drop_details,
        low_quality_dropped_total=low_quality_filter_stats.low_quality_dropped_total,
        semantic_dedup_dropped_total=low_quality_filter_stats.semantic_dedup_dropped_total,
        governance_hard_drop_total=low_quality_filter_stats.governance_hard_drop_total,
        postprocess_filter_drop_total=low_quality_filter_stats.postprocess_filter_drop_total,
        append_cap_drop_total=append_cap_drop_total,
        flow_governance_summary=flow_governance_summary,
        review_selected_count=review_selected_count,
    )
    reasons = convergence_reason_state["reasons"]
    final_description_dedup_drop = convergence_reason_state["final_description_dedup_drop"]
    total_dedup_drop = convergence_reason_state["total_dedup_drop"]
    low_quality_drop_details = convergence_reason_state["low_quality_drop_details"]
    low_quality_dropped_total = convergence_reason_state["low_quality_dropped_total"]
    semantic_dedup_dropped_total = convergence_reason_state["semantic_dedup_dropped_total"]
    governance_hard_drop_total = convergence_reason_state["governance_hard_drop_total"]
    postprocess_filter_drop_total = convergence_reason_state["postprocess_filter_drop_total"]
    effective_low_quality_dropped_total = convergence_reason_state["effective_low_quality_dropped_total"]
    duplication_rate_estimate = convergence_reason_state["duplication_rate_estimate"]
    summary_stop_reason = convergence_reason_state["summary_stop_reason"]
    quality_assessment = convergence_reason_state["quality_assessment"]
    priority_summary_flags = _resolve_review_priority_summary_flags(review_decision_summary)
    priority_conflict_count = priority_summary_flags.priority_conflict_count
    priority_undetermined_count = priority_summary_flags.priority_undetermined_count
    priority_optional_count = priority_summary_flags.priority_optional_count
    needs_priority_review = priority_summary_flags.needs_priority_review

    target_satisfaction_state = _resolve_generation_target_satisfaction(
        generation_target_case_range=generation_target_case_range,
        expected_count=expected_count,
        reference_count_effective=reference_count_effective,
        generation_coverage_mode=generation_coverage_mode,
        resolved_full_regression_floor=resolved_full_regression_floor,
        candidate_count_before_review=candidate_count_before_review,
        post_review_dedup_drop=post_review_dedup_drop,
        final_description_dedup_drop=final_description_dedup_drop,
        semantic_dedup_dropped_total=semantic_dedup_dropped_total,
        flow_governance_summary=flow_governance_summary,
        effective_low_quality_dropped_total=effective_low_quality_dropped_total,
        governance_hard_drop_total=governance_hard_drop_total,
        final_count=final_count,
    )
    target_min = target_satisfaction_state["target_min"]
    target_max = target_satisfaction_state["target_max"]
    recommended_range = target_satisfaction_state["recommended_range"]
    target_min_count = target_satisfaction_state["target_min_count"]
    target_max_count = target_satisfaction_state["target_max_count"]
    expected_count_explicit = target_satisfaction_state["expected_count_explicit"]
    target_final_count = target_satisfaction_state["target_final_count"]
    soft_min_count = target_satisfaction_state["soft_min_count"]
    hard_min_count = target_satisfaction_state["hard_min_count"]
    valid_unique_candidate_count = target_satisfaction_state["valid_unique_candidate_count"]
    postprocess_pruned_count = target_satisfaction_state["postprocess_pruned_count"]
    recommended_floor_underfilled = target_satisfaction_state["recommended_floor_underfilled"]
    min_acceptable_final = target_satisfaction_state["min_acceptable_final"]
    target_satisfaction_ratio = target_satisfaction_state["target_satisfaction_ratio"]
    target_warning = target_satisfaction_state["target_warning"]
    underfilled = target_satisfaction_state["underfilled"]
    final_stage_pruning_counts = _resolve_final_stage_pruning_counts(
        effective_low_quality_dropped_total=effective_low_quality_dropped_total,
        governance_hard_drop_total=governance_hard_drop_total,
        judge_summary_payload=judge_summary_payload,
        review_selected_count=review_selected_count,
        final_count=final_count,
        flow_governance_summary=flow_governance_summary,
        final_description_dedup_drop=final_description_dedup_drop,
        drop_by_review_llm_count=drop_by_review_llm_count,
        review_decision_summary=review_decision_summary,
        total_dedup_drop=total_dedup_drop,
        semantic_dedup_dropped_total=semantic_dedup_dropped_total,
        postprocess_filter_drop_total=postprocess_filter_drop_total,
    )
    quality_rejected_count = final_stage_pruning_counts["quality_rejected_count"]
    judge_reject_count = final_stage_pruning_counts["judge_reject_count"]
    judge_pending_count = final_stage_pruning_counts["judge_pending_count"]
    judge_pass_count = final_stage_pruning_counts["judge_pass_count"]
    final_input_count = final_stage_pruning_counts["final_input_count"]
    final_non_judge_drop_count = final_stage_pruning_counts["final_non_judge_drop_count"]
    scenario_duplicate_pruned_count = final_stage_pruning_counts["scenario_duplicate_pruned_count"]
    post_review_dedup_reorder_drop_count = final_stage_pruning_counts[
        "post_review_dedup_reorder_drop_count"
    ]
    review_selector_pruned_count = final_stage_pruning_counts["review_selector_pruned_count"]
    duplicate_pruned_count = final_stage_pruning_counts["duplicate_pruned_count"]
    invalid_pruned_count = final_stage_pruning_counts["invalid_pruned_count"]
    underfill_diagnostics = _resolve_underfill_diagnostics(
        underfilled=underfilled,
        valid_unique_candidate_count=valid_unique_candidate_count,
        hard_min_count=hard_min_count,
        scenario_duplicate_pruned_count=scenario_duplicate_pruned_count,
        review_selector_pruned_count=review_selector_pruned_count,
        duplicate_pruned_count=duplicate_pruned_count,
        quality_rejected_count=quality_rejected_count,
        final_non_judge_drop_count=final_non_judge_drop_count,
        min_acceptable_final=min_acceptable_final,
        final_count=final_count,
    )
    underfill_reason = underfill_diagnostics["underfill_reason"]
    underfill_root_cause = underfill_diagnostics["underfill_root_cause"]
    underfill_level = underfill_diagnostics["underfill_level"]
    completion_reason_lists = _resolve_completion_reason_lists(
        reasons=reasons,
        summary_stop_reason=summary_stop_reason,
        underfilled=underfilled,
        target_warning=target_warning,
    )
    reasons = completion_reason_lists["reasons"]
    summary_stop_reason = completion_reason_lists["summary_stop_reason"]

    final_case_summary = _final_case_breakdown(
        _dict_case_items(parsed_result),
        final_count=int(final_count or 0),
    )

    generation_summary = _build_generation_summary(
        recommended_range=recommended_range,
        generation_coverage_mode=generation_coverage_mode,
        generation_mode=generation_mode,
        effective_generation_coverage_mode_source=effective_generation_coverage_mode_source,
        explicit_generation_mode_override=explicit_generation_mode_override,
        explicit_expected_count_floor_preserved=explicit_expected_count_floor_preserved,
        expected_count=expected_count,
        expected_count_explicit=expected_count_explicit,
        target_min=target_min,
        target_max=target_max,
        target_final_count=target_final_count,
        soft_min_count=soft_min_count,
        hard_min_count=hard_min_count,
        min_acceptable_final=min_acceptable_final,
        target_satisfaction_ratio=target_satisfaction_ratio,
        underfilled=underfilled,
        underfill_level=underfill_level,
        underfill_reason=underfill_reason,
        underfill_root_cause=underfill_root_cause,
        final_count=final_count,
        converged=converged,
        summary_stop_reason=summary_stop_reason,
        quality_assessment=quality_assessment,
        needs_priority_review=needs_priority_review,
        priority_conflict_count=priority_conflict_count,
        priority_undetermined_count=priority_undetermined_count,
        priority_optional_count=priority_optional_count,
        final_case_summary=final_case_summary,
    )

    convergence_debug = _build_convergence_debug(
        reference_count_effective=reference_count_effective,
        final_count=final_count,
        reference_gap=reference_gap,
        converged=converged,
        duplication_rate_estimate=duplication_rate_estimate,
        stage_counts=stage_counts,
        candidate_count_before_review=candidate_count_before_review,
        review_selected_count=review_selected_count,
        post_review_dedup_drop=post_review_dedup_drop,
        judge_reject_count=judge_reject_count,
        judge_pending_count=judge_pending_count,
        judge_pass_count=judge_pass_count,
        final_input_count=final_input_count,
        final_non_judge_drop_count=final_non_judge_drop_count,
        scenario_duplicate_pruned_count=scenario_duplicate_pruned_count,
        post_review_dedup_reorder_drop_count=post_review_dedup_reorder_drop_count,
        final_description_dedup_drop=final_description_dedup_drop,
        total_dedup_drop=total_dedup_drop,
        gap_attempts=gap_attempts,
        gap_remaining_after_attempts=gap_remaining_after_attempts,
        missing_rules_final=missing_rules_final,
        missing_types_final=missing_types_final,
        effective_low_quality_dropped_total=effective_low_quality_dropped_total,
        low_quality_drop_details=low_quality_drop_details,
        postprocess_filter_drop_total=postprocess_filter_drop_total,
        semantic_dedup_dropped_total=semantic_dedup_dropped_total,
        governance_hard_drop_total=governance_hard_drop_total,
        duplicate_pruned_count=duplicate_pruned_count,
        invalid_pruned_count=invalid_pruned_count,
        quality_rejected_count=quality_rejected_count,
        review_selector_pruned_count=review_selector_pruned_count,
        valid_unique_candidate_count=valid_unique_candidate_count,
        generation_coverage_mode=generation_coverage_mode,
        generation_mode=generation_mode,
        effective_generation_coverage_mode_source=effective_generation_coverage_mode_source,
        explicit_generation_mode_override=explicit_generation_mode_override,
        explicit_expected_count_floor_preserved=explicit_expected_count_floor_preserved,
        expected_count=expected_count,
        expected_count_explicit=expected_count_explicit,
        target_min=target_min,
        target_max=target_max,
        target_final_count=target_final_count,
        soft_min_count=soft_min_count,
        hard_min_count=hard_min_count,
        min_acceptable_final=min_acceptable_final,
        target_satisfaction_ratio=target_satisfaction_ratio,
        underfilled=underfilled,
        underfill_level=underfill_level,
        underfill_reason=underfill_reason,
        underfill_root_cause=underfill_root_cause,
        append_target_count=append_target_count,
        append_final_cap_count=append_final_cap_count,
        append_cap_drop_total=append_cap_drop_total,
        flow_governance_summary=flow_governance_summary,
        needs_priority_review=needs_priority_review,
        priority_conflict_count=priority_conflict_count,
        priority_undetermined_count=priority_undetermined_count,
        priority_optional_count=priority_optional_count,
        reasons=reasons,
        generation_target_case_range=generation_target_case_range,
    )
    return _build_stream_postprocess_result_payload(
        cases=parsed_result,
        stage_counts=stage_counts,
        coverage=coverage,
        convergence_debug=convergence_debug,
        generation_summary=generation_summary,
        review_decision_summary=review_decision_summary,
        review_decision_table=review_decision_table,
        judge_summary=judge_summary_payload,
        judge_decision_table=judge_decision_table_payload,
        feedback_control_debug_builder_fn=_build_feedback_control_debug_payload,
        control_state=control_state,
        generation_coverage_mode=generation_coverage_mode,
        generation_target_case_range=generation_target_case_range,
        fact_profile=fact_profile,
        project_profile=project_profile,
        manual_quality_profile=manual_quality_profile,
    )
