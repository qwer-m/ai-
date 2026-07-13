from __future__ import annotations

from typing import Any

from .case_access import case_flat_text
from .execution_plan_validation_tokens import _STATE_FIELD_NAMES


def _text(value: Any) -> str:
    return str(value or "").strip()


def _transition_payload(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("workflow_transition")
    return dict(nested) if isinstance(nested, dict) else {}


def _state_value(case: dict[str, Any], field: str) -> Any:
    value = case.get(field)
    if value not in (None, ""):
        return value
    return _transition_payload(case).get(field)


def _case_order(case: dict[str, Any], fallback: int) -> tuple[int, int]:
    try:
        main_step = int(case.get("main_chain_step") or 0)
    except (TypeError, ValueError):
        main_step = 0
    try:
        sequence = int(case.get("execution_sequence") or fallback)
    except (TypeError, ValueError):
        sequence = fallback
    return (main_step if main_step > 0 else 100000 + sequence, sequence)


def _main_smoke_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        dict(item)
        for item in cases
        if isinstance(item, dict) and _text(item.get("execution_group")).lower() == "main_smoke"
    ]
    return [
        item
        for _, item in sorted(
            enumerate(selected, start=1),
            key=lambda row: _case_order(row[1], row[0]),
        )
    ]


def _case_semantic_text(case: dict[str, Any]) -> str:
    return case_flat_text(
        case,
        fields=("test_module", "description", "test_input", "expected_result", "preconditions", "steps"),
        separator=" ",
        lower=True,
    )


def materialize_final_case_state_fields(cases: Any) -> Any:
    """Promote the workflow transition contract into persisted final-case fields."""
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
        normalized.append(case)
    return normalized
