from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable


class OutputRepairError(ValueError):
    """业务校验在错误源头提供修复策略及结构化差异，不以文案作为协议。"""

    def __init__(
        self, message: str, *, strategy_key: str, details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.strategy_key = strategy_key
        self.details = deepcopy(details or {})


@dataclass(frozen=True)
class OutputRepairStrategy:
    build_context: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
    feedback: Callable[[dict[str, Any], dict[str, Any]], str | None]


def repairable_output(strategy_key: str) -> Callable:
    """在既有后处理边界绑定策略，业务层已提供的详细差异原样保留。"""

    def decorate(handler: Callable) -> Callable:
        @wraps(handler)
        def checked(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return handler(*args, **kwargs)
            except OutputRepairError:
                raise
            except ValueError as exc:
                raise OutputRepairError(str(exc), strategy_key=strategy_key) from exc
        return checked
    return decorate


def restore_protected_output(
    output: dict[str, Any], context: dict[str, Any],
) -> dict[str, Any]:
    """按照领域策略声明的集合保护规则恢复非目标项，规则不依赖业务字段名称。"""

    restored = deepcopy(output)
    if context.get("mode") != "minimal_patch":
        return restored
    candidate = context.get("candidate_output")
    rules = context.get("protected_collections") or []
    if not rules:
        return restored
    if not isinstance(candidate, dict):
        raise ValueError("修复保护规则缺少完整候选")
    for rule in rules:
        field = rule["field"]
        original_items = candidate.get(field)
        updated_items = restored.get(field)
        if not isinstance(original_items, list) or not isinstance(updated_items, list):
            raise ValueError(f"修复保护字段必须保持数组: {field}")
        identity_key = rule.get("identity_key")
        if identity_key:
            protected_ids = set(rule["protected_ids"])
            updated_by_id: dict[str, Any] = {}
            for item in updated_items:
                if not isinstance(item, dict) or not isinstance(item.get(identity_key), str):
                    raise ValueError(f"修复保护字段缺少稳定编号: {field}.{identity_key}")
                item_id = item[identity_key]
                if item_id in updated_by_id:
                    raise ValueError(f"修复保护字段编号重复: {item_id}")
                updated_by_id[item_id] = item
            merged = []
            for original in original_items:
                item_id = original[identity_key]
                if item_id in protected_ids:
                    merged.append(deepcopy(original))
                    updated_by_id.pop(item_id, None)
                elif item_id in updated_by_id:
                    merged.append(updated_by_id.pop(item_id))
                else:
                    raise ValueError(f"修复结果遗漏目标项: {item_id}")
            # 非法新增项留给业务校验拒绝，不能在保护恢复时悄悄丢弃。
            restored[field] = [*merged, *updated_by_id.values()]
        else:
            if len(original_items) != len(updated_items):
                raise ValueError(f"修复保护字段必须保持原数量: {field}")
            protected_indexes = set(rule["protected_indexes"])
            if any(not isinstance(index, int) or not 0 <= index < len(original_items) for index in protected_indexes):
                raise ValueError(f"修复保护规则包含非法下标: {field}")
            restored[field] = [
                deepcopy(original_items[index] if index in protected_indexes else item)
                for index, item in enumerate(updated_items)
            ]
    return restored
