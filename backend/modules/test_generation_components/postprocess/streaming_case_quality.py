from __future__ import annotations

from typing import Any, Callable, Iterable

from .case_access import case_id, case_priority, case_text_field, case_value
from .module_contract import FUNCTIONAL_PHASE_FIELDS
from .streaming_case_normalization import (
    is_placeholder_expected_result,
    normalize_priority_value,
    normalize_steps,
)
from .streaming_expected_result_quality import (
    is_case_expected_result_non_assertable,
    looks_truncated_text,
)
from .streaming_reasoning_quality import reasoning_leakage_hits
from .streaming_semantic_text import semantic_tokenize


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
    if not case_text_field(case, "test_input"):
        return "missing_test_input"
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


def diagnose_final_quality_cases(
    cases: Iterable[Any],
    low_quality_drop_details: list[dict[str, Any]],
    *,
    stage: str,
) -> tuple[list[dict[str, Any]], int]:
    retained: list[dict[str, Any]] = []
    diagnostic_total = 0
    for case in cases:
        if not isinstance(case, dict):
            continue
        diagnostic_reason = final_quality_diagnostic_reason(case)
        if diagnostic_reason:
            diagnostic_total += 1
            low_quality_drop_details.append(
                {
                    **quality_drop_detail(
                        case,
                        reason=diagnostic_reason,
                        stage=stage,
                    ),
                    "diagnostic_only": True,
                }
            )
        retained.append(case)
    return retained, diagnostic_total


def normalize_case_structure(case: dict[str, Any]) -> dict[str, Any] | None:
    normalized = dict(case or {})
    module = str(normalized.get("test_module") or "").strip()
    description = str(normalized.get("description") or "").strip()
    expected_result_raw = str(normalized.get("expected_result") or "").strip()
    expected_result = expected_result_raw
    expected_result_alignment_warning = False
    expected_result_quality = "assertable"
    expected_result_quality_reason = ""
    truncated_text_detected = False
    invalid_case_reason = ""
    invalid_case_signals: list[str] = []
    if not module or len(description) < 4:
        return None

    normalized_steps = normalize_steps(normalized.get("steps"))
    if not normalized_steps:
        return None

    preconditions = normalized.get("preconditions")
    if not isinstance(preconditions, list):
        preconditions = []
    preconditions = [str(item).strip() for item in preconditions if str(item).strip()]

    if not expected_result or is_placeholder_expected_result(expected_result):
        expected_result = expected_result_raw
        expected_result_quality = "non_assertable"
        expected_result_quality_reason = (
            "missing_expected_result" if not expected_result else "placeholder_expected_result"
        )
    else:
        step_tokens = semantic_tokenize(" ".join(normalized_steps), limit=18)
        expected_tokens = semantic_tokenize(expected_result, limit=12)
        if expected_tokens and step_tokens and not step_tokens.intersection(expected_tokens):
            expected_result_alignment_warning = True

    if looks_truncated_text(expected_result):
        expected_result_quality = "truncated"
        expected_result_quality_reason = "truncated_suffix_detected"
        truncated_text_detected = True
    elif is_case_expected_result_non_assertable(normalized):
        expected_result_quality = "non_assertable"
        if not expected_result_quality_reason:
            expected_result_quality_reason = "template_or_weak_assertion"
    else:
        expected_result_quality = "assertable"
        if not expected_result_quality_reason:
            expected_result_quality_reason = "contains_concrete_assertion"

    test_input = str(normalized.get("test_input") or "").strip()

    normalized["steps"] = normalized_steps
    normalized["preconditions"] = preconditions
    leakage_probe = dict(normalized)
    leakage_probe["steps"] = normalized_steps
    leakage_probe["preconditions"] = preconditions
    leakage_probe["test_input"] = test_input
    leakage_probe["expected_result"] = expected_result
    invalid_case_signals = reasoning_leakage_hits(leakage_probe)
    if invalid_case_signals:
        invalid_case_reason = "reasoning_leakage"
        expected_result_quality = "invalid_case"
        expected_result_quality_reason = "reasoning_leakage"
        truncated_text_detected = False

    normalized["steps"] = normalized_steps
    normalized["preconditions"] = preconditions
    normalized["test_input"] = test_input
    normalized["expected_result"] = expected_result
    normalized["expected_result_quality"] = expected_result_quality
    normalized["expected_result_quality_reason"] = expected_result_quality_reason
    normalized["expected_result_alignment_warning"] = bool(expected_result_alignment_warning)
    normalized["truncated_text_detected"] = bool(truncated_text_detected)
    normalized["case_quality"] = "invalid_case" if invalid_case_reason else "valid_case"
    normalized["invalid_case_reason"] = invalid_case_reason
    normalized["invalid_case_signals"] = invalid_case_signals
    normalized["priority"] = normalize_priority_value(str(normalized.get("priority") or ""))
    return normalized


def filter_low_quality_cases_with_stats(
    cases: Iterable[Any],
    *,
    requirement_text: str = "",
    normalize_case_structure_fn: Callable[[dict[str, Any]], dict[str, Any] | None] = normalize_case_structure,
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_cases: list[dict[str, Any]] = []
    stats = {
        "invalid_structure_dropped": 0,
        "weak_case_dropped": 0,
        "weak_case_detected": 0,
        "semantic_dedup_dropped": 0,
        "uncertain_requirement_dropped": 0,
        "governance_hard_drop": 0,
        "total_dropped": 0,
        "dropped_details": [],
        "diagnostic_details": [],
    }
    for item in cases:
        if not isinstance(item, dict):
            stats["invalid_structure_dropped"] += 1
            stats["dropped_details"].append(
                {"stage": "initial_structure_filter", "reason": "non_dict_case"}
            )
            continue
        normalized = normalize_case_structure_fn(item)
        if not isinstance(normalized, dict):
            stats["invalid_structure_dropped"] += 1
            stats["dropped_details"].append(
                quality_drop_detail(item, reason="normalize_failed", stage="initial_structure_filter")
            )
            continue
        drop_reason = low_quality_reason(normalized)
        if drop_reason:
            stats["weak_case_detected"] += 1
            stats["diagnostic_details"].append(
                quality_drop_detail(
                    normalized,
                    reason=drop_reason,
                    stage="pre_review_quality_diagnostic",
                )
            )
        normalized_cases.append(normalized)

    stats["total_dropped"] = int(
        stats.get("invalid_structure_dropped", 0)
        + stats.get("weak_case_dropped", 0)
        + stats.get("semantic_dedup_dropped", 0)
        + stats.get("uncertain_requirement_dropped", 0)
        + stats.get("governance_hard_drop", 0)
    )
    return normalized_cases, stats


def final_quality_diagnostic_reason(case: dict[str, Any]) -> str:
    expected_text = case_text_field(case, "expected_result")
    expected_quality = str(case.get("expected_result_quality") or "").strip().lower()
    if reasoning_leakage_hits(case):
        return "reasoning_leakage"
    text_truncated = looks_truncated_text(expected_text)
    text_non_assertable = is_case_expected_result_non_assertable(case)
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
        *FUNCTIONAL_PHASE_FIELDS,
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
