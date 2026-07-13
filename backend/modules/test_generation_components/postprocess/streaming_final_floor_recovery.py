from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .case_access import case_text_field
from .streaming_case_keys import case_signature, review_case_id
from .streaming_expected_result_quality import (
    is_non_assertable_expected_result,
    looks_truncated_text,
)
from .streaming_postprocess_utils import (
    _dict_case_count,
    _dict_case_items,
    _flow_profile_with_scenario_policy,
    _resolve_expected_min_floor_for_recovery,
    _rule_diagnostics_payload,
)
from .streaming_priority_semantics import apply_coverage_priority_semantics
from .streaming_reasoning_quality import reasoning_leakage_hits


@dataclass(frozen=True)
class FinalFloorRecoveryResult:
    cases: list[dict[str, Any]]
    flow_governance_summary: dict[str, Any]
    final_target_floor_count: int
    attempted: bool
    applied: bool
    recovered_count: int
    reason: str


@dataclass(frozen=True)
class PostConflictFloorRecoveryResult:
    cases: list[dict[str, Any]]
    flow_governance_summary: dict[str, Any]
    applied: bool
    recovered_count: int
    reason: str


def _recovery_pool_seed(
    *,
    review_candidate_cases: list[dict[str, Any]],
    review_selection_input: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        item
        for item in [*review_candidate_cases, *review_selection_input, *candidate_cases]
        if isinstance(item, dict)
    ]


def _unique_signature_count(cases: list[dict[str, Any]]) -> int:
    return int(len({case_signature(item) for item in cases if case_signature(item)}))


def _is_recoverable_case(case: dict[str, Any]) -> bool:
    expected_text = case_text_field(case, "expected_result")
    expected_quality = str(case.get("expected_result_quality") or "").strip().lower()
    return not (
        expected_quality in {"invalid_case", "non_assertable", "truncated"}
        or reasoning_leakage_hits(case)
        or looks_truncated_text(expected_text)
        or is_non_assertable_expected_result(expected_text)
    )


def _resolve_recovery_group_count(
    *,
    requirement: str,
    recovery_pool_seed: list[dict[str, Any]],
    project_profile: dict[str, Any] | None,
    analyze_case_structure_fn: Callable[..., dict[str, Any]],
) -> int:
    try:
        recovery_structure = analyze_case_structure_fn(
            requirement,
            recovery_pool_seed,
            project_profile=project_profile,
        )
        return int(
            len(
                {
                    str(row.get("duplicate_group_key") or row.get("intent_signature") or row.get("scenario_key") or "")
                    for row in (recovery_structure.get("rows") or [])
                    if isinstance(row, dict)
                }
            )
        )
    except Exception:
        return 0


def _build_recovery_pool(
    *,
    parsed_result: list[dict[str, Any]],
    recovery_pool_seed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    final_signatures_before_recovery = {
        case_signature(item) for item in parsed_result if isinstance(item, dict)
    }
    recovery_seen_signatures = set(final_signatures_before_recovery)
    recovery_pool: list[dict[str, Any]] = []
    for source_case in recovery_pool_seed:
        sig = case_signature(source_case)
        if not sig or sig in recovery_seen_signatures:
            continue
        recovery_seen_signatures.add(sig)
        if not _is_recoverable_case(source_case):
            continue
        recovery_pool.append(source_case)
    return recovery_pool


def recover_final_floor_from_candidate_pool(
    *,
    requirement: str,
    parsed_result: list[dict[str, Any]],
    flow_governance_summary: dict[str, Any],
    initial_final_target_floor_count: int,
    review_candidate_cases: list[dict[str, Any]],
    review_selection_input: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
    candidate_count_before_review: int,
    expected_count: int,
    expected_count_value: int,
    effective_generation_coverage_mode: str,
    resolved_full_regression_floor: int,
    append: bool,
    project_profile: dict[str, Any] | None,
    flow_project_profile: dict[str, Any],
    start_id: int,
    feedback_control_state: dict[str, Any] | None,
    requirement_semantics_context: dict[str, Any] | None,
    analyze_case_structure_fn: Callable[..., dict[str, Any]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    govern_cases_by_flow_structure_fn: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    rank_case_fn: Callable[..., tuple[int, ...]],
) -> FinalFloorRecoveryResult:
    result_cases = _dict_case_items(parsed_result)
    result_flow_summary = dict(flow_governance_summary or {})
    final_target_floor_count = int(initial_final_target_floor_count or 0)
    attempted = False
    applied = False
    recovered_count = 0
    reason = ""

    recovery_pool_seed = _recovery_pool_seed(
        review_candidate_cases=review_candidate_cases,
        review_selection_input=review_selection_input,
        candidate_cases=candidate_cases,
    )
    recovery_pool_unique_signature_count = _unique_signature_count(recovery_pool_seed)
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
        if effective_generation_coverage_mode == "expanded_regression":
            final_target_floor_count = max(
                int(final_target_floor_count or 0),
                int(round(float(expected_count or 0) * 0.80)),
            )
        else:
            final_target_floor_count = max(
                int(final_target_floor_count or 0),
                int(resolved_full_regression_floor or 0),
            )

    if int(final_target_floor_count or 0) <= 0 or append:
        return FinalFloorRecoveryResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            final_target_floor_count=final_target_floor_count,
            attempted=attempted,
            applied=applied,
            recovered_count=recovered_count,
            reason=reason,
        )

    current_final_count = _dict_case_count(result_cases)
    if current_final_count >= final_target_floor_count:
        return FinalFloorRecoveryResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            final_target_floor_count=final_target_floor_count,
            attempted=attempted,
            applied=applied,
            recovered_count=recovered_count,
            reason=reason,
        )

    attempted = True
    recovery_group_count = _resolve_recovery_group_count(
        requirement=requirement,
        recovery_pool_seed=recovery_pool_seed,
        project_profile=project_profile,
        analyze_case_structure_fn=analyze_case_structure_fn,
    )
    allow_relaxed_floor_recovery = bool(
        final_floor_candidate_count >= int(final_target_floor_count or 0)
        and int(expected_count_value or 0) > 0
    )
    if recovery_group_count < final_target_floor_count and not allow_relaxed_floor_recovery:
        return FinalFloorRecoveryResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            final_target_floor_count=final_target_floor_count,
            attempted=attempted,
            applied=applied,
            recovered_count=recovered_count,
            reason="insufficient_diverse_candidate_groups",
        )

    recovery_pool = _build_recovery_pool(
        parsed_result=result_cases,
        recovery_pool_seed=recovery_pool_seed,
    )
    recovery_coverage = analyze_coverage_fn(requirement, recovery_pool_seed)
    recovery_rule_diagnostics = _rule_diagnostics_payload(recovery_coverage)
    recovery_pool.sort(
        key=lambda item: tuple(
            [
                -value
                for value in rank_case_fn(
                    item,
                    coverage_context=recovery_coverage,
                    rule_diagnostics=recovery_rule_diagnostics,
                )
            ]
        )
        + (review_case_id(item),)
    )
    recovered: list[dict[str, Any]] = []
    for fill_case in recovery_pool:
        if current_final_count + len(recovered) >= final_target_floor_count:
            break
        recovered.append(fill_case)

    if not recovered:
        return FinalFloorRecoveryResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            final_target_floor_count=final_target_floor_count,
            attempted=attempted,
            applied=applied,
            recovered_count=recovered_count,
            reason="no_recoverable_candidates_after_quality_filter",
        )

    merged_for_recovery = deduplicate_test_cases_fn([*result_cases, *recovered])
    merged_for_recovery = apply_coverage_priority_semantics(
        requirement,
        merged_for_recovery,
        analyze_coverage_fn=analyze_coverage_fn,
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
        return FinalFloorRecoveryResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            final_target_floor_count=final_target_floor_count,
            attempted=attempted,
            applied=False,
            recovered_count=0,
            reason="recovery_judge_failed",
        )

    result_cases, result_flow_summary = govern_cases_by_flow_structure_fn(
        requirement,
        _dict_case_items(merged_for_recovery),
        start_id=start_id,
        renumber_ids=True,
        max_per_scenario=2,
        project_profile=flow_project_profile,
    )
    if _dict_case_count(result_cases) < final_target_floor_count:
        relaxed_flow_profile = _flow_profile_with_scenario_policy(
            flow_project_profile,
            coverage_mode=str(effective_generation_coverage_mode or ""),
            disable_scenario_pruning=True,
            intent_duplicate_cap=1,
            relaxed_for_floor_backfill=True,
        )
        relaxed_result, relaxed_summary = govern_cases_by_flow_structure_fn(
            requirement,
            _dict_case_items(merged_for_recovery),
            start_id=start_id,
            renumber_ids=True,
            max_per_scenario=2,
            project_profile=relaxed_flow_profile,
        )
        if _dict_case_count(relaxed_result) > _dict_case_count(result_cases):
            result_cases = relaxed_result
            result_flow_summary = relaxed_summary
            result_flow_summary["relaxed_for_floor_backfill"] = True

    recovered_count = max(0, _dict_case_count(result_cases) - current_final_count)
    if int(recovered_count or 0) > 0:
        applied = True
        reason = (
            "recovered_with_relaxed_scenario_caps"
            if bool(result_flow_summary.get("relaxed_for_floor_backfill"))
            else "recovered_to_explicit_expected_floor"
        )
    else:
        reason = "recovery_candidates_rejected_or_pruned"

    return FinalFloorRecoveryResult(
        cases=result_cases,
        flow_governance_summary=result_flow_summary,
        final_target_floor_count=final_target_floor_count,
        attempted=attempted,
        applied=applied,
        recovered_count=recovered_count,
        reason=reason,
    )


def recover_final_floor_after_conflict_filter(
    *,
    requirement: str,
    kb_context: str,
    parsed_result: list[dict[str, Any]],
    flow_governance_summary: dict[str, Any],
    final_target_floor_count: int,
    final_floor_recovered_count: int,
    effective_generation_coverage_mode: str,
    review_candidate_cases: list[dict[str, Any]],
    review_selection_input: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
    fact_profile: dict[str, Any],
    flow_project_profile: dict[str, Any],
    start_id: int,
    feedback_control_state: dict[str, Any] | None = None,
    requirement_semantics_context: dict[str, Any] | None = None,
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    filter_conflicting_cases_fn: Callable[..., tuple[list[dict[str, Any]], int]],
    govern_cases_by_flow_structure_fn: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    rank_case_fn: Callable[..., tuple[int, ...]],
) -> PostConflictFloorRecoveryResult:
    result_cases = _dict_case_items(parsed_result)
    result_flow_summary = dict(flow_governance_summary or {})
    current_after_conflict = _dict_case_count(result_cases)
    recovery_pool_seed = _recovery_pool_seed(
        review_candidate_cases=review_candidate_cases,
        review_selection_input=review_selection_input,
        candidate_cases=candidate_cases,
    )
    post_conflict_pool = [
        dict(item)
        for item in _build_recovery_pool(
            parsed_result=result_cases,
            recovery_pool_seed=recovery_pool_seed,
        )
    ]
    post_conflict_pool, _post_conflict_pool_drop = filter_conflicting_cases_fn(
        post_conflict_pool,
        requirement=str(requirement or ""),
        kb_context=str(kb_context or ""),
        fact_profile=fact_profile,
    )
    recovery_coverage = analyze_coverage_fn(requirement, recovery_pool_seed)
    recovery_rule_diagnostics = _rule_diagnostics_payload(recovery_coverage)
    post_conflict_pool.sort(
        key=lambda item: tuple(
            [
                -value
                for value in rank_case_fn(
                    item,
                    coverage_context=recovery_coverage,
                    rule_diagnostics=recovery_rule_diagnostics,
                )
            ]
        )
        + (review_case_id(item),)
    )
    recovered_after_conflict: list[dict[str, Any]] = []
    for fill_case in post_conflict_pool:
        if current_after_conflict + len(recovered_after_conflict) >= int(final_target_floor_count or 0):
            break
        recovered_after_conflict.append(fill_case)

    if not recovered_after_conflict:
        return PostConflictFloorRecoveryResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            applied=False,
            recovered_count=int(final_floor_recovered_count or 0),
            reason="",
        )

    merged_after_conflict = deduplicate_test_cases_fn([*result_cases, *recovered_after_conflict])
    merged_after_conflict = reorder_cases_by_closed_loop_fn(
        _dict_case_items(merged_after_conflict),
        start_id=start_id,
        renumber_ids=True,
    )
    merged_after_conflict = apply_coverage_priority_semantics(
        requirement,
        merged_after_conflict,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    try:
        from ..judge.test_case_judge import judge_cases as recovery_judge_cases
        from ..judge.test_case_repairer import repair_cases as recovery_repair_cases
        from ..judge.training_gate import training_gate as recovery_training_gate

        recovery_judged = recovery_judge_cases(
            cases=_dict_case_items(merged_after_conflict),
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
        merged_after_conflict = [*recovery_confirmed, *recovery_repaired_pass]
    except Exception:
        return PostConflictFloorRecoveryResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            applied=False,
            recovered_count=int(final_floor_recovered_count or 0),
            reason="post_conflict_recovery_judge_failed",
        )
    if _dict_case_count(merged_after_conflict) <= current_after_conflict:
        return PostConflictFloorRecoveryResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            applied=False,
            recovered_count=int(final_floor_recovered_count or 0),
            reason="post_conflict_recovery_rejected_by_judge",
        )
    if effective_generation_coverage_mode == "full_functional_regression":
        relaxed_flow_profile = _flow_profile_with_scenario_policy(
            flow_project_profile,
            coverage_mode=str(effective_generation_coverage_mode or ""),
            disable_scenario_pruning=True,
            intent_duplicate_cap=1,
            relaxed_for_floor_backfill=True,
        )
        result_cases, result_flow_summary = govern_cases_by_flow_structure_fn(
            requirement,
            _dict_case_items(merged_after_conflict),
            start_id=start_id,
            renumber_ids=True,
            max_per_scenario=2,
            project_profile=relaxed_flow_profile,
        )
        result_flow_summary["relaxed_for_floor_backfill"] = True
    else:
        result_cases, result_flow_summary = govern_cases_by_flow_structure_fn(
            requirement,
            _dict_case_items(merged_after_conflict),
            start_id=start_id,
            renumber_ids=True,
            max_per_scenario=2,
            project_profile=flow_project_profile,
        )

    recovered_count = max(
        int(final_floor_recovered_count or 0),
        max(0, _dict_case_count(result_cases) - current_after_conflict),
    )
    return PostConflictFloorRecoveryResult(
        cases=result_cases,
        flow_governance_summary=result_flow_summary,
        applied=True,
        recovered_count=recovered_count,
        reason="recovered_after_confirmed_conflict_filter",
    )


__all__ = [
    "FinalFloorRecoveryResult",
    "PostConflictFloorRecoveryResult",
    "recover_final_floor_after_conflict_filter",
    "recover_final_floor_from_candidate_pool",
]
