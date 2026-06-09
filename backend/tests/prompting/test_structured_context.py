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


def test_testcase_context_preserves_reference_module_order() -> None:
    output = build_structured_prompt_context(
        requirement="按参考用例顺序生成",
        existing_cases=[
            {
                "id": "TC-001",
                "biz_key": "learning_flow",
                "test_module": "督导端入口",
                "priority": "P0",
                "description": "入口展示",
            },
            {
                "id": "TC-002",
                "biz_key": "learning_flow",
                "test_module": "作业拍照批改",
                "priority": "P0",
                "description": "拍照批改",
            },
            {
                "id": "TC-003",
                "biz_key": "learning_flow",
                "test_module": "习题本",
                "priority": "P0",
                "description": "习题本展示",
            },
        ],
        current_biz_key="learning_flow",
        only_current_biz=False,
    )

    text = output["testcase_context"]

    assert text.index("#### test_module: 督导端入口") < text.index("#### test_module: 作业拍照批改")
    assert text.index("#### test_module: 作业拍照批改") < text.index("#### test_module: 习题本")


def test_module_order_hint_prefers_requirement_document_order_over_reference_cases() -> None:
    output = build_structured_prompt_context(
        requirement="按需求文档流程生成",
        rag_result={
            "debug": {
                "final_chunks": [
                    {
                        "filename": "req.md",
                        "doc_type": "requirement",
                        "biz_key": "learning_flow",
                        "module": "习题本",
                        "chunk_text": "REQ-001: 先查看习题本。",
                    },
                    {
                        "filename": "req.md",
                        "doc_type": "requirement",
                        "biz_key": "learning_flow",
                        "module": "周末提升计划",
                        "chunk_text": "REQ-002: 再进入周末提升计划。",
                    },
                ]
            }
        },
        existing_cases=[
            {
                "id": "TC-001",
                "biz_key": "learning_flow",
                "test_module": "周末提升计划",
                "priority": "P0",
                "description": "参考用例顺序靠前",
            },
            {
                "id": "TC-002",
                "biz_key": "learning_flow",
                "test_module": "习题本",
                "priority": "P0",
                "description": "参考用例顺序靠后",
            },
        ],
        current_biz_key="learning_flow",
        only_current_biz=False,
    )

    assert output["module_order_source"] == "requirement_document"
    assert output["module_order_hint"] == ["习题本", "周末提升计划"]
    assert output["context_by_biz"]["learning_flow"]["module_order_hint"] == ["习题本", "周末提升计划"]


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


def test_control_context_includes_manual_quality_profile() -> None:
    output = build_structured_prompt_context(
        requirement="recent course schedule regression",
        feedback_control_state={
            "source_meta": {
                "manual_quality_profile": {
                    "kind": "manual_quality_profile",
                    "profile_source": "priority_sample_pool_manual_verified",
                    "profile_version": "stable-1",
                    "trusted_sample_count": 12,
                    "priority_distribution": {"P0": 4, "P1": 6, "P2": 2},
                    "module_distribution_top": {
                        "\u672c\u5468\u8bfe\u7a0b\u6a21\u5757": 5,
                        "\u6392\u8bfe-\u5b66\u4e60\u8ba1\u5212-\u7b2c1\u6b65": 4,
                    },
                    "execution_lifecycle_fields": ["ST", "release", "\u8865\u5145\u9879"],
                    "high_priority_ratio": 0.83,
                    "display_ratio_cap": 0.25,
                }
            }
        },
    )

    context = output["control_context"]
    assert "### MANUAL QUALITY PROFILE" in context
    assert "target P0/P1 ratio: about 83%" in context
    assert "display-only cap: <= 25%" in context
    assert "\u672c\u5468\u8bfe\u7a0b\u6a21\u5757" in context


def test_control_context_includes_workflow_blueprints() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-904: checkout must close the paid order flow",
        feedback_control_state={
            "workflow_blueprints": [
                {
                    "id": "checkout_flow",
                    "name": "checkout flow",
                    "steps": [
                        {"id": "submit", "label": "Submit order"},
                        {"id": "verify", "label": "Verify paid status"},
                    ],
                }
            ]
        },
    )

    assert "### WORKFLOW BLUEPRINTS" in output["control_context"]
    assert "checkout flow: Submit order -> Verify paid status" in output["control_context"]
    assert int(output["control_summary"].get("workflow_blueprint_count") or 0) == 1


def test_structured_context_builds_fact_and_project_profiles() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-100: Inventory imports must not include archived records.",
        rag_result={
            "debug": {
                "final_chunks": [
                    {
                        "filename": "inventory_req.md",
                        "doc_type": "requirement",
                        "biz_key": "inventory_flow",
                        "module": "Upload Center",
                        "chunk_text": "REQ-101: Upload Center validates files before Review Queue.",
                    },
                    {
                        "filename": "inventory_req.md",
                        "doc_type": "requirement",
                        "biz_key": "inventory_flow",
                        "module": "Review Queue",
                        "chunk_text": "REQ-102: Review Queue approval happens before Dashboard statistics.",
                    },
                ]
            }
        },
        current_biz_key="inventory_flow",
        only_current_biz=True,
    )

    assert output["fact_profile"]["confirmed_facts"]
    assert output["fact_profile"]["forbidden_facts"]
    assert output["project_profile"]["flow_outline"]["flow_order"]
    assert output["project_profile"]["flow_outline"]["data_flow_edges"]
    assert "### FACT PROFILE" in output["control_context"]
    assert "### PROJECT STRUCTURE PROFILE" in output["control_context"]
    assert "* data-flow edges:" in output["control_context"]
    assert output["feedback_control_state"]["source_meta"]["fact_profile"]["forbidden_facts"]
    assert output["feedback_control_state"]["source_meta"]["project_profile"]["flow_outline"]["flow_order"]


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


def test_structured_context_extracts_requirement_semantics_and_reuse_risks() -> None:
    requirement = """
    已确认：先选版本，再选年级。
    复用单词消消乐页面，完成后回首页，不是回原列表页。
    学课文 -> 词组消消乐 -> 选词填空。
    待确认：按钮是否仅在全部完成后才展示。
    """
    output = build_structured_prompt_context(
        requirement=requirement,
        rag_result={
            "debug": {
                "final_chunks": [
                    {
                        "filename": "lesson_req.md",
                        "doc_type": "requirement",
                        "biz_key": "lesson_flow",
                        "module": "学习流程",
                        "chunk_text": "复用选词填空页面，返回目标必须是首页。",
                    }
                ]
            }
        },
        current_biz_key="lesson_flow",
        only_current_biz=True,
    )

    assert "先选版本，再选年级" in output["requirement_semantics_context"]
    assert "待确认" in output["requirement_semantics_context"]
    assert "复用单词消消乐页面" in output["requirement_semantics_context"]
    assert "词组消消乐" in output["requirement_semantics_context"]
    assert output["confirmed_facts"]
    assert output["pending_items"]
    assert output["reuse_declarations"]
    assert output["hard_flow_constraints"]
    assert any("wrong_return_target_risk" in item for item in output["reuse_risks"])
    assert "### REUSE RISKS" in output["control_context"]
    assert int(output["control_summary"].get("reuse_risks_count") or 0) >= 1
