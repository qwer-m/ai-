from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_ui_like import is_ui_like_case


def test_is_ui_like_case_detects_display_only_cases() -> None:
    case = {
        "description": "Button icon and copy display",
        "expected_result": "button title and icon display correctly",
        "steps": ["open settings"],
    }

    assert is_ui_like_case(case, {}) is True


def test_is_ui_like_case_uses_alias_fields() -> None:
    case = {
        "title": "Button icon and copy display",
        "expectedResult": "button title and icon display correctly",
        "testSteps": ["open settings"],
    }

    assert is_ui_like_case(case, {}) is True


def test_is_ui_like_case_allows_short_pseudo_flow_without_behavior_depth() -> None:
    case = {
        "description": "Click button to show copy",
        "expected_result": "button copy is displayed",
        "steps": ["click button"],
    }

    assert is_ui_like_case(case, {}) is True


def test_is_ui_like_case_excludes_cases_with_coverage_value_or_risk_words() -> None:
    case = {
        "description": "Button display",
        "expected_result": "button display correctly",
        "steps": ["open page"],
    }

    assert is_ui_like_case(case, {"missing_rule_hits": ["REQ-1"]}) is False
    assert is_ui_like_case({**case, "description": "permission button display"}, {}) is False


def test_is_ui_like_case_excludes_state_depth_and_guard_sequences() -> None:
    case = {
        "description": "button display after resume",
        "expected_result": "context is preserved and display is consistent",
        "steps": ["interrupt flow", "resume flow"],
    }

    assert is_ui_like_case(case, {}) is False


def test_is_ui_like_case_excludes_score_profile_flow_signals() -> None:
    case = {
        "description": "Button display",
        "expected_result": "button display correctly",
        "steps": ["open page"],
    }

    assert is_ui_like_case(case, {"reasons": ["main_workflow_hit"]}) is False
    assert is_ui_like_case(case, {"reuse_risk_hit": True}) is False
