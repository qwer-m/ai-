import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.coverage.coverage_analyzer import analyze_coverage
from modules.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    deduplicate_test_cases,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)
from modules.test_generation_components.legacy.multi_pass_pipeline import run_multi_pass_generation
from modules.test_generation_components.prompting.structured_context import build_structured_prompt_context


class _FakeMultiPassClient:
    def generate_response(self, requirement: str, prompt: str, db: Any = None, **kwargs) -> str:
        if "MULTI-PASS STAGE: PRIMARY" in prompt:
            return """
            [
              {"id":"TC-001","description":"验证关闭机构主流程","test_module":"机构关闭","preconditions":[],"steps":["进入关闭页面","提交关闭"],"test_input":"机构=A","expected_result":"关闭成功","priority":"P0"}
            ]
            """
        if "GAP FILL" in prompt:
            return """
            [
              {"id":"TC-002","description":"验证余额边界值为0可关闭","test_module":"机构关闭","preconditions":[],"steps":["余额设为0","提交关闭"],"test_input":"余额=0","expected_result":"关闭成功","priority":"P0"}
            ]
            """
        return """
        [
          {"id":"TC-001","description":"验证关闭机构主流程","test_module":"机构关闭","preconditions":[],"steps":["进入关闭页面","提交关闭"],"test_input":"机构=A","expected_result":"关闭成功","priority":"P0"},
          {"id":"TC-002","description":"验证余额边界值为0可关闭","test_module":"机构关闭","preconditions":[],"steps":["余额设为0","提交关闭"],"test_input":"余额=0","expected_result":"关闭成功","priority":"P0"}
        ]
        """


def test_multi_pass_pipeline_runs_quality_coverage_loop() -> None:
    client = _FakeMultiPassClient()
    requirement = "REQ-001 关闭机构前需要校验余额。REQ-002 余额边界值为0时允许关闭。"
    result = run_multi_pass_generation(
        client=client,
        requirement=requirement,
        db=None,
        base_prompt="BASE_PROMPT",
        requirement_context=requirement,
        current_biz_key="org_close_rule",
        expected_count=2,
        start_id=1,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
    )

    assert isinstance(result.get("final_cases"), list)
    assert len(result["final_cases"]) == 1
    stage_logs = result.get("stage_logs") or []
    assert stage_logs[0]["kind"] == "generation_mode"
    stages = [item.get("stage") for item in stage_logs if item.get("kind") == "generation_stage"]
    assert "primary_generation" in stages
    assert "evaluate_quality" in stages
    assert "evaluate_coverage" in stages
    assert "decide_continue_or_stop" in stages
    assert result["coverage"]["kind"] == "coverage_check"

def test_coverage_analyzer_returns_rule_diagnostics() -> None:
    requirement = "REQ-001 登录成功流程。REQ-002 登录失败超过5次触发异常提示。REQ-003 用户名长度边界值20。"
    coverage = analyze_coverage(
        requirement,
        [
            {
                "id": "TC-001",
                "description": "验证登录成功",
                "test_module": "登录",
                "preconditions": [],
                "steps": ["输入正确账号密码", "点击登录"],
                "test_input": "合法账号",
                "expected_result": "登录成功",
                "priority": "P0",
            }
        ],
    )
    assert coverage["total_rules"] >= 3
    assert isinstance(coverage["rule_diagnostics"], list)
    assert any(item.get("rule_id") == "REQ-002" for item in coverage["rule_diagnostics"])
    assert "boundary" in coverage["missing_types"]
    assert "exception" in coverage["missing_types"]


def test_only_current_biz_not_broken_in_multi_pass_stage() -> None:
    context = build_structured_prompt_context(
        requirement="机构关闭规则",
        kb_context="",
        rag_result=None,
        existing_cases=[
            {"id": "TC-001", "biz_key": "org_close_rule", "test_module": "机构关闭", "priority": "P0", "description": "关闭流程"},
            {"id": "TC-002", "biz_key": "org_open_rule", "test_module": "机构开通", "priority": "P1", "description": "开通流程"},
        ],
        current_biz_key="org_close_rule",
        only_current_biz=True,
    )
    assert "org_close_rule" in context["testcase_context"]
    assert "org_open_rule" not in context["testcase_context"]
