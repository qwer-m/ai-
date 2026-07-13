from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_expected_result_builder import (
    build_expected_result_from_case,
)


def _build(description: str, steps: list[str], module: str = "课程排课") -> str:
    return build_expected_result_from_case(
        module=module,
        description=description,
        normalized_steps=steps,
    )


def test_expected_result_builder_handles_crud_and_query_branches() -> None:
    assert "应成功完成新增课程" in _build("新增课程", ["1. 填写课程", "2. 保存课程"])
    assert "应更新编辑课程对应记录" in _build("编辑课程", ["1. 修改课程", "2. 提交"])
    assert "应删除删除课程对应记录" in _build("删除课程", ["1. 删除课程"])
    assert "仅包含满足筛选课程筛选条件" in _build("筛选课程", ["1. 输入课程名称", "2. 查询"])


def test_expected_result_builder_handles_auth_import_export_and_navigation() -> None:
    assert "仅可访问登录授权范围内页面或模块" in _build("登录", ["1. 输入账号", "2. 登录"], module="权限")
    assert "可下载的导出课程导出结果" in _build("导出课程", ["1. 点击导出"])
    assert "完成导入课程导入" in _build("导入课程", ["1. 上传文件"])
    assert "页面路径与标题均与进入课程一致" in _build("进入课程", ["1. 打开课程"])


def test_expected_result_builder_handles_validation_display_and_unknown_context() -> None:
    assert "给出明确校验提示" in _build("边界值校验", ["1. 输入超限值"])
    assert "完整显示课程列表关键字段" in _build("课程列表", ["1. 展示课程列表"], module="展示")
    assert _build("课程详情", ["1. 查看课程详情"], module="课程") == ""


def test_expected_result_builder_strips_validation_prefix_and_step_prefixes() -> None:
    result = _build("验证新增课程。", ["1. 填写课程", "2. 保存课程"])

    assert "新增课程" in result
    assert "2. 保存课程" not in result
    assert "执行保存课程后" in result
