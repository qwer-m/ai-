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

def score_case_priority(
    case: dict[str, Any],
    coverage_context: dict[str, Any] | None = None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = _extract_case_text(case)
    reasons: list[str] = []
    score = 0
    coverage_gain_score = 0

    def _add(reason: str, delta: int) -> None:
        nonlocal score
        score += int(delta)
        if reason not in reasons:
            reasons.append(reason)

    def _focus_score(value: str) -> int:
        lowered = str(value or "").lower()
        points = 0
        if _contains_any(lowered, ("边界", "最大", "最小", "临界", "boundary", "max", "min")):
            points += 2
        if _contains_any(lowered, ("异常", "失败", "错误", "拒绝", "exception", "error", "invalid")):
            points += 2
        if _contains_any(lowered, ("状态", "流转", "state", "transition")):
            points += 1
        return points

    def _is_ui_like_case(value: str) -> bool:
        lowered = str(value or "").lower()
        ui_keywords = (
            "入口",
            "图标",
            "按钮",
            "展示",
            "布局",
            "占位",
            "置灰",
            "文案",
            "显示",
            "隐藏",
            "可点击",
            "样式",
            "icon",
            "button",
            "display",
            "layout",
            "placeholder",
            "style",
        )
        if not _contains_any(lowered, ui_keywords):
            return False
        risk_words = ("异常", "失败", "错误", "权限", "安全", "并发", "性能", "exception", "error", "security", "permission")
        if _contains_any(lowered, risk_words):
            return False
        return True

    main_workflow_hit = _contains_any(
        text,
        (
            "登录",
            "下单",
            "支付",
            "提交",
            "保存",
            "发布",
            "审批",
            "核心查询",
            "核心流程",
            "checkout", "order", "payment", "submit", "save", "publish", "approve", "login",
        ),
    )
    blocking_hit = _contains_any(
        text,
        (
            "阻断",
            "无法继续",
            "不可继续",
            "无法提交",
            "无法保存",
            "流程中断",
            "系统不可用",
            "阻塞",
            "blocked", "blocker", "cannot continue", "service unavailable",
        ),
    )
    severe_data_risk = _contains_any(
        text,
        (
            "数据丢失",
            "数据错误",
            "状态污染",
            "脏数据",
            "金额错误",
            "重复扣款",
            "错账",
            "账务错误",
            "data loss", "data corruption", "amount mismatch", "state corruption",
        ),
    )
    severe_security_risk = _contains_any(
        text,
        (
            "越权",
            "权限绕过",
            "提权",
            "敏感数据泄露",
            "认证绕过",
            "鉴权绕过",
            "sql injection",
            "xss",
            "csrf", "auth bypass", "privilege escalation", "security breach",
        ),
    )
    case_level_release_blocking = _contains_case_level_release_blocking(text)

    if main_workflow_hit:
        _add("main_workflow_hit", 25)
    if blocking_hit:
        _add("workflow_blocking", 25)
    if severe_data_risk:
        _add("severe_data_risk", 20)
    if severe_security_risk:
        _add("severe_security_risk", 20)
    if case_level_release_blocking:
        _add("case_level_release_blocking", 20)

    important_non_blocking = (
        _contains_any(text, ("重要", "关键功能", "重要流程", "核心功能", "important", "critical flow"))
        and not blocking_hit
    )
    high_frequency_main_flow = _contains_any(
        text,
        ("高频", "频繁", "常用", "daily", "high frequency", "frequent"),
    )
    usability_degraded = _contains_any(
        text,
        ("体验劣化", "体验差", "功能异常但可用", "可继续", "degraded", "still usable", "usability"),
    )
    important_regression = _contains_any(text, ("重要回归", "关键回归", "regression", "回归验证", "历史缺陷复测"))

    if important_non_blocking:
        _add("important_non_blocking_flow", 12)
    if high_frequency_main_flow:
        _add("high_frequency_main_flow", 10)
    if usability_degraded:
        _add("degraded_but_usable", 10)
    if important_regression:
        _add("important_regression_validation", 8)

    focus_score = int(_focus_score(text))
    ui_like_case = bool(_is_ui_like_case(text))

    if _contains_any(
        text,
        (
            "边界",
            "最大",
            "最小",
            "临界",
            "文案",
            "格式",
            "普通校验",
            "弱异常",
            "boundary",
            "format",
            "validation",
        ),
    ):
        _add("boundary_or_low_risk_validation", -10)
    if _contains_any(
        text,
        ("长尾", "低频", "补充场景", "补充异常", "非常规", "long-tail", "rare", "supplemental"),
    ):
        _add("long_tail_or_supplemental", -12)
    if _contains_any(
        text,
        (
            "轻微",
            "非关键性能",
            "非核心兼容",
            "轻微ui",
            "样式问题",
            "minor ui",
            "cosmetic",
            "non-critical performance", "non-core compatibility",
        ),
    ):
        _add("non_critical_perf_or_ui", -15)
    if _contains_any(
        text,
        ("仅补全", "完整性检查", "覆盖率补齐", "completeness-only", "for completeness"),
    ):
        _add("completeness_only", -12)

    effective_coverage_context = (
        coverage_context
        if isinstance(coverage_context, dict) and isinstance(coverage_context.get("case_rule_map"), dict)
        else _build_priority_coverage_context([case], coverage_context, rule_diagnostics)
    )

    signature = _priority_case_signature(case)
    case_rule_info = dict((effective_coverage_context.get("case_rule_map") or {}).get(signature) or {})
    rule_meta_map = dict(effective_coverage_context.get("rule_meta") or {})

    covered_rule_ids = [str(item) for item in (case_rule_info.get("covered_rule_ids") or []) if str(item).strip()]
    missing_rule_hits = [str(item) for item in (case_rule_info.get("missing_rule_hits") or []) if str(item).strip()]
    core_rule_hits = [str(item) for item in (case_rule_info.get("core_rule_hits") or []) if str(item).strip()]
    unique_coverage_hits = [str(item) for item in (case_rule_info.get("unique_coverage_hits") or []) if str(item).strip()]
    rule_risk_reasons = [str(item) for item in (case_rule_info.get("rule_risk_reasons") or []) if str(item).strip()]

    # Keep rule-hit signals internally consistent: a rule cannot be both covered and missing for one case.
    covered_rule_ids = list(dict.fromkeys(covered_rule_ids))
    covered_set = set(covered_rule_ids)
    missing_rule_hits = [rid for rid in dict.fromkeys(missing_rule_hits) if rid not in covered_set]
    core_rule_hits = [rid for rid in dict.fromkeys(core_rule_hits) if rid in covered_set]
    unique_coverage_hits = [rid for rid in dict.fromkeys(unique_coverage_hits) if rid in covered_set]

    if core_rule_hits:
        _add("core_workflow_rule_hit", 8)
        coverage_gain_score += 8

    release_rule_hits = [rid for rid in covered_rule_ids if bool((rule_meta_map.get(rid) or {}).get("rule_is_release_blocking"))]
    if release_rule_hits:
        if case_level_release_blocking or blocking_hit:
            _add("release_blocking_rule_hit", 12)
            coverage_gain_score += 12
        else:
            _add("release_blocking_rule_hit_rule_only", 4)
            coverage_gain_score += 4

    security_or_data_hits = [
        rid
        for rid in covered_rule_ids
        if bool((rule_meta_map.get(rid) or {}).get("rule_is_security_sensitive"))
        or bool((rule_meta_map.get(rid) or {}).get("rule_is_data_critical"))
    ]
    if security_or_data_hits:
        _add("security_or_data_critical_rule_hit", 15)
        coverage_gain_score += 15

    missing_critical_hits = [
        rid
        for rid in missing_rule_hits
        if bool((rule_meta_map.get(rid) or {}).get("rule_is_core_workflow"))
        or bool((rule_meta_map.get(rid) or {}).get("rule_is_release_blocking"))
        or bool((rule_meta_map.get(rid) or {}).get("rule_is_security_sensitive"))
        or bool((rule_meta_map.get(rid) or {}).get("rule_is_data_critical"))
    ]
    if missing_critical_hits:
        _add("missing_critical_rule_hit", 10)
        coverage_gain_score += 10

    unique_critical_hits = [
        rid
        for rid in unique_coverage_hits
        if bool((rule_meta_map.get(rid) or {}).get("rule_is_core_workflow"))
        or bool((rule_meta_map.get(rid) or {}).get("rule_is_release_blocking"))
        or bool((rule_meta_map.get(rid) or {}).get("rule_is_security_sensitive"))
        or bool((rule_meta_map.get(rid) or {}).get("rule_is_data_critical"))
    ]
    if unique_critical_hits:
        _add("unique_critical_rule_coverage", 5)
        coverage_gain_score += 5

    if covered_rule_ids:
        all_covered_normal = all(
            str((rule_meta_map.get(rid) or {}).get("rule_coverage_status")) == "covered"
            and str((rule_meta_map.get(rid) or {}).get("rule_risk_level") or "low") == "low"
            for rid in covered_rule_ids
        )
        if all_covered_normal and not unique_coverage_hits:
            _add("redundant_covered_normal_rules", -10)
            coverage_gain_score -= 10

        all_low_risk_supplemental = all(
            str((rule_meta_map.get(rid) or {}).get("rule_risk_level") or "low") == "low"
            for rid in covered_rule_ids
        ) and _contains_any(text, ("边界", "格式", "文案", "long-tail", "supplemental", "补充", "低风险"))
        if all_low_risk_supplemental:
            _add("low_risk_supplemental_rule_only", -8)
            coverage_gain_score -= 8

    structural_p2_signals = _contains_any(
        text,
        (
            "supplemental",
            "completeness-only",
            "long-tail",
            "minor ui",
            "cosmetic",
            "compatibility",
            "result display",
            "statistics display",
            "badge",
            "color mapping",
            "icon mapping",
            "state display",
            "placeholder",
            "disabled-state",
            "grey-state",
            "empty-slot",
            "星级分段",
            "颜色映射",
            "图标映射",
            "结果页统计",
            "置灰",
            "占位",
            "展示态",
            "补充性",
            "低频边界分档",
        ),
    )
    if structural_p2_signals:
        _add("structural_p2_low_value_signal", -10)

    has_coverage_signals = bool(covered_rule_ids or missing_rule_hits or core_rule_hits or unique_coverage_hits)
    # 覆盖增益判定放宽：核心规则命中 / 唯一覆盖命中也算信息增益，避免被误判为“无增益”。
    has_info_gain = bool(
        missing_rule_hits
        or core_rule_hits
        or unique_coverage_hits
        or unique_critical_hits
        or missing_critical_hits
    )
    if has_coverage_signals and not has_info_gain:
        _add("no_coverage_information_gain", -10)
        coverage_gain_score -= 10

    low_risk_only_covered = bool(covered_rule_ids) and bool(not missing_rule_hits) and all(
        str((rule_meta_map.get(rid) or {}).get("rule_coverage_status")) == "covered"
        and str((rule_meta_map.get(rid) or {}).get("rule_risk_level") or "low") == "low"
        for rid in covered_rule_ids
    )
    p2_cap = False
    p2_cap_exempted = False
    p2_cap_exemption_reasons: list[str] = []

    case_level_hard_guard = bool(
        (main_workflow_hit and blocking_hit)
        or severe_data_risk
        or severe_security_risk
        or case_level_release_blocking
    )

    # 覆盖价值豁免：命中缺失/核心/唯一覆盖或主流程+核心规则时，不直接触发 P2 封顶。
    coverage_value_exempt = bool(
        missing_rule_hits
        or core_rule_hits
        or unique_coverage_hits
        or (main_workflow_hit and bool(core_rule_hits))
    )

    if has_coverage_signals and coverage_gain_score <= 0 and not case_level_hard_guard:
        if coverage_value_exempt:
            p2_cap_exempted = True
            p2_cap_exemption_reasons.append("exempt_non_positive_gain_due_to_coverage_value")
            _add("coverage_gain_non_positive", -6)
        else:
            _add("p2_cap_no_coverage_gain_without_hard_guard", -12)
            p2_cap = True

    if low_risk_only_covered and not missing_rule_hits:
        if coverage_value_exempt:
            p2_cap_exempted = True
            p2_cap_exemption_reasons.append("exempt_low_risk_only_due_to_coverage_value")
            _add("low_risk_only_covered_rules_penalty", -8)
            coverage_gain_score -= 8
        else:
            _add("p2_cap_low_risk_only_covered_rules", -12)
            coverage_gain_score -= 12
            p2_cap = True

    if structural_p2_signals and not case_level_hard_guard:
        if coverage_value_exempt:
            p2_cap_exempted = True
            p2_cap_exemption_reasons.append("exempt_structural_display_due_to_coverage_value")
            _add("structural_display_mapping_penalty", -6)
        else:
            _add("p2_cap_display_mapping_scenario", -10)
            p2_cap = True

    guards = {
        "main_workflow_blocking": bool(main_workflow_hit and blocking_hit),
        "workflow_blocking": bool(main_workflow_hit and blocking_hit),
        "severe_data_risk": bool(severe_data_risk),
        "severe_security_risk": bool(severe_security_risk),
        "case_level_release_blocking": bool(case_level_release_blocking),
        "release_blocking": bool(case_level_release_blocking),
        "rule_level_release_blocking_hit": bool(release_rule_hits),
        "case_level_hard_guard": bool(case_level_hard_guard),
    }
    guard_hit = bool(case_level_hard_guard)

    score = max(0, min(100, int(score)))
    if score >= 70:
        suggested = "P0" if guard_hit else "P1"
        if not guard_hit:
            reasons.append("p0_guard_not_met")
    elif score >= 35:
        suggested = "P1"
    else:
        suggested = "P2"

    if not guard_hit and "no_release_blocking_guard" not in reasons:
        reasons.append("no_release_blocking_guard")

    if p2_cap and not case_level_hard_guard:
        suggested = "P2"
        reasons.append("p2_capped")

    return {
        "priority_score": int(score),
        "suggested_priority": suggested,
        "guards": guards,
        "reasons": reasons,
        "focus_score": int(focus_score),
        "ui_like_case": bool(ui_like_case),
        "covered_rule_ids": covered_rule_ids,
        "case_covering_rules": covered_rule_ids,
        "case_unique_rule_hits_count": int(len(unique_coverage_hits)),
        "case_missing_rule_hits_count": int(len(missing_rule_hits)),
        "case_core_rule_hits_count": int(len(core_rule_hits)),
        "missing_rule_hits": missing_rule_hits,
        "core_rule_hits": core_rule_hits,
        "unique_coverage_hits": unique_coverage_hits,
        "coverage_gain_score": int(coverage_gain_score),
        "rule_risk_reasons": rule_risk_reasons,
        "p2_cap": bool(p2_cap),
        "p2_cap_exempted": bool(p2_cap_exempted),
        "p2_cap_exemption_reasons": p2_cap_exemption_reasons,
        "coverage_value_exempt": bool(coverage_value_exempt),
        "low_risk_only_covered": bool(low_risk_only_covered),
        "structural_p2_signals": bool(structural_p2_signals),
        "case_level_hard_guard": bool(case_level_hard_guard),
        "case_level_release_blocking": bool(case_level_release_blocking),
    }
