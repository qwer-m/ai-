from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_expected_result_quality import (
    has_concrete_expected_assertion,
    has_weak_ambiguous_expected_result,
    is_ambiguous_expected_result,
    is_non_assertable_expected_result,
    looks_template_polluted_expected_result,
    looks_truncated_text,
)
from modules.test_generation_components.coverage.core_flow_backfill_generation import (
    summarize_case_quality_gate,
)


def test_expected_result_quality_detects_concrete_assertions() -> None:
    assert has_concrete_expected_assertion("系统提示“课程保存成功”")
    assert has_concrete_expected_assertion("剩余批改次数显示为 2/5")
    assert has_concrete_expected_assertion("按钮置灰且不可点击")
    assert not has_concrete_expected_assertion("结果符合预期")


def test_expected_result_quality_marks_weak_or_placeholder_results_non_assertable() -> None:
    assert is_non_assertable_expected_result("")
    assert is_non_assertable_expected_result("result is as configured")
    assert is_non_assertable_expected_result("或显示错误信息")
    assert has_weak_ambiguous_expected_result("or show error")


def test_expected_result_quality_allows_ambiguous_text_when_specific_assertion_exists() -> None:
    text = "可能显示系统提示“课程保存成功”"

    assert is_ambiguous_expected_result(text)
    assert has_concrete_expected_assertion(text)
    assert not has_weak_ambiguous_expected_result(text)
    assert not is_non_assertable_expected_result(text)


def test_expected_result_quality_allows_multiclause_business_assertions_with_option_text() -> None:
    text = "上课日支持多选；默认每节2小时；时间段可选8:00-10:00；预览中按所选日期生成课程"

    assert is_ambiguous_expected_result(text)
    assert has_concrete_expected_assertion(text)
    assert not has_weak_ambiguous_expected_result(text)
    assert not is_non_assertable_expected_result(text)

    gate = summarize_case_quality_gate(
        [
            {
                "id": "TC-067",
                "priority_final": "P1",
                "expected_result": text,
                "expected_result_quality": "non_assertable",
                "expected_result_quality_reason": "template_or_weak_assertion",
            }
        ]
    )
    assert gate["passed"] is True
    assert gate["non_assertable_expected_result_count"] == 0


def test_expected_result_quality_allows_boundary_state_and_conflict_assertions() -> None:
    texts = [
        "默认每节2小时，一天最多只能设置5节，第6节无法添加或提示超出限制",
        "当前在学课程正常展示，下一节课读取最新计划中最近的一节课程",
        "1.系统自动标记冲突课程并提示时间冲突；2.需手动微调时间解决冲突；3.手动调整后后续课程按规则自动顺延",
    ]

    for text in texts:
        assert has_concrete_expected_assertion(text)
        assert not is_non_assertable_expected_result(text)


def test_expected_result_quality_detects_template_pollution_and_truncation() -> None:
    assert looks_template_polluted_expected_result("应跳转到目标页面，页面路径与标题均与上传图片显隐原图一致")
    assert is_non_assertable_expected_result("应跳转到目标页面，页面路径与标题均与上传图片显隐原图一致")
    assert looks_truncated_text("操作后应正常展")
    assert looks_truncated_text("操作后显示为。")
    assert not looks_truncated_text("操作后显示为已排课")
