from __future__ import annotations

from collections.abc import Iterable
from typing import Any


CASE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "ID", "case_id", "caseId", "test_case_id", "testcase_id", "用例ID", "用例编号", "编号"),
    "description": (
        "description",
        "desc",
        "name",
        "title",
        "用例描述",
        "描述",
        "标题",
        "用例标题",
        "用例名称",
        "测试点",
        "测试用例",
    ),
    "test_module": (
        "test_module",
        "module",
        "testModule",
        "模块",
        "功能模块",
        "所属模块",
        "测试模块",
    ),
    "preconditions": ("preconditions", "precondition", "prerequisites", "conditions", "前置条件", "前提条件"),
    "steps": (
        "steps",
        "step",
        "test_steps",
        "testSteps",
        "操作步骤",
        "步骤",
        "测试步骤",
        "执行步骤",
    ),
    "test_input": (
        "test_input",
        "input",
        "testInput",
        "test_data",
        "testData",
        "输入",
        "测试输入",
        "入参",
        "测试数据",
        "数据",
    ),
    "expected_result": (
        "expected_result",
        "expected_results",
        "expected",
        "expectedResult",
        "assertion",
        "预期结果",
        "期望结果",
        "断言",
    ),
    "priority": ("priority", "Priority", "prio", "优先级", "级别", "用例级别"),
    "priority_final": ("priority_final", "priorityFinal", "final_priority", "finalPriority"),
}

# 这些别名只用于用户上传文件的通用表头解析，不承担内部新旧契约转发。
CASE_FIELD_ALIASES["id"] += ("用例ID", "用例编号", "测试用例编号", "编号")
CASE_FIELD_ALIASES["description"] += ("用例标题", "用例描述", "用例名称", "测试场景", "场景", "功能点")
CASE_FIELD_ALIASES["test_module"] += ("测试模块", "功能模块", "所属模块", "一级模块", "二级模块")
CASE_FIELD_ALIASES["preconditions"] += ("前置条件", "前提条件")
CASE_FIELD_ALIASES["steps"] += ("执行步骤", "测试步骤", "操作步骤", "步骤")
CASE_FIELD_ALIASES["test_input"] += ("测试输入", "输入", "测试数据", "入参")
CASE_FIELD_ALIASES["expected_result"] += ("预期结果", "期望结果", "预期输出", "断言")
CASE_FIELD_ALIASES["priority"] += ("优先级", "级别", "用例级别")



def case_fields() -> tuple[str, ...]:
    return tuple(CASE_FIELD_ALIASES)


def case_field_aliases(field: str, *extra: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*CASE_FIELD_ALIASES.get(field, (field,)), *extra)))


def case_field_alias_key_set(fields: Iterable[str] | None = None) -> frozenset[str]:
    field_names = case_fields() if fields is None else tuple(fields)
    return frozenset(alias for field in field_names for alias in case_field_aliases(field))


def case_value(case: dict[str, Any], field: str, default: Any = "") -> Any:
    if not isinstance(case, dict):
        return default
    for key in case_field_aliases(field):
        value = case.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default
