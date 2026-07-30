from __future__ import annotations

import json
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
    review_case_id as _review_case_id,
)
from .streaming_case_source_metadata import apply_case_source_metadata as _apply_case_source_metadata
from .streaming_final_case_assembly import assemble_final_cases as _assemble_final_cases
from .streaming_final_pruning import apply_post_judge_final_pruning as _apply_post_judge_final_pruning
from .streaming_execution_plan_metadata import (
    evaluate_required_stage_candidate_coverage as _evaluate_required_stage_candidate_coverage,
)
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
from .case_fact_relations import (
    build_case_semantic_identity,
    compare_case_semantic_identity,
    deduplicate_cases_by_semantic_identity,
    normalize_case_semantic_text,
)
from .module_contract import enforce_functional_module_contract
from ..control.semantic_contract import resolve_case_semantic_gate


def deduplicate_streaming_candidates(
    cases: list[dict[str, Any]],
    *,
    structural_deduplicate_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """统一执行结构判重和语义判重，保证后续数量缺口基于真实唯一候选计算。"""

    structurally_unique = _dict_case_items(
        structural_deduplicate_fn(_dict_case_items(cases))
    )
    candidate_groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for case in structurally_unique:
        identity = build_case_semantic_identity(case)
        has_contract_anchor = bool(
            identity.fact_ids
            or identity.interaction_ids
            or identity.workflow_stages
            or identity.precondition_states
            or identity.produced_states
        )
        if has_contract_anchor:
            group_key = ("contract_anchored",)
        elif identity.module_keys:
            group_key = ("module_keys", *sorted(identity.module_keys))
        else:
            module_name = normalize_case_semantic_text(case.get("test_module"))
            group_key = ("display_module", module_name or "unscoped")
        candidate_groups.setdefault(group_key, []).append(case)

    kept_cases: list[dict[str, Any]] = []
    for group_cases in candidate_groups.values():
        semantic_result = deduplicate_cases_by_semantic_identity(group_cases)
        kept_cases.extend(_dict_case_items(semantic_result.cases))

    # 分组只限定缺口计算阶段的保守比较范围，最终顺序仍沿用原候选顺序。
    positions_by_id = {
        str(case.get("id") or "").strip(): index
        for index, case in enumerate(structurally_unique)
        if str(case.get("id") or "").strip()
    }

    def _source_position(case: dict[str, Any]) -> int:
        case_id = str(case.get("id") or "").strip()
        if case_id in positions_by_id:
            return positions_by_id[case_id]
        for index, source_case in enumerate(structurally_unique):
            if source_case == case:
                return index
        return len(structurally_unique)

    return sorted(kept_cases, key=_source_position)


def _coverage_dimension_keys(coverage: dict[str, Any]) -> set[tuple[str, str]]:
    """提取已覆盖的规则与类型维度，供集合级闭环比较使用。"""
    dimensions: set[tuple[str, str]] = set()
    for item in coverage.get("rule_diagnostics") or []:
        if not isinstance(item, dict) or item.get("covered") is not True:
            continue
        rule_id = str(item.get("rule_id") or "").strip()
        if not rule_id:
            continue
        for case_type in item.get("coverage_types") or []:
            normalized_type = str(case_type or "").strip().lower()
            if normalized_type:
                dimensions.add((rule_id, normalized_type))
    return dimensions


def _merge_case_semantic_annotations(
    coverage_witness: dict[str, Any],
    contract_candidate: dict[str, Any],
) -> dict[str, Any]:
    """保留覆盖见证的公开行为，同时合并重复候选中的结构化契约锚点。"""
    merged = dict(coverage_witness)
    witness_semantic = dict(coverage_witness.get("_semantic") or {})
    contract_semantic = dict(contract_candidate.get("_semantic") or {})
    for key, value in contract_semantic.items():
        if isinstance(value, list):
            combined = list(witness_semantic.get(key) or [])
            seen = {
                json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                for item in combined
            }
            for item in value:
                marker = json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                if marker not in seen:
                    combined.append(item)
                    seen.add(marker)
            witness_semantic[key] = combined
        elif key not in witness_semantic or witness_semantic.get(key) in (None, ""):
            witness_semantic[key] = value
    if witness_semantic:
        merged["_semantic"] = witness_semantic
    retained_id = str(contract_candidate.get("id") or "").strip()
    if retained_id:
        merged["id"] = retained_id
    if contract_candidate.get("hit_must_cover_rule") is True:
        merged["hit_must_cover_rule"] = True
    return merged


def preserve_coverage_witnesses_after_semantic_dedup(
    *,
    requirement: str,
    source_cases: list[dict[str, Any]],
    deduplicated_cases: list[dict[str, Any]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """语义判重不得破坏原集合已经满足的规则/类型覆盖维度。"""
    source = _dict_case_items(source_cases)
    retained = [dict(item) for item in _dict_case_items(deduplicated_cases)]
    if not source or not retained or len(retained) >= len(source):
        return retained

    source_dimensions = _coverage_dimension_keys(
        analyze_coverage_fn(requirement, source)
    )
    retained_dimensions = _coverage_dimension_keys(
        analyze_coverage_fn(requirement, retained)
    )
    missing_dimensions = source_dimensions - retained_dimensions
    if not missing_dimensions:
        return retained

    dropped_candidates = [
        dict(item)
        for item in source
        if not any(item == kept for kept in retained)
    ]
    for witness in dropped_candidates:
        witness_dimensions = _coverage_dimension_keys(
            analyze_coverage_fn(requirement, [witness])
        )
        if not witness_dimensions.intersection(missing_dimensions):
            continue
        for index, contract_candidate in enumerate(retained):
            relation = compare_case_semantic_identity(
                witness,
                contract_candidate,
            )
            if relation.relation not in {"duplicate", "contains", "contained_by"}:
                continue
            merged_candidate = _merge_case_semantic_annotations(
                witness,
                contract_candidate,
            )
            trial = [*retained]
            trial[index] = merged_candidate
            trial_dimensions = _coverage_dimension_keys(
                analyze_coverage_fn(requirement, trial)
            )
            if len(source_dimensions - trial_dimensions) >= len(missing_dimensions):
                continue
            retained = trial
            missing_dimensions = source_dimensions - trial_dimensions
            break
        if not missing_dimensions:
            break
    return retained


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
    initial_case_semantic_rejections: list[dict[str, Any]] | None = None,
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
    workflow_absence_declared = control_context.workflow_absence_declared
    require_case_semantic_contract, requirement_semantic_contract = resolve_case_semantic_gate(
        feedback_control_state
    )
    case_semantic_rejections = [
        dict(item)
        for item in (initial_case_semantic_rejections or [])
        if isinstance(item, dict)
    ]

    def _deduplicate_generation_candidates(
        cases: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        structurally_unique = _dict_case_items(
            deduplicate_test_cases_fn(_dict_case_items(cases))
        )
        deduplicated = deduplicate_streaming_candidates(
            cases,
            structural_deduplicate_fn=deduplicate_test_cases_fn,
        )
        return preserve_coverage_witnesses_after_semantic_dedup(
            requirement=requirement,
            source_cases=structurally_unique,
            deduplicated_cases=deduplicated,
            analyze_coverage_fn=analyze_coverage,
        )

    def _generated_case_normalizer(source_stage: str) -> Callable[[Any], Any]:
        if not require_case_semantic_contract:
            return normalize_json_structure_fn

        def _normalize(data: Any) -> Any:
            return normalize_json_structure_fn(
                data,
                require_case_semantic_contract=True,
                requirement_semantic_contract=requirement_semantic_contract,
                semantic_rejections=case_semantic_rejections,
                semantic_source_stage=source_stage,
            )

        return _normalize

    primary_case_normalizer = _generated_case_normalizer("stream_primary_postprocess")
    rescue_case_normalizer = _generated_case_normalizer("stream_primary_rescue")
    gap_case_normalizer = _generated_case_normalizer("stream_gap_supplement")

    case_governance_matcher = CaseGovernanceMatcher.from_raw(
        forbidden_patterns=forbidden_patterns,
        reuse_risks=reuse_risks,
        soft_constraints=soft_constraints,
        quality_fix_hints=quality_fix_hints,
    )
    _violates_forbidden_pattern = case_governance_matcher.violates_forbidden_pattern
    _hits_soft_constraint = case_governance_matcher.hits_soft_constraint
    _satisfies_quality_hint = case_governance_matcher.satisfies_quality_hint


    initial_parse_stage = _run_initial_parse_stage(
        full_content=full_content,
        requirement=requirement,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=primary_case_normalizer,
        deduplicate_test_cases_fn=_deduplicate_generation_candidates,
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
    effective_generation_coverage_mode = initial_state.effective_generation_coverage_mode
    effective_generation_coverage_mode_source = initial_state.effective_generation_coverage_mode_source
    explicit_generation_mode_override = initial_state.explicit_generation_mode_override
    generation_coverage_mode = initial_state.generation_coverage_mode
    explicit_expected_count_floor_preserved = initial_state.explicit_expected_count_floor_preserved
    resolved_full_regression_floor = initial_state.resolved_full_regression_floor


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
                normalize_json_structure_fn=rescue_case_normalizer,
                deduplicate_test_cases_fn=_deduplicate_generation_candidates,
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

    # 模块和交互是执行计划候选资格的一部分，必须先于阶段闭环评估。
    parsed_result = normalize_json_structure_fn(parsed_result)
    parsed_result = _deduplicate_generation_candidates(parsed_result)
    parsed_result, module_contract_summary = enforce_functional_module_contract(
        _dict_case_items(parsed_result),
        project_profile=project_profile,
    )
    stage_counts["module_contract_normalized"] = int(module_contract_summary.get("normalized_count") or 0)
    stage_counts["module_contract_rejected"] = int(module_contract_summary.get("rejected_count") or 0)

    coverage_primary = analyze_coverage(requirement, _dict_case_items(parsed_result))
    coverage_gap_state = _resolve_coverage_gap_state(coverage_primary)
    generic_gap_enabled = normalized_mode in {"multi_pass", "biz_key_multi_pass"}
    generic_gap_needed = bool(
        generic_gap_enabled
        and (
            coverage_gap_state.get("missing_rules")
            or coverage_gap_state.get("has_missing_types")
        )
    )
    required_stage_coverage = _evaluate_required_stage_candidate_coverage(
        _dict_case_items(parsed_result),
        workflow_blueprints=workflow_blueprints,
    )
    stage_repair_needed = bool(
        required_stage_coverage.get("source_generation_allowed") is True
        and required_stage_coverage.get("required_stage_coverage_complete") is not True
        and required_stage_coverage.get("missing_required_stages")
    )
    minimum_candidate_count = (
        max(0, int(expected_count or 0))
        if not append
        else 0
    )
    candidate_count_floor_needed = bool(
        minimum_candidate_count > _dict_case_count(parsed_result)
    )
    stage_counts["required_stage_source_generation_allowed"] = int(
        required_stage_coverage.get("source_generation_allowed") is True
    )
    stage_counts["required_stage_gap_before"] = int(
        len(required_stage_coverage.get("actionable_stage_ids") or [])
    )
    stage_counts["required_stage_blueprint_invalid"] = int(
        bool(workflow_blueprints)
        and required_stage_coverage.get("source_generation_allowed") is not True
    )

    if generic_gap_needed or stage_repair_needed or candidate_count_floor_needed:
        gap_result = yield from _run_gap_supplement_attempts(
            client=client,
            requirement=requirement,
            append=append,
            existing_cases=existing_cases,
            parsed_result=_dict_case_items(parsed_result),
            coverage_primary=coverage_primary,
            coverage_gap_state=coverage_gap_state,
            review_contract_context={
                "workflow_blueprints": workflow_blueprints,
                "workflow_absence_declared": workflow_absence_declared,
                "functional_architecture": dict(
                    (project_profile or {}).get("functional_architecture") or {}
                ),
            },
            current_biz_key=current_biz_key,
            infer_case_kind_fn=infer_case_kind_fn,
            build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction_fn,
            build_gap_fill_prompt_fn=build_gap_fill_prompt,
            clean_and_parse_json_fn=clean_and_parse_json_fn,
            normalize_json_structure_fn=gap_case_normalizer,
            deduplicate_test_cases_fn=_deduplicate_generation_candidates,
            analyze_coverage_fn=analyze_coverage,
            resolve_coverage_gap_state_fn=_resolve_coverage_gap_state,
            record_timing_event_fn=_record_timing_event,
            workflow_blueprints=workflow_blueprints,
            project_profile=project_profile,
            include_generic_gaps=generic_gap_enabled,
            minimum_candidate_count=minimum_candidate_count,
        )
        parsed_result = gap_result.cases
        coverage_primary = gap_result.coverage_primary
        coverage_gap_state = gap_result.coverage_gap_state
        gap_attempts = gap_result.attempt_count
        gap_remaining_after_attempts = gap_result.remaining_gap_count
        gap_stopped_by_provider_error = gap_result.stopped_by_provider_error
        stage_counts["gap"] = gap_result.added_count
        stage_counts["required_stage_gap_remaining"] = int(
            len(
                gap_result.required_stage_coverage.get("actionable_stage_ids")
                or []
            )
        )
        stage_counts["module_contract_normalized"] += int(
            gap_result.module_contract_normalized_count
        )
        stage_counts["module_contract_rejected"] += int(
            gap_result.module_contract_rejected_count
        )
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
        review_contract_context={
            "workflow_blueprints": workflow_blueprints,
            "workflow_absence_declared": workflow_absence_declared,
            "functional_architecture": dict(
                (project_profile or {}).get("functional_architecture") or {}
            ),
        },
        append_target_count=append_target_count,
        append_final_cap_count=append_final_cap_count,
        current_biz_key=current_biz_key,
        build_review_select_prompt_fn=build_review_select_prompt,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=_deduplicate_generation_candidates,
        analyze_coverage_fn=analyze_coverage,
        score_case_priority_fn=score_case_priority,
        record_timing_event_fn=_record_timing_event,
    )
    low_quality_filter_stats.accumulate(review_stage_result.review_filter_stats)
    parsed_result = review_stage_result.cases
    candidate_count_before_review = review_stage_result.candidate_count_before_review
    review_selected_count = review_stage_result.review_selected_count
    reference_count_effective = review_stage_result.reference_count_effective
    stage_counts["review"] = int(review_selected_count or 0)

    # 全局评审完成后不再按模块配额从候选池局部补回。
    stage_counts["functional_phase_recovered"] = 0

    parsed_result = normalize_json_structure_fn(parsed_result)
    parsed_result = _deduplicate_generation_candidates(parsed_result)
    parsed_result = reorder_cases_by_closed_loop_fn(parsed_result, start_id=start_id, renumber_ids=True)
    parsed_result = _apply_coverage_priority_semantics(
        requirement,
        parsed_result,
        analyze_coverage_fn=analyze_coverage,
    )
    ui_like_ratio_postprocess_drop_count = 0
    judge_gate_result = _run_streaming_judge_gate(
        cases=_dict_case_items(parsed_result),
        requirement_semantics_context=requirement_semantics_context or {},
        feedback_control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
        fact_profile=fact_profile,
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
        workflow_blueprints=workflow_blueprints,
    )
    parsed_result = final_pruning.cases
    pre_priority_coverage = final_pruning.pre_priority_coverage
    final_description_dedup_drop_signatures = final_pruning.final_description_dedup_drop_signatures
    append_cap_drop_signatures = final_pruning.append_cap_drop_signatures
    append_cap_drop_total = final_pruning.append_cap_drop_total
    stage_counts["append_cap_protected_count"] = int(
        final_pruning.append_cap_diagnostics.get("protected_count") or 0
    )
    stage_counts["append_cap_closure_overflow_count"] = int(
        final_pruning.append_cap_diagnostics.get("target_overflow_count") or 0
    )
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
    flow_project_profile = _flow_profile_with_scenario_policy(
        flow_project_profile,
        disable_scenario_pruning=True,
        global_review_selection=True,
    )
    try:
        parsed_result, flow_governance_summary = govern_cases_by_flow_structure(
            requirement,
            _dict_case_items(parsed_result),
            start_id=start_id,
            renumber_ids=True,
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

    parsed_result = _apply_case_source_metadata(
        _dict_case_items(parsed_result),
        source_cases=[
            *_dict_case_items(review_stage_result.review_candidate_cases),
            *_dict_case_items(review_stage_result.review_selection_input),
            *_dict_case_items(review_stage_result.candidate_cases),
        ],
    )
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
        workflow_absence_declared=workflow_absence_declared,
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
    final_semantic_diagnostics = {
        "final_semantic_diagnostics_available": True,
        "final_semantic_duplicate_cluster_count": int(final_assembly.semantic_duplicate_count),
        "final_semantic_duplicate_case_count": int(final_assembly.semantic_unresolved_duplicate_count),
        "final_semantic_dedup_dropped_count": int(final_assembly.semantic_dedup_dropped_count),
        "final_semantic_containment_count": int(final_assembly.semantic_containment_count),
        "final_semantic_relation_samples": list(final_assembly.semantic_relation_samples),
        "final_semantic_dropped_case_ids": list(final_assembly.semantic_dropped_case_ids),
    }

    review_summary_state = _assemble_review_summary_state(
        requirement=requirement,
        review_candidate_cases=review_stage_result.review_candidate_cases,
        review_selection_input=review_stage_result.review_selection_input,
        review_gate_trace=review_stage_result.review_gate_trace,
        parsed_result=parsed_result,
        project_profile=project_profile,
        flow_project_profile=flow_project_profile,
        flow_governance_summary=flow_governance_summary,
        final_case_structure=final_case_structure,
        final_independent_case_structure=final_independent_case_structure,
        final_order_flow_governance_summary=final_order_flow_governance_summary,
        fact_profile=fact_profile,
        execution_plan_summary=execution_plan_summary,
        final_semantic_diagnostics=final_semantic_diagnostics,
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
        review_constraint_retained_signatures=review_stage_result.review_constraint_retained_signatures,
        review_constraint_reason_map=review_stage_result.review_constraint_reason_map,
        append_cap_drop_signatures=append_cap_drop_signatures,
        must_cover_rule_set=must_cover_rule_set,
        review_selected_count=review_selected_count,
        review_target_min_count=review_stage_result.review_target_min_count,
        review_target_max_count=review_stage_result.review_target_max_count,
        review_shortfall_detected=review_stage_result.review_shortfall_detected,
        review_shortfall_before_count=review_stage_result.review_shortfall_before_count,
        generation_mode=generation_mode,
        effective_generation_coverage_mode_source=effective_generation_coverage_mode_source,
        explicit_generation_mode_override=explicit_generation_mode_override,
        explicit_expected_count_floor_preserved=explicit_expected_count_floor_preserved,
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
    convergence_debug.update(final_semantic_diagnostics)
    generation_summary.update(final_semantic_diagnostics)
    stage_counts["case_semantic_rejected"] = int(len(case_semantic_rejections))
    semantic_contract_diagnostics = {
        "enabled": bool(require_case_semantic_contract),
        "rejected_count": int(len(case_semantic_rejections)),
        "rejections": list(case_semantic_rejections)[:20],
    }
    convergence_debug["case_semantic_contract"] = semantic_contract_diagnostics
    generation_summary["case_semantic_contract"] = semantic_contract_diagnostics

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
