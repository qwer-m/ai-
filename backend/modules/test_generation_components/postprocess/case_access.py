from __future__ import annotations

import re
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

_STEP_TEXT_SEPARATOR_RE = re.compile(r"[\n\r;\uff1b\u3001]+")


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


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = [_stringify(item) for item in value.values()]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes, bytearray)):
        parts = [_stringify(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    return str(value).strip()


def case_text_value(value: Any) -> str:
    return _stringify(value)


def case_text_field(case: dict[str, Any], field: str, default: str = "") -> str:
    value = case_value(case, field, default)
    text = case_text_value(value)
    return text if text else default


def case_text_list_value(value: Any, *, split_lines: bool = False) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            text = case_text_value(item)
            if text:
                result.append(text)
        return result
    text = case_text_value(value)
    if not text:
        return []
    if split_lines:
        return [part.strip() for part in text.splitlines() if part.strip()]
    return [text]


def case_text_list_field(case: dict[str, Any], field: str, *, split_lines: bool = False) -> list[str]:
    return case_text_list_value(case_value(case, field, []), split_lines=split_lines)


def case_steps(case: dict[str, Any]) -> list[str]:
    raw = case_value(case, "steps", [])
    return case_text_list_value(raw)


def case_step_lines(case: dict[str, Any]) -> list[str]:
    raw = case_value(case, "steps", [])
    if isinstance(raw, (list, tuple)):
        result: list[str] = []
        for item in raw:
            text = case_text_value(item)
            if text:
                result.append(text)
        return result
    text = case_text_value(raw)
    if not text:
        return []
    return [part.strip() for part in _STEP_TEXT_SEPARATOR_RE.split(text) if part.strip()]


def case_text_parts(case: dict[str, Any], fields: tuple[str, ...], *, dedupe: bool = True) -> list[str]:
    parts: list[str] = []
    seen: set[str] = set()
    for field in fields:
        value = case_value(case, field, "")
        if isinstance(value, Iterable) and not isinstance(value, (str, dict, bytes, bytearray)):
            candidates = [case_text_value(item) for item in value]
        else:
            candidates = [case_text_value(value)] if value is not None else []
        for raw in candidates:
            text = case_text_value(raw)
            if not text:
                continue
            key = " ".join(text.lower().split())
            if dedupe and key in seen:
                continue
            seen.add(key)
            parts.append(text)
    return parts


def case_flat_text(
    case: dict[str, Any],
    fields: tuple[str, ...] = ("description", "test_module", "test_input", "expected_result", "preconditions", "steps"),
    *,
    separator: str = "\n",
    lower: bool = False,
) -> str:
    text = separator.join(case_text_parts(case, fields))
    return text.lower() if lower else text


def case_id(case: dict[str, Any]) -> str:
    return case_text_field(case, "id")


def case_priority(case: dict[str, Any], *, prefer_final: bool = False, default: str = "") -> str:
    fields = ("priority_final", "priority") if prefer_final else ("priority", "priority_final")
    for field in fields:
        value = case_text_field(case, field)
        if value:
            return value.upper()
    return str(default or "").strip().upper()


def case_focus_text(case: dict[str, Any], *, lower: bool = False) -> str:
    text = " ".join(
        part
        for part in (
            case_text_field(case, "description"),
            case_text_field(case, "expected_result"),
            case_text_field(case, "test_input"),
            " ".join(case_steps(case)),
        )
        if part
    )
    return text.lower() if lower else text


def case_signature_text(case: dict[str, Any]) -> str:
    return "|".join(
        [
            case_text_field(case, "test_module").lower(),
            case_text_field(case, "description").lower(),
            case_text_field(case, "expected_result").lower(),
            case_text_field(case, "test_input").lower(),
        ]
    )
