from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_priority_rebuild import (
    rebuild_priority_by_semantics,
)


def test_rebuild_priority_by_semantics_promotes_registered_p0_group_tokens() -> None:
    cases = [
        {"id": "pay", "priority": "P2", "description": "付费拦截提示", "expected_result": "无法进入"},
        {"id": "ai", "priority": "P2", "description": "auto score result", "expected_result": "score is visible"},
    ]

    rebuilt = rebuild_priority_by_semantics(cases)

    assert [case["priority"] for case in rebuilt] == ["P0", "P0"]


def test_rebuild_priority_by_semantics_promotes_p0_extra_tokens() -> None:
    rebuilt = rebuild_priority_by_semantics(
        [{"id": "main", "priority": "P2", "description": "主流程闭环", "expected_result": "完成"}]
    )

    assert rebuilt[0]["priority"] == "P0"


def test_rebuild_priority_by_semantics_applies_p1_and_p2_fallback_tokens() -> None:
    rebuilt = rebuild_priority_by_semantics(
        [
            {"id": "nav", "priority": "P2", "description": "页面跳转交互", "expected_result": "跳转成功"},
            {"id": "copy", "priority": "P1", "description": "UI文案展示", "expected_result": "文案正确"},
        ]
    )

    assert [case["priority"] for case in rebuilt] == ["P1", "P2"]


def test_rebuild_priority_by_semantics_normalizes_unknown_priority_and_skips_non_dict() -> None:
    rebuilt = rebuild_priority_by_semantics(
        [
            {"id": "plain", "priority": "unknown", "description": "普通用例", "expected_result": "保存成功"},
            "bad",
        ]  # type: ignore[list-item]
    )

    assert rebuilt == [{"id": "plain", "priority": "P2", "description": "普通用例", "expected_result": "保存成功"}]
