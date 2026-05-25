from __future__ import annotations

from typing import Any

from modules.test_generation_components.control.feedback_control_state import FeedbackControlState


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
    "不会",
    "不进入",
    "不显示",
    "不生成",
)


def _dedupe_texts(values: Any, *, limit: int = 80) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        values = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = " ".join(text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
        if len(output) >= max(1, int(limit)):
            break
    return output


def _merge_bucket_values(buckets: list[dict[str, Any]], key: str, *, limit: int = 80) -> list[str]:
    values: list[Any] = []
    for bucket in buckets:
        if isinstance(bucket, dict):
            values.extend(bucket.get(key) or [])
    return _dedupe_texts(values, limit=limit)


def _looks_negative(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return bool(lowered and any(marker.lower() in lowered for marker in _NEGATIVE_MARKERS))


def normalize_fact_profile(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    confirmed = _dedupe_texts(payload.get("confirmed_facts") or [])
    scoped_rules = _dedupe_texts(payload.get("scoped_rules") or [])
    hard_flow = _dedupe_texts(payload.get("hard_flow_constraints") or [])
    pending = _dedupe_texts(payload.get("pending_items") or [])
    reuse_declarations = _dedupe_texts(payload.get("reuse_declarations") or [])
    reuse_risks = _dedupe_texts(payload.get("reuse_risks") or [])
    forbidden = _dedupe_texts(
        [
            *(payload.get("forbidden_facts") or []),
            *[item for item in [*confirmed, *scoped_rules] if _looks_negative(item)],
        ]
    )
    source_priority = payload.get("source_priority")
    if not isinstance(source_priority, list):
        source_priority = [
            "current_requirement",
            "rag_requirement",
            "supplement_boundary",
            "testcase_style_only",
            "historical_feedback",
        ]
    return {
        "profile_version": str(payload.get("profile_version") or "fact-profile-v1"),
        "profile_source": str(payload.get("profile_source") or "requirement_semantics"),
        "confidence": float(payload.get("confidence") or (0.75 if (confirmed or scoped_rules or hard_flow) else 0.0)),
        "confirmed_facts": confirmed,
        "scoped_rules": scoped_rules,
        "forbidden_facts": forbidden,
        "pending_items": pending,
        "reuse_declarations": reuse_declarations,
        "hard_flow_constraints": hard_flow,
        "reuse_risks": reuse_risks,
        "source_priority": [str(item) for item in source_priority if str(item).strip()],
        "conflict_policy": str(payload.get("conflict_policy") or "current_requirement_wins"),
        "strategy_only": False,
    }


def build_fact_profile(
    *,
    requirement_semantics_by_biz: dict[str, dict[str, list[str]]] | None = None,
    current_biz_key: str = "",
    source: str = "requirement_semantics",
) -> dict[str, Any]:
    by_biz = requirement_semantics_by_biz if isinstance(requirement_semantics_by_biz, dict) else {}
    current = str(current_biz_key or "").strip() or "unknown"
    buckets: list[dict[str, Any]] = []
    if isinstance(by_biz.get(current), dict):
        buckets.append(dict(by_biz.get(current) or {}))
    for biz_key, bucket in by_biz.items():
        if biz_key == current or not isinstance(bucket, dict):
            continue
        buckets.append(dict(bucket))

    profile = normalize_fact_profile(
        {
            "profile_source": source,
            "confirmed_facts": _merge_bucket_values(buckets, "confirmed_facts"),
            "scoped_rules": _merge_bucket_values(buckets, "scoped_rules"),
            "pending_items": _merge_bucket_values(buckets, "pending_items"),
            "reuse_declarations": _merge_bucket_values(buckets, "reuse_declarations"),
            "hard_flow_constraints": _merge_bucket_values(buckets, "hard_flow_constraints"),
            "reuse_risks": _merge_bucket_values(buckets, "reuse_risks"),
        }
    )
    profile["current_biz_key"] = current
    profile["biz_key_count"] = int(len(by_biz))
    return profile


def merge_fact_profile_control_state(base_state: Any, fact_profile: dict[str, Any] | None) -> FeedbackControlState:
    normalized_base = FeedbackControlState.from_any(base_state)
    profile = normalize_fact_profile(fact_profile or {})
    meaningful = bool(
        profile.get("confirmed_facts")
        or profile.get("scoped_rules")
        or profile.get("forbidden_facts")
        or profile.get("pending_items")
        or profile.get("hard_flow_constraints")
        or profile.get("reuse_risks")
    )
    if not profile or not meaningful:
        return normalized_base
    return normalized_base.merge(
        {
            "reuse_risks": profile.get("reuse_risks") or [],
            "source_meta": {
                "sources": ["fact_profile"],
                "fact_profile": profile,
            },
        }
    )
