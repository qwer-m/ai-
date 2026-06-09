from __future__ import annotations

from typing import Any

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
        " ".join(
            [
                str(case.get("test_module") or ""),
                str(case.get("description") or ""),
                str(case.get("expected_result") or ""),
                str(case.get("test_input") or ""),
                " ".join([str(x) for x in (case.get("steps") or []) if str(x).strip()])
                if isinstance(case.get("steps"), list)
                else "",
            ]
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
