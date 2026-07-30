from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .streaming_postprocess_utils import _dict_case_items


@dataclass(frozen=True)
class StreamingJudgeGateResult:
    cases: list[dict[str, Any]]
    judge_summary_payload: dict[str, Any]
    judge_decision_table_payload: list[dict[str, Any]]


def run_streaming_judge_gate(
    *,
    cases: list[dict[str, Any]],
    requirement_semantics_context: dict[str, Any],
    feedback_control_state: dict[str, Any] | None,
    fact_profile: dict[str, Any],
    review_case_id_fn: Callable[[dict[str, Any]], str],
    build_judge_summary_payload_fn: Callable[..., dict[str, Any]],
    build_judge_decision_table_payload_fn: Callable[..., list[dict[str, Any]]],
    judge_cases_fn: Callable[..., Any] | None = None,
    repair_cases_fn: Callable[..., Any] | None = None,
    training_gate_fn: Callable[..., Any] | None = None,
) -> StreamingJudgeGateResult:
    """生成 Judge 诊断，但不覆盖全局 Review 已选定的用例集合。"""
    result_cases = _dict_case_items(cases)
    try:
        if judge_cases_fn is None or repair_cases_fn is None or training_gate_fn is None:
            from ..judge.test_case_judge import judge_cases
            from ..judge.test_case_repairer import repair_cases
            from ..judge.training_gate import training_gate

            judge_cases_fn = judge_cases_fn or judge_cases
            repair_cases_fn = repair_cases_fn or repair_cases
            training_gate_fn = training_gate_fn or training_gate

        control_state = feedback_control_state if isinstance(feedback_control_state, dict) else {}
        semantics_context = requirement_semantics_context or {}
        judged = judge_cases_fn(
            cases=_dict_case_items(result_cases),
            requirement_semantics_context=semantics_context,
            control_state=control_state,
        )
        repaired = repair_cases_fn(
            judged=judged,
            requirement_semantics_context=semantics_context,
            control_state=control_state,
            strategy="rule_first_llm_fallback",
        )
        confirmed_pass_cases, repaired_pass_cases, rejected_cases, pending_cases = training_gate_fn(repaired)
        judge_summary_payload = build_judge_summary_payload_fn(
            repaired=repaired,
            input_count=len(result_cases),
            confirmed_pass_cases=confirmed_pass_cases,
            repaired_pass_cases=repaired_pass_cases,
            rejected_cases=rejected_cases,
            pending_cases=pending_cases,
            fact_profile=fact_profile,
        )
        judge_decision_table_payload = build_judge_decision_table_payload_fn(
            repaired=repaired,
            review_case_id_fn=review_case_id_fn,
        )
        return StreamingJudgeGateResult(
            cases=_dict_case_items(result_cases),
            judge_summary_payload=dict(judge_summary_payload or {}),
            judge_decision_table_payload=_dict_case_items(judge_decision_table_payload),
        )
    except Exception:
        return StreamingJudgeGateResult(
            cases=_dict_case_items(result_cases),
            judge_summary_payload={},
            judge_decision_table_payload=[],
        )
