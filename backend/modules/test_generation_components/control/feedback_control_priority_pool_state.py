from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from modules.testing.manual_quality_profile import (
    build_manual_quality_profile as _build_manual_quality_profile,
    manual_quality_profile_hints as _manual_quality_profile_hints,
)
from modules.testing.priority_sample_pool_store import (
    ensure_priority_pool_pattern_index as _ensure_priority_pool_pattern_index,
    load_priority_sample_pool as _load_priority_sample_pool,
    retrieve_priority_sample_patterns as _retrieve_priority_sample_patterns,
)

from .feedback_control_config import (
    _MAX_MUST_COVER_RULES,
    _MAX_PREFERRED_PATTERNS,
    _MAX_PRIORITY_POOL_CLUSTER_CAP,
    _MAX_PRIORITY_POOL_FORBIDDEN_PATTERNS,
    _MAX_PRIORITY_POOL_HINTS,
    _MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
    _MAX_PRIORITY_POOL_SAMPLES,
    _MAX_PRIORITY_POOL_SCENARIOS,
    _MAX_PRIORITY_POOL_SOFT_CONSTRAINTS,
    _MAX_WORKFLOW_BLUEPRINTS,
    _MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
    _PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
    _PRIORITY_POOL_MIN_POSITIVE_TOP_K,
    _SYNC_PRIORITY_INDEX_ON_READ,
)
from .feedback_control_pattern_policy import (
    _build_negative_forbidden_patterns,
    _extract_reuse_risks,
)
from .feedback_control_priority_retrieval import (
    _sample_text_for_retrieval,
    _select_priority_pool_samples_by_requirement,
)
from .feedback_control_priority_signals import (
    _count_signal_split,
    _is_manual_verified_negative_sample,
    _is_manual_verified_sample,
)
from .feedback_control_priority_workflows import (
    _priority_pool_sample_identity,
    _select_priority_pool_workflow_blueprint_samples,
    _workflow_blueprint_from_sample,
)
from .feedback_control_sample_access import (
    extract_forbidden_pattern_from_sample as _extract_forbidden_pattern_from_sample,
    extract_rule_ids as _extract_rule_ids,
    normalize_comment_hint as _normalize_comment_hint,
    normalize_expected_priority as _normalize_expected_priority,
    normalize_pattern_category as _normalize_pattern_category,
    normalize_pattern_usage as _normalize_pattern_usage,
    normalize_reason_category as _normalize_reason_category,
    normalize_signal_type as _normalize_signal_type,
    safe_int as _safe_int,
    sample_case_id as _sample_case_id,
    sample_value as _sample_value,
)
from .feedback_control_state import FeedbackControlState
from ..coverage.scenario_registry import classify_registered_scenario_family, infer_primary_domain_tag


LoadPrioritySamplePoolFn = Callable[..., Any]
RetrievePrioritySamplePatternsFn = Callable[..., list[dict[str, Any]]]
EnsurePriorityPoolPatternIndexFn = Callable[..., Any]
BuildManualQualityProfileFn = Callable[..., dict[str, Any]]
ManualQualityProfileHintsFn = Callable[[dict[str, Any]], list[str]]
ManualPriorityHintFn = Callable[[str, str], str]


def _default_manual_priority_hint(label: str, expected_priority: str) -> str:
    return f"{label} expected priority {expected_priority} from manual feedback."


def build_from_priority_sample_pool(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    requirement_text: str = "",
    load_priority_sample_pool_fn: LoadPrioritySamplePoolFn | None = None,
    retrieve_priority_sample_patterns_fn: RetrievePrioritySamplePatternsFn | None = None,
    ensure_priority_pool_pattern_index_fn: EnsurePriorityPoolPatternIndexFn | None = None,
    build_manual_quality_profile_fn: BuildManualQualityProfileFn | None = None,
    manual_quality_profile_hints_fn: ManualQualityProfileHintsFn | None = None,
    reason_to_scenario: dict[str, str] | None = None,
    reason_hints: dict[str, str] | None = None,
    manual_priority_hint_fn: ManualPriorityHintFn | None = None,
    sync_priority_index_on_read: bool = _SYNC_PRIORITY_INDEX_ON_READ,
    max_priority_pool_samples: int = _MAX_PRIORITY_POOL_SAMPLES,
    max_priority_pool_retrieval_top_k: int = _MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
    max_priority_pool_cluster_cap: int = _MAX_PRIORITY_POOL_CLUSTER_CAP,
    min_positive_top_k: int = _PRIORITY_POOL_MIN_POSITIVE_TOP_K,
    max_negative_top_k: int = _PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
    min_pattern_confidence: float = _MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
    max_priority_pool_hints: int = _MAX_PRIORITY_POOL_HINTS,
    max_must_cover_rules: int = _MAX_MUST_COVER_RULES,
    max_priority_pool_scenarios: int = _MAX_PRIORITY_POOL_SCENARIOS,
    max_priority_pool_forbidden_patterns: int = _MAX_PRIORITY_POOL_FORBIDDEN_PATTERNS,
    max_preferred_patterns: int = _MAX_PREFERRED_PATTERNS,
    max_priority_pool_soft_constraints: int = _MAX_PRIORITY_POOL_SOFT_CONSTRAINTS,
    max_workflow_blueprints: int = _MAX_WORKFLOW_BLUEPRINTS,
) -> FeedbackControlState:
    if db is None or not project_id or not user_id:
        return FeedbackControlState.empty()

    loader = load_priority_sample_pool_fn or _load_priority_sample_pool
    retrieve_patterns = retrieve_priority_sample_patterns_fn or _retrieve_priority_sample_patterns
    ensure_pattern_index = ensure_priority_pool_pattern_index_fn or _ensure_priority_pool_pattern_index
    build_manual_profile = build_manual_quality_profile_fn or _build_manual_quality_profile
    manual_profile_hints_fn = manual_quality_profile_hints_fn or _manual_quality_profile_hints
    manual_priority_hint = manual_priority_hint_fn or _default_manual_priority_hint
    reason_to_scenario = dict(reason_to_scenario or {})
    reason_hints = dict(reason_hints or {})

    payload = loader(
        db=db,
        project_id=int(project_id),
        user_id=int(user_id),
    )
    if not isinstance(payload, dict):
        return FeedbackControlState.empty()

    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        return FeedbackControlState.empty()

    samples: list[dict[str, Any]] = []
    for item in raw_samples[:max(1, int(max_priority_pool_samples))]:
        if isinstance(item, dict):
            samples.append(item)
    if not samples:
        return FeedbackControlState.empty()

    manual_quality_profile = build_manual_profile(
        samples,
        project_id=int(project_id),
        user_id=int(user_id),
        existing_profile=payload.get("manual_quality_profile"),
    )
    manual_profile_hints = manual_profile_hints_fn(manual_quality_profile)
    manual_profile_meta: dict[str, Any] = {}
    if manual_quality_profile:
        manual_profile_meta = {
            "manual_quality_profile": manual_quality_profile,
            "manual_quality_profile_version": str(manual_quality_profile.get("profile_version") or ""),
            "manual_quality_profile_trusted_count": int(manual_quality_profile.get("trusted_sample_count") or 0),
            "manual_quality_profile_case_count": int(manual_quality_profile.get("profile_case_count") or 0),
            "manual_quality_profile_high_priority_ratio": float(
                manual_quality_profile.get("high_priority_ratio") or 0.0
            ),
            "manual_quality_profile_display_ratio_cap": float(
                manual_quality_profile.get("display_ratio_cap") or 0.0
            ),
        }

    pool_total_positive_count, pool_total_negative_count = _count_signal_split(samples)
    generation_id = _safe_int(payload.get("generation_id"), default=0) or None
    pattern_index_token = str(payload.get("pattern_index_token") or "").strip()
    if sync_priority_index_on_read:
        try:
            ensure_pattern_index(
                project_id=int(project_id),
                user_id=int(user_id),
                generation_id=generation_id,
                pattern_index_token=pattern_index_token,
                samples=samples,
            )
        except Exception:
            pass

    selected_samples, retrieval_meta = _select_priority_pool_samples_by_requirement(
        samples=samples,
        project_id=int(project_id),
        user_id=int(user_id),
        generation_id=generation_id,
        pattern_index_token=pattern_index_token,
        requirement_text=str(requirement_text or ""),
        retrieve_priority_sample_patterns_fn=retrieve_patterns,
        max_retrieval_top_k=max_priority_pool_retrieval_top_k,
        max_cluster_cap=max_priority_pool_cluster_cap,
        min_positive_top_k=min_positive_top_k,
        max_negative_top_k=max_negative_top_k,
        min_pattern_confidence=min_pattern_confidence,
    )
    direct_workflow_samples = _select_priority_pool_workflow_blueprint_samples(
        samples=samples,
        requirement_text=str(requirement_text or ""),
        max_workflow_blueprints=max_workflow_blueprints,
        min_pattern_confidence=min_pattern_confidence,
    )
    direct_added = 0
    if direct_workflow_samples:
        selected_identities = {_priority_pool_sample_identity(sample) for sample in selected_samples}
        for sample in direct_workflow_samples:
            identity = _priority_pool_sample_identity(sample)
            if identity in selected_identities:
                continue
            selected_samples.append(sample)
            selected_identities.add(identity)
            direct_added += 1
    retrieval_meta["workflow_blueprint_direct_candidate_count"] = int(len(direct_workflow_samples))
    retrieval_meta["workflow_blueprint_direct_selected_count"] = int(direct_added)
    retrieval_meta["retrieval_index_resync_attempted"] = False
    retrieval_meta["retrieval_index_resync_success"] = False
    retrieval_meta["retrieval_index_resync_error"] = ""

    if (
        (
            int(retrieval_meta.get("retrieval_hit_count") or 0) <= 0
            or int(retrieval_meta.get("retrieval_index_mismatch_count") or 0) > 0
        )
        and str(retrieval_meta.get("retrieval_fallback") or "") == "lexical_fallback"
    ):
        retrieval_meta["retrieval_index_resync_attempted"] = True
        try:
            ensure_pattern_index(
                project_id=int(project_id),
                user_id=int(user_id),
                generation_id=generation_id,
                pattern_index_token=pattern_index_token,
                samples=samples,
            )
            retry_selected, retry_meta = _select_priority_pool_samples_by_requirement(
                samples=samples,
                project_id=int(project_id),
                user_id=int(user_id),
                generation_id=generation_id,
                pattern_index_token=pattern_index_token,
                requirement_text=str(requirement_text or ""),
                retrieve_priority_sample_patterns_fn=retrieve_patterns,
                max_retrieval_top_k=max_priority_pool_retrieval_top_k,
                max_cluster_cap=max_priority_pool_cluster_cap,
                min_positive_top_k=min_positive_top_k,
                max_negative_top_k=max_negative_top_k,
                min_pattern_confidence=min_pattern_confidence,
            )
            selected_samples = retry_selected
            retrieval_meta = retry_meta
            retrieval_meta["retrieval_index_resync_attempted"] = True
            retrieval_meta["retrieval_index_resync_success"] = (
                int(retrieval_meta.get("retrieval_hit_count") or 0) > 0
            )
            retrieval_meta["retrieval_index_resync_error"] = ""
        except Exception as resync_err:
            retrieval_meta["retrieval_index_resync_success"] = False
            retrieval_meta["retrieval_index_resync_error"] = str(resync_err)[:240]

    if not selected_samples:
        domain_gate_blocked = bool(
            retrieval_meta.get("retrieval_domain_gate_blocked")
            or retrieval_meta.get("retrieval_domain_no_match")
        )
        if manual_quality_profile and not domain_gate_blocked:
            return FeedbackControlState(
                quality_fix_hints=manual_profile_hints[:max(1, int(max_priority_pool_hints))],
                source_meta={
                    "sources": ["priority_sample_pool_manual_profile"],
                    "priority_pool_sample_count": int(len(samples)),
                    **manual_profile_meta,
                    "generation_id": payload.get("generation_id"),
                    **retrieval_meta,
                },
            )
        if domain_gate_blocked:
            return FeedbackControlState(
                source_meta={
                    "sources": ["priority_sample_pool_domain_gate"],
                    "priority_pool_sample_count": int(len(samples)),
                    "priority_pool_total_positive_count": int(pool_total_positive_count),
                    "priority_pool_total_negative_count": int(pool_total_negative_count),
                    "generation_id": payload.get("generation_id"),
                    **retrieval_meta,
                },
            )
        return FeedbackControlState.empty()

    reason_counter: Counter[str] = Counter()
    pattern_category_counter: Counter[str] = Counter()
    expected_counter: Counter[str] = Counter()
    rule_counter: Counter[str] = Counter()
    rule_expected_high: set[str] = set()
    scenario_counter: Counter[str] = Counter()
    pattern_counter: Counter[str] = Counter()
    pattern_scope_counter: Counter[str] = Counter()
    pattern_grain_counter: Counter[str] = Counter()
    forbidden_patterns: list[str] = []
    forbidden_pattern_seen: set[str] = set()
    preferred_patterns: list[str] = []
    reuse_risks: list[str] = []
    reuse_risk_seen: set[str] = set()
    soft_constraints: list[str] = []
    quality_hints: list[str] = list(manual_profile_hints)
    workflow_blueprints: list[dict[str, Any]] = []
    positive_scenario_counter: Counter[str] = Counter()
    redundant_scenario_cap_counter: Counter[str] = Counter()
    verified_count = 0
    manual_comment_count = 0
    positive_selected_count = 0
    negative_selected_count = 0
    ui_low_value_negative_count = 0

    for sample in selected_samples:
        reason = _normalize_reason_category(_sample_value(sample, "reason_category", "reasonCategory"))
        pattern_category = _normalize_pattern_category(
            _sample_value(sample, "pattern_category", "patternCategory")
        )
        expected_priority = _normalize_expected_priority(
            _sample_value(sample, "expected_priority", "expectedPriority")
        )
        comment = str(_sample_value(sample, "user_comment", "userComment") or "").strip()
        signal_type = _normalize_signal_type(
            _sample_value(
                sample,
                "signal_type",
                "signalType",
                "pattern_signal_type",
                "patternSignalType",
                "feedback_direction",
                "feedbackDirection",
                "sample_type",
                "sampleType",
                "sample_kind",
                "sampleKind",
            )
        )
        pattern_usage = _normalize_pattern_usage(
            _sample_value(sample, "pattern_usage", "patternUsage"),
            signal_type=signal_type,
        )
        is_positive_signal = bool(signal_type == "positive" or pattern_usage == "prefer")

        if is_positive_signal:
            is_verified = _is_manual_verified_sample(
                reason=reason,
                pattern_category=pattern_category,
                expected_priority=expected_priority,
                comment=comment,
            )
        else:
            is_verified = _is_manual_verified_negative_sample(
                sample=sample,
                reason=reason,
                expected_priority=expected_priority,
                comment=comment,
            )
        if not is_verified:
            continue

        verified_count += 1
        if reason:
            reason_counter[reason] += 1
        if pattern_category:
            pattern_category_counter[pattern_category] += 1
        if expected_priority:
            expected_counter[expected_priority] += 1

        pattern_scope = str(_sample_value(sample, "pattern_scope", "patternScope") or "unknown").strip() or "unknown"
        pattern_grain = str(_sample_value(sample, "pattern_grain", "patternGrain") or "case").strip() or "case"
        pattern_scope_counter[pattern_scope[:40]] += 1
        pattern_grain_counter[pattern_grain[:40]] += 1

        scenario_label = reason_to_scenario.get(reason)
        if scenario_label:
            scenario_counter[scenario_label] += 1

        reason_hint = reason_hints.get(reason)
        if reason_hint:
            quality_hints.append(reason_hint)

        case_id = _sample_case_id(sample)
        title = str(_sample_value(sample, "title") or "").strip()
        sample_domain_text = "\n".join([_sample_text_for_retrieval(sample), title, comment])
        sample_primary_domain = infer_primary_domain_tag(sample_domain_text)
        pattern_key = str(
            _sample_value(sample, "pattern_canonical", "patternCanonical")
            or _sample_value(sample, "pattern_summary", "patternSummary")
            or title
            or case_id
        ).strip()
        if pattern_key:
            pattern_counter[pattern_key[:120]] += 1

        if is_positive_signal:
            positive_selected_count += 1
            workflow_blueprint = _workflow_blueprint_from_sample(sample)
            if workflow_blueprint is not None:
                workflow_blueprints.append(workflow_blueprint)
            preferred_pattern = str(
                _sample_value(sample, "pattern_summary", "patternSummary")
                or _sample_value(sample, "pattern_canonical", "patternCanonical")
                or title
            ).strip()
            if preferred_pattern:
                preferred_patterns.append(preferred_pattern)
                quality_hints.append(f"Prefer reusable pattern: {preferred_pattern[:120]}")
            scenario_family = classify_registered_scenario_family(
                "\n".join(
                    [
                        _sample_text_for_retrieval(sample),
                        preferred_pattern,
                        title,
                        comment,
                    ]
                ),
                primary_domain=sample_primary_domain,
                include_domain_specific=not bool(sample_primary_domain),
            )
            if scenario_family:
                positive_scenario_counter[scenario_family] += 1
                scenario_counter[scenario_family] += 1
        else:
            negative_selected_count += 1
            if reason != "redundant_case":
                forbidden_candidates, ui_low_value = _build_negative_forbidden_patterns(
                    sample=sample,
                    title=title,
                    comment=comment,
                    reason=reason,
                    sample_value_fn=_sample_value,
                    extract_forbidden_pattern_from_sample_fn=_extract_forbidden_pattern_from_sample,
                )
                if ui_low_value:
                    ui_low_value_negative_count += 1
                for forbidden_pattern in forbidden_candidates:
                    candidate = str(forbidden_pattern or "").strip()
                    if not candidate:
                        continue
                    normalized = candidate.lower()
                    if normalized in forbidden_pattern_seen:
                        continue
                    forbidden_pattern_seen.add(normalized)
                    forbidden_patterns.append(candidate[:120])

        priority_debug = _sample_value(sample, "priority_debug", "priorityDebug")
        sample_text = " ".join([case_id, title, comment, str(priority_debug or "")])
        for reuse_risk in _extract_reuse_risks(
            title,
            comment,
            _sample_value(sample, "pattern_summary", "patternSummary"),
            _sample_value(sample, "pattern_canonical", "patternCanonical"),
        ):
            normalized_risk = str(reuse_risk or "").strip().lower()
            if not normalized_risk or normalized_risk in reuse_risk_seen:
                continue
            reuse_risk_seen.add(normalized_risk)
            reuse_risks.append(reuse_risk)

        sample_rules = _extract_rule_ids(sample_text)
        for rule_id in sample_rules:
            rule_counter[rule_id] += 1
            if expected_priority in {"P0", "P1"}:
                rule_expected_high.add(rule_id)

        if reason == "redundant_case" and not (signal_type == "positive" or pattern_usage == "prefer"):
            pattern = str(_sample_value(sample, "pattern_summary", "patternSummary") or "").strip()
            pattern = pattern or _extract_forbidden_pattern_from_sample(title=title, comment=comment)
            if pattern:
                soft_constraints.append(pattern)
            scenario_family = classify_registered_scenario_family(
                "\n".join(
                    [
                        _sample_text_for_retrieval(sample),
                        pattern,
                        title,
                        comment,
                    ]
                ),
                primary_domain=sample_primary_domain,
                include_domain_specific=not bool(sample_primary_domain),
            )
            if scenario_family:
                redundant_scenario_cap_counter[scenario_family] += 1

        comment_hint = _normalize_comment_hint(comment)
        if comment_hint:
            manual_comment_count += 1
            quality_hints.append(comment_hint)

        if expected_priority in {"P0", "P1"}:
            label = case_id or title or "manual_case"
            quality_hints.append(manual_priority_hint(label, expected_priority))

    if verified_count <= 0:
        return FeedbackControlState.empty()

    must_cover_rules = [rule for rule, _ in rule_counter.most_common(max(1, int(max_must_cover_rules)))]
    must_have_scenarios = [
        scenario for scenario, _ in scenario_counter.most_common(max(1, int(max_priority_pool_scenarios)))
    ]
    rule_quota: dict[str, int] = {}
    for rule_id in must_cover_rules:
        base = 2 if rule_id in rule_expected_high else 1
        if int(rule_counter.get(rule_id, 0)) >= 2:
            base = max(base, 2)
        rule_quota[rule_id] = int(base)

    return FeedbackControlState(
        must_cover_rules=must_cover_rules,
        must_have_scenarios=must_have_scenarios,
        forbidden_patterns=forbidden_patterns[:max(1, int(max_priority_pool_forbidden_patterns))],
        preferred_patterns=preferred_patterns[:max(1, int(max_preferred_patterns))],
        reuse_risks=reuse_risks[:max(1, int(max_preferred_patterns))],
        soft_constraints=soft_constraints[:max(1, int(max_priority_pool_soft_constraints))],
        rule_quota=rule_quota,
        quality_fix_hints=quality_hints[:max(1, int(max_priority_pool_hints))],
        workflow_blueprints=workflow_blueprints[:max(1, int(max_workflow_blueprints))],
        source_meta={
            "sources": [
                "priority_sample_pool_manual_verified",
                *(["priority_sample_pool_manual_profile"] if manual_quality_profile else []),
            ],
            "priority_pool_sample_count": int(len(samples)),
            "priority_pool_total_positive_count": int(pool_total_positive_count),
            "priority_pool_total_negative_count": int(pool_total_negative_count),
            "priority_pool_selected_sample_count": int(len(selected_samples)),
            "verified_sample_count": int(verified_count),
            "manual_comment_count": int(manual_comment_count),
            "preferred_pattern_count": int(len(preferred_patterns)),
            "workflow_blueprint_count": int(len(workflow_blueprints)),
            "reuse_risk_count": int(len(reuse_risks)),
            "positive_selected_count": int(positive_selected_count),
            "negative_selected_count": int(negative_selected_count),
            "ui_low_value_negative_count": int(ui_low_value_negative_count),
            "reason_category_distribution": dict(reason_counter),
            "pattern_category_distribution": dict(pattern_category_counter),
            "pattern_scope_distribution": dict(pattern_scope_counter),
            "pattern_grain_distribution": dict(pattern_grain_counter),
            "expected_priority_distribution": dict(expected_counter),
            "pattern_hit_distribution": {
                key: int(value)
                for key, value in pattern_counter.most_common(12)
            },
            "priority_pool_redundant_scenario_caps": {
                key: 1 for key, _ in redundant_scenario_cap_counter.most_common(12)
            },
            "priority_pool_redundant_scenario_cap_count": int(len(redundant_scenario_cap_counter)),
            "priority_pool_positive_scenario_families": {
                key: int(value)
                for key, value in positive_scenario_counter.most_common(12)
            },
            "priority_pool_positive_scenario_family_count": int(len(positive_scenario_counter)),
            "pattern_hit_total": int(sum(pattern_counter.values())),
            "generation_id": payload.get("generation_id"),
            **manual_profile_meta,
            **retrieval_meta,
        },
    )
