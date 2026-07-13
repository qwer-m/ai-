from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from .json_generation_fallback_review import run_json_fallback_review


@dataclass(frozen=True)
class JsonReviewPostprocessResult:
    result: Any
    stage_counts: dict[str, Any]
    coverage_check_payload: dict[str, Any] | None
    convergence_payload: dict[str, Any]
    generation_summary_payload: dict[str, Any]
    review_decision_summary_payload: dict[str, Any]
    review_decision_table_payload: list[dict[str, Any]]
    judge_summary_payload: dict[str, Any]
    judge_decision_table_payload: list[dict[str, Any]]
    candidate_cases_before_judge: list[dict[str, Any]]
    candidate_total_before_judge: int
    final_cases_after_judge: list[dict[str, Any]]
    final_case_count: int
    empty_result_guard_triggered: bool
    empty_result_stage: str
    stream_postprocess_applied: bool


def drain_generator_return(iterator: Any) -> Any:
    """Exhaust a generator and return its StopIteration value."""
    while True:
        try:
            next(iterator)
        except StopIteration as stop:
            return stop.value


def run_json_review_postprocess(
    *,
    result: Any,
    db: Any,
    client: Any,
    requirement: str,
    base_prompt: str,
    kb_context: str,
    expected_count: int,
    start_id: int,
    resolved_current_biz: str,
    multi_pass: bool,
    generation_mode: str,
    feedback_control_state: dict[str, Any],
    prompt_context: dict[str, Any],
    stage_logs: list[dict[str, Any]],
    stage_counts: dict[str, Any],
    coverage_check_payload: dict[str, Any] | None,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    count_unique_test_cases_fn: Callable[[list[dict[str, Any]]], int],
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
    build_supplement_closed_loop_instruction_fn: Callable[..., str],
    build_requirement_semantics_payload_fn: Callable[[Any], dict[str, Any]],
    stream_postprocess_cases_fn: Callable[..., Any],
    judge_cases_fn: Callable[..., Any],
    repair_cases_fn: Callable[..., Any],
    training_gate_fn: Callable[..., Any],
    apply_existing_execution_group_ordering_fn: Callable[..., Any],
) -> JsonReviewPostprocessResult:
    postprocess_result = _try_stream_postprocess(
        result=result,
        db=db,
        client=client,
        requirement=requirement,
        base_prompt=base_prompt,
        kb_context=kb_context,
        expected_count=expected_count,
        start_id=start_id,
        resolved_current_biz=resolved_current_biz,
        multi_pass=multi_pass,
        generation_mode=generation_mode,
        feedback_control_state=feedback_control_state,
        prompt_context=prompt_context,
        stage_counts=stage_counts,
        coverage_check_payload=coverage_check_payload,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
        count_unique_test_cases_fn=count_unique_test_cases_fn,
        infer_case_kind_fn=infer_case_kind_fn,
        build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction_fn,
        build_requirement_semantics_payload_fn=build_requirement_semantics_payload_fn,
        stream_postprocess_cases_fn=stream_postprocess_cases_fn,
    )
    if postprocess_result.stream_postprocess_applied:
        return postprocess_result

    if isinstance(result, list):
        fallback_review = run_json_fallback_review(
            result=result,
            prompt_context=prompt_context,
            requirement=requirement,
            feedback_control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
            stage_logs=stage_logs,
            expected_count=expected_count,
            start_id=start_id,
            judge_cases_fn=judge_cases_fn,
            repair_cases_fn=repair_cases_fn,
            training_gate_fn=training_gate_fn,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
            apply_existing_execution_group_ordering_fn=apply_existing_execution_group_ordering_fn,
        )
        return JsonReviewPostprocessResult(
            result=fallback_review.result,
            stage_counts=dict(stage_counts or {}),
            coverage_check_payload=fallback_review.coverage_check_payload,
            convergence_payload=fallback_review.convergence_payload,
            generation_summary_payload=fallback_review.generation_summary_payload,
            review_decision_summary_payload=fallback_review.review_decision_summary_payload,
            review_decision_table_payload=fallback_review.review_decision_table_payload,
            judge_summary_payload=fallback_review.judge_summary_payload,
            judge_decision_table_payload=fallback_review.judge_decision_table_payload,
            candidate_cases_before_judge=fallback_review.candidate_cases_before_judge,
            candidate_total_before_judge=fallback_review.candidate_total_before_judge,
            final_cases_after_judge=fallback_review.final_cases_after_judge,
            final_case_count=fallback_review.final_case_count,
            empty_result_guard_triggered=fallback_review.empty_result_guard_triggered,
            empty_result_stage=fallback_review.empty_result_stage,
            stream_postprocess_applied=False,
        )

    return postprocess_result


def _try_stream_postprocess(
    *,
    result: Any,
    db: Any,
    client: Any,
    requirement: str,
    base_prompt: str,
    kb_context: str,
    expected_count: int,
    start_id: int,
    resolved_current_biz: str,
    multi_pass: bool,
    generation_mode: str,
    feedback_control_state: dict[str, Any],
    prompt_context: dict[str, Any],
    stage_counts: dict[str, Any],
    coverage_check_payload: dict[str, Any] | None,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    count_unique_test_cases_fn: Callable[[list[dict[str, Any]]], int],
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
    build_supplement_closed_loop_instruction_fn: Callable[..., str],
    build_requirement_semantics_payload_fn: Callable[[Any], dict[str, Any]],
    stream_postprocess_cases_fn: Callable[..., Any],
) -> JsonReviewPostprocessResult:
    if not (isinstance(result, list) and isinstance(db, Session)):
        return _empty_review_postprocess_result(
            result=result,
            stage_counts=stage_counts,
            coverage_check_payload=coverage_check_payload,
        )

    try:
        postprocess_payload = drain_generator_return(
            stream_postprocess_cases_fn(
                client=client,
                requirement=requirement,
                base_prompt=base_prompt,
                kb_context=kb_context,
                full_content=json.dumps(result, ensure_ascii=False),
                expected_count=expected_count,
                append=False,
                existing_cases=[],
                existing_unique_count=0,
                start_id=start_id,
                db=db,
                clean_and_parse_json_fn=clean_and_parse_json_fn,
                normalize_json_structure_fn=normalize_json_structure_fn,
                deduplicate_test_cases_fn=deduplicate_test_cases_fn,
                reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
                count_unique_test_cases_fn=count_unique_test_cases_fn,
                infer_case_kind_fn=infer_case_kind_fn,
                build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction_fn,
                current_biz_key=resolved_current_biz,
                multi_pass=multi_pass,
                generation_mode=generation_mode,
                feedback_control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
                requirement_semantics_context=build_requirement_semantics_payload_fn(prompt_context),
            )
        )
    except Exception as exc:
        print(f"JSON stream postprocess fallback: {type(exc).__name__}: {exc}")
        return _empty_review_postprocess_result(
            result=result,
            stage_counts=stage_counts,
            coverage_check_payload=coverage_check_payload,
        )

    if not (isinstance(postprocess_payload, dict) and isinstance(postprocess_payload.get("cases"), list)):
        return _empty_review_postprocess_result(
            result=result,
            stage_counts=stage_counts,
            coverage_check_payload=coverage_check_payload,
        )

    final_result = [item for item in (postprocess_payload.get("cases") or []) if isinstance(item, dict)]
    convergence_payload = dict(postprocess_payload.get("convergence_debug") or {})
    review_decision_summary_payload = dict(postprocess_payload.get("review_decision_summary") or {})
    final_cases_after_judge = [item for item in final_result if isinstance(item, dict)]
    final_case_count = int(len(final_cases_after_judge))
    candidate_total_before_judge = int(
        convergence_payload.get("candidate_count_before_review")
        or review_decision_summary_payload.get("candidate_total")
        or final_case_count
    )
    return JsonReviewPostprocessResult(
        result=final_result,
        stage_counts=dict(postprocess_payload.get("stage_counts") or stage_counts or {}),
        coverage_check_payload=dict(postprocess_payload.get("coverage") or coverage_check_payload or {}),
        convergence_payload=convergence_payload,
        generation_summary_payload=dict(postprocess_payload.get("generation_summary") or {}),
        review_decision_summary_payload=review_decision_summary_payload,
        review_decision_table_payload=[
            dict(item) for item in (postprocess_payload.get("review_decision_table") or []) if isinstance(item, dict)
        ],
        judge_summary_payload=dict(postprocess_payload.get("judge_summary") or {}),
        judge_decision_table_payload=[
            dict(item) for item in (postprocess_payload.get("judge_decision_table") or []) if isinstance(item, dict)
        ],
        candidate_cases_before_judge=[],
        candidate_total_before_judge=candidate_total_before_judge,
        final_cases_after_judge=final_cases_after_judge,
        final_case_count=final_case_count,
        empty_result_guard_triggered=False,
        empty_result_stage="",
        stream_postprocess_applied=True,
    )


def _empty_review_postprocess_result(
    *,
    result: Any,
    stage_counts: dict[str, Any],
    coverage_check_payload: dict[str, Any] | None,
) -> JsonReviewPostprocessResult:
    return JsonReviewPostprocessResult(
        result=result,
        stage_counts=dict(stage_counts or {}),
        coverage_check_payload=coverage_check_payload,
        convergence_payload={},
        generation_summary_payload={},
        review_decision_summary_payload={},
        review_decision_table_payload=[],
        judge_summary_payload={},
        judge_decision_table_payload=[],
        candidate_cases_before_judge=[],
        candidate_total_before_judge=0,
        final_cases_after_judge=[],
        final_case_count=0,
        empty_result_guard_triggered=False,
        empty_result_stage="",
        stream_postprocess_applied=False,
    )


__all__ = [
    "JsonReviewPostprocessResult",
    "drain_generator_return",
    "run_json_review_postprocess",
]
