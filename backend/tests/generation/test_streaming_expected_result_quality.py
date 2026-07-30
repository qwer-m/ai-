from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_expected_result_quality import (
    has_concrete_expected_assertion,
    has_weak_ambiguous_expected_result,
    is_ambiguous_expected_result,
    is_case_expected_result_non_assertable,
    is_non_assertable_expected_result,
    looks_template_polluted_expected_result,
    looks_truncated_text,
)
from modules.test_generation_components.coverage.case_quality_gate import (
    summarize_case_quality_gate,
)


def test_expected_result_quality_detects_concrete_assertions() -> None:
    assert has_concrete_expected_assertion("系统提示“记录保存成功”")
    assert has_concrete_expected_assertion("剩余次数显示为 2/5")
    assert has_concrete_expected_assertion("按钮置灰且不可点击")
    assert not has_concrete_expected_assertion("结果符合预期")
    assert has_concrete_expected_assertion("checkout is ready")
    assert has_concrete_expected_assertion("关闭后标题和正文输入区可编辑")


def test_expected_result_quality_marks_weak_or_placeholder_results_non_assertable() -> None:
    assert is_non_assertable_expected_result("")
    assert is_non_assertable_expected_result("result is as configured")
    assert is_non_assertable_expected_result("或显示错误信息")
    assert has_weak_ambiguous_expected_result("or show error")


def test_expected_result_text_heuristics_are_diagnostic_not_hard_gate() -> None:
    gate = summarize_case_quality_gate(
        [
            {
                "id": "TC-DIAGNOSTIC",
                "priority_final": "P1",
                "expected_result": "result is as configured",
            }
        ]
    )

    assert gate["passed"] is True
    assert gate["failed_checks"] == []
    assert gate["diagnostic_checks"] == ["non_assertable_expected_result_count=1"]


def test_expected_result_quality_allows_ambiguous_text_when_specific_assertion_exists() -> None:
    text = "可能显示系统提示“记录保存成功”"

    assert is_ambiguous_expected_result(text)
    assert has_concrete_expected_assertion(text)
    assert not has_weak_ambiguous_expected_result(text)
    assert not is_non_assertable_expected_result(text)


def test_expected_result_quality_allows_multiclause_business_assertions_with_option_text() -> None:
    text = "执行批次支持多选；默认每项2小时；时间段可选8:00-10:00；预览中按所选日期生成记录"

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
        "默认每批2小时，一天最多只能设置5批，第6批无法添加或提示超出限制",
        "当前执行项正常展示；下一项显示为“ITEM-002”，来源字段显示“最新版本”",
        "1.系统自动标记冲突项并提示时间冲突；2.需手动微调时间解决冲突；3.调整后后续项目自动顺延",
    ]

    for text in texts:
        assert has_concrete_expected_assertion(text)
        assert not is_non_assertable_expected_result(text)


def test_expected_result_quality_allows_common_ui_visibility_and_formula_assertions() -> None:
    texts = [
        "未编辑的标题标记正常展示，标记内容完整显示，输入值保持一致",
        "内容有改动的旧标记被移除，未改动的标记正常展示",
        "不展示内部标签，不展示隐藏字段，账号名称正常展示",
        "跳转到对应记录的详情页，记录内容正常展示",
        "T=MAX(1-72/72,0)=0，该记录权重仅由L和R决定，排序位置符合公式计算结果",
    ]

    for text in texts:
        assert has_concrete_expected_assertion(text)
        assert not is_non_assertable_expected_result(text)


def test_expected_result_quality_allows_descriptive_observable_business_results() -> None:
    texts = [
        "记录支持替换、删除、添加操作",
        "详情区域显示完整内容，并显示处理后的数量",
        "审核中记录显示审核文案，右上角显示审核中状态标识",
        "不合规记录审核不通过并不予展示",
        "点击缩略图后进入对应记录的详情页",
    ]

    for text in texts:
        assert has_concrete_expected_assertion(text)
        assert not is_non_assertable_expected_result(text)


def test_expected_result_quality_keeps_generic_success_phrases_non_assertable() -> None:
    texts = [
        "页面正常显示",
        "结果符合预期",
        "功能正常",
        "显示对应内容",
        "系统支持操作",
        "系统支持用户完成相关操作",
        "页面显示对应的相关内容信息",
        "系统完成业务处理并正常显示",
        "操作后系统展示对应结果内容",
    ]

    for text in texts:
        assert not has_concrete_expected_assertion(text)
        assert is_non_assertable_expected_result(text)


def test_case_expected_result_quality_uses_only_verified_semantic_anchors() -> None:
    verified_case = {
        "expected_result": "处理完成",
        "_semantic": {
            "module_candidates": [
                {
                    "module_name": "处理中心",
                    "module_key": "processing",
                    "evidence_verified": True,
                }
            ],
            "produced_states": [
                {
                    "entity": "处理",
                    "state": "完成",
                    "evidence_verified": True,
                }
            ],
        },
    }
    unverified_case = {
        **verified_case,
        "_semantic": {
            "produced_states": [
                {
                    "entity": "处理",
                    "state": "完成",
                    "evidence_verified": False,
                }
            ]
        },
    }

    assert not is_case_expected_result_non_assertable(verified_case)
    assert is_case_expected_result_non_assertable(unverified_case)
    assert is_non_assertable_expected_result("处理完成")


def test_case_expected_result_quality_keeps_generic_text_contract_without_semantic_data() -> None:
    case = {"expected_result": "记录状态等于 APPROVED"}

    assert not is_case_expected_result_non_assertable(case)


def test_expected_result_quality_detects_template_pollution_and_truncation() -> None:
    assert looks_template_polluted_expected_result("应跳转到目标页面，页面路径与标题均与上传图片显隐原图一致")
    assert is_non_assertable_expected_result("应跳转到目标页面，页面路径与标题均与上传图片显隐原图一致")
    legacy_delete_template = (
        "执行观察提示及列表变化后，应删除失败场景验证对应记录，"
        "且列表或查询中不再显示该记录"
    )
    assert looks_template_polluted_expected_result(legacy_delete_template)
    assert is_non_assertable_expected_result(legacy_delete_template)
    assert looks_truncated_text("操作后应正常展")
    assert looks_truncated_text("操作后显示为。")
    assert not looks_truncated_text("操作后显示为已排课")
