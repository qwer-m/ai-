from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import ToolExecutionContext, ToolRegistry


SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "score": {"type": "number", "minimum": 0, "maximum": 10},
        "analysis": {"type": "string", "minLength": 1},
    },
    "required": ["key", "name", "score", "analysis"],
    "additionalProperties": False,
}


EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "overall_score": {"type": "number", "minimum": 0, "maximum": 10},
        "execution_status": {
            "type": "string",
            "enum": ["success", "failed", "unknown"],
        },
        "criteria": {
            "type": "array",
            "minItems": 5,
            "maxItems": 5,
            "items": SCORE_SCHEMA,
        },
        "coverage": {
            "type": "object",
            "properties": {
                "rate": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                "covered_items": {"type": "array", "items": {"type": "string"}},
                "missing_items": {"type": "array", "items": {"type": "string"}},
                "explanation": {"type": "string"},
            },
            "required": ["rate", "covered_items", "missing_items", "explanation"],
            "additionalProperties": False,
        },
        "risks": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "recommendations": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": [
        "summary",
        "overall_score",
        "execution_status",
        "criteria",
        "coverage",
        "risks",
        "recommendations",
    ],
    "additionalProperties": False,
}


def persist_automation_evaluation(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """将自动化评测结果固化为当前 Agent Run 的结构化产物。"""

    evaluation_type = str(arguments["evaluation_type"])
    result = dict(arguments["evaluation"])
    artifact = {
        "run_id": context.run_id,
        "project_id": context.project_id,
        "evaluation_type": evaluation_type,
        "source_execution_id": context.run_input.get("source_execution_id"),
        "result": result,
    }
    context.artifacts["automation_evaluation"] = artifact
    return {
        "status": "persisted",
        "run_id": context.run_id,
        "artifact_key": "automation_evaluation",
        "evaluation_type": evaluation_type,
        "overall_score": result["overall_score"],
    }


PERSIST_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "const": "persisted"},
        "run_id": {"type": "integer", "minimum": 1},
        "artifact_key": {"type": "string", "const": "automation_evaluation"},
        "evaluation_type": {"type": "string", "enum": ["ui", "api"]},
        "overall_score": {"type": "number", "minimum": 0, "maximum": 10},
    },
    "required": [
        "status",
        "run_id",
        "artifact_key",
        "evaluation_type",
        "overall_score",
    ],
    "additionalProperties": False,
}


BUILTIN_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tool_key": "persist_automation_evaluation",
        "name": "持久化自动化评测",
        "description": "把结构化的 UI 或 API 自动化评测写入当前 Agent Run 产物。",
        "handler_key": "testing.persist_automation_evaluation",
        "input_schema": {
            "type": "object",
            "properties": {
                "evaluation_type": {"type": "string", "enum": ["ui", "api"]},
                "evaluation": EVALUATION_SCHEMA,
            },
            "required": ["evaluation_type", "evaluation"],
            "additionalProperties": False,
        },
        "output_schema": PERSIST_OUTPUT_SCHEMA,
        "risk_level": "medium",
        "requires_approval": False,
    },
)


BUILTIN_AGENT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "agent_key": "ui_automation_evaluator",
        "name": "UI 自动化评测智能体",
        "description": "基于真实脚本、执行结果、用户旅程和项目上下文输出结构化评测。",
        "instructions": (
            "你是 UI 自动化测试评测智能体。只依据输入中的真实脚本、真实执行结果、"
            "user_journey 和 project_context 评测，不得假设脚本未展示的行为。"
            "最终 JSON 顶层必须且只能包含 summary、overall_score、execution_status、"
            "criteria、coverage、risks、recommendations 七个字段。criteria 必须是恰好五个对象的数组，"
            "每个对象必须且只能包含 key、name、score、analysis；五项依次为脚本结构、错误处理、"
            "测试覆盖、执行成功、结果报告，key 分别使用 structure、error_handling、coverage、"
            "execution、reporting，score 使用 0 到 10 分。禁止把 structure 等评测项展开为顶层字段。"
            "若 user_journey 有内容，逐项核对脚本操作并计算 coverage.rate；没有用户旅程时 rate 必须为 null。"
            "coverage 必须且只能包含 rate、covered_items、missing_items、explanation。"
            "execution_status 只能是 success、failed、unknown，并且只能依据执行结果判定，证据不足时使用 unknown。"
            "overall_score 使用五项 score 的平均值。risks 和 recommendations 必须是字符串数组。"
            "所有分析、风险和建议使用中文，协议名、API 和代码标识除外。"
        ),
        "model": "",
        "output_schema": EVALUATION_SCHEMA,
        "runtime_config": {"model_route": "main", "max_turns": 1, "tool_keys": []},
    },
    {
        "agent_key": "api_automation_evaluator",
        "name": "API 自动化评测智能体",
        "description": "基于真实脚本、执行结果、OpenAPI 和项目上下文输出结构化评测。",
        "instructions": (
            "你是 API 自动化测试评测智能体。只依据输入中的真实脚本、真实执行结果、"
            "openapi_spec 和 project_context 评测，不得臆造接口或执行证据。"
            "最终 JSON 顶层必须且只能包含 summary、overall_score、execution_status、"
            "criteria、coverage、risks、recommendations 七个字段。criteria 必须是恰好五个对象的数组，"
            "每个对象必须且只能包含 key、name、score、analysis；五项依次为脚本结构、断言、错误处理、"
            "测试覆盖、执行成功，key 分别使用 structure、assertions、error_handling、coverage、execution，"
            "score 使用 0 到 10 分。禁止把 structure 等评测项展开为顶层字段。"
            "若 openapi_spec 有内容，按 method 与 path 核对端点覆盖并计算 coverage.rate；"
            "没有接口规范时 rate 必须为 null。coverage 必须且只能包含 rate、covered_items、"
            "missing_items、explanation。execution_status 只能是 success、failed、unknown，并且只能依据"
            "执行结果判定，证据不足时使用 unknown。overall_score 使用五项 score 的平均值。"
            "risks 和 recommendations 必须是字符串数组。所有分析、风险和建议使用中文，协议名、API 和代码标识除外。"
        ),
        "model": "",
        "output_schema": EVALUATION_SCHEMA,
        "runtime_config": {"model_route": "main", "max_turns": 1, "tool_keys": []},
    },
)


def _workflow_input_schema(
    evaluation_type: str,
    extra_property: tuple[str, dict[str, Any]],
) -> dict[str, Any]:
    property_name, property_schema = extra_property
    return {
        "type": "object",
        "properties": {
            "script": {"type": "string", "minLength": 1},
            "execution_result": {"type": "string", "minLength": 1},
            "evaluation_type": {"const": evaluation_type},
            property_name: property_schema,
            "project_context": {"type": "string"},
            "source_execution_id": {"type": ["integer", "null"], "minimum": 1},
        },
        "required": [
            "script",
            "execution_result",
            "evaluation_type",
            property_name,
            "project_context",
        ],
        "additionalProperties": False,
    }


def _workflow_spec(
    *,
    workflow_key: str,
    name: str,
    description: str,
    agent_key: str,
    evaluation_type: str,
    extra_property: tuple[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "workflow_key": workflow_key,
        "name": name,
        "description": description,
        "definition": {
            "execution_mode": "dag",
            "input_schema": _workflow_input_schema(evaluation_type, extra_property),
            "nodes": [
                {
                    "node_key": "evaluate",
                    "node_type": "agent",
                    "reference_key": agent_key,
                    "depends_on": [],
                    "max_attempts": 1,
                },
                {
                    "node_key": "persist",
                    "node_type": "tool",
                    "reference_key": "persist_automation_evaluation",
                    "depends_on": ["evaluate"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "evaluation_type": "input.evaluation_type",
                        "evaluation": "dependencies.evaluate",
                    },
                },
            ],
            "output_node_key": "persist",
        },
    }


def _build_workflow_specs() -> tuple[dict[str, Any], ...]:
    ui = _workflow_spec(
        workflow_key="ui_automation_evaluation",
        name="UI 自动化 Agent 评测",
        description="由评测智能体分析真实 UI 脚本和执行结果，并固化结构化 Run 产物。",
        agent_key="ui_automation_evaluator",
        evaluation_type="ui",
        extra_property=("user_journey", {"type": ["object", "null"]}),
    )
    api = _workflow_spec(
        workflow_key="api_automation_evaluation",
        name="API 自动化 Agent 评测",
        description="由评测智能体分析真实 API 脚本和执行结果，并固化结构化 Run 产物。",
        agent_key="api_automation_evaluator",
        evaluation_type="api",
        extra_property=("openapi_spec", {"type": "string"}),
    )
    return ui, api


BUILTIN_WORKFLOW_SPECS = _build_workflow_specs()


def register_automation_evaluation_tools(registry: ToolRegistry) -> None:
    registry.register(
        "testing.persist_automation_evaluation",
        persist_automation_evaluation,
    )
