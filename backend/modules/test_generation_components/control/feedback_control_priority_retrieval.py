from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from modules.testing.priority_sample_pool_store import (
    retrieve_priority_sample_patterns as _retrieve_priority_sample_patterns,
)
from .feedback_control_config import (
    _ASCII_TOKEN_PATTERN,
    _CJK_CHAR_PATTERN,
    _MAX_PRIORITY_POOL_CLUSTER_CAP,
    _MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
    _MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
    _PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
    _PRIORITY_POOL_MIN_POSITIVE_TOP_K,
)
from .feedback_control_priority_signals import (
    _count_signal_split,
    _is_pattern_active,
    _pattern_confidence,
)
from .feedback_control_priority_quota import _apply_signal_quota as _apply_signal_quota_impl
from .feedback_control_priority_retrieval_text import (
    _sample_matches_primary_domain,
    _sample_text_for_retrieval,
)
from .feedback_control_priority_retrieval_meta import build_priority_retrieval_meta
from .feedback_control_sample_access import (
    sample_case_id as _sample_case_id,
    sample_value as _sample_value,
)
from ..coverage.scenario_registry import infer_domain_tags, infer_primary_domain_tag


RetrievePrioritySamplePatternsFn = Callable[..., list[dict[str, Any]]]


def _apply_signal_quota(
    candidates: list[dict[str, Any]],
    *,
    retrieval_meta: dict[str, Any],
    max_retrieval_top_k: int = _MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
    min_positive_top_k: int = _PRIORITY_POOL_MIN_POSITIVE_TOP_K,
    max_negative_top_k: int = _PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
) -> list[dict[str, Any]]:
    return _apply_signal_quota_impl(
        candidates,
        retrieval_meta=retrieval_meta,
        max_retrieval_top_k=max_retrieval_top_k,
        min_positive_top_k=min_positive_top_k,
        max_negative_top_k=max_negative_top_k,
    )


def _select_priority_pool_samples_by_requirement(
    *,
    samples: list[dict[str, Any]],
    project_id: int,
    user_id: int,
    generation_id: int | None = None,
    pattern_index_token: str = "",
    requirement_text: str,
    retrieve_priority_sample_patterns_fn: RetrievePrioritySamplePatternsFn | None = None,
    max_retrieval_top_k: int = _MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
    max_cluster_cap: int = _MAX_PRIORITY_POOL_CLUSTER_CAP,
    min_positive_top_k: int = _PRIORITY_POOL_MIN_POSITIVE_TOP_K,
    max_negative_top_k: int = _PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
    min_pattern_confidence: float = _MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    max_retrieval_top_k = max(1, int(max_retrieval_top_k))
    max_cluster_cap = max(1, min(int(max_cluster_cap), int(max_retrieval_top_k)))
    min_positive_top_k = max(0, min(int(min_positive_top_k), int(max_retrieval_top_k)))
    max_negative_top_k = max(0, min(int(max_negative_top_k), int(max_retrieval_top_k)))
    min_pattern_confidence = max(0.0, min(1.0, float(min_pattern_confidence)))
    retriever = retrieve_priority_sample_patterns_fn or _retrieve_priority_sample_patterns

    retrieval_meta = build_priority_retrieval_meta(
        requirement_text=str(requirement_text or ""),
        max_retrieval_top_k=max_retrieval_top_k,
        max_cluster_cap=max_cluster_cap,
        min_positive_top_k=min_positive_top_k,
        max_negative_top_k=max_negative_top_k,
        min_pattern_confidence=min_pattern_confidence,
    )
    if not samples:
        return [], retrieval_meta
    active_status_samples = [item for item in samples if _is_pattern_active(item)]
    active_samples = [
        item
        for item in active_status_samples
        if _pattern_confidence(item) >= float(min_pattern_confidence)
    ]
    retrieval_meta["retrieval_active_sample_count"] = int(len(active_samples))
    retrieval_meta["retrieval_disabled_sample_count"] = int(len(samples) - len(active_status_samples))
    retrieval_meta["retrieval_low_confidence_sample_count"] = int(len(active_status_samples) - len(active_samples))
    pool_positive, pool_negative = _count_signal_split(active_samples)
    retrieval_meta["retrieval_pool_positive_count"] = int(pool_positive)
    retrieval_meta["retrieval_pool_negative_count"] = int(pool_negative)
    if not active_samples:
        retrieval_meta["retrieval_fallback"] = "no_active_patterns"
        return [], retrieval_meta

    query = str(requirement_text or "").strip()
    query_domains = infer_domain_tags(query)
    primary_query_domain = infer_primary_domain_tag(query)
    retrieval_meta["retrieval_query_domain_tags"] = sorted(query_domains)
    retrieval_meta["retrieval_query_primary_domain"] = primary_query_domain
    allowed_object_ids: set[int] = {id(item) for item in active_samples}
    if primary_query_domain:
        domain_matched_samples = [
            item
            for item in active_samples
            if _sample_matches_primary_domain(item, primary_query_domain)
        ]
        retrieval_meta["retrieval_domain_matched_sample_count"] = int(len(domain_matched_samples))
        retrieval_meta["retrieval_domain_skipped_sample_count"] = int(
            len(active_samples) - len(domain_matched_samples)
        )
        retrieval_meta["retrieval_domain_filter_applied"] = True
        if not domain_matched_samples:
            retrieval_meta["retrieval_domain_no_match"] = True
            retrieval_meta["retrieval_fallback"] = "domain_no_match"
            return [], retrieval_meta
        active_samples = domain_matched_samples
        allowed_object_ids = {id(item) for item in active_samples}

    sample_by_id: dict[str, dict[str, Any]] = {}
    for item in active_samples:
        sample_id = str(_sample_value(item, "sample_id", "sampleId") or "").strip()
        if sample_id:
            sample_by_id[sample_id] = item

    def _cluster_key(sample_like: dict[str, Any]) -> str:
        return str(
            _sample_value(sample_like, "pattern_cluster_key", "patternClusterKey")
            or _sample_value(sample_like, "pattern_canonical", "patternCanonical")
            or _sample_value(sample_like, "pattern_summary", "patternSummary")
            or _sample_value(sample_like, "title")
            or _sample_case_id(sample_like)
            or ""
        ).strip().lower()[:120]

    def _apply_diversity_cap(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cluster_counter: Counter[str] = Counter()
        selected_local: list[dict[str, Any]] = []
        skipped = 0
        for item in candidates:
            key = _cluster_key(item) or "misc"
            if int(cluster_counter.get(key, 0)) >= int(max_cluster_cap):
                skipped += 1
                continue
            cluster_counter[key] += 1
            selected_local.append(item)
            if len(selected_local) >= max_retrieval_top_k:
                break
        retrieval_meta["retrieval_diversity_skipped_count"] = int(
            retrieval_meta.get("retrieval_diversity_skipped_count") or 0
        ) + int(skipped)
        return selected_local

    def _ascii_tokens(text: str) -> set[str]:
        return {token.lower() for token in _ASCII_TOKEN_PATTERN.findall(str(text or "")) if token}

    def _cjk_chars(text: str) -> set[str]:
        return {char for char in _CJK_CHAR_PATTERN.findall(str(text or "")) if char.strip()}

    query_ascii = _ascii_tokens(query)
    query_cjk = _cjk_chars(query)

    def _lexical_score(sample_like: dict[str, Any]) -> float:
        text = _sample_text_for_retrieval(sample_like)
        if not text:
            return 0.0
        sample_ascii = _ascii_tokens(text)
        sample_cjk = _cjk_chars(text)
        ascii_overlap = len(query_ascii & sample_ascii) if query_ascii else 0
        cjk_overlap = len(query_cjk & sample_cjk) if query_cjk else 0
        sample_primary_domain = infer_primary_domain_tag(text)
        domain_overlap = 1 if primary_query_domain and sample_primary_domain == primary_query_domain else 0
        try:
            weight = float(_sample_value(sample_like, "pattern_weight") or 0.0)
        except Exception:
            weight = 0.0
        return float(
            domain_overlap * 12.0
            + ascii_overlap * 2.0
            + cjk_overlap * 0.6
            + min(max(weight, 0.0), 2.0) * 0.2
        )

    if not query:
        candidates = sorted(
            active_samples,
            key=lambda item: (
                float(_sample_value(item, "pattern_weight") or 0.0),
                float(_sample_value(item, "pattern_quality_score") or 0.0),
            ),
            reverse=True,
        )
        raw_positive, raw_negative = _count_signal_split(candidates)
        retrieval_meta["retrieval_raw_positive_count"] = int(raw_positive)
        retrieval_meta["retrieval_raw_negative_count"] = int(raw_negative)
        selected_diversity = _apply_diversity_cap(candidates)
        diversity_positive, diversity_negative = _count_signal_split(selected_diversity)
        retrieval_meta["retrieval_after_diversity_positive_count"] = int(diversity_positive)
        retrieval_meta["retrieval_after_diversity_negative_count"] = int(diversity_negative)
        selected = _apply_signal_quota(
            selected_diversity,
            retrieval_meta=retrieval_meta,
            max_retrieval_top_k=max_retrieval_top_k,
            min_positive_top_k=min_positive_top_k,
            max_negative_top_k=max_negative_top_k,
        )
        retrieval_meta["retrieval_selected_count"] = int(len(selected))
        retrieval_meta["retrieval_fallback"] = "top_weight_no_query"
        if selected:
            retrieval_meta["retrieval_selected_weight_avg"] = round(
                sum(float(_sample_value(item, "pattern_weight") or 0.0) for item in selected) / len(selected),
                4,
            )
            retrieval_meta["retrieval_selected_quality_avg"] = round(
                sum(float(_sample_value(item, "pattern_quality_score") or 0.0) for item in selected) / len(selected),
                4,
            )
        return selected, retrieval_meta

    try:
        retrieved = retriever(
            project_id=int(project_id),
            user_id=int(user_id),
            query_text=query,
            generation_id=(int(generation_id) if generation_id is not None else None),
            pattern_index_token=str(pattern_index_token or ""),
            top_k=max_retrieval_top_k,
        )
    except Exception:
        retrieved = []

    retrieval_meta["retrieval_hit_count"] = int(len(retrieved))
    sample_ref_seen: set[int] = set()
    pattern_seen: set[str] = set()
    selected_raw: list[dict[str, Any]] = []
    for item in retrieved:
        canonical = str((item or {}).get("pattern_canonical") or "").strip().lower()
        retrieved_cluster = str((item or {}).get("pattern_cluster_key") or "").strip().lower()
        sample_id = str((item or {}).get("sample_id") or "").strip()
        picked: dict[str, Any] | None = None
        if sample_id:
            picked = sample_by_id.get(sample_id)
            if picked is None:
                retrieval_meta["retrieval_index_mismatch_count"] = int(
                    retrieval_meta.get("retrieval_index_mismatch_count") or 0
                ) + 1
                continue
            retrieval_meta["retrieval_sample_id_hit_count"] = int(
                retrieval_meta.get("retrieval_sample_id_hit_count") or 0
            ) + 1
        else:
            try:
                sample_index = int((item or {}).get("sample_index"))
            except Exception:
                retrieval_meta["retrieval_index_mismatch_count"] = int(
                    retrieval_meta.get("retrieval_index_mismatch_count") or 0
                ) + 1
                continue
            if sample_index < 0 or sample_index >= len(samples):
                retrieval_meta["retrieval_index_mismatch_count"] = int(
                    retrieval_meta.get("retrieval_index_mismatch_count") or 0
                ) + 1
                continue
            picked = samples[sample_index]

        if id(picked) in sample_ref_seen:
            continue
        if canonical and canonical in pattern_seen:
            continue
        if not _is_pattern_active(picked):
            continue
        if id(picked) not in allowed_object_ids:
            continue
        if not sample_id:
            picked_canonical = str(
                _sample_value(picked, "pattern_canonical", "patternCanonical") or ""
            ).strip().lower()
            picked_cluster = str(
                _sample_value(picked, "pattern_cluster_key", "patternClusterKey") or ""
            ).strip().lower()
            canonical_mismatch = bool(canonical and picked_canonical and canonical != picked_canonical)
            cluster_mismatch = bool(retrieved_cluster and picked_cluster and retrieved_cluster != picked_cluster)
            if canonical_mismatch and cluster_mismatch:
                retrieval_meta["retrieval_index_mismatch_count"] = int(
                    retrieval_meta.get("retrieval_index_mismatch_count") or 0
                ) + 1
                continue
        if _pattern_confidence(picked) < float(min_pattern_confidence):
            continue
        sample_ref_seen.add(id(picked))
        if canonical:
            pattern_seen.add(canonical)
        retrieved_weight = float((item or {}).get("pattern_weight") or 0.0)
        retrieved_quality = float((item or {}).get("pattern_quality_score") or 0.0)
        if retrieved_weight > 0:
            picked = dict(picked)
            picked["pattern_weight"] = round(retrieved_weight, 4)
            picked["pattern_quality_score"] = round(max(0.0, min(1.0, retrieved_quality)), 4)
        selected_raw.append(picked)
        if len(selected_raw) >= (max_retrieval_top_k * 3):
            break
    raw_positive, raw_negative = _count_signal_split(selected_raw)
    retrieval_meta["retrieval_raw_positive_count"] = int(raw_positive)
    retrieval_meta["retrieval_raw_negative_count"] = int(raw_negative)
    selected_diversity = _apply_diversity_cap(selected_raw)
    diversity_positive, diversity_negative = _count_signal_split(selected_diversity)
    retrieval_meta["retrieval_after_diversity_positive_count"] = int(diversity_positive)
    retrieval_meta["retrieval_after_diversity_negative_count"] = int(diversity_negative)
    selected = _apply_signal_quota(
            selected_diversity,
            retrieval_meta=retrieval_meta,
            max_retrieval_top_k=max_retrieval_top_k,
            min_positive_top_k=min_positive_top_k,
            max_negative_top_k=max_negative_top_k,
        )

    if selected:
        retrieval_meta["retrieval_selected_count"] = int(len(selected))
        retrieval_meta["retrieval_selected_weight_avg"] = round(
            sum(float(_sample_value(item, "pattern_weight") or 0.0) for item in selected) / len(selected),
            4,
        )
        retrieval_meta["retrieval_selected_quality_avg"] = round(
            sum(float(_sample_value(item, "pattern_quality_score") or 0.0) for item in selected) / len(selected),
            4,
        )
        return selected, retrieval_meta

    lexical_sorted = sorted(
        active_samples,
        key=lambda item: (
            _lexical_score(item),
            float(_sample_value(item, "pattern_weight") or 0.0),
            float(_sample_value(item, "pattern_quality_score") or 0.0),
        ),
        reverse=True,
    )
    raw_positive, raw_negative = _count_signal_split(lexical_sorted)
    retrieval_meta["retrieval_raw_positive_count"] = int(raw_positive)
    retrieval_meta["retrieval_raw_negative_count"] = int(raw_negative)
    lexical_selected_diversity = _apply_diversity_cap(lexical_sorted)
    diversity_positive, diversity_negative = _count_signal_split(lexical_selected_diversity)
    retrieval_meta["retrieval_after_diversity_positive_count"] = int(diversity_positive)
    retrieval_meta["retrieval_after_diversity_negative_count"] = int(diversity_negative)
    lexical_selected = _apply_signal_quota(
        lexical_selected_diversity,
        retrieval_meta=retrieval_meta,
        max_retrieval_top_k=max_retrieval_top_k,
        min_positive_top_k=min_positive_top_k,
        max_negative_top_k=max_negative_top_k,
    )
    if lexical_selected:
        retrieval_meta["retrieval_fallback"] = "lexical_fallback"
        retrieval_meta["retrieval_lexical_fallback_used"] = True
        retrieval_meta["retrieval_selected_count"] = int(len(lexical_selected))
        retrieval_meta["retrieval_selected_weight_avg"] = round(
            sum(float(_sample_value(item, "pattern_weight") or 0.0) for item in lexical_selected) / len(lexical_selected),
            4,
        )
        retrieval_meta["retrieval_selected_quality_avg"] = round(
            sum(float(_sample_value(item, "pattern_quality_score") or 0.0) for item in lexical_selected) / len(lexical_selected),
            4,
        )
        return lexical_selected, retrieval_meta

    retrieval_meta["retrieval_fallback"] = "head_top_k"
    fallback = sorted(
        active_samples,
        key=lambda item: (
            float(_sample_value(item, "pattern_weight") or 0.0),
            float(_sample_value(item, "pattern_quality_score") or 0.0),
        ),
        reverse=True,
    )
    raw_positive, raw_negative = _count_signal_split(fallback)
    retrieval_meta["retrieval_raw_positive_count"] = int(raw_positive)
    retrieval_meta["retrieval_raw_negative_count"] = int(raw_negative)
    fallback_diversity = _apply_diversity_cap(fallback)
    diversity_positive, diversity_negative = _count_signal_split(fallback_diversity)
    retrieval_meta["retrieval_after_diversity_positive_count"] = int(diversity_positive)
    retrieval_meta["retrieval_after_diversity_negative_count"] = int(diversity_negative)
    fallback = _apply_signal_quota(
        fallback_diversity,
        retrieval_meta=retrieval_meta,
        max_retrieval_top_k=max_retrieval_top_k,
        min_positive_top_k=min_positive_top_k,
        max_negative_top_k=max_negative_top_k,
    )
    retrieval_meta["retrieval_selected_count"] = int(len(fallback))
    if fallback:
        retrieval_meta["retrieval_selected_weight_avg"] = round(
            sum(float(_sample_value(item, "pattern_weight") or 0.0) for item in fallback) / len(fallback),
            4,
        )
        retrieval_meta["retrieval_selected_quality_avg"] = round(
            sum(float(_sample_value(item, "pattern_quality_score") or 0.0) for item in fallback) / len(fallback),
            4,
        )
    return fallback, retrieval_meta
