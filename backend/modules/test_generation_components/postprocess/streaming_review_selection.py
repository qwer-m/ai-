from __future__ import annotations

from typing import Any, Callable, Iterable

from .case_access import case_flat_text, case_priority
from .result_postprocess_priority_semantics import score_case_priority
from .streaming_case_keys import (
    case_coverage_bucket,
    case_focus_score,
    case_signature,
    review_case_id,
)
from .streaming_postprocess_utils import _dict_case_items
from .streaming_review_candidate_pool import (
    ReviewCandidatePoolSplit,
    hit_must_cover_rule,
    is_high_signal,
    merge_review_selection_candidates,
    rank_review_case_for_fill,
    review_must_keep_reasons,
    split_review_candidate_pool,
)
from .streaming_review_mapping import normalize_review_llm_reason
from .streaming_review_constraints import (
    build_review_selection_constraints,
    enforce_review_selection_constraints,
)
from .streaming_review_selection_summary import (
    build_review_decision_summary_payload,
    review_llm_drop_summary_fields,
    summarize_review_decision_counts,
    summarize_review_drop_reason_counts,
    summarize_review_drop_stage_counts,
    summarize_review_llm_drop_diagnostics,
    summarize_review_must_keep_and_signal_counts,
)
from .streaming_rule_keys import extract_rule_keys
from .streaming_semantic_text import jaccard_similarity, semantic_signature, semantic_tokenize

ReviewRankFn = Callable[..., tuple[int, ...]]
CoverageAnalyzeFn = Callable[[str, list[dict[str, Any]]], dict[str, Any]]
RuleDiagnosticsFn = Callable[[dict[str, Any]], dict[str, Any] | list[dict[str, Any]]]
SignatureFn = Callable[[dict[str, Any]], str]


def recover_review_selection_shortfall(
    *,
    selection_input: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
    target_min_count: int,
    constraint_reason_map: dict[str, str] | None,
    domain_guard_active: bool = False,
    cross_domain_noise_fn: Callable[[dict[str, Any]], bool] | None = None,
    coverage_context: dict[str, Any] | None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
    rank_case_fn: ReviewRankFn,
) -> tuple[list[dict[str, Any]], dict[str, str], int]:
    selected = _dict_case_items(selection_input)
    target_min = max(1, int(target_min_count or 1))
    if len(selected) >= target_min:
        return selected, dict(constraint_reason_map or {}), 0

    reason_map = dict(constraint_reason_map or {})
    selected_signatures = {case_signature(item) for item in selected if case_signature(item)}
    fill_pool = [
        item
        for item in _dict_case_items(candidate_cases)
        if case_signature(item) and case_signature(item) not in selected_signatures
    ]
    if domain_guard_active and cross_domain_noise_fn is not None:
        guarded_fill_pool = [item for item in fill_pool if not cross_domain_noise_fn(item)]
        if guarded_fill_pool:
            fill_pool = guarded_fill_pool

    fill_pool.sort(
        key=lambda item: tuple(
            -value
            for value in rank_case_fn(
                item,
                coverage_context=coverage_context,
                rule_diagnostics=rule_diagnostics,
            )
        )
        + (review_case_id(item),)
    )

    before_count = len(selected)
    for fill_case in fill_pool:
        if len(selected) >= target_min:
            break
        signature = case_signature(fill_case)
        if not signature or signature in selected_signatures:
            continue
        selected.append(fill_case)
        selected_signatures.add(signature)
        reason_map.setdefault(signature, "retained_by_shortfall_recovery")

    return selected, reason_map, max(0, len(selected) - before_count)


def resolve_review_post_rerank_floor_count(
    *,
    candidate_count_before_review: int,
    reference_count_effective: int,
    generation_coverage_mode: str,
) -> int:
    candidate_count = int(candidate_count_before_review or 0)
    if candidate_count >= 2:
        reference_count = int(reference_count_effective or 0)
        if reference_count >= 10:
            review_floor_ratio = 0.2
            coverage_mode = str(generation_coverage_mode or "")
            if coverage_mode == "expanded_regression":
                review_floor_ratio = 0.80
            elif coverage_mode == "full_functional_regression":
                review_floor_ratio = 0.35
            return min(
                candidate_count,
                max(2, int(round(float(reference_count) * float(review_floor_ratio)))),
            )
        return min(candidate_count, 2)
    return 1


def apply_append_target_cap(
    *,
    requirement: str,
    parsed_cases: Iterable[Any],
    append_final_cap_count: int,
    analyze_coverage_fn: CoverageAnalyzeFn,
    rule_diagnostics_fn: RuleDiagnosticsFn,
    rank_case_fn: ReviewRankFn,
    signature_fn: SignatureFn,
) -> tuple[list[Any], set[str], int]:
    original_cases = list(parsed_cases)
    target_count = int(append_final_cap_count or 0)
    dict_case_count = int(sum(1 for item in original_cases if isinstance(item, dict)))
    if target_count <= 0 or dict_case_count <= target_count:
        return list(original_cases), set(), 0

    dict_cases = _dict_case_items(original_cases)
    cap_coverage = analyze_coverage_fn(requirement, dict_cases)
    cap_rule_diagnostics = rule_diagnostics_fn(cap_coverage)
    indexed_cases = [
        (index, item)
        for index, item in enumerate(original_cases)
        if isinstance(item, dict)
    ]
    indexed_cases.sort(
        key=lambda pair: tuple(
            [
                -value
                for value in rank_case_fn(
                    pair[1],
                    coverage_context=cap_coverage,
                    rule_diagnostics=cap_rule_diagnostics,
                )
            ]
        )
        + (int(pair[0]),)
    )
    keep_indices = {
        int(index)
        for index, _case in indexed_cases[:target_count]
    }
    drop_signatures = {
        signature_fn(case)
        for index, case in enumerate(original_cases)
        if isinstance(case, dict) and int(index) not in keep_indices
    }
    capped_cases = [
        case
        for index, case in enumerate(original_cases)
        if isinstance(case, dict) and int(index) in keep_indices
    ]
    return capped_cases, drop_signatures, int(len(drop_signatures))


def recover_post_rerank_shortfall(
    *,
    parsed_cases: list[dict[str, Any]],
    review_selection_input: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
    floor_count: int,
    coverage_context: dict[str, Any] | None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
    rank_case_fn: ReviewRankFn,
) -> tuple[list[dict[str, Any]], int]:
    recovered_cases = _dict_case_items(parsed_cases)
    target_floor = max(1, int(floor_count or 1))
    if len(recovered_cases) >= target_floor:
        return recovered_cases, 0

    recovered_signatures = {case_signature(item) for item in recovered_cases if case_signature(item)}
    recovery_pool: list[dict[str, Any]] = []
    for source_case in [*_dict_case_items(review_selection_input), *_dict_case_items(candidate_cases)]:
        signature = case_signature(source_case)
        if not signature or signature in recovered_signatures:
            continue
        recovered_signatures.add(signature)
        recovery_pool.append(source_case)

    recovery_pool.sort(
        key=lambda item: tuple(
            -value
            for value in rank_case_fn(
                item,
                coverage_context=coverage_context,
                rule_diagnostics=rule_diagnostics,
            )
        )
        + (review_case_id(item),)
    )

    before_count = len(recovered_cases)
    for fill_case in recovery_pool:
        if len(recovered_cases) >= target_floor:
            break
        recovered_cases.append(fill_case)

    return recovered_cases, max(0, len(recovered_cases) - before_count)


def resolve_review_llm_drop_reason_maps(
    *,
    pool_cases: list[dict[str, Any]],
    selected_cases: list[dict[str, Any]],
    raw_drop_reason_map: dict[str, str] | None,
    raw_drop_reason_origin_map: dict[str, str] | None = None,
    coverage_context: dict[str, Any] | None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    raw_map = {
        str(signature or "").strip(): str(reason or "").strip()
        for signature, reason in dict(raw_drop_reason_map or {}).items()
        if str(signature or "").strip()
    }
    origin_map = {
        str(signature or "").strip(): str(origin or "").strip().lower()
        for signature, origin in dict(raw_drop_reason_origin_map or {}).items()
        if str(signature or "").strip()
    }
    resolved_map: dict[str, str] = {}
    source_map: dict[str, str] = {}
    evidence_map: dict[str, Any] = {}
    candidate_items = _dict_case_items(pool_cases)
    selected_items = _dict_case_items(selected_cases)
    selected_signatures = {case_signature(item) for item in selected_items if case_signature(item)}
    semantic_duplicate_threshold = 0.82

    selected_entries: list[dict[str, Any]] = []
    selected_by_bucket: dict[str, list[dict[str, Any]]] = {}
    for item in selected_items:
        signature = case_signature(item)
        if not signature:
            continue
        rule_keys = list(extract_rule_keys(item))
        selected_rank = rank_review_case_for_fill(
            item,
            coverage_context=coverage_context,
            rule_diagnostics=rule_diagnostics,
        )
        entry = {
            "signature": signature,
            "case_id": review_case_id(item),
            "bucket": case_coverage_bucket(item),
            "semantic_signature": semantic_signature(item, rule_keys),
            "semantic_tokens": semantic_tokenize(
                case_flat_text(item, ("description", "expected_result", "test_input", "steps"), separator=" ")
            ),
            "rank_tuple": tuple(int(x) for x in selected_rank),
        }
        selected_entries.append(entry)
        selected_by_bucket.setdefault(str(entry.get("bucket") or ""), []).append(entry)

    for item in candidate_items:
        signature = case_signature(item)
        if not signature or signature in selected_signatures:
            continue

        raw_reason = str(raw_map.get(signature) or "").strip()

        score_profile = score_case_priority(
            item,
            coverage_context=coverage_context,
            rule_diagnostics=rule_diagnostics,
        )
        missing_rule_hits = [str(x) for x in (score_profile.get("missing_rule_hits") or []) if str(x).strip()]
        core_rule_hits = [str(x) for x in (score_profile.get("core_rule_hits") or []) if str(x).strip()]
        unique_coverage_hits = [str(x) for x in (score_profile.get("unique_coverage_hits") or []) if str(x).strip()]
        coverage_gain_score = int(score_profile.get("coverage_gain_score") or 0)
        has_coverage_signal = bool(
            missing_rule_hits or core_rule_hits or unique_coverage_hits or coverage_gain_score > 0
        )
        reuse_risk_hit = bool(score_profile.get("reuse_risk_hit"))
        high_signal_seed = bool(is_high_signal(item, score_profile))
        has_high_signal = bool(high_signal_seed or reuse_risk_hit)
        focus_score = int(case_focus_score(item))
        bucket = case_coverage_bucket(item)
        priority = case_priority(item)
        moderate_signal = bool(priority in {"P0", "P1"} or focus_score >= 1 or coverage_gain_score > 0)
        bucket_selected = [entry for entry in (selected_by_bucket.get(bucket) or []) if isinstance(entry, dict)]

        candidate_rank_tuple = tuple(
            int(x)
            for x in rank_review_case_for_fill(
                item,
                coverage_context=coverage_context,
                rule_diagnostics=rule_diagnostics,
            )
        )
        best_bucket_rank = max(
            (tuple(int(x) for x in (entry.get("rank_tuple") or ())) for entry in bucket_selected),
            default=(),
        )
        has_competition_signal = bool(bucket_selected and best_bucket_rank and best_bucket_rank > candidate_rank_tuple)
        competition_only_signal = bool(
            has_competition_signal and not has_coverage_signal and not has_high_signal and not reuse_risk_hit
        )
        has_positive_evidence = bool(has_coverage_signal or has_high_signal or has_competition_signal)

        base_evidence = {
            "bucket": bucket,
            "priority": priority,
            "focus_score": int(focus_score),
            "coverage_gain_score": int(coverage_gain_score),
            "selected_case_ids": [
                str(entry.get("case_id") or "") for entry in bucket_selected if str(entry.get("case_id") or "")
            ][:3],
            "selected_count_in_bucket": int(len(bucket_selected)),
            "has_positive_evidence": bool(has_positive_evidence),
            "has_coverage_signal": bool(has_coverage_signal),
            "has_high_signal": bool(has_high_signal),
            "has_competition_signal": bool(has_competition_signal),
            "missing_rule_hits": missing_rule_hits,
            "core_rule_hits": core_rule_hits,
            "unique_coverage_hits": unique_coverage_hits,
            "reuse_risk_hit": bool(reuse_risk_hit),
        }

        if raw_reason and raw_reason.lower() not in {"unspecified", "unknown", "other"}:
            llm_reason = normalize_review_llm_reason(raw_reason)
            llm_reason_lower = llm_reason.lower()
            adjusted_reason = llm_reason
            adjustment_rule = ""
            reason_origin = str(origin_map.get(signature) or "llm").strip().lower()
            if reason_origin not in {"llm", "fallback_llm"}:
                reason_origin = "llm"
            if llm_reason_lower == "coverage_redundant":
                if has_coverage_signal:
                    adjusted_reason = "coverage_protected_omitted"
                    adjustment_rule = "llm_coverage_redundant_with_coverage_signal"
                elif competition_only_signal and moderate_signal:
                    adjusted_reason = "selection_tradeoff_omitted"
                    adjustment_rule = "llm_coverage_redundant_with_competition_only_signal"
                elif has_high_signal:
                    adjusted_reason = "high_signal_omitted"
                    adjustment_rule = "llm_coverage_redundant_with_high_signal"
            elif llm_reason_lower == "low_value":
                if has_coverage_signal:
                    adjusted_reason = "coverage_protected_omitted"
                    adjustment_rule = "llm_low_value_with_coverage_signal"
                elif has_competition_signal and moderate_signal:
                    adjusted_reason = "selection_tradeoff_omitted"
                    adjustment_rule = "llm_low_value_with_competition_signal"
                elif has_high_signal:
                    adjusted_reason = "high_signal_omitted"
                    adjustment_rule = "llm_low_value_with_high_signal"

            if adjusted_reason != llm_reason:
                resolved_map[signature] = str(adjusted_reason)
                source_map[signature] = reason_origin if reason_origin == "fallback_llm" else "deterministic_backfill"
                evidence_map[signature] = {
                    **base_evidence,
                    "reason_from": "llm",
                    "reason_adjusted_from": llm_reason,
                    "reason_adjusted_to": str(adjusted_reason),
                    "reason_adjustment_rule": str(adjustment_rule),
                    "reason_origin": str(reason_origin),
                }
            else:
                resolved_map[signature] = llm_reason
                source_map[signature] = str(reason_origin)
                evidence_map[signature] = {
                    **base_evidence,
                    "reason_from": "llm",
                    "reason_origin": str(reason_origin),
                }
            continue

        if (
            bucket_selected
            and not has_coverage_signal
            and not has_high_signal
            and not reuse_risk_hit
            and not competition_only_signal
        ):
            resolved_map[signature] = "coverage_redundant"
            source_map[signature] = "deterministic_backfill"
            evidence_map[signature] = dict(base_evidence)
            continue

        rule_keys = list(extract_rule_keys(item))
        semantic_sig = semantic_signature(item, rule_keys)
        semantic_tokens = semantic_tokenize(
            " ".join(
                [
                    str(item.get("description") or ""),
                    str(item.get("expected_result") or ""),
                    str(item.get("test_input") or ""),
                    " ".join([str(x) for x in item.get("steps", [])]) if isinstance(item.get("steps"), list) else "",
                ]
            )
        )
        duplicate_match: dict[str, Any] | None = None
        duplicate_similarity = 0.0
        for selected_entry in selected_entries:
            selected_signature = str(selected_entry.get("semantic_signature") or "")
            selected_tokens = set(selected_entry.get("semantic_tokens") or set())
            similarity = jaccard_similarity(semantic_tokens, selected_tokens)
            if semantic_sig and semantic_sig == selected_signature:
                duplicate_match = selected_entry
                duplicate_similarity = 1.0
                break
            if similarity >= semantic_duplicate_threshold and similarity >= duplicate_similarity:
                duplicate_match = selected_entry
                duplicate_similarity = float(similarity)
        if duplicate_match:
            resolved_map[signature] = "duplicate"
            source_map[signature] = "deterministic_backfill"
            evidence_map[signature] = {
                **base_evidence,
                "duplicate_of_case_id": str(duplicate_match.get("case_id") or ""),
                "duplicate_bucket": str(duplicate_match.get("bucket") or ""),
                "similarity": round(float(duplicate_similarity), 4),
            }
            continue

        if (
            not has_coverage_signal
            and not has_high_signal
            and not reuse_risk_hit
            and not moderate_signal
            and not has_competition_signal
        ):
            resolved_map[signature] = "low_value"
            source_map[signature] = "deterministic_backfill"
            evidence_map[signature] = dict(base_evidence)
            continue

        if has_coverage_signal:
            resolved_map[signature] = "coverage_protected_omitted"
            source_map[signature] = "deterministic_backfill"
            evidence_map[signature] = dict(base_evidence)
            continue

        if has_high_signal:
            resolved_map[signature] = "high_signal_omitted"
            source_map[signature] = "deterministic_backfill"
            evidence_map[signature] = dict(base_evidence)
            continue

        if competition_only_signal and moderate_signal:
            resolved_map[signature] = "selection_tradeoff_omitted"
            source_map[signature] = "deterministic_backfill"
            evidence_map[signature] = dict(base_evidence)
            continue

        resolved_map[signature] = "fallback_unspecified"
        source_map[signature] = "deterministic_backfill"
        evidence_map[signature] = dict(base_evidence)

    return resolved_map, source_map, evidence_map
