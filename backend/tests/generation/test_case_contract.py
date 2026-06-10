from __future__ import annotations

from modules.testing.test_generation_components.postprocess.case_contract import (
    merge_contract_quality_gate,
    project_persistable_cases,
    summarize_persistable_case_contract,
)


def test_project_persistable_cases_preserves_priority_and_execution_fields() -> None:
    result = project_persistable_cases(
        [
            {
                "id": "TC-001",
                "description": "create plan",
                "test_module": "plan",
                "preconditions": ["logged in"],
                "steps": ["open page"],
                "test_input": "valid plan",
                "expected_result": "plan is saved",
                "priority": "P1",
                "priority_final": "P0",
                "priority_decision_source": "execution_plan_final_priority",
                "execution_group": "main_smoke",
                "role": "student",
                "session_key": "student_session",
                "workflow_transition": {
                    "workflow_id": "schedule_flow",
                    "source_state": "draft",
                    "action": "save",
                    "target_state": "saved",
                    "path_type": "positive",
                    "blocking": False,
                    "destructive": False,
                    "can_advance_main_flow": True,
                    "stage_kind": "commit",
                },
            }
        ]
    )

    assert result[0]["priority"] == "P0"
    assert result[0]["priority_final"] == "P0"
    assert list(result[0].keys())[:9] == [
        "id",
        "description",
        "test_module",
        "preconditions",
        "steps",
        "test_input",
        "expected_result",
        "priority",
        "priority_final",
    ]
    assert result[0]["workflow_id"] == "schedule_flow"
    assert result[0]["source_state"] == "draft"
    assert result[0]["target_state"] == "saved"
    assert result[0]["execution_group"] == "main_smoke"
    assert result[0]["main_chain_stage_kind"] == "commit"
    assert result[0]["role"] == "student"
    assert result[0]["session_key"] == "student_session"
    assert "priority_decision_source" not in result[0]
    assert "workflow_transition" not in result[0]


def test_contract_summary_blocks_reasoning_leakage_in_test_input() -> None:
    summary = summarize_persistable_case_contract(
        [
            {
                "id": "TC-001",
                "description": "create plan",
                "test_module": "plan",
                "preconditions": ["logged in"],
                "steps": ["open page"],
                "test_input": "need product confirm before assuming here",
                "expected_result": "plan is saved",
                "priority": "P1",
                "priority_final": "P1",
            }
        ]
    )

    assert summary["passed"] is False
    assert summary["persistable_reasoning_leakage_case_ids"] == ["TC-001"]
    assert "persistable_reasoning_leakage_count=1" in summary["failed_checks"]


def test_contract_summary_keeps_business_terms_in_public_fields() -> None:
    summary = summarize_persistable_case_contract(
        [
            {
                "id": "TC-001",
                "description": "AI模型针对错题生成可能的追问方向",
                "test_module": "AI提问逻辑",
                "preconditions": ["学生已进入讲错题页面"],
                "steps": ["提交错题答案", "查看AI追问内容"],
                "test_input": "题目内容或类似错因数据",
                "expected_result": "AI展示追问内容，并包含与错因相关的知识点提示",
                "priority": "P1",
                "priority_final": "P1",
            }
        ]
    )

    assert summary["passed"] is True
    assert summary["persistable_reasoning_leakage_case_ids"] == []


def test_contract_summary_reports_missing_priority_final_before_projection() -> None:
    summary = summarize_persistable_case_contract(
        [
            {
                "id": "TC-001",
                "description": "create plan",
                "test_module": "plan",
                "preconditions": ["logged in"],
                "steps": ["open page"],
                "test_input": "valid plan",
                "expected_result": "plan is saved",
                "priority": "P1",
            }
        ]
    )

    assert summary["passed"] is False
    assert summary["persistable_priority_final_invalid_case_ids"] == ["TC-001"]
    assert "persistable_priority_final_invalid_count=1" in summary["failed_checks"]


def test_contract_quality_gate_merge_preserves_existing_failures() -> None:
    merged = merge_contract_quality_gate(
        {"passed": False, "failed_checks": ["priority_final_null_count=1"], "metrics": {"final_count": 1}},
        {
            "passed": False,
            "failed_checks": ["persistable_reasoning_leakage_count=1"],
            "metrics": {"persistable_reasoning_leakage_count": 1},
            "persistable_reasoning_leakage_case_ids": ["TC-001"],
        },
    )

    assert merged["passed"] is False
    assert merged["failed_checks"] == [
        "priority_final_null_count=1",
        "persistable_reasoning_leakage_count=1",
    ]
    assert merged["metrics"]["final_count"] == 1
    assert merged["metrics"]["persistable_reasoning_leakage_count"] == 1
    assert merged["persistable_reasoning_leakage_case_ids"] == ["TC-001"]
