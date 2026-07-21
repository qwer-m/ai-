from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..postprocess.case_access import case_flat_text

_PRIMARY_DOMAIN_DOMINANCE_RATIO = 0.67


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
    judge_duplicate: bool = False


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
        source = str(raw.get("source") or "registry").strip() or "registry"
        raw_judge_duplicate = raw.get("judge_duplicate")
        judge_duplicate = (
            bool(raw_judge_duplicate)
            if raw_judge_duplicate is not None
            else bool(raw_judge_threshold or source in {"legacy_duplicate_policy", "legacy_judge_duplicate_policy"})
        )
        policies.append(
            ScenarioFamilyPolicy(
                key=key,
                keywords=keywords,
                default_cap=max(1, int(raw.get("default_cap", 1))),
                specific=bool(raw.get("specific", True)),
                precedence=int(raw.get("precedence", 0)),
                mode_caps={str(mode): max(1, int(cap)) for mode, cap in raw_mode_caps.items()},
                domain=str(raw.get("domain") or "general").strip() or "general",
                source=source,
                status=status,
                documents=_string_tuple(raw.get("documents", []), field_name=f"scenarios.{key}.documents"),
                judge_score_threshold=float(raw_judge_threshold.get("score", 0.20)) if isinstance(raw_judge_threshold, dict) else 0.20,
                judge_overlap_threshold=max(1, int(raw_judge_threshold.get("overlap", 5))) if isinstance(raw_judge_threshold, dict) else 5,
                cross_module=bool(raw.get("cross_module", True)),
                judge_duplicate=judge_duplicate,
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


def _load_candidate_payload() -> dict[str, object]:
    candidates_path = Path(__file__).with_name("registry_candidates_data.json")
    if not candidates_path.exists():
        return {"version": 1, "candidates": []}
    with candidates_path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    return payload if isinstance(payload, dict) else {"version": 1, "candidates": []}


def _candidate_status_count(status: str) -> int:
    try:
        raw = _load_candidate_payload().get("candidates", [])
        if not isinstance(raw, list):
            return 0
        return sum(
            1
            for item in raw
            if isinstance(item, dict) and str(item.get("status") or "").strip().lower() == status
        )
    except Exception:
        return 0


def _load_candidate_policies(*, status_filter: str = "accepted") -> tuple[ScenarioFamilyPolicy, ...]:
    """Load reviewed registry candidates as runtime ScenarioFamilyPolicy entries."""
    try:
        raw = _load_candidate_payload().get("candidates", [])
        if not isinstance(raw, list):
            return ()
        status_filter = str(status_filter or "").strip().lower()
        policies: list[ScenarioFamilyPolicy] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().lower()
            if status != status_filter:
                continue
            key = str(item.get("key") or "").strip()
            keywords = _string_tuple(item.get("keywords", []), field_name=f"candidates.{key}.keywords")
            if not key or not keywords:
                continue
            candidate_domain = str(item.get("domain") or "general").strip() or "general"
            raw_mode_caps = item.get("proposed_mode_caps") or {}
            if not isinstance(raw_mode_caps, dict):
                raw_mode_caps = {}
            policies.append(
                ScenarioFamilyPolicy(
                    key=key,
                    keywords=keywords,
                    default_cap=max(1, int(item.get("proposed_default_cap", 1))),
                    specific=False,
                    precedence=0,
                    mode_caps={str(m): max(1, int(c)) for m, c in raw_mode_caps.items()},
                    domain=candidate_domain,
                    source="candidate_pool",
                    status=status,
                    documents=(),
                    judge_score_threshold=0.20,
                    judge_overlap_threshold=5,
                    cross_module=False,
                    judge_duplicate=False,
                )
            )
        return tuple(policies)
    except Exception:
        return ()


_CANDIDATE_POLICIES = _load_candidate_policies()
_PENDING_CANDIDATE_COUNT = _candidate_status_count("pending")


def _merged_scenario_policies() -> tuple[ScenarioFamilyPolicy, ...]:
    return SCENARIO_FAMILY_POLICIES + _CANDIDATE_POLICIES


def iter_scenario_family_policies() -> Iterable[ScenarioFamilyPolicy]:
    return _merged_scenario_policies()


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


def infer_primary_domain_tag(text: str) -> str:
    """Return a single dominant domain, or an empty string when ambiguous."""
    scores = infer_domain_scores(text)
    if not scores:
        return ""
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    top_key, top_score = ordered[0]
    policy_by_key = {policy.key: policy for policy in DOMAIN_POLICIES}
    top_policy = policy_by_key.get(top_key)
    if top_policy:
        min_required_score = min(top_policy.min_strong_score, max(2, top_policy.min_tag_score))
    else:
        min_required_score = 2
    if top_score < min_required_score:
        return ""
    if len(ordered) > 1:
        second_score = ordered[1][1]
        if second_score > 0 and (second_score / float(top_score)) > _PRIMARY_DOMAIN_DOMINANCE_RATIO:
            return ""
    return top_key


def _policy_keyword_hit_count(text: str, keywords: tuple[str, ...]) -> int:
    lowered = str(text or "").lower()
    count = 0
    for keyword in keywords:
        token = str(keyword or "").strip().lower()
        if token and token in lowered:
            count += 1
    return count


def _policy_allowed_for_runtime_domain(
    policy: ScenarioFamilyPolicy,
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> bool:
    domain = str(policy.domain or "general").strip() or "general"
    if domain == "general":
        return True
    if include_domain_specific:
        return True
    if primary_domain:
        return domain == str(primary_domain)
    return False


def classify_registered_scenario_family(
    text: str,
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> str:
    if not str(text or "").strip():
        return ""
    candidates: list[tuple[int, int, str]] = []
    for policy in _merged_scenario_policies():
        if not _policy_allowed_for_runtime_domain(
            policy,
            primary_domain=primary_domain,
            include_domain_specific=include_domain_specific,
        ):
            continue
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


def scenario_pattern_entries(
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (policy.key, policy.keywords)
        for policy in _merged_scenario_policies()
        if _policy_allowed_for_runtime_domain(
            policy,
            primary_domain=primary_domain,
            include_domain_specific=include_domain_specific,
        )
    )


def specific_scenario_kinds() -> set[str]:
    return {policy.key for policy in _merged_scenario_policies() if policy.specific}


def judge_duplicate_scenario_kinds(
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> set[str]:
    return {
        policy.key
        for policy in _merged_scenario_policies()
        if policy.judge_duplicate
        and _policy_allowed_for_runtime_domain(
            policy,
            primary_domain=primary_domain,
            include_domain_specific=include_domain_specific,
        )
    }


def specific_scenario_precedence() -> dict[str, int]:
    return {
        policy.key: int(policy.precedence)
        for policy in _merged_scenario_policies()
        if policy.specific
    }


def judge_duplicate_thresholds(
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> dict[str, tuple[float, int]]:
    return {
        policy.key: (
            max(0.0, min(1.0, float(policy.judge_score_threshold))),
            max(1, int(policy.judge_overlap_threshold)),
        )
        for policy in _merged_scenario_policies()
        if _policy_allowed_for_runtime_domain(
            policy,
            primary_domain=primary_domain,
            include_domain_specific=include_domain_specific,
        )
    }


def cross_module_scenario_kinds(
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> set[str]:
    return {
        policy.key
        for policy in _merged_scenario_policies()
        if policy.judge_duplicate
        and policy.cross_module
        and _policy_allowed_for_runtime_domain(
            policy,
            primary_domain=primary_domain,
            include_domain_specific=include_domain_specific,
        )
    }


def default_scenario_caps() -> dict[str, int]:
    return {policy.key: max(1, int(policy.default_cap)) for policy in _merged_scenario_policies()}


def mode_scenario_caps() -> dict[str, dict[str, int]]:
    merged: dict[str, dict[str, int]] = {}
    for policy in _merged_scenario_policies():
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
        "scenario_policy_candidate_count": len(_CANDIDATE_POLICIES),
        "scenario_policy_pending_candidate_count": _PENDING_CANDIDATE_COUNT,
        "domain_policy_registered_count": len(DOMAIN_POLICIES),
        "scenario_policy_domains": domains,
        "scenario_policy_sources": sources,
        "scenario_policy_documents": sorted(documents),
    }


def _scenario_kind_from_key(scenario_key: str) -> str:
    value = str(scenario_key or "")
    parts = [part for part in value.split(":") if part]
    known_kinds = {policy.key for policy in _merged_scenario_policies()} | {
        "intent",
        "semantic",
        "toast",
        "list",
        "navigate",
    }
    for part in parts:
        if part in known_kinds:
            return part
    return parts[-1] if parts else value


def diagnose_registry_impact(
    cases: list[dict[str, Any]],
    *,
    scenario_keys: list[str] | None = None,
    judge_kinds: list[str] | None = None,
    primary_domain: str = "",
    mode: str = "",
) -> dict[str, object]:
    """Return diagnostics explaining which registry policies affected a batch.

    Reports:
    - Policies matched (scenario families classified)
    - Caps applied (per-scenario and mode-based)
    - Judge thresholds used
    - Cross-module policies that could affect dedup
    - Sources of matched policies (static registry vs candidate)
    """
    primary_domain = str(primary_domain or "").strip()
    if scenario_keys is None:
        scenario_keys = [
            classify_registered_scenario_family(_flatten_case_text(case), primary_domain=primary_domain)
            for case in cases
        ]

    kinds_seen: dict[str, int] = {}
    caps_used: dict[str, int] = {}
    policies_matched: list[dict[str, object]] = []
    policy_keys_seen: set[str] = set()
    policy_by_key = {policy.key: policy for policy in _merged_scenario_policies()}

    for key in scenario_keys:
        kind = _scenario_kind_from_key(key)
        kinds_seen[kind] = kinds_seen.get(kind, 0) + 1

        policy = policy_by_key.get(kind)
        if policy is None or policy.key in policy_keys_seen:
            continue
        policy_keys_seen.add(policy.key)
        mode_cap = (policy.mode_caps or {}).get(mode) if mode else None
        policies_matched.append({
            "key": policy.key,
            "domain": policy.domain,
            "source": policy.source,
            "specific": policy.specific,
            "default_cap": max(1, int(policy.default_cap)),
            "mode_cap": mode_cap,
            "cross_module": policy.cross_module,
            "judge_duplicate": policy.judge_duplicate,
            "judge_threshold": {
                "score": policy.judge_score_threshold,
                "overlap": policy.judge_overlap_threshold,
            },
            "status": policy.status,
            "documents": list(policy.documents),
        })
        caps_used[kind] = max(1, int(mode_cap if mode_cap is not None else policy.default_cap))

    # Collect judge thresholds for scenarios seen
    judge_thresholds_used: dict[str, dict[str, float | int]] = {}
    if judge_kinds:
        thresholds = judge_duplicate_thresholds(primary_domain=primary_domain)
        for k in judge_kinds:
            if k in thresholds:
                score, overlap = thresholds[k]
                judge_thresholds_used[k] = {"score": score, "overlap": overlap}

    return {
        "registry_version": 1,
        "total_policies_available": len(_merged_scenario_policies()),
        "candidate_policies_active": len(_CANDIDATE_POLICIES),
        "candidate_policies_pending": _PENDING_CANDIDATE_COUNT,
        "primary_domain": primary_domain,
        "scenario_kinds_seen": kinds_seen,
        "policies_matched": policies_matched,
        "matched_documents": sorted(
            {
                str(document)
                for policy in policies_matched
                for document in (policy.get("documents") or [])
                if str(document).strip()
            }
        ),
        "caps_applied": caps_used,
        "judge_thresholds_used": judge_thresholds_used,
        "cross_module_policies_in_effect": sorted(
            str(policy.get("key") or "")
            for policy in policies_matched
            if bool(policy.get("cross_module")) and str(policy.get("key") or "").strip()
        )[:50],
    }


def _flatten_case_text(case: dict[str, Any]) -> str:
    return case_flat_text(
        case,
        fields=("test_module", "module", "description", "title", "expected_result", "expected", "steps"),
    )
