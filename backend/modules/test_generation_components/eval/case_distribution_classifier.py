from __future__ import annotations

import re
from typing import Any

CASE_TYPE_FLOW = "FLOW"
CASE_TYPE_STATE = "STATE"
CASE_TYPE_UI = "UI"
CASE_TYPES = (CASE_TYPE_FLOW, CASE_TYPE_STATE, CASE_TYPE_UI)

_FLOW_TOKENS = (
    "\u6d41\u7a0b",
    "\u8def\u5f84",
    "\u8fdb\u5165",
    "\u8fd4\u56de",
    "\u8df3\u8f6c",
    "\u5b8c\u6210",
    "\u5b66\u4e60",
    "\u7ec3\u4e60",
    "\u63d0\u4ea4",
    "\u95ed\u73af",
    "\u7ee7\u7eed",
    "flow",
    "journey",
    "submit",
    "complete",
    "continue",
)
_FLOW_PROGRESS_TOKENS = (
    "\u63d0\u4ea4",
    "\u5b8c\u6210",
    "\u7ee7\u7eed",
    "\u4e0b\u4e00\u6b65",
    "\u4e0b\u4e00\u9636\u6bb5",
    "\u8fdb\u5165\u4e0b\u4e00\u9636\u6bb5",
    "\u95ed\u73af",
    "\u7b54\u9898",
    "\u7ec3\u4e60",
    "submit",
    "complete",
    "continue",
    "finish",
    "next step",
    "next stage",
    "close the loop",
)
_CROSS_PAGE_TOKENS = (
    "\u8df3\u8f6c",
    "\u8fd4\u56de",
    "\u8fdb\u5165",
    "\u8de8\u9875",
    "\u8de8\u9875\u9762",
    "\u8de8\u6a21\u5757",
    "\u5bfc\u822a",
    "\u5207\u6362\u9875\u9762",
    "cross-page",
    "cross page",
    "cross module",
    "page jump",
    "navigation",
    "navigate",
    "open details",
    "re-enter",
    "reenter",
)
_FLOW_SEQUENCE_PATTERNS = (
    ("\u8fd4\u56de", "\u518d\u8fdb\u5165"),
    ("\u4e0a\u4e00\u6b65", "\u4e0b\u4e00\u6b65"),
    ("return", "re-enter"),
    ("return", "reenter"),
    ("previous", "next"),
)
_STATE_TOKENS = (
    "\u72b6\u6001",
    "\u5207\u6362",
    "\u52a0\u8f7d",
    "\u5237\u65b0",
    "\u7f13\u5b58",
    "\u63a5\u53e3",
    "\u8bf7\u6c42",
    "\u6570\u636e",
    "\u6062\u590d",
    "\u4e2d\u65ad",
    "\u91cd\u8bd5",
    "\u5e76\u53d1",
    "\u4e00\u81f4",
    "\u4e0a\u4e0b\u6587",
    "\u4fdd\u6301",
    "\u4fdd\u7559",
    "\u5f53\u524d\u8282\u70b9",
    "state",
    "status",
    "transition",
    "cache",
    "request",
    "data",
    "context",
    "consistent",
    "restore",
    "retry",
)
_STATE_GUARD_TOKENS = (
    "\u4e0d\u4e32\u8bfe\u6587",
    "\u4e0d\u4e32\u5355\u5143",
    "\u4e0d\u4e22\u4e0a\u4e0b\u6587",
    "\u4e0d\u9519\u8bef\u63a8\u8fdb",
    "\u4e0d\u6807\u8bb0\u5b8c\u6210",
    "\u4fdd\u6301\u5f53\u524d\u8282\u70b9",
    "\u4fdd\u6301\u5f53\u524d\u9875",
    "\u4fdd\u6301\u5f53\u524d\u72b6\u6001",
    "context preserved",
    "keep current node",
    "keep current page",
    "keep current state",
    "no wrong progression",
    "no cross-unit leak",
    "no cross-lesson leak",
)
_UI_TOKENS = (
    "\u6309\u94ae",
    "\u6587\u6848",
    "\u6837\u5f0f",
    "\u989c\u8272",
    "\u5c55\u793a",
    "\u63d0\u793a",
    "\u5f39\u7a97",
    "\u56fe\u6807",
    "\u975e\u7a7a",
    "\u9875\u9762\u5143\u7d20",
    "\u6807\u9898",
    "\u5b57\u6bb5",
    "\u5e03\u5c40",
    "\u5217\u8868",
    "\u8868\u683c",
    "\u6392\u5e8f",
    "\u7b5b\u9009",
    "\u663e\u9690",
    "\u5360\u4f4d",
    "ui",
    "button",
    "copy",
    "style",
    "color",
    "display",
    "tooltip",
    "popup",
    "icon",
    "title",
    "field",
    "layout",
    "list",
    "table",
    "sort",
    "filter",
    "placeholder",
)


def _normalize_steps(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n;；。]", value) if item.strip()]
    return []


def _flatten_case_text(case: dict[str, Any]) -> str:
    steps = _normalize_steps(case.get("steps"))
    text_parts = [
        str(case.get("description") or ""),
        str(case.get("test_module") or case.get("module") or ""),
        " ".join(steps),
        str(case.get("expected_result") or ""),
        str(case.get("test_input") or ""),
    ]
    return " ".join(part for part in text_parts if part).lower()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in text for token in tokens)


def _count_step_tokens(text: str, patterns: tuple[tuple[str, str], ...]) -> bool:
    return any(all(token.lower() in text for token in pattern) for pattern in patterns)


def classify_case_distribution(case: dict[str, Any]) -> str:
    steps = _normalize_steps(case.get("steps"))
    step_count = len(steps)
    step_text = " ".join(steps).lower()
    text = _flatten_case_text(case)

    has_cross_page = _contains_any(text, _CROSS_PAGE_TOKENS)
    has_state_transition = _contains_any(text, _STATE_TOKENS)
    has_state_guard = _contains_any(text, _STATE_GUARD_TOKENS)
    has_flow_token = _contains_any(text, _FLOW_TOKENS)
    has_flow_progress = _contains_any(text, _FLOW_PROGRESS_TOKENS)
    has_flow_sequence = _count_step_tokens(step_text or text, _FLOW_SEQUENCE_PATTERNS)
    has_ui_token = _contains_any(text, _UI_TOKENS)

    strong_flow_hit = bool(
        has_flow_progress
        and (
            has_flow_sequence
            or (has_cross_page and step_count >= 2)
            or step_count >= 3
            or (has_flow_token and step_count >= 2)
        )
    )
    state_hit = bool(has_state_transition or has_state_guard)

    if has_state_guard and not strong_flow_hit:
        return CASE_TYPE_STATE
    if strong_flow_hit:
        return CASE_TYPE_FLOW
    if state_hit:
        return CASE_TYPE_STATE
    if has_ui_token:
        return CASE_TYPE_UI
    return CASE_TYPE_UI


def classify_case_distributions(cases: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id") or case.get("case_id") or case.get("caseId") or "").strip()
        if not case_id:
            case_id = f"__index_{index}"
        mapping[case_id] = classify_case_distribution(case)
    return mapping


def summarize_case_distribution(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        CASE_TYPE_FLOW: 0,
        CASE_TYPE_STATE: 0,
        CASE_TYPE_UI: 0,
    }
    for case_type in classify_case_distributions(cases).values():
        counts[case_type] = int(counts.get(case_type, 0)) + 1
    return counts


def summarize_case_structure_signals(cases: list[dict[str, Any]]) -> dict[str, int]:
    cross_page = 0
    multi_step = 0
    state_transition = 0
    for case in cases:
        if not isinstance(case, dict):
            continue
        steps = _normalize_steps(case.get("steps"))
        text = _flatten_case_text(case)
        if len(steps) >= 3:
            multi_step += 1
        if _contains_any(text, _CROSS_PAGE_TOKENS):
            cross_page += 1
        if _contains_any(text, _STATE_TOKENS) or _contains_any(text, _STATE_GUARD_TOKENS):
            state_transition += 1
    return {
        "cross_page_case_count": int(cross_page),
        "multi_step_case_count": int(multi_step),
        "state_transition_case_count": int(state_transition),
    }
