from __future__ import annotations

import time
from typing import Any, Callable, Iterator

from .result_postprocess_priority_semantics import score_case_priority
from .streaming_priority_semantics import (
    apply_coverage_priority_semantics as _apply_coverage_priority_semantics,
)
from .streaming_postprocess_utils import (
    _clip_text,
    _dict_case_count,
    _flow_profile_with_scenario_policy,
    _dict_case_items,
    _merged_unique_total,
    build_feedback_control_debug_payload as _build_feedback_control_debug_payload,
    build_flow_project_profile_for_governance as _build_flow_project_profile_for_governance,
)
from .streaming_postprocess_timing import record_timing_event as _record_streaming_timing_event
from .streaming_rescue_pass import run_initial_rescue_pass as _run_initial_rescue_pass
from .streaming_control_context import resolve_streaming_control_context
from .streaming_coverage_gap import resolve_coverage_gap_state as _resolve_coverage_gap_state
from .streaming_case_keys import (
    case_focus_score as _focus_score,
    review_case_id as _review_case_id,
)
from .streaming_final_case_assembly import assemble_final_cases as _assemble_final_cases
from .streaming_final_pruning import apply_post_judge_final_pruning as _apply_post_judge_final_pruning
from .streaming_final_recovery_stage import run_final_recovery_stage as _run_final_recovery_stage
from .streaming_postprocess_result_payload import (
    build_stream_postprocess_result_payload as _build_stream_postprocess_result_payload,
)
from .streaming_generation_report import (
    FinalGenerationReportInputs,
    build_final_generation_report as _build_final_generation_report,
)
from .streaming_gap_supplement import (
    run_gap_supplement_attempts as _run_gap_supplement_attempts,
)
from .streaming_judge_summary import (
    build_judge_decision_table_payload as _build_judge_decision_table_payload,
    build_judge_summary_payload as _build_judge_summary_payload,
)
from .streaming_judge_gate import run_streaming_judge_gate as _run_streaming_judge_gate
from .streaming_initial_parse_stage import run_initial_parse_stage as _run_initial_parse_stage
from .streaming_postprocess_state import init_stream_postprocess_state as _init_stream_postprocess_state
from .streaming_review_stage import run_streaming_review_stage as _run_streaming_review_stage
from .streaming_review_summary_assembly import assemble_review_summary_state as _assemble_review_summary_state
from .streaming_review_selection import (
    hit_must_cover_rule as _hit_must_cover_rule,
    is_high_signal as _is_high_signal,
    rank_review_case_for_fill as _rank_review_case_for_fill,
)
from .streaming_reasoning_quality import reasoning_leakage_hits as _reasoning_leakage_hits
from .streaming_text_match import CaseGovernanceMatcher
from .streaming_ui_like import apply_ui_like_ratio_postprocess_cap as _apply_ui_like_ratio_postprocess_cap
from .module_contract import enforce_functional_module_contract, rebalance_functional_phase_coverage


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

    postprocess_started = time.perf_counter()
    timing_events: list[dict[str, Any]] = []

    def _record_timing_event(stage: str, started_at: float, **fields: Any) -> dict[str, Any]:
        return _record_streaming_timing_event(timing_events, stage, started_at, **fields)

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


    initial_parse_stage = _run_initial_parse_stage(
        full_content=full_content,
        requirement=requirement,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        analyze_coverage_fn=analyze_coverage,
        record_timing_event_fn=_record_timing_event,
    )
    parsed_result = initial_parse_stage.parsed_result
    low_quality_filter_stats = initial_parse_stage.low_quality_filter_stats

    initial_state = _init_stream_postprocess_state(
        parsed_result=parsed_result,
        append=append,
        expected_count=expected_count,
        existing_unique_count=existing_unique_count,
        generation_mode=generation_mode,
        generation_coverage_mode=generation_coverage_mode,
        generation_target_case_range=generation_target_case_range,
    )
    stage_counts = initial_state.stage_counts
    gap_attempts = initial_state.gap_attempts
    gap_remaining_after_attempts = initial_state.gap_remaining_after_attempts
    gap_stopped_by_provider_error = initial_state.gap_stopped_by_provider_error
    candidate_count_before_review = initial_state.candidate_count_before_review
    append_target_count = initial_state.append_target_count
    reference_count_effective = initial_state.reference_count_effective
    append_final_cap_count = initial_state.append_final_cap_count
    expected_count_value = initial_state.expected_count_value
    effective_generation_coverage_mode = initial_state.effective_generation_coverage_mode
    effective_generation_coverage_mode_source = initial_state.effective_generation_coverage_mode_source
    explicit_generation_mode_override = initial_state.explicit_generation_mode_override
    generation_coverage_mode = initial_state.generation_coverage_mode
    explicit_expected_count_floor_preserved = initial_state.explicit_expected_count_floor_preserved
    resolved_full_regression_floor = initial_state.resolved_full_regression_floor

    final_target_floor_count = 0

    current_total = _merged_unique_total(
        parsed_result,
        append=append,
        existing_cases=existing_cases,
        count_unique_test_cases_fn=count_unique_test_cases_fn,
    )
    if current_total == 0 and int(expected_count or 0) > 0:
        yield "@@STATUS@@:Initial streaming result is empty, trying one non-stream rescue pass...\n"
        try:
            rescue_result = _run_initial_rescue_pass(
                client=client,
                requirement=requirement,
                base_prompt=base_prompt,
                db=db,
                append=append,
                existing_cases=existing_cases,
                clean_and_parse_json_fn=clean_and_parse_json_fn,
                normalize_json_structure_fn=normalize_json_structure_fn,
                deduplicate_test_cases_fn=deduplicate_test_cases_fn,
                count_unique_test_cases_fn=count_unique_test_cases_fn,
                analyze_coverage_fn=analyze_coverage,
            )
            if rescue_result is not None:
                low_quality_filter_stats.accumulate(rescue_result.filter_stats)
                parsed_result = rescue_result.cases
                stage_counts["primary"] = len(parsed_result)
                current_total = rescue_result.current_total
                yield f"@@STATUS@@:Rescue succeeded, recovered {len(parsed_result)} cases.\n"
        except Exception as rescue_err:
            yield f"@@STATUS@@:Rescue failed ({str(rescue_err)}), continue pipeline.\n"

    normalized_mode = str(generation_mode or "").strip().lower()
    if normalized_mode not in {"single_pass", "multi_pass", "biz_key_multi_pass"}:
        normalized_mode = "multi_pass" if bool(multi_pass) else "single_pass"

    if normalized_mode in {"multi_pass", "biz_key_multi_pass"} and isinstance(parsed_result, list):
        coverage_primary = analyze_coverage(requirement, _dict_case_items(parsed_result))
        coverage_gap_state = _resolve_coverage_gap_state(coverage_primary)
        missing_rules = coverage_gap_state["missing_rules"]
        has_missing_types = bool(coverage_gap_state["has_missing_types"])

        need_gap = bool(missing_rules) or has_missing_types
        if need_gap:
            gap_result = yield from _run_gap_supplement_attempts(
                client=client,
                requirement=requirement,
                append=append,
                existing_cases=existing_cases,
                parsed_result=_dict_case_items(parsed_result),
                coverage_primary=coverage_primary,
                coverage_gap_state=coverage_gap_state,
                current_biz_key=current_biz_key,
                infer_case_kind_fn=infer_case_kind_fn,
                build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction_fn,
                build_gap_fill_prompt_fn=build_gap_fill_prompt,
                clean_and_parse_json_fn=clean_and_parse_json_fn,
                normalize_json_structure_fn=normalize_json_structure_fn,
                deduplicate_test_cases_fn=deduplicate_test_cases_fn,
                analyze_coverage_fn=analyze_coverage,
                resolve_coverage_gap_state_fn=_resolve_coverage_gap_state,
                record_timing_event_fn=_record_timing_event,
            )
            parsed_result = gap_result.cases
            coverage_primary = gap_result.coverage_primary
            coverage_gap_state = gap_result.coverage_gap_state
            gap_attempts = gap_result.attempt_count
            gap_remaining_after_attempts = gap_result.remaining_gap_count
            gap_stopped_by_provider_error = gap_result.stopped_by_provider_error
            stage_counts["gap"] = gap_result.added_count
            for filter_stats in gap_result.filter_stats:
                low_quality_filter_stats.accumulate(filter_stats)

    review_stage_result = yield from _run_streaming_review_stage(
        client=client,
        db=db,
        requirement=requirement,
        parsed_result=_dict_case_items(parsed_result),
        normalized_mode=normalized_mode,
        append=append,
        expected_count=expected_count,
        existing_cases=existing_cases,
        existing_unique_count=existing_unique_count,
        reference_count_effective=reference_count_effective,
        must_cover_rule_set=must_cover_rule_set,
        generation_coverage_profile=generation_coverage_profile,
        generation_coverage_mode=generation_coverage_mode,
        append_target_count=append_target_count,
        append_final_cap_count=append_final_cap_count,
        current_biz_key=current_biz_key,
        build_review_select_prompt_fn=build_review_select_prompt,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        analyze_coverage_fn=analyze_coverage,
        score_case_priority_fn=score_case_priority,
        hits_reuse_risk_fn=_hits_reuse_risk,
        hits_soft_constraint_fn=_hits_soft_constraint,
        record_timing_event_fn=_record_timing_event,
    )
    low_quality_filter_stats.accumulate(review_stage_result.review_filter_stats)
    parsed_result = review_stage_result.cases
    candidate_count_before_review = review_stage_result.candidate_count_before_review
    review_selected_count = review_stage_result.review_selected_count
    reference_count_effective = review_stage_result.reference_count_effective
    stage_counts["review"] = int(review_selected_count or 0)

    phase_target_count = min(
        int(candidate_count_before_review or 0),
        max(_dict_case_count(parsed_result), int(final_target_floor_count or 0)),
    )
    parsed_result, functional_phase_recovery_summary = rebalance_functional_phase_coverage(
        _dict_case_items(parsed_result),
        candidate_cases=[
            *_dict_case_items(review_stage_result.review_candidate_cases),
            *_dict_case_items(review_stage_result.candidate_cases),
        ],
        project_profile=project_profile,
        target_count=phase_target_count,
    )
    stage_counts["functional_phase_recovered"] = int(
        functional_phase_recovery_summary.get("added_count") or 0
    )

    parsed_result = normalize_json_structure_fn(parsed_result)
    parsed_result = deduplicate_test_cases_fn(parsed_result)
    parsed_result, module_contract_summary = enforce_functional_module_contract(
        _dict_case_items(parsed_result),
        project_profile=project_profile,
    )
    stage_counts["module_contract_normalized"] = int(module_contract_summary.get("normalized_count") or 0)
    stage_counts["module_contract_rejected"] = int(module_contract_summary.get("rejected_count") or 0)
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
    judge_gate_result = _run_streaming_judge_gate(
        cases=_dict_case_items(parsed_result),
        requirement_semantics_context=requirement_semantics_context or {},
        feedback_control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
        fact_profile=fact_profile,
        start_id=start_id,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
        review_case_id_fn=_review_case_id,
        build_judge_summary_payload_fn=_build_judge_summary_payload,
        build_judge_decision_table_payload_fn=_build_judge_decision_table_payload,
    )
    parsed_result = judge_gate_result.cases
    judge_summary_payload = judge_gate_result.judge_summary_payload
    judge_decision_table_payload = judge_gate_result.judge_decision_table_payload
    final_pruning = _apply_post_judge_final_pruning(
        requirement=requirement,
        parsed_result=_dict_case_items(parsed_result),
        low_quality_drop_details=low_quality_filter_stats.low_quality_drop_details,
        append_final_cap_count=append_final_cap_count,
        start_id=start_id,
        analyze_coverage_fn=analyze_coverage,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
        rank_case_fn=_rank_review_case_for_fill,
    )
    parsed_result = final_pruning.cases
    pre_priority_coverage = final_pruning.pre_priority_coverage
    final_description_dedup_drop_signatures = final_pruning.final_description_dedup_drop_signatures
    append_cap_drop_signatures = final_pruning.append_cap_drop_signatures
    append_cap_drop_total = final_pruning.append_cap_drop_total
    final_quality_drop_total = final_pruning.final_quality_drop_total
    if final_quality_drop_total > 0:
        low_quality_filter_stats.add_postprocess_quality_drop(final_quality_drop_total)
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

    final_recovery = yield from _run_final_recovery_stage(
        client=client,
        db=db,
        requirement=requirement,
        kb_context=kb_context,
        parsed_result=_dict_case_items(parsed_result),
        flow_governance_summary=flow_governance_summary,
        final_target_floor_count=final_target_floor_count,
        review_candidate_cases=review_stage_result.review_candidate_cases,
        review_selection_input=review_stage_result.review_selection_input,
        candidate_cases=review_stage_result.candidate_cases,
        candidate_count_before_review=candidate_count_before_review,
        expected_count=expected_count,
        expected_count_value=expected_count_value,
        effective_generation_coverage_mode=effective_generation_coverage_mode,
        resolved_full_regression_floor=resolved_full_regression_floor,
        append=append,
        project_profile=project_profile,
        flow_project_profile=flow_project_profile,
        start_id=start_id,
        feedback_control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
        requirement_semantics_context=requirement_semantics_context or {},
        fact_profile=fact_profile,
        low_quality_drop_details=low_quality_filter_stats.low_quality_drop_details,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
        analyze_case_structure_fn=analyze_case_structure,
        analyze_coverage_fn=analyze_coverage,
        govern_cases_by_flow_structure_fn=govern_cases_by_flow_structure,
        record_timing_event_fn=_record_timing_event,
    )
    low_quality_filter_stats.accumulate(final_recovery.shortfall_filter_stats)
    low_quality_filter_stats.add_postprocess_quality_drop(final_recovery.final_quality_drop_total)
    parsed_result = final_recovery.cases
    flow_governance_summary = final_recovery.flow_governance_summary

    final_assembly = _assemble_final_cases(
        parsed_result=_dict_case_items(parsed_result),
        requirement=requirement,
        start_id=start_id,
        effective_generation_coverage_mode=effective_generation_coverage_mode,
        generation_coverage_mode=generation_coverage_mode,
        review_candidate_cases=review_stage_result.review_candidate_cases,
        review_selected_count=review_selected_count,
        workflow_blueprints=workflow_blueprints,
        trusted_workflow_contracts=trusted_workflow_contracts,
        current_requirement_workflow_blueprints=current_requirement_workflow_blueprints,
        authoritative_workflow_blueprints=authoritative_workflow_blueprints,
        flow_project_profile=flow_project_profile,
        project_profile=project_profile,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
        govern_cases_by_flow_structure_fn=govern_cases_by_flow_structure,
        analyze_case_structure_fn=analyze_case_structure,
    )
    parsed_result = final_assembly.cases
    execution_plan_summary = final_assembly.execution_plan_summary
    final_order_flow_governance_summary = final_assembly.final_order_flow_governance_summary
    final_case_structure = final_assembly.final_case_structure
    final_independent_case_structure = final_assembly.final_independent_case_structure
    final_count = final_assembly.final_count
    post_review_dedup_drop = final_assembly.post_review_dedup_drop

    review_summary_state = _assemble_review_summary_state(
        requirement=requirement,
        review_candidate_cases=review_stage_result.review_candidate_cases,
        review_selection_input=review_stage_result.review_selection_input,
        review_gate_trace=review_stage_result.review_gate_trace,
        parsed_result=parsed_result,
        project_profile=project_profile,
        flow_project_profile=flow_project_profile,
        flow_governance_summary=flow_governance_summary,
        final_shortfall_supplement_applied=bool(final_recovery.final_shortfall_supplement_applied),
        effective_generation_coverage_mode=effective_generation_coverage_mode,
        final_case_structure=final_case_structure,
        final_independent_case_structure=final_independent_case_structure,
        final_order_flow_governance_summary=final_order_flow_governance_summary,
        fact_profile=fact_profile,
        execution_plan_summary=execution_plan_summary,
        ui_like_ratio_postprocess_drop_count=ui_like_ratio_postprocess_drop_count,
        final_description_dedup_drop_signatures=final_description_dedup_drop_signatures,
        review_llm_applied=review_stage_result.review_llm_applied,
        review_llm_selected_signatures=review_stage_result.review_llm_selected_signatures,
        review_llm_omitted_signatures=review_stage_result.review_llm_omitted_signatures,
        review_llm_runtime_debug=review_stage_result.review_llm_runtime_debug,
        review_llm_drop_reason_raw_map=review_stage_result.review_llm_drop_reason_raw_map,
        review_llm_drop_reason_map=review_stage_result.review_llm_drop_reason_map,
        review_llm_drop_reason_source_map=review_stage_result.review_llm_drop_reason_source_map,
        review_llm_drop_reason_evidence_map=review_stage_result.review_llm_drop_reason_evidence_map,
        review_must_keep_signatures=review_stage_result.review_must_keep_signatures,
        review_constraint_retained_signatures=review_stage_result.review_constraint_retained_signatures,
        review_constraint_reason_map=review_stage_result.review_constraint_reason_map,
        append_cap_drop_signatures=append_cap_drop_signatures,
        must_cover_rule_set=must_cover_rule_set,
        review_must_keep_reason_map=review_stage_result.review_must_keep_reason_map,
        review_selected_count=review_selected_count,
        review_target_min_count=review_stage_result.review_target_min_count,
        review_target_max_count=review_stage_result.review_target_max_count,
        review_shortfall_detected=review_stage_result.review_shortfall_detected,
        review_shortfall_before_count=review_stage_result.review_shortfall_before_count,
        review_shortfall_recovered_count=review_stage_result.review_shortfall_recovered_count,
        review_post_rerank_floor_count=review_stage_result.review_post_rerank_floor_count,
        review_post_rerank_recovered_count=review_stage_result.review_post_rerank_recovered_count,
        final_target_floor_count=final_recovery.final_target_floor_count,
        final_floor_recovery_attempted=final_recovery.final_floor_recovery_attempted,
        final_floor_recovery_applied=final_recovery.final_floor_recovery_applied,
        final_floor_recovered_count=final_recovery.final_floor_recovered_count,
        final_floor_recovery_reason=final_recovery.final_floor_recovery_reason,
        final_confirmed_conflict_drop_count=final_recovery.final_confirmed_conflict_drop_count,
        final_shortfall_supplement_attempted=final_recovery.final_shortfall_supplement_attempted,
        final_shortfall_supplement_count=final_recovery.final_shortfall_supplement_count,
        final_shortfall_supplement_reason=final_recovery.final_shortfall_supplement_reason,
        final_shortfall_supplement_debug=final_recovery.final_shortfall_supplement_debug,
        generation_mode=generation_mode,
        effective_generation_coverage_mode_source=effective_generation_coverage_mode_source,
        explicit_generation_mode_override=explicit_generation_mode_override,
        explicit_expected_count_floor_preserved=explicit_expected_count_floor_preserved,
        review_fill_source=review_stage_result.review_fill_source,
        review_llm_pool_count=review_stage_result.review_llm_pool_count,
        stage_counts=stage_counts,
        analyze_coverage_fn=analyze_coverage,
        analyze_case_structure_fn=analyze_case_structure,
        summarize_duplicate_excess_by_policy_fn=summarize_duplicate_excess_by_policy,
        score_case_priority_fn=score_case_priority,
        hit_must_cover_rule_fn=_hit_must_cover_rule,
        is_high_signal_fn=_is_high_signal,
        violates_forbidden_pattern_fn=_violates_forbidden_pattern,
        hits_soft_constraint_fn=_hits_soft_constraint,
        satisfies_quality_hint_fn=_satisfies_quality_hint,
        reasoning_leakage_hits_fn=_reasoning_leakage_hits,
        dict_case_count_fn=_dict_case_count,
        flow_profile_with_scenario_policy_fn=_flow_profile_with_scenario_policy,
    )
    review_decision_table = review_summary_state.review_decision_table
    final_description_dedup_drop_signatures = review_summary_state.final_description_dedup_drop_signatures
    drop_by_review_llm_count = review_summary_state.drop_by_review_llm_count
    drop_by_review_selector_count = review_summary_state.drop_by_review_selector_count
    review_decision_summary = review_summary_state.review_decision_summary

    final_coverage = analyze_coverage(requirement, _dict_case_items(parsed_result))
    final_generation_report = _build_final_generation_report(
        FinalGenerationReportInputs(
            parsed_result=_dict_case_items(parsed_result),
            pre_priority_coverage=pre_priority_coverage,
            final_coverage=final_coverage,
            reference_count_effective=reference_count_effective,
            final_count=final_count,
            gap_remaining_after_attempts=gap_remaining_after_attempts,
            gap_attempts=gap_attempts,
            gap_stopped_by_provider_error=gap_stopped_by_provider_error,
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
            review_decision_summary=review_decision_summary,
            generation_target_case_range=generation_target_case_range,
            expected_count=expected_count,
            generation_coverage_mode=generation_coverage_mode,
            resolved_full_regression_floor=resolved_full_regression_floor,
            candidate_count_before_review=candidate_count_before_review,
            judge_summary_payload=judge_summary_payload,
            drop_by_review_llm_count=drop_by_review_llm_count,
            stage_counts=stage_counts,
            append_target_count=append_target_count,
            append_final_cap_count=append_final_cap_count,
            generation_mode=generation_mode,
            effective_generation_coverage_mode_source=effective_generation_coverage_mode_source,
            explicit_generation_mode_override=explicit_generation_mode_override,
            explicit_expected_count_floor_preserved=explicit_expected_count_floor_preserved,
        )
    )
    coverage = final_generation_report.coverage
    generation_summary = final_generation_report.generation_summary
    convergence_debug = final_generation_report.convergence_debug

    _record_timing_event(
        "postprocess_total",
        postprocess_started,
        final_count=int(_dict_case_count(parsed_result)),
        candidate_count_before_review=int(candidate_count_before_review or 0),
        review_selected_count=int(review_selected_count or 0),
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
        timing_events=timing_events,
    )
