from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .streaming_case_quality import filter_final_quality_cases
from .streaming_case_source_metadata import (
    annotate_case_source_metadata,
    apply_case_source_metadata,
)
from .streaming_flow_conflicts import filter_cases_conflicting_with_confirmed_flow_facts
from .streaming_final_floor_recovery import (
    recover_final_floor_after_conflict_filter,
    recover_final_floor_from_candidate_pool,
)
from .streaming_postprocess_utils import _clip_text, _dict_case_count, _dict_case_items
from .streaming_review_selection import rank_review_case_for_fill
from .streaming_shortfall_supplement import (
    resolve_final_shortfall_supplement_size,
    run_final_shortfall_supplement,
    should_attempt_final_shortfall_supplement,
)


@dataclass(frozen=True)
class FinalRecoveryStageResult:
    cases: list[dict[str, Any]]
    flow_governance_summary: dict[str, Any]
    final_target_floor_count: int
    final_floor_recovery_attempted: bool
    final_floor_recovery_applied: bool
    final_floor_recovered_count: int
    final_floor_recovery_reason: str
    final_confirmed_conflict_drop_count: int
    final_shortfall_supplement_attempted: bool
    final_shortfall_supplement_applied: bool
    final_shortfall_supplement_count: int
    final_shortfall_supplement_reason: str
    final_shortfall_supplement_debug: dict[str, Any]
    shortfall_filter_stats: dict[str, Any]
    final_quality_drop_total: int


def run_final_recovery_stage(
    *,
    client: Any,
    db: Any,
    requirement: str,
    kb_context: str,
    parsed_result: list[dict[str, Any]],
    flow_governance_summary: dict[str, Any],
    final_target_floor_count: int,
    review_candidate_cases: list[dict[str, Any]],
    review_selection_input: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
    candidate_count_before_review: int,
    expected_count: int,
    expected_count_value: int,
    effective_generation_coverage_mode: str,
    resolved_full_regression_floor: int,
    append: bool,
    project_profile: dict[str, Any],
    flow_project_profile: dict[str, Any],
    start_id: int,
    feedback_control_state: dict[str, Any] | None,
    requirement_semantics_context: dict[str, Any] | None,
    fact_profile: dict[str, Any],
    low_quality_drop_details: list[dict[str, Any]],
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    analyze_case_structure_fn: Callable[..., dict[str, Any]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    govern_cases_by_flow_structure_fn: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
    record_timing_event_fn: Callable[..., dict[str, Any]],
) -> Iterator[str]:
    result_cases = _dict_case_items(parsed_result)
    review_candidate_cases = annotate_case_source_metadata(
        review_candidate_cases,
        source_stage="review_candidate",
        set_candidate_index=True,
    )
    candidate_cases = annotate_case_source_metadata(
        apply_case_source_metadata(
            candidate_cases,
            source_cases=review_candidate_cases,
        ),
        source_stage="review_candidate",
        set_candidate_index=True,
    )
    review_selection_input = apply_case_source_metadata(
        review_selection_input,
        source_cases=[*review_candidate_cases, *candidate_cases],
    )
    result_cases = apply_case_source_metadata(
        result_cases,
        source_cases=[*review_candidate_cases, *review_selection_input, *candidate_cases],
    )
    result_flow_summary = dict(flow_governance_summary or {})
    final_confirmed_conflict_drop_count = 0
    final_shortfall_supplement_attempted = False
    final_shortfall_supplement_applied = False
    final_shortfall_supplement_count = 0
    final_shortfall_supplement_reason = ""
    final_shortfall_supplement_debug: dict[str, Any] = {}
    shortfall_filter_stats: dict[str, Any] = {}

    final_floor_recovery = recover_final_floor_from_candidate_pool(
        requirement=requirement,
        parsed_result=result_cases,
        flow_governance_summary=result_flow_summary,
        initial_final_target_floor_count=final_target_floor_count,
        review_candidate_cases=review_candidate_cases,
        review_selection_input=review_selection_input,
        candidate_cases=candidate_cases,
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
        analyze_case_structure_fn=analyze_case_structure_fn,
        analyze_coverage_fn=analyze_coverage_fn,
        govern_cases_by_flow_structure_fn=govern_cases_by_flow_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        rank_case_fn=rank_review_case_for_fill,
    )
    result_cases = final_floor_recovery.cases
    result_flow_summary = final_floor_recovery.flow_governance_summary
    final_target_floor_count = final_floor_recovery.final_target_floor_count
    final_floor_recovery_attempted = final_floor_recovery.attempted
    final_floor_recovery_applied = final_floor_recovery.applied
    final_floor_recovered_count = final_floor_recovery.recovered_count
    final_floor_recovery_reason = final_floor_recovery.reason

    if should_attempt_final_shortfall_supplement(
        effective_generation_coverage_mode=effective_generation_coverage_mode,
        expected_count_value=expected_count_value,
        final_target_floor_count=final_target_floor_count,
        append=append,
        current_count=_dict_case_count(result_cases),
    ):
        current_shortfall_count = _dict_case_count(result_cases)
        supplement_size = resolve_final_shortfall_supplement_size(
            current_count=current_shortfall_count,
            target_floor_count=final_target_floor_count,
        )
        final_shortfall_supplement_attempted = True
        final_shortfall_started = time.perf_counter()
        try:
            yield "@@STATUS@@:Final shortfall supplement started...\n"
            shortfall_result = run_final_shortfall_supplement(
                client=client,
                db=db,
                requirement=requirement,
                supplement_prompt="",
                current_shortfall_count=current_shortfall_count,
                target_floor_count=final_target_floor_count,
                supplement_needed=supplement_size["needed"],
                parsed_result=_dict_case_items(result_cases),
                kb_context=kb_context,
                fact_profile=fact_profile,
                flow_project_profile=flow_project_profile,
                effective_generation_coverage_mode=effective_generation_coverage_mode,
                start_id=start_id,
                final_floor_recovered_count=final_floor_recovered_count,
                clean_and_parse_json_fn=clean_and_parse_json_fn,
                normalize_json_structure_fn=normalize_json_structure_fn,
                deduplicate_test_cases_fn=deduplicate_test_cases_fn,
                analyze_coverage_fn=analyze_coverage_fn,
                govern_cases_by_flow_structure_fn=govern_cases_by_flow_structure_fn,
            )
            final_shortfall_supplement_debug = dict(shortfall_result.debug or {})
            shortfall_filter_stats = dict(shortfall_result.filter_stats or {})
            final_confirmed_conflict_drop_count += int(shortfall_result.conflict_drop_count or 0)
            if shortfall_result.applied:
                result_cases = shortfall_result.cases
                result_flow_summary = shortfall_result.flow_governance_summary
                final_shortfall_supplement_applied = True
                final_shortfall_supplement_count = shortfall_result.supplement_count
                final_floor_recovered_count = shortfall_result.floor_recovered_count
                final_floor_recovery_applied = shortfall_result.floor_recovery_applied
                final_floor_recovery_reason = shortfall_result.floor_recovery_reason
                yield f"@@STATUS@@:Final shortfall supplement added {final_shortfall_supplement_count} cases.\n"
            else:
                final_shortfall_supplement_reason = shortfall_result.reason
        except Exception as supplement_err:
            final_shortfall_supplement_reason = f"exception:{_clip_text(supplement_err, 120)}"
        record_timing_event_fn(
            "final_shortfall_supplement",
            final_shortfall_started,
            attempted=bool(final_shortfall_supplement_attempted),
            applied=bool(final_shortfall_supplement_applied),
            added_count=int(final_shortfall_supplement_count or 0),
            reason=str(final_shortfall_supplement_reason or ""),
            debug=final_shortfall_supplement_debug,
        )

    result_cases, final_filter_conflict_drop_count = filter_cases_conflicting_with_confirmed_flow_facts(
        _dict_case_items(result_cases),
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
        and _dict_case_count(result_cases) < int(final_target_floor_count or 0)
    ):
        post_conflict_recovery = recover_final_floor_after_conflict_filter(
            requirement=requirement,
            kb_context=kb_context,
            parsed_result=_dict_case_items(result_cases),
            flow_governance_summary=result_flow_summary,
            final_target_floor_count=final_target_floor_count,
            final_floor_recovered_count=final_floor_recovered_count,
            effective_generation_coverage_mode=effective_generation_coverage_mode,
            review_candidate_cases=review_candidate_cases,
            review_selection_input=review_selection_input,
            candidate_cases=candidate_cases,
            fact_profile=fact_profile,
            flow_project_profile=flow_project_profile,
            start_id=start_id,
            feedback_control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
            requirement_semantics_context=requirement_semantics_context or {},
            analyze_coverage_fn=analyze_coverage_fn,
            filter_conflicting_cases_fn=filter_cases_conflicting_with_confirmed_flow_facts,
            govern_cases_by_flow_structure_fn=govern_cases_by_flow_structure_fn,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
            rank_case_fn=rank_review_case_for_fill,
        )
        result_cases = post_conflict_recovery.cases
        result_flow_summary = post_conflict_recovery.flow_governance_summary
        if post_conflict_recovery.applied:
            final_floor_recovery_applied = True
            final_floor_recovery_reason = post_conflict_recovery.reason
            final_floor_recovered_count = post_conflict_recovery.recovered_count

    result_cases, final_post_recovery_conflict_drop_count = filter_cases_conflicting_with_confirmed_flow_facts(
        _dict_case_items(result_cases),
        requirement=str(requirement or ""),
        kb_context=str(kb_context or ""),
        fact_profile=fact_profile,
    )
    final_confirmed_conflict_drop_count += int(final_post_recovery_conflict_drop_count or 0)
    final_invalid_quality_filtered_result, final_invalid_quality_drop_total = filter_final_quality_cases(
        result_cases,
        low_quality_drop_details,
        stage="post_recovery_quality_filter",
    )
    if final_invalid_quality_drop_total > 0:
        result_cases = final_invalid_quality_filtered_result

    return FinalRecoveryStageResult(
        cases=_dict_case_items(result_cases),
        flow_governance_summary=dict(result_flow_summary or {}),
        final_target_floor_count=int(final_target_floor_count or 0),
        final_floor_recovery_attempted=bool(final_floor_recovery_attempted),
        final_floor_recovery_applied=bool(final_floor_recovery_applied),
        final_floor_recovered_count=int(final_floor_recovered_count or 0),
        final_floor_recovery_reason=str(final_floor_recovery_reason or ""),
        final_confirmed_conflict_drop_count=int(final_confirmed_conflict_drop_count or 0),
        final_shortfall_supplement_attempted=bool(final_shortfall_supplement_attempted),
        final_shortfall_supplement_applied=bool(final_shortfall_supplement_applied),
        final_shortfall_supplement_count=int(final_shortfall_supplement_count or 0),
        final_shortfall_supplement_reason=str(final_shortfall_supplement_reason or ""),
        final_shortfall_supplement_debug=dict(final_shortfall_supplement_debug or {}),
        shortfall_filter_stats=shortfall_filter_stats,
        final_quality_drop_total=int(final_invalid_quality_drop_total or 0),
    )


__all__ = [
    "FinalRecoveryStageResult",
    "run_final_recovery_stage",
]
