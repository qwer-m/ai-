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
    # start_id 仅保留调用兼容；批次数量只由本轮全局目标和批大小决定。
    _ = start_id
    resolved_expected_count = max(0, int(expected_count or 0))
    resolved_batch_size = max(1, int(batch_size or 25))
    if append:
        needed_to_append = resolved_expected_count - int(existing_unique_count or 0)
        if needed_to_append > 0:
            resolved_batch_size = min(resolved_batch_size, needed_to_append)
    auto_extended = bool(append and resolved_expected_count <= int(existing_unique_count or 0))
    if auto_extended:
        resolved_expected_count = int(existing_unique_count or 0) + resolved_batch_size

    generation_target_count = (
        max(0, resolved_expected_count - int(existing_unique_count or 0))
        if append
        else resolved_expected_count
    )
    total_batches = math.ceil(generation_target_count / resolved_batch_size) if generation_target_count else 0
    return {
        "expected_count": int(resolved_expected_count),
        "batch_size": int(resolved_batch_size),
        "generation_target_count": int(generation_target_count),
        "total_batches": int(total_batches),
        "auto_extended": bool(auto_extended),
    }


def select_complete_generated_cases(
    cases: list[dict[str, Any]],
    *,
    limit: int,
    start_id: int,
    is_placeholder_expected_result_fn: Callable[[str], bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """只接收模型完整产出的用例，缺字段用例留给下一次模型生成补足。"""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    max_count = max(0, int(limit or 0))

    for raw_case in cases:
        if not isinstance(raw_case, dict):
            continue
        case = dict(raw_case)
        missing_fields: list[str] = []
        if len(str(case.get("description") or "").strip()) < 4:
            missing_fields.append("description")
        if not str(case.get("test_module") or "").strip():
            missing_fields.append("test_module")
        if not any(str(item or "").strip() for item in (case.get("preconditions") or [])):
            missing_fields.append("preconditions")
        if not any(str(item or "").strip() for item in (case.get("steps") or [])):
            missing_fields.append("steps")
        if not str(case.get("test_input") or "").strip():
            missing_fields.append("test_input")
        expected_result = str(case.get("expected_result") or "").strip()
        if not expected_result or is_placeholder_expected_result_fn(expected_result):
            missing_fields.append("expected_result")
        if str(case.get("priority") or "").strip().upper() not in {"P0", "P1", "P2"}:
            missing_fields.append("priority")

        if missing_fields:
            rejected.append(
                {
                    "case_id": str(case.get("id") or "").strip(),
                    "missing_fields": missing_fields,
                }
            )
            continue
        if len(accepted) >= max_count:
            break
        case["id"] = f"TC-{int(start_id) + len(accepted):03d}"
        accepted.append(case)

    return accepted, rejected


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
