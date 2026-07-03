"""Priority/P0 anchor configuration loader.

Reads optional postprocess_priority_config_data.json and exposes keyword lists,
scoring deltas, and anchor rules. If the file is absent, callers receive empty
configuration so generation can proceed without a bundled domain template.
"""

from __future__ import annotations

import json
from pathlib import Path

from .postprocess_priority_defaults import (
    _DEFAULT_BEHAVIOR_DEPTH_TOKENS,
    _DEFAULT_BLOCKING_TOKENS,
    _DEFAULT_CROSS_PAGE_FLOW_TOKENS,
    _DEFAULT_FOCUS_BOUNDARY_TOKENS,
    _DEFAULT_FOCUS_EXCEPTION_TOKENS,
    _DEFAULT_FOCUS_STATE_TOKENS,
    _DEFAULT_HIGH_FREQUENCY_TOKENS,
    _DEFAULT_IMPORTANT_CONTENT_LIMIT_TOKENS,
    _DEFAULT_IMPORTANT_DETAIL_NAVIGATION_TOKENS,
    _DEFAULT_IMPORTANT_NON_BLOCKING_TOKENS,
    _DEFAULT_IMPORTANT_REGRESSION_TOKENS,
    _DEFAULT_INVALID_CASE_QUALITY_MARKERS,
    _DEFAULT_MAIN_WORKFLOW_TOKENS,
    _DEFAULT_P0_CORE_TOKENS,
    _DEFAULT_P0_CRITICAL_FAMILIES,
    _DEFAULT_P0_ESSAY_DOMAIN_NEGATIVE_TOKENS,
    _DEFAULT_P0_ESSAY_DOMAIN_POSITIVE_TOKENS,
    _DEFAULT_P0_ESSAY_DOMAIN_PRIMARY_TOKENS,
    _DEFAULT_P0_ESSAY_EXCLUSION_TOKENS,
    _DEFAULT_P0_KEYWORDS,
    _DEFAULT_P0_LOW_VALUE_TOKENS,
    _DEFAULT_P1_KEYWORDS,
    _DEFAULT_P2_KEYWORDS,
    _DEFAULT_PREFERRED_PATTERN_CATEGORIES,
    _DEFAULT_PREFERRED_PATTERN_TEXT_TOKENS,
    _DEFAULT_QUALITY_CHECK_FIELDS,
    _DEFAULT_REASONING_LEAKAGE_SIGNALS,
    _DEFAULT_REUSE_RISK_TOKENS,
    _DEFAULT_SCORING_DELTAS,
    _DEFAULT_SEVERE_DATA_RISK_TOKENS,
    _DEFAULT_SEVERE_SECURITY_RISK_TOKENS,
    _DEFAULT_STATE_GUARD_TOKENS,
    _DEFAULT_STATE_TRANSITION_TOKENS,
    _DEFAULT_STRONG_P0_AI_SCORING_TOKENS,
    _DEFAULT_STRONG_P0_PAYMENT_GATE_TOKENS,
    _DEFAULT_STRONG_P0_SUBMIT_REPORT_TOKENS,
    _DEFAULT_STRONG_P0_WEEK_BOUNDARY_TOKENS,
    _DEFAULT_STRONG_P0_WRONG_COLLECTION_TOKENS,
    _DEFAULT_UI_KEYWORDS,
    _DEFAULT_UI_RISK_WORDS,
    _DEFAULT_UNCERTAIN_REQUIREMENT_SIGNALS,
    _DEFAULT_USABILITY_DEGRADED_TOKENS,
)

_CONFIG_PATH = Path(__file__).with_name("postprocess_priority_config_data.json")


def _load_payload() -> dict[str, object]:
    if not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict):
        raise ValueError("priority config data must be a JSON object")
    return payload


def _string_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(str(v) for v in values if str(v or "").strip())


def _keyword_pair_tuple(values: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
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


def _int_dict(values: object) -> dict[str, int]:
    if not isinstance(values, dict):
        return {}
    return {str(k): int(v) for k, v in values.items()}


_PAYLOAD = _load_payload()

# -- P0 anchor rules (result_postprocess.py) --

def p0_critical_families() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return _keyword_pair_tuple(_PAYLOAD.get("p0_critical_families")) or _DEFAULT_P0_CRITICAL_FAMILIES


def p0_core_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_core_tokens")) or _DEFAULT_P0_CORE_TOKENS


def p0_low_value_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_low_value_tokens")) or _DEFAULT_P0_LOW_VALUE_TOKENS


def p0_essay_domain_positive_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_essay_domain_positive_tokens")) or _DEFAULT_P0_ESSAY_DOMAIN_POSITIVE_TOKENS


def p0_essay_domain_negative_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_essay_domain_negative_tokens")) or _DEFAULT_P0_ESSAY_DOMAIN_NEGATIVE_TOKENS


def p0_essay_exclusion_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_essay_exclusion_tokens")) or _DEFAULT_P0_ESSAY_EXCLUSION_TOKENS


def p0_essay_domain_primary_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_essay_domain_primary_tokens")) or _DEFAULT_P0_ESSAY_DOMAIN_PRIMARY_TOKENS


# -- Priority semantics (result_postprocess_priority_semantics.py) --

def uncertain_requirement_signals() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("uncertain_requirement_signals")) or _DEFAULT_UNCERTAIN_REQUIREMENT_SIGNALS


def p0_keywords() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p0_keywords")) or _DEFAULT_P0_KEYWORDS


def p1_keywords() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p1_keywords")) or _DEFAULT_P1_KEYWORDS


def p2_keywords() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("p2_keywords")) or _DEFAULT_P2_KEYWORDS


def strong_p0_payment_gate_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_payment_gate_tokens")) or _DEFAULT_STRONG_P0_PAYMENT_GATE_TOKENS


def strong_p0_ai_scoring_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_ai_scoring_tokens")) or _DEFAULT_STRONG_P0_AI_SCORING_TOKENS


def strong_p0_wrong_collection_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_wrong_collection_tokens")) or _DEFAULT_STRONG_P0_WRONG_COLLECTION_TOKENS


def strong_p0_week_boundary_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_week_boundary_tokens")) or _DEFAULT_STRONG_P0_WEEK_BOUNDARY_TOKENS


def strong_p0_submit_report_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("strong_p0_submit_report_tokens")) or _DEFAULT_STRONG_P0_SUBMIT_REPORT_TOKENS


# -- Priority scoring helpers (result_postprocess_priority_semantics_split_helpers.py) --

def ui_keywords() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("ui_keywords")) or _DEFAULT_UI_KEYWORDS


def ui_risk_words() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("ui_risk_words")) or _DEFAULT_UI_RISK_WORDS


def main_workflow_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("main_workflow_tokens")) or _DEFAULT_MAIN_WORKFLOW_TOKENS


def cross_page_flow_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("cross_page_flow_tokens")) or _DEFAULT_CROSS_PAGE_FLOW_TOKENS


def state_transition_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("state_transition_tokens")) or _DEFAULT_STATE_TRANSITION_TOKENS


def preferred_pattern_text_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("preferred_pattern_text_tokens")) or _DEFAULT_PREFERRED_PATTERN_TEXT_TOKENS


def preferred_pattern_categories() -> set[str]:
    raw = _PAYLOAD.get("preferred_pattern_categories", [])
    if not isinstance(raw, list):
        return set(_DEFAULT_PREFERRED_PATTERN_CATEGORIES)
    configured = {str(v).strip().lower() for v in raw if str(v or "").strip()}
    return configured or set(_DEFAULT_PREFERRED_PATTERN_CATEGORIES)


def reuse_risk_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("reuse_risk_tokens")) or _DEFAULT_REUSE_RISK_TOKENS


def blocking_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("blocking_tokens")) or _DEFAULT_BLOCKING_TOKENS


def severe_data_risk_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("severe_data_risk_tokens")) or _DEFAULT_SEVERE_DATA_RISK_TOKENS


def severe_security_risk_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("severe_security_risk_tokens")) or _DEFAULT_SEVERE_SECURITY_RISK_TOKENS


def behavior_depth_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("behavior_depth_tokens")) or _DEFAULT_BEHAVIOR_DEPTH_TOKENS


def state_guard_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("state_guard_tokens")) or _DEFAULT_STATE_GUARD_TOKENS


def important_non_blocking_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("important_non_blocking_tokens")) or _DEFAULT_IMPORTANT_NON_BLOCKING_TOKENS


def high_frequency_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("high_frequency_tokens")) or _DEFAULT_HIGH_FREQUENCY_TOKENS


def important_detail_navigation_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("important_detail_navigation_tokens")) or _DEFAULT_IMPORTANT_DETAIL_NAVIGATION_TOKENS


def important_content_limit_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("important_content_limit_tokens")) or _DEFAULT_IMPORTANT_CONTENT_LIMIT_TOKENS


def usability_degraded_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("usability_degraded_tokens")) or _DEFAULT_USABILITY_DEGRADED_TOKENS


def important_regression_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("important_regression_tokens")) or _DEFAULT_IMPORTANT_REGRESSION_TOKENS


def focus_boundary_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("focus_boundary_tokens")) or _DEFAULT_FOCUS_BOUNDARY_TOKENS


def focus_exception_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("focus_exception_tokens")) or _DEFAULT_FOCUS_EXCEPTION_TOKENS


def focus_state_tokens() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("focus_state_tokens")) or _DEFAULT_FOCUS_STATE_TOKENS


# -- Expected-result quality rules --

def reasoning_leakage_signals() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("reasoning_leakage_signals")) or _DEFAULT_REASONING_LEAKAGE_SIGNALS


def invalid_case_quality_markers() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("invalid_case_quality_markers")) or _DEFAULT_INVALID_CASE_QUALITY_MARKERS


def quality_check_fields() -> tuple[str, ...]:
    return _string_tuple(_PAYLOAD.get("quality_check_fields")) or _DEFAULT_QUALITY_CHECK_FIELDS


# -- Scoring deltas --

def scoring_deltas() -> dict[str, int]:
    return {**_DEFAULT_SCORING_DELTAS, **_int_dict(_PAYLOAD.get("scoring_deltas"))}
