from __future__ import annotations

from copy import deepcopy
from typing import Any

from .judge_types import JudgeBatchResult, JudgeResult, JudgeSignalSet, JudgeStatus, RepairActionType


_UNTYPED_GAP_NOTE = "untyped_batch_gap_not_auto_repaired"


def _append_note(signals: JudgeSignalSet, note: str) -> JudgeSignalSet:
    current = [str(item) for item in (signals.notes or []) if str(item).strip()]
    if note not in current:
        current.append(note)
    signals.notes = current
    return signals


def _mark_unrepaired_gap(result: JudgeResult, reason: str) -> JudgeResult:
    result.after_case = {}
    result.repaired = False
    result.repaired_pass = False
    result.reject_reason = result.reject_reason or reason
    result.signals = _append_note(result.signals, _UNTYPED_GAP_NOTE)
    return result


def repair_case(
    judge_result: JudgeResult,
    requirement_semantics_context: dict[str, Any] | str | None,
    control_state: dict[str, Any] | None = None,
    strategy: str = "rule_first_llm_fallback",
) -> JudgeResult:
    _ = strategy
    _ = control_state
    _ = requirement_semantics_context

    result = deepcopy(judge_result)
    if result.status != JudgeStatus.REPAIRABLE:
        return result

    for action in result.suggested_actions:
        if action.action_type in {
            RepairActionType.APPEND_CORE_FLOW_CASE,
            RepairActionType.APPEND_REUSE_RISK_CASE,
        }:
            return _mark_unrepaired_gap(result, "requires_typed_requirement_unit")
        if action.action_type in {RepairActionType.ISOLATE_PENDING, RepairActionType.DROP_CASE}:
            return _mark_unrepaired_gap(result, "repair_action_not_safe_for_final_output")

    return _mark_unrepaired_gap(result, "no_safe_repair_action")


def repair_cases(
    judged: JudgeBatchResult,
    requirement_semantics_context: dict[str, Any] | str | None,
    control_state: dict[str, Any] | None = None,
    strategy: str = "rule_first_llm_fallback",
) -> JudgeBatchResult:
    repaired_cases: list[JudgeResult] = []
    for item in judged.cases:
        repaired_cases.append(
            repair_case(
                judge_result=item,
                requirement_semantics_context=requirement_semantics_context,
                control_state=control_state,
                strategy=strategy,
            )
        )

    appended_case_count = sum(
        1
        for item in repaired_cases
        if item.status == JudgeStatus.REPAIRABLE and item.repaired_pass and isinstance(item.after_case, dict) and item.after_case
    )
    repaired_case_count = sum(1 for item in repaired_cases if bool(item.repaired))

    return JudgeBatchResult(
        cases=repaired_cases,
        core_flow_covered=judged.core_flow_covered,
        reuse_risk_covered=judged.reuse_risk_covered,
        pass_count=sum(1 for item in repaired_cases if item.status == JudgeStatus.PASS),
        repairable_count=sum(1 for item in repaired_cases if item.status == JudgeStatus.REPAIRABLE),
        reject_count=sum(1 for item in repaired_cases if item.status == JudgeStatus.REJECT),
        pending_count=sum(1 for item in repaired_cases if item.status == JudgeStatus.PENDING),
        appended_case_count=int(appended_case_count),
        repaired_case_count=int(repaired_case_count),
        notes=list(judged.notes or []),
    )
