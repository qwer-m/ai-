from __future__ import annotations

from modules.test_generation_components.execution.execution_suite_history import (
    build_execution_suite_from_generated_result,
    hydrate_cases_from_execution_suite,
)


def test_hydrate_cases_from_execution_suite_fills_append_gate_metadata() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "用户提交登录表单",
            "test_module": "登录",
            "preconditions": ["已打开登录页"],
            "steps": ["输入账号密码", "点击登录"],
            "test_input": "账号密码正确",
            "expected_result": "登录成功",
            "priority": "P1",
        }
    ]
    suite = {
        "suites": [
            {
                "suite_id": "main_smoke_chain",
                "execution_group": "main_smoke",
                "cases": [
                    {
                        "case_id": "TC-001",
                        "suite_order": 1,
                        "execution_sequence": 1,
                        "priority": "P1",
                        "role": "login_submit",
                        "session_key": "user",
                    }
                ],
            }
        ]
    }

    hydrated = hydrate_cases_from_execution_suite(cases, suite)

    assert hydrated[0]["priority_final"] == "P1"
    assert hydrated[0]["execution_group"] == "main_smoke"
    assert hydrated[0]["execution_sequence"] == 1
    assert hydrated[0]["role"] == "login_submit"
    assert hydrated[0]["session_key"] == "user"
    assert hydrated[0]["workflow_id"] == "main_smoke_chain"
    assert hydrated[0]["workflow_transition"]["workflow_id"] == "main_smoke_chain"
    assert hydrated[0]["workflow_transition"]["can_advance_main_flow"] is True


def test_build_execution_suite_from_generated_result_uses_compact_priority_metadata() -> None:
    generated_result = """
    [
      {
        "id": "TC-001",
        "description": "用户提交登录表单",
        "test_module": "登录",
        "preconditions": ["已打开登录页"],
        "steps": ["输入账号密码", "点击登录"],
        "test_input": "账号密码正确",
        "expected_result": "登录成功",
        "priority": "P1"
      }
    ]
    """
    compact_suite = {
        "suites": [
            {
                "suite_id": "main_smoke_chain",
                "execution_group": "main_smoke",
                "cases": [
                    {
                        "case_id": "TC-001",
                        "suite_order": 1,
                        "priority": "P1",
                        "role": "login_submit",
                        "session_key": "user",
                    }
                ],
            }
        ]
    }

    suite = build_execution_suite_from_generated_result(generated_result, suite_hint=compact_suite)

    assert suite["metadata_quality"]["complete_execution_metadata"] is True
    assert suite["suites"][0]["cases"][0]["priority"] == "P1"
    assert suite["suites"][0]["cases"][0]["role"] == "login_submit"
