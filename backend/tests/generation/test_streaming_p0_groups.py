from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_p0_groups import (
    P0_GROUP_TOKENS,
    covered_p0_groups,
    required_p0_groups_from_requirement,
)


def test_required_p0_groups_uses_specific_week_and_wrong_collection_terms() -> None:
    assert "week_flow" in required_p0_groups_from_requirement("周中学习报告需要生成")
    assert "week_flow" not in required_p0_groups_from_requirement("普通报告展示")
    assert "wrong_collection" in required_p0_groups_from_requirement("错题自动归集到错题本")
    assert "wrong_collection" not in required_p0_groups_from_requirement("普通错题展示")


def test_required_p0_groups_detects_other_registered_groups() -> None:
    required = required_p0_groups_from_requirement("未付费时需要付费拦截，AI判分完成后支持补做历史周")

    assert {"payment_gate", "ai_scoring", "makeup_rule"}.issubset(required)


def test_covered_p0_groups_uses_priority_or_final_decision_state() -> None:
    cases = [
        {"priority": "P0", "description": "付费拦截提示", "expected_result": "拦截"},
        {
            "priority": "P2",
            "priority_final": "P0",
            "priority_decision_state": "decided",
            "description": "AI判分结果",
            "expected_result": "评分结果",
        },
        {
            "priority": "P0",
            "priority_final": "P2",
            "priority_decision_state": "undetermined",
            "description": "错题归集",
            "expected_result": "错题本更新",
        },
    ]

    assert covered_p0_groups(cases) == {"payment_gate", "ai_scoring"}


def test_covered_p0_groups_accepts_alias_fields() -> None:
    cases = [
        {
            "Priority": "P0",
            "testModule": "Payment",
            "title": "paywall blocks unpaid users",
            "expectedResult": "payment gate is shown",
        },
        {
            "Priority": "P2",
            "priorityFinal": "P0",
            "priority_decision_state": "decided",
            "title": "auto score result is generated",
            "expectedResult": "ai scoring result is visible",
        },
    ]

    assert covered_p0_groups(cases) == {"payment_gate", "ai_scoring"}


def test_p0_group_tokens_remain_available_for_priority_rebuild() -> None:
    assert "payment_gate" in P0_GROUP_TOKENS
    assert "paywall" in P0_GROUP_TOKENS["payment_gate"]
