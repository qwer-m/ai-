import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.coverage.coverage_analyzer import analyze_coverage


def test_rule_level_diagnostics_contains_missing_types() -> None:
    requirement_context = """
【Requirements - 按业务分组】

### biz_key: org_close_rule（当前业务）

* REQ-023: 关闭机构前必须校验余额为0
* REQ-024: 存在未结算订单时禁止关闭
* REQ-025: 关闭失败时返回异常提示
"""
    cases = [
        {
            "id": "TC-001",
            "description": "验证关闭机构主流程",
            "test_module": "机构关闭",
            "preconditions": [],
            "steps": ["余额为0", "提交关闭"],
            "test_input": "机构=A",
            "expected_result": "关闭成功",
            "priority": "P0",
        }
    ]

    result = analyze_coverage(requirement_context, cases)
    assert result["total_rules"] >= 3
    assert "REQ-023" in result["covered_rules"]
    assert "REQ-024" in result["missing_rules"] or "REQ-025" in result["missing_rules"]
    assert isinstance(result["rule_diagnostics"], list)
    assert any(item["rule_id"] == "REQ-025" and "exception" in item["missing_types"] for item in result["rule_diagnostics"])


def test_rule_match_supports_req_id_and_sentence() -> None:
    requirement = "REQ-100 用户名长度边界值20。REQ-101 登录失败超过5次触发异常提示。"
    cases = [
        {
            "id": "TC-100",
            "description": "验证REQ-100 用户名最大长度边界",
            "test_module": "登录",
            "preconditions": [],
            "steps": ["输入长度20用户名"],
            "test_input": "username=20chars",
            "expected_result": "提交成功",
            "priority": "P1",
        }
    ]
    result = analyze_coverage(requirement, cases)
    assert "REQ-100" in result["covered_rules"]
    assert "REQ-101" in result["missing_rules"]
    assert 0 <= result["coverage_rate"] <= 1
