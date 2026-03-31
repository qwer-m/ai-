import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.prompting.prompt_orchestration import (
    build_closed_loop_base_prompt,
    build_gap_fill_prompt,
)


def test_business_isolation_rule_contains_current_biz_key() -> None:
    prompt = build_closed_loop_base_prompt(
        strategy_plan={"system_type": "Web", "impact_scope": "module", "suggested_ratios": {}},
        requirement_context="需求A",
        testcase_context="用例A",
        supplement_context="补充A",
        current_biz_key="org_close_rule",
    )
    assert "BUSINESS ISOLATION RULE" in prompt
    assert "当前生成目标 biz_key: org_close_rule" in prompt


def test_gap_fill_prompt_consumes_coverage_result() -> None:
    prompt = build_gap_fill_prompt(
        requirement_context="REQ-023 关闭机构前必须校验余额为0",
        existing_cases=[
            {
                "id": "TC-001",
                "description": "验证关闭主流程",
                "test_module": "机构关闭",
                "preconditions": [],
                "steps": ["提交关闭"],
                "test_input": "机构=A",
                "expected_result": "关闭成功",
                "priority": "P0",
            }
        ],
        coverage_result={
            "rule_diagnostics": [
                {
                    "rule_id": "REQ-023",
                    "rule_text": "关闭机构前必须校验余额为0",
                    "biz_key": "org_close_rule",
                    "covered": True,
                    "missing_types": ["boundary", "exception"],
                }
            ]
        },
        current_biz_key="org_close_rule",
    )
    assert "REQ-023" in prompt
    assert "missing_types=boundary,exception" in prompt
    assert "只补上述 coverage 缺口" in prompt
