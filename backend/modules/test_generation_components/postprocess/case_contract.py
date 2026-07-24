from __future__ import annotations

from typing import Any

from .case_access import case_id as case_access_id, case_text_field, case_text_parts, case_value
from .streaming_reasoning_quality import reasoning_leakage_hits


VALID_PRIORITIES = {"P0", "P1", "P2"}

DEBUG_CASE_FIELDS = {
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
    "priorityDebug",
    "priority_debug",
}

PERSISTABLE_CASE_FIELDS = (
    "id",
    "description",
    "test_module",
    "preconditions",
    "steps",
    "test_input",
    "expected_result",
    "priority",
    "priority_final",
)

REQUIRED_PUBLIC_FIELDS = (
    "id",
    "description",
    "test_module",
    "preconditions",
    "steps",
    "test_input",
    "expected_result",
    "priority",
    "priority_final",
)

REQUIRED_NON_EMPTY_FIELDS = (
    "id",
    "description",
    "test_module",
    "steps",
    "expected_result",
    "priority",
    "priority_final",
)

_STATE_FIELD_NAMES = (
    "workflow_id",
    "source_state",
    "action",
    "target_state",
    "path_type",
    "blocking",
    "destructive",
    "can_advance_main_flow",
    "state_transition_confidence",
)

_PUBLIC_NOTE_REASONING_SIGNALS = (
    "need product confirm",
    "product confirm",
    "requirement unclear",
    "assume here",
    "assuming here",
    "reread requirement",
    "not reasonable",
    "需求未明确",
    "再读需求",
    "按需求原文",
    "需求说",
    "这里应该",
    "假设此处",
    "此处假设",
    "暂且认为",
    "暂按",
    "暂时按",
    "需考虑",
)


def _case_id(case: dict[str, Any], index: int) -> str:
    return case_access_id(case) or f"ROW-{index:03d}"


def _priority(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in VALID_PRIORITIES else ""


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _transition_payload(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("workflow_transition")
    return dict(nested) if isinstance(nested, dict) else {}


def materialize_case_state_fields(cases: Any) -> Any:
    """Promote nested workflow transition fields without importing the validator."""
    if not isinstance(cases, list):
        return cases
    normalized: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        case = dict(item)
        transition = _transition_payload(case)
        for field in _STATE_FIELD_NAMES:
            value = case.get(field)
            if value in (None, "") and transition.get(field) not in (None, ""):
                case[field] = transition[field]
        if case.get("main_chain_stage_kind") in (None, "") and transition.get("stage_kind") not in (None, ""):
            case["main_chain_stage_kind"] = transition["stage_kind"]
        normalized.append(case)
    return normalized


def case_has_reasoning_leakage(case: dict[str, Any]) -> bool:
    if reasoning_leakage_hits(case):
        return True

    parts = case_text_parts(case, ("description", "test_input"))
    text = "\n".join(parts).lower()
    return any(signal in text for signal in _PUBLIC_NOTE_REASONING_SIGNALS)


def _materialize_public_alias_fields(source: dict[str, Any]) -> None:
    for field in ("id", "description", "test_module", "test_input", "expected_result", "priority", "priority_final"):
        if not _has_value(source.get(field)):
            value = case_text_field(source, field)
            if value:
                source[field] = value
    for field in ("preconditions", "steps"):
        if not _has_value(source.get(field)):
            value = case_value(source, field, [])
            if _has_value(value):
                source[field] = value


def project_persistable_cases(cases: Any) -> list[dict[str, Any]]:
    """Return the public case shape allowed to be stored as the final asset."""
    materialized = materialize_case_state_fields(cases)
    if not isinstance(materialized, list):
        return []

    projected: list[dict[str, Any]] = []
    for item in materialized:
        if not isinstance(item, dict):
            continue
        source = dict(item)
        _materialize_public_alias_fields(source)
        final_priority = _priority(source.get("priority_final")) or _priority(source.get("priority"))
        if final_priority:
            source["priority"] = final_priority
            source["priority_final"] = final_priority

        row: dict[str, Any] = {}
        for field in PERSISTABLE_CASE_FIELDS:
            if field in source and field not in DEBUG_CASE_FIELDS:
                row[field] = source[field]
        projected.append(row)
    return projected


def summarize_persistable_case_contract(cases: Any) -> dict[str, Any]:
    """Validate the current final-case payload without mutating it."""
    materialized = materialize_case_state_fields(cases)
    case_items = [dict(item) for item in materialized if isinstance(item, dict)] if isinstance(materialized, list) else []

    missing_required_ids: list[str] = []
    invalid_priority_final_ids: list[str] = []
    reasoning_leakage_ids: list[str] = []

    for index, case in enumerate(case_items, start=1):
        case_id = _case_id(case, index)
        missing_key = any(field not in case for field in REQUIRED_PUBLIC_FIELDS)
        missing_value = any(not _has_value(case.get(field)) for field in REQUIRED_NON_EMPTY_FIELDS)
        if missing_key or missing_value:
            missing_required_ids.append(case_id)
        if _priority(case.get("priority_final")) not in VALID_PRIORITIES:
            invalid_priority_final_ids.append(case_id)
        if case_has_reasoning_leakage(case):
            reasoning_leakage_ids.append(case_id)

    failed_checks: list[str] = []
    diagnostic_checks: list[str] = []
    if missing_required_ids:
        failed_checks.append(f"persistable_required_field_missing_count={len(missing_required_ids)}")
    if invalid_priority_final_ids:
        failed_checks.append(f"persistable_priority_final_invalid_count={len(invalid_priority_final_ids)}")
    if reasoning_leakage_ids:
        diagnostic_checks.append(
            f"persistable_reasoning_leakage_count={len(reasoning_leakage_ids)}"
        )

    return {
        "passed": not failed_checks,
        "failed_checks": failed_checks,
        "diagnostic_checks": diagnostic_checks,
        "metrics": {
            "persistable_case_count": int(len(case_items)),
            "persistable_required_field_missing_count": int(len(missing_required_ids)),
            "persistable_priority_final_invalid_count": int(len(invalid_priority_final_ids)),
            "persistable_reasoning_leakage_count": int(len(reasoning_leakage_ids)),
        },
        "persistable_required_field_missing_case_ids": missing_required_ids,
        "persistable_priority_final_invalid_case_ids": invalid_priority_final_ids,
        "persistable_reasoning_leakage_case_ids": reasoning_leakage_ids,
    }


def merge_contract_quality_gate(
    quality_gate: dict[str, Any] | None,
    contract_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(quality_gate or {})
    contract = dict(contract_summary or {})
    failed_checks = [
        str(item).strip()
        for item in (merged.get("failed_checks") or [])
        if str(item).strip()
    ]
    for item in contract.get("failed_checks") or []:
        value = str(item).strip()
        if value and value not in failed_checks:
            failed_checks.append(value)

    diagnostic_checks = [
        str(item).strip()
        for item in (merged.get("diagnostic_checks") or [])
        if str(item).strip()
    ]
    for item in contract.get("diagnostic_checks") or []:
        value = str(item).strip()
        if value and value not in diagnostic_checks:
            diagnostic_checks.append(value)

    metrics = dict(merged.get("metrics") or {})
    metrics.update(dict(contract.get("metrics") or {}))
    merged["failed_checks"] = failed_checks
    merged["diagnostic_checks"] = diagnostic_checks
    merged["passed"] = not bool(failed_checks)
    merged["metrics"] = metrics
    for key in (
        "persistable_required_field_missing_case_ids",
        "persistable_priority_final_invalid_case_ids",
        "persistable_reasoning_leakage_case_ids",
    ):
        if key in contract:
            merged[key] = list(contract.get(key) or [])
    return merged


__all__ = [
    "DEBUG_CASE_FIELDS",
    "PERSISTABLE_CASE_FIELDS",
    "case_has_reasoning_leakage",
    "materialize_case_state_fields",
    "merge_contract_quality_gate",
    "project_persistable_cases",
    "summarize_persistable_case_contract",
]
