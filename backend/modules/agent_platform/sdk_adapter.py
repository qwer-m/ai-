from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import ValidationError, validate
from openai import AsyncOpenAI

from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
    RunConfig,
    Runner,
)
from pydantic import BaseModel, ConfigDict, create_model

from core.ai.ai_client import get_client_for_user
from core.ai.providers.openai_compatible_provider import OpenAICompatibleProvider
from core.db.model_defs import AgentDefinition, AgentToolDefinition
from .registry import ToolExecutionContext, tool_registry


@dataclass(frozen=True)
class AgentExecutionResult:
    output: dict[str, Any]
    final_text: str
    last_agent_name: str
    usage: dict[str, int]


def _schema_python_type(schema: dict[str, Any], name: str) -> Any:
    if "const" in schema:
        return Literal[schema["const"]]
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return Literal[tuple(enum_values)]
    schema_type = schema.get("type")
    if schema_type == "string":
        return str
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        item_type = _schema_python_type(dict(schema.get("items") or {}), f"{name}Item")
        return list[item_type]
    if schema_type == "object":
        return _output_model_from_schema(schema, name)
    return Any


def _output_model_from_schema(schema: dict[str, Any], name: str) -> type[BaseModel]:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValueError("智能体输出 JSON Schema 的对象必须声明 properties")
    fields: dict[str, tuple[Any, Any]] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_name, str) or not field_name.isidentifier():
            raise ValueError(f"智能体输出字段不是合法标识符: {field_name}")
        fields[field_name] = (
            _schema_python_type(dict(field_schema or {}), f"{name}_{field_name}"),
            ...,
        )
    model_name = re.sub(r"[^0-9A-Za-z_]", "_", name) or "AgentOutput"
    return create_model(
        model_name,
        __config__=ConfigDict(extra="ignore"),
        **fields,
    )


def _resolve_provider(client: Any, route: str) -> tuple[Any, str]:
    normalized = str(route or "main").strip().lower()
    if normalized == "turbo" and getattr(client, "turbo_model", ""):
        return (
            getattr(client, "turbo_provider", None) or client.provider,
            str(client.turbo_model),
        )
    if normalized == "review" and getattr(client, "review_model", ""):
        return (
            getattr(client, "review_provider", None) or client.provider,
            str(client.review_model),
        )
    return client.provider, str(client.model or "")


def _sdk_model(client: Any, agent_definition: AgentDefinition) -> Any:
    runtime_config = dict(agent_definition.runtime_config or {})
    provider, routed_model = _resolve_provider(
        client,
        str(runtime_config.get("model_route") or "main"),
    )
    if not isinstance(provider, OpenAICompatibleProvider):
        raise RuntimeError(
            f"Agents SDK 当前只接入 OpenAI-compatible provider，实际为 {type(provider).__name__}"
        )
    model_name = str(agent_definition.model or routed_model or provider.model).strip()
    if not model_name:
        raise RuntimeError("智能体未解析到可用模型")
    openai_client = AsyncOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        timeout=provider._http_timeout(),
    )
    if str(getattr(provider, "wire_api", "chat_completions")) == "responses":
        return OpenAIResponsesModel(model=model_name, openai_client=openai_client)
    return OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=openai_client,
        strict_feature_validation=False,
    )


def _function_tool(
    definition: AgentToolDefinition,
    execution_context: ToolExecutionContext,
) -> FunctionTool:
    handler = tool_registry.resolve(definition.handler_key)

    async def invoke(_tool_context: Any, arguments_json: str) -> str:
        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"工具参数不是合法 JSON: {exc}") from exc
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        validate(instance=arguments, schema=dict(definition.input_schema or {}))
        result = handler(execution_context, arguments)
        validate(instance=result, schema=dict(definition.output_schema or {}))
        execution_context.executed_tools.append(definition.tool_key)
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    return FunctionTool(
        name=definition.tool_key,
        description=definition.description or definition.name,
        params_json_schema=dict(definition.input_schema or {}),
        on_invoke_tool=invoke,
        strict_json_schema=True,
        needs_approval=False,
    )


def _normalize_final_output(value: Any, schema: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if hasattr(value, "model_dump"):
        normalized: Any = value.model_dump()
        final_text = json.dumps(normalized, ensure_ascii=False)
    elif isinstance(value, dict):
        normalized = value
        final_text = json.dumps(value, ensure_ascii=False)
    else:
        final_text = str(value or "").strip()
        try:
            normalized = json.loads(final_text)
        except json.JSONDecodeError as exc:
            if schema:
                raise ValueError("智能体最终输出不满足 JSON 契约") from exc
            normalized = {"text": final_text}
    if not isinstance(normalized, dict):
        normalized = {"value": normalized}
    if schema:
        try:
            validate(instance=normalized, schema=schema)
        except ValidationError as exc:
            raise ValueError(f"智能体最终输出契约校验失败: {exc.message}") from exc
    return normalized, final_text


def run_agent(
    *,
    db: Any,
    agent_definition: AgentDefinition,
    tool_definitions: list[AgentToolDefinition],
    execution_context: ToolExecutionContext,
    input_payload: dict[str, Any],
) -> AgentExecutionResult:
    client = get_client_for_user(execution_context.user_id, db)
    model = _sdk_model(client, agent_definition)
    tools = [
        _function_tool(definition, execution_context)
        for definition in tool_definitions
    ]
    runtime_config = dict(agent_definition.runtime_config or {})
    agent = Agent(
        name=agent_definition.name,
        instructions=agent_definition.instructions,
        model=model,
        tools=tools,
        model_settings=ModelSettings(
            tool_choice="required" if runtime_config.get("require_tool") else None,
        ),
        output_type=(
            _output_model_from_schema(
                dict(agent_definition.output_schema or {}),
                f"{agent_definition.agent_key}_output",
            )
            if agent_definition.output_schema
            else None
        ),
    )
    result = Runner.run_sync(
        agent,
        json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")),
        context=execution_context,
        max_turns=int(runtime_config.get("max_turns") or 8),
        run_config=RunConfig(
            tracing_disabled=True,
            workflow_name=agent_definition.agent_key,
            trace_include_sensitive_data=False,
        ),
    )
    if result.interruptions:
        raise RuntimeError("SDK 内部工具审批尚未映射到平台审批节点")
    if runtime_config.get("require_tool") and not execution_context.executed_tools:
        raise RuntimeError("智能体未执行要求的真实工具，拒绝接受模型摘要")
    output, final_text = _normalize_final_output(
        result.final_output,
        dict(agent_definition.output_schema or {}),
    )
    usage = getattr(result.context_wrapper, "usage", None)
    usage_payload = {
        "requests": int(getattr(usage, "requests", 0) or 0),
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    return AgentExecutionResult(
        output=output,
        final_text=final_text,
        last_agent_name=str(result.last_agent.name or ""),
        usage=usage_payload,
    )
