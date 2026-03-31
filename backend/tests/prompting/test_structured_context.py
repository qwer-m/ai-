import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.prompting.structured_context import build_structured_prompt_context


def test_structured_context_groups_requirement_and_supplement_by_biz_key() -> None:
    rag_result = {
        "debug": {
            "final_chunks": [
                {
                    "filename": "close_req.md",
                    "doc_type": "requirement",
                    "biz_key": "org_close_rule",
                    "module": "机构关闭",
                    "chunk_text": "REQ-023: 关闭机构前必须校验余额为0。",
                },
                {
                    "filename": "open_req.md",
                    "doc_type": "requirement",
                    "biz_key": "org_open_rule",
                    "module": "机构开通",
                    "chunk_text": "REQ-101: 开通机构前需完成审批。",
                },
            ]
        }
    }
    output = build_structured_prompt_context(
        requirement="REQ-024: 存在未结算订单时禁止关闭机构。",
        rag_result=rag_result,
        existing_cases=[
            {
                "id": "TC-001",
                "biz_key": "org_close_rule",
                "test_module": "机构关闭",
                "priority": "P0",
                "description": "余额不为0禁止关闭",
            },
            {
                "id": "TC-101",
                "biz_key": "org_open_rule",
                "test_module": "机构开通",
                "priority": "P1",
                "description": "审批通过后允许开通",
            },
        ],
        current_biz_key="org_close_rule",
        only_current_biz=False,
    )

    assert "【Requirements - 按业务分组】" in output["requirement_context"]
    assert "### biz_key: org_close_rule（当前业务）" in output["requirement_context"]
    assert "### biz_key: org_open_rule（参考）" in output["requirement_context"]
    assert "【Supplement - 按业务分组】" in output["supplement_context"]
    assert "### biz_key: org_close_rule（当前业务）" in output["testcase_context"]
    assert "### biz_key: org_open_rule（参考）" in output["testcase_context"]


def test_only_current_biz_keeps_only_current_scope() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-001: 关闭机构需要校验余额。",
        rag_result={
            "debug": {
                "final_chunks": [
                    {
                        "filename": "close_req.md",
                        "doc_type": "requirement",
                        "biz_key": "org_close_rule",
                        "module": "机构关闭",
                        "chunk_text": "REQ-001: 关闭机构需要校验余额。",
                    },
                    {
                        "filename": "open_req.md",
                        "doc_type": "requirement",
                        "biz_key": "org_open_rule",
                        "module": "机构开通",
                        "chunk_text": "REQ-101: 开通机构需审批。",
                    },
                ]
            }
        },
        existing_cases=[
            {
                "id": "TC-001",
                "biz_key": "org_close_rule",
                "test_module": "机构关闭",
                "priority": "P0",
                "description": "关闭主流程",
            },
            {
                "id": "TC-002",
                "biz_key": "org_open_rule",
                "test_module": "机构开通",
                "priority": "P1",
                "description": "开通审批流程",
            },
        ],
        current_biz_key="org_close_rule",
        only_current_biz=True,
    )

    assert "org_open_rule" not in output["testcase_context"]
    assert "org_open_rule" not in output["requirement_context"]
    assert output["biz_key_isolation_log"]["mode"] == "strict_current_only"


def test_missing_fields_fallback_and_degrade_when_current_unknown() -> None:
    output = build_structured_prompt_context(
        requirement="登录失败超过5次触发异常提示。",
        kb_context=(
            "--- Relevant Knowledge: login_spec.md (requirement) ---\n"
            "登录失败超过5次需要图形验证码。\n"
        ),
        existing_cases=[{"description": "字段缺失也要可回退"}],
        current_biz_key="",
        only_current_biz=True,
    )

    assert output["current_biz_key"] == "unknown"
    assert "### biz_key: unknown（当前业务）" in output["testcase_context"]
    assert output["biz_key_isolation_log"]["mode"] == "reference_allowed_current_unknown"
    assert output["biz_key_order"]
