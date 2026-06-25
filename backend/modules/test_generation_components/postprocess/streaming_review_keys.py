from __future__ import annotations

from typing import Any

from .case_access import case_flat_text
from .streaming_case_keys import case_coverage_bucket
from .streaming_text_match import normalize_match_text


def review_scenario(case: dict[str, Any]) -> str:
    bucket = str(case_coverage_bucket(case) or "")
    kind = bucket.split("|")[-1].strip().lower() if "|" in bucket else "happy"
    if kind == "state":
        return "state"
    if kind in {"exception", "risk", "boundary"}:
        return "exception"
    return "happy"


def review_domain(case: dict[str, Any]) -> str:
    text = normalize_match_text(
        case_flat_text(
            case,
            fields=("test_module", "description", "expected_result", "test_input", "steps"),
            separator=" ",
        )
    )
    permission_tokens = (
        "permission",
        "auth",
        "authorize",
        "role",
        "access",
        "权限",
        "鉴权",
        "授权",
        "角色",
    )
    report_tokens = (
        "report",
        "dashboard",
        "metric",
        "analytics",
        "报表",
        "报告",
        "看板",
        "统计",
    )
    if any(token and token in text for token in permission_tokens):
        return "permission"
    if any(token and token in text for token in report_tokens):
        return "report"
    return "general"
