from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_ui_like import is_ui_like_case


def test_is_ui_like_case_detects_display_only_cases() -> None:
    case = {
        "description": "Button icon and copy display",
        "expected_result": "button title and icon display correctly",
        "steps": ["1. open settings"],
    }

    assert is_ui_like_case(case, {}) is True


def test_is_ui_like_case_uses_alias_fields() -> None:
    case = {
        "title": "Button icon and copy display",
        "expectedResult": "button title and icon display correctly",
        "testSteps": ["1. open settings"],
    }

    assert is_ui_like_case(case, {}) is True


def test_is_ui_like_case_allows_short_pseudo_flow_without_behavior_depth() -> None:
    case = {
        "description": "点击按钮展示文案",
        "expected_result": "按钮文案显示正确",
        "steps": ["1. 点击按钮"],
    }

    assert is_ui_like_case(case, {}) is True


def test_is_ui_like_case_excludes_cases_with_coverage_value_or_risk_words() -> None:
    case = {
        "description": "Button display",
        "expected_result": "button display correctly",
        "steps": ["1. open page"],
    }

    assert is_ui_like_case(case, {"missing_rule_hits": ["REQ-1"]}) is False
    assert is_ui_like_case({**case, "description": "权限按钮显示"}, {}) is False


def test_is_ui_like_case_excludes_state_depth_and_guard_sequences() -> None:
    assert (
        is_ui_like_case(
            {
                "description": "button display after resume",
                "expected_result": "context preserved and display is consistent",
                "steps": ["1. interrupt flow", "2. resume flow"],
            },
            {},
        )
        is False
    )
    assert (
        is_ui_like_case(
            {
                "description": "按钮展示",
                "expected_result": "display is correct",
                "steps": ["1. 返回列表", "2. 再进入详情"],
            },
            {},
        )
        is False
    )


def test_is_ui_like_case_excludes_score_profile_flow_signals() -> None:
    case = {
        "description": "Button display",
        "expected_result": "button display correctly",
        "steps": ["1. open page"],
    }

    assert is_ui_like_case(case, {"reasons": ["main_workflow_hit"]}) is False
    assert is_ui_like_case(case, {"reuse_risk_hit": True}) is False
