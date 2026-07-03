from __future__ import annotations

import re
from typing import Any

from .execution_plan_case_state import (
    _case_semantic_text,
    _state_value,
    _text,
)

_ACTION_SUPPORT_SPLIT_RE = re.compile(r"[的了着和与及并或且在从到于后前时中上下里内为把将对、，。；：:（）()\[\]\s]+")
_ACTION_SUPPORT_GENERIC_TOKENS = {
    "button",
    "click",
    "current",
    "page",
    "user",
    "view",
    "页面",
    "按钮",
    "点击",
    "操作",
    "用户",
    "当前",
    "对应",
    "进行",
    "所有",
}


def _action_support_tokens(value: Any) -> list[str]:
    text = _text(value).lower()
    if not text:
        return []
    raw_tokens: list[str] = []
    raw_tokens.extend(re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", text))
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for piece in _ACTION_SUPPORT_SPLIT_RE.split(sequence):
            if len(piece) < 2:
                continue
            if len(piece) <= 6:
                raw_tokens.append(piece)
            for index in range(len(piece) - 1):
                raw_tokens.append(piece[index : index + 2])

    tokens: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        normalized = token.strip().lower()
        if len(normalized) < 2 or normalized in _ACTION_SUPPORT_GENERIC_TOKENS:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return tokens


def _action_token_in_text(text: str, token: str) -> bool:
    if token.isascii() and re.search(r"[a-z0-9]", token):
        if re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", text):
            return True
        words = re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", text)
        variants = {token}
        if token.endswith("ed") and len(token) > 4:
            variants.add(token[:-1])
            variants.add(token[:-2])
        if token.endswith("s") and len(token) > 4:
            variants.add(token[:-1])
        for word in words:
            if word in variants:
                return True
            if len(word) >= 7 and len(token) >= 7 and (word.startswith(token[:5]) or token.startswith(word[:5])):
                return True
        return False
    return token in text


def main_chain_action_support_conflict_reason(case: dict[str, Any]) -> str:
    """Return a conflict reason when workflow action metadata is not supported by public case text."""
    action = _text(_state_value(case, "action"))
    label = _text(case.get("main_chain_stage_label"))
    action_tokens = _action_support_tokens(action)
    label_tokens = _action_support_tokens(label)
    if len(action_tokens) < 2 and len(label_tokens) < 2:
        return ""

    expected_tokens = list(dict.fromkeys([*action_tokens, *label_tokens]))
    if len(expected_tokens) < 2:
        return ""

    text = _case_semantic_text(case)
    matched = [token for token in expected_tokens if _action_token_in_text(text, token)]
    required = 1
    if len(expected_tokens) >= 5:
        required = 3
    elif len(expected_tokens) >= 3:
        required = 2
    if len(matched) < required or (len(matched) / max(1, len(expected_tokens))) < 0.3:
        return "stage_action_not_supported_by_case_text"
    return ""
