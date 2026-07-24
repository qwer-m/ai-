from __future__ import annotations

import re

from .judge_text_utils import _normalize_text


_NEGATIVE_MARKERS = (
    "must not",
    "should not",
    "do not",
    "don't",
    "forbid",
    "forbidden",
    "禁止",
    "不得",
    "不能",
    "不可",
    "不允许",
)

_MIN_NEGATIVE_FACT_TAIL_CHARS = 4

_TEMPORAL_SHUTDOWN_SCOPE_MARKERS = (
    "结束后",
    "活动结束",
    "下线后",
    "过期后",
    "截止后",
    "到期后",
    "失效后",
    "after end",
    "after deadline",
    "after expiry",
    "expired",
    "offline",
)

_TEMPORAL_SHUTDOWN_BLOCK_MARKERS = (
    "入口关闭",
    "入口消失",
    "入口不可见",
    "入口不可点击",
    "不可进入",
    "不能进入",
    "无法进入",
    "禁止进入",
    "不可打开",
    "不能打开",
    "无法打开",
    "禁止打开",
    "不可访问",
    "不能访问",
    "无法访问",
    "禁止访问",
    "不跳转",
    "无响应",
    "not enter",
    "cannot enter",
    "can't enter",
    "not open",
    "cannot open",
    "can't open",
    "not access",
    "cannot access",
    "can't access",
    "closed",
    "unavailable",
)

_TEMPORAL_SHUTDOWN_POSITIVE_MARKERS = (
    "仍可进入",
    "可进入",
    "可以进入",
    "正常进入",
    "成功进入",
    "允许进入",
    "继续进入",
    "入口可点击",
    "入口展示",
    "入口开放",
    "仍可打开",
    "可打开",
    "可以打开",
    "正常打开",
    "成功打开",
    "允许打开",
    "继续打开",
    "跳转至",
    "跳转到",
    "can enter",
    "may enter",
    "allowed to enter",
    "successfully enters",
    "can open",
    "may open",
    "allowed to open",
    "successfully opens",
    "redirects to",
)

_TEMPORAL_SHUTDOWN_TRANSITION_MARKERS = (
    "进入",
    "打开",
    "跳转",
    "访问",
    "enter",
    "open",
    "navigate",
    "redirect",
)

_TEMPORAL_SHUTDOWN_ACTION_MARKERS = (
    "关闭",
    "消失",
    "不可见",
    "不可点击",
    "不可进入",
    "不能进入",
    "无法进入",
    "禁止进入",
    "不可打开",
    "不能打开",
    "无法打开",
    "禁止打开",
    "不可访问",
    "不能访问",
    "无法访问",
    "禁止访问",
    "不跳转",
    "无响应",
    "closed",
    "unavailable",
    "not enter",
    "cannot enter",
    "can't enter",
    "not open",
    "cannot open",
    "can't open",
    "not access",
    "cannot access",
    "can't access",
)

_NEGATED_TAIL_CONTEXT_MARKERS = (
    *_NEGATIVE_MARKERS,
    "not",
    "cannot",
    "can't",
    "without",
    "无法",
    "不能",
    "不可",
    "不允许",
    "禁止",
    "关闭",
    "消失",
    "无响应",
    "未进入",
    "未打开",
    "未跳转",
    "没有进入",
    "没有打开",
    "没有跳转",
    "未能",
    "does not",
    "doesn't",
    "not allowed",
)


def _contains_raw_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(str(marker or "").lower() in lowered for marker in markers if str(marker or "").strip())


def _negative_fact_marker_pattern() -> str:
    markers = sorted(
        {str(marker or "").strip().lower() for marker in _NEGATIVE_MARKERS if str(marker or "").strip()},
        key=len,
        reverse=True,
    )
    return "(" + "|".join(re.escape(marker) for marker in markers) + ")"


def _split_negative_fact_tail(fact: str) -> str:
    lowered_fact = str(fact or "").strip().lower()
    if not lowered_fact:
        return ""
    pieces = re.split(_negative_fact_marker_pattern(), lowered_fact, maxsplit=1)
    if len(pieces) < 3:
        return ""
    return str(pieces[-1] or "").strip()


def _is_temporal_shutdown_fact(fact: str) -> bool:
    lowered_fact = str(fact or "").strip().lower()
    return bool(
        _contains_raw_marker(lowered_fact, _TEMPORAL_SHUTDOWN_SCOPE_MARKERS)
        and _contains_raw_marker(lowered_fact, _TEMPORAL_SHUTDOWN_BLOCK_MARKERS)
    )


def _case_negates_tail(case_text: str, tail: str) -> bool:
    tail_text = str(tail or "").strip().lower()
    if not tail_text:
        return False
    lowered_case = str(case_text or "").strip().lower()
    start = 0
    while True:
        index = lowered_case.find(tail_text, start)
        if index < 0:
            return False
        window = lowered_case[max(0, index - 12): index + len(tail_text) + 8]
        if _contains_raw_marker(window, _NEGATED_TAIL_CONTEXT_MARKERS):
            return True
        start = index + max(1, len(tail_text))


def _temporal_shutdown_target_terms(fact: str) -> tuple[str, ...]:
    lowered = str(fact or "").strip().lower()
    for marker in sorted(_TEMPORAL_SHUTDOWN_SCOPE_MARKERS, key=len, reverse=True):
        lowered = lowered.replace(marker, " ")
    lowered = re.sub(r"^\s*(?:后|then)\s*", "", lowered)
    terms: list[str] = []
    seen: set[str] = set()
    for segment in re.split(r"[，,；;。.!！]|(?:\b(?:and|while)\b)|(?:且|并且|同时)", lowered):
        value = str(segment or "").strip()
        if not value:
            continue
        action_index = -1
        for marker in sorted(_TEMPORAL_SHUTDOWN_ACTION_MARKERS, key=len, reverse=True):
            index = value.find(marker)
            if index >= 0 and (action_index < 0 or index < action_index):
                action_index = index
        if action_index <= 0:
            continue
        prefix = value[:action_index]
        prefix = re.sub(r"\b(?:the|a|an|is|are|should|must)\b", " ", prefix)
        prefix = re.sub(r"(?:必须|应当|应该|需要)", " ", prefix)
        candidates = re.findall(r"[a-z][a-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", prefix)
        for candidate in candidates:
            normalized = str(candidate or "").strip().lower()
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(normalized)
    return tuple(terms)


def _clause_has_unnegated_transition(clause: str) -> bool:
    if _contains_raw_marker(clause, _TEMPORAL_SHUTDOWN_POSITIVE_MARKERS):
        return True
    for marker in _TEMPORAL_SHUTDOWN_TRANSITION_MARKERS:
        start = 0
        while True:
            index = clause.find(marker, start)
            if index < 0:
                break
            window = clause[max(0, index - 12): index + len(marker) + 4]
            if not _contains_raw_marker(window, _NEGATED_TAIL_CONTEXT_MARKERS):
                return True
            start = index + max(1, len(marker))
    return False


def _violates_temporal_shutdown_fact(case_text: str, fact: str = "") -> bool:
    target_terms = _temporal_shutdown_target_terms(fact)
    clauses = [
        str(item or "").strip().lower()
        for item in re.split(r"[，,；;。.!！\n]+", str(case_text or ""))
        if str(item or "").strip()
    ]
    for clause in clauses:
        if not _contains_raw_marker(clause, _TEMPORAL_SHUTDOWN_SCOPE_MARKERS):
            continue
        if _contains_raw_marker(clause, _TEMPORAL_SHUTDOWN_BLOCK_MARKERS):
            continue
        if target_terms and not any(term in clause for term in target_terms):
            continue
        if _clause_has_unnegated_transition(clause):
            return True
    return False


def _violates_negative_fact(case_text: str, fact: str) -> bool:
    lowered_fact = str(fact or "").strip().lower()
    if not lowered_fact:
        return False
    if not any(marker in lowered_fact for marker in _NEGATIVE_MARKERS):
        return False

    if _is_temporal_shutdown_fact(lowered_fact):
        return _violates_temporal_shutdown_fact(case_text, lowered_fact)

    normalized_case = _normalize_text(case_text)
    tail = _split_negative_fact_tail(lowered_fact)
    tail_normalized = _normalize_text(tail)
    if len(tail_normalized) < _MIN_NEGATIVE_FACT_TAIL_CHARS:
        return False
    if tail_normalized not in normalized_case:
        return False
    return not _case_negates_tail(case_text, tail)


__all__ = [
    "_NEGATIVE_MARKERS",
    "_MIN_NEGATIVE_FACT_TAIL_CHARS",
    "_TEMPORAL_SHUTDOWN_SCOPE_MARKERS",
    "_TEMPORAL_SHUTDOWN_BLOCK_MARKERS",
    "_TEMPORAL_SHUTDOWN_POSITIVE_MARKERS",
    "_TEMPORAL_SHUTDOWN_TRANSITION_MARKERS",
    "_TEMPORAL_SHUTDOWN_ACTION_MARKERS",
    "_NEGATED_TAIL_CONTEXT_MARKERS",
    "_contains_raw_marker",
    "_negative_fact_marker_pattern",
    "_split_negative_fact_tail",
    "_is_temporal_shutdown_fact",
    "_case_negates_tail",
    "_temporal_shutdown_target_terms",
    "_clause_has_unnegated_transition",
    "_violates_temporal_shutdown_fact",
    "_violates_negative_fact",
]
