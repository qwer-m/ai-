from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_uncertain_requirement import (
    extract_uncertain_requirement_tokens,
    filter_uncertain_requirement_cases,
)


def test_extract_uncertain_requirement_tokens_only_uses_uncertain_lines() -> None:
    tokens = extract_uncertain_requirement_tokens(
        "课程排课为核心流程\n课后复习按钮待确认\ncalendar export need discussion"
    )

    assert "课程排课为核心流程" not in tokens
    assert "课后复习按钮" in tokens
    assert {"calendar", "export"}.issubset(tokens)


def test_filter_uncertain_requirement_cases_rejects_unconfirmed_cases_without_rewriting() -> None:
    cases = [
        {"description": "课后复习按钮展示", "expected_result": "展示按钮", "priority": "P0"},
        {"description": "课程保存", "expected_result": "保存成功", "priority": "P0"},
    ]

    accepted, rejected = filter_uncertain_requirement_cases(
        cases,
        requirement_text="课后复习按钮待确认",
    )

    assert [item["description"] for item in accepted] == ["课程保存"]
    assert [item["description"] for item in rejected] == ["课后复习按钮展示"]
    assert rejected[0]["expected_result"] == "展示按钮"
    assert rejected[0]["priority"] == "P0"


def test_filter_uncertain_requirement_cases_keeps_confirmed_cases_unchanged() -> None:
    cases = [{"description": "课程保存", "expected_result": "保存后状态为已生效", "priority": "P1"}]

    accepted, rejected = filter_uncertain_requirement_cases(cases, requirement_text="课后复习按钮待确认")

    assert accepted == cases
    assert rejected == []
