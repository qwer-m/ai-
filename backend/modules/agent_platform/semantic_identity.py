from __future__ import annotations

import hashlib
import json
import sys
from functools import lru_cache
from importlib.metadata import version
from typing import Any

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.model_defs import AgentDefinition, AgentToolDefinition

from .contracts import WorkflowNode
from .definition_repository import AgentDefinitionRepository
from .registry import runtime_registry_signature, tool_registry
from .sdk_adapter import _resolved_agent_model, _subagent_keys, _validate_runtime_config


def _hash_contract(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=1)
def _sdk_dependency_versions() -> dict[str, str]:
    """SDK 和结构校验库升级也必须使旧语义身份失效。"""

    return {
        "python": sys.version,
        **{name: version(name) for name in (
            "openai-agents", "openai", "pydantic", "jsonschema", "json-repair",
            "pdfplumber", "pypdfium2", "Pillow",
        )},
    }


def _handler_contract(handler_key: str) -> dict[str, str] | None:
    if not handler_key:
        return None
    handler = tool_registry.resolve(handler_key)
    return {
        "key": handler_key,
        "callable": f"{handler.__module__}.{handler.__qualname__}",
    }


def _tool_contract(tool: AgentToolDefinition) -> dict[str, Any]:
    return {
        "id": int(tool.id) if tool.id is not None else None,
        "key": tool.tool_key,
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.input_schema or {}),
        "output_schema": dict(tool.output_schema or {}),
        "requires_approval": bool(tool.requires_approval),
        "enabled": bool(tool.enabled),
        "handler": _handler_contract(str(tool.handler_key)),
    }


def _model_contract(client: Any, definition: AgentDefinition) -> list[dict[str, str]]:
    """复用 SDK 的真实路由解析，认证信息不参与执行语义身份。"""

    runtime_config = dict(definition.runtime_config or {})
    routes: list[str | None] = [None]
    fallback_route = str(runtime_config.get("transient_fallback_model_route") or "").strip()
    if fallback_route:
        routes.append(fallback_route)
    models = []
    for route_override in routes:
        provider, model, route = _resolved_agent_model(
            client, definition, model_route_override=route_override,
        )
        models.append({
            "route": route,
            "model": model,
            # 沿用 AIClient 的公开路由身份，包含完整端点和协议而不读取 api_key。
            "provider_hash": client._provider_cache_identity(provider),
        })
    return models


def agent_execution_semantic_hash(
    *,
    db: Session,
    user_id: int,
    project_id: int,
    node: WorkflowNode,
    definition: AgentDefinition,
    tools: list[AgentToolDefinition],
    client: Any | None = None,
    definition_snapshots: dict[str, tuple[AgentDefinition, list[AgentToolDefinition]]] | None = None,
) -> str:
    """为当前真实执行契约生成统一指纹，供结果缓存和持久行动恢复使用。

    数据库定义、实际模型路由、工具与子 Agent 契约共同决定身份；源码指纹覆盖
    SDK、节点执行器和后处理间接调用的生产源码。实际请求传入 SDK 构建时捕获的
    客户端和定义快照，禁止以请求完成时的数据库配置替代实际调用契约。
    """

    repo = AgentDefinitionRepository(db) if definition_snapshots is None else None
    if definition_snapshots is not None and client is None:
        raise ValueError("实际构建的 Agent 快照必须同时提供实际模型客户端")
    resolved_client = client if client is not None else get_client_for_user(user_id, db)

    def captured_definition(key: str) -> tuple[AgentDefinition, list[AgentToolDefinition]]:
        snapshot = definition_snapshots.get(key) if definition_snapshots is not None else None
        if snapshot is None or snapshot[0].agent_key != key:
            raise ValueError(f"执行身份缺少实际构建的 Agent 快照: {key}")
        return snapshot

    if definition_snapshots is not None:
        definition, tools = captured_definition(str(definition.agent_key))

    def agent_contract(
        agent: AgentDefinition,
        bound_tools: list[AgentToolDefinition],
        *,
        allow_subagents: bool,
        allow_managed: bool = False,
    ) -> dict[str, Any]:
        runtime_config = dict(agent.runtime_config or {})
        # 缓存开关和候选数量属于复用策略，不属于生成结果的语义契约。
        runtime_config.pop("result_cache", None)
        managed_keys = runtime_config.get("managed_agent_keys") or []
        if managed_keys and not allow_managed:
            raise ValueError(f"专业 Agent 禁止再建立托管协调层: {agent.agent_key}")
        if not isinstance(managed_keys, list) or any(
            not isinstance(key, str) or not key.strip() for key in managed_keys
        ):
            raise ValueError(f"Agent managed_agent_keys 必须是非空名称数组: {agent.agent_key}")
        subagent_keys = [*_subagent_keys(runtime_config), *(key.strip() for key in managed_keys)]
        if subagent_keys and not allow_subagents:
            raise ValueError(f"专业 Agent 禁止继续嵌套子 Agent: {agent.agent_key}")
        if len(subagent_keys) != len(set(subagent_keys)):
            raise ValueError(f"Agent 的子 Agent 声明存在重复项: {agent.agent_key}")
        subagents = []
        for key in subagent_keys:
            if key == agent.agent_key:
                raise ValueError(f"Agent 不能把自身注册为工具: {key}")
            if definition_snapshots is not None:
                child, child_tools = captured_definition(key)
            else:
                assert repo is not None
                child = repo.get_agent(project_id=project_id, agent_key=key)
                if child is None:
                    raise LookupError(f"找不到专业 Agent 定义: {key}")
                child_tools = repo.list_agent_tools(child.id, project_id=project_id)
            subagents.append(agent_contract(
                child, child_tools, allow_subagents=key in managed_keys,
            ))
        _validate_runtime_config(runtime_config, has_tools=bool(bound_tools or subagents))
        return {
            "id": int(agent.id) if agent.id is not None else None,
            "key": agent.agent_key,
            "name": agent.name,
            "description": agent.description,
            "instructions": agent.instructions,
            "model": agent.model,
            "output_schema": dict(agent.output_schema or {}),
            "runtime_config": runtime_config,
            "enabled": bool(agent.enabled),
            "models": _model_contract(resolved_client, agent),
            "tools": [_tool_contract(tool) for tool in bound_tools],
            "subagents": subagents,
            "postprocessor": _handler_contract(
                str(runtime_config.get("output_postprocessor") or "").strip(),
            ),
            "state_handlers": {
                key: _handler_contract(str(runtime_config.get(key) or "").strip())
                for key in ("state_initializer", "state_observer")
            },
        }

    item_postprocessor = node.map_config.item_postprocessor if node.map_config is not None else None
    return _hash_contract({
        "runtime_signature": runtime_registry_signature(),
        "sdk_dependencies": _sdk_dependency_versions(),
        "node": node.model_dump(mode="json"),
        "agent": agent_contract(definition, tools, allow_subagents=True, allow_managed=True),
        "item_postprocessor": _handler_contract(str(item_postprocessor or "").strip()),
    })
