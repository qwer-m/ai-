from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StreamPostprocessResultPayload:
    parsed_result: list[Any]
    stage_counts: dict[str, Any]
    coverage_payload: dict[str, Any]
    convergence_payload: dict[str, Any]
    generation_summary_payload: dict[str, Any]
    review_decision_summary_payload: dict[str, Any]
    review_decision_table_payload: list[dict[str, Any]]
    judge_decision_table_payload: list[dict[str, Any]]
    feedback_control_debug_payload: dict[str, Any]
    judge_summary_payload: dict[str, Any]
    generation_timing_events: list[dict[str, Any]]


def merge_pre_projection_functional_phase_summary(
    module_contract_summary: dict[str, Any],
    *,
    review_decision_summary: dict[str, Any],
    final_case_count: int,
) -> dict[str, Any]:
    """用公共字段投影前的阶段统计修正最终诊断，不恢复内部字段。"""
    result = dict(module_contract_summary or {})
    execution_plan = review_decision_summary.get("execution_plan")
    if not isinstance(execution_plan, dict):
        return result
    coverage = execution_plan.get("functional_phase_coverage")
    if not isinstance(coverage, dict) or not bool(coverage.get("applied")):
        return result
    phase_counts = {
        str(key): int(value or 0)
        for key, value in (coverage.get("phase_counts") or {}).items()
        if str(key).strip() and int(value or 0) > 0
    }
    if sum(phase_counts.values()) != max(0, int(final_case_count or 0)):
        return result
    result["functional_phase_counts"] = phase_counts
    result["functional_phase_counts_source"] = "execution_plan_pre_public_projection"
    result["functional_module_counts"] = dict(coverage.get("module_counts") or {})
    result["functional_interaction_counts"] = dict(coverage.get("interaction_counts") or {})
    result["functional_uncovered_modules"] = list(coverage.get("uncovered_modules") or [])
    result["functional_uncovered_interactions"] = list(coverage.get("uncovered_interactions") or [])
    return result


def unpack_stream_postprocess_result(
    postprocess_result: Any,
    *,
    generation_timing_events: list[dict[str, Any]],
    sanitize_timing_events_fn: Callable[[Any], list[dict[str, Any]]],
) -> StreamPostprocessResultPayload:
    stage_counts: dict[str, Any] = {}
    coverage_payload: dict[str, Any] = {}
    convergence_payload: dict[str, Any] = {}
    generation_summary_payload: dict[str, Any] = {}
    review_decision_summary_payload: dict[str, Any] = {}
    review_decision_table_payload: list[dict[str, Any]] = []
    judge_decision_table_payload: list[dict[str, Any]] = []
    feedback_control_debug_payload: dict[str, Any] = {}
    judge_summary_payload: dict[str, Any] = {}

    if isinstance(postprocess_result, dict):
        parsed_result = postprocess_result.get("cases")
        if not isinstance(parsed_result, list):
            parsed_result = []
        stage_counts = dict(postprocess_result.get("stage_counts") or {})
        coverage_payload = dict(postprocess_result.get("coverage") or {})
        convergence_payload = dict(postprocess_result.get("convergence_debug") or {})
        generation_summary_payload = dict(postprocess_result.get("generation_summary") or {})
        review_decision_summary_payload = dict(
            postprocess_result.get("review_decision_summary") or {}
        )
        generation_timing_events.extend(
            sanitize_timing_events_fn(postprocess_result.get("timing_events") or [])
        )
        review_decision_table_payload = [
            item
            for item in (postprocess_result.get("review_decision_table") or [])
            if isinstance(item, dict)
        ]
        judge_decision_table_payload = [
            item
            for item in (postprocess_result.get("judge_decision_table") or [])
            if isinstance(item, dict)
        ]
        feedback_control_debug_payload = dict(postprocess_result.get("feedback_control_debug") or {})
        judge_summary_payload = dict(postprocess_result.get("judge_summary") or {})
    else:
        parsed_result = postprocess_result if isinstance(postprocess_result, list) else []

    return StreamPostprocessResultPayload(
        parsed_result=parsed_result,
        stage_counts=stage_counts,
        coverage_payload=coverage_payload,
        convergence_payload=convergence_payload,
        generation_summary_payload=generation_summary_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        review_decision_table_payload=review_decision_table_payload,
        judge_decision_table_payload=judge_decision_table_payload,
        feedback_control_debug_payload=feedback_control_debug_payload,
        judge_summary_payload=judge_summary_payload,
        generation_timing_events=generation_timing_events,
    )
