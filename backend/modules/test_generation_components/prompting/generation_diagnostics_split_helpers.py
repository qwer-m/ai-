"""生成链路诊断工具（阶段2.5）。"""
from __future__ import annotations

import re
from typing import Any

from modules.domain.stage25_switches import STAGE25_SWITCHES


_STOPWORDS = {
    "以及",
    "或者",
    "并且",
    "如果",
    "那么",
    "这个",
    "那个",
    "需要",
    "可以",
    "功能",
    "模块",
    "系统",
    "页面",
    "用户",
    "数据",
}

def _extract_keywords(text: str, limit: int = 30) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}", text or "")
    seen: set[str] = set()
    keywords: list[str] = []
    for token in tokens:
        key = token.lower()
        if key in seen or key in _STOPWORDS:
            continue
        seen.add(key)
        keywords.append(token)
        if len(keywords) >= max(5, int(limit)):
            break
    return keywords


def _flatten_case_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("description", "test_module", "test_input", "expected_result"):
        value = case.get(key)
        if value:
            parts.append(str(value))
    for key in ("preconditions", "steps"):
        value = case.get(key)
        if isinstance(value, list):
            parts.extend(str(x) for x in value if x)
        elif isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _priority_distribution(cases: list[dict[str, Any]]) -> dict[str, int]:
    dist = {"P0": 0, "P1": 0, "P2": 0, "other": 0}
    for case in cases:
        priority = str(case.get("priority") or "").strip().upper()
        if priority in dist:
            dist[priority] += 1
        else:
            dist["other"] += 1
    return dist


def _steps_count(case: dict[str, Any]) -> int:
    steps = case.get("steps")
    if isinstance(steps, list):
        return len([x for x in steps if str(x).strip()])
    if isinstance(steps, str):
        return len([x for x in re.split(r"[\n;；。]", steps) if str(x).strip()])
    return 0


def _classify_case_type(case: dict[str, Any]) -> str:
    text = _flatten_case_text(case).lower()
    edge_tokens = [
        "边界",
        "上限",
        "下限",
        "最大",
        "最小",
        "临界",
        "threshold",
        "boundary",
        "max",
        "min",
        "越界",
    ]
    negative_tokens = [
        "失败",
        "异常",
        "错误",
        "拒绝",
        "无效",
        "非法",
        "超时",
        "not",
        "fail",
        "error",
        "exception",
    ]
    if any(token in text for token in edge_tokens):
        return "edge"
    if any(token in text for token in negative_tokens):
        return "negative"
    return "positive"
