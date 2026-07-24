from __future__ import annotations

from typing import Any

from .case_access import case_flat_text
from .postprocess_priority_config import (
    p1_keywords as _cfg_p1_keywords,
    p2_keywords as _cfg_p2_keywords,
)
from .result_postprocess_priority_rules import (
    _contains_any,
    _normalize_existing_priority,
)


def _build_priority_decision(
    *,
    priority_final: str | None,
    decision_state: str,
    decision_source: str,
    confidence: str,
    conflict_reason: str = "",
    resolution_reason: str = "",
) -> dict[str, Any]:
    final_value = str(priority_final or "").strip().upper()
    normalized_final = final_value if final_value in {"P0", "P1", "P2"} else None
    return {
        "priority_final": normalized_final,
        "priority_decision_state": str(decision_state or "undetermined"),
        "priority_decision_source": str(decision_source or "insufficient_evidence"),
        "priority_confidence": str(confidence or "low"),
        "priority_conflict_reason": str(conflict_reason or ""),
        "priority_resolution_reason": str(resolution_reason or ""),
    }


def _resolve_priority_conflict_to_final(
    *,
    normalized_model: str,
    suggested_priority: str,
    score_result: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    normalized_model = _normalize_existing_priority(normalized_model)
    suggested_priority = _normalize_existing_priority(suggested_priority)
    pair = {normalized_model, suggested_priority}

    case_text = case_flat_text(
        case,
        fields=("description", "test_module", "expected_result", "test_input", "steps"),
        separator=" ",
        lower=True,
    )
    reasons = [str(item) for item in (score_result.get("reasons") or [])]
    guards = dict(score_result.get("guards") or {})
    case_level_hard_guard = bool(score_result.get("case_level_hard_guard"))
    if not case_level_hard_guard:
        case_level_hard_guard = any(
            bool(guards.get(key))
            for key in (
                "main_workflow_blocking",
                "workflow_blocking",
                "severe_data_risk",
                "severe_security_risk",
                "case_level_release_blocking",
            )
        )
    p1_keywords = _cfg_p1_keywords()
    p2_keywords = _cfg_p2_keywords()
    explicit_low_value = any(
        reason in reasons
        for reason in (
            "boundary_or_low_risk_validation",
            "long_tail_or_supplemental",
            "non_critical_perf_or_ui",
            "completeness_only",
            "structural_p2_low_value_signal",
            "p2_cap_no_coverage_gain_without_hard_guard",
            "p2_cap_low_risk_only_covered_rules",
            "p2_cap_display_mapping_scenario",
        )
    ) or bool(score_result.get("structural_p2_signals"))
    positive_p1_evidence = _has_positive_p1_evidence(score_result, reasons)
    has_p1_keyword = _contains_any(case_text, p1_keywords)
    has_p2_keyword = _contains_any(case_text, p2_keywords)
    conflict_reason = f"model={normalized_model},suggested={suggested_priority}"

    if pair == {"P0", "P2"}:
        if case_level_hard_guard:
            return _build_priority_decision(
                priority_final="P0",
                decision_state="conflict_resolved",
                decision_source="conflict_resolved_by_high_risk_business_rule",
                confidence="medium",
                conflict_reason=conflict_reason,
                resolution_reason="explicit_structured_high_risk_guard",
            )
        if positive_p1_evidence or has_p1_keyword:
            return _build_priority_decision(
                priority_final="P1",
                decision_state="conflict_resolved",
                decision_source="conflict_resolved_by_core_business_rule",
                confidence="medium",
                conflict_reason=conflict_reason,
                resolution_reason="core_business_evidence",
            )
        if explicit_low_value or has_p2_keyword:
            return _build_priority_decision(
                priority_final="P2",
                decision_state="conflict_resolved",
                decision_source="conflict_resolved_by_non_blocking_experience_rule",
                confidence="medium",
                conflict_reason=conflict_reason,
                resolution_reason="explicit_low_value_or_experience_case",
            )
        return _build_priority_decision(
            priority_final="P1",
            decision_state="conflict_resolved",
            decision_source="conflict_resolved_by_conservative_middle_priority",
            confidence="medium",
            conflict_reason=conflict_reason,
            resolution_reason="conservative_middle_priority",
        )

    if pair == {"P1", "P2"}:
        if positive_p1_evidence or has_p1_keyword or case_level_hard_guard:
            return _build_priority_decision(
                priority_final="P1",
                decision_state="conflict_resolved",
                decision_source="conflict_resolved_by_core_business_rule",
                confidence="medium",
                conflict_reason=conflict_reason,
                resolution_reason="core_business_evidence",
            )
        if explicit_low_value or has_p2_keyword:
            return _build_priority_decision(
                priority_final="P2",
                decision_state="conflict_resolved",
                decision_source="conflict_resolved_by_non_blocking_experience_rule",
                confidence="medium",
                conflict_reason=conflict_reason,
                resolution_reason="explicit_low_value_or_experience_case",
            )
        return _build_priority_decision(
            priority_final="P2",
            decision_state="conflict_resolved",
            decision_source="conflict_resolved_by_insufficient_positive_evidence",
            confidence="medium",
            conflict_reason=conflict_reason,
            resolution_reason="insufficient_positive_evidence",
        )

    return _build_priority_decision(
        priority_final=suggested_priority,
        decision_state="decided",
        decision_source="semantic_fallback",
        confidence="medium",
        conflict_reason=conflict_reason,
        resolution_reason="default_semantic_fallback",
    )

def _should_uplift_to_p1(case_meta: dict[str, Any]) -> tuple[bool, str, str, int]:
    """
    中文注释：P1 uplift 判定，仅用于 P2->P1 提档。
    返回: (是否提升, 原因文案, uplift_source, bonus_score)
    """
    meta = dict(case_meta or {})
    core_rule_hits = [str(x) for x in (meta.get("core_rule_hits") or []) if str(x).strip()]
    missing_rule_hits = [str(x) for x in (meta.get("missing_rule_hits") or []) if str(x).strip()]
    unique_coverage_hits = [str(x) for x in (meta.get("unique_coverage_hits") or []) if str(x).strip()]
    reasons = [str(x) for x in (meta.get("reasons") or [])]
    rule_risk_reasons = [str(x).strip().lower() for x in (meta.get("rule_risk_reasons") or []) if str(x).strip()]
    focus_score = int(meta.get("focus_score") or 0)
    coverage_gain_score = int(meta.get("coverage_gain_score") or 0)
    structural_p2_signals = bool(meta.get("structural_p2_signals"))
    low_risk_only_covered = bool(meta.get("low_risk_only_covered"))
    ui_like_case = bool(meta.get("ui_like_case"))
    cross_page_flow_hit = bool(meta.get("cross_page_flow_hit"))
    state_transition_hit = bool(meta.get("state_transition_hit"))
    preferred_pattern_hit = bool(meta.get("preferred_pattern_hit"))
    reuse_risk_hit = bool(meta.get("reuse_risk_hit"))

    # 防止乱升
    if structural_p2_signals:
        return False, "structural_p2_signals", "blocked_structural", 0
    if ui_like_case:
        return False, "ui_like_case", "blocked_ui_like", 0
    if low_risk_only_covered and coverage_gain_score <= 0:
        return False, "low_risk_only_no_gain", "blocked_low_risk_only", 0

    main_workflow_hit = "main_workflow_hit" in reasons
    cross_page_flow_hit = bool(cross_page_flow_hit or ("cross_page_flow_hit" in reasons))
    state_transition_hit = bool(state_transition_hit or ("state_transition_hit" in reasons))
    preferred_pattern_hit = bool(preferred_pattern_hit or ("preferred_pattern_hit" in reasons))
    has_high_risk_signal = "high" in rule_risk_reasons

    # uplift 触发条件
    if core_rule_hits:
        return True, "core_rule_hits", "core_rule", 8
    if missing_rule_hits:
        return True, "missing_rule_hits", "missing_rule", 8
    if unique_coverage_hits and has_high_risk_signal:
        return True, "unique_coverage_high_risk", "coverage_gain", 8
    if main_workflow_hit and focus_score >= 1:
        return True, "workflow_focus_relaxed", "workflow", 6
    if main_workflow_hit and preferred_pattern_hit:
        return True, "workflow_preferred_pattern", "workflow", 6
    if cross_page_flow_hit:
        return True, "cross_page_flow", "workflow", 6
    if state_transition_hit and focus_score >= 1:
        return True, "state_transition_focus", "workflow", 6
    if preferred_pattern_hit and (main_workflow_hit or cross_page_flow_hit or state_transition_hit):
        return True, "preferred_pattern_flow", "workflow", 6
    if reuse_risk_hit and (focus_score >= 1 or main_workflow_hit or state_transition_hit):
        return True, "reuse_risk_flow", "workflow", 6
    if coverage_gain_score >= 8:
        return True, "coverage_gain", "coverage_gain", 6

    return False, "", "", 0


def _has_positive_p1_evidence(score_result: dict[str, Any], reasons: list[str] | None = None) -> bool:
    """Return true only for coverage/workflow evidence strong enough to keep P1.

    A low-risk unique coverage hit alone means "new case", not "important case".
    Without this guard, almost every selected display/config case can keep model P1.
    """
    reasons = [str(item) for item in (reasons or score_result.get("reasons") or [])]
    missing_rule_hits = [str(item) for item in (score_result.get("missing_rule_hits") or []) if str(item).strip()]
    core_rule_hits = [str(item) for item in (score_result.get("core_rule_hits") or []) if str(item).strip()]
    unique_coverage_hits = [str(item) for item in (score_result.get("unique_coverage_hits") or []) if str(item).strip()]
    coverage_gain_score = int(score_result.get("coverage_gain_score") or 0)
    low_risk_only_covered = bool(score_result.get("low_risk_only_covered"))
    structural_p2_signals = bool(score_result.get("structural_p2_signals"))
    rule_risk_reasons = {
        str(item).strip().lower()
        for item in (score_result.get("rule_risk_reasons") or [])
        if str(item).strip()
    }
    high_risk_coverage = bool(rule_risk_reasons.intersection({"high", "critical", "release_blocking"}))

    if missing_rule_hits or core_rule_hits:
        return not (low_risk_only_covered and coverage_gain_score <= 0 and not high_risk_coverage)
    if unique_coverage_hits:
        return bool(high_risk_coverage or coverage_gain_score >= 8)
    if coverage_gain_score >= 8 and not (structural_p2_signals or low_risk_only_covered):
        return True
    return any(
        reason in reasons
        for reason in (
            "important_non_blocking_flow",
            "high_frequency_main_flow",
            "main_workflow_hit",
            "cross_page_flow_hit",
            "state_transition_hit",
            "reuse_risk_hit",
        )
    )


def _normalize_score_result_for_debug_and_resolve(score_result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(score_result or {})

    covered_rule_ids = [str(item) for item in (normalized.get("covered_rule_ids") or []) if str(item).strip()]
    missing_rule_hits = [str(item) for item in (normalized.get("missing_rule_hits") or []) if str(item).strip()]
    core_rule_hits = [str(item) for item in (normalized.get("core_rule_hits") or []) if str(item).strip()]
    unique_coverage_hits = [str(item) for item in (normalized.get("unique_coverage_hits") or []) if str(item).strip()]
    reasons = [str(item) for item in (normalized.get("reasons") or []) if str(item).strip()]

    covered_rule_ids = list(dict.fromkeys(covered_rule_ids))
    covered_set = set(covered_rule_ids)
    missing_rule_hits = [rid for rid in dict.fromkeys(missing_rule_hits) if rid not in covered_set]
    core_rule_hits = [rid for rid in dict.fromkeys(core_rule_hits) if rid in covered_set]
    unique_coverage_hits = [rid for rid in dict.fromkeys(unique_coverage_hits) if rid in covered_set]

    has_coverage_signals = bool(covered_rule_ids or missing_rule_hits or core_rule_hits or unique_coverage_hits)
    if not has_coverage_signals:
        drop_reasons = {
            "no_coverage_information_gain",
            "p2_cap_no_coverage_gain_without_hard_guard",
            "redundant_covered_normal_rules",
            "p2_cap_low_risk_only_covered_rules",
            "low_risk_only_covered_rules_penalty",
            "p2_capped",
        }
        reasons = [reason for reason in reasons if reason not in drop_reasons]
        normalized["coverage_gain_score"] = max(0, int(normalized.get("coverage_gain_score") or 0))
        normalized["p2_cap"] = False
        normalized["p2_cap_exempted"] = False
        normalized["p2_cap_exemption_reasons"] = []
        normalized["low_risk_only_covered"] = False

    normalized["covered_rule_ids"] = covered_rule_ids
    normalized["missing_rule_hits"] = missing_rule_hits
    normalized["core_rule_hits"] = core_rule_hits
    normalized["unique_coverage_hits"] = unique_coverage_hits
    normalized["reasons"] = reasons
    return normalized
