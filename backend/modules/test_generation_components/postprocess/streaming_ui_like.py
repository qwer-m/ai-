from __future__ import annotations

from typing import Any

from .case_access import case_flat_text, case_step_lines, case_steps, case_text_field
from .result_postprocess_priority_semantics_split_helpers import score_case_priority
from .json_validator import infer_case_kind

FINAL_DISPLAY_SURFACE_TOKENS = (
    "display",
    "ui-only",
    "static ui",
    "copy",
    "style",
    "layout",
    "analytics",
    "tracking",
    "button",
    "icon",
    "title",
    "label",
    "list",
    "table",
    "sort",
    "filter",
    "展示",
    "文案",
    "样式",
    "布局",
    "埋点",
    "曝光",
    "按钮",
    "图标",
    "标题",
    "标签",
    "列表",
    "表格",
    "排序",
    "筛选",
    "置灰",
)
FINAL_DISPLAY_BUSINESS_ANCHOR_TOKENS = (
    "save",
    "submit",
    "create",
    "delete",
    "archive",
    "sync",
    "update",
    "effective",
    "navigate",
    "enter",
    "open",
    "auto",
    "generate",
    "retain",
    "restore",
    "rollback",
    "retry",
    "failed",
    "error",
    "permission",
    "state",
    "progress",
    "complete",
    "consistent",
    "保存",
    "提交",
    "创建",
    "新增",
    "删除",
    "归档",
    "同步",
    "更新",
    "生效",
    "跳转",
    "进入",
    "打开",
    "自动",
    "生成",
    "顺延",
    "保留",
    "恢复",
    "回滚",
    "重试",
    "失败",
    "异常",
    "权限",
    "状态",
    "进度",
    "完成",
    "一致",
    "跨端",
    "下游",
    "不可删除",
    "无法删除",
    "不可点击",
    "按规则",
    "最近",
    "最新",
)
FINAL_DISPLAY_BUSINESS_REASON_KEYS = {
    "main_workflow_hit",
    "cross_page_flow_hit",
    "state_transition_hit",
    "reuse_risk_hit",
    "preferred_pattern_hit",
    "workflow_blocking",
    "case_level_release_blocking",
    "release_blocking_rule_hit",
    "security_or_data_critical_rule_hit",
}


def _case_plain_text(case: dict[str, Any]) -> str:
    parts: list[str] = [
        case_text_field(case, key)
        for key in (
            "execution_group",
            "test_module",
            "description",
            "expected_result",
            "test_input",
        )
    ]
    parts.extend(case_step_lines(case))
    return " ".join(parts).lower()


def is_display_only_final_case(case: dict[str, Any]) -> bool:
    """Return true for low-value display/UI checks, not business visibility/state assertions."""
    if not isinstance(case, dict):
        return False
    group = str(case.get("execution_group") or "").strip().lower()
    text = _case_plain_text(case)
    score_profile = score_case_priority(case)
    reasons = {str(item) for item in (score_profile.get("reasons") or []) if str(item).strip()}
    has_surface_signal = bool(
        group == "display"
        or any(token.lower() in text for token in FINAL_DISPLAY_SURFACE_TOKENS)
        or is_ui_like_case(case, score_profile)
    )
    if not has_surface_signal:
        return False
    has_business_anchor = bool(
        reasons.intersection(FINAL_DISPLAY_BUSINESS_REASON_KEYS)
        or any(token.lower() in text for token in FINAL_DISPLAY_BUSINESS_ANCHOR_TOKENS)
    )
    if not has_business_anchor:
        return True
    # A business case can still be display-only when its own UI-like classifier says
    # it has no meaningful flow/state/risk depth.
    return bool(is_ui_like_case(case, score_profile))


def is_ui_like_case(case: dict[str, Any], score_profile: dict[str, Any]) -> bool:
    if infer_case_kind(case) == "workflow_entry":
        return False
    steps = case_steps(case)
    step_count = len(steps)
    step_text = " ".join(steps).lower()
    text = case_flat_text(
        case,
        fields=("description", "expected_result", "test_input", "steps"),
        separator=" ",
        lower=True,
    )
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
        "无串扰",
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
        "无串扰",
        "保持隔离",
        "不丢上下文",
        "不错误推进",
        "不标记完成",
        "保持当前节点",
        "context preserved",
        "no wrong progression",
        "keep current node",
        "no cross-context leak",
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


__all__ = ["is_ui_like_case"]
