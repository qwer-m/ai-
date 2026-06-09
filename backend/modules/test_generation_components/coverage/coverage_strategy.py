from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STRATEGY_DATA_PATH = Path(__file__).with_name("coverage_strategy_data.json")


def _load_strategy_payload() -> dict[str, object]:
    with _STRATEGY_DATA_PATH.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict):
        raise ValueError("coverage strategy data must be a JSON object")
    return payload


def _string_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(str(v) for v in values if str(v or "").strip())


def _keyword_pairs(values: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(values, list):
        return ()
    result: list[tuple[str, tuple[str, ...]]] = []
    for item in values:
        if not isinstance(item, list) or len(item) < 2:
            continue
        key = str(item[0] or "").strip()
        if not key:
            continue
        keywords = _string_tuple(item[1])
        if keywords:
            result.append((key, keywords))
    return tuple(result)


_PAYLOAD = _load_strategy_payload()


def intent_action_keywords() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return _keyword_pairs(_PAYLOAD.get("intent_action_keywords"))


def intent_outcome_keywords() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return _keyword_pairs(_PAYLOAD.get("intent_outcome_keywords"))


def intent_stopwords() -> set[str]:
    raw = _PAYLOAD.get("intent_stopwords", [])
    if not isinstance(raw, list):
        return set()
    return {str(v).strip().lower() for v in raw if str(v or "").strip()}


def stopwords() -> set[str]:
    raw = _PAYLOAD.get("stopwords", [])
    if not isinstance(raw, list):
        return set()
    return {str(v).strip() for v in raw if str(v or "").strip()}


def complexity_hints() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("complexity_hints"))


def boundary_hints() -> set[str]:
    raw = _PAYLOAD.get("boundary_hints", [])
    if not isinstance(raw, list):
        return set()
    return {str(v).strip() for v in raw if str(v or "").strip()}


def boundary_required_hints() -> set[str]:
    raw = _PAYLOAD.get("boundary_required_hints", [])
    if not isinstance(raw, list):
        return set()
    return {str(v).strip() for v in raw if str(v or "").strip()}


def exception_hints() -> set[str]:
    raw = _PAYLOAD.get("exception_hints", [])
    if not isinstance(raw, list):
        return set()
    return {str(v).strip() for v in raw if str(v or "").strip()}


def risk_hints() -> set[str]:
    raw = _PAYLOAD.get("risk_hints", [])
    if not isinstance(raw, list):
        return set()
    return {str(v).strip() for v in raw if str(v or "").strip()}


def rule_action_hints() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("rule_action_hints"))


def generic_non_blocking_rules() -> set[str]:
    raw = _PAYLOAD.get("generic_non_blocking_rules", [])
    if not isinstance(raw, list):
        return set()
    return {str(v).strip() for v in raw if str(v or "").strip()}


def cross_cutting_hints() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("cross_cutting_hints"))


def data_flow_phases() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return _keyword_pairs(_PAYLOAD.get("data_flow_phases"))


def data_flow_phase_tie_priority() -> dict[str, int]:
    raw = _PAYLOAD.get("data_flow_phase_tie_priority", {})
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items()}


def flow_stage_definitions() -> tuple[dict[str, Any], ...]:
    raw = _PAYLOAD.get("flow_stage_definitions", [])
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, dict))


def cross_cutting_definitions() -> tuple[dict[str, Any], ...]:
    raw = _PAYLOAD.get("cross_cutting_definitions", [])
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, dict))
