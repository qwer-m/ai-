from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from .case_access import case_flat_text


CASE_GOVERNANCE_MATCH_FIELDS = ("description", "test_module", "expected_result", "test_input", "steps")
CASE_GOVERNANCE_QUALITY_HINT_FIELDS = ("description", "expected_result", "test_input", "steps")


def normalize_match_text(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def case_match_text(case: dict[str, Any], fields: tuple[str, ...]) -> str:
    return normalize_match_text(case_flat_text(case, fields=fields, separator=" "))


def case_has_match_pattern(
    case: dict[str, Any],
    fields: tuple[str, ...],
    patterns: Iterable[str],
) -> bool:
    pattern_items = [pattern for pattern in (patterns or []) if pattern]
    if not pattern_items:
        return False
    text = case_match_text(case, fields)
    return any(pattern in text for pattern in pattern_items)


def normalize_match_patterns(values: Iterable[object]) -> list[str]:
    patterns: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = normalize_match_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        patterns.append(normalized)
    return patterns


def build_quality_hint_keywords(hints: Iterable[object], *, max_tokens_per_hint: int = 4) -> list[str]:
    keywords: list[str] = []
    for hint in hints or []:
        normalized_hint = normalize_match_text(hint)
        if not normalized_hint:
            continue
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{3,}", normalized_hint)
        for token in tokens[: max(1, int(max_tokens_per_hint))]:
            if token not in keywords:
                keywords.append(token)
    return keywords


@dataclass(frozen=True)
class CaseGovernanceMatcher:
    forbidden_patterns: tuple[str, ...] = ()
    reuse_risk_patterns: tuple[str, ...] = ()
    soft_constraint_patterns: tuple[str, ...] = ()
    quality_hint_keywords: tuple[str, ...] = ()

    @classmethod
    def from_raw(
        cls,
        *,
        forbidden_patterns: Iterable[object] = (),
        reuse_risks: Iterable[object] = (),
        soft_constraints: Iterable[object] = (),
        quality_fix_hints: Iterable[object] = (),
    ) -> "CaseGovernanceMatcher":
        return cls(
            forbidden_patterns=tuple(normalize_match_patterns(forbidden_patterns)),
            reuse_risk_patterns=tuple(normalize_match_patterns(reuse_risks)),
            soft_constraint_patterns=tuple(normalize_match_patterns(soft_constraints)),
            quality_hint_keywords=tuple(build_quality_hint_keywords(quality_fix_hints)),
        )

    def violates_forbidden_pattern(self, case: dict[str, Any]) -> bool:
        return case_has_match_pattern(
            case,
            CASE_GOVERNANCE_MATCH_FIELDS,
            self.forbidden_patterns,
        )

    def hits_soft_constraint(self, case: dict[str, Any]) -> bool:
        return case_has_match_pattern(
            case,
            CASE_GOVERNANCE_MATCH_FIELDS,
            self.soft_constraint_patterns,
        )

    def hits_reuse_risk(self, case: dict[str, Any], score_profile: dict[str, Any] | None = None) -> bool:
        if bool((score_profile or {}).get("reuse_risk_hit")):
            return True
        return case_has_match_pattern(
            case,
            CASE_GOVERNANCE_MATCH_FIELDS,
            self.reuse_risk_patterns,
        )

    def satisfies_quality_hint(self, case: dict[str, Any]) -> bool:
        return case_has_match_pattern(
            case,
            CASE_GOVERNANCE_QUALITY_HINT_FIELDS,
            self.quality_hint_keywords,
        )
