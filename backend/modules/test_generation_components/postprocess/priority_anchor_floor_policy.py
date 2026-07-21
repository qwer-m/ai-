from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .priority_behavior_semantics import (
    has_cross_module_structure,
    has_generic_blocking_outcome,
    has_generic_non_blocking_behavior,
    has_structured_blocking_priority_evidence,
    has_structured_core_signal,
    is_structured_non_blocking_detail,
    structured_anchor_family,
)


@dataclass(frozen=True)
class MainPathAnchorPolicy:
    configured_anchor_family_fn: Callable[[str], str]
    has_core_signal_fn: Callable[[str], bool]
    has_low_value_signal_fn: Callable[[str], bool]
    complexity_profile_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None

    def anchor_family(self, text: str, *, item: dict[str, Any] | None = None) -> str:
        structured_family = structured_anchor_family(item)
        if structured_family:
            return structured_family
        configured_family = str(self.configured_anchor_family_fn(text) or "").strip()
        return configured_family or "general"

    def has_strong_anchor(self, text: str, *, item: dict[str, Any] | None = None) -> bool:
        if is_structured_non_blocking_detail(item):
            return False
        return bool(
            has_structured_core_signal(item)
            or self.has_core_signal_fn(text)
            or has_generic_blocking_outcome(text)
        )

    def critical_anchor_family(self, text: str, *, item: dict[str, Any] | None = None) -> str:
        structured_family = structured_anchor_family(item)
        if structured_family and structured_family != "cross_module":
            return structured_family
        if is_structured_non_blocking_detail(item):
            return ""
        return str(self.configured_anchor_family_fn(text) or "").strip()

    def has_critical_anchor(self, text: str, *, item: dict[str, Any] | None = None) -> bool:
        return bool(self.critical_anchor_family(text, item=item))

    def has_low_value_anchor(self, text: str, *, item: dict[str, Any] | None = None) -> bool:
        return bool(
            is_structured_non_blocking_detail(item)
            or has_generic_non_blocking_behavior(text)
            or self.has_low_value_signal_fn(text)
        )

    def has_non_blocking_detail_anchor(
        self,
        text: str,
        *,
        item: dict[str, Any] | None = None,
    ) -> bool:
        return bool(
            is_structured_non_blocking_detail(item)
            or has_generic_non_blocking_behavior(text)
        )

    def has_blocking_anchor(self, text: str, *, item: dict[str, Any] | None = None) -> bool:
        if is_structured_non_blocking_detail(item):
            return False
        return bool(
            has_structured_blocking_priority_evidence(item)
            or self.critical_anchor_family(text, item=item)
            or has_generic_blocking_outcome(text)
        )

    def should_demote_non_blocking(
        self,
        text: str,
        *,
        item: dict[str, Any] | None = None,
        critical_family: str | None = None,
    ) -> bool:
        family = (
            self.critical_anchor_family(text, item=item)
            if critical_family is None
            else critical_family
        )
        return bool(
            not family
            and self.has_low_value_anchor(text, item=item)
            and not self.has_blocking_anchor(text, item=item)
        )

    def complexity_penalty(self, item: dict[str, Any]) -> int:
        if self.complexity_profile_fn is None:
            return 0
        try:
            return 4 * int((self.complexity_profile_fn(item) or {}).get("complexity_score") or 0)
        except Exception:
            return 0

    def primary_rank(
        self,
        *,
        item: dict[str, Any],
        index: int,
        text: str,
        normalized_priority: str,
    ) -> tuple[int, int, str, dict[str, Any]] | None:
        score = 0
        if has_structured_core_signal(item):
            score += 24
        if has_cross_module_structure(item):
            score += 6
        if self.has_strong_anchor(text, item=item):
            score += 10
        if self.has_low_value_anchor(text, item=item):
            score -= 12
        if normalized_priority == "P1":
            score += 6

        critical_family = self.critical_anchor_family(text, item=item)
        if critical_family:
            score += 70
        if self.should_demote_non_blocking(
            text,
            item=item,
            critical_family=critical_family,
        ):
            score -= 40
        if str(item.get("priority_decision_state") or "").strip().lower() in {"optional", "invalid"}:
            score -= 20
        score -= self.complexity_penalty(item)
        if score >= 10:
            return (score, -index, critical_family or self.anchor_family(text, item=item), item)
        return None

    def fallback_rank(
        self,
        *,
        item: dict[str, Any],
        index: int,
        text: str,
        normalized_priority: str,
        mode: str,
    ) -> tuple[int, int, str, dict[str, Any]] | None:
        critical_family = self.critical_anchor_family(text, item=item)
        if self.should_demote_non_blocking(
            text,
            item=item,
            critical_family=critical_family,
        ):
            return None
        if mode == "full_functional_regression" and not (
            self.has_strong_anchor(text, item=item) or critical_family
        ):
            return None
        priority_bonus = 8 if normalized_priority == "P1" else 3 if normalized_priority == "P2" else 0
        fallback_score = priority_bonus + (60 if critical_family else 0) - self.complexity_penalty(item)
        if fallback_score >= 3:
            return (
                fallback_score,
                -index,
                critical_family or self.anchor_family(text, item=item),
                item,
            )
        return None


__all__ = ["MainPathAnchorPolicy"]
