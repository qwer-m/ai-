from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..coverage.coverage_analyzer import case_complexity_profile
from .case_access import case_flat_text, case_priority
from .result_postprocess_priority_semantics import score_case_priority
from .streaming_case_keys import (
    case_coverage_bucket,
    case_focus_score,
    case_priority_score,
    case_signature,
    review_case_id,
)
from .streaming_postprocess_utils import _dict_case_items
from .streaming_review_keys import review_domain, review_scenario
from .streaming_review_mapping import normalize_review_llm_reason
from .streaming_rule_keys import extract_rule_keys
from .streaming_semantic_text import jaccard_similarity, semantic_signature, semantic_tokenize

ReviewRankFn = Callable[..., tuple[int, ...]]
CoverageAnalyzeFn = Callable[[str, list[dict[str, Any]]], dict[str, Any]]
RuleDiagnosticsFn = Callable[[dict[str, Any]], dict[str, Any] | list[dict[str, Any]]]
SignatureFn = Callable[[dict[str, Any]], str]
ScorePriorityFn = Callable[..., dict[str, Any]]
MustKeepReasonsFn = Callable[..., list[str]]


@dataclass(frozen=True)
class ReviewCandidatePoolSplit:
    must_keep_cases: list[dict[str, Any]]
    llm_pool_cases: list[dict[str, Any]]
    must_keep_signatures: set[str]
    must_keep_reason_map: dict[str, list[str]]


def summarize_review_decision_counts(
    review_decision_table: Iterable[Any] | None,
    dropped_rows: Iterable[Any] | None,
    *,
    ui_like_ratio_postprocess_drop_count: int = 0,
    final_description_dedup_drop_signatures: Iterable[Any] | None = None,
    drop_by_review_llm_count: int = 0,
    drop_by_review_selector_count: int = 0,
) -> dict[str, int]:
    review_rows = [row for row in (review_decision_table or []) if isinstance(row, dict)]
    dropped_items = [row for row in (dropped_rows or []) if isinstance(row, dict)]
    return {
        **summarize_review_must_keep_and_signal_counts(review_rows),
        **summarize_review_drop_stage_counts(
            dropped_items,
            drop_by_review_llm_count=drop_by_review_llm_count,
            drop_by_review_selector_count=drop_by_review_selector_count,
        ),
        **summarize_review_drop_reason_counts(
            dropped_items,
            ui_like_ratio_postprocess_drop_count=ui_like_ratio_postprocess_drop_count,
            final_description_dedup_drop_signatures=final_description_dedup_drop_signatures,
        ),
    }


def summarize_review_must_keep_and_signal_counts(
    review_decision_table: Iterable[Any] | None,
) -> dict[str, int]:
    review_rows = [row for row in (review_decision_table or []) if isinstance(row, dict)]
    return {
        "must_keep_candidate_count": int(sum(1 for row in review_rows if bool(row.get("must_keep_candidate")))),
        "must_keep_retained_count": int(
            sum(
                1
                for row in review_rows
                if bool(row.get("must_keep_candidate")) and bool(row.get("retained_final"))
            )
        ),
        "must_keep_dropped_count": int(
            sum(
                1
                for row in review_rows
                if bool(row.get("must_keep_candidate")) and not bool(row.get("retained_final"))
            )
        ),
        "retained_due_to_coverage_value_count": int(
            sum(
                1
                for row in review_rows
                if str(row.get("retained_reason") or "") == "retained_due_to_coverage_value"
            )
        ),
        "must_cover_rule_hit_count": int(sum(1 for row in review_rows if bool(row.get("hit_must_cover_rule")))),
        "forbidden_pattern_violation_count": int(
            sum(1 for row in review_rows if bool(row.get("violates_forbidden_pattern")))
        ),
        "soft_constraint_hit_count": int(sum(1 for row in review_rows if bool(row.get("hits_soft_constraint")))),
        "quality_hint_satisfied_count": int(
            sum(1 for row in review_rows if bool(row.get("satisfies_quality_hint")))
        ),
    }


def summarize_review_drop_stage_counts(
    dropped_rows: Iterable[Any] | None,
    *,
    drop_by_review_llm_count: int = 0,
    drop_by_review_selector_count: int = 0,
) -> dict[str, int]:
    dropped_items = [row for row in (dropped_rows or []) if isinstance(row, dict)]
    return {
        "drop_by_review_llm_count": int(drop_by_review_llm_count),
        "drop_by_review_selector_count": int(drop_by_review_selector_count),
        "drop_by_review_gate_count": int(
            sum(1 for row in dropped_items if row.get("dropped_stage") == "review_gate")
        ),
        "drop_by_pre_gate_dedup_count": int(
            sum(1 for row in dropped_items if row.get("dropped_stage") == "review_dedup_pre_gate")
        ),
        "drop_by_post_review_dedup_count": int(
            sum(1 for row in dropped_items if row.get("dropped_stage") == "post_review_dedup_or_reorder")
        ),
    }


def summarize_review_drop_reason_counts(
    dropped_rows: Iterable[Any] | None,
    *,
    ui_like_ratio_postprocess_drop_count: int = 0,
    final_description_dedup_drop_signatures: Iterable[Any] | None = None,
) -> dict[str, int]:
    dropped_items = [row for row in (dropped_rows or []) if isinstance(row, dict)]
    final_description_duplicate_count = (
        sum(1 for _item in final_description_dedup_drop_signatures)
        if final_description_dedup_drop_signatures is not None
        else 0
    )

    return {
        "drop_no_new_signal_count": int(
            sum(
                1
                for row in dropped_items
                if row.get("dropped_reason") == "drop_no_new_rule_no_new_bucket_no_high_signal"
            )
        ),
        "drop_rule_cap_count": int(
            sum(1 for row in dropped_items if row.get("dropped_reason") == "drop_rule_cap")
        ),
        "drop_ui_like_redundant_count": int(
            sum(1 for row in dropped_items if row.get("dropped_reason") == "drop_ui_like_redundant_case")
        ),
        "drop_ui_like_ratio_cap_count": int(
            sum(1 for row in dropped_items if row.get("dropped_reason") == "drop_ui_like_ratio_cap")
        ),
        "drop_outside_target_window_count": int(
            sum(1 for row in dropped_items if row.get("dropped_reason") == "drop_outside_target_window")
        ),
        "drop_by_rerank_low_signal_count": int(
            sum(
                1
                for row in dropped_items
                if row.get("dropped_reason") == "drop_no_new_rule_no_new_bucket_no_high_signal"
            )
        ),
        "dropped_model_priority_p0_p1_count": int(
            sum(1 for row in dropped_items if str(row.get("model_priority_current") or "").upper() in {"P0", "P1"})
        ),
        "dropped_core_rule_hit_count": int(sum(1 for row in dropped_items if bool(row.get("core_rule_hits")))),
        "dropped_missing_rule_hit_count": int(sum(1 for row in dropped_items if bool(row.get("missing_rule_hits")))),
        "dropped_high_signal_count": int(sum(1 for row in dropped_items if bool(row.get("high_signal")))),
        "dropped_has_coverage_value_count": int(
            sum(1 for row in dropped_items if bool(row.get("has_coverage_value")))
        ),
        "drop_ui_like_ratio_postprocess_count": int(ui_like_ratio_postprocess_drop_count),
        "drop_final_description_duplicate_count": int(final_description_duplicate_count),
    }


def summarize_review_llm_drop_diagnostics(
    *,
    review_llm_applied: bool,
    review_llm_omitted_signatures: Iterable[Any] | None,
    dropped_rows: list[dict[str, Any]] | None,
    review_llm_drop_reason_map: dict[str, str] | None,
    review_llm_drop_reason_raw_map: dict[str, str] | None,
    review_llm_drop_reason_source_map: dict[str, str] | None,
    review_llm_drop_reason_evidence_map: dict[str, Any] | None,
    review_llm_runtime_debug: dict[str, Any] | None,
) -> dict[str, Any]:
    runtime_debug = dict(review_llm_runtime_debug or {})
    dropped_items = _dict_case_items(dropped_rows or [])
    reason_map = dict(review_llm_drop_reason_map or {})
    raw_reason_map = dict(review_llm_drop_reason_raw_map or {})
    source_map = dict(review_llm_drop_reason_source_map or {})
    evidence_map = dict(review_llm_drop_reason_evidence_map or {})

    review_llm_omitted_for_summary = {
        str(signature or "").strip()
        for signature in (review_llm_omitted_signatures or [])
        if str(signature or "").strip()
    }
    if review_llm_applied and not review_llm_omitted_for_summary:
        review_llm_omitted_for_summary = {
            str(row.get("signature") or "").strip()
            for row in dropped_items
            if str(row.get("dropped_stage") or "") == "review_llm"
            and str(row.get("signature") or "").strip()
        }

    review_llm_drop_reason_counts: dict[str, int] = {}
    review_llm_drop_reason_raw_counts: dict[str, int] = {}
    review_llm_drop_reason_source_counts: dict[str, int] = {}
    fallback_with_positive_evidence_count = 0
    fallback_without_positive_evidence_count = 0
    for signature in review_llm_omitted_for_summary:
        reason_key = str(reason_map.get(signature) or "").strip() or "unspecified"
        review_llm_drop_reason_counts[reason_key] = int(review_llm_drop_reason_counts.get(reason_key, 0)) + 1
        raw_reason_key = str(raw_reason_map.get(signature) or "").strip() or "unspecified"
        review_llm_drop_reason_raw_counts[raw_reason_key] = int(
            review_llm_drop_reason_raw_counts.get(raw_reason_key, 0)
        ) + 1
        source_key = str(source_map.get(signature) or "").strip() or "unresolved"
        review_llm_drop_reason_source_counts[source_key] = int(
            review_llm_drop_reason_source_counts.get(source_key, 0)
        ) + 1
        if reason_key == "fallback_unspecified":
            evidence = evidence_map.get(signature)
            if not isinstance(evidence, dict):
                evidence = {}
            if bool(evidence.get("has_positive_evidence")):
                fallback_with_positive_evidence_count += 1
            else:
                fallback_without_positive_evidence_count += 1

    drop_by_review_llm_count = int(len(review_llm_omitted_for_summary))
    drop_by_review_selector_count = int(
        sum(1 for row in dropped_items if row.get("dropped_stage") == "review_selector")
    )
    if (
        str(runtime_debug.get("final_source") or "") == "review_selector"
        and not review_llm_applied
        and dropped_items
    ):
        drop_by_review_selector_count = max(drop_by_review_selector_count, int(len(dropped_items)))

    fallback_dropped_reason_count = int(
        (
            runtime_debug.get("final_dropped_reason_payload_count")
            if str(runtime_debug.get("final_source") or "") == "fallback_llm"
            else 0
        )
        or 0
    )
    fallback_dropped_reason_mapped_count = int(review_llm_drop_reason_source_counts.get("fallback_llm", 0))
    if str(runtime_debug.get("final_source") or "") == "fallback_llm":
        fallback_dropped_reason_mapped_count = int(runtime_debug.get("final_dropped_reason_count") or 0)
    fallback_dropped_reason_unmapped_count = int(
        (
            runtime_debug.get("final_dropped_reason_unmapped_count")
            if str(runtime_debug.get("final_source") or "") == "fallback_llm"
            else 0
        )
        or 0
    )
    llm_reason_total = int(review_llm_drop_reason_source_counts.get("llm", 0)) + int(
        review_llm_drop_reason_source_counts.get("fallback_llm", 0)
    )
    deterministic_backfill_total = int(review_llm_drop_reason_source_counts.get("deterministic_backfill", 0))
    fallback_reason_incomplete = bool(
        str(runtime_debug.get("final_source") or "") == "fallback_llm"
        and fallback_dropped_reason_count <= 0
    )
    fallback_reason_coverage_ratio = round(
        float(fallback_dropped_reason_mapped_count) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 0.0
    llm_reason_coverage_ratio = round(
        float(llm_reason_total) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 0.0
    deterministic_backfill_ratio = round(
        float(deterministic_backfill_total) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 0.0
    final_reason_incomplete = bool(drop_by_review_llm_count > 0 and llm_reason_total <= 0)
    final_reason_coverage_ratio = round(
        float(llm_reason_total) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 1.0

    runtime_debug_updates: dict[str, Any] = {
        "fallback_reason_incomplete": bool(fallback_reason_incomplete),
        "final_reason_incomplete": bool(final_reason_incomplete),
        "final_reason_coverage_ratio": float(final_reason_coverage_ratio),
    }
    if final_reason_incomplete and str(runtime_debug.get("applied_reason") or "") == "mapped_valid_payload":
        runtime_debug_updates["applied_reason"] = "mapped_valid_payload_reason_incomplete"

    reason_source_breakdown = {
        "primary": int(review_llm_drop_reason_source_counts.get("llm", 0)),
        "fallback": int(review_llm_drop_reason_source_counts.get("fallback_llm", 0)),
        "backfill": int(review_llm_drop_reason_source_counts.get("deterministic_backfill", 0)),
    }
    return {
        "review_llm_drop_reason_breakdown": dict(review_llm_drop_reason_counts),
        "review_llm_drop_reason_raw_breakdown": dict(review_llm_drop_reason_raw_counts),
        "review_llm_drop_reason_source_breakdown": dict(review_llm_drop_reason_source_counts),
        "fallback_reason_incomplete": bool(fallback_reason_incomplete),
        "final_reason_incomplete": bool(final_reason_incomplete),
        "final_reason_coverage_ratio": float(final_reason_coverage_ratio),
        "fallback_dropped_reason_count": int(fallback_dropped_reason_count),
        "fallback_dropped_reason_mapped_count": int(fallback_dropped_reason_mapped_count),
        "fallback_dropped_reason_unmapped_count": int(fallback_dropped_reason_unmapped_count),
        "fallback_reason_coverage_ratio": float(fallback_reason_coverage_ratio),
        "llm_reason_coverage_ratio": float(llm_reason_coverage_ratio),
        "deterministic_backfill_ratio": float(deterministic_backfill_ratio),
        "reason_source_breakdown": dict(reason_source_breakdown),
        "fallback_with_positive_evidence_count": int(fallback_with_positive_evidence_count),
        "fallback_without_positive_evidence_count": int(fallback_without_positive_evidence_count),
        "drop_by_review_llm_count": int(drop_by_review_llm_count),
        "drop_by_review_selector_count": int(drop_by_review_selector_count),
        "runtime_debug_updates": dict(runtime_debug_updates),
    }


def review_llm_drop_summary_fields(
    review_llm_drop_diagnostics: dict[str, Any] | None,
    review_llm_runtime_debug: dict[str, Any] | None,
) -> dict[str, Any]:
    diagnostics = dict(review_llm_drop_diagnostics or {})
    runtime_debug = dict(review_llm_runtime_debug or {})
    return {
        "review_llm_drop_reason_breakdown": dict(
            diagnostics.get("review_llm_drop_reason_breakdown") or {}
        ),
        "review_llm_drop_reason_raw_breakdown": dict(
            diagnostics.get("review_llm_drop_reason_raw_breakdown") or {}
        ),
        "review_llm_drop_reason_source_breakdown": dict(
            diagnostics.get("review_llm_drop_reason_source_breakdown") or {}
        ),
        "fallback_reason_incomplete": bool(diagnostics.get("fallback_reason_incomplete")),
        "final_reason_incomplete": bool(diagnostics.get("final_reason_incomplete")),
        "final_reason_coverage_ratio": float(diagnostics.get("final_reason_coverage_ratio") or 0.0),
        "fallback_dropped_reason_count": int(diagnostics.get("fallback_dropped_reason_count") or 0),
        "fallback_dropped_reason_mapped_count": int(
            diagnostics.get("fallback_dropped_reason_mapped_count") or 0
        ),
        "fallback_dropped_reason_unmapped_count": int(
            diagnostics.get("fallback_dropped_reason_unmapped_count") or 0
        ),
        "fallback_reason_coverage_ratio": float(diagnostics.get("fallback_reason_coverage_ratio") or 0.0),
        "llm_reason_coverage_ratio": float(diagnostics.get("llm_reason_coverage_ratio") or 0.0),
        "deterministic_backfill_ratio": float(diagnostics.get("deterministic_backfill_ratio") or 0.0),
        "reason_source_breakdown": dict(diagnostics.get("reason_source_breakdown") or {}),
        "primary_reason_incomplete": bool(runtime_debug.get("primary_reason_incomplete")),
        "primary_dropped_reason_count": int(runtime_debug.get("primary_dropped_reason_count") or 0),
        "primary_dropped_reason_payload_count": int(
            runtime_debug.get("primary_dropped_reason_payload_count") or 0
        ),
        "primary_reason_coverage_ratio": float(runtime_debug.get("primary_reason_coverage_ratio") or 0.0),
        "fallback_with_positive_evidence_count": int(
            diagnostics.get("fallback_with_positive_evidence_count") or 0
        ),
        "fallback_without_positive_evidence_count": int(
            diagnostics.get("fallback_without_positive_evidence_count") or 0
        ),
    }


def build_review_decision_summary_payload(
    *,
    review_decision_table: Iterable[Any] | None,
    dropped_rows: Iterable[Any] | None,
    review_flow_summary_fields: dict[str, Any] | None,
    parsed_result: Iterable[Any] | None,
    reasoning_leakage_hits_fn: Callable[[dict[str, Any]], bool],
    priority_summary_fields: dict[str, Any] | None,
    needs_priority_review: bool,
    review_llm_applied: bool,
    review_selection_input: Any,
    dict_case_count_fn: Callable[[Any], int],
    review_selected_count: int | None,
    review_target_min_count: int | None,
    review_target_max_count: int | None,
    review_shortfall_detected: bool,
    review_shortfall_before_count: int,
    review_shortfall_recovered_count: int,
    review_post_rerank_floor_count: int | None,
    review_post_rerank_recovered_count: int | None,
    final_target_floor_count: int | None,
    final_floor_recovery_attempted: bool,
    final_floor_recovery_applied: bool,
    final_floor_recovered_count: int | None,
    final_floor_recovery_reason: str | None,
    final_confirmed_conflict_drop_count: int | None,
    final_shortfall_supplement_attempted: bool,
    final_shortfall_supplement_applied: bool,
    final_shortfall_supplement_count: int | None,
    final_shortfall_supplement_reason: str | None,
    generation_mode: str | None,
    effective_generation_coverage_mode_source: str | None,
    explicit_generation_mode_override: bool,
    explicit_expected_count_floor_preserved: bool,
    review_fill_source: str | None,
    review_llm_selected_signatures: Iterable[Any] | None,
    review_llm_runtime_debug: dict[str, Any] | None,
    review_constraint_retained_signatures: Iterable[Any] | None,
    review_llm_summary_fields: dict[str, Any] | None,
    review_llm_pool_count: int,
    stage_counts: dict[str, Any] | None,
    review_decision_counts: dict[str, Any] | None,
) -> dict[str, Any]:
    review_rows = [row for row in (review_decision_table or []) if isinstance(row, dict)]
    dropped_items = [row for row in (dropped_rows or []) if isinstance(row, dict)]
    parsed_items = parsed_result or []
    stages = dict(stage_counts or {})

    return {
        "candidate_total": int(len(review_rows)),
        "retained_total": int(sum(1 for row in review_rows if bool(row.get("retained_final")))),
        "dropped_total": int(len(dropped_items)),
        **dict(review_flow_summary_fields or {}),
        "final_reasoning_leakage_case_count": int(
            sum(
                1
                for item in parsed_items
                if isinstance(item, dict) and reasoning_leakage_hits_fn(item)
            )
        ),
        **dict(priority_summary_fields or {}),
        "invalid_case_count": int(
            sum(1 for row in review_rows if str(row.get("case_quality") or "") == "invalid_case")
        ),
        "reasoning_leakage_case_count": int(
            sum(1 for row in review_rows if str(row.get("invalid_case_reason") or "") == "reasoning_leakage")
        ),
        "needs_priority_review": bool(needs_priority_review),
        "review_llm_filter_applied": bool(review_llm_applied),
        "review_input_size": int(dict_case_count_fn(review_selection_input)),
        "review_output_size": int(review_selected_count or 0),
        "review_target_min_count": int(review_target_min_count or 1),
        "review_target_max_count": int(review_target_max_count or review_target_min_count or 1),
        "review_shortfall_detected": bool(review_shortfall_detected),
        "review_shortfall_before_count": int(review_shortfall_before_count),
        "review_shortfall_recovered_count": int(review_shortfall_recovered_count),
        "review_post_rerank_floor_count": int(review_post_rerank_floor_count or 1),
        "review_post_rerank_recovered_count": int(review_post_rerank_recovered_count or 0),
        "final_target_floor_count": int(final_target_floor_count or 0),
        "final_floor_recovery_attempted": bool(final_floor_recovery_attempted),
        "final_floor_recovery_applied": bool(final_floor_recovery_applied),
        "final_floor_recovered_count": int(final_floor_recovered_count or 0),
        "final_floor_recovery_reason": str(final_floor_recovery_reason or ""),
        "final_confirmed_conflict_drop_count": int(final_confirmed_conflict_drop_count or 0),
        "final_shortfall_supplement_attempted": bool(final_shortfall_supplement_attempted),
        "final_shortfall_supplement_applied": bool(final_shortfall_supplement_applied),
        "final_shortfall_supplement_count": int(final_shortfall_supplement_count or 0),
        "final_shortfall_supplement_reason": str(final_shortfall_supplement_reason or ""),
        "requested_generation_mode": str(generation_mode or ""),
        "effective_generation_coverage_mode_source": str(effective_generation_coverage_mode_source or ""),
        "explicit_generation_mode_override": bool(explicit_generation_mode_override),
        "explicit_expected_count_floor_preserved": bool(explicit_expected_count_floor_preserved),
        "review_fill_source": str(review_fill_source or "none"),
        "review_llm_selected_count": int(len(list(review_llm_selected_signatures or []))),
        "review_llm_runtime_debug": dict(review_llm_runtime_debug or {}),
        "review_constraint_selected_count": int(len(list(review_constraint_retained_signatures or []))),
        **dict(review_llm_summary_fields or {}),
        "review_llm_pool_count": int(review_llm_pool_count),
        "candidate_by_pass": {
            "primary": int(stages.get("primary") or 0),
            "gap": int(stages.get("gap") or 0),
        },
        **dict(review_decision_counts or {}),
    }


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


def build_review_selection_constraints(
    cases: list[dict[str, Any]],
    *,
    reference_count: int,
    generation_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_cases = _dict_case_items(cases)
    total = int(len(candidate_cases))
    if total <= 0:
        return {
            "target_min_count": 1,
            "target_max_count": 1,
            "priority_min": {},
            "scenario_min": {},
            "domain_min": {},
        }

    reference = max(1, int(reference_count or total))
    profile = dict(generation_profile or {})
    coverage_mode = str(profile.get("coverage_mode") or "").strip()

    priority_counts: dict[str, int] = {}
    scenario_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    for case in candidate_cases:
        priority = case_priority(case)
        if priority in {"P0", "P1", "P2"}:
            priority_counts[priority] = int(priority_counts.get(priority, 0)) + 1
        scenario = review_scenario(case)
        scenario_counts[scenario] = int(scenario_counts.get(scenario, 0)) + 1
        domain = review_domain(case)
        domain_counts[domain] = int(domain_counts.get(domain, 0)) + 1

    priority_min: dict[str, int] = {}
    if int(priority_counts.get("P0") or 0) > 0:
        priority_min["P0"] = 1
    if int(priority_counts.get("P1") or 0) > 0:
        priority_min["P1"] = min(int(priority_counts.get("P1") or 0), 2)
    if int(priority_counts.get("P2") or 0) > 0:
        priority_min["P2"] = 1

    scenario_min: dict[str, int] = {}
    for scenario in ("happy", "state", "exception"):
        if int(scenario_counts.get(scenario) or 0) > 0:
            scenario_min[scenario] = 1

    domain_min: dict[str, int] = {}
    for domain in ("permission", "report"):
        if int(domain_counts.get(domain) or 0) > 0:
            domain_min[domain] = 1

    active_priority_count = int(sum(1 for value in priority_counts.values() if int(value) > 0))
    active_scenario_count = int(sum(1 for value in scenario_counts.values() if int(value) > 0))
    active_domain_count = int(sum(1 for value in domain_counts.values() if int(value) > 0))
    diversity_floor = int((active_priority_count * 2) + active_scenario_count + min(2, active_domain_count))

    target_min = min(
        total,
        max(
            8,
            int(round(total * 0.24)),
            int(diversity_floor),
        ),
    )
    target_max = min(
        total,
        max(
            int(target_min + 6),
            int(round(total * 0.42)),
            int(round(target_min * 1.6)),
        ),
    )

    if coverage_mode == "full_functional_regression":
        target_min = min(
            total,
            max(
                target_min,
                int(round(total * 0.65)),
                int(round(reference * 0.35)),
            ),
        )
        target_max = min(
            total,
            max(
                target_min,
                int(round(total * 0.90)),
                int(round(reference * 0.75)),
            ),
        )
    elif coverage_mode == "expanded_regression":
        target_min = min(
            total,
            max(
                target_min,
                int(round(total * 0.80)),
                int(round(reference * 0.80)),
            ),
        )
        target_max = min(
            total,
            max(
                target_min,
                int(round(total * 0.96)),
                int(round(reference * 1.10)),
            ),
        )
    elif coverage_mode == "standard_regression":
        target_min = min(
            total,
            max(
                target_min,
                int(round(total * 0.45)),
                int(round(reference * 0.35)),
            ),
        )
        target_max = min(
            total,
            max(
                target_min,
                int(round(total * 0.70)),
                int(round(reference * 0.65)),
            ),
        )
    else:
        reference_cap = max(12, int(round(reference * 0.65)))
        target_max = min(target_max, total, reference_cap)
    target_min = min(target_min, target_max)

    return {
        "target_min_count": int(target_min),
        "target_max_count": int(target_max),
        "priority_min": priority_min,
        "scenario_min": scenario_min,
        "domain_min": domain_min,
    }


def enforce_review_selection_constraints(
    *,
    selected_cases: list[dict[str, Any]],
    pool_cases: list[dict[str, Any]],
    constraints: dict[str, Any],
    coverage_context: dict[str, Any] | None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
    rank_case_fn: ReviewRankFn,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    selected: list[dict[str, Any]] = []
    selected_signature_set: set[str] = set()
    selected_case_id_set: set[str] = set()
    constraint_reasons: dict[str, str] = {}

    def _append(case: dict[str, Any], reason: str = "") -> None:
        signature = case_signature(case)
        if not signature or signature in selected_signature_set:
            return
        selected.append(case)
        selected_signature_set.add(signature)
        case_id = review_case_id(case)
        if case_id:
            selected_case_id_set.add(case_id)
        if reason and signature:
            constraint_reasons[signature] = reason

    for case in selected_cases:
        if isinstance(case, dict):
            _append(case)

    all_pool_cases = _dict_case_items(pool_cases)
    remaining_pool = [item for item in all_pool_cases if case_signature(item) not in selected_signature_set]

    def _select_best(predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
        candidates = [item for item in remaining_pool if predicate(item)]
        if not candidates:
            return None
        candidates.sort(
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
        return candidates[0]

    def _selected_priority_count(priority: str) -> int:
        return int(sum(1 for item in selected if case_priority(item) == str(priority).upper()))

    def _selected_scenario_count(scenario: str) -> int:
        return int(sum(1 for item in selected if review_scenario(item) == str(scenario).strip().lower()))

    def _selected_domain_count(domain: str) -> int:
        return int(sum(1 for item in selected if review_domain(item) == str(domain).strip().lower()))

    priority_min = dict(constraints.get("priority_min") or {})
    for priority, min_count in priority_min.items():
        required = max(0, int(min_count or 0))
        while _selected_priority_count(priority) < required:
            best = _select_best(lambda item, p=priority: case_priority(item) == str(p).upper())
            if best is None:
                break
            _append(best, reason=f"retained_by_constraint_priority_{priority}")
            remaining_pool = [item for item in remaining_pool if case_signature(item) != case_signature(best)]

    scenario_min = dict(constraints.get("scenario_min") or {})
    for scenario, min_count in scenario_min.items():
        required = max(0, int(min_count or 0))
        while _selected_scenario_count(scenario) < required:
            best = _select_best(lambda item, s=scenario: review_scenario(item) == str(s).strip().lower())
            if best is None:
                break
            _append(best, reason=f"retained_by_constraint_scenario_{scenario}")
            remaining_pool = [item for item in remaining_pool if case_signature(item) != case_signature(best)]

    domain_min = dict(constraints.get("domain_min") or {})
    for domain, min_count in domain_min.items():
        required = max(0, int(min_count or 0))
        while _selected_domain_count(domain) < required:
            best = _select_best(lambda item, d=domain: review_domain(item) == str(d).strip().lower())
            if best is None:
                break
            _append(best, reason=f"retained_by_constraint_domain_{domain}")
            remaining_pool = [item for item in remaining_pool if case_signature(item) != case_signature(best)]

    target_min_count = max(1, int(constraints.get("target_min_count") or 1))
    while len(selected) < target_min_count:
        best = _select_best(lambda item: True)
        if best is None:
            break
        _append(best, reason="retained_by_constraint_target_min")
        remaining_pool = [item for item in remaining_pool if case_signature(item) != case_signature(best)]

    target_max_count = max(target_min_count, int(constraints.get("target_max_count") or target_min_count))
    if len(selected) > target_max_count:
        priority_min = {
            str(key).strip().upper(): max(0, int(value or 0))
            for key, value in dict(constraints.get("priority_min") or {}).items()
        }
        scenario_min = {
            str(key).strip().lower(): max(0, int(value or 0))
            for key, value in dict(constraints.get("scenario_min") or {}).items()
        }
        domain_min = {
            str(key).strip().lower(): max(0, int(value or 0))
            for key, value in dict(constraints.get("domain_min") or {}).items()
        }

        def _can_remove(case: dict[str, Any], current: list[dict[str, Any]]) -> bool:
            priority = case_priority(case)
            scenario = review_scenario(case)
            domain = review_domain(case)
            if priority in priority_min:
                count = sum(1 for item in current if case_priority(item) == priority)
                if count <= int(priority_min.get(priority) or 0):
                    return False
            if scenario in scenario_min:
                count = sum(1 for item in current if review_scenario(item) == scenario)
                if count <= int(scenario_min.get(scenario) or 0):
                    return False
            if domain in domain_min:
                count = sum(1 for item in current if review_domain(item) == domain)
                if count <= int(domain_min.get(domain) or 0):
                    return False
            return True

        removal_candidates = list(selected)
        removal_candidates.sort(
            key=lambda item: tuple(
                rank_case_fn(
                    item,
                    coverage_context=coverage_context,
                    rule_diagnostics=rule_diagnostics,
                )
            )
            + (review_case_id(item),)
        )

        for case in removal_candidates:
            if len(selected) <= target_max_count:
                break
            if not _can_remove(case, selected):
                continue
            signature = case_signature(case)
            selected = [item for item in selected if case_signature(item) != signature]
            if signature in selected_signature_set:
                selected_signature_set.remove(signature)
            case_id = review_case_id(case)
            if case_id and case_id in selected_case_id_set:
                selected_case_id_set.remove(case_id)
            if signature not in constraint_reasons:
                constraint_reasons[signature] = "dropped_by_target_max"

    return selected, constraint_reasons


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
