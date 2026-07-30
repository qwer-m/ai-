from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Iterator

from .streaming_case_keys import case_signature as _signature
from .streaming_case_keys import candidate_identity_key as _candidate_identity_key
from .streaming_execution_plan_metadata import retain_required_stage_assignment
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
    rank_review_case_for_fill as _rank_review_case_for_fill,
    resolve_review_llm_drop_reason_maps as _resolve_review_llm_drop_reason_maps,
)
from .streaming_global_review_selection import (
    finalize_global_review_selection as _finalize_global_review_selection,
)


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
    review_contract_context: dict[str, Any],
    append_target_count: int,
    append_final_cap_count: int,
    current_biz_key: str,
    build_review_select_prompt_fn: Callable[..., str],
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    score_case_priority_fn: Callable[..., dict[str, Any]],
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
    review_llm_runtime_debug: dict[str, Any] = _default_review_llm_runtime_debug()
    review_filter_stats: dict[str, Any] = {}
    candidate_cases = _dict_case_items(parsed_result)

    if normalized_mode in {"multi_pass", "biz_key_multi_pass"} and isinstance(parsed_result, list):
        review_started = time.perf_counter()
        yield "@@STATUS@@:[multi-pass] Stage 3/3 review selection started...\n"

        review_profile = dict(generation_coverage_profile or {})
        if not append and int(expected_count or 0) > 0:
            review_profile["explicit_expected_count_floor"] = int(
                reference_count_effective or expected_count
            )
        review_preflight = _prepare_review_preflight(
            requirement=requirement,
            parsed_result=_dict_case_items(parsed_result),
            reference_count_effective=reference_count_effective,
            generation_coverage_profile=review_profile,
            append_target_count=append_target_count,
            append_final_cap_count=append_final_cap_count,
            analyze_coverage_fn=analyze_coverage_fn,
        )
        candidate_cases = review_preflight.candidate_cases
        review_filter_stats = review_preflight.review_filter_stats
        candidate_count_before_review = review_preflight.candidate_count_before_review
        review_candidate_cases = review_preflight.review_candidate_cases
        review_candidate_coverage_context = review_preflight.review_candidate_coverage_context
        review_candidate_rule_diagnostics = review_preflight.review_candidate_rule_diagnostics
        llm_pool_cases = review_preflight.llm_pool_cases
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
            review_contract_context=review_contract_context,
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

        review_constraint_reason_map = {}
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
            )
        )
        pool_signatures = {
            _signature(item)
            for item in _dict_case_items(llm_pool_cases)
            if _signature(item)
        }
        selected_signatures_after_review = {
            _signature(item)
            for item in _dict_case_items(selected_from_llm_pool)
            if _signature(item)
        }
        accounted_signatures = selected_signatures_after_review | set(
            review_llm_drop_reason_raw_map.keys()
        )
        unaccounted_signatures = sorted(pool_signatures - accounted_signatures)
        debug_text = " ".join(
            str(review_llm_runtime_debug.get(key) or "").lower()
            for key in (
                "primary_invalid_reason",
                "primary_contract_retry_invalid_reason",
                "applied_reason",
                "exception",
            )
        )
        review_input_overflow = any(
            signal in debug_text
            for signal in (
                "context_length",
                "context length",
                "maximum context",
                "too many tokens",
                "token limit",
                "input overflow",
            )
        )
        global_review_complete = bool(
            review_llm_applied
            and not review_input_overflow
            and not unaccounted_signatures
        )
        if review_llm_applied and not global_review_complete:
            # 局部响应不能代表全局评审；契约不完整时保留全量候选。
            selected_from_llm_pool = _dict_case_items(llm_pool_cases)
            review_llm_selected_signatures = set(pool_signatures)
            review_llm_drop_reason_raw_map = {}
            review_llm_drop_reason_raw_origin_map = {}
            review_llm_applied = False
        review_llm_runtime_debug["review_candidate_count"] = int(len(llm_pool_cases))
        review_llm_runtime_debug["review_input_overflow"] = bool(review_input_overflow)
        review_llm_runtime_debug["global_review_complete"] = bool(global_review_complete)
        review_llm_runtime_debug["global_review_unaccounted_count"] = int(
            len(unaccounted_signatures)
        )
        review_llm_runtime_debug["global_review_unaccounted_case_signatures"] = (
            unaccounted_signatures[:20]
        )
        review_llm_runtime_debug["global_review_incomplete_reason"] = (
            "review_input_overflow"
            if review_input_overflow
            else "review_response_did_not_cover_all_candidates"
            if unaccounted_signatures
            else str(review_llm_runtime_debug.get("applied_reason") or "review_unavailable")
            if not review_llm_applied
            else ""
        )
        review_llm_runtime_debug["full_candidate_set_preserved"] = bool(
            not review_llm_applied
        )
        required_stage_retention: dict[str, Any] = {}
        restored_signatures: set[str] = set()
        replaced_signatures: set[str] = set()
        if review_llm_applied:
            selected_from_llm_pool, required_stage_retention = retain_required_stage_assignment(
                _dict_case_items(llm_pool_cases),
                _dict_case_items(selected_from_llm_pool),
                workflow_blueprints=list(
                    review_contract_context.get("workflow_blueprints") or []
                ),
                target_max_count=int(review_target_max_count or 0),
                require_complete_source=True,
                removal_rank_fn=lambda item: _rank_review_case_for_fill(
                    item,
                    coverage_context=review_candidate_coverage_context,
                    rule_diagnostics=review_candidate_rule_diagnostics,
                ),
            )
            source_by_key = {
                _candidate_identity_key(item): item
                for item in _dict_case_items(llm_pool_cases)
                if _candidate_identity_key(item)
            }
            restored_signatures = {
                _signature(source_by_key[key])
                for key in (
                    required_stage_retention.get("restored_candidate_keys") or []
                )
                if key in source_by_key and _signature(source_by_key[key])
            }
            replaced_signatures = {
                _signature(source_by_key[key])
                for key in (
                    required_stage_retention.get("replaced_candidate_keys") or []
                )
                if key in source_by_key and _signature(source_by_key[key])
            }
            review_constraint_retained_signatures = set(restored_signatures)
            review_constraint_reason_map.update(
                {
                    signature: "required_workflow_stage_assignment"
                    for signature in restored_signatures
                }
            )
            for signature in restored_signatures:
                review_llm_drop_reason_raw_map.pop(signature, None)
                review_llm_drop_reason_raw_origin_map.pop(signature, None)
            for signature in replaced_signatures:
                review_llm_drop_reason_raw_map[signature] = "selection_tradeoff_omitted"
                review_llm_drop_reason_raw_origin_map[signature] = (
                    "required_stage_assignment_constraint"
                )
        review_llm_runtime_debug["required_stage_review_retention"] = dict(
            required_stage_retention or {}
        )
        explicit_floor_backfilled_signatures: set[str] = set()
        explicit_floor_shortfall = max(
            0,
            int(review_target_min_count or 0) - len(selected_from_llm_pool),
        )
        if review_llm_applied and explicit_floor_shortfall > 0:
            selected_candidate_keys = {
                _candidate_identity_key(item)
                for item in _dict_case_items(selected_from_llm_pool)
                if _candidate_identity_key(item)
            }
            fill_candidates = [
                dict(item)
                for item in _dict_case_items(llm_pool_cases)
                if _candidate_identity_key(item) not in selected_candidate_keys
            ]
            fill_candidates.sort(
                key=lambda item: tuple(
                    -value
                    for value in _rank_review_case_for_fill(
                        item,
                        coverage_context=review_candidate_coverage_context,
                        rule_diagnostics=review_candidate_rule_diagnostics,
                    )
                )
                + (_candidate_identity_key(item),)
            )
            floor_backfill_cases = fill_candidates[:explicit_floor_shortfall]
            selected_from_llm_pool.extend(floor_backfill_cases)
            explicit_floor_backfilled_signatures = {
                _signature(item)
                for item in floor_backfill_cases
                if _signature(item)
            }
            review_constraint_retained_signatures.update(
                explicit_floor_backfilled_signatures
            )
            review_constraint_reason_map.update(
                {
                    signature: "explicit_expected_count_floor"
                    for signature in explicit_floor_backfilled_signatures
                }
            )
            for signature in explicit_floor_backfilled_signatures:
                review_llm_drop_reason_raw_map.pop(signature, None)
                review_llm_drop_reason_raw_origin_map.pop(signature, None)
        review_llm_runtime_debug["explicit_floor_backfill_count"] = int(
            len(explicit_floor_backfilled_signatures)
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
        for signature in replaced_signatures:
            review_llm_drop_reason_source_map[signature] = (
                "required_stage_assignment_constraint"
            )
            review_llm_drop_reason_evidence_map[signature] = {
                **dict(review_llm_drop_reason_evidence_map.get(signature) or {}),
                "reason_from": "required_stage_assignment_constraint",
            }
        review_llm_omitted_signatures = (
            set(review_llm_drop_reason_map.keys()) if review_llm_applied else set()
        )
        if review_llm_applied:
            selected_pool_signatures = {
                _signature(item) for item in selected_from_llm_pool if isinstance(item, dict)
            }
            review_llm_selected_signatures = {
                signature
                for signature in review_llm_selected_signatures
                if signature and signature in selected_pool_signatures
            }

        selection_input = _dict_case_items(selected_from_llm_pool)

        review_shortfall_before_count = int(len(selection_input))
        review_shortfall_detected = bool(
            int(review_target_min_count or 1) > 0
            and int(len(selection_input)) < int(review_target_min_count or 1)
        )

        review_selection_input = _dict_case_items(selection_input)
        review_selection_coverage = analyze_coverage_fn(requirement, review_selection_input)
        rerank_result = _finalize_global_review_selection(
            review_selection_input,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            include_trace=True,
        )
        parsed_cases, review_gate_trace = _cases_and_trace_from_result(rerank_result)

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
        rerank_result = _finalize_global_review_selection(
            candidate_cases,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            include_trace=True,
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
        review_llm_runtime_debug=dict(review_llm_runtime_debug or {}),
    )


__all__ = [
    "StreamingReviewStageResult",
    "run_streaming_review_stage",
]
