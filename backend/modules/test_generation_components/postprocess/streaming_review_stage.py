from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Iterator

from .streaming_case_keys import case_signature as _signature
from .streaming_postprocess_utils import (
    _cases_and_trace_from_result,
    _dict_case_items,
    _rule_diagnostics_payload,
)
from .streaming_review_llm_selection import run_review_llm_selection as _run_review_llm_selection
from .streaming_review_preflight import prepare_review_preflight as _prepare_review_preflight
from .streaming_review_reason_repair import (
    apply_reason_repair_for_dropped_cases as _apply_reason_repair_for_dropped_cases,
)
from .streaming_review_retry import default_review_llm_runtime_debug as _default_review_llm_runtime_debug
from .streaming_review_selection import (
    enforce_review_selection_constraints as _enforce_review_selection_constraints,
    merge_review_selection_candidates as _merge_review_selection_candidates,
    rank_review_case_for_fill as _rank_review_case_for_fill,
    recover_post_rerank_shortfall as _recover_post_rerank_shortfall,
    recover_review_selection_shortfall as _recover_review_selection_shortfall,
    resolve_review_llm_drop_reason_maps as _resolve_review_llm_drop_reason_maps,
    resolve_review_post_rerank_floor_count as _resolve_review_post_rerank_floor_count,
)
from .streaming_rule_rerank import rerank_and_cap_by_rule as _rerank_and_cap_by_rule


@dataclass(frozen=True)
class StreamingReviewStageResult:
    cases: list[dict[str, Any]]
    review_filter_stats: dict[str, Any]
    candidate_cases: list[dict[str, Any]]
    candidate_count_before_review: int
    review_candidate_cases: list[dict[str, Any]]
    review_selection_input: list[dict[str, Any]]
    review_gate_trace: dict[str, Any]
    review_candidate_coverage_context: dict[str, Any]
    review_candidate_rule_diagnostics: dict[str, Any]
    review_selected_count: int
    reference_count_effective: int
    review_llm_applied: bool
    review_llm_pool_count: int
    review_llm_selected_signatures: set[str]
    review_llm_omitted_signatures: set[str]
    review_constraint_retained_signatures: set[str]
    review_llm_drop_reason_raw_map: dict[str, str]
    review_llm_drop_reason_map: dict[str, str]
    review_llm_drop_reason_source_map: dict[str, str]
    review_llm_drop_reason_evidence_map: dict[str, Any]
    review_constraint_reason_map: dict[str, str]
    review_target_min_count: int
    review_target_max_count: int
    review_shortfall_detected: bool
    review_shortfall_before_count: int
    review_shortfall_recovered_count: int
    review_post_rerank_floor_count: int
    review_post_rerank_recovered_count: int
    review_fill_source: str
    review_must_keep_signatures: set[str]
    review_must_keep_reason_map: dict[str, list[str]]
    review_llm_runtime_debug: dict[str, Any]


def run_streaming_review_stage(
    *,
    client: Any,
    db: Any,
    requirement: str,
    parsed_result: list[dict[str, Any]],
    normalized_mode: str,
    append: bool,
    expected_count: int,
    existing_cases: list[dict[str, Any]],
    existing_unique_count: int,
    reference_count_effective: int,
    must_cover_rule_set: set[str],
    generation_coverage_profile: dict[str, Any],
    generation_coverage_mode: str,
    append_target_count: int,
    append_final_cap_count: int,
    current_biz_key: str,
    build_review_select_prompt_fn: Callable[..., str],
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    score_case_priority_fn: Callable[..., dict[str, Any]],
    hits_reuse_risk_fn: Callable[[dict[str, Any]], bool],
    hits_soft_constraint_fn: Callable[[dict[str, Any]], bool],
    is_cross_domain_noise_fn: Callable[[dict[str, Any]], bool] | None = None,
    record_timing_event_fn: Callable[..., dict[str, Any]],
) -> Iterator[str]:
    review_candidate_cases: list[dict[str, Any]] = []
    review_selection_input: list[dict[str, Any]] = []
    review_gate_trace: dict[str, Any] = {}
    review_llm_applied = False
    review_llm_pool_count = 0
    review_llm_selected_signatures: set[str] = set()
    review_llm_omitted_signatures: set[str] = set()
    review_constraint_retained_signatures: set[str] = set()
    review_llm_drop_reason_raw_map: dict[str, str] = {}
    review_llm_drop_reason_map: dict[str, str] = {}
    review_llm_drop_reason_source_map: dict[str, str] = {}
    review_llm_drop_reason_evidence_map: dict[str, Any] = {}
    review_constraint_reason_map: dict[str, str] = {}
    review_target_min_count = 1
    review_target_max_count = 1
    review_candidate_coverage_context: dict[str, Any] = {}
    review_candidate_rule_diagnostics: dict[str, Any] = {"rule_diagnostics": []}
    review_shortfall_detected = False
    review_shortfall_before_count = 0
    review_shortfall_recovered_count = 0
    review_post_rerank_floor_count = 1
    review_post_rerank_recovered_count = 0
    review_fill_source = "none"
    review_must_keep_signatures: set[str] = set()
    review_must_keep_reason_map: dict[str, list[str]] = {}
    review_llm_runtime_debug: dict[str, Any] = _default_review_llm_runtime_debug()
    review_filter_stats: dict[str, Any] = {}
    candidate_cases = _dict_case_items(parsed_result)

    if normalized_mode in {"multi_pass", "biz_key_multi_pass"} and isinstance(parsed_result, list):
        review_started = time.perf_counter()
        yield "@@STATUS@@:[multi-pass] Stage 3/3 review selection started...\n"

        review_preflight = _prepare_review_preflight(
            requirement=requirement,
            parsed_result=_dict_case_items(parsed_result),
            must_cover_rule_set=must_cover_rule_set,
            reference_count_effective=reference_count_effective,
            generation_coverage_profile=generation_coverage_profile,
            append_target_count=append_target_count,
            append_final_cap_count=append_final_cap_count,
            analyze_coverage_fn=analyze_coverage_fn,
            score_case_priority_fn=score_case_priority_fn,
        )
        candidate_cases = review_preflight.candidate_cases
        review_filter_stats = review_preflight.review_filter_stats
        candidate_count_before_review = review_preflight.candidate_count_before_review
        review_candidate_cases = review_preflight.review_candidate_cases
        review_candidate_coverage_context = review_preflight.review_candidate_coverage_context
        review_candidate_rule_diagnostics = review_preflight.review_candidate_rule_diagnostics
        must_keep_cases = review_preflight.must_keep_cases
        llm_pool_cases = review_preflight.llm_pool_cases
        review_must_keep_signatures = review_preflight.review_must_keep_signatures
        review_must_keep_reason_map = review_preflight.review_must_keep_reason_map
        review_llm_pool_count = review_preflight.review_llm_pool_count
        review_constraints = review_preflight.review_constraints
        review_target_min_count = review_preflight.review_target_min_count
        review_target_max_count = review_preflight.review_target_max_count
        selected_from_llm_pool: list[dict[str, Any]] = review_preflight.selected_from_llm_pool
        skip_review_llm_by_noop = review_preflight.skip_review_llm_by_noop
        review_llm_runtime_debug.update(review_preflight.runtime_debug_fields)

        review_llm_result = _run_review_llm_selection(
            client=client,
            db=db,
            requirement=requirement,
            llm_pool_cases=_dict_case_items(llm_pool_cases),
            selected_from_llm_pool=_dict_case_items(selected_from_llm_pool),
            skip_review_llm_by_noop=bool(skip_review_llm_by_noop),
            review_llm_runtime_debug=review_llm_runtime_debug,
            reference_count_effective=reference_count_effective,
            review_target_min_count=review_target_min_count,
            review_target_max_count=review_target_max_count,
            review_constraints=review_constraints,
            current_biz_key=current_biz_key,
            build_review_select_prompt_fn=build_review_select_prompt_fn,
            clean_and_parse_json_fn=clean_and_parse_json_fn,
            normalize_json_structure_fn=normalize_json_structure_fn,
        )
        selected_from_llm_pool = review_llm_result.selected_from_llm_pool
        review_llm_selected_signatures = review_llm_result.selected_signatures
        review_llm_drop_reason_raw_map = review_llm_result.drop_reason_raw_map
        review_llm_drop_reason_raw_origin_map = review_llm_result.drop_reason_raw_origin_map
        review_llm_runtime_debug = review_llm_result.runtime_debug
        review_llm_applied = review_llm_result.applied

        selected_from_llm_pool, constraint_reason_map = _enforce_review_selection_constraints(
            selected_cases=_dict_case_items(selected_from_llm_pool),
            pool_cases=_dict_case_items(llm_pool_cases),
            constraints=review_constraints,
            coverage_context=review_candidate_coverage_context,
            rule_diagnostics=review_candidate_rule_diagnostics,
            rank_case_fn=_rank_review_case_for_fill,
        )
        review_constraint_reason_map = dict(constraint_reason_map or {})
        selected_signature_after_constraints = {
            _signature(item) for item in selected_from_llm_pool if isinstance(item, dict)
        }
        review_llm_drop_reason_raw_map, review_llm_drop_reason_raw_origin_map, review_llm_runtime_debug = (
            _apply_reason_repair_for_dropped_cases(
                client=client,
                db=db,
                llm_pool_cases=_dict_case_items(llm_pool_cases),
                selected_from_llm_pool=_dict_case_items(selected_from_llm_pool),
                review_llm_applied=bool(review_llm_applied),
                review_llm_drop_reason_raw_map=review_llm_drop_reason_raw_map,
                review_llm_drop_reason_raw_origin_map=review_llm_drop_reason_raw_origin_map,
                review_llm_runtime_debug=review_llm_runtime_debug,
                parse_json_fn=clean_and_parse_json_fn,
                max_candidates=80,
            )
        )
        review_llm_drop_reason_map, review_llm_drop_reason_source_map, review_llm_drop_reason_evidence_map = (
            _resolve_review_llm_drop_reason_maps(
                pool_cases=_dict_case_items(llm_pool_cases),
                selected_cases=_dict_case_items(selected_from_llm_pool),
                raw_drop_reason_map=review_llm_drop_reason_raw_map,
                raw_drop_reason_origin_map=review_llm_drop_reason_raw_origin_map,
                coverage_context=review_candidate_coverage_context,
                rule_diagnostics=review_candidate_rule_diagnostics,
            )
        )
        review_llm_omitted_signatures = (
            set(review_llm_drop_reason_map.keys()) if review_llm_applied else set()
        )
        review_constraint_retained_signatures = {
            signature
            for signature, reason in review_constraint_reason_map.items()
            if signature in selected_signature_after_constraints and str(reason or "").startswith("retained_by_constraint_")
        }
        if review_llm_applied:
            selected_pool_signatures = {
                _signature(item) for item in selected_from_llm_pool if isinstance(item, dict)
            }
            review_llm_selected_signatures = {
                signature
                for signature in review_llm_selected_signatures
                if signature and signature in selected_pool_signatures
            }

        selection_input = _merge_review_selection_candidates(
            must_keep_cases,
            selected_from_llm_pool,
            signature_fn=_signature,
        )

        review_shortfall_before_count = int(len(selection_input))
        if int(review_target_min_count or 1) > 0 and int(len(selection_input)) < int(review_target_min_count or 1):
            review_shortfall_detected = True
            review_fill_source = "constraint_fill"
            selection_input, review_constraint_reason_map, review_shortfall_recovered_count = (
                _recover_review_selection_shortfall(
                    selection_input=selection_input,
                    candidate_cases=candidate_cases,
                    target_min_count=review_target_min_count,
                    constraint_reason_map=review_constraint_reason_map,
                    domain_guard_active=bool(review_constraints.get("domain_guard_active")),
                    cross_domain_noise_fn=is_cross_domain_noise_fn,
                    coverage_context=review_candidate_coverage_context,
                    rule_diagnostics=review_candidate_rule_diagnostics,
                    rank_case_fn=_rank_review_case_for_fill,
                )
            )
        else:
            review_shortfall_before_count = int(len(selection_input))

        review_selection_input = _dict_case_items(selection_input)
        review_selection_coverage = analyze_coverage_fn(requirement, review_selection_input)
        rerank_result = _rerank_and_cap_by_rule(
            review_selection_input,
            expected_count=expected_count,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            hits_reuse_risk_fn=hits_reuse_risk_fn,
            hits_soft_constraint_fn=hits_soft_constraint_fn,
            max_per_rule=3,
            include_trace=True,
            coverage_context=review_selection_coverage,
            rule_diagnostics=_rule_diagnostics_payload(review_selection_coverage),
            generation_profile=generation_coverage_profile,
        )
        parsed_cases, review_gate_trace = _cases_and_trace_from_result(rerank_result)
        if not parsed_cases and candidate_cases:
            fallback_coverage = analyze_coverage_fn(requirement, candidate_cases)
            fallback_result = _rerank_and_cap_by_rule(
                candidate_cases,
                expected_count=expected_count,
                deduplicate_test_cases_fn=deduplicate_test_cases_fn,
                hits_reuse_risk_fn=hits_reuse_risk_fn,
                hits_soft_constraint_fn=hits_soft_constraint_fn,
                max_per_rule=3,
                include_trace=True,
                coverage_context=fallback_coverage,
                rule_diagnostics=_rule_diagnostics_payload(fallback_coverage),
                generation_profile=generation_coverage_profile,
            )
            parsed_cases, review_gate_trace = _cases_and_trace_from_result(fallback_result)
            review_selection_input = list(candidate_cases)
            review_llm_applied = False
            review_llm_selected_signatures = set()
            review_constraint_retained_signatures = set()
            review_llm_drop_reason_raw_map = {}
            review_llm_drop_reason_map = {}
            review_llm_drop_reason_source_map = {}
            review_llm_drop_reason_evidence_map = {}
            review_llm_omitted_signatures = set()
            review_constraint_reason_map = {}
            review_llm_runtime_debug["forced_reset_by_fallback"] = True
            review_llm_runtime_debug["final_source"] = "review_selector"
            review_llm_runtime_debug["applied"] = False
            review_llm_runtime_debug["applied_reason"] = "forced_reset_by_empty_rerank_result"
            review_llm_runtime_debug["fallback_reason_incomplete"] = False

        review_selected_count = len(parsed_cases)
        record_timing_event_fn(
            "review_selection",
            review_started,
            candidate_count=int(candidate_count_before_review),
            llm_pool_count=int(review_llm_pool_count),
            selected_count=int(review_selected_count),
            llm_invoked=bool(review_llm_runtime_debug.get("invoked")),
            llm_applied=bool(review_llm_applied),
            llm_skip_reason=str(review_llm_runtime_debug.get("skip_reason") or ""),
        )
    else:
        review_started = time.perf_counter()
        if append and isinstance(existing_cases, list):
            reference_count_effective = max(1, int(expected_count or 1) - int(existing_unique_count or 0))
        candidate_cases = _dict_case_items(parsed_result)
        candidate_count_before_review = len(candidate_cases)
        review_candidate_cases = list(candidate_cases)
        review_selection_input = list(candidate_cases)
        review_candidate_coverage = analyze_coverage_fn(requirement, candidate_cases)
        review_candidate_coverage_context = review_candidate_coverage
        review_candidate_rule_diagnostics = _rule_diagnostics_payload(review_candidate_coverage_context)
        rerank_result = _rerank_and_cap_by_rule(
            candidate_cases,
            expected_count=expected_count,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            hits_reuse_risk_fn=hits_reuse_risk_fn,
            hits_soft_constraint_fn=hits_soft_constraint_fn,
            max_per_rule=3,
            include_trace=True,
            coverage_context=review_candidate_coverage,
            rule_diagnostics=_rule_diagnostics_payload(review_candidate_coverage),
            generation_profile=generation_coverage_profile,
        )
        parsed_cases, review_gate_trace = _cases_and_trace_from_result(rerank_result)
        review_selected_count = len(parsed_cases)
        record_timing_event_fn(
            "review_selection",
            review_started,
            candidate_count=int(candidate_count_before_review),
            llm_pool_count=0,
            selected_count=int(review_selected_count),
            llm_invoked=False,
            llm_applied=False,
        )

    review_post_rerank_floor_count = _resolve_review_post_rerank_floor_count(
        candidate_count_before_review=candidate_count_before_review,
        reference_count_effective=reference_count_effective,
        generation_coverage_mode=generation_coverage_mode,
    )

    if int(len(parsed_cases)) < int(review_post_rerank_floor_count or 1):
        review_shortfall_detected = True
        parsed_cases, review_post_rerank_recovered_count = (
            _recover_post_rerank_shortfall(
                parsed_cases=parsed_cases,
                review_selection_input=review_selection_input,
                candidate_cases=candidate_cases,
                floor_count=review_post_rerank_floor_count,
                coverage_context=review_candidate_coverage_context,
                rule_diagnostics=review_candidate_rule_diagnostics,
                rank_case_fn=_rank_review_case_for_fill,
            )
        )
        if review_post_rerank_recovered_count > 0:
            review_fill_source = (
                "post_rerank_recovery"
                if str(review_fill_source or "none") in {"", "none"}
                else f"{review_fill_source}+post_rerank_recovery"
            )

    return StreamingReviewStageResult(
        cases=_dict_case_items(parsed_cases),
        review_filter_stats=dict(review_filter_stats or {}),
        candidate_cases=_dict_case_items(candidate_cases),
        candidate_count_before_review=int(candidate_count_before_review or 0),
        review_candidate_cases=_dict_case_items(review_candidate_cases),
        review_selection_input=_dict_case_items(review_selection_input),
        review_gate_trace=dict(review_gate_trace or {}),
        review_candidate_coverage_context=dict(review_candidate_coverage_context or {}),
        review_candidate_rule_diagnostics=dict(review_candidate_rule_diagnostics or {"rule_diagnostics": []}),
        review_selected_count=int(review_selected_count or 0),
        reference_count_effective=int(reference_count_effective or 0),
        review_llm_applied=bool(review_llm_applied),
        review_llm_pool_count=int(review_llm_pool_count or 0),
        review_llm_selected_signatures=set(review_llm_selected_signatures),
        review_llm_omitted_signatures=set(review_llm_omitted_signatures),
        review_constraint_retained_signatures=set(review_constraint_retained_signatures),
        review_llm_drop_reason_raw_map=dict(review_llm_drop_reason_raw_map),
        review_llm_drop_reason_map=dict(review_llm_drop_reason_map),
        review_llm_drop_reason_source_map=dict(review_llm_drop_reason_source_map),
        review_llm_drop_reason_evidence_map=dict(review_llm_drop_reason_evidence_map),
        review_constraint_reason_map=dict(review_constraint_reason_map),
        review_target_min_count=int(review_target_min_count or 1),
        review_target_max_count=int(review_target_max_count or 1),
        review_shortfall_detected=bool(review_shortfall_detected),
        review_shortfall_before_count=int(review_shortfall_before_count or 0),
        review_shortfall_recovered_count=int(review_shortfall_recovered_count or 0),
        review_post_rerank_floor_count=int(review_post_rerank_floor_count or 1),
        review_post_rerank_recovered_count=int(review_post_rerank_recovered_count or 0),
        review_fill_source=str(review_fill_source or "none"),
        review_must_keep_signatures=set(review_must_keep_signatures),
        review_must_keep_reason_map=dict(review_must_keep_reason_map or {}),
        review_llm_runtime_debug=dict(review_llm_runtime_debug or {}),
    )


__all__ = [
    "StreamingReviewStageResult",
    "run_streaming_review_stage",
]
