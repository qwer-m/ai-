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

    flow_missing_count = summary_metric("final_flow_missing_stage_count", "flow_missing_stage_count")
    flow_misordered_count = summary_metric("final_flow_misordered_count", "flow_misordered_count")
    scenario_duplicate_cluster_count = summary_metric(
        "final_scenario_duplicate_cluster_count",
        "scenario_duplicate_cluster_count",
    )
    scenario_duplicate_case_count = summary_metric(
        "final_scenario_duplicate_case_count",
        "scenario_duplicate_case_count",
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
    add_deduction("judge_rejected", "判定拒绝", rejected_count, min(30, rejected_count * 3))
    add_deduction("judge_pending", "待确认逻辑", pending_count, min(15, pending_count * 1.5))
    add_deduction("judge_repairable", "最终残留可修复问题", repairable_count, min(8, repairable_count * 1))
    add_deduction("fact_forbidden", "违反已确认事实", fact_violation_count, min(30, fact_violation_count * 6))
    add_deduction("fact_pending", "命中待确认事实", fact_pending_count, min(12, fact_pending_count * 1.5))
    add_deduction("low_quality_dropped", "低质量用例被过滤", low_quality_dropped_count, min(20, low_quality_dropped_count * 3))
    add_deduction("semantic_dedup", "语义去重压力", semantic_penalty_count, min(10, semantic_penalty_count * 0.5))
    if manual_delivery_metrics.get("applied"):
        high_priority_shortfall = _to_float(manual_delivery_metrics.get("high_priority_ratio_shortfall"))
        display_excess = _to_float(manual_delivery_metrics.get("display_ratio_excess"))
        priority_drift = _to_float(manual_delivery_metrics.get("priority_distribution_drift"))
        module_drift = _to_float(manual_delivery_metrics.get("module_distribution_drift"))
        add_deduction("manual_high_priority_shortfall", "人工画像P0/P1偏低", high_priority_shortfall, min(12, high_priority_shortfall * 20))
        add_deduction("manual_display_ratio_excess", "人工画像展示类过量", display_excess, min(10, display_excess * 25))
        add_deduction("manual_priority_distribution_drift", "人工画像优先级分布偏移", priority_drift, min(10, priority_drift * 12))
        add_deduction("manual_module_distribution_drift", "人工画像模块分布偏移", module_drift, min(8, module_drift * 8))
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
    if manual_delivery_metrics.get("applied"):
        score_basis = f"{score_basis}+manual_profile"
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
        "filtered_semantic_duplicate_reject_count": 0,
        "filtered_pending_candidate_count": filtered_pending_candidate_count,
        "repairable_count": repairable_count,
        "raw_repairable_count": raw_repairable_count,
        "repaired_pass_count": repaired_pass_count,
        "unrepaired_repairable_count": unrepaired_repairable_count,
        "fact_profile_forbidden_count": fact_profile_forbidden_count,
        "fact_violation_count": fact_violation_count,
        "flow_missing_count": flow_missing_count,
        "flow_misordered_count": flow_misordered_count,
        "scenario_duplicate_cluster_count": scenario_duplicate_cluster_count,
        "scenario_duplicate_case_count": scenario_duplicate_case_count,
        "low_quality_dropped_count": low_quality_dropped_count,
        "semantic_dedup_dropped_count": semantic_dedup_dropped_count,
        "semantic_dedup_pressure_count": semantic_pressure_count,
        "semantic_dedup_penalty_count": semantic_penalty_count,
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
        "quality_score_confidence": "high" if total_rules > 0 and judge_total > 0 else "medium",
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
    rejected_count = _to_int(
        judge_summary_payload.get("rejected_out_count")
        or judge_summary_payload.get("reject_count")
        or judge_reject_clusters.get("rejected_total")
    )
    final_duplicate_count = _to_int(review_decision_summary_payload.get("final_scenario_duplicate_case_count"))
    final_misordered_count = _to_int(review_decision_summary_payload.get("final_flow_misordered_count"))
    reasoning_leak_count = _to_int(review_decision_summary_payload.get("final_reasoning_leakage_case_count"))
    role_mismatch_count = _to_int(
        review_decision_summary_payload.get("final_role_mismatch_count")
        or (judge_reject_clusters.get("reason_clusters") or {}).get("role_mismatch")
    )
    raw_rejected_count = int(rejected_count)
    semantic_duplicate_rejected_count = _to_int(
        (judge_reject_clusters.get("reason_clusters") or {}).get("semantic_duplicate")
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
        filtered_semantic_duplicate_reject_count=0,
    )
    return {
        "kind": "case_quality_gate",
        "mode": "shadow",
        "passed": not failures,
        "blocked": False,
        "failure_reasons": failures,
        "metrics": metrics,
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
    return {
        "kind": "generation_quality_ledger",
        "generation_id": int(generation_id or 0),
        "request_id": str(request_id or ""),
        "generation_mode": str(mode or ""),
        "final_count": int(generation_summary_payload.get("final_count") or convergence_payload.get("final_count") or 0),
        "quality_assessment": str(generation_summary_payload.get("quality_assessment") or ""),
        **score_payload,
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
        },
        "case_quality_gate": case_quality_gate_payload,
    }
