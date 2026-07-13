from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_uncertain_requirement import (
    apply_uncertain_requirement_downgrade,
    enforce_uncertain_priority_floor,
    extract_uncertain_requirement_tokens,
)


def test_extract_uncertain_requirement_tokens_only_uses_uncertain_lines() -> None:
    tokens = extract_uncertain_requirement_tokens(
        "课程排课为核心流程\n课后复习按钮待确认\ncalendar export need discussion"
    )

    assert "课程排课为核心流程" not in tokens
    assert "课后复习按钮" in tokens
    assert {"calendar", "export"}.issubset(tokens)


def test_apply_uncertain_requirement_downgrade_marks_matching_cases_optional() -> None:
    cases = [
        {"description": "课后复习按钮展示", "expected_result": "展示按钮", "priority": "P0"},
        {"description": "课程保存", "expected_result": "保存成功", "priority": "P0"},
    ]

    downgraded = apply_uncertain_requirement_downgrade(
        cases,
        requirement_text="课后复习按钮待确认",
    )

    assert downgraded[0]["priority"] == "P2"
    assert "可选/视配置" in downgraded[0]["expected_result"]
    assert downgraded[1]["priority"] == "P0"
    assert "可选/视配置" not in downgraded[1]["expected_result"]


def test_apply_uncertain_requirement_downgrade_does_not_duplicate_optional_suffix() -> None:
    cases = [{"description": "课后复习按钮", "expected_result": "展示按钮（可选/视配置）", "priority": "P1"}]

    downgraded = apply_uncertain_requirement_downgrade(cases, requirement_text="课后复习按钮待确认")

    assert downgraded[0]["priority"] == "P2"
    assert str(downgraded[0]["expected_result"]).count("可选/视配置") == 1


def test_enforce_uncertain_priority_floor_keeps_optional_cases_at_p2() -> None:
    cases = [
        {"description": "optional", "expected_result": "展示按钮（可选/视配置）", "priority": "P0"},
        {"description": "normal", "expected_result": "保存成功", "priority": "P0"},
    ]

    enforced = enforce_uncertain_priority_floor(cases)

    assert enforced[0]["priority"] == "P2"
    assert enforced[1]["priority"] == "P0"
