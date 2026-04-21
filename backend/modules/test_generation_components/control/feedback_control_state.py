from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _normalize_text_list(values: list[Any] | None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in (values or []):
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _normalize_rule_quota(raw_quota: dict[Any, Any] | None) -> dict[str, int]:
    quota: dict[str, int] = {}
    for key, value in dict(raw_quota or {}).items():
        rule = str(key or "").strip().upper()
        if not rule:
            continue
        try:
            amount = int(value)
        except Exception:
            amount = 0
        if amount <= 0:
            continue
        quota[rule] = max(quota.get(rule, 0), amount)
    return quota


def _merge_meta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(left or {})
    for key, value in dict(right or {}).items():
        if key not in merged:
            merged[key] = value
            continue
        left_value = merged.get(key)
        if isinstance(left_value, list) or isinstance(value, list):
            left_list = left_value if isinstance(left_value, list) else [left_value]
            right_list = value if isinstance(value, list) else [value]
            result: list[Any] = []
            seen: set[str] = set()
            for item in [*left_list, *right_list]:
                marker = str(item)
                if marker in seen:
                    continue
                seen.add(marker)
                result.append(item)
            merged[key] = result
            continue
        if isinstance(left_value, dict) and isinstance(value, dict):
            nested = dict(left_value)
            for nested_key, nested_value in value.items():
                if nested_key not in nested:
                    nested[nested_key] = nested_value
                    continue
                try:
                    nested[nested_key] = max(int(nested[nested_key]), int(nested_value))
                except Exception:
                    nested[nested_key] = nested_value
            merged[key] = nested
            continue
        merged[key] = value
    return merged


@dataclass
class FeedbackControlState:
    must_cover_rules: list[str] = field(default_factory=list)
    must_have_scenarios: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)
    preferred_patterns: list[str] = field(default_factory=list)
    reuse_risks: list[str] = field(default_factory=list)
    soft_constraints: list[str] = field(default_factory=list)
    rule_quota: dict[str, int] = field(default_factory=dict)
    quality_fix_hints: list[str] = field(default_factory=list)
    source_meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "FeedbackControlState":
        return cls()

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "FeedbackControlState":
        data = dict(payload or {})
        return cls(
            must_cover_rules=_normalize_text_list(data.get("must_cover_rules") or []),
            must_have_scenarios=_normalize_text_list(data.get("must_have_scenarios") or []),
            forbidden_patterns=_normalize_text_list(data.get("forbidden_patterns") or []),
            preferred_patterns=_normalize_text_list(
                data.get("preferred_patterns") or data.get("positive_patterns") or []
            ),
            reuse_risks=_normalize_text_list(data.get("reuse_risks") or []),
            soft_constraints=_normalize_text_list(data.get("soft_constraints") or data.get("negative_bias") or []),
            rule_quota=_normalize_rule_quota(data.get("rule_quota") or {}),
            quality_fix_hints=_normalize_text_list(data.get("quality_fix_hints") or []),
            source_meta=dict(data.get("source_meta") or {}),
        )

    @classmethod
    def from_any(cls, payload: Any) -> "FeedbackControlState":
        if isinstance(payload, FeedbackControlState):
            return payload
        if isinstance(payload, dict):
            return cls.from_dict(payload)
        return cls.empty()

    def to_dict(self) -> dict[str, Any]:
        return {
            "must_cover_rules": _normalize_text_list(self.must_cover_rules),
            "must_have_scenarios": _normalize_text_list(self.must_have_scenarios),
            "forbidden_patterns": _normalize_text_list(self.forbidden_patterns),
            "preferred_patterns": _normalize_text_list(self.preferred_patterns),
            "reuse_risks": _normalize_text_list(self.reuse_risks),
            "soft_constraints": _normalize_text_list(self.soft_constraints),
            "rule_quota": _normalize_rule_quota(self.rule_quota),
            "quality_fix_hints": _normalize_text_list(self.quality_fix_hints),
            "source_meta": dict(self.source_meta or {}),
        }

    def merge(self, other: "FeedbackControlState" | dict[str, Any] | None) -> "FeedbackControlState":
        target = FeedbackControlState.from_any(other)
        merged_quota = _normalize_rule_quota(self.rule_quota)
        for rule, quota in _normalize_rule_quota(target.rule_quota).items():
            merged_quota[rule] = max(merged_quota.get(rule, 0), quota)
        return FeedbackControlState(
            must_cover_rules=_normalize_text_list([*self.must_cover_rules, *target.must_cover_rules]),
            must_have_scenarios=_normalize_text_list([*self.must_have_scenarios, *target.must_have_scenarios]),
            forbidden_patterns=_normalize_text_list([*self.forbidden_patterns, *target.forbidden_patterns]),
            preferred_patterns=_normalize_text_list([*self.preferred_patterns, *target.preferred_patterns]),
            reuse_risks=_normalize_text_list([*self.reuse_risks, *target.reuse_risks]),
            soft_constraints=_normalize_text_list([*self.soft_constraints, *target.soft_constraints]),
            rule_quota=merged_quota,
            quality_fix_hints=_normalize_text_list([*self.quality_fix_hints, *target.quality_fix_hints]),
            source_meta=_merge_meta(dict(self.source_meta or {}), dict(target.source_meta or {})),
        )

    def has_signals(self) -> bool:
        return bool(
            self.must_cover_rules
            or self.must_have_scenarios
            or self.forbidden_patterns
            or self.preferred_patterns
            or self.reuse_risks
            or self.soft_constraints
            or self.rule_quota
            or self.quality_fix_hints
        )
