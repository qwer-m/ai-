import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    deduplicate_test_cases,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)
from modules.test_generation_components.legacy.multi_pass_pipeline import run_multi_pass_generation


class _FakeBizClient:
    def generate_response(self, requirement: str, prompt: str, db: Any = None, **kwargs) -> str:
        if "BIZ=org_close_rule" in prompt and "MULTI-PASS STAGE: PRIMARY" in prompt:
            return """
            [{"id":"TC-C1","description":"关闭机构主流程","test_module":"机构关闭","preconditions":[],"steps":["提交关闭"],"test_input":"机构=A","expected_result":"关闭成功","priority":"P0"}]
            """
        if "BIZ=org_open_rule" in prompt and "MULTI-PASS STAGE: PRIMARY" in prompt:
            return """
            [{"id":"TC-O1","description":"开通机构主流程","test_module":"机构开通","preconditions":[],"steps":["提交开通"],"test_input":"机构=B","expected_result":"开通成功","priority":"P1"}]
            """
        if "GAP FILL" in prompt:
            return "[]"
        return """
        [{"id":"TC-R1","description":"候选保持原样","test_module":"通用","preconditions":[],"steps":["检查"],"test_input":"N/A","expected_result":"通过","priority":"P2"}]
        """


def test_biz_key_multi_pass_runs_per_biz_stage() -> None:
    client = _FakeBizClient()
    prompt_context = {
        "biz_key_order": ["org_close_rule", "org_open_rule"],
        "context_by_biz": {
            "org_close_rule": {
                "requirement_context": "REQ-023 关闭机构前必须校验余额为0",
                "testcase_context": "close cases",
                "supplement_context": "close supplement",
            },
            "org_open_rule": {
                "requirement_context": "REQ-101 开通机构前需完成审批",
                "testcase_context": "open cases",
                "supplement_context": "open supplement",
            },
        },
        "requirement_context": "REQ-023 ... REQ-101 ...",
    }

    result = run_multi_pass_generation(
        client=client,
        requirement="机构开关规则",
        db=None,
        base_prompt="BASE",
        requirement_context="REQ-023 ... REQ-101 ...",
        current_biz_key="org_close_rule",
        expected_count=2,
        start_id=1,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        multi_pass=True,
        generation_mode="biz_key_multi_pass",
        prompt_context=prompt_context,
        build_base_prompt_fn=lambda req_ctx, tc_ctx, sup_ctx, biz_key: f"BIZ={biz_key}\nREQ={req_ctx}\nTC={tc_ctx}\nSUP={sup_ctx}",
    )

    assert len(result["final_cases"]) == 2
    logs = result["stage_logs"]
    assert logs[0]["kind"] == "generation_mode"
    assert logs[0]["mode"] == "biz_key_multi_pass"
    biz_stage_logs = [x for x in logs if x.get("kind") == "biz_key_pass_stage"]
    assert any(x.get("biz_key") == "org_close_rule" and x.get("stage") == "primary_generation" for x in biz_stage_logs)
    assert any(x.get("biz_key") == "org_open_rule" and x.get("stage") == "primary_generation" for x in biz_stage_logs)
    assert any(x.get("biz_key") == "org_close_rule" and x.get("stage") == "decide_continue_or_stop" for x in biz_stage_logs)
    assert any(x.get("biz_key") == "org_open_rule" and x.get("stage") == "decide_continue_or_stop" for x in biz_stage_logs)


def test_biz_key_multi_pass_degrades_to_multi_pass_when_single_biz() -> None:
    client = _FakeBizClient()
    result = run_multi_pass_generation(
        client=client,
        requirement="机构关闭规则",
        db=None,
        base_prompt="BIZ=org_close_rule",
        requirement_context="REQ-023 关闭机构前必须校验余额为0",
        current_biz_key="org_close_rule",
        expected_count=1,
        start_id=1,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        multi_pass=True,
        generation_mode="biz_key_multi_pass",
        prompt_context={"biz_key_order": ["org_close_rule"]},
    )
    assert result["stage_logs"][0]["mode"] == "multi_pass"
