from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .feedback_control_sample_access import (
    extract_forbidden_pattern_from_sample as _extract_forbidden_pattern_from_sample,
    sample_value as _sample_value,
)
from .reuse_risk_policy import (
    _REUSE_RISK_DESCRIPTIONS,
    _REUSE_RISK_PATTERNS,
    extract_reuse_risks,
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


def _is_ui_low_value_pattern(*parts: Any) -> bool:
    merged = " ".join(str(part or "") for part in parts).strip().lower()
    if not merged:
        return False
    return any(token in merged for token in _UI_LOW_VALUE_PATTERN_TOKENS)


def _extract_reuse_risks(*parts: Any) -> list[str]:
    return extract_reuse_risks(*parts)


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
