from __future__ import annotations

from typing import Any

from ..postprocess.case_access import case_id as case_access_id, case_text_field
from ..postprocess.streaming_expected_result_quality import (
    is_non_assertable_expected_result as _shared_non_assertable_expected_result,
)

TRUNCATED_TEXT_ENDINGS = (
    "鎴栨樉",
    "瀵瑰簲鍐?",
    "鍙牎",
    "姝ｅ父灞?",
    "璺宠浆鑷?",
    "鏄剧ず涓?",
)


def is_non_assertable_expected_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    return _shared_non_assertable_expected_result(normalized)


def summarize_case_quality_gate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    case_items = [item for item in (cases or []) if isinstance(item, dict)]
    priority_final_invalid_case_ids: list[str] = []
    non_assertable_case_ids: list[str] = []
    truncated_case_ids: list[str] = []
    priority_final_null_count = 0
    non_assertable_expected_result_count = 0
    truncated_text_count = 0

    for index, case_item in enumerate(case_items, start=1):
        case_id = case_access_id(case_item) or f"ROW-{int(index):03d}"
        priority_final_value = case_text_field(case_item, "priority_final").upper()
        if priority_final_value not in {"P0", "P1", "P2"}:
            priority_final_null_count += 1
            priority_final_invalid_case_ids.append(case_id)

        expected_result_text = case_text_field(case_item, "expected_result")
        expected_result_quality = str(case_item.get("expected_result_quality") or "").strip().lower()
        quality_reason = str(case_item.get("expected_result_quality_reason") or "").strip().lower()
        truncated_flag = bool(case_item.get("truncated_text_detected"))

        text_non_assertable = is_non_assertable_expected_result(expected_result_text)
        metadata_non_assertable = bool(
            expected_result_quality == "non_assertable"
            or quality_reason in {"no_concrete_assertion", "template_or_weak_assertion"}
        )
        non_assertable_hit = bool(text_non_assertable or (metadata_non_assertable and not expected_result_text))
        if non_assertable_hit:
            non_assertable_expected_result_count += 1
            non_assertable_case_ids.append(case_id)

        expected_result_trimmed = expected_result_text.rstrip("銆傦紒锛?!? ")
        truncated_suffix_hit = any(expected_result_trimmed.endswith(suffix) for suffix in TRUNCATED_TEXT_ENDINGS)
        truncated_hit = bool(
            expected_result_quality == "truncated"
            or truncated_flag
            or truncated_suffix_hit
        )
        if truncated_hit:
            truncated_text_count += 1
            truncated_case_ids.append(case_id)

    failed_checks: list[str] = []
    if priority_final_null_count > 0:
        failed_checks.append(f"priority_final_null_count={int(priority_final_null_count)}")
    if non_assertable_expected_result_count > 0:
        failed_checks.append(f"non_assertable_expected_result_count={int(non_assertable_expected_result_count)}")
    if truncated_text_count > 0:
        failed_checks.append(f"truncated_text_count={int(truncated_text_count)}")

    return {
        "passed": not bool(failed_checks),
        "failed_checks": failed_checks,
        "priority_final_null_count": int(priority_final_null_count),
        "invalid_priority_final_count": int(len(priority_final_invalid_case_ids)),
        "invalid_priority_final_case_ids": list(priority_final_invalid_case_ids),
        "non_assertable_expected_result_count": int(non_assertable_expected_result_count),
        "truncated_text_count": int(truncated_text_count),
        "non_assertable_case_ids": list(non_assertable_case_ids),
        "truncated_case_ids": list(truncated_case_ids),
    }


__all__ = [
    "TRUNCATED_TEXT_ENDINGS",
    "is_non_assertable_expected_result",
    "summarize_case_quality_gate",
]
