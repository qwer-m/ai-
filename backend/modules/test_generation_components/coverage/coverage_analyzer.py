from __future__ import annotations

import re
import unicodedata
from typing import Any


_STOPWORDS = {
    "以及",
    "或者",
    "并且",
    "如果",
    "那么",
    "需要",
    "可以",
    "必须",
    "系统",
    "模块",
    "页面",
    "用户",
    "功能",
    "流程",
    "规则",
}

_BOUNDARY_HINTS = {"边界", "上限", "下限", "最大", "最小", "临界", "范围", "boundary", "max", "min"}
_BOUNDARY_REQUIRED_HINTS = {
    "边界",
    "上限",
    "下限",
    "最大",
    "最小",
    "临界",
    "超过",
    "少于",
    "至少",
    "至多",
    "最多",
    "最少",
    "boundary",
    "max",
    "min",
}

_EXCEPTION_HINTS = {"异常", "失败", "错误", "拒绝", "超时", "fail", "error", "exception", "invalid"}
_RISK_HINTS = {"权限", "安全", "鉴权", "并发", "性能", "风控", "risk", "security", "permission", "performance"}

_RULE_ACTION_HINTS = (
    "新增",
    "调整",
    "插入",
    "后移",
    "保持",
    "保留",
    "隐藏",
    "显示",
    "展示",
    "支持",
    "点击",
    "返回",
    "切换",
    "播放",
    "打印",
    "适配",
    "不变",
    "不做改动",
    "只保留",
    "增加入口",
    "must",
    "should",
    "hide",
    "show",
    "display",
    "keep",
    "support",
)

_HEADING_PATTERNS = (
    r"^[一二三四五六七八九十]+[、.．]\s*[^：:]{1,24}$",
    r"^\d+[、.．]\s*[^：:]{1,24}$",
    r".*说明$",
    r".*调整说明$",
)

_GENERIC_NON_BLOCKING_RULES = {
    "页面布局与展示",
    "页面布局展示",
    "页面展示",
    "布局展示",
    "页面布局",
    "展示说明",
    "交互说明",
}

_OCR_CHAR_TRANSLATION = str.maketrans(
    {
        "⾼": "高",
        "⾸": "首",
        "⻚": "页",
        "⽂": "文",
        "⽣": "生",
        "⼊": "入",
        "⼝": "口",
        "⼆": "二",
        "⼀": "一",
        "⽬": "目",
        "⽤": "用",
        "⼾": "户",
        "⽀": "支",
        "⻓": "长",
        "⽅": "方",
        "⻅": "见",
        "⽇": "日",
        "⾃": "自",
        "⼒": "力",
        "⾄": "至",
        "⼼": "心",
        "⼯": "工",
        "⽆": "无",
        "⽹": "网",
    }
)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.translate(_OCR_CHAR_TRANSLATION)
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", normalized)
    normalized = normalized.replace("\u3000", " ")
    return normalized


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


def _tokenize(text: str, limit: int = 18) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}", _normalize_text(text))
    output: list[str] = []
    seen: set[str] = set()
    expanded: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", token):
            for idx in range(0, len(token) - 1):
                expanded.append(token[idx : idx + 2])
        else:
            expanded.append(token)
    for token in expanded:
        key = token.lower()
        if key in seen or key in _STOPWORDS:
            continue
        seen.add(key)
        output.append(token)
        if len(output) >= max(6, int(limit)):
            break
    return output


def _extract_rule_id(text: str) -> str | None:
    match = re.search(r"\bREQ[-_\s]?\d+\b", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(0).upper().replace(" ", "")


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
    text = _normalize_text(requirement_context).strip()
    if not text:
        return []

    rules: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    current_biz_key = "unknown"

    for raw_line in text.splitlines():
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


def _flatten_case_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("id", "description", "test_module", "test_input", "expected_result"):
        value = case.get(key)
        if value:
            parts.append(str(value))
    for key in ("steps", "preconditions"):
        value = case.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif isinstance(value, str):
            parts.append(value)
    return _normalize_text("\n".join(parts))


def _detect_case_types(case_text: str) -> set[str]:
    lowered = _normalize_text(case_text).lower()
    types: set[str] = set()
    if any(keyword in lowered for keyword in _BOUNDARY_HINTS):
        types.add("boundary")
    if any(keyword in lowered for keyword in _EXCEPTION_HINTS):
        types.add("exception")
    if any(keyword in lowered for keyword in _RISK_HINTS):
        types.add("risk")
    if not types:
        types.add("happy")
    else:
        types.add("happy")
    return types


def _required_types_for_rule(rule_text: str) -> set[str]:
    lowered = _normalize_text(rule_text).lower()
    required = {"happy"}
    if any(keyword in lowered for keyword in _BOUNDARY_REQUIRED_HINTS):
        required.add("boundary")
    if any(keyword in lowered for keyword in _EXCEPTION_HINTS):
        required.add("exception")
    if any(keyword in lowered for keyword in _RISK_HINTS):
        required.add("risk")
    return required


def _is_rule_hit(rule: dict[str, Any], case_text: str) -> bool:
    lowered_case = _normalize_text(case_text).lower()
    rule_id = str(rule.get("rule_id") or "").strip().lower().replace(" ", "")
    rule_text = _normalize_text(str(rule.get("rule_text") or "")).strip()
    if rule_id and rule_id in lowered_case.replace(" ", ""):
        return True
    if rule_text and rule_text.lower() in lowered_case:
        return True
    tokens = _tokenize(rule_text, limit=18)
    if not tokens:
        return False
    hit_count = sum(1 for token in tokens if token.lower() in lowered_case)
    strong_hits = [
        token
        for token in tokens
        if len(token) >= 2 and token.lower() in lowered_case and token.lower() not in _STOPWORDS
    ]
    if len(strong_hits) >= 2 and any(hint.lower() in lowered_case for hint in _RULE_ACTION_HINTS):
        return True
    return (hit_count / len(tokens)) >= 0.35


def analyze_coverage(requirement_context: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """中文注释：规则级覆盖诊断（可直接驱动 gap 阶段精准补漏）。"""
    normalized_cases = [item for item in (cases or []) if isinstance(item, dict)]
    rules = _extract_requirement_rules(requirement_context)
    blocking_rules = [rule for rule in rules if bool(rule.get("blocking", True))]
    total_rules = len(blocking_rules)
    if total_rules <= 0:
        return {
            "total_rules": 0,
            "total_extracted_rules": len(rules),
            "non_blocking_rules": [rule.get("rule_id") for rule in rules if not bool(rule.get("blocking", True))],
            "covered_rules": [],
            "missing_rules": [],
            "rule_diagnostics": [],
            "coverage_rate": 1.0,
            "missing_types": {"boundary": [], "exception": []},
        }

    case_texts = [_flatten_case_text(case) for case in normalized_cases]
    case_type_map = [_detect_case_types(text) for text in case_texts]

    covered_rules: list[str] = []
    missing_rules: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    missing_boundary: list[str] = []
    missing_exception: list[str] = []

    for rule in rules:
        required_types = _required_types_for_rule(rule.get("rule_text") or "")
        coverage_types: set[str] = set()
        for idx, case_text in enumerate(case_texts):
            if _is_rule_hit(rule, case_text):
                coverage_types.update(case_type_map[idx])
        covered = bool(coverage_types)
        blocking = bool(rule.get("blocking", True))
        if covered and blocking:
            covered_rules.append(rule["rule_id"])
            missing_types = sorted(required_types - coverage_types)
        elif not covered and blocking:
            missing_rules.append(rule["rule_id"])
            missing_types = sorted(required_types)
        else:
            missing_types = []
        if blocking and "boundary" in missing_types:
            missing_boundary.append(rule["rule_id"])
        if blocking and "exception" in missing_types:
            missing_exception.append(rule["rule_id"])
        diagnostics.append(
            {
                "rule_id": rule["rule_id"],
                "rule_text": rule["rule_text"],
                "biz_key": rule.get("biz_key") or "unknown",
                "rule_level": rule.get("rule_level") or ("hard" if blocking else "soft"),
                "confidence": rule.get("confidence") or ("high" if blocking else "low"),
                "source_type": rule.get("source_type") or "confirmed_requirement",
                "blocking": blocking,
                "non_blocking_reason": rule.get("non_blocking_reason") or "",
                "covered": covered,
                "coverage_types": sorted(coverage_types) if covered else [],
                "missing_types": missing_types,
            }
        )

    coverage_rate = round(len(covered_rules) / total_rules, 4) if total_rules else 1.0
    coverage_rate = max(0.0, min(1.0, coverage_rate))

    return {
        "total_rules": total_rules,
        "total_extracted_rules": len(rules),
        "non_blocking_rules": [rule.get("rule_id") for rule in rules if not bool(rule.get("blocking", True))],
        "covered_rules": covered_rules,
        "missing_rules": missing_rules,
        "rule_diagnostics": diagnostics,
        "coverage_rate": coverage_rate,
        "missing_types": {
            "boundary": sorted(set(missing_boundary)),
            "exception": sorted(set(missing_exception)),
        },
    }
