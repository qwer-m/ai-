from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable
from .input_snapshots import restore_node_input
from .results import persisted_run_output, persisted_test_generation_result


class DuplicateJSONKeyError(ValueError):
    """重复对象字段会丢失原候选语义，不能按一般 JSON 语法损坏修补。"""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"JSON 对象包含重复字段: {json.dumps(key, ensure_ascii=False)}")


def strict_json_loads(value: str) -> Any:
    """在字典形成之前拒绝任意层重复字段，避免默认的后值覆盖前值。"""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, item in pairs:
            if key in result:
                raise DuplicateJSONKeyError(key)
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=unique_object)


def _serialize_utc_datetime(value: Any) -> Any:
    """执行时间在数据库中以无时区 UTC 保存，对外补齐明确时区。"""

    if value is None or not isinstance(value, datetime):
        return value
    aware_value = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return aware_value.isoformat().replace("+00:00", "Z")


def serialize_agent(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "project_id": value.project_id,
        "agent_key": value.agent_key,
        "name": value.name,
        "description": value.description,
        "instructions": value.instructions,
        "model": value.model,
        "output_schema": value.output_schema or {},
        "runtime_config": value.runtime_config or {},
        "version": value.version,
        "enabled": bool(value.enabled),
        "builtin": bool(value.builtin),
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def serialize_tool(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "project_id": value.project_id,
        "tool_key": value.tool_key,
        "name": value.name,
        "description": value.description,
        "input_schema": value.input_schema or {},
        "output_schema": value.output_schema or {},
        "risk_level": value.risk_level,
        "requires_approval": bool(value.requires_approval),
        "enabled": bool(value.enabled),
        "builtin": bool(value.builtin),
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def serialize_workflow(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "project_id": value.project_id,
        "workflow_key": value.workflow_key,
        "name": value.name,
        "description": value.description,
        "definition": value.definition or {},
        "version": value.version,
        "enabled": bool(value.enabled),
        "builtin": bool(value.builtin),
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def serialize_node_run(
    value: Any, *, load_node: Callable[[int], Any | None] | None = None,
) -> dict[str, Any]:
    return {
        "id": value.id,
        "node_key": value.node_key,
        "node_type": value.node_type,
        "agent_definition_id": value.agent_definition_id,
        "status": value.status,
        "attempt": value.attempt,
        "input_payload": restore_node_input(value, load_node),
        "output_payload": value.output_payload or {},
        "sdk_state": value.sdk_state or {},
        "error_message": value.error_message or "",
        "started_at": _serialize_utc_datetime(value.started_at),
        "finished_at": _serialize_utc_datetime(value.finished_at),
        "created_at": value.created_at,
    }


def serialize_event(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "run_id": value.run_id,
        "node_run_id": value.node_run_id,
        "sequence": value.sequence,
        "event_type": value.event_type,
        "payload": value.payload or {},
        "created_at": value.created_at,
    }


def serialize_approval(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "run_id": value.run_id,
        "node_run_id": value.node_run_id,
        "status": value.status,
        "request_payload": value.request_payload or {},
        "decision_payload": value.decision_payload or {},
        "requested_at": value.requested_at,
        "decided_at": value.decided_at,
    }


def _serialize_run_header(value: Any, *, run_context: dict[str, Any]) -> dict[str, Any]:
    """共享运行响应头；上下文由调用方提供，避免摘要触发大字段延迟加载。"""
    run_attempt = int(run_context.get("run_attempt") or 1)
    return {
        "id": value.id,
        "run_attempt": max(1, run_attempt),
        "project_id": value.project_id,
        "workflow_definition_id": value.workflow_definition_id,
        "status": value.status,
        "current_node_key": value.current_node_key,
        "input_payload": value.input_payload or {},
        "error_message": value.error_message or "",
        "parent_run_id": value.parent_run_id,
        "task_id": value.task_id,
        "created_at": value.created_at,
        "started_at": _serialize_utc_datetime(value.started_at),
        "finished_at": _serialize_utc_datetime(value.finished_at),
    }


def serialize_run(
    value: Any,
    *,
    node_runs: list[Any] | None = None,
    approvals: list[Any] | None = None,
) -> dict[str, Any]:
    run_context = value.run_context or {}
    nodes_by_id = {item.id: item for item in (node_runs or [])}
    return {
        **_serialize_run_header(value, run_context=dict(run_context)),
        "run_context": run_context,
        "output_payload": persisted_run_output(value),
        "test_generation_result": persisted_test_generation_result(value),
        "nodes": [serialize_node_run(item, load_node=nodes_by_id.get) for item in (node_runs or [])],
        "approvals": [serialize_approval(item) for item in (approvals or [])],
    }


def serialize_run_summary(value: Any) -> dict[str, Any]:
    """工作台初始化只需要活动状态，不传输节点和运行产物大字段。"""

    loaded_context = dict(getattr(value, "__dict__", {}).get("run_context") or {})
    return {
        **_serialize_run_header(value, run_context=loaded_context),
        "run_context": {},
        "output_payload": {},
        "test_generation_result": None,
        "nodes": [],
        "approvals": [],
    }
