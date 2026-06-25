from __future__ import annotations

from typing import Any

from .execution_plan_validator import (
    ExecutionPlanValidationPolicy,
    materialize_final_case_state_fields,
    validate_execution_plan,
)


_VALID_GATE_MODES = {"shadow", "enforce"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return int(default)


def _gate_mode(settings: Any = None) -> str:
    value = str(getattr(settings, "EXECUTION_PLAN_GATE_MODE", "enforce") or "enforce").strip().lower()
    return value if value in _VALID_GATE_MODES else "enforce"


def _policy(settings: Any = None) -> ExecutionPlanValidationPolicy:
    return ExecutionPlanValidationPolicy(
        min_main_smoke_count=int(getattr(settings, "EXECUTION_PLAN_MIN_MAIN_SMOKE_COUNT", 6)),
        min_p0_count=int(getattr(settings, "EXECUTION_PLAN_MIN_P0_COUNT", 6)),
        min_state_field_coverage=float(getattr(settings, "EXECUTION_PLAN_MIN_STATE_FIELD_COVERAGE", 0.8)),
        max_workflow_id_missing_rate=float(getattr(settings, "EXECUTION_PLAN_MAX_WORKFLOW_ID_MISSING_RATE", 0.2)),
        reject_untrusted_blueprint_source=bool(
            getattr(settings, "EXECUTION_PLAN_REJECT_CANDIDATE_DERIVED_BLUEPRINT", True)
        ),
        allow_candidate_blueprint_without_contract=bool(
            getattr(settings, "EXECUTION_PLAN_ALLOW_CANDIDATE_BLUEPRINT_WITHOUT_CONTRACT", True)
        ),
    )


def _setting_int(settings: Any, name: str, default: int) -> int:
    return _to_int(getattr(settings, name, default), default)


def is_candidate_insufficient_underfill(generation_summary: dict[str, Any] | None) -> bool:
    """Treat source-model/candidate scarcity as an advisory quantity shortfall."""
    generation = dict(generation_summary or {})
    final_count = _to_int(generation.get("final_count") or generation.get("final_case_count_after_backfill"))
    min_acceptable_final = _to_int(generation.get("min_acceptable_final"))
    if final_count <= 0 or min_acceptable_final <= 0 or final_count >= min_acceptable_final:
        return False
    reason = str(generation.get("underfill_reason") or "").strip()
    root_cause = str(generation.get("underfill_root_cause") or "").strip()
    return reason == "valid_candidate_insufficient" or root_cause == "candidate_insufficient"


def set_truthy_diagnostic_flag(payload: dict[str, Any], key: str, enabled: bool) -> None:
    """Keep diagnostic booleans sparse so false anomaly flags do not read as findings."""
    if enabled:
        payload[key] = True
    else:
        payload.pop(key, None)


def build_case_quality_metrics(
    *,
    final_count: int,
    min_acceptable_final: int,
    judge_rejected_count: int,
    final_duplicate_count: int,
    final_misordered_count: int,
    reasoning_leak_count: int,
    role_mismatch_count: int,
    quantity_shortfall_advisory: bool = False,
    existing_metrics: dict[str, Any] | None = None,
    quality_score: int | None = None,
    quality_score_grade: str | None = None,
    raw_judge_rejected_count: int | None = None,
    semantic_duplicate_reject_count: int | None = None,
    filtered_semantic_duplicate_reject_count: int | None = None,
) -> dict[str, Any]:
    metrics = dict(existing_metrics or {})
    metrics.update(
        {
            "final_count": int(final_count),
            "min_acceptable_final": int(min_acceptable_final),
            "judge_rejected_count": int(judge_rejected_count),
            "final_scenario_duplicate_case_count": int(final_duplicate_count),
            "final_flow_misordered_count": int(final_misordered_count),
            "reasoning_leak_count": int(reasoning_leak_count),
            "role_mismatch_count": int(role_mismatch_count),
        }
    )
    if quality_score is not None:
        metrics["quality_score"] = int(quality_score)
    if quality_score_grade is not None:
        metrics["quality_score_grade"] = str(quality_score_grade)
    if raw_judge_rejected_count is not None:
        metrics["raw_judge_rejected_count"] = int(raw_judge_rejected_count)
    if semantic_duplicate_reject_count is not None:
        metrics["semantic_duplicate_reject_count"] = int(semantic_duplicate_reject_count)
    if filtered_semantic_duplicate_reject_count is not None:
        metrics["filtered_semantic_duplicate_reject_count"] = int(filtered_semantic_duplicate_reject_count)
    set_truthy_diagnostic_flag(metrics, "quantity_shortfall_advisory", quantity_shortfall_advisory)
    return metrics


def build_case_quality_failures(
    *,
    existing_failures: list[str] | None = None,
    final_count: int,
    min_acceptable_final: int,
    judge_rejected_count: int,
    final_duplicate_count: int = 0,
    final_misordered_count: int = 0,
    reasoning_leak_count: int = 0,
    role_mismatch_count: int = 0,
    quantity_shortfall_advisory: bool = False,
    quality_score: int | None = None,
    quality_score_grade: str | None = None,
    max_judge_rejected: int = 20,
    max_final_duplicates: int | None = None,
    max_final_misordered: int | None = None,
    max_role_mismatch: int = 5,
) -> list[str]:
    failures = [str(item).strip() for item in (existing_failures or []) if str(item).strip()]

    def add(name: str) -> None:
        if name not in failures:
            failures.append(name)

    if min_acceptable_final > 0 and final_count < min_acceptable_final and not quantity_shortfall_advisory:
        add("final_count_below_min_acceptable")
    if quality_score is not None:
        grade = str(quality_score_grade or "").strip().lower()
        if int(quality_score) <= 0 or grade == "critical":
            add("quality_score_critical")
    if judge_rejected_count > max_judge_rejected:
        add("judge_rejected_above_threshold")
    if max_final_duplicates is not None and final_duplicate_count > max_final_duplicates:
        add("final_scenario_duplicates_above_threshold")
    if max_final_misordered is not None and final_misordered_count > max_final_misordered:
        add("final_flow_misordered_above_threshold")
    if reasoning_leak_count > 0:
        add("reasoning_leakage_detected")
    if role_mismatch_count > max_role_mismatch:
        add("role_mismatch_above_threshold")
    return failures


def summarize_persistence_case_quality_gate(
    structure_quality_gate: dict[str, Any] | None = None,
    *,
    generation_summary: dict[str, Any] | None = None,
    review_decision_summary: dict[str, Any] | None = None,
    judge_summary: dict[str, Any] | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    """Merge final-batch quality signals into the pre-persistence gate payload."""
    quality = dict(structure_quality_gate or {})
    failed_checks = [
        str(item).strip()
        for item in (quality.get("failed_checks") or [])
        if str(item).strip()
    ]
    generation = dict(generation_summary or {})
    review = dict(review_decision_summary or {})
    judge = dict(judge_summary or {})

    final_count = _to_int(
        generation.get("final_count")
        or generation.get("final_case_count_after_backfill")
        or review.get("final_count")
    )
    min_acceptable_final = _to_int(generation.get("min_acceptable_final"))
    rejected_count = _to_int(judge.get("rejected_out_count") or judge.get("reject_count"))
    final_duplicate_count = _to_int(review.get("final_scenario_duplicate_case_count"))
    final_misordered_count = _to_int(review.get("final_flow_misordered_count"))
    reasoning_leak_count = _to_int(review.get("final_reasoning_leakage_case_count"))
    role_mismatch_count = _to_int(review.get("final_role_mismatch_count"))

    max_judge_rejected = _setting_int(settings, "CASE_QUALITY_MAX_JUDGE_REJECTED", 20)
    max_final_duplicates = _setting_int(settings, "CASE_QUALITY_MAX_FINAL_SCENARIO_DUPLICATES", 0)
    max_final_misordered = _setting_int(settings, "CASE_QUALITY_MAX_FINAL_FLOW_MISORDERED", 0)
    max_role_mismatch = _setting_int(settings, "CASE_QUALITY_MAX_ROLE_MISMATCH", 5)

    quantity_shortfall_advisory = is_candidate_insufficient_underfill(generation)
    failed_checks = build_case_quality_failures(
        existing_failures=failed_checks,
        final_count=final_count,
        min_acceptable_final=min_acceptable_final,
        judge_rejected_count=rejected_count,
        final_duplicate_count=final_duplicate_count,
        final_misordered_count=final_misordered_count,
        reasoning_leak_count=reasoning_leak_count,
        role_mismatch_count=role_mismatch_count,
        quantity_shortfall_advisory=quantity_shortfall_advisory,
        max_judge_rejected=max_judge_rejected,
        max_final_duplicates=max_final_duplicates,
        max_final_misordered=max_final_misordered,
        max_role_mismatch=max_role_mismatch,
    )

    metrics = build_case_quality_metrics(
        final_count=final_count,
        min_acceptable_final=min_acceptable_final,
        judge_rejected_count=rejected_count,
        final_duplicate_count=final_duplicate_count,
        final_misordered_count=final_misordered_count,
        reasoning_leak_count=reasoning_leak_count,
        role_mismatch_count=role_mismatch_count,
        quantity_shortfall_advisory=quantity_shortfall_advisory,
        existing_metrics=dict(quality.get("metrics") or {}),
    )
    quality["failed_checks"] = failed_checks
    quality["passed"] = not bool(failed_checks)
    quality["metrics"] = metrics
    return quality


def evaluate_persistence_gate(
    final_cases: Any,
    *,
    workflow_blueprints: list[dict[str, Any]] | None = None,
    execution_plan: dict[str, Any] | None = None,
    generation_mode: str = "",
    quality_gate: dict[str, Any] | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    """Apply one persistence decision surface to stream and JSON generation."""
    cases = materialize_final_case_state_fields(final_cases)
    execution_validation = validate_execution_plan(
        cases,
        workflow_blueprints=workflow_blueprints,
        execution_plan=execution_plan,
        generation_mode=generation_mode,
        policy=_policy(settings),
    )
    quality = dict(quality_gate or {})
    quality_failures = [str(item) for item in (quality.get("failed_checks") or []) if str(item).strip()]
    empty_result = not bool(cases)
    gate_mode = _gate_mode(settings)
    execution_would_block = not bool(execution_validation.get("passed"))
    quality_would_block = bool(quality_failures or empty_result)
    blocked = bool(quality_would_block or (gate_mode == "enforce" and execution_would_block))
    failure_code = ""
    if empty_result:
        failure_code = "EMPTY_GENERATED_RESULT"
    elif quality_failures:
        failure_code = "LOW_QUALITY_GENERATED_CASES"
    elif blocked:
        failure_code = "execution_plan_failed"
    return {
        "kind": "persistence_gate",
        "passed": not blocked,
        "gate_mode": gate_mode,
        "blocked": blocked,
        "failure_code": failure_code,
        "quality_would_block": quality_would_block,
        "execution_plan_would_block": execution_would_block,
        "quality_gate": quality,
        "execution_plan_validation": execution_validation,
        "cases": cases if isinstance(cases, list) else [],
    }


def build_persistence_gate_diagnostic(gate_result: dict[str, Any]) -> dict[str, Any]:
    """Remove final-case payloads before writing the gate decision to diagnostics."""
    payload = {key: value for key, value in dict(gate_result or {}).items() if key != "cases"}
    execution_validation = dict(payload.get("execution_plan_validation") or {})
    execution_validation.pop("cases", None)
    payload["execution_plan_validation"] = execution_validation
    return payload


__all__ = [
    "build_persistence_gate_diagnostic",
    "build_case_quality_failures",
    "build_case_quality_metrics",
    "evaluate_persistence_gate",
    "is_candidate_insufficient_underfill",
    "set_truthy_diagnostic_flag",
    "summarize_persistence_case_quality_gate",
]
