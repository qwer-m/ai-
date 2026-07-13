from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .feedback_control_sample_access import (
    extract_forbidden_pattern_from_sample as _extract_forbidden_pattern_from_sample,
    sample_value as _sample_value,
)


_UI_LOW_VALUE_PATTERN_TOKENS = (
    "ui-only",
    "static ui",
    "static display",
    "copy check",
    "copy-only",
    "style check",
    "layout check",
    "layout-only",
    "visual only",
    "field display",
    "list sorting",
    "placeholder",
    "ui ",
    "display",
    "文案",
    "样式",
    "布局",
    "展示",
    "列表排序",
    "字段展示",
)

_UI_FORBIDDEN_GUARDRAILS = (
    "avoid static ui-only checks without workflow/state transition assertions",
    "avoid repetitive list sorting / field display / layout-only checks unless they block workflow",
    "copy/style/layout checks are supplemental only and must not dominate the case set",
)
_REUSE_RISK_PATTERNS: dict[str, tuple[str, ...]] = {
    "wrong_return_target_risk": (
        "回首页",
        "回列表",
        "返回首页",
        "返回列表",
        "返回目标",
        "return home",
        "return list",
        "wrong return",
    ),
    "legacy_behavior_risk": (
        "复用",
        "沿用",
        "残留",
        "旧按钮",
        "旧文案",
        "旧跳转",
        "legacy behavior",
        "legacy button",
        "obsolete behavior",
    ),
    "shared_page_residual_risk": (
        "共享页面",
        "共用页面",
        "原页面",
        "已有页面",
        "既有页面",
        "shared page",
        "existing page",
    ),
    "shared_flow_residual_risk": (
        "串课文",
        "串单元",
        "串逻辑",
        "串流程",
        "上下文污染",
        "原模块",
        "已有模块",
        "既有模块",
        "shared flow",
        "wrong progression",
        "context leak",
    ),
}
_REUSE_RISK_DESCRIPTIONS = {
    "wrong_return_target_risk": "wrong_return_target_risk: verify reused flow returns to the current module target instead of a legacy page.",
    "legacy_behavior_risk": "legacy_behavior_risk: verify reused module does not retain legacy buttons, copy, or obsolete behaviors.",
    "shared_page_residual_risk": "shared_page_residual_risk: verify shared page shells do not leak legacy entry or exit behavior into the new module.",
    "shared_flow_residual_risk": "shared_flow_residual_risk: verify reused flow does not串原模块逻辑、串课文/单元或污染当前上下文。",
}


def _is_ui_low_value_pattern(*parts: Any) -> bool:
    merged = " ".join(str(part or "") for part in parts).strip().lower()
    if not merged:
        return False
    return any(token in merged for token in _UI_LOW_VALUE_PATTERN_TOKENS)


def _extract_reuse_risks(*parts: Any) -> list[str]:
    merged = " ".join(str(part or "") for part in parts).strip().lower()
    if not merged:
        return []
    output: list[str] = []
    for risk_key, markers in _REUSE_RISK_PATTERNS.items():
        if any(marker.lower() in merged for marker in markers):
            output.append(_REUSE_RISK_DESCRIPTIONS[risk_key])
    return output


def _build_negative_forbidden_patterns(
    *,
    sample: dict[str, Any],
    title: str,
    comment: str,
    reason: str,
    sample_value_fn: Callable[..., Any] | None = None,
    extract_forbidden_pattern_from_sample_fn: Callable[..., str] | None = None,
    is_ui_low_value_pattern_fn: Callable[..., bool] | None = None,
) -> tuple[list[str], bool]:
    sample_value = sample_value_fn or _sample_value
    extract_forbidden_pattern = (
        extract_forbidden_pattern_from_sample_fn or _extract_forbidden_pattern_from_sample
    )
    is_ui_low_value_pattern = is_ui_low_value_pattern_fn or _is_ui_low_value_pattern
    base_pattern = str(
        sample_value(sample, "pattern_summary", "patternSummary")
        or sample_value(sample, "pattern_canonical", "patternCanonical")
        or comment
    ).strip() or extract_forbidden_pattern(title=title, comment=comment)
    patterns: list[str] = [base_pattern[:120]] if base_pattern else []
    is_ui_low_value = is_ui_low_value_pattern(
        reason,
        sample_value(sample, "pattern_category", "patternCategory"),
        sample_value(sample, "pattern_summary", "patternSummary"),
        sample_value(sample, "pattern_canonical", "patternCanonical"),
        title,
        comment,
    )
    if is_ui_low_value:
        patterns.extend(_UI_FORBIDDEN_GUARDRAILS)
    return patterns, bool(is_ui_low_value)
