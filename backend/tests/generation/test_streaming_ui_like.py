from __future__ import annotations

from modules.test_generation_components.postprocess import streaming_ui_like
from modules.test_generation_components.postprocess.streaming_ui_like import (
    apply_ui_like_ratio_postprocess_cap,
    is_ui_like_case,
)


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


def test_apply_ui_like_ratio_postprocess_cap_removes_lowest_value_ui_cases(monkeypatch) -> None:
    cases = [
        {"id": "TC-001", "description": "ui one", "priority": "P2"},
        {"id": "TC-002", "description": "ui two", "priority": "P2"},
        {"id": "TC-003", "description": "ui three", "priority": "P2"},
        {"id": "TC-004", "description": "business", "priority": "P1"},
        {"id": "TC-005", "description": "business", "priority": "P2"},
    ]

    def _score(case: dict) -> dict:
        case_id = str(case.get("id") or "")
        return {
            "ui_like_case": case_id in {"TC-001", "TC-002", "TC-003"},
            "focus_score": {"TC-001": 1, "TC-002": 2, "TC-003": 3}.get(case_id, 5),
            "coverage_gain_score": 0,
        }

    monkeypatch.setattr(streaming_ui_like, "score_case_priority", _score)

    filtered, drop_count = apply_ui_like_ratio_postprocess_cap(
        cases,
        forbidden_patterns_active=True,
        focus_score_fn=lambda _case: 9,
        ui_max_ratio=0.40,
        ui_min_keep=2,
    )

    assert drop_count == 1
    assert [case["id"] for case in filtered] == ["TC-002", "TC-003", "TC-004", "TC-005"]


def test_apply_ui_like_ratio_postprocess_cap_preserves_protected_ui_cases(monkeypatch) -> None:
    cases = [
        {"id": "TC-001", "priority": "P2"},
        {"id": "TC-002", "priority": "P2"},
        {"id": "TC-003", "priority": "P2"},
        {"id": "TC-004", "priority": "P2"},
    ]

    def _score(case: dict) -> dict:
        case_id = str(case.get("id") or "")
        return {
            "ui_like_case": True,
            "focus_score": {"TC-001": 0, "TC-002": 1, "TC-003": 2, "TC-004": 3}.get(case_id, 5),
            "core_rule_hits": ["REQ-1"] if case_id == "TC-001" else [],
            "reasons": ["main_workflow_hit"] if case_id == "TC-002" else [],
        }

    monkeypatch.setattr(streaming_ui_like, "score_case_priority", _score)

    filtered, drop_count = apply_ui_like_ratio_postprocess_cap(
        cases,
        forbidden_patterns_active=True,
        focus_score_fn=lambda _case: 9,
        ui_max_ratio=0.25,
        ui_min_keep=1,
    )

    assert drop_count == 2
    assert [case["id"] for case in filtered] == ["TC-001", "TC-002"]


def test_apply_ui_like_ratio_postprocess_cap_noops_when_forbidden_patterns_disabled(monkeypatch) -> None:
    calls = 0

    def _score(_case: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"ui_like_case": True}

    monkeypatch.setattr(streaming_ui_like, "score_case_priority", _score)
    cases = [{"id": "TC-001"}, {"id": "TC-002"}]

    filtered, drop_count = apply_ui_like_ratio_postprocess_cap(
        cases,
        forbidden_patterns_active=False,
        focus_score_fn=lambda _case: 0,
    )

    assert filtered == cases
    assert filtered is not cases
    assert drop_count == 0
    assert calls == 0
