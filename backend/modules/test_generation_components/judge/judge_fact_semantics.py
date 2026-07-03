from __future__ import annotations

import json
from typing import Any

from .judge_text_utils import (
    _dedupe_texts as _dedupe_texts,
    _normalize_text as _normalize_text,
)


_FACT_SEMANTIC_KEYS = (
    "confirmed_facts",
    "scoped_rules",
    "forbidden_facts",
    "pending_items",
    "reuse_declarations",
    "hard_flow_constraints",
    "reuse_risks",
)

_PROTECTED_FACT_KEYS = (
    "confirmed_facts",
    "scoped_rules",
    "hard_flow_constraints",
)


def normalize_requirement_semantics_context(
    requirement_semantics_context: dict[str, Any] | str | None,
) -> dict[str, list[str]]:
    payload: dict[str, Any] = {}
    if isinstance(requirement_semantics_context, dict):
        payload = dict(requirement_semantics_context)
    elif isinstance(requirement_semantics_context, str):
        text = requirement_semantics_context.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                decoded = json.loads(text)
                if isinstance(decoded, dict):
                    payload = decoded
            except Exception:
                payload = {}

    return {
        key: _dedupe_texts(payload.get(key) if isinstance(payload, dict) else [])
        for key in _FACT_SEMANTIC_KEYS
    }


def _merge_fact_profile_semantics(
    semantics: dict[str, list[str]],
    control_state: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    if not isinstance(control_state, dict):
        return semantics
    source_meta = control_state.get("source_meta") if isinstance(control_state.get("source_meta"), dict) else {}
    profile = source_meta.get("fact_profile") if isinstance(source_meta, dict) else None
    if not isinstance(profile, dict):
        return semantics
    merged = {key: list(value or []) for key, value in (semantics or {}).items()}
    for key in _FACT_SEMANTIC_KEYS:
        values = profile.get(key)
        if isinstance(values, list):
            merged[key] = _dedupe_texts([*merged.get(key, []), *values])

    protected_fact_keys = {
        _normalize_text(item)
        for key in _PROTECTED_FACT_KEYS
        for item in (merged.get(key) or [])
        if _normalize_text(item)
    }
    if protected_fact_keys:
        merged["forbidden_facts"] = [
            item
            for item in (merged.get("forbidden_facts") or [])
            if _normalize_text(item) and _normalize_text(item) not in protected_fact_keys
        ]
    return merged


__all__ = [
    "normalize_requirement_semantics_context",
    "_merge_fact_profile_semantics",
]
