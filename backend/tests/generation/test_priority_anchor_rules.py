from __future__ import annotations

from modules.testing.test_generation_components.postprocess.priority_anchor_rules import (
    apply_priority_override,
    enforce_entry_path_p0,
    enforce_pure_ui_p2,
)


def test_compiled_blocking_entry_is_p0_without_product_specific_tokens() -> None:
    cases = enforce_entry_path_p0(
        [
            {
                "description": "Open the protected page",
                "steps": ["Open the protected page"],
                "expected_result": "The access rule blocks access to the target page",
                "priority": "P1",
                "main_chain_stage_kind": "entry",
                "workflow_transition": {
                    "workflow_id": "workflow-1",
                    "stage_kind": "entry",
                    "blocking": True,
                },
            }
        ]
    )

    assert cases[0]["priority"] == "P0"
    assert cases[0]["priority_decision_source"] == "entry_path_availability_p0"


def test_body_entry_words_and_direct_blocking_do_not_create_p0_without_compiled_entry() -> None:
    cases = enforce_entry_path_p0(
        [
            {
                "description": "点击入口按钮并打开页面",
                "expected_result": "入口不存在且点击无效",
                "priority": "P1",
                "blocking": True,
            }
        ]
    )

    assert cases[0]["priority"] == "P1"
    assert "priority_decision_source" not in cases[0]


def test_compiled_critical_entry_does_not_depend_on_entry_wording() -> None:
    cases = enforce_entry_path_p0(
        [
            {
                "description": "执行导航动作并校验目标资源可达",
                "expected_result": "目标资源可供后续业务使用",
                "priority": "P1",
                "main_chain_stage_kind": "entry",
                "workflow_transition": {
                    "workflow_id": "workflow-2",
                    "stage_kind": "entry",
                    "critical": True,
                },
            }
        ]
    )

    assert cases[0]["priority"] == "P0"


def test_apply_priority_override_sets_final_priority_contract_fields() -> None:
    case = {"priority": "P2", "description": "save"}

    apply_priority_override(case, priority="p0", source="declared_critical")

    assert case["priority"] == "P0"
    assert case["priority_final"] == "P0"
    assert case["priority_decision_state"] == "overridden"
    assert case["priority_decision_source"] == "declared_critical"


def test_enforce_pure_ui_p2_demotes_non_blocking_detail() -> None:
    cases = [
        {
            "id": "detail",
            "priority": "P0",
            "description": "辅助信息的文案与布局检查",
            "expected_result": "文案、样式与占位展示一致",
        },
        {"id": "submit", "priority": "P0", "description": "提交成功并进入审核中"},
        {"id": "result", "priority": "P0", "description": "上传文件后生成结果并展示结果详情"},
    ]

    updated = enforce_pure_ui_p2(cases)

    detail = next(item for item in updated if item.get("id") == "detail")
    assert detail["priority"] == "P2"
    assert detail["priority_decision_source"] == "pure_ui_non_blocking_p2"


def test_enforce_pure_ui_p2_demotes_ordinary_ui_entry() -> None:
    cases = [
        {
            "id": "entry",
            "priority": "P0",
            "description": "入口布局与文案可见",
            "execution_group": "main_smoke",
            "main_chain_stage_kind": "entry",
        },
        {"id": "submit", "priority": "P0", "description": "提交成功并进入审核中"},
    ]

    updated = enforce_pure_ui_p2(cases)

    entry = next(item for item in updated if item.get("id") == "entry")
    assert entry["priority"] == "P2"
    assert entry["priority_decision_source"] == "pure_ui_non_blocking_p2"


def test_priority_rules_do_not_promote_ordinary_business_case_to_p0() -> None:
    cases = [
        {"id": "submit", "priority": "P1", "description": "提交成功并进入审核中"},
        {"id": "detail", "priority": "P2", "description": "辅助文案与布局展示"},
    ]

    updated = enforce_pure_ui_p2(cases)

    submit = next(item for item in updated if item.get("id") == "submit")
    assert submit["priority"] == "P1"
    assert "priority_decision_source" not in submit
