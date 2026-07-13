from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..coverage.coverage_case_complexity import case_complexity_profile
from .case_access import case_priority
from .result_postprocess_priority_semantics import score_case_priority
from .streaming_case_keys import (
    case_focus_score,
    case_priority_score,
    case_signature,
)
from .streaming_rule_keys import extract_rule_keys

SignatureFn = Callable[[dict[str, Any]], str]
ScorePriorityFn = Callable[..., dict[str, Any]]
MustKeepReasonsFn = Callable[..., list[str]]


@dataclass(frozen=True)
class ReviewCandidatePoolSplit:
    must_keep_cases: list[dict[str, Any]]
    llm_pool_cases: list[dict[str, Any]]
    must_keep_signatures: set[str]
    must_keep_reason_map: dict[str, list[str]]


def is_high_signal(case: dict[str, Any], score_profile: dict[str, Any] | None = None) -> bool:
    profile = score_profile if isinstance(score_profile, dict) else {}
    focus_score = int(case_focus_score(case))
    missing_rule_hits = [str(x) for x in (profile.get("missing_rule_hits") or []) if str(x).strip()]
    core_rule_hits = [str(x) for x in (profile.get("core_rule_hits") or []) if str(x).strip()]
    unique_coverage_hits = [str(x) for x in (profile.get("unique_coverage_hits") or []) if str(x).strip()]
    rule_risk_reasons = [
        str(x).strip().lower()
        for x in (profile.get("rule_risk_reasons") or [])
        if str(x).strip()
    ]
    has_coverage_value = bool(missing_rule_hits or core_rule_hits or unique_coverage_hits)
    has_high_risk_signal = "high" in rule_risk_reasons
    coverage_gain_score = int(profile.get("coverage_gain_score") or 0)
    reuse_risk_hit = bool(profile.get("reuse_risk_hit"))
    return bool(
        has_coverage_value
        or has_high_risk_signal
        or reuse_risk_hit
        or focus_score >= 2
        or coverage_gain_score >= 8
    )


def rank_review_case_for_fill(
    case: dict[str, Any],
    *,
    coverage_context: dict[str, Any] | None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
) -> tuple[int, int, int, int, int, int, int]:
    profile = score_case_priority(
        case,
        coverage_context=coverage_context,
        rule_diagnostics=rule_diagnostics,
    )
    has_coverage_value = bool(
        (profile.get("missing_rule_hits") or [])
        or (profile.get("core_rule_hits") or [])
        or (profile.get("unique_coverage_hits") or [])
    )
    reuse_risk_hit = bool(profile.get("reuse_risk_hit"))
    high_signal = bool(is_high_signal(case, profile))
    focus_score = int(case_focus_score(case))
    coverage_gain_score = int(profile.get("coverage_gain_score") or 0)
    priority_score = int(case_priority_score(case))
    complexity_score = int(case_complexity_profile(case).get("complexity_score") or 0)
    return (
        int(has_coverage_value),
        int(reuse_risk_hit),
        int(high_signal),
        int(focus_score),
        int(max(0, coverage_gain_score)),
        -min(complexity_score, 8),
        int(priority_score),
    )


def hit_must_cover_rule(
    rule_keys: list[str],
    score_profile: dict[str, Any] | None = None,
    *,
    must_cover_rule_set: set[str] | None = None,
) -> bool:
    if not must_cover_rule_set:
        return False
    profile = dict(score_profile or {})
    case_rules = set()
    for key in rule_keys or []:
        normalized = str(key or "").strip().upper()
        if normalized:
            case_rules.add(normalized)
    for field in ("covered_rule_ids", "missing_rule_hits", "core_rule_hits", "unique_coverage_hits"):
        for item in profile.get(field) or []:
            normalized = str(item or "").strip().upper()
            if normalized:
                case_rules.add(normalized)
    return bool(case_rules.intersection(must_cover_rule_set))


def review_must_keep_reasons(
    case: dict[str, Any],
    score_profile: dict[str, Any] | None = None,
    *,
    must_cover_rule_set: set[str] | None = None,
) -> list[str]:
    profile = dict(score_profile or {})
    reasons: list[str] = []

    priority = case_priority(case)
    if priority == "P0":
        reasons.append("priority_p0")

    if bool(profile.get("reuse_risk_hit")):
        reasons.append("reuse_risk_hit")

    if hit_must_cover_rule(
        extract_rule_keys(case),
        profile,
        must_cover_rule_set=must_cover_rule_set,
    ):
        reasons.append("must_cover_rule_hit")

    confirmed_fact_hits: list[str] = []
    raw_hits = case.get("confirmed_fact_hits")
    if isinstance(raw_hits, list):
        confirmed_fact_hits.extend([str(item) for item in raw_hits if str(item).strip()])
    meta = case.get("meta")
    if isinstance(meta, dict):
        meta_hits = meta.get("confirmed_fact_hits")
        if isinstance(meta_hits, list):
            confirmed_fact_hits.extend([str(item) for item in meta_hits if str(item).strip()])
    if confirmed_fact_hits:
        reasons.append("confirmed_fact_hit")

    unique_reasons: list[str] = []
    seen_reasons: set[str] = set()
    for reason in reasons:
        normalized = str(reason or "").strip()
        if not normalized or normalized in seen_reasons:
            continue
        seen_reasons.add(normalized)
        unique_reasons.append(normalized)
    return unique_reasons


def merge_review_selection_candidates(
    must_keep_cases: Iterable[Any] | None,
    selected_cases: Iterable[Any] | None,
    *,
    signature_fn: SignatureFn = case_signature,
) -> list[dict[str, Any]]:
    merged_cases: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()

    for source_cases in (must_keep_cases or [], selected_cases or []):
        for case in source_cases:
            if not isinstance(case, dict):
                continue
            signature = signature_fn(case)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            merged_cases.append(case)

    return merged_cases


def split_review_candidate_pool(
    candidate_cases: Iterable[Any] | None,
    *,
    coverage_context: dict[str, Any] | None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
    must_cover_rule_set: set[str] | None,
    score_case_priority_fn: ScorePriorityFn = score_case_priority,
    must_keep_reasons_fn: MustKeepReasonsFn = review_must_keep_reasons,
    signature_fn: SignatureFn = case_signature,
) -> ReviewCandidatePoolSplit:
    must_keep_cases: list[dict[str, Any]] = []
    llm_pool_cases: list[dict[str, Any]] = []
    must_keep_seen_signatures: set[str] = set()
    must_keep_signatures: set[str] = set()
    must_keep_reason_map: dict[str, list[str]] = {}

    for case in candidate_cases or []:
        if not isinstance(case, dict):
            continue
        score_profile = score_case_priority_fn(
            case,
            coverage_context=coverage_context,
            rule_diagnostics=rule_diagnostics,
        )
        must_keep_reasons = must_keep_reasons_fn(
            case,
            score_profile,
            must_cover_rule_set=must_cover_rule_set,
        )
        signature = signature_fn(case)
        if must_keep_reasons:
            if signature not in must_keep_seen_signatures:
                must_keep_cases.append(case)
                must_keep_seen_signatures.add(signature)
            must_keep_signatures.add(signature)
            must_keep_reason_map[signature] = list(must_keep_reasons)
        else:
            llm_pool_cases.append(case)

    return ReviewCandidatePoolSplit(
        must_keep_cases=must_keep_cases,
        llm_pool_cases=llm_pool_cases,
        must_keep_signatures=must_keep_signatures,
        must_keep_reason_map=must_keep_reason_map,
    )


__all__ = [
    "ReviewCandidatePoolSplit",
    "hit_must_cover_rule",
    "is_high_signal",
    "merge_review_selection_candidates",
    "rank_review_case_for_fill",
    "review_must_keep_reasons",
    "split_review_candidate_pool",
]
