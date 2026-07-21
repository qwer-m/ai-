from __future__ import annotations

import re
from typing import Any

from .coverage_strategy import (
    intent_action_keywords,
    intent_outcome_keywords,
    intent_stopwords,
)
from .flow_outline import (
    _CROSS_CUTTING_DEFINITIONS,
    _FLOW_STAGE_DEFINITIONS,
    _canonical_stage_label,
)
from .rule_coverage import _normalize_text, _tokenize
from .scenario_registry import (
    iter_scenario_family_policies,
    scenario_pattern_entries,
    specific_scenario_kinds,
    specific_scenario_precedence,
)
from ..postprocess.case_access import case_flat_text, case_steps, case_text_field


def _flatten_case_text(case: dict[str, Any]) -> str:
    return _normalize_text(
        case_flat_text(
            case,
            fields=("id", "description", "test_module", "test_input", "expected_result", "steps", "preconditions"),
        )
    )


def _flatten_case_intent_text(case: dict[str, Any]) -> str:
    return _normalize_text(
        case_flat_text(case, fields=("description", "test_module", "test_input", "expected_result", "steps"))
    )


_SCENARIO_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = scenario_pattern_entries(
    include_domain_specific=True
)
_SCENARIO_POLICY_BY_KEY = {policy.key: policy for policy in iter_scenario_family_policies()}

_SPECIFIC_SCENARIO_KINDS = specific_scenario_kinds()
_SPECIFIC_SCENARIO_PRECEDENCE = specific_scenario_precedence()


_INTENT_ACTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = intent_action_keywords()
_INTENT_OUTCOME_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = intent_outcome_keywords()
_INTENT_STOPWORDS = intent_stopwords()


def _coverage_match_text(value: Any) -> str:
    return (
        str(value or "")
        .replace("⻔", "门")
        .replace("戶", "户")
        .replace("户", "户")
    )


def _stage_label_aliases(label: str, key: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for value in (
        label,
        _canonical_stage_label(label),
        str(key).split(":", 1)[-1].replace("_", " "),
    ):
        normalized = _coverage_match_text(value).strip()
        if normalized:
            aliases.append(normalized)
    for token in _tokenize(_coverage_match_text(label), limit=10):
        normalized = _coverage_match_text(token).strip()
        if len(normalized) < 2:
            continue
        if normalized.lower() in _INTENT_STOPWORDS:
            continue
        aliases.append(normalized)
    return tuple(dict.fromkeys(aliases))


def _keyword_score(text: str, keywords: tuple[str, ...]) -> int:
    score = 0
    normalized = _coverage_match_text(text)
    for keyword in keywords:
        if not keyword:
            continue
        candidate = _coverage_match_text(keyword)
        if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_\s-]*", candidate):
            hit = bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_])", normalized, flags=re.IGNORECASE))
        else:
            hit = candidate in normalized
        if hit:
            score += max(1, min(4, len(candidate) // 2))
    return score


def _keyword_hit_count(text: str, keywords: tuple[str, ...]) -> int:
    normalized = _coverage_match_text(text)
    count = 0
    for keyword in keywords:
        if not keyword:
            continue
        candidate = _coverage_match_text(keyword)
        if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_\s-]*", candidate):
            hit = bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_])", normalized, flags=re.IGNORECASE))
        else:
            hit = candidate in normalized
        if hit:
            count += 1
    return count


def _specific_scenario_matches(scenario_key: str, text: str, keywords: tuple[str, ...]) -> bool:
    normalized = str(text or "")
    return _keyword_score(normalized, keywords) >= 4 or _keyword_hit_count(normalized, keywords) >= 2


def classify_case_flow_stage(case: dict[str, Any], flow_outline: dict[str, Any] | None = None) -> str:
    text = _flatten_case_text(case)
    case_module_stage = _canonical_stage_label(case_text_field(case, "test_module"))
    if isinstance(flow_outline, dict):
        candidates: list[tuple[int, int, str]] = []
        labels = dict(flow_outline.get("flow_labels") or {})
        for index, key in enumerate(flow_outline.get("flow_order") or []):
            label = str(labels.get(key) or "")
            aliases = _stage_label_aliases(label, str(key))
            score = _keyword_score(text, aliases) if aliases else 0
            if case_module_stage and case_module_stage == _canonical_stage_label(label):
                score += 8
            if score > 0:
                candidates.append((score, -index, str(key)))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][2] or "unknown"
    scored: list[tuple[int, int, str]] = []
    for index, definition in enumerate(_FLOW_STAGE_DEFINITIONS):
        score = _keyword_score(text, tuple(definition.get("keywords") or ()))
        if score > 0:
            scored.append((score, -index, str(definition.get("key") or "")))
    if not scored:
        return "unknown"
    scored.sort(reverse=True)
    return scored[0][2] or "unknown"


def classify_case_cross_cutting(case: dict[str, Any], flow_outline: dict[str, Any] | None = None) -> list[str]:
    text = _flatten_case_text(case)
    hits: list[str] = []
    if isinstance(flow_outline, dict):
        labels = dict(flow_outline.get("cross_cutting_labels") or {})
        for key in flow_outline.get("cross_cutting") or []:
            label = str(labels.get(key) or "")
            if label and _keyword_score(text, (label,)) > 0:
                hits.append(str(key))
    for definition in _CROSS_CUTTING_DEFINITIONS:
        score = _keyword_score(text, tuple(definition.get("keywords") or ()))
        if score > 0:
            hits.append(str(definition.get("key") or ""))
    return [item for item in hits if item]


def _first_keyword_label(text: str, patterns: tuple[tuple[str, tuple[str, ...]], ...], default: str) -> str:
    lowered = _normalize_text(text).lower()
    for label, keywords in patterns:
        if any(str(keyword or "").lower() in lowered for keyword in keywords if str(keyword or "").strip()):
            return label
    return default


def _case_intent_parts(case: dict[str, Any]) -> tuple[str, str, str]:
    description = case_text_field(case, "description")
    module = _canonical_stage_label(case_text_field(case, "test_module"))
    expected = case_text_field(case, "expected_result")
    steps = " ".join(case_steps(case))
    intent_text = "\n".join([module, description, steps, expected])
    action = _first_keyword_label(intent_text, _INTENT_ACTION_KEYWORDS, "observe")
    outcome = _first_keyword_label(expected or intent_text, _INTENT_OUTCOME_KEYWORDS, "content")
    object_tokens = [
        token.lower()
        for token in _tokenize("\n".join([module, description, expected]), limit=12)
        if token.lower() not in _INTENT_STOPWORDS
        and token.lower() not in {action, outcome}
        and len(token.strip()) >= 2
    ]
    compact_object = "_".join(object_tokens[:3]) or "general"
    return action, compact_object, outcome


def _scenario_policy_allowed_for_domains(
    scenario_key: str,
    *,
    primary_domain: str = "",
    domain_tags: set[str] | None = None,
) -> bool:
    policy = _SCENARIO_POLICY_BY_KEY.get(str(scenario_key or ""))
    if policy is None:
        return True
    policy_domain = str(policy.domain or "general").strip() or "general"
    if policy_domain == "general":
        return True
    if primary_domain:
        return policy_domain == str(primary_domain)
    if domain_tags is None:
        return True
    if len(domain_tags) == 1:
        return policy_domain in domain_tags
    return False


def _scenario_patterns_for_domain(
    primary_domain: str = "",
    *,
    domain_tags: set[str] | None = None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (scenario_key, keywords)
        for scenario_key, keywords in _SCENARIO_PATTERNS
        if _scenario_policy_allowed_for_domains(
            scenario_key,
            primary_domain=primary_domain,
            domain_tags=domain_tags,
        )
    )


def classify_case_scenario_key(
    case: dict[str, Any],
    flow_stage: str | None = None,
    *,
    primary_domain: str = "",
    domain_tags: set[str] | None = None,
) -> str:
    text = _flatten_case_text(case)
    intent_text = _flatten_case_intent_text(case)
    stage = str(flow_stage or "unknown")
    action, compact_object, outcome = _case_intent_parts(case)
    scenario_patterns = _scenario_patterns_for_domain(primary_domain, domain_tags=domain_tags)
    specific_patterns = [
        (scenario_key, keywords)
        for scenario_key, keywords in scenario_patterns
        if scenario_key in _SPECIFIC_SCENARIO_KINDS
    ]
    specific_patterns.sort(key=lambda item: (_SPECIFIC_SCENARIO_PRECEDENCE.get(item[0], 10), item[0]))
    generic_patterns = [
        (scenario_key, keywords)
        for scenario_key, keywords in scenario_patterns
        if scenario_key not in _SPECIFIC_SCENARIO_KINDS
    ]
    for scenario_key, keywords in [*specific_patterns, *generic_patterns]:
        if scenario_key in _SPECIFIC_SCENARIO_KINDS:
            # Specific domain clusters are intentionally global, so a single broad
            # keyword from preconditions or shared setup text is too weak to group
            # cases. Require multiple intent-text hits or one strong phrase.
            if _specific_scenario_matches(scenario_key, intent_text, keywords):
                return f"global:{scenario_key}"
            continue
        if _keyword_score(text, keywords) > 0:
            return f"{stage}:{scenario_key}:obj:{compact_object}"
    tokens = _tokenize(case_flat_text(case, fields=("test_module", "description", "expected_result")), limit=8)
    token_key = "_".join(token.lower() for token in tokens[:6])
    return f"{stage}:semantic:{token_key or 'unknown'}"


def classify_case_intent_signature(case: dict[str, Any], flow_stage: str | None = None) -> str:
    """Build a coarse, product-agnostic action/object/outcome signature for duplicate detection."""
    stage = str(flow_stage or "unknown")
    action, compact_object, outcome = _case_intent_parts(case)
    return f"{stage}:intent:{action}:{compact_object}:{outcome}"
