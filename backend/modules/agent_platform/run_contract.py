"""当前运行的任务身份与执行契约快照。"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any
from types import SimpleNamespace
from uuid import uuid4



CONTRACT_SCHEMA_VERSION = 4
CHECKPOINT_SCHEMA_VERSION = 1


def new_logical_task_id() -> str:
    """生成跨重试保持不变的业务任务身份。"""

    return f"task-{uuid4().hex}"


def attempt_id(logical_task_id: str, attempt: int) -> str:
    """生成不可复用的执行尝试身份。"""

    normalized = str(logical_task_id or "").strip()
    if not normalized:
        raise ValueError("逻辑任务身份不能为空")
    if int(attempt) < 1:
        raise ValueError("执行尝试必须从 1 开始")
    return f"{normalized}/attempt-{int(attempt)}"


def execution_attempt_number(context: dict[str, Any]) -> int:
    """真实尝试编号从不可变身份读取，展示重置不能参与生成下一次身份。"""

    task_id = str(context.get("logical_task_id") or "").strip()
    identity = str(context.get("attempt_id") or "")
    prefix = f"{task_id}/attempt-"
    number = identity.removeprefix(prefix)
    if not task_id or not identity.startswith(prefix) or not number.isdigit() or int(number) < 1:
        raise ValueError("运行缺少有效的不可变尝试身份")
    if attempt_id(task_id, int(number)) != identity:
        raise ValueError("运行尝试身份不是规范编号")
    return int(number)


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def input_payload_hash(value: Any) -> str:
    """计算运行原始输入指纹，与执行契约使用同一规范化规则。"""

    return _canonical_hash(value)


def model_route_snapshot(client: Any, definition: Any) -> dict[str, dict[str, str | None]]:
    """记录脱敏后的真实模型路由身份，避免恢复时静默切换 Provider。"""

    if client is None or not getattr(client, "provider", None):
        return {}
    runtime_config = dict(getattr(definition, "runtime_config", {}) or {})
    route_names = [str(runtime_config.get("model_route") or "main").strip().lower()]
    fallback = str(runtime_config.get("transient_fallback_model_route") or "").strip().lower()
    if fallback and fallback not in route_names:
        route_names.append(fallback)
    provider_attrs = {
        "main": "provider",
        "turbo": "turbo_provider",
        "review": "review_provider",
        "vision": "vl_provider",
    }
    result: dict[str, dict[str, str | None]] = {}
    for route in route_names:
        provider = getattr(client, provider_attrs.get(route, "provider"), None) or client.provider
        if provider is None:
            continue
        model = str(getattr(definition, "model", "") or getattr(provider, "model", "") or "").strip()
        if not model:
            continue
        provider_hash = getattr(client, "_provider_cache_identity", None)
        identity = provider_hash(provider) if callable(provider_hash) else ""
        result[route] = {
            "model": model,
            "provider_hash": str(identity),
            "provider_class": f"{type(provider).__module__}.{type(provider).__qualname__}",
            "wire_api": str(getattr(provider, "wire_api", "") or ""),
            # 与 SDK 构建实际请求保持一致；显式覆盖模型时不继承另一模型的思考档位。
            "reasoning_effort": getattr(provider, "reasoning_effort", None)
            if model == getattr(provider, "model", None) else None,
        }
    return result


def _definition_snapshot(value: Any, *, model_client: Any | None = None) -> dict[str, Any]:
    """只保留执行所需定义字段，避免把 SQLAlchemy 对象写入 JSON。"""

    snapshot = {
        "id": int(value.id),
        "project_id": int(value.project_id) if value.project_id is not None else None,
        "key": str(getattr(value, "agent_key", getattr(value, "tool_key", ""))),
        "version": int(value.version) if getattr(value, "version", None) is not None else None,
        "name": str(value.name or ""),
        "description": str(value.description or ""),
        "instructions": str(getattr(value, "instructions", "") or ""),
        "model": str(getattr(value, "model", "") or ""),
        "handler_key": str(getattr(value, "handler_key", "") or ""),
        "input_schema": deepcopy(getattr(value, "input_schema", {}) or {}),
        "output_schema": deepcopy(getattr(value, "output_schema", {}) or {}),
        "runtime_config": deepcopy(getattr(value, "runtime_config", {}) or {}),
        "risk_level": str(getattr(value, "risk_level", "") or ""),
        "requires_approval": bool(getattr(value, "requires_approval", False)),
    }
    if getattr(value, "agent_key", None) is not None:
        snapshot["model_routes"] = model_route_snapshot(model_client, value)
    return snapshot


def _declared_subagent_keys(runtime_config: dict[str, Any]) -> list[str]:
    """读取契约构建所需的专业 Agent 声明，避免引入 SDK 执行器依赖。"""

    values = runtime_config.get("subagent_keys") or []
    if not isinstance(values, list):
        raise ValueError("Agent subagent_keys 必须是数组")
    return [
        str(value).strip()
        for value in values
        if str(value).strip()
    ]


def build_execution_contract(
    *,
    workflow: Any,
    execution: Any,
    definitions: Any,
    runtime_registry_signature: str,
    model_client: Any | None = None,
) -> dict[str, Any]:
    """生成创建运行时的工作流、Agent、工具和运行时契约快照。"""

    agent_snapshots: dict[str, dict[str, Any]] = {}
    tool_snapshots: dict[str, dict[str, Any]] = {}

    nodes = list(getattr(execution, "nodes", []) or [])
    entry_agent_key = getattr(execution, "entry_agent_key", None)
    if entry_agent_key:
        nodes.append(SimpleNamespace(
            node_type="agent_network",
            reference_key=entry_agent_key,
        ))

    def capture_agent(
        definition: Any,
        bound_tools: list[Any],
        active_keys: tuple[str, ...] = (),
    ) -> None:
        """递归冻结 Agent 及其专业子 Agent，避免恢复时回读当前 catalog。"""

        definition_id = int(definition.id)
        definition_key = str(definition.agent_key)
        if definition_key in active_keys:
            cycle = " -> ".join((*active_keys, definition_key))
            raise ValueError(f"Agent 子 Agent 声明存在循环: {cycle}")
        if str(definition_id) in agent_snapshots:
            return
        agent_snapshot = _definition_snapshot(definition, model_client=model_client)
        agent_snapshot["tool_ids"] = [int(tool.id) for tool in bound_tools]
        agent_snapshots[str(definition_id)] = agent_snapshot
        for tool in bound_tools:
            tool_snapshots[str(tool.id)] = _definition_snapshot(tool)

        runtime_config = dict(getattr(definition, "runtime_config", {}) or {})
        managed_values = runtime_config.get("managed_agent_keys") or []
        if not isinstance(managed_values, list):
            raise ValueError("Agent managed_agent_keys 必须是数组")
        child_keys = [
            *_declared_subagent_keys(runtime_config),
            *[
                str(value).strip()
                for value in managed_values
                if str(value).strip()
            ],
        ]
        if len(child_keys) != len(set(child_keys)):
            raise ValueError(f"Agent 子 Agent 声明存在重复项: {definition_key}")
        for child_key in child_keys:
            if child_key == definition_key:
                raise ValueError(f"Agent 不能把自身注册为子 Agent: {child_key}")
            child = definitions.get_agent(
                project_id=int(workflow.project_id), agent_key=child_key,
            )
            if child is None:
                # 保持创建运行时的错误边界：子 Agent 缺失时不生成不完整契约。
                raise LookupError(f"找不到专业 Agent 定义: {child_key}")
            child_tools = definitions.list_agent_tools(
                int(child.id), project_id=int(workflow.project_id),
            )
            capture_agent(child, child_tools, (*active_keys, definition_key))

    for node in nodes:
        node_type = str(getattr(node, "node_type", ""))
        reference_key = str(getattr(node, "reference_key", ""))
        if node_type in {"agent", "agent_network", "agent_map"}:
            definition = definitions.get_agent(
                project_id=int(workflow.project_id),
                agent_key=reference_key,
            )
            if definition is None:
                raise LookupError(f"找不到工作流 Agent 定义: {reference_key}")
            bound_tools = definitions.list_agent_tools(
                int(definition.id), project_id=int(workflow.project_id),
            )
            capture_agent(definition, bound_tools)
        elif node_type == "tool":
            definition = definitions.get_tool(
                project_id=int(workflow.project_id),
                tool_key=reference_key,
            )
            if definition is None:
                raise LookupError(f"找不到工作流工具定义: {reference_key}")
            tool_snapshots[str(definition.id)] = _definition_snapshot(definition)

    contract = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "workflow": {
            "id": int(workflow.id),
            "project_id": int(workflow.project_id),
            "workflow_key": str(workflow.workflow_key),
            "version": int(workflow.version),
            "definition": deepcopy(workflow.definition or {}),
        },
        "agents": sorted(agent_snapshots.values(), key=lambda item: (item["key"], item["id"])),
        "tools": sorted(tool_snapshots.values(), key=lambda item: (item["key"], item["id"])),
        "runtime_registry_signature": str(runtime_registry_signature),
    }
    contract["hash"] = _canonical_hash(contract)
    return contract



