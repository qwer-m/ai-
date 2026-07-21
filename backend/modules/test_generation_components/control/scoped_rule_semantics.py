from __future__ import annotations

import re


_CONDITION_MARKERS = (
    "如果",
    "若",
    "当",
    "仅当",
    "之前",
    "之后",
    "截止",
    "到期",
    "过期",
    "结束",
    "if",
    "when",
    "before",
    "after",
    "until",
    "once",
    "during",
    "within",
    "deadline",
    "expired",
)
_NORMATIVE_MARKERS = (
    "必须",
    "禁止",
    "不得",
    "不能",
    "不可",
    "不允许",
    "仅可",
    "只可",
    "只能",
    "must",
    "must not",
    "should",
    "should not",
    "cannot",
    "forbidden",
    "not allowed",
    "only",
    "read-only",
    "readonly",
)
_GENERIC_SCOPE_STOP_WORDS = frozenset(
    {
        "系统",
        "用户",
        "规则",
        "要求",
        "条件",
        "the",
        "system",
        "user",
        "rule",
        "condition",
    }
)
_CALENDAR_OR_VALUE_PATTERN = re.compile(
    r"(?:周[一二三四五六日天]|星期[一二三四五六日天])?\s*"
    r"(?:\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?|\d{1,2}:\d{2}|\d+(?:\.\d+)?%?)",
    flags=re.IGNORECASE,
)
_ASCII_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9_-]{2,}", flags=re.IGNORECASE)
_CJK_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}")


def _contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").strip().lower()
    return any(marker in lowered for marker in markers)


def _condition_relation(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if re.search(r"(?:\d{1,2}:\d{2}|截止|到期|过期|结束).{0,4}(?:之前|以前|前)", lowered):
        return "before"
    if re.search(r"(?:\d{1,2}:\d{2}|截止|到期|过期|结束).{0,4}(?:之后|以后|后)", lowered):
        return "after"
    if re.search(r"\bbefore\b|\buntil\b", lowered):
        return "before"
    if re.search(r"\bafter\b|\bexpired\b", lowered):
        return "after"
    if _contains_marker(lowered, ("如果", "若", "当", "仅当", "if", "when", "once")):
        return "conditional"
    return ""


def is_scoped_requirement_rule(fragment: str) -> bool:
    """识别带明确适用条件的规则，不绑定具体日期、模块或产品词。"""
    text = str(fragment or "").strip()
    if not text or not _contains_marker(text, _NORMATIVE_MARKERS):
        return False
    has_condition = bool(
        _condition_relation(text)
        or _CALENDAR_OR_VALUE_PATTERN.search(text)
        or _contains_marker(text, _CONDITION_MARKERS)
    )
    return has_condition


def _exclusive_allowed_actions(rule_text: str) -> set[str]:
    lowered = str(rule_text or "").strip().lower()
    actions: set[str] = set()
    for pattern in (
        r"(?:仅可|只可|只能)\s*([\u4e00-\u9fff]{2,8})",
        r"\bonly\s+(?:can\s+)?([a-z][a-z0-9_-]{2,})",
    ):
        for match in re.finditer(pattern, lowered, flags=re.IGNORECASE):
            action = str(match.group(1) or "").strip().lower()
            if action:
                actions.add(action)
    if "read-only" in lowered or "readonly" in lowered:
        actions.update({"read", "view"})
    return actions


def scoped_rule_scope_terms(rule_text: str) -> tuple[str, ...]:
    """从规则本身提取作用对象；中文使用通用 n-gram，不维护业务词典。"""
    text = str(rule_text or "").strip().lower()
    if not text:
        return ()
    for action in _exclusive_allowed_actions(text):
        text = text.replace(action, " ")
    text = _CALENDAR_OR_VALUE_PATTERN.sub(" ", text)
    for marker in (*_CONDITION_MARKERS, *_NORMATIVE_MARKERS):
        text = text.replace(marker, " ")
    text = re.sub(r"[<>＝=≤≥]+", " ", text)
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff_-]+", " ", text)

    terms: list[str] = []
    for token in _ASCII_TOKEN_PATTERN.findall(text):
        normalized = token.lower()
        if normalized not in _GENERIC_SCOPE_STOP_WORDS:
            terms.append(normalized)
    for chunk in _CJK_TOKEN_PATTERN.findall(text):
        if chunk in _GENERIC_SCOPE_STOP_WORDS:
            continue
        terms.append(chunk)
        max_width = min(4, len(chunk))
        for width in range(max_width, 1, -1):
            terms.extend(chunk[index : index + width] for index in range(0, len(chunk) - width + 1))
    return tuple(dict.fromkeys(term for term in terms if len(term) >= 2))[:24]


def scoped_rule_applies_to_case(case_text: str, rule_text: str) -> bool:
    if not is_scoped_requirement_rule(rule_text):
        return False
    rule_relation = _condition_relation(rule_text)
    if rule_relation in {"before", "after"} and _condition_relation(case_text) != rule_relation:
        return False
    lowered_case = str(case_text or "").lower()
    hits = [term for term in scoped_rule_scope_terms(rule_text) if term in lowered_case]
    return bool(any(len(term) >= 4 for term in hits) or len(hits) >= 2)


def scoped_rule_has_exclusive_action_conflict(case_text: str, rule_text: str) -> bool:
    allowed_actions = _exclusive_allowed_actions(rule_text)
    if not allowed_actions:
        return False
    lowered = str(case_text or "").strip().lower()
    observed: set[str] = set()
    for pattern in (
        r"(?:可以|允许|可)\s*([\u4e00-\u9fff]{2,8})",
        r"\b(?:can|may|allowed\s+to)\s+([a-z][a-z0-9_-]{2,})",
    ):
        for match in re.finditer(pattern, lowered, flags=re.IGNORECASE):
            action = str(match.group(1) or "").strip().lower()
            if action:
                observed.add(action)
    if not observed:
        return False
    return any(
        not any(action == allowed or action in allowed or allowed in action for allowed in allowed_actions)
        for action in observed
    )


__all__ = [
    "is_scoped_requirement_rule",
    "scoped_rule_applies_to_case",
    "scoped_rule_has_exclusive_action_conflict",
    "scoped_rule_scope_terms",
]
