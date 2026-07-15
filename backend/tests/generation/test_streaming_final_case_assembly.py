from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_final_case_assembly import assemble_final_cases


def test_assemble_final_cases_filters_restored_non_assertable_cases() -> None:
    valid_case = {
        "id": "TC-001",
        "description": "登录成功后展示首页",
        "test_module": "登录",
        "preconditions": ["用户已打开登录页"],
        "steps": ["输入正确账号密码", "点击登录"],
        "test_input": "账号=teacher001，密码正确",
        "expected_result": "页面跳转到首页，顶部显示用户名 teacher001，接口返回 200",
        "priority": "P0",
        "priority_final": "P0",
    }
    weak_restored_candidate = {
        "id": "TC-002",
        "description": "异常提示展示",
        "test_module": "登录",
        "preconditions": ["用户已打开登录页"],
        "steps": ["输入错误密码", "点击登录"],
        "test_input": "密码错误",
        "expected_result": "or show error",
        "expected_result_quality": "non_assertable",
        "expected_result_quality_reason": "template_or_weak_assertion",
        "priority": "P1",
        "priority_final": "P1",
        "model_priority": "P0",
        "priority_decision_source": "model_p0_guard_downgrade",
    }

    result = assemble_final_cases(
        parsed_result=[valid_case],
        requirement="登录功能",
        start_id=1,
        effective_generation_coverage_mode="standard_regression",
        generation_coverage_mode="standard_regression",
        review_candidate_cases=[weak_restored_candidate],
        review_selected_count=2,
        workflow_blueprints=[],
        trusted_workflow_contracts=[],
        current_requirement_workflow_blueprints=[],
        authoritative_workflow_blueprints=[],
        flow_project_profile={},
        project_profile={},
        append=False,
        reorder_cases_by_closed_loop_fn=lambda cases, **kwargs: list(cases),
        govern_cases_by_flow_structure_fn=lambda *args, **kwargs: (list(args[1]), {}),
        analyze_case_structure_fn=lambda *args, **kwargs: {"rows": []},
    )

    assert [case["id"] for case in result.cases] == ["TC-001"]
    assert result.final_quality_drop_total == 1
    assert result.final_quality_drop_details[0]["case_id"] == "TC-002"


def test_assemble_final_cases_prunes_append_duplicate_scenarios() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "拍照后缩略图展示",
            "test_module": "拍照",
            "preconditions": ["已打开拍照弹窗"],
            "steps": ["拍摄第一张照片"],
            "test_input": "照片1",
            "expected_result": "缩略图列表显示1张照片，计数显示已拍1张",
            "priority": "P1",
            "priority_final": "P1",
        },
        {
            "id": "TC-002",
            "description": "连续拍照后缩略图展示",
            "test_module": "拍照",
            "preconditions": ["已打开拍照弹窗"],
            "steps": ["连续拍摄两张照片"],
            "test_input": "照片1、照片2",
            "expected_result": "缩略图列表显示2张照片，计数显示已拍2张",
            "priority": "P1",
            "priority_final": "P1",
        },
    ]

    def _govern(_requirement, input_cases, **kwargs):  # noqa: ANN001, ANN202, ARG001
        return [dict(input_cases[0])], {"scenario_duplicate_pruned_count": 1}

    result = assemble_final_cases(
        parsed_result=cases,
        requirement="拍照上传",
        start_id=1,
        effective_generation_coverage_mode="standard_regression",
        generation_coverage_mode="standard_regression",
        review_candidate_cases=[],
        review_selected_count=2,
        workflow_blueprints=[],
        trusted_workflow_contracts=[],
        current_requirement_workflow_blueprints=[],
        authoritative_workflow_blueprints=[],
        flow_project_profile={},
        project_profile={},
        append=True,
        reorder_cases_by_closed_loop_fn=lambda input_cases, **kwargs: list(input_cases),
        govern_cases_by_flow_structure_fn=_govern,
        analyze_case_structure_fn=lambda *args, **kwargs: {"rows": []},
    )

    assert [case["id"] for case in result.cases] == ["TC-001"]
    assert result.append_duplicate_pruned_count == 1
