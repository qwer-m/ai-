from __future__ import annotations

from modules.testing.test_generation_components.postprocess.priority_anchor_rules import (
    apply_priority_override,
    enforce_entry_path_p0,
    enforce_main_path_p0_anchors,
    p0_configured_anchor_family,
    p0_has_low_value_signal,
    p0_main_path_anchor,
    p0_main_path_target_count,
)


def test_blocked_page_entry_is_p0_without_product_specific_tokens() -> None:
    cases = enforce_entry_path_p0(
        [
            {
                "description": "Open the protected page",
                "steps": ["Open the protected page"],
                "expected_result": "The access rule blocks access to the target page",
                "priority": "P1",
            }
        ]
    )

    assert cases[0]["priority"] == "P0"
    assert cases[0]["priority_decision_source"] == "entry_path_availability_p0"


def test_generic_generation_result_is_a_main_path_anchor() -> None:
    case = {
        "description": "上传文件后生成处理结果",
        "test_module": "文件处理",
        "expected_result": "结果详情页展示生成结果",
        "priority": "P1",
    }

    assert p0_configured_anchor_family(case) == "generation_result"
    assert p0_main_path_anchor(case) is True


def test_low_value_result_detail_is_not_public_p0_anchor() -> None:
    case = {
        "description": "辅助信息的文案与布局检查",
        "test_module": "辅助展示",
        "expected_result": "文案、样式与占位展示一致",
    }

    assert p0_has_low_value_signal(case) is True
    assert p0_main_path_anchor(case) is False


def test_generic_permission_anchor_is_preserved() -> None:
    case = {
        "description": "未授权用户打开受限报表",
        "test_module": "权限",
        "expected_result": "访问被权限规则阻止，受限内容保持锁定",
    }

    assert p0_configured_anchor_family(case) == "permission"
    assert p0_main_path_anchor(case) is True


def test_generic_permission_anchor_accepts_alias_fields() -> None:
    case = {
        "title": "Unauthorized user opens a restricted report",
        "testModule": "Permission",
        "expectedResult": "The restricted report remains locked by the paywall.",
        "testSteps": ["open report list", "open locked report"],
    }

    assert p0_configured_anchor_family(case) == "permission"
    assert p0_main_path_anchor(case) is True


def test_main_path_target_count_matches_streaming_regression_floors() -> None:
    assert p0_main_path_target_count(80, coverage_mode="full_functional_regression") == 8
    assert p0_main_path_target_count(40, coverage_mode="full_functional_regression") == 9
    assert p0_main_path_target_count(49, coverage_mode="expanded_regression") == 3
    assert p0_main_path_target_count(60, coverage_mode="expanded_regression") == 4
    assert p0_main_path_target_count(12, coverage_mode="standard_regression") == 0


def test_apply_priority_override_sets_final_priority_contract_fields() -> None:
    case = {"priority": "P2", "description": "save"}

    apply_priority_override(case, priority="p0", source="main_path_anchor_floor")

    assert case["priority"] == "P0"
    assert case["priority_final"] == "P0"
    assert case["priority_decision_state"] == "overridden"
    assert case["priority_decision_source"] == "main_path_anchor_floor"


def test_enforce_main_path_p0_anchors_demotes_non_blocking_detail() -> None:
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

    updated = enforce_main_path_p0_anchors(
        cases,
        coverage_mode="full_functional_regression",
        requirement_text="通用内容提交与审核流程",
        case_signature_fn=lambda item: str(item.get("id") or ""),
    )

    detail = next(item for item in updated if item.get("id") == "detail")
    assert detail["priority"] == "P1"
    assert detail["priority_decision_source"] == "main_path_anchor_demoted_non_blocking"


def test_enforce_main_path_p0_anchors_preserves_structured_entry_stage() -> None:
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

    updated = enforce_main_path_p0_anchors(
        cases,
        coverage_mode="full_functional_regression",
        case_signature_fn=lambda item: str(item.get("id") or ""),
    )

    entry = next(item for item in updated if item.get("id") == "entry")
    assert entry["priority"] == "P0"
    assert entry.get("priority_decision_source") != "main_path_anchor_demoted_non_blocking"


def test_enforce_main_path_p0_anchors_promotes_business_anchor_floor() -> None:
    cases = [
        {"id": "submit", "priority": "P1", "description": "提交成功并进入审核中"},
        {"id": "detail", "priority": "P2", "description": "辅助文案与布局展示"},
    ]

    updated = enforce_main_path_p0_anchors(
        cases,
        coverage_mode="expanded_regression",
        requirement_text="通用内容提交与审核流程",
        case_signature_fn=lambda item: str(item.get("id") or ""),
    )

    submit = next(item for item in updated if item.get("id") == "submit")
    assert submit["priority"] == "P0"
    assert submit["priority_decision_source"] == "main_path_anchor_floor"
