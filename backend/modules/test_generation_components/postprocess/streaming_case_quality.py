from __future__ import annotations

from typing import Any

from .case_access import case_id, case_priority, case_text_field, case_value
from .streaming_expected_result_quality import is_non_assertable_expected_result, looks_truncated_text
from .streaming_reasoning_quality import reasoning_leakage_hits


def low_quality_reason(case: dict[str, Any]) -> str:
    desc = case_text_field(case, "description")
    module = case_text_field(case, "test_module")
    expected_result = case_text_field(case, "expected_result")
    priority = case_priority(case)
    steps = case_value(case, "steps", [])
    preconditions = case_value(case, "preconditions", [])

    if len(desc) < 4:
        return "description_too_short"
    if not module:
        return "missing_test_module"
    if not expected_result:
        return "missing_expected_result"
    if priority not in {"P0", "P1", "P2"}:
        return "invalid_priority"
    if not isinstance(steps, list) or not any(str(step).strip() for step in steps):
        return "missing_steps"
    if not isinstance(preconditions, list) or not any(str(item).strip() for item in preconditions):
        return "missing_preconditions"
    return ""


def is_low_quality(case: dict[str, Any]) -> bool:
    return bool(low_quality_reason(case))


def quality_drop_detail(case: dict[str, Any], *, reason: str, stage: str) -> dict[str, Any]:
    return {
        "stage": str(stage or ""),
        "reason": str(reason or ""),
        "case_id": case_id(case),
        "test_module": case_text_field(case, "test_module"),
        "priority": case_priority(case),
        "description": case_text_field(case, "description")[:240],
        "expected_result": case_text_field(case, "expected_result")[:240],
    }


def record_low_quality_drop(
    details: list[dict[str, Any]],
    case: dict[str, Any],
    *,
    reason: str,
    stage: str,
) -> None:
    details.append(quality_drop_detail(case, reason=reason, stage=stage))


def final_quality_drop_reason(case: dict[str, Any]) -> str:
    expected_text = case_text_field(case, "expected_result")
    expected_quality = str(case.get("expected_result_quality") or "").strip().lower()
    if reasoning_leakage_hits(case):
        return "reasoning_leakage"
    text_truncated = looks_truncated_text(expected_text)
    text_non_assertable = is_non_assertable_expected_result(expected_text)
    if expected_quality == "invalid_case":
        return "expected_result_quality:invalid_case"
    if expected_quality == "truncated" and text_truncated:
        return "expected_result_quality:truncated"
    if expected_quality == "non_assertable" and text_non_assertable:
        return "expected_result_quality:non_assertable"
    if text_truncated:
        return "truncated_text"
    if text_non_assertable:
        return "non_assertable_expected_result"
    return ""


def strip_case_meta_list(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    debug_fields = {
        "meta",
        "displayPriority",
        "rawPriority",
        "finalPriority",
        "model_priority_current",
        "model_priority",
        "legacy_priority",
        "priority_decision_state",
        "priority_decision_source",
        "priority_confidence",
        "priority_conflict_reason",
        "priority_resolution_reason",
        "priority_score",
        "suggested_priority",
        "priority_reasons",
    }
    stripped: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        case = dict(item)
        final_priority = case_priority(case, prefer_final=True)
        if final_priority in {"P0", "P1", "P2"}:
            case["priority"] = final_priority
            case["priority_final"] = final_priority
        for field in debug_fields:
            case.pop(field, None)
        stripped.append(case)
    return stripped
