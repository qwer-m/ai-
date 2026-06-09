from __future__ import annotations

import re
from typing import Any


def semantic_normalize_text(text: str) -> str:
    """
    中文注释：轻量语义归一化，避免“同义换皮”导致重复保留。
    不引入外部依赖，仅做本地规则收敛。
    """
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    replacements = {
        "toast": "提示",
        "不可点击": "禁用",
        "不可用": "禁用",
        "无法点击": "禁用",
        "置灰": "禁用",
        "按钮可用": "可点击",
        "按钮可点击": "可点击",
        "显示": "展示",
        "文案": "提示文案",
        "入口": "功能入口",
        "图标": "功能入口",
    }
    for src, dst in replacements.items():
        lowered = lowered.replace(src, dst)
    lowered = re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)
    return lowered


def semantic_tokenize(text: str, limit: int = 16) -> set[str]:
    raw = semantic_normalize_text(text)
    if not raw:
        return set()
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", raw)
    output: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        output.append(token)
        if len(output) >= max(6, int(limit)):
            break
    return set(output)


def semantic_signature(case: dict[str, Any], rule_keys: list[str]) -> str:
    module = str(case.get("test_module") or "").strip().lower() or "general"
    desc = semantic_normalize_text(str(case.get("description") or ""))
    expected = semantic_normalize_text(str(case.get("expected_result") or ""))
    steps = case.get("steps")
    steps_text = ""
    if isinstance(steps, list):
        steps_text = semantic_normalize_text(" ".join(str(x) for x in steps if str(x).strip()))
    core_text = (desc + "|" + expected + "|" + steps_text)[:120]
    rule_key = "|".join(sorted(rule_keys)) if rule_keys else "NO_RULE"
    return f"{module}|{rule_key}|{core_text}"


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    inter = len(left.intersection(right))
    union = len(left.union(right))
    if union <= 0:
        return 0.0
    return float(inter) / float(union)
