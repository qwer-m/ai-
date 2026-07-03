from __future__ import annotations

from typing import Any

from .coverage_strategy import (
    boundary_hints,
    boundary_required_hints,
    exception_hints,
    risk_hints,
)
from .rule_coverage_extraction import (
    _ambiguous_fragment_reason,
    _classify_requirement_rule,
    _extract_requirement_rules,
    _has_rule_action_signal,
    _is_low_confidence_requirement_discussion,
    _looks_like_heading_or_fragment,
)
from .rule_coverage_text import _STOPWORDS, _extract_rule_id, _normalize_text, _tokenize

_BOUNDARY_HINTS = boundary_hints()
_BOUNDARY_REQUIRED_HINTS = boundary_required_hints()

_EXCEPTION_HINTS = exception_hints()
_RISK_HINTS = risk_hints()

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
    if len(strong_hits) >= 2 and _has_rule_action_signal(lowered_case):
        return True
    return (hit_count / len(tokens)) >= 0.35

def analyze_requirement_rule_coverage(requirement_context: str, case_texts: list[str]) -> dict[str, Any]:
    """Analyze requirement-rule coverage from already-flattened case texts."""
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

__all__ = [
    "_ambiguous_fragment_reason",
    "_classify_requirement_rule",
    "_extract_requirement_rules",
    "_extract_rule_id",
    "_has_rule_action_signal",
    "_is_low_confidence_requirement_discussion",
    "_looks_like_heading_or_fragment",
    "_normalize_text",
    "_tokenize",
    "analyze_requirement_rule_coverage",
]
