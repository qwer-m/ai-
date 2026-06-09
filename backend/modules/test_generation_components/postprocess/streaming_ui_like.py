from __future__ import annotations

from typing import Any


def is_ui_like_case(case: dict[str, Any], score_profile: dict[str, Any]) -> bool:
    steps = case.get("steps", [])
    step_count = len([x for x in steps if str(x or "").strip()]) if isinstance(steps, list) else 0
    step_text = " ".join([str(x) for x in steps if str(x or "").strip()]).lower() if isinstance(steps, list) else ""
    text = " ".join(
        [
            str(case.get("description") or ""),
            str(case.get("expected_result") or ""),
            str(case.get("test_input") or ""),
            " ".join([str(x) for x in steps]) if isinstance(steps, list) else "",
        ]
    ).lower()
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
        "列表",
        "表格",
        "排序",
        "筛选",
        "icon",
        "button",
        "display",
        "layout",
        "placeholder",
        "style",
        "list",
        "table",
        "column",
        "label",
        "sort",
        "filter",
    )
    ui_hit_count = sum(1 for keyword in ui_keywords if keyword in text)
    if ui_hit_count <= 0:
        return False
    pseudo_flow_tokens = (
        "点击",
        "进入",
        "返回",
        "查看",
        "跳转",
        "navigate",
        "open",
        "view",
        "click",
    )
    behavior_depth_tokens = (
        "状态",
        "恢复",
        "重试",
        "回滚",
        "幂等",
        "一致",
        "不丢上下文",
        "不串课文",
        "不错跳",
        "上下文保持",
        "断言",
        "assert",
        "state transition",
        "context",
        "consistent",
        "resume",
        "rollback",
        "idempotent",
    )
    has_pseudo_flow = any(token in text for token in pseudo_flow_tokens)
    has_behavior_depth = any(token in text for token in behavior_depth_tokens)
    state_guard_tokens = (
        "不串课文",
        "不串单元",
        "不丢上下文",
        "不错误推进",
        "不标记完成",
        "保持当前节点",
        "context preserved",
        "no wrong progression",
        "keep current node",
        "no cross-unit leak",
        "no cross-lesson leak",
    )
    has_state_guard_signal = any(token in text for token in state_guard_tokens)
    step_guard_patterns = (
        ("返回", "再进入"),
        ("return", "re-enter"),
        ("return", "reenter"),
        ("上一步", "下一步"),
        ("previous step", "next step"),
        ("中断", "恢复"),
        ("interrupt", "resume"),
    )
    has_step_guard_sequence = any(all(token in step_text for token in pattern) for pattern in step_guard_patterns)
    failure_guard_tokens = ("失败", "异常", "failure", "failed", "error")
    current_hold_tokens = ("当前页", "当前状态", "current page", "current state")
    has_failure_hold_sequence = any(token in step_text for token in failure_guard_tokens) and any(
        token in step_text for token in current_hold_tokens
    )
    has_coverage_value = bool(
        (score_profile.get("missing_rule_hits") or [])
        or (score_profile.get("core_rule_hits") or [])
        or (score_profile.get("unique_coverage_hits") or [])
    )
    if has_coverage_value:
        return False
    risk_words = ("异常", "失败", "错误", "权限", "安全", "并发", "性能", "exception", "error", "security", "permission")
    if any(word in text for word in risk_words):
        return False
    reasons = [str(item) for item in (score_profile.get("reasons") or []) if str(item).strip()]
    reuse_risk_hit = bool(score_profile.get("reuse_risk_hit"))
    if (
        bool(score_profile.get("cross_page_flow_hit"))
        or bool(score_profile.get("state_transition_hit"))
        or bool(score_profile.get("preferred_pattern_hit"))
        or reuse_risk_hit
        or ("main_workflow_hit" in reasons)
        or has_behavior_depth
        or has_state_guard_signal
        or has_step_guard_sequence
        or has_failure_hold_sequence
        or (step_count >= 3 and has_behavior_depth)
    ):
        return False
    if ui_hit_count >= 2 and not has_behavior_depth:
        return True
    if has_pseudo_flow and ui_hit_count >= 1 and step_count <= 2 and not has_behavior_depth:
        return True
    return False
