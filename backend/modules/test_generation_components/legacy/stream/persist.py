from typing import Any, Iterator
import json

from core.db.models import LogEntry, TestGeneration
from core.settings.config import settings
from modules.domain.stage25_switches import STAGE25_SWITCHES
from ...coverage.core_flow_backfill_generation import (
    summarize_case_quality_gate,
)
from ...postprocess.persistence_gate import (
    build_persistence_gate_diagnostic,
    evaluate_persistence_gate,
    summarize_persistence_case_quality_gate,
)
from ...postprocess.case_contract import (
    merge_contract_quality_gate,
    project_persistable_cases,
    summarize_persistable_case_contract,
)
from ...prompting.generation_diagnostics import (
    build_context_compression_diagnostics,
    build_coverage_diagnostics,
)
from ...prompting.prompt_orchestration import (
    build_supplement_closed_loop_instruction,
)
from ...postprocess.result_postprocess import (
    merge_cases_for_append,
    normalize_final_case_priorities,
    stream_postprocess_cases,
)
from ..adapters import (
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
    clean_and_parse_json,
)


_STOP_REASON_LABELS = {
    "coverage_satisfied": "coverage_satisfied（核心规则覆盖已满足）",
    "stopped_due_to_diminishing_returns": "stopped_due_to_diminishing_returns（继续生成收益递减）",
    "optimal_case_set_reached": "optimal_case_set_reached（当前为最优测试用例集合）",
}
_MAX_GEN_DIAG_MESSAGE_BYTES = 60000


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


def _manual_quality_profile_from_control(payload: dict[str, Any]) -> dict[str, Any]:
    source_meta = payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {}
    profile = source_meta.get("manual_quality_profile") if isinstance(source_meta, dict) else None
    if isinstance(profile, dict) and profile.get("kind") == "manual_quality_profile":
        return dict(profile)
    return {}


def _ratio_map(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    values: dict[str, float] = {}
    total = 0.0
    for key, value in raw.items():
        text = str(key or "").strip()
        if not text:
            continue
        amount = _to_float(value)
        if amount <= 0:
            continue
        values[text] = amount
        total += amount
    if total <= 0:
        return {}
    return {key: round(float(value) / total, 6) for key, value in values.items()}


def _distribution_drift(target: Any, actual: Any) -> float:
    target_ratios = _ratio_map(target)
    actual_ratios = _ratio_map(actual)
    if not target_ratios or not actual_ratios:
        return 0.0
    keys = set(target_ratios) | set(actual_ratios)
    return round(sum(abs(target_ratios.get(key, 0.0) - actual_ratios.get(key, 0.0)) for key in keys) / 2.0, 4)


def _build_manual_delivery_metrics(
    *,
    feedback_control_debug_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
) -> dict[str, Any]:
    profile = _manual_quality_profile_from_control(feedback_control_debug_payload)
    if not profile:
        return {"applied": False}
    final_count = _to_int(generation_summary_payload.get("final_count"))
    has_final_distribution = bool(
        generation_summary_payload.get("final_priority_breakdown")
        or generation_summary_payload.get("final_module_breakdown_top")
        or ("final_display_ratio" in generation_summary_payload)
    )
    if final_count <= 0 or not has_final_distribution:
        return {
            "applied": False,
            "profile_version": str(profile.get("profile_version") or ""),
            "profile_trusted_sample_count": int(profile.get("trusted_sample_count") or 0),
            "reason": "missing_final_distribution",
        }
    target_high_priority_ratio = max(0.0, min(1.0, _to_float(profile.get("high_priority_ratio"))))
    final_high_priority_ratio = max(
        0.0,
        min(1.0, _to_float(generation_summary_payload.get("final_high_priority_ratio"))),
    )
    target_display_cap = max(0.0, min(1.0, _to_float(profile.get("display_ratio_cap"))))
    final_display_ratio = max(
        0.0,
        min(1.0, _to_float(generation_summary_payload.get("final_display_ratio"))),
    )
    priority_drift = _distribution_drift(
        profile.get("priority_distribution"),
        generation_summary_payload.get("final_priority_breakdown"),
    )
    module_drift = _distribution_drift(
        profile.get("module_distribution_top"),
        generation_summary_payload.get("final_module_breakdown_top"),
    )
    return {
        "applied": True,
        "profile_source": str(profile.get("profile_source") or ""),
        "profile_version": str(profile.get("profile_version") or ""),
        "profile_trusted_sample_count": int(profile.get("trusted_sample_count") or 0),
        "target_high_priority_ratio": round(target_high_priority_ratio, 4),
        "final_high_priority_ratio": round(final_high_priority_ratio, 4),
        "high_priority_ratio_shortfall": round(max(0.0, target_high_priority_ratio - final_high_priority_ratio), 4),
        "target_display_ratio_cap": round(target_display_cap, 4),
        "final_display_ratio": round(final_display_ratio, 4),
        "display_ratio_excess": round(max(0.0, final_display_ratio - target_display_cap), 4),
        "priority_distribution_drift": priority_drift,
        "module_distribution_drift": module_drift,
    }


def _normalize_missing_priority_final_cases(
    cases: Any,
    *,
    requirement_text: str,
) -> Any:
    """Fill stripped priority_final fields without masking explicit invalid values."""
    if not isinstance(cases, list):
        return cases
    if not any(isinstance(item, dict) and "priority_final" not in item for item in cases):
        return cases

    normalized = normalize_final_case_priorities(cases, requirement_text=requirement_text)
    normalized_by_index = {
        index: item
        for index, item in enumerate(normalized if isinstance(normalized, list) else [])
        if isinstance(item, dict)
    }
    resolved: list[Any] = []
    for index, item in enumerate(cases):
        if isinstance(item, dict) and "priority_final" not in item:
            resolved.append(normalized_by_index.get(index, item))
        else:
            resolved.append(item)
    return resolved


def _build_initial_quality_score(
    *,
    coverage_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    feedback_control_debug_payload: dict[str, Any],
    context_result: dict[str, Any],
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
    fact_forbidden_count = _to_int(review_decision_summary_payload.get("fact_profile_forbidden_count"))
    fact_pending_count = _to_int(review_decision_summary_payload.get("fact_profile_pending_count"))

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
    repairable_count = _to_int(judge_summary_payload.get("repairable_count") or judge_summary_payload.get("repaired_pass_out_count"))

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
    add_deduction("judge_repairable", "可修复问题", repairable_count, min(8, repairable_count * 1))
    add_deduction("fact_forbidden", "违反已确认事实", fact_forbidden_count, min(30, fact_forbidden_count * 6))
    add_deduction("fact_pending", "命中待确认事实", fact_pending_count, min(12, fact_pending_count * 1.5))
    add_deduction("low_quality_dropped", "低质量用例被过滤", low_quality_dropped_count, min(20, low_quality_dropped_count * 3))
    add_deduction("semantic_dedup", "语义去重压力", semantic_dedup_dropped_count or total_dedup_drop_count, min(10, max(semantic_dedup_dropped_count, total_dedup_drop_count) * 0.5))
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
        "pending_count": pending_count,
        "repairable_count": repairable_count,
        "flow_missing_count": flow_missing_count,
        "flow_misordered_count": flow_misordered_count,
        "scenario_duplicate_cluster_count": scenario_duplicate_cluster_count,
        "scenario_duplicate_case_count": scenario_duplicate_case_count,
        "low_quality_dropped_count": low_quality_dropped_count,
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


def _cluster_judge_reject_reasons(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    clusters: dict[str, int] = {}
    rejected_total = 0
    for row in rows or []:
        if not isinstance(row, dict) or _judge_status_key(row) != "REJECT":
            continue
        rejected_total += 1
        reason = str(row.get("reject_reason") or "").strip().lower()
        signals = row.get("signals") if isinstance(row.get("signals"), dict) else {}
        if reason.startswith("semantic_duplicate") or bool(signals.get("is_semantic_duplicate")):
            key = "semantic_duplicate"
        elif bool(signals.get("violates_confirmed_fact")) or "fact" in reason or "事实" in reason:
            key = "fact_conflict"
        elif "role" in reason or "角色" in reason or "session" in reason:
            key = "role_mismatch"
        elif "precondition" in reason or "前置" in reason:
            key = "invalid_precondition"
        elif "assert" in reason or "断言" in reason or "non_assertable" in reason:
            key = "non_assertable"
        elif "duplicate" in reason or "重复" in reason:
            key = "duplicate_other"
        elif reason:
            key = reason.split(":", 1)[0][:80]
        else:
            key = "unspecified"
        clusters[key] = int(clusters.get(key) or 0) + 1
    ordered = dict(sorted(clusters.items(), key=lambda item: (-int(item[1]), item[0])))
    return {
        "rejected_total": int(rejected_total),
        "reason_clusters": ordered,
        "dominant_reason": next(iter(ordered), ""),
    }


def _build_case_quality_gate_payload(
    *,
    score_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    judge_reject_clusters: dict[str, Any],
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
    failures: list[str] = []
    if min_acceptable_final > 0 and final_count < min_acceptable_final:
        failures.append("final_count_below_min_acceptable")
    if quality_score <= 0 or grade == "critical":
        failures.append("quality_score_critical")
    if rejected_count > 20:
        failures.append("judge_rejected_above_threshold")
    if reasoning_leak_count > 0:
        failures.append("reasoning_leakage_detected")
    if role_mismatch_count > 5:
        failures.append("role_mismatch_above_threshold")
    return {
        "kind": "case_quality_gate",
        "mode": "shadow",
        "passed": not failures,
        "blocked": False,
        "failure_reasons": failures,
        "metrics": {
            "final_count": int(final_count),
            "min_acceptable_final": int(min_acceptable_final),
            "quality_score": int(quality_score),
            "quality_score_grade": grade,
            "final_scenario_duplicate_case_count": int(final_duplicate_count),
            "final_flow_misordered_count": int(final_misordered_count),
            "judge_rejected_count": int(rejected_count),
            "reasoning_leak_count": int(reasoning_leak_count),
            "role_mismatch_count": int(role_mismatch_count),
        },
    }


def _render_stop_reason_text(stop_reasons: list[Any]) -> str:
    labels: list[str] = []
    for reason in stop_reasons:
        key = str(reason or "").strip()
        if not key:
            continue
        label = _STOP_REASON_LABELS.get(key, key)
        if label in labels:
            continue
        labels.append(label)
    return "；".join(labels)


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
    )
    judge_reject_clusters = _cluster_judge_reject_reasons(judge_decision_table_payload)
    case_quality_gate_payload = _build_case_quality_gate_payload(
        score_payload=score_payload,
        generation_summary_payload=generation_summary_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        judge_summary_payload=judge_summary_payload,
        judge_reject_clusters=judge_reject_clusters,
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
                judge_summary_payload.get("repairable_count") or judge_summary_payload.get("repaired_pass_out_count") or 0
            ),
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


def _judge_status_key(row: dict[str, Any]) -> str:
    status = str((row or {}).get("judge_status") or (row or {}).get("status") or "").strip().upper()
    return status


def _safe_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _build_judge_signal_payload(row: dict[str, Any]) -> dict[str, Any]:
    signals_raw = row.get("signals") if isinstance(row.get("signals"), dict) else {}
    return {
        "violates_confirmed_fact": bool(
            signals_raw.get("violates_confirmed_fact", row.get("violates_confirmed_fact"))
        ),
        "missing_core_flow": bool(
            signals_raw.get("missing_core_flow", row.get("missing_core_flow"))
        ),
        "missing_reuse_risk": bool(
            signals_raw.get("missing_reuse_risk", row.get("missing_reuse_risk"))
        ),
        "contains_pending_logic": bool(
            signals_raw.get("contains_pending_logic", row.get("contains_pending_logic"))
        ),
        "confirmed_fact_hits": _safe_list(
            signals_raw.get("confirmed_fact_hits", row.get("confirmed_fact_hits"))
        ),
        "confirmed_fact_violations": _safe_list(
            signals_raw.get("confirmed_fact_violations", row.get("confirmed_fact_violations"))
        ),
        "reuse_risk_hits": _safe_list(signals_raw.get("reuse_risk_hits", row.get("reuse_risk_hits"))),
        "pending_hits": _safe_list(signals_raw.get("pending_hits", row.get("pending_hits"))),
        "vague_or_unconfirmed_hits": _safe_list(
            signals_raw.get("vague_or_unconfirmed_hits", row.get("vague_or_unconfirmed_hits"))
        ),
    }


def _normalize_judge_row(
    row: dict[str, Any],
    *,
    generation_id: int,
    request_id: str,
) -> dict[str, Any]:
    signals_payload = _build_judge_signal_payload(row)
    before_case = row.get("before_case_snapshot")
    if not isinstance(before_case, dict):
        before_case = row.get("before_case")
    if not isinstance(before_case, dict):
        before_case = {}
    after_case = row.get("after_case_snapshot")
    if not isinstance(after_case, dict):
        after_case = row.get("after_case")
    if not isinstance(after_case, dict):
        after_case = {}

    return {
        "generation_id": int(generation_id),
        "request_id": str(request_id or "").strip(),
        "case_id": str(row.get("case_id") or "").strip(),
        "judge_status": _judge_status_key(row),
        "reject_reason": str(row.get("reject_reason") or "").strip(),
        "pending_reason": str(row.get("pending_reason") or "").strip(),
        "signals": signals_payload,
        "violates_confirmed_fact": bool(signals_payload.get("violates_confirmed_fact")),
        "missing_core_flow": bool(signals_payload.get("missing_core_flow")),
        "missing_reuse_risk": bool(signals_payload.get("missing_reuse_risk")),
        "contains_pending_logic": bool(signals_payload.get("contains_pending_logic")),
        "confirmed_fact_hits": list(signals_payload.get("confirmed_fact_hits") or []),
        "confirmed_fact_violations": list(signals_payload.get("confirmed_fact_violations") or []),
        "reuse_risk_hits": list(signals_payload.get("reuse_risk_hits") or []),
        "pending_hits": list(signals_payload.get("pending_hits") or []),
        "vague_or_unconfirmed_hits": list(signals_payload.get("vague_or_unconfirmed_hits") or []),
        "before_case_snapshot": dict(before_case),
        "after_case_snapshot": dict(after_case),
    }


def _normalize_review_compact_rows(
    rows: list[dict[str, Any]],
    *,
    generation_id: int,
    request_id: str,
) -> list[dict[str, Any]]:
    compact_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("dropped_stage") or "") != "review_llm":
            continue
        evidence = row.get("review_llm_drop_reason_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        compact_rows.append(
            {
                "generation_id": int(generation_id),
                "request_id": str(request_id or "").strip(),
                "candidate_index": int(row.get("candidate_index") or 0),
                "case_id": str(row.get("case_id") or "").strip(),
                "test_module": str(row.get("test_module") or "").strip(),
                "flow_stage": str(row.get("flow_stage") or "").strip(),
                "flow_stage_label": str(row.get("flow_stage_label") or "").strip(),
                "scenario_key": str(row.get("scenario_key") or "").strip(),
                "is_scenario_duplicate": bool(row.get("is_scenario_duplicate")),
                "duplicate_cluster_id": str(row.get("duplicate_cluster_id") or "").strip(),
                "misordered_against_requirement_flow": bool(row.get("misordered_against_requirement_flow")),
                "model_priority_current": str(row.get("model_priority_current") or "").strip(),
                "bucket": str(row.get("bucket") or "").strip(),
                "dropped_stage": "review_llm",
                "dropped_reason": str(row.get("dropped_reason") or "").strip(),
                "review_llm_drop_reason_raw": str(row.get("review_llm_drop_reason_raw") or "").strip(),
                "review_llm_drop_reason": str(row.get("review_llm_drop_reason") or "").strip(),
                "review_llm_drop_reason_source": str(row.get("review_llm_drop_reason_source") or "").strip(),
                "high_signal": bool(row.get("high_signal")),
                "has_coverage_value": bool(row.get("has_coverage_value")),
                "has_positive_evidence": bool(row.get("has_positive_evidence")),
                "has_coverage_signal": bool(row.get("has_coverage_signal")),
                "has_high_signal": bool(row.get("has_high_signal")),
                "has_competition_signal": bool(row.get("has_competition_signal")),
                "focus_score": int(row.get("focus_score") or 0),
                "evidence": {
                    "selected_case_ids": list(evidence.get("selected_case_ids") or [])[:3],
                    "selected_count_in_bucket": int(evidence.get("selected_count_in_bucket") or 0),
                    "coverage_gain_score": int(evidence.get("coverage_gain_score") or 0),
                    "missing_rule_hits_count": int(len(evidence.get("missing_rule_hits") or [])),
                    "core_rule_hits_count": int(len(evidence.get("core_rule_hits") or [])),
                    "unique_coverage_hits_count": int(len(evidence.get("unique_coverage_hits") or [])),
                    "similarity": float(evidence.get("similarity") or 0.0),
                    "duplicate_of_case_id": str(evidence.get("duplicate_of_case_id") or "").strip(),
                },
            }
        )
    return compact_rows


def _fit_table_diag_payload_size(payload: dict[str, Any], *, max_bytes: int = _MAX_GEN_DIAG_MESSAGE_BYTES) -> dict[str, Any]:
    fitted = dict(payload or {})
    rows = [item for item in (fitted.get("rows") or []) if isinstance(item, dict)]
    fitted["rows"] = rows
    fitted["row_count"] = int(len(rows))
    fitted.setdefault("row_count_total", int(len(rows)))

    def _payload_size_bytes(obj: dict[str, Any]) -> int:
        return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    if _payload_size_bytes(fitted) <= max_bytes:
        return fitted

    sampled = list(rows)
    while sampled:
        candidate = dict(fitted)
        candidate["rows"] = sampled
        candidate["row_count"] = int(len(sampled))
        candidate["row_count_total"] = int(len(rows))
        candidate["rows_scope"] = "sampled_due_to_size"
        if _payload_size_bytes(candidate) <= max_bytes:
            return candidate
        if len(sampled) <= 1:
            break
        sampled = sampled[: max(1, int(len(sampled) // 2))]

    fallback = dict(fitted)
    fallback["rows"] = []
    fallback["row_count"] = 0
    fallback["row_count_total"] = int(len(rows))
    fallback["rows_scope"] = "summary_only_due_to_size"
    return fallback


def _with_run_context(
    payload: dict[str, Any],
    *,
    request_id: str,
    project_id: int,
    multi_pass: bool,
    generation_mode: str,
) -> dict[str, Any]:
    enriched = dict(payload or {})
    if request_id:
        enriched["request_id"] = request_id
    enriched["project_id"] = int(project_id)
    enriched["multi_pass"] = bool(multi_pass)
    enriched["generation_mode"] = str(generation_mode or "")
    return enriched


def _build_pre_persistence_failure_diagnostics(
    *,
    generation_id: int | None,
    request_id: str,
    project_id: int,
    mode: str,
    multi_pass: bool,
    expected_count: int,
    stage_counts: dict[str, Any],
    coverage_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    review_decision_table_payload: list[dict[str, Any]],
    judge_summary_payload: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]],
    feedback_control_debug_payload: dict[str, Any],
    compression_diag_payload: dict[str, Any],
    context_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Persist enough diagnostics to debug a run that is blocked before insertion."""
    diagnostics: list[dict[str, Any]] = []
    generation_id_int = int(generation_id or 0)

    def add(payload: dict[str, Any]) -> None:
        if not payload:
            return
        diagnostics.append(
            _with_run_context(
                payload,
                request_id=request_id,
                project_id=project_id,
                multi_pass=multi_pass,
                generation_mode=mode,
            )
        )

    if generation_summary_payload:
        add({"kind": "generation_summary", **generation_summary_payload})
    if convergence_payload:
        add(
            {
                "kind": "generation_convergence",
                **convergence_payload,
                "expected_count": int(expected_count or 0),
            }
        )
    if review_decision_summary_payload:
        add({"kind": "review_decision_summary", **review_decision_summary_payload})
    if feedback_control_debug_payload:
        add({"kind": "feedback_control_state", **feedback_control_debug_payload})
    if judge_summary_payload:
        add(
            {
                "kind": "judge_summary",
                **judge_summary_payload,
                "generation_id": generation_id_int,
            }
        )

    if judge_summary_payload or judge_decision_table_payload:
        normalized_rows = [
            _normalize_judge_row(
                item,
                generation_id=generation_id_int,
                request_id=request_id,
            )
            for item in judge_decision_table_payload
            if isinstance(item, dict)
        ]
        reject_pending_rows = [
            row
            for row in normalized_rows
            if str(row.get("judge_status") or "").upper() in {"REJECT", "PENDING"}
        ]
        rows_to_persist = reject_pending_rows or normalized_rows
        judge_table_diag = {
            "kind": "judge_decision_table",
            "generation_id": generation_id_int,
            "rows": rows_to_persist,
            "row_count": int(len(rows_to_persist)),
            "row_count_total": int(len(normalized_rows)),
            "row_count_reject_pending": int(len(reject_pending_rows)),
            "rows_scope": "reject_pending_only" if reject_pending_rows else "all_when_no_reject_pending",
            "row_evidence_incomplete": bool(
                int(judge_summary_payload.get("rejected_out_count") or 0)
                + int(judge_summary_payload.get("pending_out_count") or 0) > 0
                and len(reject_pending_rows) == 0
            ),
        }
        add(_fit_table_diag_payload_size(judge_table_diag))

    if review_decision_table_payload:
        review_table_diag = {
            "kind": "review_decision_table",
            "generation_id": generation_id_int,
            "rows": review_decision_table_payload,
            "row_count": int(len(review_decision_table_payload)),
        }
        add(_fit_table_diag_payload_size(review_table_diag))
        compact_rows = _normalize_review_compact_rows(
            review_decision_table_payload,
            generation_id=generation_id_int,
            request_id=request_id,
        )
        if compact_rows:
            compact_diag = {
                "kind": "review_decision_table_compact",
                "generation_id": generation_id_int,
                "rows": compact_rows,
                "row_count": int(len(compact_rows)),
            }
            add(_fit_table_diag_payload_size(compact_diag))

    quality_ledger_payload = _build_quality_ledger_payload(
        generation_id=generation_id,
        request_id=request_id,
        mode=mode,
        stage_counts=stage_counts,
        coverage_payload=coverage_payload,
        convergence_payload=convergence_payload,
        generation_summary_payload=generation_summary_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        judge_summary_payload=judge_summary_payload,
        feedback_control_debug_payload=feedback_control_debug_payload,
        compression_diag_payload=compression_diag_payload,
        context_result=context_result,
        judge_decision_table_payload=judge_decision_table_payload,
    )
    add(quality_ledger_payload)
    case_quality_gate_payload = dict(quality_ledger_payload.get("case_quality_gate") or {})
    if case_quality_gate_payload:
        case_quality_gate_payload["generation_id"] = generation_id_int
        add(case_quality_gate_payload)
    if coverage_payload:
        add(dict(coverage_payload))
    return diagnostics


class LegacyGenerationStreamPersistMixin:

    def _stream_persist_phase(
        self,
        *,
        state: dict[str, Any],
    ) -> Iterator[None]:
        client = state["client"]
        requirement = state["requirement"]
        project_id = state["project_id"]
        db = state["db"]
        doc_type = state["doc_type"]
        compress = state["compress"]
        expected_count = state["expected_count"]
        overwrite = state["overwrite"]
        append = state["append"]
        user_id = state["user_id"]
        original_requirement = state["original_requirement"]
        kb_context = state.get("kb_context") or ""
        start_id = int(state.get("start_id") or 1)
        existing_cases = state.get("existing_cases") or []
        existing_entry = state.get("existing_entry")
        context_result = state.get("context_result") or {}
        gate_debug = state.get("gate_debug") or {}
        base_prompt = state.get("base_prompt") or ""
        full_content = state.get("full_content") or ""
        existing_unique_count = int(state.get("existing_unique_count") or 0)
        system_prompt = state.get("system_prompt") or ""
        current_biz_key = str(state.get("current_biz_key") or "")
        multi_pass = bool(state.get("multi_pass", True))
        generation_mode = str(state.get("generation_mode") or "").strip().lower()
        request_id = str(state.get("request_id") or "").strip()
        feedback_control_state = state.get("feedback_control_state") or {}
        requirement_semantics_context = state.get("requirement_semantics_context") or {}
        memory_diag = state.get("memory_diag") if isinstance(state.get("memory_diag"), dict) else {}

        try:
            postprocess_result = yield from stream_postprocess_cases(
                client=client,
                requirement=requirement,
                base_prompt=base_prompt,
                kb_context=kb_context,
                full_content=full_content,
                expected_count=expected_count,
                append=append,
                existing_cases=existing_cases,
                existing_unique_count=existing_unique_count,
                start_id=start_id,
                db=db,
                clean_and_parse_json_fn=clean_and_parse_json,
                normalize_json_structure_fn=normalize_json_structure,
                deduplicate_test_cases_fn=deduplicate_test_cases,
                reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                count_unique_test_cases_fn=count_unique_test_cases,
                infer_case_kind_fn=infer_case_kind,
                build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction,
                current_biz_key=current_biz_key,
                multi_pass=multi_pass,
                generation_mode=generation_mode,
                feedback_control_state=feedback_control_state,
                requirement_semantics_context=requirement_semantics_context,
            )

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
                review_decision_summary_payload = dict(postprocess_result.get("review_decision_summary") or {})
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

            parsed_result = _normalize_missing_priority_final_cases(parsed_result, requirement_text=requirement)
            stream_quality_gate_result = summarize_case_quality_gate(parsed_result if isinstance(parsed_result, list) else [])
            stream_quality_gate_result = merge_contract_quality_gate(
                stream_quality_gate_result,
                summarize_persistable_case_contract(parsed_result),
            )
            parsed_result = project_persistable_cases(parsed_result)
            stream_quality_gate_result = summarize_persistence_case_quality_gate(
                stream_quality_gate_result,
                generation_summary=generation_summary_payload,
                review_decision_summary=review_decision_summary_payload,
                judge_summary=judge_summary_payload,
                settings=settings,
            )
            persistence_preview = parsed_result
            if append and existing_entry:
                persistence_preview = merge_cases_for_append(
                    existing_cases,
                    parsed_result,
                    deduplicate_test_cases_fn=deduplicate_test_cases,
                    reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                )
            persistence_preview = project_persistable_cases(persistence_preview)
            stream_quality_gate_result = merge_contract_quality_gate(
                stream_quality_gate_result,
                summarize_persistable_case_contract(persistence_preview),
            )
            workflow_blueprints = [
                dict(item)
                for item in (feedback_control_state.get("workflow_blueprints") or [])
                if isinstance(item, dict)
            ] if isinstance(feedback_control_state, dict) else []
            execution_plan = dict(review_decision_summary_payload.get("execution_plan") or {})
            persistence_gate_result = evaluate_persistence_gate(
                persistence_preview,
                workflow_blueprints=workflow_blueprints,
                execution_plan=execution_plan,
                generation_mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                quality_gate=stream_quality_gate_result,
                settings=settings,
            )
            persistence_gate_diag = build_persistence_gate_diagnostic(persistence_gate_result)
            persistence_gate_diag["request_id"] = request_id
            persistence_gate_diag["project_id"] = int(project_id)
            if db:
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(persistence_gate_diag, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                db.commit()
            yield f"GEN_DIAG:{json.dumps(persistence_gate_diag, ensure_ascii=False)}\n"
            if not bool(persistence_gate_result.get("passed")):
                compression_diag_payload = build_context_compression_diagnostics(
                    context_result=context_result if isinstance(context_result, dict) else {},
                )
                pre_failure_diagnostics = _build_pre_persistence_failure_diagnostics(
                    generation_id=None,
                    request_id=request_id,
                    project_id=int(project_id),
                    mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    multi_pass=bool(multi_pass),
                    expected_count=int(expected_count or 0),
                    stage_counts=stage_counts,
                    coverage_payload=coverage_payload,
                    convergence_payload=convergence_payload,
                    generation_summary_payload=generation_summary_payload,
                    review_decision_summary_payload=review_decision_summary_payload,
                    review_decision_table_payload=review_decision_table_payload,
                    judge_summary_payload=judge_summary_payload,
                    judge_decision_table_payload=judge_decision_table_payload,
                    feedback_control_debug_payload=feedback_control_debug_payload,
                    compression_diag_payload=compression_diag_payload,
                    context_result=context_result if isinstance(context_result, dict) else {},
                )
                for diag_payload in pre_failure_diagnostics:
                    if db:
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(diag_payload, ensure_ascii=False)}",
                                user_id=user_id,
                            )
                        )
                    yield f"GEN_DIAG:{json.dumps(diag_payload, ensure_ascii=False)}\n"
                if db and pre_failure_diagnostics:
                    db.commit()
                failure_code = str(persistence_gate_result.get("failure_code") or "execution_plan_failed")
                execution_plan_validation = persistence_gate_result.get("execution_plan_validation")
                quality_gate = persistence_gate_result.get("quality_gate")
                quality_failed_checks = (
                    quality_gate.get("failed_checks")
                    if isinstance(quality_gate, dict)
                    else []
                )
                execution_failure_reasons = (
                    execution_plan_validation.get("failure_reasons")
                    if isinstance(execution_plan_validation, dict)
                    else []
                )
                failure_reasons = (
                    quality_failed_checks
                    if failure_code == "LOW_QUALITY_GENERATED_CASES"
                    else execution_failure_reasons
                )
                failure_reason_text = ",".join(
                    str(item).strip()
                    for item in (failure_reasons or [])
                    if str(item).strip()
                )
                failure_detail = f": {failure_reason_text}" if failure_reason_text else ""
                yield f"\n@@STATUS@@:生成结果未通过落库门禁{failure_detail}\n"
                yield f"Error: {failure_code}{failure_detail}\n"
                return
            parsed_result = persistence_gate_result.get("cases") if isinstance(persistence_gate_result.get("cases"), list) else []
            cleaned_response = json.dumps(parsed_result, ensure_ascii=False)
            persisted_generation_id: int | None = None

            if db:
                if overwrite:
                    from sqlalchemy import desc

                    query = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text == original_requirement,
                    )
                    if user_id:
                        query = query.filter(TestGeneration.user_id == user_id)
                    existing_entry_overwrite = query.order_by(desc(TestGeneration.created_at)).first()
                    if existing_entry_overwrite:
                        existing_entry_overwrite.generated_result = cleaned_response
                        db.commit()
                        persisted_generation_id = int(existing_entry_overwrite.id or 0) or None
                    else:
                        new_entry = TestGeneration(
                            requirement_text=original_requirement,
                            generated_result=cleaned_response,
                            project_id=project_id,
                            user_id=user_id,
                        )
                        db.add(
                            new_entry
                        )
                        db.commit()
                        persisted_generation_id = int(new_entry.id or 0) or None
                elif append and existing_entry:
                    existing_entry.generated_result = json.dumps(parsed_result, ensure_ascii=False)
                    db.commit()
                    persisted_generation_id = int(existing_entry.id or 0) or None
                else:
                    new_entry = TestGeneration(
                        requirement_text=original_requirement,
                        generated_result=cleaned_response,
                        project_id=project_id,
                        user_id=user_id,
                    )
                    db.add(
                        new_entry
                    )
                    db.commit()
                    persisted_generation_id = int(new_entry.id or 0) or None

                # 中文注释：把本次最终落库 generation_id 回传给前端，便于流式完成后回拉最终结果。
                if persisted_generation_id:
                    persisted_payload = {
                        "kind": "generation_persisted",
                        "generation_id": int(persisted_generation_id),
                        "project_id": int(project_id),
                    }
                    if request_id:
                        persisted_payload["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(persisted_payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(persisted_payload, ensure_ascii=False)}\n"

                mode_payload = {
                    "kind": "generation_mode",
                    "mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    "biz_keys": [current_biz_key or "unknown"],
                    "current_biz_key": current_biz_key or "unknown",
                    "multi_pass": bool(multi_pass),
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(mode_payload, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                yield f"GEN_DIAG:{json.dumps(mode_payload, ensure_ascii=False)}\n"

                # 中文注释：记录阶段日志，便于观察 multi-pass 执行情况。
                for stage in ("primary", "gap", "review"):
                    payload = {
                        "kind": "generation_stage",
                        "stage": stage,
                        "case_count": int(stage_counts.get(stage, 0)),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}\n"

                # GEN_DIAG summary
                full_input = (system_prompt or "") + requirement
                actual_model = client.select_model(full_input, task_type="generation")
                compression_diag_payload = build_context_compression_diagnostics(
                    context_result=context_result if isinstance(context_result, dict) else {},
                )
                diag = {
                    "kind": "gen_diag",
                    "mode": "stream",
                    "doc_type": doc_type,
                    "compress": compress,
                    "expected_count": expected_count,
                    "generated_count": count_unique_test_cases(parsed_result),
                    "content_length": len(requirement),
                    "kb_length": len(kb_context or ""),
                    "model": actual_model,
                    "max_tokens": client.max_tokens,
                    "multi_pass": bool(multi_pass),
                    "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    "context_compression_ratio": compression_diag_payload.get("compression_ratio"),
                    "context_retained_chunk_count": compression_diag_payload.get("retained_chunk_count"),
                    "context_relevance_distribution": compression_diag_payload.get("relevance_distribution") or {},
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                yield f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}\n"
                compression_diag = {
                    "kind": "generation_context_compression",
                    **compression_diag_payload,
                    "multi_pass": bool(multi_pass),
                    "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                }
                if request_id:
                    compression_diag["request_id"] = request_id
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(compression_diag, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                yield f"GEN_DIAG:{json.dumps(compression_diag, ensure_ascii=False)}\n"

                # 中文注释：记录“质量/覆盖收敛”诊断，数量仅作为参考差异，不再判定为失败。
                if convergence_payload:
                    convergence_diag = {
                        "kind": "generation_convergence",
                        **convergence_payload,
                        "expected_count": int(expected_count or 0),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(convergence_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(convergence_diag, ensure_ascii=False)}\n"

                if review_decision_summary_payload:
                    review_summary_diag = {
                        "kind": "review_decision_summary",
                        **review_decision_summary_payload,
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    if request_id:
                        review_summary_diag["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(review_summary_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(review_summary_diag, ensure_ascii=False)}\n"

                if feedback_control_debug_payload:
                    control_diag = {
                        "kind": "feedback_control_state",
                        **feedback_control_debug_payload,
                    }
                    if request_id:
                        control_diag["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(control_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(control_diag, ensure_ascii=False)}\n"
                if judge_summary_payload:
                    judge_diag = {
                        "kind": "judge_summary",
                        **judge_summary_payload,
                    }
                    if persisted_generation_id:
                        judge_diag["generation_id"] = int(persisted_generation_id)
                    if request_id:
                        judge_diag["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(judge_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(judge_diag, ensure_ascii=False)}\n"
                if judge_summary_payload or judge_decision_table_payload:
                    normalized_rows = [
                        _normalize_judge_row(
                            item,
                            generation_id=int(persisted_generation_id or 0),
                            request_id=request_id,
                        )
                        for item in judge_decision_table_payload
                        if isinstance(item, dict)
                    ]
                    reject_pending_rows = [
                        row
                        for row in normalized_rows
                        if str(row.get("judge_status") or "").upper() in {"REJECT", "PENDING"}
                    ]
                    rows_to_persist = reject_pending_rows or normalized_rows
                    judge_table_diag = {
                        "kind": "judge_decision_table",
                        "generation_id": int(persisted_generation_id or 0),
                        "rows": rows_to_persist,
                        "row_count": int(len(rows_to_persist)),
                        "row_count_total": int(len(normalized_rows)),
                        "row_count_reject_pending": int(len(reject_pending_rows)),
                        "rows_scope": "reject_pending_only" if reject_pending_rows else "all_when_no_reject_pending",
                        "row_evidence_incomplete": bool(
                            int(judge_summary_payload.get("rejected_out_count") or 0)
                            + int(judge_summary_payload.get("pending_out_count") or 0) > 0
                            and len(reject_pending_rows) == 0
                        ),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    if request_id:
                        judge_table_diag["request_id"] = request_id
                    judge_table_diag = _fit_table_diag_payload_size(judge_table_diag)
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(judge_table_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(judge_table_diag, ensure_ascii=False)}\n"
                if memory_diag:
                    memory_diag_payload = {
                        "kind": "memory_fabric_diag",
                        **dict(memory_diag),
                    }
                    if request_id:
                        memory_diag_payload["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(memory_diag_payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(memory_diag_payload, ensure_ascii=False)}\n"

                if review_decision_table_payload:
                    review_table_diag = {
                        "kind": "review_decision_table",
                        "generation_id": int(persisted_generation_id or 0),
                        "rows": review_decision_table_payload,
                        "row_count": int(len(review_decision_table_payload)),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    if request_id:
                        review_table_diag["request_id"] = request_id
                    review_table_diag = _fit_table_diag_payload_size(review_table_diag)
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(review_table_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(review_table_diag, ensure_ascii=False)}\n"

                    compact_rows = _normalize_review_compact_rows(
                        review_decision_table_payload,
                        generation_id=int(persisted_generation_id or 0),
                        request_id=request_id,
                    )
                    if compact_rows:
                        review_table_compact_diag = {
                            "kind": "review_decision_table_compact",
                            "generation_id": int(persisted_generation_id or 0),
                            "rows": compact_rows,
                            "row_count": int(len(compact_rows)),
                            "multi_pass": bool(multi_pass),
                            "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        }
                        if request_id:
                            review_table_compact_diag["request_id"] = request_id
                        review_table_compact_diag = _fit_table_diag_payload_size(review_table_compact_diag)
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(review_table_compact_diag, ensure_ascii=False)}",
                                user_id=user_id,
                            )
                        )
                        yield f"GEN_DIAG:{json.dumps(review_table_compact_diag, ensure_ascii=False)}\n"

                if generation_summary_payload:
                    generation_summary_diag = {
                        "kind": "generation_summary",
                        **generation_summary_payload,
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(generation_summary_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(generation_summary_diag, ensure_ascii=False)}\n"
                    status = str(generation_summary_payload.get("status") or "")
                    stop_reason_text = _render_stop_reason_text(
                        list(generation_summary_payload.get("stop_reason") or [])
                    )
                    if status in {"completed_with_optimal_set", "completed_with_quality_stop"}:
                        yield "@@STATUS@@:正常完成\n"
                        if stop_reason_text:
                            yield f"@@STATUS@@:停止原因：{stop_reason_text}\n"
                    if status == "completed_with_optimal_set":
                        yield "@@STATUS@@:已达到质量停止条件\n"
                        yield "@@STATUS@@:当前为最优测试用例集合\n"
                        yield "@@STATUS@@:继续生成将降低质量或增加冗余\n"

                quality_ledger_payload = _build_quality_ledger_payload(
                    generation_id=persisted_generation_id,
                    request_id=request_id,
                    mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    stage_counts=stage_counts,
                    coverage_payload=coverage_payload,
                    convergence_payload=convergence_payload,
                    generation_summary_payload=generation_summary_payload,
                    review_decision_summary_payload=review_decision_summary_payload,
                    judge_summary_payload=judge_summary_payload,
                    feedback_control_debug_payload=feedback_control_debug_payload,
                    compression_diag_payload=compression_diag_payload,
                    context_result=context_result if isinstance(context_result, dict) else {},
                    judge_decision_table_payload=judge_decision_table_payload,
                )
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(quality_ledger_payload, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                yield f"GEN_DIAG:{json.dumps(quality_ledger_payload, ensure_ascii=False)}\n"
                case_quality_gate_payload = dict(quality_ledger_payload.get("case_quality_gate") or {})
                if case_quality_gate_payload:
                    if persisted_generation_id:
                        case_quality_gate_payload["generation_id"] = int(persisted_generation_id)
                    if request_id:
                        case_quality_gate_payload["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(case_quality_gate_payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(case_quality_gate_payload, ensure_ascii=False)}\n"

                # 中文注释：记录覆盖检查日志。
                if coverage_payload:
                    coverage_payload["multi_pass"] = bool(multi_pass)
                    coverage_payload["generation_mode"] = generation_mode or ("multi_pass" if multi_pass else "single_pass")
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(coverage_payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(coverage_payload, ensure_ascii=False)}\n"

                # 淇濈暀鏃㈡湁瑕嗙洊璇婃柇
                if STAGE25_SWITCHES.coverage_diagnostics_enabled:
                    coverage_diag = build_coverage_diagnostics(
                        requirement=requirement,
                        generated_cases=[x for x in parsed_result if isinstance(x, dict)],
                        kb_context=kb_context,
                        fusion_debug=(context_result or {}).get("fusion_debug") or {},
                        expected_count=int(expected_count or 0),
                    )
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_COVERAGE_DIAG:{json.dumps(coverage_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_COVERAGE_DIAG:{json.dumps(coverage_diag, ensure_ascii=False)}\n"

                db.commit()

                self._emit_context_source_log(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    context_result=context_result,
                    gate_debug=gate_debug,
                    doc_type=doc_type,
                    compress=compress,
                    requirement_length=len(requirement or ""),
                )
        except Exception as e:
            print(f"Failed to save streamed result to DB: {e}")
