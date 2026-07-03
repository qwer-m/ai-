from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Generator

from .result_postprocess_priority_semantics import apply_priority_semantics_to_cases
from .streaming_case_quality import filter_low_quality_cases_with_stats
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


def build_gap_supplement_request(
    *,
    requirement: str,
    append: bool,
    existing_cases: list[dict[str, Any]],
    parsed_result: list[dict[str, Any]],
    coverage_primary: dict[str, Any],
    missing_rules: list[str],
    current_biz_key: str,
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
        current_biz_key=current_biz_key,
        pretty_json=False,
    )
    return GapSupplementRequest(
        supplement_source=supplement_source,
        system_prompt=f"""
{gap_prompt}

CLOSED_LOOP_HINT:
{closed_loop_instruction}

APPEND_POLICY: only append if new cases add coverage gain; otherwise return [].
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
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
    build_supplement_closed_loop_instruction_fn: Callable[..., str],
    build_gap_fill_prompt_fn: Callable[..., str],
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    resolve_coverage_gap_state_fn: Callable[[dict[str, Any]], dict[str, Any]],
    record_timing_event_fn: Callable[..., dict[str, Any]],
) -> Generator[str, None, GapSupplementRunResult]:
    gap_started = time.perf_counter()
    yield "@@STATUS@@:[multi-pass] Stage 2/3 gap supplement started...\n"
    before_gap = len(parsed_result)
    supplement_attempt = 0
    gap_no_gain_streak = 0
    stopped_by_no_gain = False
    stopped_by_provider_error = False
    gap_stop_reason = ""
    filter_stats: list[dict[str, Any]] = []
    missing_rules = list(coverage_gap_state.get("missing_rules") or [])
    has_missing_types = bool(coverage_gap_state.get("has_missing_types"))

    while supplement_attempt < 3 and (missing_rules or has_missing_types):
        supplement_attempt += 1
        gap_attempt_started = time.perf_counter()
        before_attempt_count = _dict_case_count(parsed_result)
        gap_remaining_before = int(coverage_gap_state["gap_count"])
        yield f"@@STATUS@@:Gap supplement attempt #{supplement_attempt}...\n"

        gap_request = build_gap_supplement_request(
            requirement=requirement,
            append=append,
            existing_cases=existing_cases,
            parsed_result=parsed_result,
            coverage_primary=coverage_primary,
            missing_rules=missing_rules,
            current_biz_key=current_biz_key,
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
            yield chunk
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
                attempt_status="provider_error",
                stop_reason="provider_error",
            )
            yield "\n@@STATUS@@:Generation failed\n"
            yield f"{provider_error}\n"
            stopped_by_provider_error = True
            gap_stop_reason = "provider_error"
            break

        parsed_extra_count = 0
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
                filter_stats.append(dict(gap_parse_result.filter_stats or {}))
                parsed_result.extend(_dict_case_items(gap_parse_result.cases))
                parsed_result = normalize_json_structure_fn(parsed_result)
                parsed_result = deduplicate_test_cases_fn(parsed_result)
        except Exception:
            pass
        after_attempt_count = _dict_case_count(parsed_result)
        effective_added_count = max(0, int(after_attempt_count) - int(before_attempt_count))
        if effective_added_count <= 0:
            gap_no_gain_streak += 1
        else:
            gap_no_gain_streak = 0
        coverage_primary = analyze_coverage_fn(requirement, parsed_result)
        coverage_gap_state = resolve_coverage_gap_state_fn(coverage_primary)
        missing_rules = list(coverage_gap_state["missing_rules"])
        has_missing_types = bool(coverage_gap_state["has_missing_types"])
        gap_remaining_after = int(coverage_gap_state["gap_count"])
        attempt_stop_reason = ""
        if not missing_rules and not has_missing_types:
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
            no_gain_streak=int(gap_no_gain_streak),
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
    remaining_gap_count = int(coverage_gap_state["gap_count"])
    added_count = max(0, len(parsed_result) - before_gap)
    record_timing_event_fn(
        "gap_supplement",
        gap_started,
        attempt_count=int(gap_attempts),
        added_count=int(added_count),
        remaining_gap_count=int(remaining_gap_count),
        stopped_by_provider_error=bool(stopped_by_provider_error),
        stopped_by_no_gain=bool(stopped_by_no_gain),
        stop_reason=str(gap_stop_reason or ""),
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
    )


__all__ = [
    "GapSupplementParseResult",
    "GapSupplementRequest",
    "GapSupplementRunResult",
    "build_gap_supplement_request",
    "parse_gap_supplement_cases",
    "run_gap_supplement_attempts",
]
