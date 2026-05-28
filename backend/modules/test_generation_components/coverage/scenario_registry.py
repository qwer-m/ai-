from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ScenarioFamilyPolicy:
    key: str
    keywords: tuple[str, ...]
    default_cap: int = 1
    specific: bool = True
    precedence: int = 0
    mode_caps: dict[str, int] | None = None
    domain: str = "general"
    source: str = "registry"
    status: str = "active"
    documents: tuple[str, ...] = ()
    judge_score_threshold: float = 0.20
    judge_overlap_threshold: int = 5
    cross_module: bool = True


@dataclass(frozen=True)
class DomainPolicy:
    key: str
    hints: tuple[str, ...]
    dominant_threshold: float = 0.35
    min_strong_score: int = 3
    min_tag_score: int = 2
    source: str = "registry"
    status: str = "active"
    documents: tuple[str, ...] = ()


_REGISTRY_DATA_PATH = Path(__file__).with_name("scenario_registry_data.json")


def _load_registry_payload() -> dict[str, object]:
    with _REGISTRY_DATA_PATH.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict):
        raise ValueError("scenario registry data must be a JSON object")
    return payload


def _string_tuple(values: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise ValueError(f"scenario registry field {field_name!r} must be a list")
    return tuple(str(value) for value in values if str(value or "").strip())


def _build_domain_policies(payload: dict[str, object]) -> tuple[DomainPolicy, ...]:
    raw_domains = payload.get("domains", [])
    if not isinstance(raw_domains, list):
        raise ValueError("scenario registry field 'domains' must be a list")
    policies: list[DomainPolicy] = []
    seen_keys: set[str] = set()
    for raw in raw_domains:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        hints = _string_tuple(raw.get("hints", []), field_name=f"domains.{key}.hints")
        status = str(raw.get("status") or "active").strip().lower() or "active"
        if status == "disabled" or not key or not hints:
            continue
        if key in seen_keys:
            raise ValueError(f"duplicate domain policy key: {key}")
        seen_keys.add(key)
        policies.append(
            DomainPolicy(
                key=key,
                hints=hints,
                dominant_threshold=float(raw.get("dominant_threshold", 0.35)),
                min_strong_score=int(raw.get("min_strong_score", 3)),
                min_tag_score=int(raw.get("min_tag_score", 2)),
                source=str(raw.get("source") or "registry").strip() or "registry",
                status=status,
                documents=_string_tuple(raw.get("documents", []), field_name=f"domains.{key}.documents"),
            )
        )
    if not policies:
        raise ValueError("scenario registry must contain at least one domain policy")
    return tuple(policies)


def _build_scenario_policies(payload: dict[str, object]) -> tuple[ScenarioFamilyPolicy, ...]:
    raw_scenarios = payload.get("scenarios", [])
    if not isinstance(raw_scenarios, list):
        raise ValueError("scenario registry field 'scenarios' must be a list")
    policies: list[ScenarioFamilyPolicy] = []
    seen_keys: set[str] = set()
    for raw in raw_scenarios:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        keywords = _string_tuple(raw.get("keywords", []), field_name=f"scenarios.{key}.keywords")
        status = str(raw.get("status") or "active").strip().lower() or "active"
        if status == "disabled" or not key or not keywords:
            continue
        if key in seen_keys:
            raise ValueError(f"duplicate scenario family policy key: {key}")
        seen_keys.add(key)
        raw_mode_caps = raw.get("mode_caps") or {}
        if not isinstance(raw_mode_caps, dict):
            raise ValueError(f"scenario registry field 'scenarios.{key}.mode_caps' must be an object")
        raw_judge_threshold = raw.get("judge_threshold") or {}
        if raw_judge_threshold and not isinstance(raw_judge_threshold, dict):
            raise ValueError(f"scenario registry field 'scenarios.{key}.judge_threshold' must be an object")
        policies.append(
            ScenarioFamilyPolicy(
                key=key,
                keywords=keywords,
                default_cap=max(1, int(raw.get("default_cap", 1))),
                specific=bool(raw.get("specific", True)),
                precedence=int(raw.get("precedence", 0)),
                mode_caps={str(mode): max(1, int(cap)) for mode, cap in raw_mode_caps.items()},
                domain=str(raw.get("domain") or "general").strip() or "general",
                source=str(raw.get("source") or "registry").strip() or "registry",
                status=status,
                documents=_string_tuple(raw.get("documents", []), field_name=f"scenarios.{key}.documents"),
                judge_score_threshold=float(raw_judge_threshold.get("score", 0.20)) if isinstance(raw_judge_threshold, dict) else 0.20,
                judge_overlap_threshold=max(1, int(raw_judge_threshold.get("overlap", 5))) if isinstance(raw_judge_threshold, dict) else 5,
                cross_module=bool(raw.get("cross_module", True)),
            )
        )
    if not policies:
        raise ValueError("scenario registry must contain at least one scenario family policy")
    return tuple(policies)


def _validate_registry_links(
    domains: tuple[DomainPolicy, ...],
    scenarios: tuple[ScenarioFamilyPolicy, ...],
) -> None:
    domain_keys = {policy.key for policy in domains}
    domain_keys.add("general")
    unknown_domains = sorted({policy.domain for policy in scenarios if policy.domain not in domain_keys})
    if unknown_domains:
        raise ValueError(f"scenario registry references unknown domains: {', '.join(unknown_domains)}")


_REGISTRY_PAYLOAD = _load_registry_payload()
DOMAIN_POLICIES = _build_domain_policies(_REGISTRY_PAYLOAD)
SCENARIO_FAMILY_POLICIES = _build_scenario_policies(_REGISTRY_PAYLOAD)
_validate_registry_links(DOMAIN_POLICIES, SCENARIO_FAMILY_POLICIES)


def iter_scenario_family_policies() -> Iterable[ScenarioFamilyPolicy]:
    return SCENARIO_FAMILY_POLICIES


def iter_domain_policies() -> Iterable[DomainPolicy]:
    return DOMAIN_POLICIES


def infer_domain_scores(text: str) -> dict[str, int]:
    lowered = str(text or "").lower()
    if not lowered:
        return {}
    scores: dict[str, int] = {}
    for policy in DOMAIN_POLICIES:
        score = 0
        for hint in policy.hints:
            token = str(hint or "").strip().lower()
            if token and token in lowered:
                score += 1
        if score > 0:
            scores[policy.key] = int(score)
    return scores


def infer_domain_tags(text: str) -> set[str]:
    scores = infer_domain_scores(text)
    if not scores:
        return set()
    max_score = max(scores.values())
    if max_score < min(policy.min_strong_score for policy in DOMAIN_POLICIES):
        return {key for key, score in scores.items() if score > 0}
    tags: set[str] = set()
    policy_by_key = {policy.key: policy for policy in DOMAIN_POLICIES}
    for key, score in scores.items():
        policy = policy_by_key.get(key)
        min_score = policy.min_tag_score if policy else 2
        threshold = policy.dominant_threshold if policy else 0.35
        if score >= max(min_score, int(max_score * threshold)):
            tags.add(key)
    return tags


def _policy_keyword_hit_count(text: str, keywords: tuple[str, ...]) -> int:
    lowered = str(text or "").lower()
    count = 0
    for keyword in keywords:
        token = str(keyword or "").strip().lower()
        if token and token in lowered:
            count += 1
    return count


def classify_registered_scenario_family(text: str) -> str:
    if not str(text or "").strip():
        return ""
    candidates: list[tuple[int, int, str]] = []
    for policy in SCENARIO_FAMILY_POLICIES:
        hits = _policy_keyword_hit_count(text, policy.keywords)
        if hits <= 0:
            continue
        # Require either two weak hits or one highly specific phrase.
        if hits >= 2 or any(len(str(keyword or "")) >= 5 and str(keyword).lower() in str(text).lower() for keyword in policy.keywords):
            candidates.append((hits, -int(policy.precedence), policy.key))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][2]


def scenario_pattern_entries() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple((policy.key, policy.keywords) for policy in SCENARIO_FAMILY_POLICIES)


def specific_scenario_kinds() -> set[str]:
    return {policy.key for policy in SCENARIO_FAMILY_POLICIES if policy.specific}


def specific_scenario_precedence() -> dict[str, int]:
    return {
        policy.key: int(policy.precedence)
        for policy in SCENARIO_FAMILY_POLICIES
        if policy.specific
    }


def judge_duplicate_thresholds() -> dict[str, tuple[float, int]]:
    return {
        policy.key: (
            max(0.0, min(1.0, float(policy.judge_score_threshold))),
            max(1, int(policy.judge_overlap_threshold)),
        )
        for policy in SCENARIO_FAMILY_POLICIES
    }


def cross_module_scenario_kinds() -> set[str]:
    return {policy.key for policy in SCENARIO_FAMILY_POLICIES if policy.cross_module}


def default_scenario_caps() -> dict[str, int]:
    return {policy.key: max(1, int(policy.default_cap)) for policy in SCENARIO_FAMILY_POLICIES}


def mode_scenario_caps() -> dict[str, dict[str, int]]:
    merged: dict[str, dict[str, int]] = {}
    for policy in SCENARIO_FAMILY_POLICIES:
        for mode, cap in (policy.mode_caps or {}).items():
            merged.setdefault(str(mode), {})[policy.key] = max(1, int(cap))
    return merged


def scenario_registry_meta() -> dict[str, object]:
    domains: dict[str, int] = {}
    sources: dict[str, int] = {}
    documents: set[str] = set()
    for policy in SCENARIO_FAMILY_POLICIES:
        domains[policy.domain] = domains.get(policy.domain, 0) + 1
        sources[policy.source] = sources.get(policy.source, 0) + 1
        documents.update(policy.documents)
    return {
        "scenario_policy_registry_version": 1,
        "scenario_policy_registered_count": len(SCENARIO_FAMILY_POLICIES),
        "domain_policy_registered_count": len(DOMAIN_POLICIES),
        "scenario_policy_domains": domains,
        "scenario_policy_sources": sources,
        "scenario_policy_documents": sorted(documents),
    }
