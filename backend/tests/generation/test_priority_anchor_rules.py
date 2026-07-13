from __future__ import annotations

from modules.testing.test_generation_components.postprocess.priority_anchor_rules import (
    apply_priority_override,
    enforce_main_path_p0_anchors,
    p0_configured_anchor_family,
    p0_cross_domain_essay_case,
    p0_has_low_value_signal,
    p0_main_path_anchor,
    p0_main_path_target_count,
)


def test_schedule_requirement_rejects_essay_correction_p0_anchor() -> None:
    case = {
        "description": "上传作文图片后生成批改结果",
        "test_module": "作文批改",
        "expected_result": "进入批改结果页",
        "priority": "P1",
    }
    requirement = "近期课程+排课：课程时间冲突和顺延规则"

    assert p0_cross_domain_essay_case(case, requirement_text=requirement) is True
    assert p0_main_path_anchor(case, requirement_text=requirement) is False


def test_essay_requirement_accepts_complete_correction_result_anchor() -> None:
    case = {
        "description": "上传图片后点击去批改成功生成批改结果",
        "test_module": "作文批改",
        "expected_result": "批改结果页展示综合点评、分句点评、全文润色和优化建议四部分内容",
    }
    requirement = "作文批改 full regression"

    assert p0_cross_domain_essay_case(case, requirement_text=requirement) is False
    assert p0_configured_anchor_family(case, requirement_text=requirement) in {
        "generation_result",
        "result_display",
    }
    assert p0_main_path_anchor(case, requirement_text=requirement) is True


def test_low_value_result_detail_is_not_public_p0_anchor() -> None:
    case = {
        "description": "综合点评星星评分展示",
        "test_module": "批改结果",
        "expected_result": "星星数量与综合评分值匹配",
    }

    assert p0_has_low_value_signal(case) is True
    assert p0_main_path_anchor(case, requirement_text="作文批改") is False


def test_course_permission_anchor_survives_non_essay_requirement() -> None:
    case = {
        "description": "Normal user first lesson is available and other lessons are locked",
        "test_module": "Permission",
        "expected_result": "The first lesson is available and other lessons are locked by the paywall.",
    }

    assert p0_configured_anchor_family(case, requirement_text="course permission regression") == "permission"
    assert p0_main_path_anchor(case, requirement_text="course permission regression") is True


def test_course_permission_anchor_accepts_alias_fields() -> None:
    case = {
        "title": "Normal user first lesson is available and other lessons are locked",
        "testModule": "Permission",
        "expectedResult": "The first lesson is available and other lessons are locked by the paywall.",
        "testSteps": ["open course list", "open locked lesson"],
    }

    assert p0_configured_anchor_family(case, requirement_text="course permission regression") == "permission"
    assert p0_main_path_anchor(case, requirement_text="course permission regression") is True


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
            "description": "综合点评星星评分展示",
            "expected_result": "星星数量与综合评分值匹配",
        },
        {"id": "submit", "priority": "P0", "description": "投稿提交成功并进入审核中"},
        {"id": "result", "priority": "P0", "description": "生成批改结果并完整展示四部分"},
    ]

    updated = enforce_main_path_p0_anchors(
        cases,
        coverage_mode="full_functional_regression",
        requirement_text="作文批改",
        case_signature_fn=lambda item: str(item.get("id") or ""),
    )

    detail = next(item for item in updated if item.get("id") == "detail")
    assert detail["priority"] == "P1"
    assert detail["priority_decision_source"] == "main_path_anchor_demoted_non_blocking"


def test_enforce_main_path_p0_anchors_promotes_business_anchor_floor() -> None:
    cases = [
        {"id": "submit", "priority": "P1", "description": "投稿提交成功并进入审核中"},
        {"id": "detail", "priority": "P2", "description": "星星评分展示"},
    ]

    updated = enforce_main_path_p0_anchors(
        cases,
        coverage_mode="expanded_regression",
        requirement_text="作文批改",
        case_signature_fn=lambda item: str(item.get("id") or ""),
    )

    submit = next(item for item in updated if item.get("id") == "submit")
    assert submit["priority"] == "P0"
    assert submit["priority_decision_source"] == "main_path_anchor_floor"
