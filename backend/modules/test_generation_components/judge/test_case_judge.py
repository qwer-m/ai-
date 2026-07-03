from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..postprocess.case_access import (
    case_flat_text,
    case_id as case_access_id,
)

from .judge_duplicate_rules import (
    _CROSS_MODULE_DUPLICATE_SCENARIOS as _CROSS_MODULE_DUPLICATE_SCENARIOS,
    _DUPLICATE_SCENARIO_PATTERNS as _DUPLICATE_SCENARIO_PATTERNS,
    _DUPLICATE_SCENARIO_THRESHOLDS as _DUPLICATE_SCENARIO_THRESHOLDS,
    _DUPLICATE_SIMPLE_SCENARIOS as _DUPLICATE_SIMPLE_SCENARIOS,
    _REGISTERED_SCENARIO_KINDS as _REGISTERED_SCENARIO_KINDS,
    _REGISTERED_SCENARIO_THRESHOLDS as _REGISTERED_SCENARIO_THRESHOLDS,
    _SEMANTIC_STOP_TOKENS as _SEMANTIC_STOP_TOKENS,
    _case_quality_key as _case_quality_key,
    _is_semantic_duplicate_case as _is_semantic_duplicate_case,
    _module_family as _module_family,
    _priority_rank as _priority_rank,
    _same_module_family as _same_module_family,
    _scenario_kind as _scenario_kind,
    _semantic_overlap_size as _semantic_overlap_size,
    _semantic_similarity as _semantic_similarity,
    _semantic_similarity_text as _semantic_similarity_text,
    _semantic_tokens as _semantic_tokens,
)
from .judge_fact_rules import (
    _NEGATIVE_MARKERS as _NEGATIVE_MARKERS,
    _PENDING_HINTS as _PENDING_HINTS,
    _VAGUE_UNCONFIRMED_HINTS as _VAGUE_UNCONFIRMED_HINTS,
    normalize_requirement_semantics_context as normalize_requirement_semantics_context,
    _merge_fact_profile_semantics as _merge_fact_profile_semantics,
    _contains_pending_logic as _contains_pending_logic,
    _contains_vague_unconfirmed_logic as _contains_vague_unconfirmed_logic,
    _extract_sequence_candidates as _extract_sequence_candidates,
    _MIN_NEGATIVE_FACT_TAIL_CHARS as _MIN_NEGATIVE_FACT_TAIL_CHARS,
    _TEMPORAL_SHUTDOWN_SCOPE_MARKERS as _TEMPORAL_SHUTDOWN_SCOPE_MARKERS,
    _TEMPORAL_SHUTDOWN_BLOCK_MARKERS as _TEMPORAL_SHUTDOWN_BLOCK_MARKERS,
    _TEMPORAL_SHUTDOWN_POSITIVE_MARKERS as _TEMPORAL_SHUTDOWN_POSITIVE_MARKERS,
    _NEGATED_TAIL_CONTEXT_MARKERS as _NEGATED_TAIL_CONTEXT_MARKERS,
    _contains_raw_marker as _contains_raw_marker,
    _negative_fact_marker_pattern as _negative_fact_marker_pattern,
    _split_negative_fact_tail as _split_negative_fact_tail,
    _is_temporal_shutdown_fact as _is_temporal_shutdown_fact,
    _case_negates_tail as _case_negates_tail,
    _violates_temporal_shutdown_fact as _violates_temporal_shutdown_fact,
    _violates_negative_fact as _violates_negative_fact,
    _violates_flow_order as _violates_flow_order,
    _is_time_window_scope_rule as _is_time_window_scope_rule,
    _matches_time_window_scope as _matches_time_window_scope,
    _is_before_deadline_context as _is_before_deadline_context,
    _violates_time_window_scope_rule as _violates_time_window_scope_rule,
    _rule_applies_to_case as _rule_applies_to_case,
    _find_confirmed_fact_violations as _find_confirmed_fact_violations,
    _find_forbidden_fact_violations as _find_forbidden_fact_violations,
    _hits_any_pattern as _hits_any_pattern,
)
from .judge_text_utils import (
    _dedupe_texts as _dedupe_texts,
    _normalize_text as _normalize_text,
)
from .judge_types import (
    JudgeBatchResult,
    JudgeResult,
    JudgeSignalSet,
    JudgeStatus,
    RepairAction,
    RepairActionType,
)




def _collect_case_text(case: dict[str, Any]) -> str:
    return case_flat_text(
        case,
        fields=("id", "description", "test_module", "test_input", "expected_result", "preconditions", "steps", "tags"),
        separator=" ",
    )




def judge_case(
    case: dict[str, Any],
    requirement_semantics_context: dict[str, Any] | str | None,
    control_state: dict[str, Any] | None = None,
) -> JudgeResult:
    semantics = _merge_fact_profile_semantics(
        normalize_requirement_semantics_context(requirement_semantics_context),
        control_state=control_state,
    )
    before = deepcopy(case) if isinstance(case, dict) else {}
    case_id = case_access_id(before) or "UNKNOWN"
    case_text = _collect_case_text(before)

    contains_pending_logic, pending_hits = _contains_pending_logic(case_text, semantics.get("pending_items") or [])
    contains_vague_unconfirmed, vague_or_unconfirmed_hits = _contains_vague_unconfirmed_logic(before)
    contains_pending_logic = bool(contains_pending_logic or contains_vague_unconfirmed)
    pending_hits = _dedupe_texts([*pending_hits, *vague_or_unconfirmed_hits])
    confirmed_fact_hits, confirmed_fact_violations = _find_confirmed_fact_violations(
        case_text,
        semantics.get("confirmed_facts") or [],
        semantics.get("scoped_rules") or [],
        semantics.get("hard_flow_constraints") or [],
    )
    forbidden_fact_violations = _find_forbidden_fact_violations(
        case_text,
        semantics.get("forbidden_facts") or [],
    )
    confirmed_fact_violations = _dedupe_texts(
        [*confirmed_fact_violations, *forbidden_fact_violations]
    )
    reuse_risk_hits = _hits_any_pattern(case_text, semantics.get("reuse_risks") or [])

    signals = JudgeSignalSet(
        violates_confirmed_fact=bool(confirmed_fact_violations),
        contains_pending_logic=bool(contains_pending_logic),
        confirmed_fact_hits=confirmed_fact_hits,
        confirmed_fact_violations=confirmed_fact_violations,
        reuse_risk_hits=reuse_risk_hits,
        pending_hits=pending_hits,
        vague_or_unconfirmed_hits=vague_or_unconfirmed_hits,
    )

    if signals.contains_pending_logic:
        return JudgeResult(
            case_id=case_id,
            status=JudgeStatus.PENDING,
            signals=signals,
            pending_reason="contains_pending_logic",
            suggested_actions=[
                RepairAction(
                    action_type=RepairActionType.ISOLATE_PENDING,
                    reason="Case contains pending/unconfirmed statements.",
                    target_case_id=case_id,
                )
            ],
            before_case=before,
        )

    if signals.violates_confirmed_fact:
        return JudgeResult(
            case_id=case_id,
            status=JudgeStatus.REJECT,
            signals=signals,
            reject_reason="violates_confirmed_fact",
            suggested_actions=[
                RepairAction(
                    action_type=RepairActionType.DROP_CASE,
                    reason="Case violates confirmed facts or hard flow constraints.",
                    target_case_id=case_id,
                )
            ],
            before_case=before,
        )

    return JudgeResult(
        case_id=case_id,
        status=JudgeStatus.PASS,
        signals=signals,
        before_case=before,
        after_case=deepcopy(before),
    )


def _all_patterns_covered(cases: list[dict[str, Any]], patterns: list[str]) -> tuple[bool, list[str]]:
    if not patterns:
        return True, []
    missing: list[str] = []
    for pattern in patterns:
        marker = _normalize_text(pattern)
        if not marker:
            continue
        hit = False
        for case in cases:
            if marker in _normalize_text(_collect_case_text(case)):
                hit = True
                break
        if not hit:
            missing.append(pattern)
    return len(missing) == 0, missing


def judge_cases(
    cases: list[dict[str, Any]],
    requirement_semantics_context: dict[str, Any] | str | None,
    control_state: dict[str, Any] | None = None,
) -> JudgeBatchResult:
    semantics = _merge_fact_profile_semantics(
        normalize_requirement_semantics_context(requirement_semantics_context),
        control_state=control_state,
    )
    judged_cases: list[JudgeResult] = []
    for index, case in enumerate(cases or [], start=1):
        if not isinstance(case, dict):
            continue
        judged = judge_case(case, semantics, control_state=control_state)
        if not judged.case_id or judged.case_id == "UNKNOWN":
            judged.case_id = case_access_id(case) or f"CASE-{index:03d}"
        judged_cases.append(judged)

    kept_passes: list[tuple[int, JudgeResult, dict[str, Any]]] = []
    for index, item in enumerate(judged_cases):
        if item.status != JudgeStatus.PASS:
            continue
        candidate_case = item.after_case if item.after_case else item.before_case
        if not isinstance(candidate_case, dict):
            continue

        duplicate_match: tuple[int, JudgeResult, dict[str, Any], float] | None = None
        for kept_index, kept_item, kept_case in kept_passes:
            is_duplicate, similarity = _is_semantic_duplicate_case(candidate_case, kept_case)
            if not is_duplicate:
                continue
            if duplicate_match is None or similarity > duplicate_match[3]:
                duplicate_match = (kept_index, kept_item, kept_case, similarity)

        if duplicate_match is None:
            kept_passes.append((index, item, candidate_case))
            continue

        kept_index, kept_item, kept_case, similarity = duplicate_match
        candidate_quality = _case_quality_key(candidate_case, index)
        kept_quality = _case_quality_key(kept_case, kept_index)

        if candidate_quality > kept_quality:
            kept_item.status = JudgeStatus.REJECT
            kept_item.reject_reason = f"semantic_duplicate:{item.case_id}"
            kept_item.signals.is_semantic_duplicate = True
            kept_item.signals.duplicate_of_case_id = item.case_id
            kept_item.signals.duplicate_similarity = round(float(similarity), 4)
            kept_item.signals.notes = _dedupe_texts([*kept_item.signals.notes, "batch_semantic_duplicate"])
            kept_item.suggested_actions = [
                RepairAction(
                    action_type=RepairActionType.DROP_CASE,
                    reason="Case is semantically duplicated by a stronger candidate.",
                    target_case_id=kept_item.case_id,
                    payload={"duplicate_of_case_id": item.case_id, "similarity": round(float(similarity), 4)},
                )
            ]
            kept_passes = [
                (
                    index if existing_index == kept_index else existing_index,
                    item if existing_index == kept_index else existing_item,
                    candidate_case if existing_index == kept_index else existing_case,
                )
                for existing_index, existing_item, existing_case in kept_passes
            ]
            continue

        item.status = JudgeStatus.REJECT
        item.reject_reason = f"semantic_duplicate:{kept_item.case_id}"
        item.signals.is_semantic_duplicate = True
        item.signals.duplicate_of_case_id = kept_item.case_id
        item.signals.duplicate_similarity = round(float(similarity), 4)
        item.signals.notes = _dedupe_texts([*item.signals.notes, "batch_semantic_duplicate"])
        item.suggested_actions = [
            RepairAction(
                action_type=RepairActionType.DROP_CASE,
                reason="Case is semantically duplicated by an already accepted candidate.",
                target_case_id=item.case_id,
                payload={"duplicate_of_case_id": kept_item.case_id, "similarity": round(float(similarity), 4)},
            )
        ]

    pass_cases = [
        item.after_case if item.after_case else item.before_case
        for item in judged_cases
        if item.status == JudgeStatus.PASS
    ]

    core_flow_patterns = semantics.get("hard_flow_constraints") or []
    reuse_risk_patterns = semantics.get("reuse_risks") or []
    core_flow_covered, missing_core_flow = _all_patterns_covered(pass_cases, core_flow_patterns)
    reuse_risk_covered, missing_reuse_risk = _all_patterns_covered(pass_cases, reuse_risk_patterns)

    if not core_flow_covered:
        judged_cases.append(
            JudgeResult(
                case_id="AUTO-CORE-FLOW",
                status=JudgeStatus.REPAIRABLE,
                signals=JudgeSignalSet(
                    missing_core_flow=True,
                    notes=["batch_level_gap"],
                ),
                suggested_actions=[
                    RepairAction(
                        action_type=RepairActionType.APPEND_CORE_FLOW_CASE,
                        reason="Batch misses core flow coverage.",
                        payload={"missing_core_flow_items": missing_core_flow},
                    )
                ],
                before_case={},
            )
        )

    if not reuse_risk_covered:
        judged_cases.append(
            JudgeResult(
                case_id="AUTO-REUSE-RISK",
                status=JudgeStatus.REPAIRABLE,
                signals=JudgeSignalSet(
                    missing_reuse_risk=True,
                    missing_reuse_risk_items=missing_reuse_risk,
                    notes=["batch_level_gap"],
                ),
                suggested_actions=[
                    RepairAction(
                        action_type=RepairActionType.APPEND_REUSE_RISK_CASE,
                        reason="Batch misses reuse risk coverage.",
                        payload={"missing_reuse_risk_items": missing_reuse_risk},
                    )
                ],
                before_case={},
            )
        )

    result = JudgeBatchResult(
        cases=judged_cases,
        core_flow_covered=bool(core_flow_covered),
        reuse_risk_covered=bool(reuse_risk_covered),
        pass_count=sum(1 for item in judged_cases if item.status == JudgeStatus.PASS),
        repairable_count=sum(1 for item in judged_cases if item.status == JudgeStatus.REPAIRABLE),
        reject_count=sum(1 for item in judged_cases if item.status == JudgeStatus.REJECT),
        pending_count=sum(1 for item in judged_cases if item.status == JudgeStatus.PENDING),
        notes=[
            f"confirmed_facts={len(semantics.get('confirmed_facts') or [])}",
            f"scoped_rules={len(semantics.get('scoped_rules') or [])}",
            f"pending_items={len(semantics.get('pending_items') or [])}",
            f"hard_flow_constraints={len(core_flow_patterns)}",
            f"reuse_risks={len(reuse_risk_patterns)}",
            f"registry_duplicate_scenario_kinds={len(_CROSS_MODULE_DUPLICATE_SCENARIOS)}",
            f"registry_threshold_entries={len(_DUPLICATE_SCENARIO_THRESHOLDS)}",
        ],
    )
    return result
