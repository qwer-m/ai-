from __future__ import annotations

import re
from typing import Any


def case_signature(case: dict[str, Any]) -> str:
    module = str(case.get("test_module") or "").strip().lower()
    desc = str(case.get("description") or "").strip().lower()
    expected = str(case.get("expected_result") or "").strip().lower()
    test_input = str(case.get("test_input") or "").strip().lower()
    return f"{module}|{desc}|{expected}|{test_input}"


def case_priority_score(case: dict[str, Any]) -> int:
    value = str(case.get("priority") or "").strip().upper()
    return 3 if value == "P0" else 2 if value == "P1" else 1 if value == "P2" else 0


def _case_focus_text(case: dict[str, Any]) -> str:
    return " ".join(
        [
            str(case.get("description") or ""),
            str(case.get("expected_result") or ""),
            str(case.get("test_input") or ""),
            " ".join([str(x) for x in case.get("steps", [])]) if isinstance(case.get("steps"), list) else "",
        ]
    ).lower()


def case_focus_score(case: dict[str, Any]) -> int:
    text = _case_focus_text(case)
    score = 0
    if any(k in text for k in ["边界", "最大", "最小", "临界", "boundary", "max", "min"]):
        score += 2
    if any(k in text for k in ["异常", "失败", "错误", "拒绝", "exception", "error", "invalid"]):
        score += 2
    if any(k in text for k in ["状态", "流转", "state", "transition"]):
        score += 1
    return score


def case_coverage_bucket(case: dict[str, Any]) -> str:
    module = str(case.get("test_module") or "").strip().lower() or "general"
    text = _case_focus_text(case)
    if any(k in text for k in ["异常", "失败", "错误", "拒绝", "exception", "error", "invalid"]):
        kind = "exception"
    elif any(k in text for k in ["边界", "最大", "最小", "临界", "boundary", "max", "min"]):
        kind = "boundary"
    elif any(k in text for k in ["状态", "流转", "state", "transition"]):
        kind = "state"
    elif any(k in text for k in ["权限", "安全", "鉴权", "性能", "permission", "security", "auth", "performance"]):
        kind = "risk"
    else:
        kind = "happy"
    return f"{module}|{kind}"


def review_case_id(case: dict[str, Any]) -> str:
    return str(case.get("id") or case.get("case_id") or "").strip()


def final_description_dedup_key(case: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(case.get("description") or "").strip()).lower()


def dedupe_by_final_description(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    if not isinstance(cases, list):
        return [], set()
    kept: list[dict[str, Any]] = []
    dropped_signatures: set[str] = set()
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            continue
        key = final_description_dedup_key(case)
        if key and key in seen:
            signature = case_signature(case)
            if signature:
                dropped_signatures.add(signature)
            continue
        if key:
            seen.add(key)
        kept.append(case)
    return kept, dropped_signatures
