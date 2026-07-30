from __future__ import annotations

from typing import Any

from .case_access import case_flat_text
from .execution_plan_validation_tokens import _STATE_FIELD_NAMES


_UNKNOWN_STATE_SOURCES = {"", "unknown", "unspecified", "uncertain"}
_PREVIOUS_STAGE_SOURCES = {"previous_stage", "previous_step", "upstream_stage"}
_NEGATIVE_POLARITIES = {"negative", "negated", "absent", "false", "not"}


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


def _normalized_token(value: Any) -> str:
    return _text(value).lower().replace("-", "_").replace(" ", "_")


def _semantic_payload(case: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(case, dict):
        return {}
    payload = case.get("_semantic")
    return dict(payload) if isinstance(payload, dict) else {}


def _normalized_state_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for raw_item in value:
        if not isinstance(raw_item, dict):
            continue
        entity = _normalized_token(raw_item.get("entity"))
        state = _normalized_token(raw_item.get("state"))
        if not entity or not state:
            continue
        source = _normalized_token(raw_item.get("source"))
        scope = _normalized_token(raw_item.get("scope"))
        polarity = _normalized_token(raw_item.get("polarity")) or "positive"
        key = (entity, state, source, scope, polarity)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "entity": entity,
                "state": state,
                "source": source,
                "scope": scope,
                "polarity": polarity,
                "temporal": _normalized_token(raw_item.get("temporal")),
            }
        )
    return records


def _state_record_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _positive_confidence(value: Any) -> bool:
    try:
        return float(value) > 0.0
    except (TypeError, ValueError):
        return False


def _verified_semantic_state_items(value: Any) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in _state_record_items(value)
        if item.get("evidence_verified") is True
        and _positive_confidence(item.get("confidence"))
    ]


def _state_scope_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_scope = _normalized_token(left.get("scope"))
    right_scope = _normalized_token(right.get("scope"))
    return not left_scope or not right_scope or left_scope == right_scope


def _state_polarity_is_negative(state: dict[str, Any]) -> bool:
    return _normalized_token(state.get("polarity")) in _NEGATIVE_POLARITIES


def typed_precondition_states(
    case: dict[str, Any] | None,
    *,
    step_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    semantic = _semantic_payload(case)
    return _normalized_state_records(
        [
            *_state_record_items((step_meta or {}).get("required_states")),
            *_verified_semantic_state_items(semantic.get("precondition_states")),
        ]
    )


def typed_produced_states(
    case: dict[str, Any] | None,
    *,
    step_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    semantic = _semantic_payload(case)
    return _normalized_state_records(
        [
            *_state_record_items((step_meta or {}).get("produced_states")),
            *_verified_semantic_state_items(semantic.get("produced_states")),
        ]
    )


def typed_state_contract_conflicts(
    case: dict[str, Any],
    *,
    step_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """蓝图状态可继承；模型状态只在与同实体权威状态显式矛盾时拒绝。"""
    semantic = _semantic_payload(case)
    actual_preconditions = _normalized_state_records(
        _verified_semantic_state_items(semantic.get("precondition_states"))
    )
    actual_produced = _normalized_state_records(
        _verified_semantic_state_items(semantic.get("produced_states"))
    )
    declared_preconditions = _normalized_state_records(
        _state_record_items(step_meta.get("required_states"))
    )
    declared_produced = _normalized_state_records(
        _state_record_items(step_meta.get("produced_states"))
    )
    conflicts: list[dict[str, Any]] = []
    for semantic_key, actual, declared in (
        ("precondition_states", actual_preconditions, declared_preconditions),
        ("produced_states", actual_produced, declared_produced),
    ):
        for observed in actual:
            same_entity = [
                expected
                for expected in declared
                if str(expected.get("entity") or "") == str(observed.get("entity") or "")
                and _state_scope_compatible(expected, observed)
            ]
            if not same_entity:
                continue
            same_state = [
                expected
                for expected in same_entity
                if str(expected.get("state") or "") == str(observed.get("state") or "")
            ]
            if same_state and any(
                _state_polarity_is_negative(expected)
                != _state_polarity_is_negative(observed)
                for expected in same_state
            ):
                expected = same_state[0]
                conflicts.append(
                    {
                        "reason": f"case_{semantic_key}_opposite_polarity_workflow_contract_state",
                        "entity": str(observed.get("entity") or ""),
                        "state": str(observed.get("state") or ""),
                        "scope": str(observed.get("scope") or expected.get("scope") or ""),
                        "expected_polarity": str(expected.get("polarity") or "positive"),
                        "actual_polarity": str(observed.get("polarity") or "positive"),
                    }
                )
                continue
            for expected in same_state:
                expected_source = str(expected.get("source") or "")
                actual_source = str(observed.get("source") or "")
                if (
                    expected_source not in _UNKNOWN_STATE_SOURCES
                    and actual_source not in _UNKNOWN_STATE_SOURCES
                    and expected_source != actual_source
                ):
                    conflicts.append(
                        {
                            "reason": f"case_{semantic_key}_source_conflicts_workflow_contract_state",
                            "entity": str(observed.get("entity") or ""),
                            "state": str(observed.get("state") or ""),
                            "scope": str(observed.get("scope") or expected.get("scope") or ""),
                            "expected_source": expected_source,
                            "actual_source": actual_source,
                        }
                    )
                    break
    return conflicts


def unknown_precondition_source_count(
    case: dict[str, Any],
    *,
    step_meta: dict[str, Any] | None = None,
) -> int:
    return sum(
        1
        for state in typed_precondition_states(case, step_meta=step_meta)
        if str(state.get("source") or "") in _UNKNOWN_STATE_SOURCES
    )


def _same_typed_state(required: dict[str, Any], produced: dict[str, Any]) -> bool:
    if required.get("entity") != produced.get("entity") or required.get("state") != produced.get("state"):
        return False
    required_scope = str(required.get("scope") or "")
    produced_scope = str(produced.get("scope") or "")
    if required_scope and produced_scope and required_scope != produced_scope:
        return False
    required_negative = str(required.get("polarity") or "") in _NEGATIVE_POLARITIES
    produced_negative = str(produced.get("polarity") or "") in _NEGATIVE_POLARITIES
    return required_negative == produced_negative


def main_chain_precondition_conflicts(
    previous_case: dict[str, Any] | None,
    current_case: dict[str, Any],
    *,
    previous_step_meta: dict[str, Any] | None = None,
    current_step_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    required_states = [
        state
        for state in _normalized_state_records(
            _state_record_items((current_step_meta or {}).get("required_states"))
        )
        if str(state.get("source") or "") in _PREVIOUS_STAGE_SOURCES
    ]
    if not required_states:
        return []
    produced_states = _normalized_state_records(
        _state_record_items((previous_step_meta or {}).get("produced_states"))
    )
    return [
        {
            "reason": "precondition_state_not_produced_by_previous_stage",
            "entity": str(required.get("entity") or ""),
            "state": str(required.get("state") or ""),
            "scope": str(required.get("scope") or ""),
            "polarity": str(required.get("polarity") or "positive"),
            "source": "previous_stage",
        }
        for required in required_states
        if not any(_same_typed_state(required, produced) for produced in produced_states)
    ]


def main_chain_precondition_conflict_reason(
    previous_case: dict[str, Any] | None,
    current_case: dict[str, Any],
    *,
    previous_step_meta: dict[str, Any] | None = None,
    current_step_meta: dict[str, Any] | None = None,
) -> str:
    """只校验明确声明为上一阶段来源的结构化实体状态。"""
    if not isinstance(current_case, dict):
        return ""
    conflicts = main_chain_precondition_conflicts(
        previous_case,
        current_case,
        previous_step_meta=previous_step_meta,
        current_step_meta=current_step_meta,
    )
    return str(conflicts[0].get("reason") or "") if conflicts else ""


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
