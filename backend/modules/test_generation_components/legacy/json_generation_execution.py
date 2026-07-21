from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class JsonGenerationExecutionResult:
    result: Any
    stage_logs: list[dict[str, Any]]
    coverage_check_payload: dict[str, Any] | None
    raw_response_payload: Any


def run_json_generation_execution(
    *,
    client: Any,
    requirement: str,
    db: Any,
    system_prompt: str,
    prompt_context: dict[str, Any],
    resolved_current_biz: str,
    expected_count: int,
    batch_size: int,
    start_id: int,
    normalized_generation_mode: str,
    multi_pass: bool,
    generation_mode: str,
    strategy_plan: dict[str, Any],
    doc_type: str,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    run_multi_pass_generation_fn: Callable[..., dict[str, Any]],
    finalize_generated_cases_fn: Callable[..., Any],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    build_closed_loop_base_prompt_fn: Callable[..., str],
) -> JsonGenerationExecutionResult:
    use_pipeline = bool(multi_pass) or normalized_generation_mode in {
        "single_pass",
        "multi_pass",
        "biz_key_multi_pass",
    }
    if use_pipeline:
        multi_pass_result = run_multi_pass_generation_fn(
            client=client,
            requirement=requirement,
            db=db,
            base_prompt=system_prompt,
            requirement_context=prompt_context.get("requirement_context") or requirement,
            current_biz_key=resolved_current_biz,
            expected_count=int(expected_count or batch_size or 1),
            start_id=start_id,
            clean_and_parse_json_fn=clean_and_parse_json_fn,
            normalize_json_structure_fn=normalize_json_structure_fn,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
            multi_pass=bool(multi_pass),
            generation_mode=generation_mode,
            prompt_context=prompt_context,
            build_base_prompt_fn=lambda req_ctx, req_sem_ctx, tc_ctx, sup_ctx, ctl_ctx, biz_key: build_closed_loop_base_prompt_fn(
                strategy_plan,
                requirement_context=req_ctx,
                requirement_semantics_context=req_sem_ctx,
                testcase_context=tc_ctx,
                supplement_context=sup_ctx,
                control_context=ctl_ctx,
                current_biz_key=biz_key,
                doc_type=doc_type,
                pretty_json=False,
            ),
        )
        return JsonGenerationExecutionResult(
            result=multi_pass_result.get("final_cases") or [],
            stage_logs=list(multi_pass_result.get("stage_logs") or []),
            coverage_check_payload=dict(multi_pass_result.get("coverage") or {}),
            raw_response_payload=multi_pass_result.get("raw") or {},
        )

    response = client.generate_response(
        requirement,
        system_prompt,
        db=db,
        task_type="generation",
        response_mode="json",
    )
    result = finalize_generated_cases_fn(
        response,
        start_id=start_id,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop_fn,
    )
    stage_logs = [
        {
            "kind": "generation_mode",
            "mode": "single_pass",
            "biz_keys": [resolved_current_biz],
            "current_biz_key": resolved_current_biz,
        },
        {
            "kind": "generation_stage",
            "stage": "primary",
            "case_count": len(result) if isinstance(result, list) else 0,
        },
        {"kind": "generation_stage", "stage": "gap", "case_count": 0},
        {
            "kind": "generation_stage",
            "stage": "review",
            "case_count": len(result) if isinstance(result, list) else 0,
        },
    ]
    coverage_check_payload = None
    if isinstance(result, list):
        coverage_check_payload = {
            "kind": "coverage_check",
            **analyze_coverage_fn(prompt_context.get("requirement_context") or requirement, result),
        }
    return JsonGenerationExecutionResult(
        result=result,
        stage_logs=stage_logs,
        coverage_check_payload=coverage_check_payload,
        raw_response_payload=response,
    )


__all__ = [
    "JsonGenerationExecutionResult",
    "run_json_generation_execution",
]
