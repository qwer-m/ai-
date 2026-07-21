"""Controlled persistence path for recurring sample-pool signals into registry candidates.

When the priority sample pool accumulates recurring signals (patterns that appear
across multiple samples with high confidence/weight), they can be persisted as
registry candidates for human review.

Candidates are stored alongside the static registry data and can be loaded by
scenario_registry.py to augment the active policy set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .scenario_registry import infer_primary_domain_tag

_CANDIDATES_DATA_PATH = Path(__file__).with_name("registry_candidates_data.json")


@dataclass
class RegistryCandidate:
    key: str
    keywords: tuple[str, ...]
    proposed_action: str  # "add_scenario", "update_cap", "update_keywords", "deprecate"
    domain: str = "general"
    reason: str = ""
    source_signal_ids: tuple[str, ...] = ()
    source_pattern_ids: tuple[str, ...] = ()
    proposed_default_cap: int = 1
    proposed_mode_caps: dict[str, int] = field(default_factory=dict)
    status: str = "pending"  # "pending", "accepted", "rejected"
    created_at: str = ""
    resolution_note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "keywords": list(self.keywords),
            "proposed_action": self.proposed_action,
            "domain": self.domain,
            "reason": self.reason,
            "source_signal_ids": list(self.source_signal_ids),
            "source_pattern_ids": list(self.source_pattern_ids),
            "proposed_default_cap": self.proposed_default_cap,
            "proposed_mode_caps": dict(self.proposed_mode_caps),
            "status": self.status,
            "created_at": self.created_at,
            "resolution_note": self.resolution_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RegistryCandidate:
        def _str_tuple(key: str) -> tuple[str, ...]:
            raw = data.get(key, [])
            if isinstance(raw, list):
                return tuple(str(v) for v in raw if str(v or "").strip())
            return ()

        return cls(
            key=str(data.get("key") or "").strip(),
            keywords=_str_tuple("keywords"),
            proposed_action=str(data.get("proposed_action") or "add_scenario").strip(),
            domain=str(data.get("domain") or "general").strip(),
            reason=str(data.get("reason") or "").strip(),
            source_signal_ids=_str_tuple("source_signal_ids"),
            source_pattern_ids=_str_tuple("source_pattern_ids"),
            proposed_default_cap=max(1, int(data.get("proposed_default_cap", 1))),
            proposed_mode_caps=dict(data.get("proposed_mode_caps") or {}),
            status=str(data.get("status") or "pending").strip(),
            created_at=str(data.get("created_at") or "").strip(),
            resolution_note=str(data.get("resolution_note") or "").strip(),
        )


def _load_candidates_payload() -> dict[str, object]:
    if not _CANDIDATES_DATA_PATH.exists():
        return {"version": 1, "candidates": []}
    with _CANDIDATES_DATA_PATH.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict):
        return {"version": 1, "candidates": []}
    return payload


def _save_candidates_payload(payload: dict[str, object]) -> None:
    _CANDIDATES_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _CANDIDATES_DATA_PATH.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def load_registry_candidates(*, status_filter: str | None = "pending") -> tuple[RegistryCandidate, ...]:
    """Load registry candidates, optionally filtered by status."""
    payload = _load_candidates_payload()
    raw = payload.get("candidates", [])
    if not isinstance(raw, list):
        return ()
    candidates = tuple(RegistryCandidate.from_dict(item) for item in raw if isinstance(item, dict))
    if status_filter:
        candidates = tuple(c for c in candidates if c.status == status_filter)
    return candidates


def _upsert_candidate(candidate: RegistryCandidate) -> None:
    payload = _load_candidates_payload()
    raw_list = list(payload.get("candidates", []) or [])
    existing_idx = None
    for idx, item in enumerate(raw_list):
        if isinstance(item, dict) and str(item.get("key") or "") == candidate.key:
            existing_idx = idx
            break
    entry = candidate.to_dict()
    if existing_idx is not None:
        raw_list[existing_idx] = entry
    else:
        raw_list.append(entry)
    payload["candidates"] = raw_list
    _save_candidates_payload(payload)


def propose_registry_candidate(
    *,
    key: str,
    keywords: list[str],
    proposed_action: str = "add_scenario",
    domain: str = "general",
    reason: str = "",
    source_signal_ids: list[str] | None = None,
    source_pattern_ids: list[str] | None = None,
    proposed_default_cap: int = 1,
    proposed_mode_caps: dict[str, int] | None = None,
) -> RegistryCandidate:
    """Propose a registry change candidate from feedback signals.

    Returns the created candidate. If a candidate with the same key already
    exists, it is updated (e.g. sample_count increases, keywords merged).
    """
    existing = {c.key: c for c in load_registry_candidates(status_filter=None)}
    now = datetime.now(timezone.utc).isoformat()

    if key in existing:
        prev = existing[key]
        merged_keywords = tuple(sorted(set(prev.keywords) | set(keywords)))
        merged_signal_ids = tuple(sorted(set(prev.source_signal_ids) | set(source_signal_ids or ())))
        merged_pattern_ids = tuple(sorted(set(prev.source_pattern_ids) | set(source_pattern_ids or ())))
        candidate = RegistryCandidate(
            key=key,
            keywords=merged_keywords,
            proposed_action=prev.proposed_action if prev.proposed_action != "add_scenario" else proposed_action,
            domain=prev.domain if prev.domain != "general" else domain,
            reason=(prev.reason + "; " + reason) if reason else prev.reason,
            source_signal_ids=merged_signal_ids,
            source_pattern_ids=merged_pattern_ids,
            proposed_default_cap=max(prev.proposed_default_cap, proposed_default_cap),
            proposed_mode_caps={**prev.proposed_mode_caps, **(proposed_mode_caps or {})},
            status="pending",
            created_at=prev.created_at or now,
        )
    else:
        candidate = RegistryCandidate(
            key=key,
            keywords=tuple(keywords),
            proposed_action=proposed_action,
            domain=domain,
            reason=reason,
            source_signal_ids=tuple(source_signal_ids or ()),
            source_pattern_ids=tuple(source_pattern_ids or ()),
            proposed_default_cap=proposed_default_cap,
            proposed_mode_caps=proposed_mode_caps or {},
            created_at=now,
        )

    _upsert_candidate(candidate)
    _shadow_write_candidate(candidate)
    return candidate


def resolve_registry_candidate(key: str, *, status: str, resolution_note: str = "") -> RegistryCandidate | None:
    """Accept or reject a registry candidate."""
    candidates = {c.key: c for c in load_registry_candidates(status_filter=None)}
    if key not in candidates:
        return None
    candidate = candidates[key]
    updated = RegistryCandidate(
        key=candidate.key,
        keywords=candidate.keywords,
        proposed_action=candidate.proposed_action,
        domain=candidate.domain,
        reason=candidate.reason,
        source_signal_ids=candidate.source_signal_ids,
        source_pattern_ids=candidate.source_pattern_ids,
        proposed_default_cap=candidate.proposed_default_cap,
        proposed_mode_caps=candidate.proposed_mode_caps,
        status=status,
        created_at=candidate.created_at,
        resolution_note=resolution_note,
    )
    _upsert_candidate(updated)
    return updated


def propose_from_recurring_signals(
    *,
    signals: list[dict[str, Any]],
    patterns: list[dict[str, Any]],
    min_sample_count: int = 3,
    min_avg_weight: float = 0.6,
) -> list[RegistryCandidate]:
    """Scan recurring pool signals and propose candidates for signals that
    cross the recurrence threshold and have no matching registry entry.

    Only proposes candidates for signals with:
    - sample_count >= min_sample_count (recurring across multiple reviews)
    - avg_weight >= min_avg_weight (high quality signal)
    - pattern_category not already represented in the registry keywords
    """
    from .scenario_registry import (
        classify_registered_scenario_family,
        DOMAIN_POLICIES,
        SCENARIO_FAMILY_POLICIES,
    )

    existing_keys = {p.key for p in SCENARIO_FAMILY_POLICIES}
    registered_domains = {policy.key for policy in DOMAIN_POLICIES}
    proposed: list[RegistryCandidate] = []

    for pattern in patterns:
        sample_count = int(pattern.get("sample_count") or 0)
        avg_weight = float(pattern.get("avg_weight") or 0)
        if sample_count < min_sample_count or avg_weight < min_avg_weight:
            continue

        pattern_canonical = str(pattern.get("pattern_canonical") or "").strip()
        if not pattern_canonical:
            continue

        pattern_scope = str(pattern.get("pattern_scope") or "general").strip() or "general"
        if pattern_scope in {"general", "ui", "unknown"}:
            continue
        if pattern_scope not in registered_domains:
            continue
        if any(token in pattern_canonical.lower() for token in ("ui-only", "static ui", "layout-only", "copy-only", "display")):
            continue

        # Check if this pattern already matches a registered scenario family
        registry_match = classify_registered_scenario_family(
            pattern_canonical,
            include_domain_specific=True,
        )
        if registry_match:
            continue

        primary_domain = infer_primary_domain_tag(pattern_canonical)
        if primary_domain and primary_domain != pattern_scope:
            continue

        # Build a candidate key from the pattern category + cluster
        category = str(pattern.get("pattern_category") or "general").strip().lower()
        cluster = str(pattern.get("cluster_key") or "").strip()[:32]
        candidate_key = f"candidate_{category}_{cluster}" if cluster else f"candidate_{category}"
        # Sanitize: remove non-alphanumeric chars
        candidate_key = "".join(c for c in candidate_key if c.isalnum() or c == "_")[:80]

        if candidate_key in existing_keys:
            continue

        # Derive keywords from the pattern canonical text
        raw_tokens = [t.strip().lower() for t in pattern_canonical.replace(",", " ").split() if len(t.strip()) >= 2]
        keywords = list(dict.fromkeys(raw_tokens))[:12]  # dedupe, max 12

        if not keywords:
            continue

        signal_ids = [str(s.get("signal_id") or "") for s in signals
                      if str(s.get("pattern_id") or "") == str(pattern.get("pattern_id") or "")]
        signal_ids = [s for s in signal_ids if s]

        candidate = propose_registry_candidate(
            key=candidate_key,
            keywords=keywords,
            proposed_action="add_scenario",
            domain=pattern_scope,
            reason=f"Recurring signal: {pattern_canonical[:120]} (n={sample_count}, w={avg_weight:.2f})",
            source_signal_ids=signal_ids,
            source_pattern_ids=[str(pattern.get("pattern_id") or "")],
            proposed_default_cap=max(1, sample_count // 2),
        )
        proposed.append(candidate)

    return proposed


def _shadow_write_candidate(candidate: RegistryCandidate) -> None:
    """Persist candidate proposal as a quality feedback event for audit trail."""
    try:
        from modules.testing.sample_pool_shadow_store import shadow_write_event

        shadow_write_event(
            db=None,
            project_id=None,
            user_id=None,
            event_type="registry_candidate",
            payload=candidate.to_dict(),
        )
    except Exception:
        pass  # Shadow write is best-effort; candidate is already in JSON


def get_candidate_summary() -> dict[str, object]:
    """Return a diagnostic summary of current candidate state."""
    all_candidates = load_registry_candidates(status_filter=None)
    by_status: dict[str, int] = {}
    for c in all_candidates:
        by_status[c.status] = by_status.get(c.status, 0) + 1
    return {
        "registry_candidate_count": len(all_candidates),
        "registry_candidates_by_status": by_status,
        "registry_candidates_pending": [
            {"key": c.key, "action": c.proposed_action, "reason": c.reason[:120]}
            for c in all_candidates
            if c.status == "pending"
        ],
    }
