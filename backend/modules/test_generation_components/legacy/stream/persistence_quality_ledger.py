from collections.abc import Callable
from typing import Any

from .persistence_judge_rows import (
    cluster_judge_reject_reasons as _cluster_judge_reject_reasons,
    judge_status_key as _judge_status_key,
)
from .persistence_manual_delivery import build_manual_delivery_metrics as _build_manual_delivery_metrics


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp_score(value: float) -> int:
    return int(round(max(0.0, min(100.0, float(value)))))


def _quality_assessment_from_score_grade(grade: Any) -> str:
    normalized = str(grade or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return normalized
    if normalized == "critical":
        return "low"
    return ""


def _execution_plan_closes_required_flow(summary: dict[str, Any]) -> bool:
    execution_plan = summary.get("execution_plan") if isinstance(summary.get("execution_plan"), dict) else {}
    final_plan = (
        summary.get("final_execution_orchestration_plan")
        if isinstance(summary.get("final_execution_orchestration_plan"), dict)
        else {}
    )
    independent_suite_executable = bool(
        summary.get("independent_suite_executable")
        or execution_plan.get("independent_suite_executable")
    )
    if independent_suite_executable:
        return True
    linear_executable = bool(summary.get("linear_executable") or execution_plan.get("linear_executable"))
    main_chain_case_count = _to_int(
        summary.get("main_chain_case_count")
        or execution_plan.get("main_chain_case_count")
        or final_plan.get("main_chain_case_count")
    )
    broken_dependency_count = _to_int(
        summary.get("broken_dependency_count") or execution_plan.get("broken_dependency_count")
    )
    state_conflict_count = _to_int(summary.get("state_conflict_count") or execution_plan.get("state_conflict_count"))
    incomplete_reason = str(execution_plan.get("main_chain_incomplete_reason") or "").strip()
    return bool(
        linear_executable
        and main_chain_case_count > 0
        and broken_dependency_count <= 0
        and state_conflict_count <= 0
        and not incomplete_reason
    )


def _build_initial_quality_score(
    *,
    coverage_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    feedback_control_debug_payload: dict[str, Any],
    context_result: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]] | None = None,
    execution_plan_validation_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score the generated batch from persisted pipeline diagnostics.

    This is intentionally deterministic and evidence-based: it uses only
    coverage, review, judge, funnel and context signals produced by the run.
    It is not a model opinion and should be displayed with its source.
    """
    missing_types = coverage_payload.get("missing_types") if isinstance(coverage_payload.get("missing_types"), dict) else {}
    coverage_rate = max(0.0, min(1.0, _to_float(coverage_payload.get("coverage_rate"), 0.0)))
    total_rules = _to_int(coverage_payload.get("total_rules"))
    missing_rules_count = len(coverage_payload.get("missing_rules") or [])
    missing_boundary_count = len(missing_types.get("boundary") or [])
    missing_exception_count = len(missing_types.get("exception") or [])

    final_count = _to_int(generation_summary_payload.get("final_count") or convergence_payload.get("final_count"))
    low_quality_dropped_count = _to_int(convergence_payload.get("low_quality_dropped_count"))
    semantic_dedup_dropped_count = _to_int(convergence_payload.get("semantic_dedup_dropped_count"))
    total_dedup_drop_count = _to_int(convergence_payload.get("total_dedup_drop_count"))
    candidate_total = _to_int(review_decision_summary_payload.get("candidate_total"))
    retained_total = _to_int(review_decision_summary_payload.get("retained_total"))

    def summary_metric(final_key: str, legacy_key: str) -> int:
        if final_key in review_decision_summary_payload:
            return _to_int(review_decision_summary_payload.get(final_key))
        return _to_int(review_decision_summary_payload.get(legacy_key))

    raw_flow_missing_count = summary_metric("final_flow_missing_stage_count", "flow_missing_stage_count")
    flow_missing_advisory_count = 0
    flow_missing_count = raw_flow_missing_count
    if raw_flow_missing_count > 0 and _execution_plan_closes_required_flow(review_decision_summary_payload):
        flow_missing_advisory_count = raw_flow_missing_count
        flow_missing_count = 0
    flow_misordered_count = summary_metric("final_flow_misordered_count", "flow_misordered_count")
    scenario_duplicate_cluster_count = summary_metric(
        "final_scenario_duplicate_cluster_count",
        "scenario_duplicate_cluster_count",
    )
    scenario_duplicate_case_count = summary_metric(
        "final_scenario_duplicate_case_count",
        "scenario_duplicate_case_count",
    )
    final_semantic_diagnostics_available = bool(
        review_decision_summary_payload.get("final_semantic_diagnostics_available")
    )
    final_semantic_duplicate_cluster_count = _to_int(
        review_decision_summary_payload.get("final_semantic_duplicate_cluster_count")
    )
    final_semantic_duplicate_case_count = _to_int(
        review_decision_summary_payload.get("final_semantic_duplicate_case_count")
    )
    final_semantic_dedup_dropped_count = _to_int(
        review_decision_summary_payload.get("final_semantic_dedup_dropped_count")
        or convergence_payload.get("final_semantic_dedup_dropped_count")
    )
    final_reasoning_leakage_case_count = _to_int(
        review_decision_summary_payload.get("final_reasoning_leakage_case_count")
    )
    fact_profile_forbidden_count = _to_int(review_decision_summary_payload.get("fact_profile_forbidden_count"))
    fact_violation_count = _to_int(
        judge_summary_payload.get("fact_violation_count")
        or judge_summary_payload.get("fact_conflict_rejected_count")
    )
    fact_pending_count = _to_int(review_decision_summary_payload.get("fact_profile_pending_count"))
    semantic_pressure_count = max(semantic_dedup_dropped_count, total_dedup_drop_count)
    semantic_penalty_count = semantic_pressure_count
    if (
        semantic_pressure_count > 0
        and candidate_total > 0
        and flow_misordered_count <= 0
        and scenario_duplicate_case_count <= 0
        and scenario_duplicate_cluster_count <= 0
    ):
        tolerated_semantic_prune = max(3, int(round(float(candidate_total) * 0.08)))
        semantic_penalty_count = max(0, semantic_pressure_count - tolerated_semantic_prune)

    execution_validation = dict(execution_plan_validation_payload or {})
    execution_metrics = (
        dict(execution_validation.get("metrics") or {})
        if isinstance(execution_validation.get("metrics"), dict)
        else {}
    )
    semantic_conflict_count = _to_int(
        execution_metrics.get("semantic_conflict_count")
        or len(execution_validation.get("semantic_conflicts") or [])
    )
    semantic_warning_count = _to_int(
        execution_metrics.get("semantic_warning_count")
        or len(execution_validation.get("semantic_warnings") or [])
    )

    judge_total = _to_int(
        judge_summary_payload.get("total")
        or judge_summary_payload.get("input_count")
        or (
            _to_int(judge_summary_payload.get("pass_count") or judge_summary_payload.get("confirmed_pass_out_count"))
            + _to_int(judge_summary_payload.get("repairable_count"))
            + _to_int(judge_summary_payload.get("reject_count") or judge_summary_payload.get("rejected_out_count"))
            + _to_int(judge_summary_payload.get("pending_count") or judge_summary_payload.get("pending_out_count"))
        )
    )
    rejected_count = _to_int(judge_summary_payload.get("reject_count") or judge_summary_payload.get("rejected_out_count"))
    pending_count = _to_int(judge_summary_payload.get("pending_count") or judge_summary_payload.get("pending_out_count"))
    raw_rejected_count = int(rejected_count)
    raw_pending_count = int(pending_count)
    semantic_duplicate_rejected_count = 0
    filtered_semantic_duplicate_reject_count = 0
    filtered_pending_candidate_count = 0
    for row in judge_decision_table_payload or []:
        if not isinstance(row, dict):
            continue
        status = _judge_status_key(row)
        signals = row.get("signals") if isinstance(row.get("signals"), dict) else {}
        reject_reason = str(row.get("reject_reason") or "").strip().lower()
        if status == "REJECT" and (
            reject_reason.startswith("semantic_duplicate")
            or bool(signals.get("is_semantic_duplicate"))
            or bool(row.get("is_semantic_duplicate"))
        ):
            semantic_duplicate_rejected_count += 1
        elif status == "PENDING":
            filtered_pending_candidate_count += 1
    if (
        final_reasoning_leakage_case_count <= 0
        and fact_pending_count <= 0
        and filtered_pending_candidate_count > 0
    ):
        pending_count = max(0, pending_count - filtered_pending_candidate_count)
    min_acceptable_final = _to_int(generation_summary_payload.get("min_acceptable_final"))
    final_count_sufficient = min_acceptable_final <= 0 or final_count >= min_acceptable_final
    final_structure_clean = (
        final_count_sufficient
        and flow_missing_count <= 0
        and flow_misordered_count <= 0
        and scenario_duplicate_case_count <= 0
        and scenario_duplicate_cluster_count <= 0
        and final_reasoning_leakage_case_count <= 0
    )
    if final_structure_clean and semantic_duplicate_rejected_count > 0:
        filtered_semantic_duplicate_reject_count = min(
            int(rejected_count),
            int(semantic_duplicate_rejected_count),
        )
        rejected_count = max(0, int(rejected_count) - filtered_semantic_duplicate_reject_count)
    raw_repairable_count = _to_int(
        judge_summary_payload.get("raw_repairable_count")
        if "raw_repairable_count" in judge_summary_payload
        else (
            judge_summary_payload.get("repairable_count")
            or judge_summary_payload.get("repaired_pass_out_count")
        )
    )
    repaired_pass_count = _to_int(judge_summary_payload.get("repaired_pass_out_count"))
    unrepaired_repairable_count = _to_int(judge_summary_payload.get("unrepaired_repairable_count"))
    repairable_count = (
        _to_int(judge_summary_payload.get("remaining_repairable_count"))
        if "remaining_repairable_count" in judge_summary_payload
        else raw_repairable_count
    )

    context_debug = dict((context_result or {}).get("context_debug") or {})
    realtime_rag_used = bool(context_debug.get("realtime_rag_used"))
    current_document_used = bool(context_debug.get("current_document_used"))
    control_state_applied = bool(feedback_control_debug_payload.get("control_state_applied"))
    manual_delivery_metrics = _build_manual_delivery_metrics(
        feedback_control_debug_payload=feedback_control_debug_payload,
        generation_summary_payload=generation_summary_payload,
    )

    deductions: list[dict[str, Any]] = []

    def add_deduction(key: str, label: str, count: int | float, points: float) -> None:
        if points <= 0:
            return
        deductions.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "points": round(float(points), 2),
            }
        )

    add_deduction("coverage_gap", "规则覆盖缺口", round(1.0 - coverage_rate, 4), (1.0 - coverage_rate) * 35)
    add_deduction("missing_rules", "未覆盖阻断规则", missing_rules_count, min(30, missing_rules_count * 6))
    add_deduction("missing_boundary", "缺少边界覆盖", missing_boundary_count, min(12, missing_boundary_count * 2))
    add_deduction("missing_exception", "缺少异常覆盖", missing_exception_count, min(12, missing_exception_count * 2))
    add_deduction("flow_missing", "流程缺失", flow_missing_count, min(25, flow_missing_count * 5))
    add_deduction("flow_misordered", "流程顺序异常", flow_misordered_count, min(25, flow_misordered_count * 3))
    add_deduction("scenario_duplicates", "重复意图", scenario_duplicate_case_count or scenario_duplicate_cluster_count, min(25, scenario_duplicate_case_count * 0.75 + scenario_duplicate_cluster_count * 1.5))
    add_deduction(
        "final_semantic_duplicates",
        "最终集合未解决语义重复",
        final_semantic_duplicate_case_count,
        min(30, final_semantic_duplicate_case_count * 3),
    )
    add_deduction(
        "final_semantic_dedup",
        "最终集合语义去重压力",
        final_semantic_dedup_dropped_count,
        min(10, final_semantic_dedup_dropped_count * 0.5),
    )
    add_deduction("judge_rejected", "判定拒绝", rejected_count, min(30, rejected_count * 3))
    add_deduction("judge_pending", "待确认逻辑", pending_count, min(15, pending_count * 1.5))
    add_deduction("judge_repairable", "最终残留可修复问题", repairable_count, min(8, repairable_count * 1))
    add_deduction("fact_forbidden", "违反已确认事实", fact_violation_count, min(30, fact_violation_count * 6))
    add_deduction("fact_pending", "命中待确认事实", fact_pending_count, min(12, fact_pending_count * 1.5))
    add_deduction("low_quality_dropped", "低质量用例被过滤", low_quality_dropped_count, min(20, low_quality_dropped_count * 3))
    add_deduction("semantic_dedup", "语义去重压力", semantic_penalty_count, min(10, semantic_penalty_count * 0.5))
    add_deduction(
        "main_chain_semantic_conflict",
        "主链语义冲突",
        semantic_conflict_count,
        min(40, semantic_conflict_count * 20),
    )
    add_deduction(
        "main_chain_semantic_warning",
        "主链语义警告",
        semantic_warning_count,
        min(10, semantic_warning_count),
    )
    if manual_delivery_metrics.get("applied"):
        manual_delivery_metrics["scoring_mode"] = "advisory"
    if candidate_total > 0 and retained_total <= 0:
        add_deduction("empty_retained", "复核后无可用用例", 1, 40)
    if final_count <= 0:
        add_deduction("empty_final", "最终无可用用例", 1, 50)
    if not current_document_used:
        add_deduction("missing_current_document", "当前需求上下文未确认使用", 1, 8)
    if not realtime_rag_used and not control_state_applied:
        add_deduction("weak_context_control", "缺少RAG或样本池治理信号", 1, 5)

    total_deduction = sum(float(item["points"]) for item in deductions)
    score = _clamp_score(100 - total_deduction)
    score_basis = "coverage+review+judge+funnel+context"
    if execution_validation:
        score_basis = f"{score_basis}+execution_plan"
    if manual_delivery_metrics.get("applied"):
        score_basis = f"{score_basis}+manual_profile_advisory"
    if score >= 85:
        grade = "high"
    elif score >= 70:
        grade = "medium"
    elif score >= 50:
        grade = "low"
    else:
        grade = "critical"
    quality_score_inputs = {
        "coverage_rate": round(coverage_rate, 4),
        "total_rules": total_rules,
        "missing_rules_count": missing_rules_count,
        "final_count": final_count,
        "candidate_total": candidate_total,
        "retained_total": retained_total,
        "judge_total": judge_total,
        "rejected_count": rejected_count,
        "raw_rejected_count": raw_rejected_count,
        "pending_count": pending_count,
        "raw_pending_count": raw_pending_count,
        "semantic_duplicate_reject_count": semantic_duplicate_rejected_count,
        "filtered_semantic_duplicate_reject_count": filtered_semantic_duplicate_reject_count,
        "filtered_pending_candidate_count": filtered_pending_candidate_count,
        "repairable_count": repairable_count,
        "raw_repairable_count": raw_repairable_count,
        "repaired_pass_count": repaired_pass_count,
        "unrepaired_repairable_count": unrepaired_repairable_count,
        "fact_profile_forbidden_count": fact_profile_forbidden_count,
        "fact_violation_count": fact_violation_count,
        "raw_flow_missing_count": raw_flow_missing_count,
        "flow_missing_advisory_count": flow_missing_advisory_count,
        "flow_missing_count": flow_missing_count,
        "flow_misordered_count": flow_misordered_count,
        "scenario_duplicate_cluster_count": scenario_duplicate_cluster_count,
        "scenario_duplicate_case_count": scenario_duplicate_case_count,
        "final_semantic_diagnostics_available": bool(final_semantic_diagnostics_available),
        "final_semantic_duplicate_cluster_count": final_semantic_duplicate_cluster_count,
        "final_semantic_duplicate_case_count": final_semantic_duplicate_case_count,
        "final_semantic_dedup_dropped_count": final_semantic_dedup_dropped_count,
        "low_quality_dropped_count": low_quality_dropped_count,
        "semantic_dedup_dropped_count": semantic_dedup_dropped_count,
        "semantic_dedup_pressure_count": semantic_pressure_count,
        "semantic_dedup_penalty_count": semantic_penalty_count,
        "main_chain_semantic_conflict_count": semantic_conflict_count,
        "main_chain_semantic_warning_count": semantic_warning_count,
        "structure_metric_scope": (
            "final_cases"
            if "final_flow_misordered_count" in review_decision_summary_payload
            else "review_candidates"
        ),
        "manual_quality_profile_applied": bool(manual_delivery_metrics.get("applied")),
        "manual_quality_profile_version": str(manual_delivery_metrics.get("profile_version") or ""),
    }
    if manual_delivery_metrics.get("applied"):
        quality_score_inputs.update(
            {
                "manual_high_priority_ratio_shortfall": _to_float(
                    manual_delivery_metrics.get("high_priority_ratio_shortfall")
                ),
                "manual_display_ratio_excess": _to_float(manual_delivery_metrics.get("display_ratio_excess")),
                "manual_priority_distribution_drift": _to_float(
                    manual_delivery_metrics.get("priority_distribution_drift")
                ),
                "manual_module_distribution_drift": _to_float(
                    manual_delivery_metrics.get("module_distribution_drift")
                ),
            }
        )
    return {
        "initial_quality_score": score,
        "quality_score": score,
        "quality_score_grade": grade,
        "quality_score_source": "backend_diagnostic_v1",
        "quality_score_basis": score_basis,
        "quality_score_confidence": (
            "high"
            if (
                total_rules > 0
                and judge_total > 0
                and semantic_conflict_count <= 0
                and final_semantic_diagnostics_available
            )
            else "medium"
        ),
        "quality_score_deductions": deductions[:20],
        "quality_score_inputs": quality_score_inputs,
        "manual_delivery": manual_delivery_metrics,
    }


def _build_case_quality_gate_payload(
    *,
    score_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    judge_reject_clusters: dict[str, Any],
    build_case_quality_failures_fn: Callable[..., Any],
    build_case_quality_metrics_fn: Callable[..., Any],
    is_candidate_insufficient_underfill_fn: Callable[..., Any],
) -> dict[str, Any]:
    final_count = _to_int(generation_summary_payload.get("final_count"))
    min_acceptable_final = _to_int(generation_summary_payload.get("min_acceptable_final"))
    quality_score = _to_int(score_payload.get("quality_score"))
    grade = str(score_payload.get("quality_score_grade") or "").strip().lower()
    score_inputs = score_payload.get("quality_score_inputs") if isinstance(score_payload.get("quality_score_inputs"), dict) else {}
    raw_rejected_count = _to_int(
        judge_summary_payload.get("rejected_out_count")
        or judge_summary_payload.get("reject_count")
        or judge_reject_clusters.get("rejected_total")
    )
    rejected_count = _to_int(score_inputs.get("rejected_count"), raw_rejected_count)
    if bool(review_decision_summary_payload.get("final_semantic_diagnostics_available")):
        final_duplicate_count = _to_int(
            review_decision_summary_payload.get("final_semantic_duplicate_case_count")
        )
    else:
        final_duplicate_count = _to_int(
            review_decision_summary_payload.get("final_scenario_duplicate_case_count")
        )
    final_misordered_count = _to_int(review_decision_summary_payload.get("final_flow_misordered_count"))
    reasoning_leak_count = _to_int(review_decision_summary_payload.get("final_reasoning_leakage_case_count"))
    role_mismatch_count = _to_int(
        review_decision_summary_payload.get("final_role_mismatch_count")
        or (judge_reject_clusters.get("reason_clusters") or {}).get("role_mismatch")
    )
    semantic_duplicate_rejected_count = _to_int(
        score_inputs.get("semantic_duplicate_reject_count"),
        _to_int((judge_reject_clusters.get("reason_clusters") or {}).get("semantic_duplicate")),
    )
    filtered_semantic_duplicate_reject_count = _to_int(
        score_inputs.get("filtered_semantic_duplicate_reject_count")
    )
    quantity_shortfall_advisory = is_candidate_insufficient_underfill_fn(generation_summary_payload)
    failures = build_case_quality_failures_fn(
        final_count=final_count,
        min_acceptable_final=min_acceptable_final,
        judge_rejected_count=rejected_count,
        final_duplicate_count=final_duplicate_count,
        final_misordered_count=final_misordered_count,
        reasoning_leak_count=reasoning_leak_count,
        role_mismatch_count=role_mismatch_count,
        quantity_shortfall_advisory=quantity_shortfall_advisory,
        quality_score=quality_score,
        quality_score_grade=grade,
    )
    metrics = build_case_quality_metrics_fn(
        final_count=final_count,
        min_acceptable_final=min_acceptable_final,
        judge_rejected_count=rejected_count,
        final_duplicate_count=final_duplicate_count,
        final_misordered_count=final_misordered_count,
        reasoning_leak_count=reasoning_leak_count,
        role_mismatch_count=role_mismatch_count,
        quantity_shortfall_advisory=quantity_shortfall_advisory,
        quality_score=quality_score,
        quality_score_grade=grade,
        raw_judge_rejected_count=raw_rejected_count,
        semantic_duplicate_reject_count=semantic_duplicate_rejected_count,
        filtered_semantic_duplicate_reject_count=filtered_semantic_duplicate_reject_count,
    )
    return {
        "kind": "case_quality_gate",
        "mode": "shadow",
        "passed": not failures,
        "blocked": False,
        "failure_reasons": failures,
        "metrics": metrics,
    }


def _limited_strings(values: Any, limit: int = 20) -> list[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()][:limit]


def _build_quality_remediation_payload(
    *,
    score_payload: dict[str, Any],
    coverage_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    judge_reject_clusters: dict[str, Any],
) -> dict[str, Any]:
    score_inputs = dict(score_payload.get("quality_score_inputs") or {})
    deductions = [
        dict(item)
        for item in (score_payload.get("quality_score_deductions") or [])
        if isinstance(item, dict)
    ]
    deduction_keys = {str(item.get("key") or "") for item in deductions}
    missing_types = coverage_payload.get("missing_types") if isinstance(coverage_payload.get("missing_types"), dict) else {}
    reason_clusters = (
        judge_reject_clusters.get("reason_clusters")
        if isinstance(judge_reject_clusters.get("reason_clusters"), dict)
        else {}
    )
    actions: list[dict[str, Any]] = []

    def add_action(
        action_id: str,
        *,
        priority: str,
        reason: str,
        target_stage: str,
        evidence: dict[str, Any],
    ) -> None:
        if any(item.get("action_id") == action_id for item in actions):
            return
        actions.append(
            {
                "action_id": action_id,
                "priority": priority,
                "reason": reason,
                "target_stage": target_stage,
                "evidence": evidence,
            }
        )

    missing_rules = _limited_strings(coverage_payload.get("missing_rules"), limit=30)
    missing_boundary = _limited_strings(missing_types.get("boundary"), limit=20)
    missing_exception = _limited_strings(missing_types.get("exception"), limit=20)
    if missing_rules or missing_boundary or missing_exception:
        add_action(
            "cover_missing_rules",
            priority="P0",
            reason="blocking_coverage_gap",
            target_stage="gap_or_final_quality_supplement",
            evidence={
                "missing_rules": missing_rules,
                "missing_boundary": missing_boundary,
                "missing_exception": missing_exception,
                "coverage_rate": score_inputs.get("coverage_rate"),
            },
        )

    rejected_count = _to_int(score_inputs.get("rejected_count"))
    pending_count = _to_int(score_inputs.get("pending_count"))
    semantic_duplicate_reject_count = _to_int(score_inputs.get("semantic_duplicate_reject_count"))
    filtered_semantic_duplicate_reject_count = _to_int(score_inputs.get("filtered_semantic_duplicate_reject_count"))
    unresolved_semantic_duplicate_reject_count = max(
        0,
        semantic_duplicate_reject_count - filtered_semantic_duplicate_reject_count,
    )
    effective_reason_clusters = dict(reason_clusters)
    if unresolved_semantic_duplicate_reject_count <= 0 and filtered_semantic_duplicate_reject_count > 0:
        effective_reason_clusters.pop("semantic_duplicate", None)
    if rejected_count > 0 or pending_count > 0:
        action_id = (
            "reduce_semantic_duplicates"
            if unresolved_semantic_duplicate_reject_count > 0
            else "reduce_judge_rejections"
        )
        add_action(
            action_id,
            priority="P0" if rejected_count >= 5 else "P1",
            reason="judge_rejected_or_pending_candidates",
            target_stage="primary_generation_and_review_selection",
            evidence={
                "rejected_count": rejected_count,
                "pending_count": pending_count,
                "semantic_duplicate_reject_count": semantic_duplicate_reject_count,
                "filtered_semantic_duplicate_reject_count": filtered_semantic_duplicate_reject_count,
                "unresolved_semantic_duplicate_reject_count": unresolved_semantic_duplicate_reject_count,
                "dominant_reason": str(judge_reject_clusters.get("dominant_reason") or ""),
                "reason_clusters": effective_reason_clusters,
            },
        )

    low_quality_count = _to_int(score_inputs.get("low_quality_dropped_count"))
    if low_quality_count > 0:
        add_action(
            "tighten_expected_results",
            priority="P1",
            reason="low_quality_cases_filtered",
            target_stage="primary_generation_prompt_and_postprocess",
            evidence={
                "low_quality_dropped_count": low_quality_count,
                "examples": [
                    dict(item)
                    for item in (convergence_payload.get("low_quality_dropped_examples") or [])[:5]
                    if isinstance(item, dict)
                ],
            },
        )

    semantic_penalty_count = _to_int(score_inputs.get("semantic_dedup_penalty_count"))
    if semantic_penalty_count > 0 and "reduce_semantic_duplicates" not in {item.get("action_id") for item in actions}:
        add_action(
            "reduce_semantic_duplicates",
            priority="P1",
            reason="semantic_dedup_pressure",
            target_stage="primary_generation_sharding_or_review_selection",
            evidence={
                "semantic_dedup_dropped_count": _to_int(score_inputs.get("semantic_dedup_dropped_count")),
                "semantic_dedup_penalty_count": semantic_penalty_count,
                "candidate_total": _to_int(score_inputs.get("candidate_total")),
            },
        )

    final_flow_misordered = _to_int(score_inputs.get("flow_misordered_count"))
    final_flow_missing = _to_int(score_inputs.get("flow_missing_count"))
    if final_flow_misordered > 0 or final_flow_missing > 0:
        add_action(
            "repair_final_flow_structure",
            priority="P0",
            reason="final_flow_structure_invalid",
            target_stage="final_case_assembly",
            evidence={
                "final_flow_misordered_count": final_flow_misordered,
                "final_flow_missing_stage_count": final_flow_missing,
                "raw_final_flow_missing_stage_count": _to_int(
                    score_inputs.get("raw_flow_missing_count")
                    or review_decision_summary_payload.get("final_flow_missing_stage_count")
                ),
            },
        )

    priority_rank = {"P0": 0, "P1": 1, "P2": 2}
    actions.sort(key=lambda item: (priority_rank.get(str(item.get("priority") or "P2"), 2), str(item.get("action_id") or "")))
    return {
        "kind": "quality_remediation",
        "quality_score": _to_int(score_payload.get("quality_score")),
        "quality_score_grade": str(score_payload.get("quality_score_grade") or ""),
        "action_count": len(actions),
        "primary_action": str(actions[0].get("action_id") or "") if actions else "",
        "actions": actions[:8],
        "next_run_controls": {
            "target_missing_rules": missing_rules[:20],
            "avoid_judge_reject_reasons": effective_reason_clusters,
            "low_quality_dropped_count": low_quality_count,
            "semantic_dedup_penalty_count": semantic_penalty_count,
        },
    }


def _build_quality_ledger_payload(
    *,
    generation_id: int | None,
    request_id: str,
    mode: str,
    stage_counts: dict[str, Any],
    coverage_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    feedback_control_debug_payload: dict[str, Any],
    compression_diag_payload: dict[str, Any],
    context_result: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]] | None = None,
    execution_plan_validation_payload: dict[str, Any] | None = None,
    build_case_quality_failures_fn: Callable[..., Any],
    build_case_quality_metrics_fn: Callable[..., Any],
    is_candidate_insufficient_underfill_fn: Callable[..., Any],
) -> dict[str, Any]:
    """Build a compact evidence ledger for one generation run."""
    context_debug = dict((context_result or {}).get("context_debug") or {})
    fusion_debug = dict((context_result or {}).get("fusion_debug") or {})
    context_source = str((context_result or {}).get("context_source") or "").strip()
    compression_source = str(compression_diag_payload.get("context_source") or "").strip()
    missing_types = coverage_payload.get("missing_types") if isinstance(coverage_payload.get("missing_types"), dict) else {}
    control_source_meta = dict(feedback_control_debug_payload.get("source_meta") or {})
    requirement_semantic_contract = dict(
        control_source_meta.get("requirement_semantic_contract") or {}
    )
    judge_total = int(
        judge_summary_payload.get("total")
        or judge_summary_payload.get("input_count")
        or (
            int(judge_summary_payload.get("pass_count") or judge_summary_payload.get("confirmed_pass_out_count") or 0)
            + int(judge_summary_payload.get("repairable_count") or 0)
            + int(judge_summary_payload.get("reject_count") or judge_summary_payload.get("rejected_out_count") or 0)
            + int(judge_summary_payload.get("pending_count") or judge_summary_payload.get("pending_out_count") or 0)
        )
        or 0
    )
    score_payload = _build_initial_quality_score(
        coverage_payload=coverage_payload,
        convergence_payload=convergence_payload,
        generation_summary_payload=generation_summary_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        judge_summary_payload=judge_summary_payload,
        feedback_control_debug_payload=feedback_control_debug_payload,
        context_result=context_result,
        judge_decision_table_payload=judge_decision_table_payload,
        execution_plan_validation_payload=execution_plan_validation_payload,
    )
    judge_reject_clusters = _cluster_judge_reject_reasons(judge_decision_table_payload)
    case_quality_gate_payload = _build_case_quality_gate_payload(
        score_payload=score_payload,
        generation_summary_payload=generation_summary_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        judge_summary_payload=judge_summary_payload,
        judge_reject_clusters=judge_reject_clusters,
        build_case_quality_failures_fn=build_case_quality_failures_fn,
        build_case_quality_metrics_fn=build_case_quality_metrics_fn,
        is_candidate_insufficient_underfill_fn=is_candidate_insufficient_underfill_fn,
    )
    quality_remediation_payload = _build_quality_remediation_payload(
        score_payload=score_payload,
        coverage_payload=coverage_payload,
        convergence_payload=convergence_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        judge_reject_clusters=judge_reject_clusters,
    )
    quality_assessment = _quality_assessment_from_score_grade(score_payload.get("quality_score_grade"))
    return {
        "kind": "generation_quality_ledger",
        "generation_id": int(generation_id or 0),
        "request_id": str(request_id or ""),
        "generation_mode": str(mode or ""),
        "final_count": int(generation_summary_payload.get("final_count") or convergence_payload.get("final_count") or 0),
        "quality_assessment": quality_assessment,
        **score_payload,
        "execution_plan_quality": {
            "passed": bool((execution_plan_validation_payload or {}).get("passed")),
            "semantic_conflict_count": int(
                ((execution_plan_validation_payload or {}).get("metrics") or {}).get(
                    "semantic_conflict_count", 0
                )
            ),
            "semantic_warning_count": int(
                ((execution_plan_validation_payload or {}).get("metrics") or {}).get(
                    "semantic_warning_count", 0
                )
            ),
        }
        if execution_plan_validation_payload
        else {},
        "stop_reason": list(generation_summary_payload.get("stop_reason") or []),
        "coverage": {
            "coverage_rate": float(coverage_payload.get("coverage_rate") or 0.0),
            "total_rules": int(coverage_payload.get("total_rules") or 0),
            "total_extracted_rules": int(coverage_payload.get("total_extracted_rules") or coverage_payload.get("total_rules") or 0),
            "missing_rules_count": int(len(coverage_payload.get("missing_rules") or [])),
            "missing_boundary_count": int(len(missing_types.get("boundary") or [])),
            "missing_exception_count": int(len(missing_types.get("exception") or [])),
            "non_blocking_rules_count": int(len(coverage_payload.get("non_blocking_rules") or [])),
        },
        "funnel": {
            "primary_count": int(stage_counts.get("primary") or convergence_payload.get("primary_count") or 0),
            "gap_count": int(stage_counts.get("gap") or convergence_payload.get("gap_count") or 0),
            "review_count": int(stage_counts.get("review") or convergence_payload.get("review_count") or 0),
            "candidate_count_before_review": int(convergence_payload.get("candidate_count_before_review") or 0),
            "review_selected_count": int(convergence_payload.get("review_selected_count") or 0),
            "post_review_dedup_drop": int(convergence_payload.get("post_review_dedup_drop") or 0),
            "final_description_dedup_drop_count": int(
                convergence_payload.get("final_description_dedup_drop_count") or 0
            ),
            "total_dedup_drop_count": int(convergence_payload.get("total_dedup_drop_count") or 0),
            "low_quality_dropped_count": int(convergence_payload.get("low_quality_dropped_count") or 0),
            "low_quality_dropped_examples": [
                dict(item)
                for item in (convergence_payload.get("low_quality_dropped_examples") or [])[:10]
                if isinstance(item, dict)
            ],
            "semantic_dedup_dropped_count": int(convergence_payload.get("semantic_dedup_dropped_count") or 0),
        },
        "review": {
            "candidate_total": int(review_decision_summary_payload.get("candidate_total") or 0),
            "retained_total": int(review_decision_summary_payload.get("retained_total") or 0),
            "drop_by_review_llm_count": int(review_decision_summary_payload.get("drop_by_review_llm_count") or 0),
            "drop_by_review_gate_count": int(review_decision_summary_payload.get("drop_by_review_gate_count") or 0),
            "drop_by_post_review_dedup_count": int(
                review_decision_summary_payload.get("drop_by_post_review_dedup_count") or 0
            ),
            "drop_final_description_duplicate_count": int(
                review_decision_summary_payload.get("drop_final_description_duplicate_count") or 0
            ),
            "flow_missing_stage_count": int(review_decision_summary_payload.get("flow_missing_stage_count") or 0),
            "flow_misordered_count": int(review_decision_summary_payload.get("flow_misordered_count") or 0),
            "final_flow_missing_stage_count": int(
                review_decision_summary_payload.get("final_flow_missing_stage_count") or 0
            ),
            "final_flow_misordered_count": int(
                review_decision_summary_payload.get("final_flow_misordered_count") or 0
            ),
            "scenario_duplicate_cluster_count": int(
                review_decision_summary_payload.get("scenario_duplicate_cluster_count") or 0
            ),
            "scenario_duplicate_case_count": int(
                review_decision_summary_payload.get("scenario_duplicate_case_count") or 0
            ),
            "final_scenario_duplicate_cluster_count": int(
                review_decision_summary_payload.get("final_scenario_duplicate_cluster_count") or 0
            ),
            "final_scenario_duplicate_case_count": int(
                review_decision_summary_payload.get("final_scenario_duplicate_case_count") or 0
            ),
            "final_semantic_diagnostics_available": bool(
                review_decision_summary_payload.get("final_semantic_diagnostics_available")
            ),
            "final_semantic_duplicate_cluster_count": int(
                review_decision_summary_payload.get("final_semantic_duplicate_cluster_count") or 0
            ),
            "final_semantic_duplicate_case_count": int(
                review_decision_summary_payload.get("final_semantic_duplicate_case_count") or 0
            ),
            "final_semantic_dedup_dropped_count": int(
                review_decision_summary_payload.get("final_semantic_dedup_dropped_count") or 0
            ),
            "final_semantic_containment_count": int(
                review_decision_summary_payload.get("final_semantic_containment_count") or 0
            ),
            "final_semantic_relation_samples": [
                dict(item)
                for item in (review_decision_summary_payload.get("final_semantic_relation_samples") or [])[:20]
                if isinstance(item, dict)
            ],
            "fact_profile_forbidden_count": int(
                review_decision_summary_payload.get("fact_profile_forbidden_count") or 0
            ),
            "fact_profile_pending_count": int(review_decision_summary_payload.get("fact_profile_pending_count") or 0),
        },
        "judge": {
            "total": int(judge_total),
            "rejected_out_count": int(
                judge_summary_payload.get("rejected_out_count") or judge_summary_payload.get("reject_count") or 0
            ),
            "pending_out_count": int(
                judge_summary_payload.get("pending_out_count") or judge_summary_payload.get("pending_count") or 0
            ),
            "repairable_count": int(
                judge_summary_payload.get("remaining_repairable_count")
                if "remaining_repairable_count" in judge_summary_payload
                else (
                    judge_summary_payload.get("repairable_count")
                    or judge_summary_payload.get("repaired_pass_out_count")
                    or 0
                )
            ),
            "raw_repairable_count": int(
                judge_summary_payload.get("raw_repairable_count")
                if "raw_repairable_count" in judge_summary_payload
                else (judge_summary_payload.get("repairable_count") or 0)
            ),
            "repaired_pass_out_count": int(judge_summary_payload.get("repaired_pass_out_count") or 0),
            "unrepaired_repairable_count": int(judge_summary_payload.get("unrepaired_repairable_count") or 0),
            "fact_violation_count": int(judge_summary_payload.get("fact_violation_count") or 0),
            **judge_reject_clusters,
        },
        "context": {
            "snapshot_status": str(context_debug.get("snapshot_status") or ""),
            "snapshot_used": bool(context_debug.get("snapshot_used")),
            "realtime_rag_used": bool(context_debug.get("realtime_rag_used")),
            "current_document_used": bool(context_debug.get("current_document_used")),
            "fusion_mode": str(fusion_debug.get("mode") or context_source or compression_source or ""),
            "compression_ratio": compression_diag_payload.get("compression_ratio"),
            "retained_chunk_count": int(compression_diag_payload.get("retained_chunk_count") or 0),
        },
        "control": {
            "control_state_applied": bool(feedback_control_debug_payload.get("control_state_applied")),
            "generation_coverage_mode": str(feedback_control_debug_payload.get("generation_coverage_mode") or ""),
            "must_cover_rules_count": int(feedback_control_debug_payload.get("must_cover_rules_count") or 0),
            "quality_fix_hints_count": int(feedback_control_debug_payload.get("quality_fix_hints_count") or 0),
            # Optimization 等后续产出链必须复用本轮真实契约，不能从公开用例正文反推。
            "requirement_semantic_contract": requirement_semantic_contract,
        },
        "quality_remediation": quality_remediation_payload,
        "case_quality_gate": case_quality_gate_payload,
    }
