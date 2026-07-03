from __future__ import annotations

from typing import Any, Callable


def build_stream_postprocess_result_payload(
    *,
    cases: Any,
    stage_counts: Any,
    coverage: Any,
    convergence_debug: Any,
    generation_summary: Any,
    review_decision_summary: Any,
    review_decision_table: Any,
    judge_summary: Any,
    judge_decision_table: Any,
    feedback_control_debug_builder_fn: Callable[..., Any],
    control_state: Any,
    generation_coverage_mode: Any = None,
    generation_target_case_range: Any = None,
    fact_profile: Any = None,
    project_profile: Any = None,
    manual_quality_profile: Any = None,
    timing_events: Any = None,
) -> dict[str, Any]:
    return {
        "cases": cases,
        "stage_counts": stage_counts,
        "coverage": coverage,
        "convergence_debug": convergence_debug,
        "generation_summary": generation_summary,
        "review_decision_summary": review_decision_summary,
        "review_decision_table": review_decision_table,
        "judge_summary": judge_summary,
        "judge_decision_table": judge_decision_table,
        "timing_events": timing_events if isinstance(timing_events, list) else [],
        "feedback_control_debug": feedback_control_debug_builder_fn(
            control_state=control_state,
            generation_coverage_mode=str(generation_coverage_mode or "core_smoke"),
            generation_target_case_range=generation_target_case_range,
            fact_profile=fact_profile,
            project_profile=project_profile,
            manual_quality_profile=manual_quality_profile,
        ),
    }
