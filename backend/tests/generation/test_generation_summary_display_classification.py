from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_ui_like import (
    is_display_only_final_case,
)


def test_final_display_classification_keeps_static_ui_cases() -> None:
    case = {
        "execution_group": "display",
        "test_module": "学习计划",
        "description": "学习计划页面基础导航验证",
        "expected_result": "页面顶部包含返回按钮、标题和科目筛选栏",
        "priority": "P2",
        "steps": ["打开学习计划页面"],
    }

    assert is_display_only_final_case(case)


def test_final_display_classification_exempts_business_state_visibility() -> None:
    case = {
        "execution_group": "display",
        "test_module": "排课",
        "description": "保存计划后课程列表更新验证",
        "expected_result": "保存后课程列表更新，新增课程按时间升序插入正确位置；学生端本周任务同步更新",
        "priority": "P1",
        "steps": ["保存计划", "查看课程列表", "进入学生端首页"],
    }

    assert not is_display_only_final_case(case)
