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

    assert "[Requirements - grouped by biz_key]" in output["requirement_context"]
    assert "### biz_key: org_close_rule (当前业务)" in output["requirement_context"]
    assert "### biz_key: org_open_rule (参考)" in output["requirement_context"]
    assert "[Supplement - grouped by biz_key]" in output["supplement_context"]
    assert "### biz_key: org_close_rule (当前业务)" in output["testcase_context"]
    assert "### biz_key: org_open_rule (参考)" in output["testcase_context"]


def test_only_current_biz_keeps_only_current_scope() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-001: 关闭机构前需要校验余额。",
        rag_result={
            "debug": {
                "final_chunks": [
                    {
                        "filename": "close_req.md",
                        "doc_type": "requirement",
                        "biz_key": "org_close_rule",
                        "module": "机构关闭",
                        "chunk_text": "REQ-001: 关闭机构前需要校验余额。",
                    },
                    {
                        "filename": "open_req.md",
                        "doc_type": "requirement",
                        "biz_key": "org_open_rule",
                        "module": "机构开通",
                        "chunk_text": "REQ-101: 开通机构前需审批。",
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
    assert "### biz_key: unknown (当前业务)" in output["testcase_context"]
    assert output["biz_key_isolation_log"]["mode"] == "reference_allowed_current_unknown"
    assert output["biz_key_order"]


def test_control_context_includes_preferred_patterns() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-901: keep settlement consistency",
        feedback_control_state={
            "must_cover_rules": ["RULE-901"],
            "preferred_patterns": ["deterministic settlement assertion chain"],
        },
    )

    assert "### PREFERRED PATTERNS" in output["control_context"]
    assert "deterministic settlement assertion chain" in output["control_context"]
    assert int(output["control_summary"].get("preferred_patterns_count") or 0) == 1
    assert "### PREFERRED PATTERN QUOTA (AB)" in output["control_context"]
    assert output["control_summary"].get("preferred_quota_variant") == "B"


def test_control_context_applies_preferred_quota_ab_variant(monkeypatch) -> None:
    monkeypatch.setenv("TESTGEN_ENABLE_STRONG_PREFERRED_QUOTA_AB", "true")
    monkeypatch.setenv("TESTGEN_PREFERRED_FLOW_CASE_QUOTA", "2")
    monkeypatch.setenv("TESTGEN_UI_CASE_RATIO_CAP", "0.4")
    output = build_structured_prompt_context(
        requirement="REQ-902: settlement flow reliability",
        feedback_control_state={
            "preferred_patterns": ["multi-step settlement closure path"],
        },
    )

    assert "### PREFERRED PATTERN QUOTA (AB)" in output["control_context"]
    assert "at least 2 workflow/state-transition cases" in output["control_context"]
    assert "must not exceed 40%" in output["control_context"]
    assert output["control_summary"].get("preferred_quota_variant") == "B"
    assert int(output["control_summary"].get("preferred_flow_case_quota") or 0) == 2


def test_control_context_can_disable_preferred_quota_variant_by_env(monkeypatch) -> None:
    monkeypatch.setenv("TESTGEN_ENABLE_STRONG_PREFERRED_QUOTA_AB", "false")
    output = build_structured_prompt_context(
        requirement="REQ-903: legacy mode fallback",
        feedback_control_state={
            "preferred_patterns": ["legacy preferred pattern"],
        },
    )

    assert "### PREFERRED PATTERN QUOTA (AB)" not in output["control_context"]
    assert output["control_summary"].get("preferred_quota_variant") == "A"
    assert int(output["control_summary"].get("preferred_flow_case_quota") or 0) == 0
