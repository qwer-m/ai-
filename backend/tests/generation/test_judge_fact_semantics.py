from __future__ import annotations

import json

from modules.testing.test_generation_components.judge import judge_fact_rules
from modules.testing.test_generation_components.judge.test_case_judge import (
    _merge_fact_profile_semantics,
    normalize_requirement_semantics_context,
)


def test_normalize_requirement_semantics_context_keeps_real_semantic_fields() -> None:
    payload = {
        "confirmed_facts": ["活动结束后入口关闭", "活动结束后入口关闭", ""],
        "scoped_rules": ["历史周补学周日24:00后仅可查看"],
        "forbidden_facts": ["禁止删除课程"],
        "pending_items": ["错题空状态文案待确认"],
        "reuse_declarations": ["复用首页课程入口"],
        "hard_flow_constraints": ["登录->进入课程->开始学习"],
        "reuse_risks": ["不要复用打印弹窗"],
        "ignored_extra_field": ["不应进入判定语义"],
    }

    normalized = normalize_requirement_semantics_context(json.dumps(payload, ensure_ascii=False))

    assert normalized == {
        "confirmed_facts": ["活动结束后入口关闭"],
        "scoped_rules": ["历史周补学周日24:00后仅可查看"],
        "forbidden_facts": ["禁止删除课程"],
        "pending_items": ["错题空状态文案待确认"],
        "reuse_declarations": ["复用首页课程入口"],
        "hard_flow_constraints": ["登录->进入课程->开始学习"],
        "reuse_risks": ["不要复用打印弹窗"],
    }


def test_merge_fact_profile_semantics_dedupes_and_protects_confirmed_facts() -> None:
    semantics = normalize_requirement_semantics_context(
        {
            "confirmed_facts": ["活动结束后入口关闭"],
            "scoped_rules": ["历史周补学周日24:00后仅可查看"],
            "forbidden_facts": ["活动结束后入口关闭", "错误入口仍可访问"],
            "pending_items": ["补学次数限制待确认"],
            "hard_flow_constraints": ["登录->进入课程->开始学习"],
        }
    )
    control_state = {
        "source_meta": {
            "fact_profile": {
                "confirmed_facts": ["活动结束后入口关闭", "课程入口展示学习进度"],
                "scoped_rules": ["历史周补学周日24:00后仅可查看"],
                "forbidden_facts": ["登录->进入课程->开始学习", "错题入口跳转到打印弹窗"],
                "reuse_risks": ["跨模块复用打印弹窗流程"],
            }
        }
    }

    merged = _merge_fact_profile_semantics(semantics, control_state)

    assert merged["confirmed_facts"] == ["活动结束后入口关闭", "课程入口展示学习进度"]
    assert merged["scoped_rules"] == ["历史周补学周日24:00后仅可查看"]
    assert merged["hard_flow_constraints"] == ["登录->进入课程->开始学习"]
    assert merged["forbidden_facts"] == ["错误入口仍可访问", "错题入口跳转到打印弹窗"]
    assert merged["reuse_risks"] == ["跨模块复用打印弹窗流程"]


def test_judge_fact_rules_facade_reexports_semantic_helpers() -> None:
    assert judge_fact_rules.normalize_requirement_semantics_context is normalize_requirement_semantics_context
    assert judge_fact_rules._merge_fact_profile_semantics is _merge_fact_profile_semantics
