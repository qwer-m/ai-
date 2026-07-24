from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Generator

from .result_postprocess_priority_semantics import apply_priority_semantics_to_cases
from .streaming_case_quality import filter_low_quality_cases_with_stats
from .streaming_coverage_gap import diagnose_candidate_set_coverage_gain
from .streaming_execution_plan_metadata import (
    evaluate_required_stage_candidate_coverage,
)
from .module_contract import enforce_functional_module_contract
from .streaming_postprocess_utils import _dict_case_count, _dict_case_items


@dataclass(frozen=True)
class GapSupplementRequest:
    supplement_source: list[dict[str, Any]]
    system_prompt: str


@dataclass(frozen=True)
class GapSupplementParseResult:
    cases: list[dict[str, Any]]
    parsed_case_count: int
    filter_stats: dict[str, Any]


@dataclass(frozen=True)
class GapSupplementRunResult:
    cases: list[dict[str, Any]]
    coverage_primary: dict[str, Any]
    coverage_gap_state: dict[str, Any]
    attempt_count: int
    remaining_gap_count: int
    added_count: int
    stopped_by_provider_error: bool
    stopped_by_no_gain: bool
    stop_reason: str
    filter_stats: list[dict[str, Any]]
    required_stage_coverage: dict[str, Any]
    module_contract_normalized_count: int
    module_contract_rejected_count: int


def build_gap_supplement_request(
    *,
    requirement: str,
    append: bool,
    existing_cases: list[dict[str, Any]],
    parsed_result: list[dict[str, Any]],
    coverage_primary: dict[str, Any],
    missing_rules: list[str],
    missing_workflow_stages: list[dict[str, Any]] | None = None,
    current_biz_key: str,
    review_contract_context: dict[str, Any] | None = None,
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
    build_supplement_closed_loop_instruction_fn: Callable[..., str],
    build_gap_fill_prompt_fn: Callable[..., str],
) -> GapSupplementRequest:
    supplement_source: list[dict[str, Any]] = []
    if append and isinstance(existing_cases, list):
        supplement_source.extend(_dict_case_items(existing_cases))
    supplement_source.extend(_dict_case_items(parsed_result))

    closed_loop_instruction = build_supplement_closed_loop_instruction_fn(
        all_cases=supplement_source,
        requirement=requirement,
        infer_case_kind_fn=infer_case_kind_fn,
    )
    gap_prompt = build_gap_fill_prompt_fn(
        requirement_context=requirement,
        existing_cases=supplement_source,
        coverage_result=coverage_primary,
        missing_rules=missing_rules,
        missing_workflow_stages=list(missing_workflow_stages or []),
        current_biz_key=current_biz_key,
        review_contract_context=dict(review_contract_context or {}),
        pretty_json=False,
    )
    return GapSupplementRequest(
        supplement_source=supplement_source,
        system_prompt=f"""
{gap_prompt}

CLOSED_LOOP_HINT:
{closed_loop_instruction}

CANDIDATE_POLICY: return every contract-valid gap candidate. Do not locally select or discard
candidates by per-case coverage gain; the unified Review stage makes the global selection.
""",
    )


def parse_gap_supplement_cases(
    *,
    extra_content: str,
    requirement: str,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
) -> GapSupplementParseResult | None:
    extra_parsed = clean_and_parse_json_fn(extra_content)
    extra_parsed = normalize_json_structure_fn(extra_parsed)
    if not isinstance(extra_parsed, list) or not extra_parsed:
        return None

    parsed_case_count = int(len(extra_parsed))
    extra_cases = deduplicate_test_cases_fn(extra_parsed)
    extra_cases = apply_priority_semantics_to_cases(
        _dict_case_items(extra_cases),
        attach_debug=False,
    )
    extra_cases, extra_filter_stats = filter_low_quality_cases_with_stats(
        extra_cases,
        requirement_text=requirement,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    return GapSupplementParseResult(
        cases=extra_cases,
        parsed_case_count=parsed_case_count,
        filter_stats=extra_filter_stats,
    )


def run_gap_supplement_attempts(
    *,
    client: Any,
    requirement: str,
    append: bool,
    existing_cases: list[dict[str, Any]],
    parsed_result: list[dict[str, Any]],
    coverage_primary: dict[str, Any],
    coverage_gap_state: dict[str, Any],
    current_biz_key: str,
    review_contract_context: dict[str, Any] | None = None,
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
    build_supplement_closed_loop_instruction_fn: Callable[..., str],
    build_gap_fill_prompt_fn: Callable[..., str],
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    resolve_coverage_gap_state_fn: Callable[[dict[str, Any]], dict[str, Any]],
    record_timing_event_fn: Callable[..., dict[str, Any]],
    workflow_blueprints: list[dict[str, Any]] | None = None,
    project_profile: dict[str, Any] | None = None,
    include_generic_gaps: bool = True,
) -> Generator[str, None, GapSupplementRunResult]:
    gap_started = time.perf_counter()
    yield (
        "@@STATUS@@:[multi-pass] Stage 2/3 gap supplement started...\n"
        if include_generic_gaps
        else "@@STATUS@@:Required workflow stage source repair started...\n"
    )
    before_gap = len(parsed_result)
    supplement_attempt = 0
    gap_no_gain_streak = 0
    stopped_by_no_gain = False
    stopped_by_provider_error = False
    gap_stop_reason = ""
    filter_stats: list[dict[str, Any]] = []
    module_contract_normalized_count = 0
    module_contract_rejected_count = 0
    missing_rules = (
        list(coverage_gap_state.get("missing_rules") or [])
        if include_generic_gaps
        else []
    )
    has_missing_types = bool(
        include_generic_gaps and coverage_gap_state.get("has_missing_types")
    )
    required_stage_coverage = evaluate_required_stage_candidate_coverage(
        parsed_result,
        workflow_blueprints=workflow_blueprints,
    )
    missing_workflow_stages = list(
        required_stage_coverage.get("missing_required_stages") or []
    )

    while supplement_attempt < 3 and (
        missing_rules or has_missing_types or missing_workflow_stages
    ):
        supplement_attempt += 1
        gap_attempt_started = time.perf_counter()
        before_attempt_count = _dict_case_count(parsed_result)
        generic_gap_before = (
            int(coverage_gap_state["gap_count"]) if include_generic_gaps else 0
        )
        stage_gap_before = int(len(missing_workflow_stages))
        gap_remaining_before = generic_gap_before + stage_gap_before
        yield f"@@STATUS@@:Gap supplement attempt #{supplement_attempt}...\n"

        gap_request = build_gap_supplement_request(
            requirement=requirement,
            append=append,
            existing_cases=existing_cases,
            parsed_result=parsed_result,
            coverage_primary=(
                coverage_primary
                if include_generic_gaps
                else {"missing_rules": [], "rule_diagnostics": []}
            ),
            missing_rules=missing_rules,
            missing_workflow_stages=missing_workflow_stages,
            current_biz_key=current_biz_key,
            review_contract_context=review_contract_context,
            infer_case_kind_fn=infer_case_kind_fn,
            build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction_fn,
            build_gap_fill_prompt_fn=build_gap_fill_prompt_fn,
        )

        extra_content = ""
        extra_stream = client.generate_response_stream(
            requirement,
            gap_request.system_prompt,
            task_type="generation",
        )
        provider_error = None
        for chunk in extra_stream:
            extra_content += chunk
            if (
                chunk.startswith("Error:")
                or chunk.startswith("[妫版繂瀹抽懓妤€鏁朷")
                or chunk.startswith("Exception occurred:")
            ):
                provider_error = chunk
                break
        if provider_error:
            record_timing_event_fn(
                "gap_supplement_attempt",
                gap_attempt_started,
                attempt=int(supplement_attempt),
                response_chars=int(len(extra_content or "")),
                provider_error=str(provider_error or "")[:200],
                gap_remaining_before=int(gap_remaining_before),
                generic_gap_before=int(generic_gap_before),
                stage_gap_before=int(stage_gap_before),
                attempt_status="provider_error",
                stop_reason="provider_error",
            )
            yield "\n@@STATUS@@:Gap supplement generation failed.\n"
            stopped_by_provider_error = True
            gap_stop_reason = "provider_error"
            break

        parsed_extra_count = 0
        attempt_coverage_diagnostics: dict[str, Any] = {}
        try:
            gap_parse_result = parse_gap_supplement_cases(
                extra_content=extra_content,
                requirement=requirement,
                clean_and_parse_json_fn=clean_and_parse_json_fn,
                normalize_json_structure_fn=normalize_json_structure_fn,
                deduplicate_test_cases_fn=deduplicate_test_cases_fn,
                analyze_coverage_fn=analyze_coverage_fn,
            )
            if gap_parse_result is not None:
                parsed_extra_count = int(gap_parse_result.parsed_case_count)
                candidate_cases = _dict_case_items(gap_parse_result.cases)
                try:
                    coverage_gain_stats = diagnose_candidate_set_coverage_gain(
                        requirement=requirement,
                        base_cases=parsed_result,
                        candidate_cases=candidate_cases,
                        analyze_coverage_fn=analyze_coverage_fn,
                        resolve_coverage_gap_state_fn=resolve_coverage_gap_state_fn,
                    )
                except Exception as coverage_diagnostic_error:
                    coverage_gain_stats = {
                        "coverage_gain_candidate_count": int(len(candidate_cases)),
                        "coverage_gain_forwarded_count": int(len(candidate_cases)),
                        "coverage_gain_kept_count": int(len(candidate_cases)),
                        "coverage_gain_dropped_count": 0,
                        "coverage_gain_diagnostic_error": str(coverage_diagnostic_error)[:200],
                    }
                attempt_coverage_diagnostics = dict(coverage_gain_stats)
                filter_stats.append({**dict(gap_parse_result.filter_stats or {}), **coverage_gain_stats})
                parsed_result.extend(candidate_cases)
                parsed_result = normalize_json_structure_fn(parsed_result)
                parsed_result = deduplicate_test_cases_fn(parsed_result)
                parsed_result, module_contract_summary = enforce_functional_module_contract(
                    _dict_case_items(parsed_result),
                    project_profile=project_profile,
                )
                module_contract_normalized_count += int(
                    module_contract_summary.get("normalized_count") or 0
                )
                module_contract_rejected_count += int(
                    module_contract_summary.get("rejected_count") or 0
                )
                filter_stats[-1]["functional_module_contract"] = dict(
                    module_contract_summary or {}
                )
        except Exception:
            pass
        after_attempt_count = _dict_case_count(parsed_result)
        effective_added_count = max(0, int(after_attempt_count) - int(before_attempt_count))
        coverage_primary = analyze_coverage_fn(requirement, parsed_result)
        coverage_gap_state = resolve_coverage_gap_state_fn(coverage_primary)
        missing_rules = (
            list(coverage_gap_state["missing_rules"])
            if include_generic_gaps
            else []
        )
        has_missing_types = bool(
            include_generic_gaps and coverage_gap_state["has_missing_types"]
        )
        required_stage_coverage = evaluate_required_stage_candidate_coverage(
            parsed_result,
            workflow_blueprints=workflow_blueprints,
        )
        missing_workflow_stages = list(
            required_stage_coverage.get("missing_required_stages") or []
        )
        generic_gap_after = (
            int(coverage_gap_state["gap_count"]) if include_generic_gaps else 0
        )
        stage_gap_after = int(len(missing_workflow_stages))
        gap_remaining_after = generic_gap_after + stage_gap_after
        required_stage_gap_reduction = max(
            0,
            stage_gap_before - stage_gap_after,
        )
        combined_gap_reduction = max(
            0,
            int(gap_remaining_before) - int(gap_remaining_after),
        )
        gap_gain_detected = bool(
            required_stage_gap_reduction > 0
            or generic_gap_after < generic_gap_before
        )
        if not gap_gain_detected:
            gap_no_gain_streak += 1
        else:
            gap_no_gain_streak = 0
        attempt_stop_reason = ""
        if not missing_rules and not has_missing_types and not missing_workflow_stages:
            attempt_stop_reason = "coverage_converged"
            gap_stop_reason = attempt_stop_reason
        elif gap_no_gain_streak >= 2:
            attempt_stop_reason = "no_gain_streak"
            gap_stop_reason = attempt_stop_reason
        record_timing_event_fn(
            "gap_supplement_attempt",
            gap_attempt_started,
            attempt=int(supplement_attempt),
            response_chars=int(len(extra_content or "")),
            parsed_case_count=int(parsed_extra_count),
            effective_added_count=int(effective_added_count),
            gap_remaining_before=int(gap_remaining_before),
            gap_remaining_after=int(gap_remaining_after),
            generic_gap_before=int(generic_gap_before),
            generic_gap_after=int(generic_gap_after),
            stage_gap_before=int(stage_gap_before),
            stage_gap_after=int(stage_gap_after),
            no_gain_streak=int(gap_no_gain_streak),
            candidate_set_coverage_gain=attempt_coverage_diagnostics,
            required_stage_gap_reduction=int(required_stage_gap_reduction),
            combined_gap_reduction=int(combined_gap_reduction),
            gap_gain_detected=bool(gap_gain_detected),
            missing_required_stage_ids=[
                str(item.get("stage_id") or "")
                for item in missing_workflow_stages
                if isinstance(item, dict) and str(item.get("stage_id") or "")
            ],
            attempt_status="parsed" if parsed_extra_count > 0 else "empty_or_parse_failed",
            stop_reason=str(attempt_stop_reason or ""),
        )
        if attempt_stop_reason == "coverage_converged":
            break
        if attempt_stop_reason == "no_gain_streak":
            stopped_by_no_gain = True
            yield "@@STATUS@@:Gap supplement stopped after 2 no-gain attempts.\n"
            break

    gap_attempts = supplement_attempt
    remaining_generic_gap_count = (
        int(coverage_gap_state["gap_count"]) if include_generic_gaps else 0
    )
    remaining_gap_count = remaining_generic_gap_count + int(len(missing_workflow_stages))
    added_count = max(0, len(parsed_result) - before_gap)
    record_timing_event_fn(
        "gap_supplement",
        gap_started,
        attempt_count=int(gap_attempts),
        added_count=int(added_count),
        remaining_gap_count=int(remaining_gap_count),
        remaining_generic_gap_count=int(remaining_generic_gap_count),
        remaining_required_stage_gap_count=int(len(missing_workflow_stages)),
        module_contract_normalized_count=int(module_contract_normalized_count),
        module_contract_rejected_count=int(module_contract_rejected_count),
        stopped_by_provider_error=bool(stopped_by_provider_error),
        stopped_by_no_gain=bool(stopped_by_no_gain),
        stop_reason=str(gap_stop_reason or ""),
        missing_required_stage_ids=list(
            required_stage_coverage.get("missing_required_stage_ids") or []
        ),
    )
    return GapSupplementRunResult(
        cases=_dict_case_items(parsed_result),
        coverage_primary=coverage_primary,
        coverage_gap_state=dict(coverage_gap_state or {}),
        attempt_count=int(gap_attempts),
        remaining_gap_count=int(remaining_gap_count),
        added_count=int(added_count),
        stopped_by_provider_error=bool(stopped_by_provider_error),
        stopped_by_no_gain=bool(stopped_by_no_gain),
        stop_reason=str(gap_stop_reason or ""),
        filter_stats=filter_stats,
        required_stage_coverage=dict(required_stage_coverage or {}),
        module_contract_normalized_count=int(module_contract_normalized_count),
        module_contract_rejected_count=int(module_contract_rejected_count),
    )


__all__ = [
    "GapSupplementParseResult",
    "GapSupplementRequest",
    "GapSupplementRunResult",
    "build_gap_supplement_request",
    "parse_gap_supplement_cases",
    "run_gap_supplement_attempts",
]
