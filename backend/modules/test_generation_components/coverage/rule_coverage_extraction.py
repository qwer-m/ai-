from __future__ import annotations

import re
from typing import Any

from .coverage_strategy import (
    generic_non_blocking_rules,
    rule_action_hints,
)
from .rule_coverage_text import _extract_rule_id, _normalize_text, _tokenize

_RULE_ACTION_HINTS = rule_action_hints()

_HEADING_PATTERNS = (
    r"^[一二三四五六七八九十]+[、.．]\s*[^：:]{1,24}$",
    r"^\d+[、.．]\s*[^：:]{1,24}$",
    r".*说明$",
    r".*调整说明$",
)

_GENERIC_NON_BLOCKING_RULES = generic_non_blocking_rules()

_NON_REQUIREMENT_SECTION_HEADERS = {
    "[parsed requirement evidence]",
    "[multimodal evidence alignment]",
    "[requirement understanding]",
}

_LIST_ITEM_PREFIX_RE = re.compile(
    r"^\s*(?:[-*•]\s+|\d+[\.\)、]\s*|[a-zA-Z]{1,6}[\.\)]\s*|[一二三四五六七八九十]+[、\.\)]\s*)"
)


def _is_bracket_section_header(line: str) -> bool:
    normalized = _normalize_text(line).strip()
    return bool(normalized.startswith("[") and normalized.endswith("]") and len(normalized) <= 120)


def _is_non_requirement_section_header(line: str) -> bool:
    return _normalize_text(line).strip().lower() in _NON_REQUIREMENT_SECTION_HEADERS


def _looks_like_parse_diagnostic_line(line: str) -> bool:
    normalized = _normalize_text(line).strip()
    lowered = normalized.lower()
    if not normalized:
        return False
    if lowered.startswith(("visual_fact:", "aligned_evidence:", "invalid_visual_sources:")):
        return True
    if lowered.startswith(("{", "}", '"version"', '"visual_facts"', '"aligned_evidence"')):
        return True
    if " -> requirement score=" in lowered:
        return True
    if re.match(r"^-\s*\w+:\s*filename=.*\bstrategy=", normalized, flags=re.IGNORECASE):
        return True
    if re.match(r"^-?\s*(pdf_visual|prototype|attachment):", normalized, flags=re.IGNORECASE) and (
        "ocr_source=" in lowered or "cloud_fallback=" in lowered
    ):
        return True
    return False


def _strip_non_requirement_sections(text: str) -> str:
    lines: list[str] = []
    skipping = False
    for raw_line in str(text or "").splitlines():
        line = _normalize_text(raw_line).strip()
        if _is_non_requirement_section_header(line):
            skipping = True
            continue
        if _is_bracket_section_header(line):
            skipping = False
        if skipping or _looks_like_parse_diagnostic_line(line):
            continue
        lines.append(raw_line)
    return "\n".join(lines)


def _starts_new_logical_line(line: str) -> bool:
    normalized = _normalize_text(line).strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    return bool(
        normalized.startswith("#")
        or normalized.startswith("[")
        or lowered.startswith(("biz_key:", "test_module:", "priority:"))
        or _LIST_ITEM_PREFIX_RE.match(normalized)
    )


def _join_line_fragment(left: str, right: str) -> str:
    left = _normalize_text(left).strip()
    right = _normalize_text(right).strip()
    if not left:
        return right
    if not right:
        return left
    if left.endswith(("。", "；", ";", "！", "!", "？", "?")):
        return f"{left}\n{right}"
    left_tail = left[-1:]
    right_head = right[:1]
    separator = " " if left_tail.isascii() and right_head.isascii() and left_tail.isalnum() and right_head.isalnum() else ""
    return f"{left}{separator}{right}"


def _looks_like_continuation_fragment(line: str) -> bool:
    normalized = _normalize_text(line).strip()
    if not normalized:
        return False
    if _starts_new_logical_line(normalized):
        return False
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    if len(chinese_chars) <= 4:
        return True
    return bool(normalized.startswith(("则", "若", "且", "并", "或", "和", "与", "及", "、", "，", "。", "：", ":")))


def _should_merge_logical_line(current: str, line: str) -> bool:
    current = _normalize_text(current).strip()
    line = _normalize_text(line).strip()
    if not current or not line:
        return False
    if current.endswith(("。", "；", ";", "！", "!", "？", "?")):
        return False
    if current in _GENERIC_NON_BLOCKING_RULES:
        return False
    if _LIST_ITEM_PREFIX_RE.match(current):
        return True
    return _looks_like_continuation_fragment(line)


def _iter_logical_requirement_lines(text: str) -> list[str]:
    logical_lines: list[str] = []
    current = ""
    for raw_line in str(text or "").splitlines():
        line = _normalize_text(raw_line).strip()
        if not line:
            if current:
                logical_lines.append(current)
                current = ""
            continue
        if current and not _starts_new_logical_line(line) and _should_merge_logical_line(current, line):
            merged = _join_line_fragment(current, line)
            if "\n" in merged:
                logical_lines.append(current)
                current = line
            else:
                current = merged
            continue
        if current:
            logical_lines.append(current)
        current = line
    if current:
        logical_lines.append(current)
    return logical_lines

def _looks_like_heading_or_fragment(line: str) -> bool:
    normalized = _normalize_text(line).strip()
    if not normalized:
        return True
    if normalized.startswith("@"):
        return True
    if any(re.fullmatch(pattern, normalized) for pattern in _HEADING_PATTERNS):
        return True
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}", normalized)
    if normalized.endswith(("如", "需", "核", "场", "内", "选", "按")):
        return True
    if len(tokens) <= 1 and not any(hint in normalized.lower() for hint in _RULE_ACTION_HINTS):
        return True
    return False

def _has_rule_action_signal(line: str) -> bool:
    normalized = _normalize_text(line).lower()
    return any(hint.lower() in normalized for hint in _RULE_ACTION_HINTS)

def _ambiguous_fragment_reason(line: str) -> str:
    normalized = _normalize_text(line).strip()
    if not normalized or _extract_rule_id(normalized):
        return ""
    lowered = normalized.lower()
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    strong_directive_tokens = (
        "必须",
        "禁止",
        "不可",
        "不能",
        "不允许",
        "不展示",
        "隐藏",
        "固定",
        "支持",
        "保存",
        "读取",
        "跳转",
        "点击",
        "弹出",
        "置灰",
        "禁用",
        "must",
        "required",
        "forbid",
        "hide",
        "show",
        "display",
        "support",
    )
    question_tokens = (
        "是否",
        "吗",
        "什么",
        "怎么",
        "如何",
        "哪个",
        "哪里",
        "待确认",
        "待定",
        "可能",
        "的话",
        "?",
    )
    has_question_signal = any(token in normalized for token in question_tokens) or any(
        token in lowered for token in ("pending confirmation", "to be confirmed", "tbd")
    )
    if has_question_signal and not normalized.startswith("已确认"):
        return "unconfirmed_question"
    motivational_copy_tokens = (
        "slogan",
        "tagline",
        "愿景",
        "使命",
        "口号",
        "品牌语",
        "宣传语",
        "相信",
        "迈向",
        "新高度",
        "赋能",
        "成长",
    )
    has_directive = any(token in normalized for token in strong_directive_tokens)
    motivational_hits = sum(1 for token in motivational_copy_tokens if token in normalized or token in lowered)
    if not has_directive and (motivational_hits >= 2 or any(token in lowered for token in ("slogan", "tagline"))):
        return "motivational_copy_fragment"
    if len(chinese_chars) <= 8 and not any(token in normalized for token in strong_directive_tokens):
        return "short_fragment"
    if len(chinese_chars) <= 16 and normalized.endswith(("的", "为", "或", "和", "及", "与", "模")):
        return "truncated_fragment"
    if len(chinese_chars) <= 18 and normalized[:1] in {"以", "并", "则", "且", "或"}:
        return "truncated_fragment"
    return ""

def _is_low_confidence_requirement_discussion(line: str) -> bool:
    normalized = _normalize_text(line).strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    explicit_tokens = (
        "必须",
        "禁止",
        "不可",
        "不能",
        "应",
        "需要",
        "需",
        "固定",
        "只显示",
        "不显示",
        "隐藏",
        "展示",
        "显示",
        "支持",
        "保留",
        "不保留",
        "must",
        "should",
        "required",
        "forbid",
        "hide",
        "show",
        "display",
        "support",
        "keep",
    )
    has_explicit_signal = any(token in lowered for token in explicit_tokens)
    uncertain_tokens = (
        "是否",
        "如何",
        "怎么",
        "吗",
        "？",
        "?",
        "待确认",
        "暂不确定",
        "待定",
        "本期不做",
        "这一期不做",
        "这期不做",
        "不做",
        "可能",
        "可选",
        "看情况",
        "哈",
    )
    if "是否" in normalized and "已确认" not in normalized and "确认" not in normalized:
        return True
    if ("如何" in normalized or "怎么" in normalized) and "已确认" not in normalized and "确认" not in normalized:
        return True
    if any(token in normalized for token in uncertain_tokens) and not has_explicit_signal:
        return True
    if re.match(r"^[a-zA-Z]\s*[\.\)、)]", normalized) and re.search(
        r"(没有按照|问题|需调整|需要调整|结构.*调整)", normalized
    ):
        return True
    if normalized.startswith("这是") and not has_explicit_signal:
        return True
    if re.match(r"^[a-zA-Z]\s*[\.\)、)]", normalized) and not has_explicit_signal:
        return True
    return False

def _classify_requirement_rule(rule_text: str) -> dict[str, Any]:
    """Classify extracted rules so diagnostics can keep context without over-blocking."""
    normalized = _normalize_text(rule_text).strip()
    lowered = normalized.lower()
    if normalized in _GENERIC_NON_BLOCKING_RULES:
        return {
            "rule_level": "soft",
            "confidence": "low",
            "source_type": "generic_display_heading",
            "blocking": False,
            "non_blocking_reason": "generic_display_heading",
        }
    if any(re.fullmatch(pattern, normalized) for pattern in _HEADING_PATTERNS):
        return {
            "rule_level": "soft",
            "confidence": "low",
            "source_type": "section_heading",
            "blocking": False,
            "non_blocking_reason": "section_heading",
        }
    if normalized.endswith((":", "：")) and not _extract_rule_id(normalized):
        return {
            "rule_level": "soft",
            "confidence": "low",
            "source_type": "label_fragment",
            "blocking": False,
            "non_blocking_reason": "label_fragment",
        }
    if len(_tokenize(normalized, limit=8)) <= 1 and not _extract_rule_id(normalized):
        return {
            "rule_level": "soft",
            "confidence": "low",
            "source_type": "short_fragment",
            "blocking": False,
            "non_blocking_reason": "short_fragment",
        }
    ambiguous_reason = _ambiguous_fragment_reason(normalized)
    if ambiguous_reason:
        return {
            "rule_level": "soft",
            "confidence": "low",
            "source_type": "ambiguous_fragment",
            "blocking": False,
            "non_blocking_reason": ambiguous_reason,
        }
    if "原型" in normalized and not any(token in lowered for token in ("必须", "需要", "固定", "禁止", "支持")):
        return {
            "rule_level": "soft",
            "confidence": "medium",
            "source_type": "prototype_reference",
            "blocking": False,
            "non_blocking_reason": "prototype_reference",
        }
    return {
        "rule_level": "hard",
        "confidence": "high" if _extract_rule_id(normalized) or _has_rule_action_signal(normalized) else "medium",
        "source_type": "confirmed_requirement",
        "blocking": True,
        "non_blocking_reason": "",
    }

def _extract_requirement_rules(requirement_context: str) -> list[dict[str, Any]]:
    """中文注释：解析 requirement_context，提取规则级条目（支持按 biz_key 分组文本）。"""
    text = _normalize_text(_strip_non_requirement_sections(requirement_context)).strip()
    if not text:
        return []

    rules: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    current_biz_key = "unknown"

    for raw_line in _iter_logical_requirement_lines(text):
        line = _normalize_text(raw_line).strip()
        if not line:
            continue
        biz_match = re.match(r"^###\s*biz_key:\s*([^\s（(]+)", line, flags=re.IGNORECASE)
        if biz_match:
            current_biz_key = biz_match.group(1).strip() or "unknown"
            continue
        if line.startswith("【"):
            continue
        normalized = re.sub(r"^\s*[-*•]\s*", "", line).strip()
        if len(normalized) < 4:
            continue
        if _looks_like_heading_or_fragment(normalized):
            continue
        if normalized.lower().startswith("biz_key:") or normalized.lower().startswith("test_module:"):
            continue
        if normalized.lower().startswith("priority:"):
            continue
        if _is_low_confidence_requirement_discussion(normalized):
            continue
        if not _has_rule_action_signal(normalized) and not _extract_rule_id(normalized):
            continue

        segments = [normalized]
        if len(re.findall(r"\bREQ[-_\s]?\d+\b", normalized, flags=re.IGNORECASE)) > 1:
            segments = [seg.strip() for seg in re.split(r"[。；;]+", normalized) if seg.strip()]

        for segment in segments:
            rule_id = _extract_rule_id(segment) or f"RULE-{len(rules) + 1:03d}"
            key = (rule_id, segment, current_biz_key)
            if key in seen:
                continue
            seen.add(key)
            rules.append(
                {
                    "rule_id": rule_id,
                    "rule_text": segment,
                    "biz_key": current_biz_key,
                    **_classify_requirement_rule(segment),
                }
            )

    if not rules:
        for sentence in re.split(r"[\n。；;]+", text):
            normalized = str(sentence or "").strip()
            if len(normalized) < 6:
                continue
            normalized = re.sub(r"^\s*[-*•]\s*", "", normalized).strip()
            if _looks_like_heading_or_fragment(normalized):
                continue
            if _is_low_confidence_requirement_discussion(normalized):
                continue
            if not _has_rule_action_signal(normalized) and not _extract_rule_id(normalized):
                continue
            rule_id = _extract_rule_id(normalized) or f"RULE-{len(rules) + 1:03d}"
            key = (rule_id, normalized, "unknown")
            if key in seen:
                continue
            seen.add(key)
            rules.append(
                {
                    "rule_id": rule_id,
                    "rule_text": normalized,
                    "biz_key": "unknown",
                    **_classify_requirement_rule(normalized),
                }
            )
            if len(rules) >= 120:
                break

    return rules[:120]

__all__ = [
    "_ambiguous_fragment_reason",
    "_classify_requirement_rule",
    "_extract_requirement_rules",
    "_has_rule_action_signal",
    "_is_low_confidence_requirement_discussion",
    "_looks_like_heading_or_fragment",
]
