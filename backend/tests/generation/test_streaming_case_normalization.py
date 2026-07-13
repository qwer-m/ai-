from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_case_normalization import (
    is_placeholder_expected_result,
    normalize_priority_value,
    normalize_steps,
    strip_step_prefix,
    strip_validation_prefix,
)


def test_normalize_priority_value_defaults_unknown_values_to_p2() -> None:
    assert normalize_priority_value(" p0 ") == "P0"
    assert normalize_priority_value("P1") == "P1"
    assert normalize_priority_value("unknown") == "P2"
    assert normalize_priority_value("") == "P2"


def test_normalize_steps_removes_common_numbering_and_reindexes() -> None:
    steps = [" 1. 打开页面 ", "step 2) 点击提交", "", "3、查看结果"]

    assert normalize_steps(steps) == [
        "1. 打开页面",
        "2. 点击提交",
        "3. 查看结果",
    ]


def test_strip_prefix_helpers() -> None:
    assert strip_step_prefix("2. 点击提交") == "点击提交"
    assert strip_validation_prefix("验证页面保存成功") == "页面保存成功"
    assert strip_validation_prefix("verify: result is visible") == "result is visible"


def test_is_placeholder_expected_result_detects_weak_defaults() -> None:
    assert is_placeholder_expected_result("")
    assert is_placeholder_expected_result("result is as configured")
    assert is_placeholder_expected_result("结果符合预期")
    assert is_placeholder_expected_result("成功")
    assert not is_placeholder_expected_result("列表展示课程名称、上课时间和教师姓名")
