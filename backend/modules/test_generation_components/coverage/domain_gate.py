from __future__ import annotations

import re
from typing import Any

from .scenario_registry import (
    infer_domain_scores,
    infer_domain_tags,
    infer_primary_domain_tag,
    iter_domain_policies,
)


def _leading_requirement_topic(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ""
    parts = [part.strip() for part in re.split(r"[。.!?！？；;]", normalized, maxsplit=1) if part.strip()]
    return (parts[0] if parts else normalized)[:120]


def _hint_score(text: str, hints: tuple[str, ...]) -> int:
    lowered = str(text or "").lower()
    return sum(1 for hint in hints if str(hint or "").strip().lower() in lowered)


def current_domain_gate(requirement_text: str) -> dict[str, Any]:
    text = str(requirement_text or "").strip()
    tags = sorted(infer_domain_tags(text))
    primary = infer_primary_domain_tag(text)
    scores = infer_domain_scores(text)
    policy_by_key = {policy.key: policy for policy in iter_domain_policies()}
    if primary:
        policy = policy_by_key.get(primary)
        topic_text = _leading_requirement_topic(text)
        topic_score = _hint_score(topic_text, policy.hints if policy else ())
        primary_score = int(scores.get(primary) or 0)
        min_strong_score = int(policy.min_strong_score if policy else 3)
        strong_document_score = primary_score >= max(4, min_strong_score + 2)
        if topic_score > 0 or strong_document_score:
            status = "stable_primary"
            reason = "primary_domain_matched"
        else:
            status = "ambiguous_registered_domain"
            reason = "primary_domain_not_in_requirement_topic"
    elif tags:
        status = "ambiguous_registered_domain"
        reason = "registered_domain_tags_without_primary"
    else:
        status = "unclassified_domain"
        reason = "no_registered_domain_signal"
    return {
        "primary_domain": primary,
        "domain_tags": tags,
        "status": status,
        "reason": reason,
        "domain_scores": dict(scores),
        "allows_historical_profile": status != "ambiguous_registered_domain",
    }


def domain_gate_meta(prefix: str, gate: dict[str, Any]) -> dict[str, Any]:
    name = str(prefix or "domain_gate").strip()
    return {
        f"{name}_primary_domain": str(gate.get("primary_domain") or ""),
        f"{name}_domain_tags": list(gate.get("domain_tags") or []),
        f"{name}_domain_scores": dict(gate.get("domain_scores") or {}),
        f"{name}_status": str(gate.get("status") or ""),
        f"{name}_reason": str(gate.get("reason") or ""),
        f"{name}_allows_historical_profile": bool(gate.get("allows_historical_profile")),
    }


__all__ = ["current_domain_gate", "domain_gate_meta"]
