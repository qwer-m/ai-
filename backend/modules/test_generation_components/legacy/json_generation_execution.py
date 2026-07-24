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
    start_id: int,
    normalized_generation_mode: str,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    finalize_generated_cases_fn: Callable[..., Any],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
) -> JsonGenerationExecutionResult:
    # JSON 入口只负责生成完整全局候选；Gap 与统一 Review 由共享 postprocess 主链执行。
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
            "mode": str(normalized_generation_mode or "global_candidates"),
            "biz_keys": [resolved_current_biz],
            "current_biz_key": resolved_current_biz,
        },
        {
            "kind": "generation_stage",
            "stage": "primary",
            "case_count": len(result) if isinstance(result, list) else 0,
        },
        {"kind": "generation_stage", "stage": "gap", "case_count": 0},
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
