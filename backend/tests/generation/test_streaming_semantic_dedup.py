from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_semantic_dedup import (
    is_semantic_duplicate_case,
    semantic_deduplicate_cases,
)


def test_is_semantic_duplicate_case_matches_identical_semantic_signature() -> None:
    case = {
        "test_module": "课程排课",
        "description": "REQ-1 保存课程",
        "expected_result": "系统提示保存成功",
        "steps": ["1. 填写课程", "2. 保存课程"],
    }

    assert is_semantic_duplicate_case(candidate=case, existed=dict(case))


def test_is_semantic_duplicate_case_uses_similarity_threshold() -> None:
    candidate = {
        "test_module": "课程排课",
        "description": "保存课程",
        "expected_result": "系统提示保存成功",
        "test_input": "课程名称",
        "steps": ["1. 填写课程名称", "2. 保存课程"],
    }
    existed = {
        "test_module": "课程排课",
        "description": "提交课程",
        "expected_result": "系统提示提交成功",
        "test_input": "课程名称",
        "steps": ["1. 填写课程名称", "2. 提交课程"],
    }

    assert is_semantic_duplicate_case(candidate=candidate, existed=existed, threshold=0.2)
    assert not is_semantic_duplicate_case(candidate=candidate, existed=existed, threshold=1.1)


def test_semantic_deduplicate_cases_keeps_higher_priority_duplicate_first() -> None:
    duplicate_low = {
        "id": "low",
        "priority": "P2",
        "test_module": "课程排课",
        "description": "保存课程",
        "expected_result": "系统提示保存成功",
        "steps": ["1. 保存课程"],
    }
    duplicate_high = {
        "id": "high",
        "priority": "P0",
        "test_module": "课程排课",
        "description": "保存课程",
        "expected_result": "系统提示保存成功",
        "steps": ["1. 保存课程"],
    }
    distinct = {
        "id": "distinct",
        "priority": "P1",
        "test_module": "课程排课",
        "description": "删除课程",
        "expected_result": "列表不再显示该课程",
        "steps": ["1. 删除课程"],
    }

    kept, dropped = semantic_deduplicate_cases([duplicate_low, distinct, duplicate_high])

    assert dropped == 1
    assert [case["id"] for case in kept] == ["high", "distinct"]
