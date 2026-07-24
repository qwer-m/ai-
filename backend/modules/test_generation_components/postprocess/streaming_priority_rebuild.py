from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .streaming_case_normalization import normalize_priority_value


_REVIEW_PRIORITY_DEMOTION_SOURCES = frozenset(
    {
        "model_p0_guard_downgrade",
    }
)
_REVIEW_PRIORITY_DEMOTION_FINAL_PRIORITIES = frozenset({"P1", "P2"})


def preserve_review_priority_demotions(
    parsed_result: list[Any],
    review_candidate_cases: list[Any],
    *,
    case_signature_fn: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    review_priority_overrides: dict[str, str] = {}
    for source_case in review_candidate_cases:
        if not isinstance(source_case, dict):
            continue
        decision_source = str(source_case.get("priority_decision_source") or "").strip()
        decision_final = str(source_case.get("priority_final") or "").strip().upper()
        if (
            decision_source in _REVIEW_PRIORITY_DEMOTION_SOURCES
            and decision_final in _REVIEW_PRIORITY_DEMOTION_FINAL_PRIORITIES
        ):
            review_priority_overrides[case_signature_fn(source_case)] = "P1"

    restored_priority_cases: list[dict[str, Any]] = []
    for item in parsed_result:
        if not isinstance(item, dict):
            continue
        updated = dict(item)
        forced_priority = (
            review_priority_overrides.get(case_signature_fn(updated)) if review_priority_overrides else None
        )
        if forced_priority:
            updated["priority"] = forced_priority
            updated["priority_final"] = forced_priority
            updated["priority_decision_state"] = "overridden"
            updated["priority_decision_source"] = "review_model_p0_demotion_preserved"
        restored_priority_cases.append(updated)
    return restored_priority_cases


def rebuild_priority_by_semantics(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """归一模型优先级，并为明确的跨页面交互提供通用 P1 下限。"""
    p1_tokens = (
        "交互",
        "页面跳转",
        "跳转",
        "数据同步",
        "sync",
        "navigate",
        "redirect",
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
        if priority == "P2" and any(token in text for token in p1_tokens):
            priority = "P1"
        updated["priority"] = priority
        output.append(updated)
    return output
