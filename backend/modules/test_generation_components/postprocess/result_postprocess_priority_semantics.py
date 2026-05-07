from __future__ import annotations

from typing import Any

from .result_postprocess_priority_rules import (
    _build_priority_coverage_context,
    _contains_any,
    _contains_case_level_release_blocking,
    _extract_case_text,
    _normalize_existing_priority,
    _priority_case_signature,
)

from modules.test_generation_components.postprocess.result_postprocess_priority_semantics_split_helpers import (
    score_case_priority,
)

PRIORITY_SEMANTICS_REVISION = "2026-04-28-r7"

_UNCERTAIN_REQUIREMENT_SIGNALS = (
    "需教研确认",
    "需要讨论",
    "本期可以不做",
    "本期可以不要",
    "暂不支持",
    "模型不支持",
    "小学没定位模型",
    "由教研提供",
    "待确认",
    "待讨论",
    "to be confirmed",
    "need discussion",
    "optional this phase",
    "model not supported",
)


def _contains_strong_p0_signal(case_text: str) -> bool:
    text = str(case_text or "").lower()
    payment_gate = _contains_any(
        text,
        ("未付费", "付费拦截", "付费提示", "paywall", "payment gate", "payment blocked", "subscribe"),
    )
    ai_scoring = _contains_any(
        text,
        ("ai判分", "智能判分", "ocr", "ai scoring", "auto score", "scoring"),
    )
    wrong_collection = _contains_any(
        text,
        ("错题归集", "错题本", "错题", "wrong question", "error collection"),
    )
    week_boundary_or_makeup = _contains_any(
        text,
        ("周次切换", "教学周", "周日24", "时间边界", "补做期", "历史周", "补做规则", "week switch", "history week"),
    )
    submit_report_closure = _contains_any(
        text,
        ("提交全部", "查看学习报告", "学习报告", "submit all", "view report", "learning report"),
    )
    return bool(
        payment_gate
        or ai_scoring
        or wrong_collection
        or week_boundary_or_makeup
        or submit_report_closure
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

    case_text = " ".join(
        [
            str(case.get("description") or ""),
            str(case.get("test_module") or ""),
            str(case.get("expected_result") or ""),
            str(case.get("test_input") or ""),
            " ".join(str(x) for x in (case.get("steps") or []) if str(x).strip())
            if isinstance(case.get("steps"), list)
            else "",
        ]
    ).lower()
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
    p0_keywords = (
        "paywall",
        "payment gate",
        "payment blocked",
        "permission denied",
        "unauthorized",
        "access denied",
        "data isolation",
        "隔离",
        "越权",
        "未授权",
        "权限",
        "付费",
        "阻断",
        "主流程",
        "闭环",
        "报告生成失败",
    )
    p1_keywords = (
        "掌握度",
        "错题数",
        "正确数",
        "统计",
        "计算",
        "筛选",
        "跳转",
        "联动",
        "更新",
        "报告",
        "习题本",
        "state transition",
        "cross page",
    )
    p2_keywords = (
        "流畅",
        "卡顿",
        "性能",
        "兼容",
        "文案",
        "提示",
        "展示",
        "缩放",
        "滑动",
        "ui",
        "display",
        "layout",
        "performance",
    )
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
    has_p0_keyword = _contains_any(case_text, p0_keywords)
    has_p1_keyword = _contains_any(case_text, p1_keywords)
    has_p2_keyword = _contains_any(case_text, p2_keywords)
    conflict_reason = f"model={normalized_model},suggested={suggested_priority}"

    if pair == {"P0", "P2"}:
        if case_level_hard_guard or has_p0_keyword:
            return _build_priority_decision(
                priority_final="P0",
                decision_state="conflict_resolved",
                decision_source="conflict_resolved_by_high_risk_business_rule",
                confidence="medium",
                conflict_reason=conflict_reason,
                resolution_reason="high_risk_guard_or_keyword",
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

def resolve_case_priority_decision(
    model_priority: str,
    score_result: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    normalized_model = _normalize_existing_priority(model_priority)
    case_text = _extract_case_text(case if isinstance(case, dict) else {})
    strong_p0_signal = _contains_strong_p0_signal(case_text)
    uncertain_requirement_hit = _contains_any(str(case_text or "").lower(), _UNCERTAIN_REQUIREMENT_SIGNALS)
    score = int(score_result.get("priority_score") or 0)
    bonus_score = int(score_result.get("bonus_score") or 0)
    p1_uplifted = bool(score_result.get("p1_uplifted"))
    effective_score = int(score + bonus_score) if p1_uplifted else score
    suggested_priority = _normalize_existing_priority(score_result.get("suggested_priority") or "P1")
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
    if strong_p0_signal:
        case_level_hard_guard = True
    p2_cap = bool(score_result.get("p2_cap"))
    coverage_value_exempt = bool(score_result.get("coverage_value_exempt"))
    missing_rule_hits = [str(item) for item in (score_result.get("missing_rule_hits") or []) if str(item).strip()]
    core_rule_hits = [str(item) for item in (score_result.get("core_rule_hits") or []) if str(item).strip()]
    unique_coverage_hits = [str(item) for item in (score_result.get("unique_coverage_hits") or []) if str(item).strip()]
    coverage_gain_score = int(score_result.get("coverage_gain_score") or 0)
    low_risk_only_covered = bool(score_result.get("low_risk_only_covered"))
    structural_p2_signals = bool(score_result.get("structural_p2_signals"))
    reasons = [str(item) for item in (score_result.get("reasons") or [])]

    if uncertain_requirement_hit:
        return _build_priority_decision(
            priority_final="P2",
            decision_state="optional",
            decision_source="uncertain_requirement_guard",
            confidence="low",
        )

    if p2_cap and not case_level_hard_guard:
        return _build_priority_decision(
            priority_final="P2",
            decision_state="decided",
            decision_source="p2_cap_guard",
            confidence="high",
        )

    if normalized_model == "P0":
        p0_score_floor = 0 if strong_p0_signal else 70
        if (not case_level_hard_guard) or score < p0_score_floor:
            return _build_priority_decision(
                priority_final="P1",
                decision_state="decided",
                decision_source="model_p0_guard_downgrade",
                confidence="high",
            )

    if (
        normalized_model == "P2"
        and strong_p0_signal
        and case_level_hard_guard
        and not p2_cap
    ):
        return _build_priority_decision(
            priority_final="P0",
            decision_state="decided",
            decision_source="strong_p0_signal_guard",
            confidence="medium",
        )

    # Third calibration: security/data critical hits with mid score can move from P2 to P1.
    if (
        normalized_model == "P2"
        and effective_score >= 20
        and "security_or_data_critical_rule_hit" in reasons
        and coverage_value_exempt
        and coverage_gain_score > 0
        and not low_risk_only_covered
        and not structural_p2_signals
    ):
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="security_data_critical_promotion",
            confidence="medium",
        )

    # Fourth calibration: core-workflow-covered mid-score cases can move from P2 to P1.
    if (
        normalized_model == "P2"
        and effective_score >= 8
        and "core_workflow_rule_hit" in reasons
        and bool(core_rule_hits)
        and coverage_gain_score >= 8
        and not p2_cap
        and not low_risk_only_covered
        and not structural_p2_signals
    ):
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="core_workflow_promotion",
            confidence="medium",
        )

    # Second calibration: near-threshold + coverage-value cases can move from P2 to P1.
    if (
        normalized_model == "P2"
        and effective_score >= 30
        and coverage_value_exempt
        and bool(missing_rule_hits or core_rule_hits or unique_coverage_hits)
        and not low_risk_only_covered
        and not (structural_p2_signals and not (missing_rule_hits or core_rule_hits))
    ):
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="coverage_signal_promotion",
            confidence="medium",
        )
    if normalized_model == "P2" and effective_score >= 35:
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="score_threshold_promotion",
            confidence="medium",
        )
    if normalized_model == "P1" and case_level_hard_guard and score >= (0 if strong_p0_signal else 70):
        return _build_priority_decision(
            priority_final="P0",
            decision_state="decided",
            decision_source="hard_guard_promotion",
            confidence="high",
        )

    if suggested_priority == "P0" and not (case_level_hard_guard and score >= 70):
        suggested_priority = "P1"

    if normalized_model == suggested_priority:
        return _build_priority_decision(
            priority_final=normalized_model,
            decision_state="decided",
            decision_source="model_semantic_consistent",
            confidence="high",
        )

    pair = {normalized_model, suggested_priority}
    if pair == {"P0", "P1"}:
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="guarded_downgrade",
            confidence="medium",
        )
    if pair == {"P1", "P2"}:
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
        )
        positive_p1_evidence = _has_positive_p1_evidence(score_result, reasons)
        if normalized_model == "P1" and positive_p1_evidence and not explicit_low_value:
            return _build_priority_decision(
                priority_final="P1",
                decision_state="decided",
                decision_source="model_p1_with_positive_evidence",
                confidence="medium",
            )
        if normalized_model == "P1":
            return _resolve_priority_conflict_to_final(
                normalized_model=normalized_model,
                suggested_priority=suggested_priority,
                score_result=score_result,
                case=case,
            )
        return _build_priority_decision(
            priority_final="P2",
            decision_state="decided",
            decision_source="semantic_low_value",
            confidence="medium",
        )
    if pair == {"P0", "P2"}:
        return _resolve_priority_conflict_to_final(
            normalized_model=normalized_model,
            suggested_priority=suggested_priority,
            score_result=score_result,
            case=case,
        )
    return _build_priority_decision(
        priority_final=suggested_priority,
        decision_state="decided",
        decision_source="semantic_fallback",
        confidence="medium",
    )


def resolve_case_priority(model_priority: str, score_result: dict[str, Any], case: dict[str, Any]) -> str:
    decision = resolve_case_priority_decision(model_priority, score_result, case)
    final_priority = str(decision.get("priority_final") or "").strip().upper()
    if final_priority in {"P0", "P1", "P2"}:
        return final_priority
    return _normalize_existing_priority(model_priority)

def apply_priority_semantics_to_case(
    case: dict[str, Any],
    *,
    attach_debug: bool = False,
    coverage_context: dict[str, Any] | None = None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    input_priority = str(case.get("priority") or "").strip()
    model_priority = str(
        case.get("model_priority_current")
        or case.get("model_priority")
        or input_priority
    ).strip()
    normalized_model_priority = _normalize_existing_priority(model_priority)
    normalized_input_priority = _normalize_existing_priority(input_priority)
    if not str(case.get("model_priority_current") or "").strip():
        case["model_priority_current"] = normalized_model_priority
    if not str(case.get("model_priority") or "").strip():
        case["model_priority"] = normalized_model_priority
    if not str(case.get("legacy_priority") or "").strip():
        case["legacy_priority"] = normalized_input_priority
    score_result = score_case_priority(
        case,
        coverage_context=coverage_context,
        rule_diagnostics=rule_diagnostics,
    )
    score_result = _normalize_score_result_for_debug_and_resolve(score_result)
    p1_uplifted = False
    p1_uplift_reason = ""
    uplift_source = ""
    bonus_score = 0
    if normalized_model_priority == "P2":
        p1_uplifted, p1_uplift_reason, uplift_source, bonus_score = _should_uplift_to_p1(score_result)
        if p1_uplifted and bool(score_result.get("p2_cap")):
            p1_uplifted = False
            p1_uplift_reason = "p2_cap_blocked"
            uplift_source = "blocked_p2_cap"
            bonus_score = 0
    score_result["p1_uplifted"] = p1_uplifted
    score_result["p1_uplift_reason"] = p1_uplift_reason
    score_result["uplift_source"] = uplift_source
    score_result["bonus_score"] = bonus_score
    decision = resolve_case_priority_decision(normalized_model_priority, score_result, case)
    final_priority = str(decision.get("priority_final") or "").strip().upper()
    if final_priority in {"P0", "P1", "P2"}:
        case["priority"] = final_priority
    else:
        case["priority"] = normalized_model_priority
    case["priority_final"] = decision.get("priority_final")
    case["priority_decision_state"] = str(decision.get("priority_decision_state") or "undetermined")
    case["priority_decision_source"] = str(decision.get("priority_decision_source") or "insufficient_evidence")
    case["priority_confidence"] = str(decision.get("priority_confidence") or "low")
    case["priority_conflict_reason"] = str(decision.get("priority_conflict_reason") or "")
    case["priority_resolution_reason"] = str(decision.get("priority_resolution_reason") or "")

    if attach_debug:
        meta = case.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        meta["priority_debug"] = {
            "model_priority": model_priority,
            "normalized_model_priority": normalized_model_priority,
            "priority_score": int(score_result.get("priority_score") or 0),
            "suggested_priority": str(score_result.get("suggested_priority") or "P2"),
            "final_priority": case.get("priority_final"),
            "priority_decision_state": str(decision.get("priority_decision_state") or "undetermined"),
            "priority_decision_source": str(decision.get("priority_decision_source") or "insufficient_evidence"),
            "priority_confidence": str(decision.get("priority_confidence") or "low"),
            "priority_conflict_reason": str(decision.get("priority_conflict_reason") or ""),
            "priority_resolution_reason": str(decision.get("priority_resolution_reason") or ""),
            "focus_score": int(score_result.get("focus_score") or 0),
            "ui_like_case": bool(score_result.get("ui_like_case")),
            "cross_page_flow_hit": bool(score_result.get("cross_page_flow_hit")),
            "state_transition_hit": bool(score_result.get("state_transition_hit")),
            "preferred_pattern_hit": bool(score_result.get("preferred_pattern_hit")),
            "reuse_risk_hit": bool(score_result.get("reuse_risk_hit")),
            "priority_reasons": [str(item) for item in (score_result.get("reasons") or [])],
            "priority_guards": dict(score_result.get("guards") or {}),
            "covered_rule_ids": [str(item) for item in (score_result.get("covered_rule_ids") or [])],
            "missing_rule_hits": [str(item) for item in (score_result.get("missing_rule_hits") or [])],
            "core_rule_hits": [str(item) for item in (score_result.get("core_rule_hits") or [])],
            "unique_coverage_hits": [str(item) for item in (score_result.get("unique_coverage_hits") or [])],
            "coverage_gain_score": int(score_result.get("coverage_gain_score") or 0),
            "rule_risk_reasons": [str(item) for item in (score_result.get("rule_risk_reasons") or [])],
            "case_level_release_blocking": bool(score_result.get("case_level_release_blocking")),
            "case_level_hard_guard": bool(score_result.get("case_level_hard_guard")),
            "p1_uplifted": bool(score_result.get("p1_uplifted")),
            "p1_uplift_reason": str(score_result.get("p1_uplift_reason") or ""),
            "uplift_source": str(score_result.get("uplift_source") or ""),
            "bonus_score": int(score_result.get("bonus_score") or 0),
            "p2_cap": bool(score_result.get("p2_cap")),
            "p2_cap_exempted": bool(score_result.get("p2_cap_exempted")),
            "p2_cap_exemption_reasons": [str(item) for item in (score_result.get("p2_cap_exemption_reasons") or [])],
            "coverage_value_exempt": bool(score_result.get("coverage_value_exempt")),
            "low_risk_only_covered": bool(score_result.get("low_risk_only_covered")),
            "structural_p2_signals": bool(score_result.get("structural_p2_signals")),
            "semantics_revision": PRIORITY_SEMANTICS_REVISION,
        }
        case["meta"] = meta
    return case


def apply_priority_semantics_to_cases(
    cases: list[dict[str, Any]],
    *,
    attach_debug: bool = False,
    coverage_context: dict[str, Any] | None = None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    effective_coverage_context = _build_priority_coverage_context(
        [item for item in cases if isinstance(item, dict)],
        coverage_context=coverage_context,
        rule_diagnostics=rule_diagnostics,
    )
    output: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        normalized = dict(case)
        output.append(
            apply_priority_semantics_to_case(
                normalized,
                attach_debug=attach_debug,
                coverage_context=effective_coverage_context,
                rule_diagnostics=rule_diagnostics,
            )
        )
    return output

