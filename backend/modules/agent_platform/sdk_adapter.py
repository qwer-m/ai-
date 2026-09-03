from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from jsonschema import ValidationError, validate
from json_repair import repair_json
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
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model

from core.ai.ai_client import get_client_for_user
from core.ai.providers.openai_compatible_provider import OpenAICompatibleProvider
from core.db.model_defs import AgentDefinition, AgentToolDefinition, KnowledgeDocument
from modules.knowledge_base_components.document.document_asset_service import (
    document_page_image_path,
    load_document_manifest,
)
from .registry import ToolExecutionContext, tool_registry
from .repository import AgentPlatformRepository


@dataclass(frozen=True)
class AgentExecutionResult:
    output: dict[str, Any]
    final_text: str
    last_agent_name: str
    usage: dict[str, int]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ToolArgumentsJSONError(ModelBehaviorError):
    """模型生成的工具参数不是合法 JSON。"""

    def __init__(
        self,
        *,
        tool_key: str,
        arguments_json: str,
        json_error: json.JSONDecodeError,
    ) -> None:
        position = max(0, int(json_error.pos))
        excerpt_start = max(0, position - 120)
        excerpt_end = min(len(arguments_json), position + 120)
        excerpt = arguments_json[excerpt_start:excerpt_end]
        self.diagnostic = {
            "tool_key": tool_key,
            "arguments_chars": len(arguments_json),
            "arguments_sha256": hashlib.sha256(
                arguments_json.encode("utf-8")
            ).hexdigest(),
            "error_line": int(json_error.lineno),
            "error_column": int(json_error.colno),
            "error_position": position,
            "excerpt_start": excerpt_start,
            "arguments_excerpt": excerpt,
            "excerpt_truncated_before": excerpt_start > 0,
            "excerpt_truncated_after": excerpt_end < len(arguments_json),
        }
        excerpt_text = json.dumps(excerpt, ensure_ascii=False)
        super().__init__(
            "工具参数不是合法 JSON: "
            f"tool={tool_key}; line={json_error.lineno}; column={json_error.colno}; "
            f"position={position}; chars={len(arguments_json)}; "
            f"sha256={self.diagnostic['arguments_sha256']}; "
            f"near_error={excerpt_text}"
        )


def _schema_missing_fields(validation_error: ValidationError) -> list[str]:
    if validation_error.validator != "required" or not isinstance(
        validation_error.instance, dict
    ):
        return []
    return [
        str(field)
        for field in list(validation_error.validator_value or [])
        if field not in validation_error.instance
    ]


class ToolArgumentsValidationError(ModelBehaviorError):
    """模型生成的工具参数不符合工具输入 Schema。"""

    def __init__(
        self,
        *,
        tool_key: str,
        arguments_json: str,
        validation_error: ValidationError,
    ) -> None:
        instance_path = [str(part) for part in validation_error.absolute_path]
        schema_path = [str(part) for part in validation_error.absolute_schema_path]
        missing_fields = _schema_missing_fields(validation_error)
        excerpt = arguments_json[:240]
        self.diagnostic = {
            "tool_key": tool_key,
            "validation_keyword": str(validation_error.validator or "unknown"),
            "instance_path": instance_path,
            "schema_path": schema_path,
            "validation_message": validation_error.message,
            "missing_fields": missing_fields,
            "arguments_chars": len(arguments_json),
            "arguments_sha256": hashlib.sha256(
                arguments_json.encode("utf-8")
            ).hexdigest(),
            "arguments_excerpt": excerpt,
            "excerpt_truncated_after": len(excerpt) < len(arguments_json),
        }
        path_text = ".".join(instance_path) or "<root>"
        missing_text = (
            f"; missing_fields={json.dumps(missing_fields, ensure_ascii=False)}"
            if missing_fields
            else ""
        )
        super().__init__(
            "工具参数不符合 Schema: "
            f"tool={tool_key}; keyword={validation_error.validator or 'unknown'}; "
            f"path={path_text}; message={validation_error.message}{missing_text}"
        )


class ToolOutputValidationError(RuntimeError):
    """平台工具处理器返回值不符合已声明的输出 Schema。"""

    def __init__(
        self,
        *,
        tool_key: str,
        output: Any,
        validation_error: ValidationError,
    ) -> None:
        try:
            output_text = json.dumps(
                output,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=repr,
            )
        except (TypeError, ValueError):
            output_text = repr(output)
        output_path = [str(part) for part in validation_error.absolute_path]
        schema_path = [str(part) for part in validation_error.absolute_schema_path]
        missing_fields = _schema_missing_fields(validation_error)
        excerpt = output_text[:240]
        self.diagnostic = {
            "tool_key": tool_key,
            "validation_keyword": str(validation_error.validator or "unknown"),
            "output_path": output_path,
            "schema_path": schema_path,
            "validation_message": validation_error.message,
            "missing_fields": missing_fields,
            "output_type": type(output).__name__,
            "output_chars": len(output_text),
            "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
            "output_excerpt": excerpt,
            "excerpt_truncated_after": len(excerpt) < len(output_text),
        }
        path_text = ".".join(output_path) or "<root>"
        missing_text = (
            f"; missing_fields={json.dumps(missing_fields, ensure_ascii=False)}"
            if missing_fields
            else ""
        )
        super().__init__(
            "工具处理器输出不符合 Schema: "
            f"tool={tool_key}; keyword={validation_error.validator or 'unknown'}; "
            f"path={path_text}; message={validation_error.message}{missing_text}"
        )


def _longest_repeated_character_run(value: str) -> int:
    longest = 0
    current = 0
    previous = ""
    for character in value:
        if character == previous:
            current += 1
        else:
            previous = character
            current = 1
        longest = max(longest, current)
    return longest


class StructuredOutputJSONError(ModelBehaviorError):
    """智能体最终输出不是合法 JSON，并携带限长原文诊断。"""

    def __init__(self, *, output_text: str, json_error: json.JSONDecodeError) -> None:
        position = max(0, int(json_error.pos))
        excerpt_start = max(0, position - 240)
        excerpt_end = min(len(output_text), position + 240)
        control_character_count = sum(ord(character) < 0x20 for character in output_text)
        longest_repeated_run = _longest_repeated_character_run(output_text)
        is_output_degeneration = (
            control_character_count >= 32 or longest_repeated_run >= 64
        )
        self.diagnostic = {
            "output_chars": len(output_text),
            "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
            "error_line": int(json_error.lineno),
            "error_column": int(json_error.colno),
            "error_position": position,
            "output_excerpt": output_text[excerpt_start:excerpt_end],
            "excerpt_start": excerpt_start,
            "excerpt_truncated_before": excerpt_start > 0,
            "excerpt_truncated_after": excerpt_end < len(output_text),
            "control_character_count": control_character_count,
            "longest_repeated_character_run": longest_repeated_run,
            "is_output_degeneration": is_output_degeneration,
        }
        category = "输出发生重复字符退化" if is_output_degeneration else "最终输出不是合法 JSON"
        super().__init__(
            f"智能体{category}: 第 {json_error.lineno} 行第 {json_error.colno} 列; "
            f"{json_error.msg}; chars={len(output_text)}; "
            f"sha256={self.diagnostic['output_sha256']}"
        )


class StructuredOutputValidationError(ModelBehaviorError):
    """智能体最终 JSON 不符合平台声明的输出 Schema。"""

    def __init__(
        self,
        *,
        output: dict[str, Any],
        output_schema: dict[str, Any],
        validation_error: ValidationError,
    ) -> None:
        output_text = json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        field_path = [str(part) for part in validation_error.absolute_path]
        missing_fields = _schema_missing_fields(validation_error)
        expected_fields = [
            str(field)
            for field in dict(validation_error.schema or {}).get("properties", {})
        ]
        self.candidate_output = output
        self.output_schema = output_schema
        self.diagnostic = {
            "validation_keyword": str(validation_error.validator or "unknown"),
            "field_path": field_path,
            "schema_path": [
                str(part) for part in validation_error.absolute_schema_path
            ],
            "validation_message": validation_error.message,
            "missing_fields": missing_fields,
            "expected_fields": expected_fields,
            "output_chars": len(output_text),
            "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
            "output_excerpt": output_text[:480],
            "excerpt_truncated_after": len(output_text) > 480,
        }
        path_text = ".".join(field_path) or "<root>"
        detail = f"; 缺少字段={json.dumps(missing_fields, ensure_ascii=False)}" if missing_fields else ""
        if expected_fields:
            detail += f"; 期望字段={json.dumps(expected_fields, ensure_ascii=False)}"
        super().__init__(
            "智能体最终输出契约校验失败: "
            f"字段={path_text}; {validation_error.message}{detail}"
        )


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
    variants = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(variants, list) and variants:
        variant_types = [
            _schema_python_type(dict(variant or {}), f"{name}Variant{index + 1}")
            for index, variant in enumerate(variants)
        ]
        result = variant_types[0]
        for variant_type in variant_types[1:]:
            result = result | variant_type
        return result
    if "const" in schema:
        return Literal[schema["const"]]
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return Literal[tuple(enum_values)]
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        member_types: list[Any] = []
        for index, member in enumerate(schema_type):
            if member == "null":
                member_types.append(type(None))
                continue
            member_schema = dict(schema)
            member_schema["type"] = member
            member_types.append(
                _schema_python_type(member_schema, f"{name}Type{index + 1}")
            )
        result = member_types[0]
        for member_type in member_types[1:]:
            result = result | member_type
        return result
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
    required_fields = set(schema.get("required") or [])
    for field_name, field_schema in properties.items():
        if not isinstance(field_name, str) or not field_name.isidentifier():
            raise ValueError(f"智能体输出字段不是合法标识符: {field_name}")
        field_type = _schema_python_type(
            dict(field_schema or {}),
            f"{name}_{field_name}",
        )
        if field_name in required_fields:
            fields[field_name] = (field_type, ...)
        else:
            fields[field_name] = (field_type | None, None)
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
    if normalized == "review":
        if not getattr(client, "review_model", ""):
            raise RuntimeError("独立评审模型未配置，禁止使用主模型冒充独立终审")
        review_provider = getattr(client, "review_provider", None) or client.provider
        review_model = str(client.review_model)
        same_endpoint = str(getattr(review_provider, "base_url", "") or "").rstrip(
            "/"
        ) == str(getattr(client.provider, "base_url", "") or "").rstrip("/")
        if same_endpoint and review_model == str(client.model or ""):
            raise RuntimeError("评审路由与主模型完全相同，无法履行独立终审职责")
        return (
            review_provider,
            review_model,
        )
    return client.provider, str(client.model or "")


def _resolved_agent_model(
    client: Any,
    agent_definition: AgentDefinition,
    *,
    model_route_override: str | None = None,
) -> tuple[Any, str, str]:
    runtime_config = dict(agent_definition.runtime_config or {})
    model_route = str(
        model_route_override or runtime_config.get("model_route") or "main"
    ).strip().lower()
    provider, routed_model = _resolve_provider(client, model_route)
    configured_model = str(agent_definition.model or "").strip()
    model_name = str(configured_model or routed_model or provider.model).strip()
    if not model_name:
        raise RuntimeError("智能体未解析到可用模型")
    return provider, model_name, model_route


def resolve_agent_model_metadata(
    *,
    db: Any,
    user_id: int,
    agent_definition: AgentDefinition,
) -> dict[str, str]:
    """解析本次 Agent 实际采用的模型和路由来源。"""

    client = get_client_for_user(user_id, db)
    _, model_name, model_route = _resolved_agent_model(client, agent_definition)
    configured_model = str(agent_definition.model or "").strip()
    route_labels = {
        "main": "主模型路由",
        "vision": "视觉模型路由",
        "review": "评审模型路由",
        "turbo": "快速模型路由",
    }
    return {
        "name": model_name,
        "route": model_route,
        "source": "Agent 固定配置" if configured_model else route_labels.get(model_route, model_route),
    }


def _sdk_model(
    client: Any,
    agent_definition: AgentDefinition,
    *,
    request_timeout_seconds: float | None = None,
    allocated_clients: list[AsyncOpenAI] | None = None,
    model_route_override: str | None = None,
) -> Any:
    runtime_config = dict(agent_definition.runtime_config or {})
    provider, model_name, _ = _resolved_agent_model(
        client,
        agent_definition,
        model_route_override=model_route_override,
    )
    if not isinstance(provider, OpenAICompatibleProvider):
        raise RuntimeError(
            f"Agents SDK 当前只接入 OpenAI-compatible provider，实际为 {type(provider).__name__}"
        )
    raw_timeout = (
        request_timeout_seconds
        if request_timeout_seconds is not None
        else runtime_config.get("request_timeout_seconds")
    )
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
    if allocated_clients is not None:
        allocated_clients.append(openai_client)
    if str(getattr(provider, "wire_api", "chat_completions")) == "responses":
        return OpenAIResponsesModel(model=model_name, openai_client=openai_client)
    return OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=openai_client,
        strict_feature_validation=False,
    )


def _normalize_json_encoded_schema_values(value: Any, schema: dict[str, Any]) -> Any:
    """将模型二次序列化的结构化字段还原为 Schema 声明的真实类型。"""

    expected_type = schema.get("type")
    expected_types = (
        set(expected_type)
        if isinstance(expected_type, list)
        else {expected_type}
        if isinstance(expected_type, str)
        else set()
    )
    if isinstance(value, str) and expected_types.intersection({"array", "object"}):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            try:
                # 部分兼容网关会把结构字段二次序列化，并遗漏内部文案引号转义；
                # 这里只恢复 Schema 明确声明的数组或对象，最终仍由完整工具契约校验。
                decoded = repair_json(
                    value,
                    return_objects=True,
                    skip_json_loads=True,
                )
            except (TypeError, ValueError, IndexError):
                decoded = value
        if (
            "array" in expected_types
            and isinstance(decoded, list)
            or "object" in expected_types
            and isinstance(decoded, dict)
        ):
            value = decoded
    if isinstance(value, dict):
        properties = dict(schema.get("properties") or {})
        return {
            key: _normalize_json_encoded_schema_values(item, dict(properties.get(key) or {}))
            for key, item in value.items()
        }
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        item_schema = dict(schema["items"])
        return [
            _normalize_json_encoded_schema_values(item, item_schema)
            for item in value
        ]
    return value


def _function_tool(
    definition: AgentToolDefinition,
    execution_context: ToolExecutionContext,
) -> FunctionTool:
    handler = tool_registry.resolve(definition.handler_key)

    async def invoke(_tool_context: Any, arguments_json: str) -> str:
        raw_arguments = arguments_json or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ToolArgumentsJSONError(
                tool_key=definition.tool_key,
                arguments_json=raw_arguments,
                json_error=exc,
            ) from exc
        arguments = _normalize_json_encoded_schema_values(
            arguments,
            dict(definition.input_schema or {}),
        )
        try:
            validate(instance=arguments, schema=dict(definition.input_schema or {}))
        except ValidationError as exc:
            raise ToolArgumentsValidationError(
                tool_key=definition.tool_key,
                arguments_json=raw_arguments,
                validation_error=exc,
            ) from exc
        if not isinstance(arguments, dict):
            raise ValueError("工具输入 Schema 必须约束参数为 JSON 对象")
        result = handler(execution_context, arguments)
        try:
            validate(instance=result, schema=dict(definition.output_schema or {}))
        except ValidationError as exc:
            raise ToolOutputValidationError(
                tool_key=definition.tool_key,
                output=result,
                validation_error=exc,
            ) from exc
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


def _normalize_final_output(
    value: Any,
    schema: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if hasattr(value, "model_dump"):
        normalized: Any = value.model_dump()
        final_text = json.dumps(normalized, ensure_ascii=False)
    elif isinstance(value, dict):
        normalized = value
        final_text = json.dumps(value, ensure_ascii=False)
    else:
        # 只移除协议外层的普通空白；保留制表符等原始控制字符用于退化诊断。
        final_text = str(value or "").strip(" \r\n")
        if schema and not final_text:
            raise ModelBehaviorError("智能体结构化输出正文为空")
        try:
            normalized = json.loads(final_text)
        except json.JSONDecodeError as original_exc:
            sanitized_text, escaped_count = _escape_json_string_control_characters(
                final_text
            )
            parse_error = original_exc
            recovered = False
            if escaped_count:
                try:
                    normalized = json.loads(sanitized_text)
                    final_text = sanitized_text
                    recovered = True
                except json.JSONDecodeError as sanitized_exc:
                    parse_error = sanitized_exc
            if not recovered and schema:
                repaired = _repair_structured_json(
                    sanitized_text if escaped_count else final_text,
                    schema,
                )
                if repaired is not None:
                    normalized = repaired
                    final_text = json.dumps(
                        repaired,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    recovered = True
            if not recovered and schema:
                raise StructuredOutputJSONError(
                    output_text=final_text,
                    json_error=parse_error,
                ) from parse_error
            elif not recovered:
                normalized = {"text": final_text}
    if not isinstance(normalized, dict):
        normalized = {"value": normalized}
    if schema:
        try:
            validate(instance=normalized, schema=schema)
        except ValidationError as exc:
            raise StructuredOutputValidationError(
                output=normalized,
                output_schema=schema,
                validation_error=exc,
            ) from exc
    return normalized, final_text


def _repair_structured_json(
    value: str,
    schema: dict[str, Any],
) -> dict[str, Any] | None:
    """只接收修复后仍完整满足原始契约的 JSON 对象。"""

    try:
        repaired = repair_json(
            value,
            return_objects=True,
            skip_json_loads=True,
        )
    except (TypeError, ValueError, IndexError):
        return None
    if not isinstance(repaired, dict) or not repaired:
        return None
    try:
        validate(instance=repaired, schema=schema)
    except ValidationError:
        return None
    return repaired


def _escape_json_string_control_characters(value: str) -> tuple[str, int]:
    """只转义 JSON 字符串内部未转义的 C0 控制字符，不修补结构语法。"""

    output: list[str] = []
    in_string = False
    escaped = False
    escaped_count = 0
    for character in value:
        if not in_string:
            output.append(character)
            if character == '"':
                in_string = True
            continue
        if escaped:
            output.append(character)
            escaped = False
            continue
        if character == "\\":
            output.append(character)
            escaped = True
            continue
        if character == '"':
            output.append(character)
            in_string = False
            continue
        if ord(character) < 0x20:
            output.append(f"\\u{ord(character):04x}")
            escaped_count += 1
            continue
        output.append(character)
    return "".join(output), escaped_count


def _runner_input(
    *,
    execution_context: ToolExecutionContext,
    input_payload: dict[str, Any],
    runtime_config: dict[str, Any],
    retry_feedback: str | None = None,
) -> str | list[dict[str, Any]]:
    """构造 SDK 输入；视觉节点只在调用期装载图片，不把 base64 写入运行记录。"""

    payload_text = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
    feedback_text = str(retry_feedback or "").strip()
    if feedback_text:
        payload_text = (
            f"{payload_text}\n\n"
            "【上次输出校验反馈】\n"
            f"{feedback_text}\n"
            "请保持原任务范围，只修正违反约束的输出；不要回显本段反馈。"
        )
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
    fallback_route = str(
        runtime_config.get("transient_fallback_model_route") or ""
    ).strip().lower()
    if fallback_route and fallback_route not in {"main", "turbo", "review"}:
        raise ValueError(
            f"不支持的 Agent transient_fallback_model_route: {fallback_route}"
        )
    if fallback_route == model_route:
        raise ValueError("Agent 备用模型路由不能与主路由相同")
    fallback_after = int(
        runtime_config.get("transient_fallback_after_failures") or 2
    )
    if fallback_after < 1:
        raise ValueError("Agent transient_fallback_after_failures 必须大于 0")
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
        if has_tools and _stop_at_tool_keys(runtime_config) is None:
            raise ValueError("页图多模态 Agent 使用工具时必须声明终止工具")


def _stop_at_tool_keys(runtime_config: dict[str, Any]) -> list[str] | None:
    raw_tool_keys = runtime_config.get("stop_at_tool_keys")
    if raw_tool_keys is None:
        return None
    if not isinstance(raw_tool_keys, list):
        raise ValueError("Agent stop_at_tool_keys 必须是数组")
    tool_keys = [str(value).strip() for value in raw_tool_keys if str(value).strip()]
    if not tool_keys:
        raise ValueError("Agent stop_at_tool_keys 不能为空")
    if len(tool_keys) != len(set(tool_keys)):
        raise ValueError("Agent stop_at_tool_keys 存在重复项")
    return tool_keys


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


def _model_settings(
    runtime_config: dict[str, Any],
    *,
    use_json_object_output: bool = False,
    disable_model_thinking: bool = False,
) -> ModelSettings:
    raw_max_output_tokens = runtime_config.get("max_output_tokens")
    max_output_tokens = None
    if raw_max_output_tokens not in (None, ""):
        max_output_tokens = int(raw_max_output_tokens)
        if max_output_tokens <= 0:
            raise ValueError("Agent max_output_tokens 必须大于 0")
    settings_kwargs: dict[str, Any] = {
        "max_tokens": max_output_tokens,
        # 默认串行执行工具，避免多个真实模型请求在同一轮突发撞向上游网关。
        "parallel_tool_calls": bool(runtime_config.get("parallel_tool_calls", False)),
    }
    stop_at_tool_keys = _stop_at_tool_keys(runtime_config)
    if stop_at_tool_keys:
        # 动态 Agent 每轮仍必须选择工具，但可以先调用专业 Agent；固定 Agent
        # 则直接强制唯一终止工具，避免自由正文绕过结构化契约。
        force_terminal_tool_choice = bool(
            runtime_config.get("force_terminal_tool_choice", True)
        )
        settings_kwargs["tool_choice"] = (
            stop_at_tool_keys[0]
            if force_terminal_tool_choice and len(stop_at_tool_keys) == 1
            else "required"
        )
    for key in ("extra_body", "extra_args"):
        value = runtime_config.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError(f"Agent {key} 必须是对象")
        settings_kwargs[key] = dict(value)
    if use_json_object_output:
        # SDK 不再发送 json_schema，由兼容网关提供基础 JSON 对象约束，平台随后继续严格校验 Schema。
        extra_args = dict(settings_kwargs.get("extra_args") or {})
        extra_args["response_format"] = {"type": "json_object"}
        settings_kwargs["extra_args"] = extra_args
    if disable_model_thinking:
        # 部分推理模型会把输出额度全部消耗在 reasoning，导致结构化正文为空。
        extra_body = dict(settings_kwargs.get("extra_body") or {})
        extra_body["thinking"] = {"type": "disabled"}
        settings_kwargs["extra_body"] = extra_body
    return ModelSettings(**settings_kwargs)


def _sdk_output_type(
    agent_definition: AgentDefinition,
    *,
    has_tools: bool,
    disable_server_output_schema: bool = False,
) -> type[BaseModel] | None:
    """工具型 Agent 由平台在最终响应处统一校验，避免响应格式约束阻断工具调用。"""

    if disable_server_output_schema or has_tools or not agent_definition.output_schema:
        return None
    return _output_model_from_schema(
        dict(agent_definition.output_schema),
        f"{agent_definition.agent_key}_output",
    )


def _should_use_json_object_output(
    *,
    disable_server_output_schema: bool,
    has_tools: bool,
) -> bool:
    """仅无工具 Agent 使用兼容网关的 JSON 对象响应模式。"""

    return bool(disable_server_output_schema and not has_tools)


def _tool_use_behavior(
    runtime_config: dict[str, Any],
    *,
    available_tool_names: set[str],
) -> str | dict[str, list[str]]:
    tool_keys = _stop_at_tool_keys(runtime_config)
    if tool_keys is None:
        return "run_llm_again"
    unknown = sorted(set(tool_keys) - available_tool_names)
    if unknown:
        raise ValueError(f"Agent stop_at_tool_keys 引用了未绑定工具: {', '.join(unknown)}")
    return {"stop_at_tool_names": tool_keys}


def _terminal_tool_result(
    runtime_config: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """终止工具的已校验结果是 Agent 唯一输出，不再解析模型自由正文。"""

    stop_at_tool_keys = _stop_at_tool_keys(runtime_config)
    if stop_at_tool_keys is None:
        return None
    matching_calls = [
        call
        for call in tool_calls
        if str(call.get("tool_key") or "") in stop_at_tool_keys
    ]
    if not matching_calls:
        raise ModelBehaviorError(
            "智能体未调用约定的终止工具: " + ", ".join(stop_at_tool_keys)
        )
    if len(matching_calls) != 1:
        raise ModelBehaviorError("智能体重复调用终止工具，无法确定唯一结构化输出")
    result = matching_calls[0].get("result")
    if not isinstance(result, dict):
        raise ModelBehaviorError("终止工具没有返回 JSON 对象")
    return result


def _postprocess_agent_output(
    *,
    agent_definition: AgentDefinition,
    execution_context: ToolExecutionContext,
    input_payload: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    postprocessor = str(
        dict(agent_definition.runtime_config or {}).get("output_postprocessor") or ""
    ).strip()
    if not postprocessor:
        return output
    handler = tool_registry.resolve(postprocessor)
    try:
        return dict(
            handler(
                execution_context,
                {"input_payload": input_payload, "output": output},
            )
        )
    except Exception as exc:
        raise ModelBehaviorError(
            f"Agent 输出后处理校验失败: postprocessor={postprocessor}; {exc}"
        ) from exc


def _agent_tool_input_payload(options: Any) -> dict[str, Any]:
    raw_params = dict(options.get("params") or {}) if isinstance(options, dict) else {}
    raw_input = raw_params.get("input")
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise ModelBehaviorError("专业 Agent 工具 input 必须是非空 JSON 字符串")
    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        raise ModelBehaviorError(f"专业 Agent 工具 input 不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ModelBehaviorError("专业 Agent 工具 input 解码后必须是 JSON 对象")
    return payload


def _build_sdk_agent(
    *,
    db: Any,
    client: Any,
    agent_definition: AgentDefinition,
    execution_context: ToolExecutionContext,
    request_timeout_seconds: float | None = None,
    disable_server_output_schema: bool = False,
    disable_model_thinking: bool = False,
    model_route_override: str | None = None,
    allow_subagents: bool,
    direct_tool_definitions: list[AgentToolDefinition] | None = None,
    allocated_clients: list[AsyncOpenAI] | None = None,
) -> tuple[Agent[Any], bool]:
    runtime_config = dict(agent_definition.runtime_config or {})
    bound_tools = (
        list(direct_tool_definitions)
        if direct_tool_definitions is not None
        else AgentPlatformRepository(db).list_agent_tools(
            agent_definition.id,
            project_id=execution_context.project_id,
        )
    )
    direct_tools: list[Any] = [
        _function_tool(definition, execution_context)
        for definition in bound_tools
    ]
    # 动态协作能力优先呈现，确定性提交工具最后呈现；最终是否调用专业
    # Agent 仍由主 Agent 按当前真实输入决定。
    tools: list[Any] = []
    subagent_keys = [
        str(value).strip()
        for value in list(runtime_config.get("subagent_keys") or [])
        if str(value).strip()
    ]
    if subagent_keys and not allow_subagents:
        raise ValueError(
            f"专业 Agent 禁止继续嵌套 subagent_keys: {agent_definition.agent_key}"
        )
    if len(subagent_keys) != len(set(subagent_keys)):
        raise ValueError(f"Agent subagent_keys 存在重复项: {agent_definition.agent_key}")
    repo = AgentPlatformRepository(db)
    for subagent_key in subagent_keys:
        if subagent_key == agent_definition.agent_key:
            raise ValueError(f"Agent 不能把自身注册为工具: {subagent_key}")
        subagent_definition = repo.get_agent(
            project_id=execution_context.project_id,
            agent_key=subagent_key,
        )
        if subagent_definition is None:
            raise LookupError(f"找不到专业 Agent 定义: {subagent_key}")
        subagent, _ = _build_sdk_agent(
            db=db,
            client=client,
            agent_definition=subagent_definition,
            execution_context=execution_context,
            allow_subagents=False,
            allocated_clients=allocated_clients,
        )
        subagent_runtime = dict(subagent_definition.runtime_config or {})
        subagent_tool_call_start = [0]

        def build_input(
            options: Any,
            *,
            config: dict[str, Any] = subagent_runtime,
            call_start: list[int] = subagent_tool_call_start,
        ) -> str | list[dict[str, Any]]:
            call_start[0] = len(execution_context.tool_calls)
            return _runner_input(
                execution_context=execution_context,
                input_payload=_agent_tool_input_payload(options),
                runtime_config=config,
            )

        async def extract_output(
            result: Any,
            *,
            definition: AgentDefinition = subagent_definition,
            config: dict[str, Any] = subagent_runtime,
            call_start: list[int] = subagent_tool_call_start,
        ) -> str:
            invocation = result.agent_tool_invocation
            if invocation is None:
                raise ModelBehaviorError("专业 Agent 运行缺少工具调用元数据")
            try:
                invocation_arguments = json.loads(invocation.tool_arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ModelBehaviorError("专业 Agent 工具调用参数不是合法 JSON") from exc
            input_payload = _agent_tool_input_payload(
                {"params": invocation_arguments}
            )
            nested_tool_calls = execution_context.tool_calls[call_start[0]:]
            terminal_tool_result = _terminal_tool_result(config, nested_tool_calls)
            output, _ = _normalize_final_output(
                terminal_tool_result
                if terminal_tool_result is not None
                else result.final_output,
                dict(definition.output_schema or {}),
            )
            output = _postprocess_agent_output(
                agent_definition=definition,
                execution_context=execution_context,
                input_payload=input_payload,
                output=output,
            )
            tool_key = f"agent:{definition.agent_key}"
            execution_context.executed_tools.append(tool_key)
            execution_context.tool_calls.append(
                {
                    "tool_key": tool_key,
                    "arguments": input_payload,
                    "result": output,
                }
            )
            return json.dumps(output, ensure_ascii=False, separators=(",", ":"))

        tools.append(
            subagent.as_tool(
                tool_name=subagent_definition.agent_key,
                tool_description=subagent_definition.description or subagent_definition.name,
                custom_output_extractor=extract_output,
                input_builder=build_input,
                max_turns=_effective_max_turns(
                    subagent_runtime,
                    has_tools=bool(subagent.tools),
                ),
                run_config=RunConfig(
                    tracing_disabled=True,
                    workflow_name=subagent_definition.agent_key,
                    trace_include_sensitive_data=False,
                ),
            )
        )

    tools.extend(direct_tools)
    has_tools = bool(tools)
    _validate_runtime_config(runtime_config, has_tools=has_tools)
    tool_use_behavior = _tool_use_behavior(
        runtime_config,
        available_tool_names={str(getattr(tool, "name", "") or "") for tool in tools},
    )
    instructions = str(agent_definition.instructions or "")
    if disable_server_output_schema and not has_tools and agent_definition.output_schema:
        instructions = (
            f"{instructions}\n\n"
            "仅返回符合既定输出契约的 JSON 对象，不要使用 Markdown 代码块或添加额外说明。"
        )
    return (
        Agent(
            name=agent_definition.name,
            instructions=instructions,
            model=_sdk_model(
                client,
                agent_definition,
                request_timeout_seconds=request_timeout_seconds,
                allocated_clients=allocated_clients,
                model_route_override=model_route_override,
            ),
            tools=tools,
            model_settings=_model_settings(
                runtime_config,
                # 工具型 Agent 由工具参数 Schema 约束结构；同时发送
                # response_format=json_object 会让部分兼容网关忽略工具选择。
                use_json_object_output=_should_use_json_object_output(
                    disable_server_output_schema=disable_server_output_schema,
                    has_tools=has_tools,
                ),
                disable_model_thinking=disable_model_thinking,
            ),
            output_type=_sdk_output_type(
                agent_definition,
                has_tools=has_tools,
                disable_server_output_schema=disable_server_output_schema,
            ),
            tool_use_behavior=tool_use_behavior,
        ),
        has_tools,
    )


async def _run_sdk_agent_async(
    *,
    agent: Agent[Any],
    runner_input: str | list[dict[str, Any]],
    execution_context: ToolExecutionContext,
    max_turns: int,
    run_config: RunConfig,
    request_timeout_seconds: float | None,
) -> Any:
    operation = Runner.run(
        agent,
        runner_input,
        context=execution_context,
        max_turns=max_turns,
        run_config=run_config,
    )
    if request_timeout_seconds is None:
        return await operation
    try:
        return await asyncio.wait_for(
            operation,
            timeout=float(request_timeout_seconds),
        )
    except TimeoutError as exc:
        raise TimeoutError(
            f"Agent 调用超过硬超时 {float(request_timeout_seconds):g} 秒"
        ) from exc


def _run_sdk_agent_sync(
    *,
    agent: Agent[Any],
    runner_input: str | list[dict[str, Any]],
    execution_context: ToolExecutionContext,
    max_turns: int,
    run_config: RunConfig,
    request_timeout_seconds: float | None,
) -> Any:
    return asyncio.run(
        _run_sdk_agent_async(
            agent=agent,
            runner_input=runner_input,
            execution_context=execution_context,
            max_turns=max_turns,
            run_config=run_config,
            request_timeout_seconds=request_timeout_seconds,
        )
    )


async def run_agent_async(
    *,
    db: Any,
    agent_definition: AgentDefinition,
    tool_definitions: list[AgentToolDefinition],
    execution_context: ToolExecutionContext,
    input_payload: dict[str, Any],
    request_timeout_seconds: float | None = None,
    retry_feedback: str | None = None,
    disable_server_output_schema: bool = False,
    disable_model_thinking: bool = False,
    model_route_override: str | None = None,
    skip_output_postprocessor: bool = False,
) -> AgentExecutionResult:
    tool_call_start = len(execution_context.tool_calls)
    client = get_client_for_user(execution_context.user_id, db)
    runtime_config = dict(agent_definition.runtime_config or {})
    allocated_clients: list[AsyncOpenAI] = []
    try:
        agent, has_tools = _build_sdk_agent(
            db=db,
            client=client,
            agent_definition=agent_definition,
            execution_context=execution_context,
            request_timeout_seconds=request_timeout_seconds,
            disable_server_output_schema=disable_server_output_schema,
            disable_model_thinking=disable_model_thinking,
            model_route_override=model_route_override,
            allow_subagents=True,
            direct_tool_definitions=tool_definitions,
            allocated_clients=allocated_clients,
        )
        result = await _run_sdk_agent_async(
            agent=agent,
            runner_input=_runner_input(
                execution_context=execution_context,
                input_payload=input_payload,
                runtime_config=runtime_config,
                retry_feedback=retry_feedback,
            ),
            execution_context=execution_context,
            max_turns=_effective_max_turns(
                runtime_config,
                has_tools=has_tools,
            ),
            run_config=RunConfig(
                tracing_disabled=True,
                workflow_name=agent_definition.agent_key,
                trace_include_sensitive_data=False,
            ),
            request_timeout_seconds=request_timeout_seconds,
        )
        if result.interruptions:
            raise RuntimeError("SDK 内部工具审批尚未映射到平台审批节点")
        tool_calls = list(execution_context.tool_calls[tool_call_start:])
        terminal_tool_result = _terminal_tool_result(runtime_config, tool_calls)
        output, final_text = _normalize_final_output(
            terminal_tool_result
            if terminal_tool_result is not None
            else result.final_output,
            dict(agent_definition.output_schema or {}),
        )
        if not skip_output_postprocessor:
            output = _postprocess_agent_output(
                agent_definition=agent_definition,
                execution_context=execution_context,
                input_payload=input_payload,
                output=output,
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
            tool_calls=tool_calls,
        )
    finally:
        await asyncio.gather(
            *(openai_client.close() for openai_client in allocated_clients),
            return_exceptions=True,
        )


def run_agent(
    *,
    db: Any,
    agent_definition: AgentDefinition,
    tool_definitions: list[AgentToolDefinition],
    execution_context: ToolExecutionContext,
    input_payload: dict[str, Any],
    request_timeout_seconds: float | None = None,
    retry_feedback: str | None = None,
    disable_server_output_schema: bool = False,
    disable_model_thinking: bool = False,
    skip_output_postprocessor: bool = False,
) -> AgentExecutionResult:
    return asyncio.run(
        run_agent_async(
            db=db,
            agent_definition=agent_definition,
            tool_definitions=tool_definitions,
            execution_context=execution_context,
            input_payload=input_payload,
            request_timeout_seconds=request_timeout_seconds,
            retry_feedback=retry_feedback,
            disable_server_output_schema=disable_server_output_schema,
            disable_model_thinking=disable_model_thinking,
            skip_output_postprocessor=skip_output_postprocessor,
        )
    )
