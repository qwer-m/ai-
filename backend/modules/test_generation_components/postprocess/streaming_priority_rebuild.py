from __future__ import annotations

from typing import Any

from .streaming_case_normalization import normalize_priority_value
from .streaming_p0_groups import P0_GROUP_TOKENS


def rebuild_priority_by_semantics(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    p0_extra_tokens = (
        "主流程",
        "闭环",
        "未付费",
        "付费提示",
        "直接url",
        "直接url访问",
        "ai判分",
        "ocr",
        "错题归集",
        "错题本",
        "教学周",
        "周日24:00",
        "周日24",
        "补做期",
        "历史周",
        "提交全部",
        "查看学习报告",
    )
    p1_tokens = (
        "交互",
        "页面跳转",
        "跳转",
        "数据同步",
        "sync",
        "navigate",
        "redirect",
    )
    p2_tokens = (
        "ui",
        "文案",
        "样式",
        "展示",
        "icon",
        "layout",
        "copywriting",
    )
    output: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        updated = dict(case)
        text = " ".join(
            [
                str(updated.get("test_module") or ""),
                str(updated.get("description") or ""),
                str(updated.get("expected_result") or ""),
                str(updated.get("test_input") or ""),
                " ".join([str(x) for x in (updated.get("steps") or []) if str(x).strip()])
                if isinstance(updated.get("steps"), list)
                else "",
            ]
        ).lower()
        priority = normalize_priority_value(str(updated.get("priority") or "P2"))
        if any(token in text for tokens in P0_GROUP_TOKENS.values() for token in tokens) or any(
            token in text for token in p0_extra_tokens
        ):
            priority = "P0"
        elif any(token in text for token in p1_tokens):
            priority = "P1"
        elif any(token in text for token in p2_tokens):
            priority = "P2"
        updated["priority"] = priority
        output.append(updated)
    return output
