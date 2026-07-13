from __future__ import annotations

import re
from typing import Any

from .case_access import case_focus_text, case_id, case_priority, case_signature_text, case_text_field


def case_signature(case: dict[str, Any]) -> str:
    return case_signature_text(case)


def case_priority_score(case: dict[str, Any]) -> int:
    value = case_priority(case)
    return 3 if value == "P0" else 2 if value == "P1" else 1 if value == "P2" else 0


def _case_focus_text(case: dict[str, Any]) -> str:
    return case_focus_text(case, lower=True)


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
    module = case_text_field(case, "test_module").lower() or "general"
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
    return case_id(case)


def final_description_dedup_key(case: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", case_text_field(case, "description")).lower()


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
