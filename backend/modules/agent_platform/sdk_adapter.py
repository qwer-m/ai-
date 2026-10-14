from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import mimetypes
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
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
    ToolsToFinalOutputResult,
)
from agents.exceptions import ModelBehaviorError, UserError
from agents.strict_schema import ensure_strict_json_schema
from agents.model_settings import ModelRetrySettings
from agents.models.interface import Model
from agents.run_config import CallModelData, ModelInputData, ToolExecutionConfig
from agents.tool_context import ToolContext
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model

from core.ai.ai_client import get_client_for_user
from core.ai.providers.openai_compatible_provider import OpenAICompatibleProvider
from core.db.model_defs import AgentDefinition, AgentToolDefinition, KnowledgeDocument
from modules.knowledge_base_components.document.document_asset_service import (
    document_page_image_details,
    document_page_image_path,
    load_document_manifest,
)
from .registry import ToolExecutionContext, tool_registry
from .contracts import AgentMapConfig, WorkflowNode
from .definition_repository import AgentDefinitionRepository
from .autonomous_tools import AutonomousToolLoop
from .autonomous_budget import AgentSubtreeRequestBudgetExceeded
from .action_journal import interrupted_transport_action, ordered_action_receipts
from .output_repair import OutputRepairError, OutputValidationError, build_output_repair_guidance
from .request_diagnostics import (
    ModelRequestDiagnostics, ObservedRunHooks, safe_exception_evidence, unwrap_sdk_execution_error,
)
from .serialization import DuplicateJSONKeyError, strict_json_loads


@dataclass(frozen=True)
class AgentExecutionResult:
    output: dict[str, Any]
    final_text: str
    last_agent_name: str
    usage: dict[str, int]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    cache_semantic_hash: str | None = None
    validated_output: dict[str, Any] | None = None
    tool_observations: list[dict[str, Any]] = field(default_factory=list)
    budget_usage: dict[str, Any] = field(default_factory=dict)
    model_requests: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentOutputContract:
    """模型只看投影，验收始终使用原始输入与现有业务后处理器。"""

    input_payload: dict[str, Any]
    map_config: Any = None
    candidate_validator: Callable[[dict[str, Any]], None] | None = None
    alternate_output_validators: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None

    def validate(
        self, definition: AgentDefinition, context: ToolExecutionContext,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        # 只有父领域明确授予的结果类型可走另一份完整契约；问题报告不是用例产物。
        result_type = output.get("result_type") if isinstance(output, dict) else None
        alternative = (self.alternate_output_validators or {}).get(result_type)
        if alternative is not None:
            return alternative(deepcopy(output))
        try:
            output, _ = _normalize_final_output(output, dict(definition.output_schema or {}))
            normalized = postprocess_agent_map_output(
                config=self.map_config, definition=definition, execution_context=context,
                item_input=self.input_payload, item_output=output,
            )
            if self.candidate_validator is not None:
                self.candidate_validator(deepcopy(normalized))
            return normalized
        except (OutputRepairError, ModelBehaviorError) as error:
            # 只沿明确的模型输出包装层查找业务差异，普通源输入异常仍然硬失败。
            current: BaseException = error
            seen: set[int] = set()
            while isinstance(current, ModelBehaviorError) and current.__cause__ is not None and id(current) not in seen:
                seen.add(id(current))
                current = current.__cause__
            if isinstance(current, OutputRepairError):
                strategy = tool_registry.resolve_repair_strategy(current.strategy_key)
                guidance = build_output_repair_guidance(
                    strategy=strategy, error=current, input_payload=self.input_payload,
                    candidate_output=output,
                )
                current.details = {**deepcopy(current.details), "repair_guidance": guidance}
            raise


class ToolArgumentsJSONError(ModelBehaviorError):
    """模型生成的工具参数不是合法 JSON。"""

    def __init__(
        self,
        *,
        tool_key: str,
        arguments_json: str,
        json_error: json.JSONDecodeError | DuplicateJSONKeyError,
    ) -> None:
        duplicate = isinstance(json_error, DuplicateJSONKeyError)
        position = None if duplicate else max(0, int(json_error.pos))
        excerpt_start = max(0, (position or 0) - 120)
        excerpt_end = min(len(arguments_json), (position or 0) + 120)
        excerpt = arguments_json[excerpt_start:excerpt_end]
        self.diagnostic = {
            "tool_key": tool_key,
            "arguments_chars": len(arguments_json),
            "arguments_sha256": hashlib.sha256(
                arguments_json.encode("utf-8")
            ).hexdigest(),
            "error_kind": "duplicate_key" if duplicate else "invalid_json_syntax",
            "error_line": None if duplicate else int(json_error.lineno),
            "error_column": None if duplicate else int(json_error.colno),
            "error_position": position,
            **({"duplicate_key": json_error.key} if duplicate else {}),
            "excerpt_start": excerpt_start,
            "arguments_excerpt": excerpt,
            "excerpt_truncated_before": excerpt_start > 0,
            "excerpt_truncated_after": excerpt_end < len(arguments_json),
        }
        excerpt_text = json.dumps(excerpt, ensure_ascii=False)
        location = (str(json_error) if duplicate else
                    f"line={json_error.lineno}; column={json_error.colno}; position={position}")
        super().__init__(
            "工具参数不是合法 JSON: "
            f"tool={tool_key}; {location}; chars={len(arguments_json)}; "
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


def _declared_object_fields(schema: dict[str, Any]) -> dict[str, Any] | None:
    """提交协议和错误反馈共用 Schema 的对象层级，不生成业务值或猜测包装格式。"""
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(properties, dict):
        return None
    return {
        "declared_fields": sorted(properties),
        "required_fields": list(schema.get("required") or []),
        "additional_fields_allowed": schema.get("additionalProperties") is not False,
    }


def _submission_object_protocol(schema: dict[str, Any]) -> str:
    protocol = (
        "submit_result 的参数值直接使用输出 Schema 要求的 JSON 对象。"
        "字段直接放在该对象的顶层；不要额外套一层工具调用包装，也不要将整个对象编码为 JSON 字符串。"
    )
    fields = _declared_object_fields(schema)
    if fields is not None:
        protocol += "当前顶层对象契约：" + json.dumps(fields, ensure_ascii=False, separators=(",", ":")) + "。"
    return protocol


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
        expected_fields = _declared_object_fields(validation_error.schema)
        misplaced_fields = []
        if expected_fields is not None:
            self.diagnostic["object_fields_at_instance_path"] = {
                **expected_fields,
                "received_fields": sorted(validation_error.instance) if isinstance(validation_error.instance, dict) else None,
                "instruction": "声明字段应直接位于 instance_path 指向的对象内；不要额外套调用包装或将整个对象编码为字符串。",
            }
            # 从真实错误对象定位字段所在层级，只反馈准确位置，不自动拆包装或接受坏候选。
            if missing_fields and isinstance(validation_error.instance, dict):
                for name, value in validation_error.instance.items():
                    encoded = isinstance(value, str)
                    if encoded:
                        try:
                            value = strict_json_loads(value)
                        except (json.JSONDecodeError, DuplicateJSONKeyError):
                            continue
                    if isinstance(value, dict) and set(missing_fields) <= value.keys():
                        misplaced_fields.append({"received_path": [*instance_path, str(name)],
                            "expected_path": instance_path, "fields": missing_fields,
                            "json_encoded": encoded})
            if misplaced_fields:
                self.diagnostic["misplaced_fields"] = misplaced_fields
        path_text = ".".join(instance_path) or "<root>"
        missing_text = (
            f"; missing_fields={json.dumps(missing_fields, ensure_ascii=False)}"
            if missing_fields
            else ""
        )
        relocation = ""
        if misplaced_fields:
            locations = [".".join(item["received_path"]) for item in misplaced_fields]
            relocation = (f"；字段实际位于 {locations}，请将 {missing_fields} 直接放在 {path_text} 对象内，"
                          "移除额外层级并按原工具 Schema 重新提交，字段值必须保持真实来源依据")
        super().__init__(
            "工具参数不符合 Schema: "
            f"tool={tool_key}; keyword={validation_error.validator or 'unknown'}; "
            f"path={path_text}; message={validation_error.message}{missing_text}{relocation}"
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

    def __init__(self, *, output_text: str, json_error: json.JSONDecodeError | DuplicateJSONKeyError) -> None:
        duplicate = isinstance(json_error, DuplicateJSONKeyError)
        position = None if duplicate else max(0, int(json_error.pos))
        excerpt_start = max(0, (position or 0) - 240)
        excerpt_end = min(len(output_text), (position or 0) + 240)
        control_character_count = sum(ord(character) < 0x20 for character in output_text)
        longest_repeated_run = _longest_repeated_character_run(output_text)
        is_output_degeneration = not duplicate and (
            control_character_count >= 32 or longest_repeated_run >= 64
        )
        self.diagnostic = {
            "output_chars": len(output_text),
            "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
            "error_kind": "duplicate_key" if duplicate else "invalid_json_syntax",
            "error_line": None if duplicate else int(json_error.lineno),
            "error_column": None if duplicate else int(json_error.colno),
            "error_position": position,
            **({"duplicate_key": json_error.key} if duplicate else {}),
            "output_excerpt": output_text[excerpt_start:excerpt_end],
            "excerpt_start": excerpt_start,
            "excerpt_truncated_before": excerpt_start > 0,
            "excerpt_truncated_after": excerpt_end < len(output_text),
            "control_character_count": control_character_count,
            "longest_repeated_character_run": longest_repeated_run,
            "is_output_degeneration": is_output_degeneration,
        }
        category = "输出发生重复字符退化" if is_output_degeneration else "最终输出不是合法 JSON"
        detail = (str(json_error) if duplicate else
                  f"第 {json_error.lineno} 行第 {json_error.colno} 列; {json_error.msg}")
        super().__init__(
            f"智能体{category}: {detail}; chars={len(output_text)}; "
            f"sha256={self.diagnostic['output_sha256']}"
        )


class OutputPostprocessingError(ModelBehaviorError):
    """保留被业务校验拒绝的完整候选，供运行时诊断和定向修复。"""

    def __init__(
        self, *, output: dict[str, Any], postprocessor: str, cause: Exception,
        message_prefix: str = "Agent 输出后处理校验失败",
    ) -> None:
        self.candidate_output = deepcopy(output)
        output_text = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
        self.diagnostic = {
            "postprocessor": postprocessor,
            "validation_message": str(cause)[:4000],
            "output_chars": len(output_text),
            "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
        }
        super().__init__(
            f"{message_prefix}: postprocessor={postprocessor}; {cause}"
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
            decoded = strict_json_loads(value)
        except json.JSONDecodeError:
            # 保留损坏字段交给既有 Schema 反馈；结构修补可能在校验前吞掉重复键。
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


def _parse_function_arguments(tool_key: str, schema: dict[str, Any], arguments_json: str) -> dict[str, Any]:
    """领域工具和模型提交工具共享同一参数解析边界。"""
    raw_arguments = arguments_json or "{}"
    try:
        arguments = strict_json_loads(raw_arguments)
        arguments = _normalize_json_encoded_schema_values(arguments, schema)
    except (json.JSONDecodeError, DuplicateJSONKeyError) as exc:
        raise ToolArgumentsJSONError(
            tool_key=tool_key, arguments_json=raw_arguments, json_error=exc,
        ) from exc
    try:
        validate(instance=arguments, schema=schema)
    except ValidationError as exc:
        raise ToolArgumentsValidationError(
            tool_key=tool_key, arguments_json=raw_arguments, validation_error=exc,
        ) from exc
    if not isinstance(arguments, dict):
        raise ValueError("工具输入 Schema 必须约束参数为 JSON 对象")
    return arguments


def _strict_schema_preserves_contract(schema: dict[str, Any]) -> bool:
    """仅当 SDK 严格化不改变验收含义时启用，禁止把补丁可选字段改成必填。"""
    def canonical(value, key=None):
        if isinstance(value, dict):
            return {name: canonical(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return sorted(value) if key == "required" else [canonical(item) for item in value]
        return value

    try:
        strict = ensure_strict_json_schema(deepcopy(schema))
    except UserError:
        # 允许动态键的合法对象不能声明 strict，继续使用原完整 Schema 校验。
        return False
    return canonical(strict) == canonical(schema)


def _output_submission_tool(
    definition: AgentDefinition, execution_context: ToolExecutionContext,
) -> FunctionTool:
    """将既有输出契约呈现为提交入口，完整验收仍由自主工具循环负责。"""
    schema = deepcopy(definition.output_schema)
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("会话内提交必须声明对象型输出契约")

    async def invoke(context: Any, arguments_json: str) -> str:
        output = _parse_function_arguments("submit_result", schema, arguments_json)
        execution_context.executed_tools.append("submit_result")
        execution_context.tool_calls.append({
            "tool_key": "submit_result", "tool_call_id": context.tool_call_id,
            "arguments": deepcopy(output), "result": deepcopy(output),
        })
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))

    # 完整对象向模型声明严格工具契约；补丁中的可选字段仍保留省略语义。
    return FunctionTool(
        name="submit_result", description="直接以输出 Schema 的 JSON 对象提交候选并执行完整验收；被拒绝后根据反馈修正并重新提交。",
        params_json_schema=schema, on_invoke_tool=invoke,
        strict_json_schema=_strict_schema_preserves_contract(schema), needs_approval=False,
    )


def _function_tool(
    definition: AgentToolDefinition,
    execution_context: ToolExecutionContext,
    *,
    state_input_filter: _PersistentStateInputFilter | None = None,
) -> FunctionTool:
    handler = tool_registry.resolve(definition.handler_key)

    async def invoke(_tool_context: Any, arguments_json: str) -> str:
        arguments = _parse_function_arguments(
            definition.tool_key, dict(definition.input_schema or {}), arguments_json,
        )
        result = handler(execution_context, arguments)
        if inspect.isawaitable(result):
            result = await result
        try:
            validate(instance=result, schema=dict(definition.output_schema or {}))
        except ValidationError as exc:
            raise ToolOutputValidationError(
                tool_key=definition.tool_key,
                output=result,
                validation_error=exc,
            ) from exc
        if state_input_filter is not None:
            state_input_filter.record_result(
                tool_key=definition.tool_key, tool_call_id=_tool_context.tool_call_id,
                result=result, observation=state_input_filter.observe(), arguments=arguments,
            )
        execution_context.executed_tools.append(definition.tool_key)
        execution_context.tool_calls.append(
            {
                "tool_key": definition.tool_key,
                "tool_call_id": getattr(_tool_context, "tool_call_id", None),
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
        # 已授权工具保持稳定注册；过期行动由当前状态校验返回可修正反馈，
        # 避免 SDK 在领域校验前以 tool not found 直接终止整轮。
        is_enabled=True,
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
            normalized = strict_json_loads(final_text)
        except DuplicateJSONKeyError as duplicate_exc:
            raise StructuredOutputJSONError(output_text=final_text, json_error=duplicate_exc) from duplicate_exc
        except json.JSONDecodeError as original_exc:
            sanitized_text, escaped_count = _escape_json_string_control_characters(
                final_text
            )
            parse_error = original_exc
            recovered = False
            if escaped_count:
                try:
                    normalized = strict_json_loads(sanitized_text)
                    final_text = sanitized_text
                    recovered = True
                except json.JSONDecodeError as sanitized_exc:
                    parse_error = sanitized_exc
                except DuplicateJSONKeyError as duplicate_exc:
                    raise StructuredOutputJSONError(output_text=final_text, json_error=duplicate_exc) from duplicate_exc
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
    page_bytes = image_path.read_bytes()
    if hashlib.sha256(page_bytes).hexdigest() != str(page_asset["image_sha256"]):
        raise ValueError("页面图像内容与资产指纹不一致")
    content = [
        {"type": "input_text", "text": payload_text},
        {
            "type": "input_image",
            "image_url": f"data:{media_type};base64,{base64.b64encode(page_bytes).decode('ascii')}",
            "detail": "high",
        },
    ]
    image_block_ids = [
        str(block["block_id"])
        for block in list(input_payload.get("blocks") or [])
        if isinstance(block, dict) and block.get("type") == "image"
    ]
    # 全景保留图文关系，原 PDF 局部重渲染保留嵌图细字；两者共享可信块锚点。
    for detail in document_page_image_details(document_id, page_number, image_block_ids):
        content.append({
            "type": "input_text",
            "text": "下图是本页真实图片块的高清局部图，来源锚点为 " + json.dumps({
                "document_id": document_id,
                "page_number": page_number,
                "block_id": detail["block_id"],
                "image_region": detail["bbox"],
            }, ensure_ascii=False),
        })
        encoded = base64.b64encode(detail["image_bytes"]).decode("ascii")
        content.append({
            "type": "input_image",
            "image_url": f"data:{detail['media_type']};base64,{encoded}",
            "detail": "high",
        })
    return [{"role": "user", "content": content}]


def _validate_runtime_config(runtime_config: dict[str, Any], *, has_tools: bool) -> None:
    if int(runtime_config.get("max_retries", 0) or 0) != 0:
        raise ValueError("Agent 禁止 SDK 隐式重试，请使用工作流节点的 max_attempts")
    catalog_key = runtime_config.get("state_action_catalog")
    if catalog_key is not None:
        if not isinstance(catalog_key, str) or not catalog_key.strip() or catalog_key != catalog_key.strip():
            raise ValueError("state_action_catalog 必须是当前观察中的非空字段名")
        tool_keys = runtime_config.get("tool_keys")
        if (not runtime_config.get("state_initializer") or not runtime_config.get("state_observer")
                or not isinstance(tool_keys, list) or not tool_keys
                or any(not isinstance(key, str) or not key.strip() for key in tool_keys)
                or len(set(tool_keys)) != len(tool_keys)):
            raise ValueError("行动目录必须绑定到具有初始化器、观察器和明确工具集合的持久 Agent")
        if _uses_output_submission(runtime_config) or _subagent_keys(runtime_config):
            raise ValueError("行动目录只能控制显式绑定的领域工具，不能混入自动提交或直接咨询工具")
    snapshot_tools = runtime_config.get("snapshot_tools")
    if snapshot_tools is not None:
        if (not isinstance(snapshot_tools, dict)
                or any(not isinstance(key, str) or not key.strip() for key in snapshot_tools)
                or any(not isinstance(scope, str) or scope.split(".")[0] not in {"input", "state"}
                       or any(not part for part in scope.split(".")) for scope in snapshot_tools.values())):
            raise ValueError("snapshot_tools 必须将工具名称映射到 input/state 中的真实数据路径")
        if snapshot_tools and (not runtime_config.get("state_initializer") or not runtime_config.get("state_observer")
                               or not set(snapshot_tools).issubset(runtime_config.get("tool_keys") or [])):
            raise ValueError("状态快照工具必须绑定到具有初始化器和观察器的持久 Agent")
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


def _subagent_keys(runtime_config: dict[str, Any]) -> list[str]:
    """统一解析专业 Agent 工具声明，供能力判断和 SDK 工具构建共用。"""

    return [
        str(value).strip()
        for value in list(runtime_config.get("subagent_keys") or [])
        if str(value).strip()
    ]


def _uses_output_submission(runtime_config: dict[str, Any]) -> bool:
    """声明通用终止工具即使用既有严格提交能力，初始节点与托管专业会话一致。"""
    return bool(runtime_config.get("validate_before_submit")
                and "submit_result" in (_stop_at_tool_keys(runtime_config) or []))


def effective_max_turns(
    runtime_config: dict[str, Any],
    *,
    has_tools: bool,
) -> int:
    """按直接工具和专业 Agent 工具的实际声明统一校验运行轮次。"""

    has_tools = has_tools or bool(_subagent_keys(runtime_config)) or _uses_output_submission(runtime_config)
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
        # SDK 与 HTTP 两层均禁用隐式重试，所有模型请求都必须经过共享预算。
        "retry": ModelRetrySettings(max_retries=0),
        # 默认串行执行工具，避免多个真实模型请求在同一轮突发撞向上游网关。
        "parallel_tool_calls": bool(runtime_config.get("parallel_tool_calls", False)),
    }
    stop_at_tool_keys = _stop_at_tool_keys(runtime_config)
    if "tool_choice" in runtime_config:
        tool_choice = runtime_config["tool_choice"]
        if not isinstance(tool_choice, str) or tool_choice not in {"auto", "required", "none"}:
            raise ValueError("Agent tool_choice 必须是 auto、required 或 none")
        if tool_choice == "none" and stop_at_tool_keys:
            raise ValueError("Agent 声明终止提交工具时不能禁用工具调用")
        # 调用协议可显式选择自动工具；是否完成提交仍由完整验收决定。
        settings_kwargs["tool_choice"] = tool_choice
    elif stop_at_tool_keys:
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
                {"input_payload": deepcopy(input_payload), "output": deepcopy(output)},
            )
        )
    except Exception as exc:
        raise OutputPostprocessingError(
            output=output, postprocessor=postprocessor, cause=exc,
        ) from exc


def postprocess_agent_map_output(
    *,
    config: Any,
    definition: Any,
    execution_context: ToolExecutionContext,
    item_input: dict[str, Any],
    item_output: dict[str, Any],
) -> dict[str, Any]:
    """使用完整原始输入校验本次映射结果，不从旧候选恢复字段。"""

    item_output = _postprocess_agent_output(
        agent_definition=definition,
        execution_context=execution_context,
        input_payload=item_input,
        output=item_output,
    )
    handler_key = str(getattr(config, "item_postprocessor", None) or "").strip()
    if not handler_key:
        return dict(item_output)
    handler = tool_registry.resolve(handler_key)
    try:
        normalized = handler(
            execution_context,
            {
                "item_input": deepcopy(item_input),
                "item_output": deepcopy(item_output),
            },
        )
    except Exception as exc:
        # 后处理失败说明本次模型结果不可用，按模型行为错误执行该映射项的独立重试。
        raise OutputPostprocessingError(
            output=item_output, postprocessor=handler_key, cause=exc,
            message_prefix="agent_map 单项结果校验失败",
        ) from exc
    if not isinstance(normalized, dict):
        raise ModelBehaviorError(
            f"agent_map 单项后处理器必须返回对象: postprocessor={handler_key}"
        )
    return normalized


def _agent_consultation_question(options: Any, *, tool_key: str) -> str:
    raw_params = options.get("params") if isinstance(options, dict) else None
    try:
        validate(instance=raw_params, schema={
            "type": "object", "required": ["input"],
            "properties": {"input": {"type": "string", "pattern": r"\S"}},
            "additionalProperties": False,
        })
    except ValidationError as exc:
        raise ToolArgumentsValidationError(
            tool_key=tool_key, arguments_json=json.dumps(raw_params, ensure_ascii=False),
            validation_error=exc,
        ) from exc
    # 问题文本即使包含 JSON，也不能替换平台提供的事实、设计项或预算。
    return raw_params["input"]


def _build_sdk_agent(
    *,
    db: Any,
    client: Any,
    agent_definition: AgentDefinition,
    execution_context: ToolExecutionContext,
    trusted_input_payload: dict[str, Any] | None = None,
    model_input_payload: dict[str, Any] | None = None,
    request_timeout_seconds: float | None = None,
    disable_server_output_schema: bool = False,
    disable_model_thinking: bool = False,
    model_route_override: str | None = None,
    allow_subagents: bool,
    direct_tool_definitions: list[AgentToolDefinition] | None = None,
    allocated_clients: list[AsyncOpenAI] | None = None,
    definition_snapshots: dict[str, tuple[AgentDefinition, list[AgentToolDefinition]]] | None = None,
    autonomous_loop: AutonomousToolLoop | None = None,
    budget_hooks: Any = None,
    request_diagnostics: ModelRequestDiagnostics | None = None,
    budget_observer: Callable[[], dict[str, Any]] | None = None,
    state_input_filter: _PersistentStateInputFilter | None = None,
) -> tuple[Agent[Any], bool]:
    if definition_snapshots is not None:
        # SDK 与指纹使用同一份定义，不能在请求执行后再查询已变化的配置。
        agent_definition = deepcopy(agent_definition)
    runtime_config = dict(agent_definition.runtime_config or {})
    bound_tools = (
        list(direct_tool_definitions)
        if direct_tool_definitions is not None
        else AgentDefinitionRepository(db).list_agent_tools(
            agent_definition.id,
            project_id=execution_context.project_id,
        )
    )
    if definition_snapshots is not None:
        bound_tools = deepcopy(bound_tools)
        definition_snapshots[agent_definition.agent_key] = (agent_definition, bound_tools)
    if runtime_config.get("state_action_catalog") is not None:
        if state_input_filter is None:
            raise ValueError("动态行动目录必须使用当前持久会话的状态观察器")
        if set(runtime_config.get("tool_keys") or []) != {item.tool_key for item in bound_tools}:
            raise ValueError("行动目录声明的工具与当前实际绑定工具不一致")
    direct_tools: list[Any] = [
        _function_tool(definition, execution_context, state_input_filter=state_input_filter)
        for definition in bound_tools
    ]
    include_output_submission = _uses_output_submission(runtime_config)
    if include_output_submission:
        if any(tool.name == "submit_result" for tool in direct_tools):
            raise ValueError("专业 Agent 的提交工具与已绑定工具重名")
        direct_tools.append(_output_submission_tool(agent_definition, execution_context))
    if execution_context.action_journal is not None:
        if autonomous_loop is None or not all(
            tool_registry.is_state_only(definition.handler_key) for definition in bound_tools
        ):
            raise ValueError("持久自主节点只能绑定声明无外部副作用的领域状态工具")
        if _subagent_keys(runtime_config):
            raise ValueError("持久自主节点必须通过领域状态工具构造专业 Agent 输入")
    # 动态协作能力优先呈现，确定性提交工具最后呈现；最终是否调用专业
    # Agent 仍由主 Agent 按当前真实输入决定。
    tools: list[Any] = []
    subagent_keys = _subagent_keys(runtime_config)
    if subagent_keys and not allow_subagents:
        raise ValueError(
            f"专业 Agent 禁止继续嵌套 subagent_keys: {agent_definition.agent_key}"
        )
    if len(subagent_keys) != len(set(subagent_keys)):
        raise ValueError(f"Agent subagent_keys 存在重复项: {agent_definition.agent_key}")
    if subagent_keys and (not isinstance(trusted_input_payload, dict) or not isinstance(model_input_payload, dict)):
        raise ValueError("专业咨询必须提供平台原始验收输入及模型输入投影")
    # 构建时固定同批输入，后续 ORM 刷新或模型咨询参数不能改变专家的验收边界。
    trusted_subagent_input = deepcopy(trusted_input_payload)
    model_subagent_input = deepcopy(model_input_payload)
    repo = AgentDefinitionRepository(db)
    for subagent_key in subagent_keys:
        if subagent_key == agent_definition.agent_key:
            raise ValueError(f"Agent 不能把自身注册为工具: {subagent_key}")
        captured = definition_snapshots.get(subagent_key) if definition_snapshots is not None else None
        subagent_definition = captured[0] if captured is not None else repo.get_agent(
            project_id=execution_context.project_id, agent_key=subagent_key,
        )
        if subagent_definition is None:
            raise LookupError(f"找不到专业 Agent 定义: {subagent_key}")
        # 专家模型构建与验收闭包共享构建时的定义快照，不捕获可被刷新覆盖的 ORM 对象。
        subagent_definition = deepcopy(subagent_definition)
        subagent, _ = _build_sdk_agent(
            db=db,
            client=client,
            agent_definition=subagent_definition,
            execution_context=execution_context,
            trusted_input_payload=trusted_subagent_input,
            model_input_payload=model_subagent_input,
            allow_subagents=False,
            direct_tool_definitions=captured[1] if captured is not None else None,
            allocated_clients=allocated_clients,
            definition_snapshots=definition_snapshots,
            budget_hooks=budget_hooks,
            request_diagnostics=request_diagnostics,
            budget_observer=budget_observer,
        )
        subagent_runtime = dict(subagent_definition.runtime_config or {})
        subagent_tool_call_start = [0]

        def build_input(
            options: Any,
            *,
            config: dict[str, Any] = subagent_runtime,
            call_start: list[int] = subagent_tool_call_start,
            tool_key: str = subagent_definition.agent_key,
        ) -> str | list[dict[str, Any]]:
            call_start[0] = len(execution_context.tool_calls)
            input_payload = deepcopy(model_subagent_input)
            input_payload["consultation_question"] = _agent_consultation_question(options, tool_key=tool_key)
            return _runner_input(
                execution_context=execution_context,
                input_payload=input_payload,
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
                invocation_arguments = strict_json_loads(invocation.tool_arguments or "{}")
            except (json.JSONDecodeError, DuplicateJSONKeyError) as exc:
                raise ToolArgumentsJSONError(tool_key=definition.agent_key,
                    arguments_json=invocation.tool_arguments or "{}", json_error=exc) from exc
            question = _agent_consultation_question(
                {"params": invocation_arguments}, tool_key=definition.agent_key,
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
                input_payload=deepcopy(trusted_subagent_input),
                output=output,
            )
            tool_key = f"agent:{definition.agent_key}"
            execution_context.executed_tools.append(tool_key)
            execution_context.tool_calls.append(
                {
                    "tool_key": tool_key,
                    "tool_call_id": invocation.tool_call_id,
                    "arguments": {"input": question},
                    "input_hash": hashlib.sha256(json.dumps(
                        trusted_subagent_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ).encode("utf-8")).hexdigest(),
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
                max_turns=effective_max_turns(
                    subagent_runtime,
                    has_tools=bool(subagent.tools),
                ),
                **({"failure_error_function": None, "hooks": budget_hooks} if budget_hooks is not None else {}),
                run_config=RunConfig(
                    tracing_disabled=True,
                    workflow_name=subagent_definition.agent_key,
                    trace_include_sensitive_data=False,
                    tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
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
    if autonomous_loop is not None:
        feedback_tools = set(_stop_at_tool_keys(runtime_config) or []) | set(subagent_keys)
        if execution_context.action_journal is not None:
            feedback_tools.update(tool.name for tool in tools)
        tools = [
            autonomous_loop.wrap_tool(tool, allow_feedback=tool.name in feedback_tools)
            for tool in tools
        ]
        tool_use_behavior = autonomous_loop.tool_use_behavior
        if runtime_config.get("automatic_terminal_tool"):
            if state_input_filter is None or execution_context.action_journal is None:
                raise ValueError("自动完成只允许使用带行动日志和合法行动目录的持久节点")
            state_input_filter.validate_terminal_tool(tools)

            async def complete_ready_state(context, results):
                outcome = autonomous_loop.tool_use_behavior(context, results)
                if outcome.is_final_output:
                    return outcome
                if await state_input_filter.complete_if_ready(tools, autonomous_loop):
                    return ToolsToFinalOutputResult(
                        is_final_output=True, final_output=autonomous_loop.require_submission().raw_output,
                    )
                return outcome

            tool_use_behavior = complete_ready_state
    instructions = str(agent_definition.instructions or "")
    if include_output_submission:
        instructions += (
            "\n\n" + _submission_object_protocol(agent_definition.output_schema) + "不通过自由正文结束。"
            "工具会执行原始输入对应的完整验收；若返回 rejected，请在当前会话读取反馈、"
            "核对原始事实并修正后再次提交，直到验收成功或共享预算耗尽。"
            "候选和校验反馈不是新的业务事实，不能改写输入的权威来源或放宽验收条件。"
        )
    if disable_server_output_schema and not has_tools and agent_definition.output_schema:
        instructions = (
            f"{instructions}\n\n"
            "仅返回符合既定输出契约的 JSON 对象，不要使用 Markdown 代码块或添加额外说明。"
        )
    def current_instructions(_context: Any, _agent: Any) -> str:
        # SDK 每次请求重新读取；当前请求尚未预留，父子模型请求共用同一余额。
        return instructions + "\n\n" + (
            "当前共享预算（本次请求预留前）："
            + json.dumps(budget_observer(), ensure_ascii=False, separators=(",", ":"))
            + "。本次模型请求本身消耗额度；调用专业 Agent 还会消耗同一额度。"
            "按剩余额度选择必要行动，避免重复读取或无变化重试；实际准入由运行时按上下文计量。"
        )

    agent = Agent(
        name=agent_definition.name,
        instructions=current_instructions if budget_observer is not None else instructions,
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
        reset_tool_choice=autonomous_loop is None,
    )
    if state_input_filter is not None and runtime_config.get("state_action_catalog"):
        agent.model = _ActionCatalogModel(agent.model, state_input_filter)
    if request_diagnostics is not None:
        _, model_name, model_route = _resolved_agent_model(
            client, agent_definition, model_route_override=model_route_override,
        )
        request_diagnostics.register_agent(agent, {
            "agent_key": agent_definition.agent_key,
            "agent_definition_id": agent_definition.id,
            "model": {"name": model_name, "route": model_route},
            "request_timeout_seconds": request_timeout_seconds or runtime_config.get("request_timeout_seconds"),
        })
    return agent, has_tools


async def _run_sdk_agent_async(
    *,
    agent: Agent[Any],
    runner_input: str | list[dict[str, Any]],
    execution_context: ToolExecutionContext,
    max_turns: int,
    run_config: RunConfig,
    request_timeout_seconds: float | None,
    hooks: Any = None,
) -> Any:
    operation = Runner.run(
        agent,
        runner_input,
        context=execution_context,
        max_turns=max_turns,
        run_config=run_config,
        hooks=hooks,
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


def _current_action_errors(context: ToolExecutionContext) -> list[dict[str, Any]]:
    from .action_journal import project_action_failures

    if context.action_journal is None:
        return []
    return project_action_failures(context.action_journal.load(), state=context.action_state)


class _ActionCatalogModel(Model):
    """只向模型发送当前合法行动；SDK 的工具注册表保持完整以处理历史调用。"""

    def __init__(self, model: Model, state_filter: _PersistentStateInputFilter) -> None:
        self._model = model
        self._state_filter = state_filter

    def _request(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        allowed = self._state_filter.available_tool_names()
        tools = [tool for tool in kwargs["tools"] if tool.name in allowed]
        if {tool.name for tool in tools} != allowed:
            raise ValueError("当前行动目录与实际注册工具不一致")
        return {**kwargs, "tools": tools}

    async def get_response(self, **kwargs):
        return await self._model.get_response(**self._request(kwargs))

    async def stream_response(self, **kwargs):
        async for event in self._model.stream_response(**self._request(kwargs)):
            yield event

    def get_retry_advice(self, request):
        return self._model.get_retry_advice(request)

    async def aclose(self):
        await self._model.aclose()


class _PersistentStateInputFilter:
    """模型读取当前观察和有效范围快照；原始工具记录与持久回执保持完整。"""

    def __init__(
        self, *, context: ToolExecutionContext, runtime_config: dict[str, Any],
        retry_feedback: str | None = None,
    ) -> None:
        self._context = context
        self._runtime_config = runtime_config
        self._observer = tool_registry.resolve(str(runtime_config["state_observer"]))
        self._retry_feedback = retry_feedback
        self._initial_input: str | None = None
        self._observations: dict[str, dict[str, str]] = {}
        self._snapshot_tools = dict(runtime_config.get("snapshot_tools") or {})
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._catalog_key = runtime_config.get("state_action_catalog")
    def _validate_action_catalog(self, observation: dict[str, Any]) -> None:
        if self._catalog_key is None:
            return
        catalog = observation.get(self._catalog_key)
        if (not isinstance(catalog, dict)
                or any(not isinstance(key, str) or not isinstance(scope, dict) for key, scope in catalog.items())
                or not set(catalog).issubset(self._runtime_config.get("tool_keys") or [])):
            raise ValueError("当前行动目录缺失或包含未绑定的工具及无效目标范围")

    def observe(self) -> dict[str, Any]:
        observation = self._observer(self._context, {})
        if not isinstance(observation, dict):
            raise TypeError("持久 Agent 的状态观察必须是对象")
        self._validate_action_catalog(observation)
        return observation

    def available_tool_names(self) -> set[str]:
        return set(self.observe()[self._catalog_key])

    def validate_terminal_tool(self, tools: list[Any]) -> None:
        key = self._runtime_config.get("automatic_terminal_tool")
        tool = next((item for item in tools if item.name == key), None)
        if (not self._catalog_key or key not in (_stop_at_tool_keys(self._runtime_config) or [])
                or tool is None or tool.params_json_schema.get("properties")
                or tool.params_json_schema.get("required")):
            raise ValueError("自动完成必须引用合法行动目录中已绑定且无业务参数的终止工具")

    async def complete_if_ready(self, tools: list[Any], loop: AutonomousToolLoop) -> bool:
        """验收条件已成立时直接调用原终止工具，仍经过行动日志和完整提交校验。"""
        key = self._runtime_config.get("automatic_terminal_tool")
        if not key or key not in self.available_tool_names():
            return False
        from .action_journal import action_state_hash

        tool = next(item for item in tools if item.name == key)
        call_id = "platform_finish_" + action_state_hash([key, self._context.action_state])
        await tool.on_invoke_tool(ToolContext(
            context=self._context, tool_name=key, tool_call_id=call_id, tool_arguments="{}",
        ), "{}")
        return any(item["tool_call_id"] == call_id and item["status"] == "accepted" for item in loop.events)

    def bind_initial_input(self, input_text: str, observation: dict[str, Any]) -> None:
        if not isinstance(input_text, str):
            raise TypeError("持久 Agent 的状态观察必须使用文本输入")
        self._initial_input = input_text
        self._validate_action_catalog(observation)

    def _snapshot_version(self, scope: str) -> str | None:
        """快照有效期由其真实数据来源决定，阅读进度等旁支变化不应让原文失效。"""
        from .action_journal import action_state_hash

        root, *path = scope.split(".")
        value = self._context.action_input if root == "input" else self._context.action_state
        for part in path:
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return action_state_hash(value)

    def record_result(
        self, *, tool_key: str, tool_call_id: str, result: Any, observation: dict[str, Any],
        arguments: dict[str, Any] | None = None,
    ) -> None:
        from .action_journal import action_state_hash

        # 仅按执行当时真实观察的完整相等性登记，不依靠字段形状或工具名称猜测。
        if result == observation:
            self._observations[tool_call_id] = {
                "tool_call_id": tool_call_id, "tool_key": tool_key,
                "observation_sha256": action_state_hash(result),
            }
        elif tool_key in self._snapshot_tools:
            if not isinstance(arguments, dict):
                raise ValueError("状态快照必须保留本次真实读取参数")
            version = self._snapshot_version(self._snapshot_tools[tool_key])
            if version is None:
                raise ValueError("状态快照的真实来源路径不存在")
            self._snapshots[tool_call_id] = {
                "tool_call_id": tool_call_id, "tool_key": tool_key,
                "output_sha256": action_state_hash(result),
                "version_scope": self._snapshot_tools[tool_key],
                "version_sha256": version,
                "arguments": deepcopy(arguments),
            }

    def __call__(self, data: CallModelData[Any]) -> ModelInputData:
        from .action_journal import action_state_hash

        if data.context is not self._context or self._initial_input is None:
            raise ValueError("状态输入过滤器只能用于其绑定的持久父会话")
        observation = self.observe()
        errors = _current_action_errors(self._context)
        if errors:
            observation["recent_action_errors"] = errors
        current_input = _runner_input(
            execution_context=self._context, input_payload=observation,
            runtime_config=self._runtime_config, retry_feedback=self._retry_feedback,
        )
        filtered = deepcopy(data.model_data.input)
        if (not filtered or filtered[0].get("role") != "user"
                or filtered[0].get("content") != self._initial_input):
            raise ValueError("持久父会话缺少可核对的原始状态观察输入")
        filtered[0]["content"] = current_input
        version_hashes = {scope: self._snapshot_version(scope) for scope in self._snapshot_tools.values()}
        # 相同版本和内容仅保留最后一次完整读取，原始审计记录不变。
        latest_snapshots = {}
        for item in filtered[1:]:
            record = self._snapshots.get(item.get("call_id")) if item.get("type") == "function_call_output" else None
            if record is not None:
                identity = (record["tool_key"], record["version_scope"], record["version_sha256"], record["output_sha256"])
                latest_snapshots[identity] = record["tool_call_id"]
        for item in filtered[1:]:
            if item.get("type") != "function_call_output":
                continue
            observation_record = self._observations.get(item.get("call_id"))
            snapshot_record = self._snapshots.get(item.get("call_id"))
            record = observation_record or snapshot_record
            if record is None:
                continue
            raw_output = item.get("output")
            try:
                output = strict_json_loads(raw_output) if isinstance(raw_output, str) else raw_output
            except (json.JSONDecodeError, DuplicateJSONKeyError) as error:
                raise ValueError("已登记状态读取的工具输出不是原始 JSON") from error
            expected_hash = record["observation_sha256"] if observation_record else record["output_sha256"]
            if action_state_hash(output) != expected_hash:
                raise ValueError("已登记状态读取与原始工具输出不一致")
            if observation_record:
                replacement = {**record, "status": "observed",
                    "state_observation": "完整状态已由本轮起始的当前观察替代；本回执仅记录历史行动"}
            elif record["version_sha256"] != version_hashes[record["version_scope"]]:
                # 不用旧索引自动刷新：规划或布局变化后，该索引可能指向另一个范围。
                replacement = {**record, "status": "historical_state_snapshot",
                    "state_snapshot": "读取依据的版本已变化，此前范围读取不再作为当前候选或审查依据；"
                                      "需要详情时先按当前观察确认目标，再重新调用该工具。原文保留在行动回执。"}
            else:
                identity = (record["tool_key"], record["version_scope"], record["version_sha256"], record["output_sha256"])
                latest_call = latest_snapshots[identity]
                if latest_call == record["tool_call_id"]:
                    continue
                replacement = {**record, "status": "duplicate_snapshot", "current_tool_call_id": latest_call,
                    "state_snapshot": "相同版本的完整内容已保留在后续读取回执中，无需再次取证。"}
            item["output"] = json.dumps(replacement, ensure_ascii=False, separators=(",", ":"))
        # 调用配对及顺序保留；未声明的原文取证、范围问题与拒绝反馈不参与快照替换。
        return ModelInputData(input=filtered, instructions=data.model_data.instructions)


def _managed_agent_call_identity(
    *, execution_semantic_hash: str, agent_key: str, contract: AgentOutputContract,
) -> dict[str, str]:
    """完整请求及冻结父契约决定复用身份，验收回调仍在每次命中时执行。"""
    from .action_journal import action_state_hash

    def callback_identity(callback: Any) -> str | None:
        if callback is None:
            return None
        target = getattr(callback, "func", callback)
        return f"{target.__module__}.{target.__qualname__}"

    config = contract.map_config
    return {
        "agent_key": agent_key, "input_hash": action_state_hash(contract.input_payload),
        "execution_semantic_hash": execution_semantic_hash,
        "contract_hash": action_state_hash({
            "map_config": config.model_dump(mode="json") if config is not None else None,
            "candidate_validator": callback_identity(contract.candidate_validator),
            "alternate_output_validators": {key: callback_identity(value)
                for key, value in sorted((contract.alternate_output_validators or {}).items())},
        }),
    }


def _revalidate_managed_agent_call(
    *, saved: dict[str, Any], contract: AgentOutputContract, definition: AgentDefinition,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    from .action_journal import ActionStateConflict

    try:
        output = contract.validate(definition, context, deepcopy(saved["raw_output"]))
    except Exception as error:
        raise ActionStateConflict("专业回执的原始候选未通过当前完整输出契约") from error
    if output != saved["output"]:
        raise ActionStateConflict("专业回执的当前复验产物与已保存标准化结果不一致")
    return output


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
    cache_node: WorkflowNode | None = None,
    output_contract: AgentOutputContract | None = None,
    request_budget: dict[str, int] | None = None,
    shared_budget_hooks: Any = None,
    execution_node: WorkflowNode | None = None,
    model_client: Any = None,
    request_diagnostics: ModelRequestDiagnostics | None = None,
    require_validated_submission: bool = False,
    captured_definitions: dict[str, tuple[AgentDefinition, list[AgentToolDefinition]]] | None = None,
) -> AgentExecutionResult:
    tool_call_start = len(execution_context.tool_calls)
    journal = execution_context.action_journal
    request_diagnostics = request_diagnostics or ModelRequestDiagnostics(
        journal=journal, max_records=(request_budget or {}).get("attempted_requests", 100),
    )
    client = model_client if model_client is not None else get_client_for_user(execution_context.user_id, db)
    if journal is not None:
        # 一个行动阶段固定真实定义与客户端，ORM 提交或配置修改不能改变正在执行的契约。
        agent_definition, tool_definitions = deepcopy((agent_definition, tool_definitions))
    runtime_config = dict(agent_definition.runtime_config or {})
    state_input_filter = (
        _PersistentStateInputFilter(
            context=execution_context, runtime_config=runtime_config, retry_feedback=retry_feedback,
        ) if journal is not None and runtime_config.get("state_initializer") else None
    )
    managed_max_turns = None
    if require_validated_submission:
        if request_budget is None or output_contract is None or shared_budget_hooks is None or journal is not None:
            raise ValueError("专业会话内验收需要原始输出契约及父级共享预算，不能替代持久领域提交")
        managed_max_turns = min(
            effective_max_turns(runtime_config, has_tools=bool(tool_definitions)),
            int(request_budget["attempted_requests"]),
        )
    enable_managed_submission = require_validated_submission and not runtime_config.get("validate_before_submit")
    if enable_managed_submission:
        uses_existing_terminal = bool(_stop_at_tool_keys(runtime_config))
        # 已有提交工具直接接入完整验收；没有提交工具时才派生通用 submit_result。
        # 仅调整本次托管会话，来源提取等原节点的配置和数据库定义不变。
        agent_definition = deepcopy(agent_definition)
        runtime_config.update({
            "validate_before_submit": True,
            "stop_at_tool_keys": _stop_at_tool_keys(runtime_config) or ["submit_result"],
            "max_turns": managed_max_turns, "force_terminal_tool_choice": False,
        })
        agent_definition.runtime_config = runtime_config
        if uses_existing_terminal:
            agent_definition.instructions = str(agent_definition.instructions or "") + (
                "\n\n当前专业会话由平台托管，继续使用已有提交工具。"
                "只有完整验收通过才能结束；收到 rejected 时，按真实来源及反馈修正后再次提交，"
                "候选和问题文本均不是新增事实。父子调用共享同一预算，不能重新申请额度。"
            )
    elif managed_max_turns is not None and runtime_config.get("max_turns") != managed_max_turns:
        agent_definition = deepcopy(agent_definition)
        runtime_config["max_turns"] = managed_max_turns
        agent_definition.runtime_config = runtime_config
    allocated_clients: list[AsyncOpenAI] = []
    definition_snapshots = (
        deepcopy(captured_definitions) if captured_definitions is not None
        else {} if cache_node is not None or journal is not None else None
    )
    cache_semantic_hash = None
    autonomous_loop = None
    budget_hooks = shared_budget_hooks
    if managed_max_turns is not None:
        # 主专业 Agent 与其 as_tool 咨询共用局部次数，不能借父级更大预算突破原会话上限。
        budget_hooks = shared_budget_hooks.scoped_requests(managed_max_turns)
    if runtime_config.get("state_initializer") and journal is None:
        raise ValueError("持久自主 Agent 必须由带行动日志的运行时执行")
    checkpoint = journal.load() if journal is not None else None
    if journal is not None and (checkpoint is None or not runtime_config.get("validate_before_submit")):
        raise ValueError("持久自主节点必须预先初始化行动检查点并启用完整验收")
    if runtime_config.get("validate_before_submit"):
        from .autonomous_budget import AutonomousBudgetHooks

        if output_contract is None or request_budget is None:
            raise ValueError("自主提交必须提供完整输入验收契约和已预留的请求预算")
        if budget_hooks is None:
            budget_hooks = AutonomousBudgetHooks(
                request_budget, checkpoint=checkpoint["budget"] if checkpoint else None,
                on_change=journal.save_budget if journal is not None else None,
            )
        autonomous_loop = AutonomousToolLoop(
            terminal_tool_names=set(_stop_at_tool_keys(runtime_config) or []),
            validate_submission=lambda output: output_contract.validate(
                agent_definition, execution_context, output,
            ),
            recoverable_errors=(
                ToolArgumentsJSONError, ToolArgumentsValidationError,
                StructuredOutputJSONError, StructuredOutputValidationError, OutputValidationError,
            ) + ((AgentSubtreeRequestBudgetExceeded,) if journal is not None else ()),
            max_tool_calls=int(request_budget["attempted_requests"]),
            action_context=execution_context if journal is not None else None,
            budget_snapshot=budget_hooks.snapshot,
        )
    if journal is not None:
        if execution_node is None:
            raise ValueError("持久自主节点必须提供真实工作流节点以验证执行身份")
        managed_keys = list(runtime_config.get("managed_agent_keys") or [])
        managed_concurrency = runtime_config.get("managed_agent_max_concurrency", 1)
        if type(managed_concurrency) is not int or not 1 <= managed_concurrency <= 16:
            raise ValueError("托管专业 Agent 的并发数必须是 1 到 16 的整数")
        execution_context.agent_concurrency = managed_concurrency
        if len(set(managed_keys)) != len(managed_keys) or agent_definition.agent_key in managed_keys:
            raise ValueError("持久自主节点的专业能力不能重复或引用自身")
        definitions = AgentDefinitionRepository(db)
        for key in managed_keys:
            captured = definition_snapshots.get(key)
            child = captured[0] if captured is not None else definitions.get_agent(
                project_id=execution_context.project_id, agent_key=key,
            )
            if child is None or not child.enabled:
                raise LookupError(f"专业 Agent 不可用: {key}")
            if captured is None:
                definition_snapshots[key] = deepcopy((
                    child, definitions.list_agent_tools(child.id, project_id=execution_context.project_id),
                ))
            child_config = dict(child.runtime_config or {})
            if child_config.get("managed_agent_keys"):
                raise ValueError(f"专业 Agent 禁止再建立托管协调层: {key}")
            # 托管生成专家仍可咨询既有叶子专家；整棵授权子树在父阶段开始时固定。
            for nested_key in _subagent_keys(child_config):
                if nested_key in {key, agent_definition.agent_key}:
                    raise ValueError(f"专业咨询不能循环引用: {nested_key}")
                captured_nested = definition_snapshots.get(nested_key)
                nested = captured_nested[0] if captured_nested is not None else definitions.get_agent(
                    project_id=execution_context.project_id, agent_key=nested_key,
                )
                if nested is None or not nested.enabled:
                    raise LookupError(f"专业咨询 Agent 不可用: {nested_key}")
                nested_config = dict(nested.runtime_config or {})
                if _subagent_keys(nested_config) or nested_config.get("managed_agent_keys"):
                    raise ValueError(f"专业咨询 Agent 必须是叶子能力: {nested_key}")
                if captured_nested is None:
                    definition_snapshots[nested_key] = deepcopy((
                        nested, definitions.list_agent_tools(nested.id, project_id=execution_context.project_id),
                    ))
        allowed_agents = frozenset(managed_keys)

        async def invoke_agent(
            *, agent_key: str, input_payload: dict[str, Any], item_postprocessor: str | None = None,
            validate_candidate: Callable[[dict[str, Any]], None] | None = None,
            alternate_output_validators: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
        ) -> dict[str, Any]:
            """领域工具负责构造可信输入；SDK 只执行授权能力并完成原有逐项验收。"""
            if agent_key not in allowed_agents:
                raise ValueError(f"当前自主节点没有被授予专业能力: {agent_key}")
            child, child_tools = definition_snapshots[agent_key]
            child_context = replace(
                execution_context, artifacts=deepcopy(execution_context.artifacts),
                executed_tools=[], tool_calls=[], action_journal=None, action_state={},
                action_input={}, invoke_agent=None, restoring_action=False,
            )
            contract = AgentOutputContract(
                deepcopy(input_payload),
                AgentMapConfig(item_postprocessor=item_postprocessor) if item_postprocessor else None,
                candidate_validator=validate_candidate,
                alternate_output_validators=alternate_output_validators,
            )
            child_config = dict(child.runtime_config or {})
            from .action_journal import action_state_hash

            identity = {"agent_key": agent_key, "input_hash": action_state_hash(input_payload)}
            saved_checkpoint = journal.load()
            receipt_identity = _managed_agent_call_identity(
                execution_semantic_hash=saved_checkpoint.get("execution_semantic_hash"),
                agent_key=agent_key, contract=contract,
            )
            saved_call = journal.validated_agent_call(receipt_identity)
            if saved_call is not None:
                output = _revalidate_managed_agent_call(
                    saved=saved_call, contract=contract, definition=child, context=child_context,
                )
                execution_context.tool_calls.append({
                    "tool_key": f"agent:{agent_key}", "input_hash": identity["input_hash"],
                    "status": "validated", "replayed": True, "model_requests_sent": 0,
                    "source_parent_tool_call_id": saved_call["parent_tool_call_id"],
                    "result_type": saved_call["result_type"],
                })
                return output
            prior_error = next((
                item["error"] for item in reversed(ordered_action_receipts(saved_checkpoint))
                if item["status"] == "failed" and item.get("error", {}).get("agent_input_identity") == identity
                and item["error"].get("recoverable")
            ), None)
            child_feedback = json.dumps(prior_error["feedback"], ensure_ascii=False) if prior_error else None
            call_summary = {
                "tool_key": f"agent:{agent_key}", "input_hash": identity["input_hash"],
                "validation_feedback_used": bool(child_feedback),
            }
            try:
                from .runtime import _project_agent_map_input

                # 复用节点的模型视图；身份和完整提交验收继续使用上方的原始输入。
                child_model_input, projection_diagnostics = _project_agent_map_input(
                    definition=child, raw_item=input_payload,
                )
                call_summary["input_projection"] = projection_diagnostics
                child_result = await run_agent_async(
                    db=db, agent_definition=child, tool_definitions=child_tools,
                    execution_context=child_context, input_payload=child_model_input,
                    request_timeout_seconds=float(child_config.get("request_timeout_seconds") or 180),
                    disable_server_output_schema=bool(child_config.get("disable_server_output_schema")),
                    skip_output_postprocessor=True, output_contract=contract, retry_feedback=child_feedback,
                    request_budget=request_budget, shared_budget_hooks=budget_hooks, model_client=client,
                    request_diagnostics=request_diagnostics,
                    require_validated_submission=True,
                    captured_definitions=definition_snapshots,
                )
                output = (
                    child_result.validated_output if child_result.validated_output is not None
                    else contract.validate(child, child_context, child_result.output)
                )
                journal.save_validated_agent_call(
                    identity=receipt_identity, raw_output=child_result.output, output=output,
                )
            except Exception as error:
                if autonomous_loop.feedback_for_error(error) is not None:
                    error.agent_input_identity = identity
                execution_context.tool_calls.append({
                    **call_summary, "status": "failed",
                    "submissions": [{key: deepcopy(call[key]) for key in ("tool_key", "tool_call_id", "arguments") if key in call}
                                    for call in child_context.tool_calls],
                    "tool_observations": deepcopy(dict(getattr(error, "diagnostic", None) or {}).get("tool_observations", [])),
                })
                raise
            execution_context.tool_calls.append({
                **call_summary, "status": "validated", "usage": child_result.usage,
                "submissions": [{key: deepcopy(call[key]) for key in ("tool_key", "tool_call_id", "arguments") if key in call}
                                for call in child_context.tool_calls],
                "tool_observations": child_result.tool_observations,
            })
            return output

        execution_context.invoke_agent = invoke_agent
    try:
        agent, has_tools = _build_sdk_agent(
            db=db,
            client=client,
            agent_definition=agent_definition,
            execution_context=execution_context,
            trusted_input_payload=output_contract.input_payload if output_contract is not None else None,
            model_input_payload=input_payload,
            request_timeout_seconds=request_timeout_seconds,
            disable_server_output_schema=disable_server_output_schema,
            disable_model_thinking=disable_model_thinking,
            model_route_override=model_route_override,
            allow_subagents=True,
            direct_tool_definitions=tool_definitions,
            allocated_clients=allocated_clients,
            definition_snapshots=definition_snapshots,
            autonomous_loop=autonomous_loop,
            budget_hooks=ObservedRunHooks(request_diagnostics, budget_hooks),
            request_diagnostics=request_diagnostics,
            budget_observer=budget_hooks.observe if budget_hooks is not None else None,
            state_input_filter=state_input_filter,
        )
        request_diagnostics.register_agent(agent, {
            "input_hash": hashlib.sha256(json.dumps(
                input_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest(),
        })
        if cache_node is not None:
            from .semantic_identity import agent_execution_semantic_hash

            cache_semantic_hash = agent_execution_semantic_hash(
                db=db, user_id=execution_context.user_id, project_id=execution_context.project_id,
                node=cache_node, definition=agent_definition, tools=tool_definitions,
                client=client, definition_snapshots=definition_snapshots,
            )
        if journal is not None:
            from .semantic_identity import agent_execution_semantic_hash

            checkpoint = journal.bind_execution_identity(agent_execution_semantic_hash(
                db=db, user_id=execution_context.user_id, project_id=execution_context.project_id,
                node=execution_node, definition=agent_definition, tools=tool_definitions,
                client=client, definition_snapshots=definition_snapshots,
            ))
        restored_submission = False
        if journal is not None:
            tools_by_name = {tool.name: tool for tool in agent.tools}
            receipts = ordered_action_receipts(checkpoint)
            terminal = set(_stop_at_tool_keys(runtime_config) or [])
            # 已经提交的状态直接复验；未完成动作按模型原来的选择继续计算。
            completed_submission = next((
                item for item in reversed(receipts)
                if item["status"] == "completed" and item["tool_key"] in terminal
            ), None)
            pending = [completed_submission] if completed_submission is not None else [
                item for item in receipts if item["status"] == "started"
            ]
            transport_retry = interrupted_transport_action(checkpoint) if not pending else None
            if transport_retry is not None:
                # 网络失败没有改变业务意图。新回执使用原参数，SDK 按完整执行身份复验并
                # 复用已成功的专业结果；原失败回执和已消耗预算仍然保留。
                transport_retry["tool_call_id"] = "transport_retry_" + hashlib.sha256(
                    transport_retry["tool_call_id"].encode("utf-8")
                ).hexdigest()
                pending = [transport_retry]
            execution_context.restoring_action = transport_retry is None
            try:
                for receipt in pending:
                    arguments_json = receipt["arguments"]["arguments_json"]
                    await tools_by_name[receipt["tool_key"]].on_invoke_tool(
                        ToolContext(
                            context=execution_context, tool_name=receipt["tool_key"],
                            tool_call_id=receipt["tool_call_id"], tool_arguments=arguments_json,
                        ), arguments_json,
                    )
                restored_submission = completed_submission is not None or any(
                    item["status"] == "accepted" for item in autonomous_loop.events
                )
            finally:
                execution_context.restoring_action = False
            if not restored_submission and state_input_filter is not None:
                # 中断可能发生在审查已落库、终止动作尚未执行之间；恢复同样无需再询问模型。
                restored_submission = await state_input_filter.complete_if_ready(agent.tools, autonomous_loop)
            observer = tool_registry.resolve(str(runtime_config["state_observer"]))
            input_payload = observer(execution_context, {})
            errors = _current_action_errors(execution_context)
            if errors:
                input_payload["recent_action_errors"] = errors
        runner_input = _runner_input(
            execution_context=execution_context, input_payload=input_payload,
            runtime_config=runtime_config, retry_feedback=retry_feedback,
        )
        if state_input_filter is not None:
            state_input_filter.bind_initial_input(runner_input, input_payload)
        result = None if restored_submission else await _run_sdk_agent_async(
            agent=agent,
            runner_input=runner_input,
            execution_context=execution_context,
            max_turns=effective_max_turns(
                runtime_config,
                has_tools=has_tools,
            ),
            run_config=RunConfig(
                tracing_disabled=True,
                workflow_name=agent_definition.agent_key,
                trace_include_sensitive_data=False,
                tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                call_model_input_filter=state_input_filter,
            ),
            request_timeout_seconds=request_timeout_seconds,
            hooks=ObservedRunHooks(request_diagnostics, budget_hooks),
        )
        if result is not None and result.interruptions:
            raise RuntimeError("SDK 内部工具审批尚未映射到平台审批节点")
        tool_calls = list(execution_context.tool_calls[tool_call_start:])
        accepted = autonomous_loop.require_submission() if autonomous_loop is not None else None
        terminal_tool_result = (
            accepted.raw_output if accepted is not None
            else _terminal_tool_result(runtime_config, tool_calls)
        )
        if accepted is not None:
            # 终止工具已按调用方授权的完整契约验收，范围反馈不能再被普通用例 Schema 拒绝。
            output = deepcopy(accepted.raw_output)
            final_text = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
        else:
            output, final_text = _normalize_final_output(
                terminal_tool_result if terminal_tool_result is not None else result.final_output,
                dict(agent_definition.output_schema or {}),
            )
        if accepted is not None and not skip_output_postprocessor:
            output = accepted.normalized_output
        elif not skip_output_postprocessor:
            output = _postprocess_agent_output(
                agent_definition=agent_definition,
                execution_context=execution_context,
                input_payload=input_payload,
                output=output,
            )
        usage = getattr(result.context_wrapper, "usage", None) if result is not None else None
        usage_payload = {
            "requests": int(getattr(usage, "requests", 0) or 0),
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
        if journal is not None:
            usage_payload = dict(budget_hooks.snapshot()["reported_usage"])
        return AgentExecutionResult(
            output=output,
            final_text=final_text,
            last_agent_name=str(result.last_agent.name if result is not None else agent.name),
            usage=usage_payload,
            tool_calls=tool_calls,
            cache_semantic_hash=cache_semantic_hash,
            validated_output=accepted.normalized_output if accepted is not None else None,
            tool_observations=autonomous_loop.events if autonomous_loop is not None else [],
            budget_usage=budget_hooks.snapshot() if budget_hooks is not None else {},
            model_requests=request_diagnostics.requests,
        )
    except BaseException as exc:
        if budget_hooks is not None:
            # 请求已确定结束但未收到用量时保留全部预留，状态不能继续伪装成正在等待。
            budget_hooks.interrupt_requests(execution_context=execution_context)
        request_diagnostics.record_failure(exc, execution_context=execution_context)
        if not isinstance(exc, Exception):
            raise
        original_error = exc
        exc = unwrap_sdk_execution_error(exc, local_max_turns=managed_max_turns)
        exc.diagnostic = {
            **dict(getattr(exc, "diagnostic", None) or {}),
            **dict(getattr(original_error, "diagnostic", None) or {}),
            "exception_chain": safe_exception_evidence(original_error),
            "failed_model_requests": request_diagnostics.failure_evidence(),
            "model_requests": request_diagnostics.requests,
        }
        if autonomous_loop is not None:
            # 预算耗尽和平台异常也必须保留此前的真实行动，不能只记录成功轮次。
            exc.tool_calls = deepcopy(execution_context.tool_calls[tool_call_start:])
            exc.diagnostic = {
                **dict(getattr(exc, "diagnostic", None) or {}),
                "tool_observations": autonomous_loop.events,
                "budget_usage": budget_hooks.snapshot() if budget_hooks is not None else {},
            }
        if exc is not original_error:
            raise exc from None
        raise
    finally:
        await asyncio.gather(
            *(openai_client.close() for openai_client in allocated_clients),
            return_exceptions=True,
        )
