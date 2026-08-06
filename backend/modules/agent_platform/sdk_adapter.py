from __future__ import annotations

import base64
import json
import mimetypes
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

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
from agents.exceptions import ModelBehaviorError
from agents.items import ItemHelpers
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model

from core.ai.ai_client import get_client_for_user
from core.ai.providers.openai_compatible_provider import OpenAICompatibleProvider
from core.db.model_defs import AgentDefinition, AgentToolDefinition, KnowledgeDocument
from modules.knowledge_base_components.document.document_asset_service import (
    document_page_image_path,
    load_document_manifest,
)
from .registry import ToolExecutionContext, tool_registry


@dataclass(frozen=True)
class AgentExecutionResult:
    output: dict[str, Any]
    final_text: str
    last_agent_name: str
    usage: dict[str, int]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def _unique_json_items(values: list[Any]) -> list[Any]:
    fingerprints: list[str] = []
    for value in values:
        serializable = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        fingerprints.append(
            json.dumps(serializable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("数组元素必须唯一")
    return values


def _schema_python_type(schema: dict[str, Any], name: str) -> Any:
    if "const" in schema:
        return Literal[schema["const"]]
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return Literal[tuple(enum_values)]
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        concrete_types = [value for value in schema_type if value != "null"]
        if len(concrete_types) == 1 and len(concrete_types) != len(schema_type):
            concrete_schema = dict(schema)
            concrete_schema["type"] = concrete_types[0]
            return _schema_python_type(concrete_schema, name) | None
        return Any
    if schema_type == "string":
        return Annotated[
            str,
            Field(
                min_length=schema.get("minLength"),
                max_length=schema.get("maxLength"),
                pattern=schema.get("pattern"),
            ),
        ]
    if schema_type == "integer":
        return Annotated[
            int,
            Field(
                ge=schema.get("minimum"),
                le=schema.get("maximum"),
                gt=schema.get("exclusiveMinimum"),
                lt=schema.get("exclusiveMaximum"),
                multiple_of=schema.get("multipleOf"),
            ),
        ]
    if schema_type == "number":
        return Annotated[
            float,
            Field(
                ge=schema.get("minimum"),
                le=schema.get("maximum"),
                gt=schema.get("exclusiveMinimum"),
                lt=schema.get("exclusiveMaximum"),
                multiple_of=schema.get("multipleOf"),
            ),
        ]
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        item_type = _schema_python_type(dict(schema.get("items") or {}), f"{name}Item")
        metadata: list[Any] = [
            Field(
                min_length=schema.get("minItems"),
                max_length=schema.get("maxItems"),
                json_schema_extra=(
                    {"uniqueItems": True} if schema.get("uniqueItems") is True else None
                ),
            )
        ]
        if schema.get("uniqueItems") is True:
            metadata.append(AfterValidator(_unique_json_items))
        return Annotated[list[item_type], *metadata]
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
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _resolve_provider(client: Any, route: str) -> tuple[Any, str]:
    normalized = str(route or "main").strip().lower()
    if normalized == "vision":
        if not getattr(client, "vl_model", ""):
            raise RuntimeError("视觉智能体未配置独立可用的视觉模型")
        return (
            getattr(client, "vl_provider", None) or client.provider,
            str(client.vl_model),
        )
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
    raw_timeout = runtime_config.get("request_timeout_seconds")
    request_timeout = None
    if raw_timeout not in (None, ""):
        request_timeout = float(raw_timeout)
        if request_timeout <= 0:
            raise ValueError("Agent request_timeout_seconds 必须大于 0")
    openai_client = AsyncOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        timeout=provider._http_timeout(request_timeout),
        # 重试由平台节点/映射项显式管理，避免 SDK 隐式重复整段长输出。
        max_retries=0,
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
        execution_context.tool_calls.append(
            {
                "tool_key": definition.tool_key,
                "arguments": arguments,
                "result": result,
            }
        )
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


def _runner_input(
    *,
    execution_context: ToolExecutionContext,
    input_payload: dict[str, Any],
    runtime_config: dict[str, Any],
) -> str | list[dict[str, Any]]:
    """构造 SDK 输入；视觉节点只在调用期装载图片，不把 base64 写入运行记录。"""

    payload_text = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
    input_mode = str(runtime_config.get("input_mode") or "text").strip().lower()
    if input_mode == "text":
        return payload_text
    if input_mode == "document_page_optional_image" and str(
        input_payload.get("source_kind") or ""
    ) == "inline":
        return payload_text
    if input_mode not in {"document_page_image", "document_page_optional_image"}:
        raise ValueError(f"不支持的 Agent input_mode: {input_mode}")

    document_id = int(input_payload.get("document_id") or 0)
    page_number = int(input_payload.get("page_number") or 0)
    if document_id < 1 or page_number < 1:
        raise ValueError("多模态文档页输入缺少有效 document_id 或 page_number")
    document = (
        execution_context.db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.project_id == execution_context.project_id,
        )
        .first()
    )
    if document is None or document.user_id not in (
        None,
        0,
        execution_context.user_id,
    ):
        raise ValueError("多模态文档页不存在或无权读取")
    if str(document.parse_status or "") != "success":
        raise ValueError("多模态文档页资产尚未准备完成")

    image_path = document_page_image_path(document_id, page_number)
    manifest = load_document_manifest(document_id)
    if str(input_payload.get("asset_source_sha256") or "") != str(
        manifest.get("source_sha256") or ""
    ):
        raise ValueError("多模态输入的文档资产指纹已变化")
    page_asset = next(
        (
            dict(page)
            for page in list(manifest.get("pages") or [])
            if int(page.get("page_number") or 0) == page_number
        ),
        None,
    )
    if page_asset is None or str(input_payload.get("page_image_sha256") or "") != str(
        page_asset.get("image_sha256") or ""
    ):
        raise ValueError("多模态输入的页面图像指纹已变化")
    region = dict(input_payload.get("region") or {})
    if region != {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}:
        raise ValueError("当前多模态 Agent 只接受已声明的完整页面图像")
    media_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": payload_text},
                {
                    "type": "input_image",
                    "image_url": f"data:{media_type};base64,{encoded}",
                    "detail": "high",
                },
            ],
        }
    ]


def _validate_runtime_config(runtime_config: dict[str, Any], *, has_tools: bool) -> None:
    if int(runtime_config.get("max_retries", 0) or 0) != 0:
        raise ValueError("Agent 禁止 SDK 隐式重试，请使用工作流节点的 max_attempts")
    model_route = str(runtime_config.get("model_route") or "main").strip().lower()
    if model_route not in {"main", "turbo", "review", "vision"}:
        raise ValueError(f"不支持的 Agent model_route: {model_route}")
    input_mode = str(runtime_config.get("input_mode") or "text").strip().lower()
    if input_mode not in {
        "text",
        "document_page_image",
        "document_page_optional_image",
    }:
        raise ValueError(f"不支持的 Agent input_mode: {input_mode}")
    if input_mode in {"document_page_image", "document_page_optional_image"}:
        if model_route != "vision":
            raise ValueError("document_page_image 输入必须使用 vision 模型路由")
        if has_tools:
            raise ValueError("直接页图多模态 Agent 不允许叠加工具调用")


def _effective_max_turns(
    runtime_config: dict[str, Any],
    *,
    has_tools: bool,
) -> int:
    raw_max_turns = runtime_config.get("max_turns")
    if raw_max_turns in (None, ""):
        return 8 if has_tools else 1
    max_turns = int(raw_max_turns)
    if max_turns <= 0:
        raise ValueError("Agent max_turns 必须大于 0")
    if not has_tools and max_turns != 1:
        raise ValueError("无工具 Agent 的 max_turns 必须为 1")
    return max_turns


def _raise_no_tool_invalid_final_output_error(handler_input: Any) -> None:
    responses = list(handler_input.run_data.raw_responses or [])
    summaries: list[str] = []
    for index, response in enumerate(responses, start=1):
        output_items = list(getattr(response, "output", None) or [])
        content_length = sum(
            len(ItemHelpers.extract_text(item) or "") for item in output_items
        )
        usage = getattr(response, "usage", None)
        reasoning_details = getattr(usage, "output_tokens_details", None)
        summaries.append(
            "#{}:output_types={},content_length={},output_tokens={},reasoning_tokens={}".format(
                index,
                [str(getattr(item, "type", type(item).__name__)) for item in output_items],
                content_length,
                int(getattr(usage, "output_tokens", 0) or 0),
                int(getattr(reasoning_details, "reasoning_tokens", 0) or 0),
            )
        )
    detail = ";".join(summaries) if summaries else "no_model_response"
    raise ModelBehaviorError(
        "无工具 Agent 未返回可校验的最终结构化正文；"
        f"response_count={len(responses)}；{detail}"
    )


def run_agent(
    *,
    db: Any,
    agent_definition: AgentDefinition,
    tool_definitions: list[AgentToolDefinition],
    execution_context: ToolExecutionContext,
    input_payload: dict[str, Any],
) -> AgentExecutionResult:
    tool_call_start = len(execution_context.tool_calls)
    client = get_client_for_user(execution_context.user_id, db)
    runtime_config = dict(agent_definition.runtime_config or {})
    _validate_runtime_config(runtime_config, has_tools=bool(tool_definitions))
    model = _sdk_model(client, agent_definition)
    tools = [
        _function_tool(definition, execution_context)
        for definition in tool_definitions
    ]
    raw_max_output_tokens = runtime_config.get("max_output_tokens")
    max_output_tokens = None
    if raw_max_output_tokens not in (None, ""):
        max_output_tokens = int(raw_max_output_tokens)
        if max_output_tokens <= 0:
            raise ValueError("Agent max_output_tokens 必须大于 0")
    agent = Agent(
        name=agent_definition.name,
        instructions=agent_definition.instructions,
        model=model,
        tools=tools,
        model_settings=ModelSettings(
            max_tokens=max_output_tokens,
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
        _runner_input(
            execution_context=execution_context,
            input_payload=input_payload,
            runtime_config=runtime_config,
        ),
        context=execution_context,
        max_turns=_effective_max_turns(
            runtime_config,
            has_tools=bool(tool_definitions),
        ),
        run_config=RunConfig(
            tracing_disabled=True,
            workflow_name=agent_definition.agent_key,
            trace_include_sensitive_data=False,
        ),
        error_handlers=(
            {"invalid_final_output": _raise_no_tool_invalid_final_output_error}
            if not tool_definitions
            else None
        ),
    )
    if result.interruptions:
        raise RuntimeError("SDK 内部工具审批尚未映射到平台审批节点")
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
        tool_calls=list(execution_context.tool_calls[tool_call_start:]),
    )
