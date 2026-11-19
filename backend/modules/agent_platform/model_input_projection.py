"""模型实际输入与容量准备共用的纯字段投影，不改写完整验收数据。"""

from copy import deepcopy
from typing import Any


def project_input_value(value: Any, spec: Any) -> Any:
    """递归选择真实发送字段；True/None保留完整值，所有结果均为独立副本。"""
    if spec is True or spec is None:
        return deepcopy(value)
    if isinstance(spec, list):
        field_names = [str(field).strip() for field in spec]
        if any(not field for field in field_names):
            raise ValueError("Agent input_projection 字段名不能为空")
        if isinstance(value, list):
            return [project_input_value(item, spec) for item in value]
        if isinstance(value, dict):
            return {field: deepcopy(value[field]) for field in field_names if field in value}
        return deepcopy(value)
    if isinstance(spec, dict):
        if isinstance(value, list):
            return [project_input_value(item, spec) for item in value]
        if isinstance(value, dict):
            return {str(field): project_input_value(value[field], child_spec)
                    for field, child_spec in spec.items() if str(field) in value}
        return deepcopy(value)
    raise ValueError("Agent input_projection 只能使用字段列表、对象或 true")
