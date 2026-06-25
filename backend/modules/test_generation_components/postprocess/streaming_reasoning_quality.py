from __future__ import annotations

from typing import Any

from .case_access import case_text_parts


STREAMING_REASONING_LEAKAGE_SIGNALS = (
    "可能",
    "似乎",
    "不合理",
    "但需",
    "故意设置",
    "实际触发条件",
    "也就是说",
    "这里应该",
    "再读需求",
    "我们按照",
    "假设此处",
    "需求说",
    "按需求原文",
    "怎么会有",
    "此处假设",
    "暂且认为",
    "先按",
    "assume here",
    "assuming here",
    "seems",
    "maybe",
    "需求未明确",
    "假设",
    "可能",
    "实际应",
    "实际应该",
    "此处",
    "此处假设",
    "暂按",
    "暂时按",
    "需考虑",
    "need product confirm",
    "product confirm",
    "requirement unclear",
    "not reasonable",
    "reread requirement",
)


def reasoning_leakage_hits(
    case: dict[str, Any],
    *,
    signals: tuple[str, ...] = STREAMING_REASONING_LEAKAGE_SIGNALS,
) -> list[str]:
    parts = case_text_parts(case, ("preconditions", "steps", "expected_result"))
    text = "\n".join(parts).lower()
    hits: list[str] = []
    for signal in signals:
        token = str(signal or "").strip()
        if token and token.lower() in text:
            hits.append(token)
    return hits
