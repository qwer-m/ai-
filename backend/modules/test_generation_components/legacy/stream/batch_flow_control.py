from __future__ import annotations

import math
from typing import Any, Callable


def resolve_stream_batch_plan(
    *,
    expected_count: int,
    batch_size: int,
    append: bool,
    start_id: int,
    existing_unique_count: int,
) -> dict[str, Any]:
    resolved_expected_count = int(expected_count or 0)
    resolved_batch_size = int(batch_size or 0)
    if append:
        needed_to_append = resolved_expected_count - int(existing_unique_count or 0)
        if needed_to_append > 25:
            resolved_batch_size = 25
        else:
            resolved_batch_size = max(1, needed_to_append)
    else:
        resolved_batch_size = 25

    resolved_batch_size = max(1, resolved_batch_size)
    auto_extended = bool(append and resolved_expected_count <= int(existing_unique_count or 0))
    if auto_extended:
        resolved_expected_count = int(existing_unique_count or 0) + resolved_batch_size

    total_batches = math.ceil((resolved_expected_count - (int(start_id or 1) - 1)) / resolved_batch_size)
    if total_batches < 1 and resolved_expected_count > (int(start_id or 1) - 1):
        total_batches = 1
    return {
        "expected_count": int(resolved_expected_count),
        "batch_size": int(resolved_batch_size),
        "total_batches": int(total_batches),
        "auto_extended": bool(auto_extended),
    }


def build_existing_case_history(
    existing_cases: Any,
    *,
    append: bool,
    build_case_signature_fn: Callable[[dict[str, Any]], str],
) -> tuple[list[str], set[str]]:
    history_summaries: list[str] = []
    seen_case_signatures: set[str] = set()
    if append and isinstance(existing_cases, list):
        for case in existing_cases:
            if isinstance(case, dict):
                history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")
                signature = build_case_signature_fn(case)
                if signature:
                    seen_case_signatures.add(signature)
    return history_summaries, seen_case_signatures


def build_stream_batch_quality_metric(
    *,
    parsed_batch_cases: list[dict[str, Any]],
    seen_case_signatures: set[str],
    batch_index: int,
    build_case_signature_fn: Callable[[dict[str, Any]], str],
    is_non_assertable_expected_result_fn: Callable[[str], bool],
    previous_low_gain_streak: int,
) -> tuple[dict[str, Any], int]:
    new_valid_cases_count = int(len(parsed_batch_cases))
    unique_increment = 0
    non_assertable_count = 0
    for case in parsed_batch_cases:
        signature = build_case_signature_fn(case)
        if signature and signature not in seen_case_signatures:
            seen_case_signatures.add(signature)
            unique_increment += 1
        if is_non_assertable_expected_result_fn(str(case.get("expected_result") or "")):
            non_assertable_count += 1

    duplicate_count = max(0, new_valid_cases_count - unique_increment)
    duplicate_rate = float(duplicate_count) / float(new_valid_cases_count) if new_valid_cases_count > 0 else 1.0
    coverage_gain_count = int(unique_increment)
    low_quality_filtered_count = int(non_assertable_count)
    low_gain_detected = bool(
        (coverage_gain_count <= 1)
        or (duplicate_rate >= 0.6)
        or (new_valid_cases_count > 0 and (float(non_assertable_count) / float(new_valid_cases_count)) >= 0.5)
    )
    low_gain_streak = int(previous_low_gain_streak or 0) + 1 if low_gain_detected else 0
    return (
        {
            "batch_index": int(batch_index),
            "new_valid_cases_count": int(new_valid_cases_count),
            "duplicate_rate": round(float(duplicate_rate), 4),
            "non_assertable_count": int(non_assertable_count),
            "low_quality_filtered_count": int(low_quality_filtered_count),
            "coverage_gain_count": int(coverage_gain_count),
            "low_gain_detected": bool(low_gain_detected),
            "low_gain_streak": int(low_gain_streak),
        },
        int(low_gain_streak),
    )
