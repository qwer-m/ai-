from __future__ import annotations

import hashlib
import re
from typing import Any, TYPE_CHECKING

from core.db.model_defs import KnowledgeDocument

if TYPE_CHECKING:
    from .registry import ToolExecutionContext, ToolRegistry


CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "case_id": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "module": {"type": "string", "minLength": 1},
        "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
        "preconditions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "minLength": 1},
                    "expected": {"type": "string", "minLength": 1},
                },
                "required": ["action", "expected"],
                "additionalProperties": False,
            },
        },
        "expected_result": {"type": "string", "minLength": 1},
        "tags": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": [
        "case_id",
        "title",
        "module",
        "priority",
        "preconditions",
        "steps",
        "expected_result",
        "tags",
    ],
    "additionalProperties": False,
}


TEXT_OR_TEXTS_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string", "minLength": 1},
        {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    ]
}


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement_summary": {"type": "string", "minLength": 1},
        "business_modules": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "actors": TEXT_OR_TEXTS_SCHEMA,
                    "lifecycle": {
                        "type": ["string", "null"],
                    },
                },
                "required": ["name", "objective", "actors", "lifecycle"],
                "additionalProperties": False,
            },
        },
        "coverage_focus": TEXT_OR_TEXTS_SCHEMA,
        "risks": TEXT_OR_TEXTS_SCHEMA,
    },
    "required": [
        "requirement_summary",
        "business_modules",
        "coverage_focus",
        "risks",
    ],
    "additionalProperties": False,
}


GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "test_cases": {
            "type": "array",
            "minItems": 1,
            "items": CASE_SCHEMA,
        },
    },
    "required": ["test_cases"],
    "additionalProperties": False,
}


GROUNDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "test_cases": {
            "type": "array",
            "items": CASE_SCHEMA,
        },
    },
    "required": ["test_cases"],
    "additionalProperties": False,
}


EVIDENCE_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["inline", "knowledge_document"]},
        "document_id": {"type": ["integer", "null"], "minimum": 1},
        "filename": {"type": "string"},
        "doc_type": {"type": "string"},
        "content_hash": {"type": "string", "minLength": 64, "maxLength": 64},
    },
    "required": ["kind", "document_id", "filename", "doc_type", "content_hash"],
    "additionalProperties": False,
}


EVIDENCE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement": {"type": "string", "minLength": 1},
        "source": EVIDENCE_SOURCE_SCHEMA,
    },
    "required": ["requirement", "source"],
    "additionalProperties": False,
}


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _identity(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").strip().casefold())


def _content_hash(content: str, stored_hash: Any = None) -> str:
    value = str(stored_hash or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return value
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def resolve_requirement_evidence(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """解析本次 Run 的唯一需求事实源。"""

    inline_requirement = str(arguments.get("requirement") or "").strip()
    requirement_doc_id = arguments.get("requirement_doc_id")

    source: dict[str, Any]
    if requirement_doc_id is not None:
        document = (
            context.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.id == int(requirement_doc_id),
                KnowledgeDocument.project_id == context.project_id,
                KnowledgeDocument.doc_type.in_(
                    ("requirement", "product_requirement", "incomplete")
                ),
            )
            .first()
        )
        if document is None:
            raise ValueError("需求文档不存在、无权访问或类型不允许")
        if str(document.parse_status or "") != "success":
            raise ValueError("需求文档尚未解析成功")
        requirement = _required_text(document.content, "需求文档正文")
        source = {
            "kind": "knowledge_document",
            "document_id": int(document.id),
            "filename": str(document.filename or ""),
            "doc_type": str(document.doc_type or "requirement"),
            "content_hash": _content_hash(requirement, document.content_hash),
        }
    else:
        requirement = _required_text(inline_requirement, "真实需求")
        source = {
            "kind": "inline",
            "document_id": None,
            "filename": "",
            "doc_type": "inline_requirement",
            "content_hash": _content_hash(requirement),
        }

    context.artifacts["requirement_evidence"] = {"source": source}
    return {
        "requirement": requirement,
        "source": source,
    }


def validate_generated_test_cases(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """执行与模型无关的用例契约、数量和重复性校验。"""

    requirement = _required_text(arguments.get("requirement"), "真实需求")
    case_budget = int(arguments.get("case_budget") or 0)
    raw_cases = arguments.get("test_cases")
    if case_budget < 1:
        raise ValueError("用例预算必须大于 0")
    if not isinstance(raw_cases, list):
        raise ValueError("test_cases 必须是数组")
    if not raw_cases:
        raise ValueError("事实对齐后没有可用测试用例")
    if len(raw_cases) > case_budget:
        raise ValueError(
            f"生成数量超过预算: budget={case_budget}, actual={len(raw_cases)}"
        )

    normalized_cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_cases: set[tuple[str, str]] = set()
    priority_counts = {"P0": 0, "P1": 0, "P2": 0}
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"第 {index} 条用例不是对象")
        case_id = _required_text(raw_case.get("case_id"), f"第 {index} 条 case_id")
        title = _required_text(raw_case.get("title"), f"第 {index} 条 title")
        module = _required_text(raw_case.get("module"), f"第 {index} 条 module")
        priority = str(raw_case.get("priority") or "").strip().upper()
        if priority not in priority_counts:
            raise ValueError(f"第 {index} 条 priority 只能是 P0、P1 或 P2")

        case_identity = (_identity(module), _identity(title))
        if case_id in seen_ids:
            raise ValueError(f"用例编号重复: {case_id}")
        if case_identity in seen_cases:
            raise ValueError(f"用例语义重复: {module}/{title}")
        seen_ids.add(case_id)
        seen_cases.add(case_identity)

        raw_steps = raw_case.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError(f"第 {index} 条用例至少需要一个测试步骤")
        steps: list[dict[str, str]] = []
        for step_index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                raise ValueError(f"第 {index} 条用例的第 {step_index} 个步骤不是对象")
            steps.append(
                {
                    "action": _required_text(
                        raw_step.get("action"),
                        f"第 {index} 条用例的第 {step_index} 个操作",
                    ),
                    "expected": _required_text(
                        raw_step.get("expected"),
                        f"第 {index} 条用例的第 {step_index} 个预期",
                    ),
                }
            )

        preconditions = raw_case.get("preconditions")
        tags = raw_case.get("tags")
        if not isinstance(preconditions, list) or not isinstance(tags, list):
            raise ValueError(f"第 {index} 条用例的 preconditions 和 tags 必须是数组")
        normalized_cases.append(
            {
                "case_id": case_id,
                "title": title,
                "module": module,
                "priority": priority,
                "preconditions": [
                    _required_text(item, f"第 {index} 条用例前置条件")
                    for item in preconditions
                ],
                "steps": steps,
                "expected_result": _required_text(
                    raw_case.get("expected_result"),
                    f"第 {index} 条 expected_result",
                ),
                "tags": [
                    _required_text(item, f"第 {index} 条用例标签")
                    for item in tags
                ],
            }
        )
        priority_counts[priority] += 1

    return {
        "status": "passed",
        "validated_count": len(normalized_cases),
        "priority_counts": priority_counts,
        "test_cases": normalized_cases,
    }


def persist_generated_test_cases(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把校验通过的测试用例固化为 Agent Run 产物。"""

    requirement = _required_text(arguments.get("requirement"), "真实需求")
    cases = arguments.get("test_cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("没有可持久化的测试用例")
    artifact = {
        "project_id": context.project_id,
        "run_id": context.run_id,
        "requirement": requirement,
        "evidence": {
            "source": dict(arguments.get("evidence_source") or {}),
        },
        "case_count": len(cases),
        "test_cases": cases,
    }
    context.artifacts["test_generation"] = artifact
    return {
        "status": "persisted",
        "run_id": context.run_id,
        "persisted_count": len(cases),
        "artifact_key": "test_generation",
    }


VALIDATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "const": "passed"},
        "validated_count": {"type": "integer", "minimum": 1},
        "priority_counts": {
            "type": "object",
            "properties": {
                "P0": {"type": "integer", "minimum": 0},
                "P1": {"type": "integer", "minimum": 0},
                "P2": {"type": "integer", "minimum": 0},
            },
            "required": ["P0", "P1", "P2"],
            "additionalProperties": False,
        },
        "test_cases": {
            "type": "array",
            "minItems": 1,
            "items": CASE_SCHEMA,
        },
    },
    "required": ["status", "validated_count", "priority_counts", "test_cases"],
    "additionalProperties": False,
}


BUILTIN_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tool_key": "resolve_requirement_evidence",
        "name": "解析需求证据",
        "description": "从当前需求文档或直接输入解析本次运行的唯一事实源。",
        "handler_key": "testing.resolve_requirement_evidence",
        "input_schema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string"},
                "requirement_doc_id": {"type": ["integer", "null"], "minimum": 1},
            },
            "required": ["requirement", "requirement_doc_id"],
            "additionalProperties": False,
        },
        "output_schema": EVIDENCE_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "validate_test_cases",
        "name": "校验测试用例",
        "description": "确定性校验 Agent 生成用例的数量、字段、步骤、断言和重复项。",
        "handler_key": "testing.validate_test_cases",
        "input_schema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "minLength": 1},
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
                "test_cases": GROUNDING_SCHEMA["properties"]["test_cases"],
            },
            "required": ["requirement", "case_budget", "test_cases"],
            "additionalProperties": False,
        },
        "output_schema": VALIDATION_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "persist_test_cases",
        "name": "持久化测试用例",
        "description": "把确定性校验通过的测试用例固化为 Agent Run 产物。",
        "handler_key": "testing.persist_test_cases",
        "input_schema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "minLength": 1},
                "evidence_source": EVIDENCE_SOURCE_SCHEMA,
                "test_cases": VALIDATION_OUTPUT_SCHEMA["properties"]["test_cases"],
            },
            "required": ["requirement", "evidence_source", "test_cases"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "const": "persisted"},
                "run_id": {"type": "integer", "minimum": 1},
                "persisted_count": {"type": "integer", "minimum": 1},
                "artifact_key": {"type": "string", "const": "test_generation"},
            },
            "required": ["status", "run_id", "persisted_count", "artifact_key"],
            "additionalProperties": False,
        },
        "risk_level": "medium",
        "requires_approval": False,
    },
)


BUILTIN_AGENT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "agent_key": "test_business_planner",
        "name": "测试业务规划智能体",
        "description": "从真实需求中识别业务模块、角色、生命周期、覆盖重点和风险。",
        "instructions": (
            "你是测试业务规划智能体。requirement 是本次唯一需求事实源。"
            "只根据当前需求建立通用业务规划，不得臆造需求外的系统、角色或规则。"
            "按业务目标拆分模块，识别参与角色；仅在需求确实包含状态变化时填写生命周期。"
            "coverage_focus 必须覆盖主流程、异常路径、边界条件、权限或状态约束中与需求相关的部分。"
            "最终 JSON 顶层只能包含 requirement_summary、business_modules、coverage_focus、risks；"
            "business_modules 的每项只能包含 name、objective、actors、lifecycle；"
            "actors、coverage_focus、risks 可使用单个字符串或字符串数组；"
            "lifecycle 有状态流转时填写单个状态链字符串，没有状态流转时必须为 null。"
            "不得输出 business_goal、modules、roles、case_budget、run_id 或 project_id。"
        ),
        "model": "",
        "output_schema": PLAN_SCHEMA,
        "runtime_config": {"model_route": "main", "max_turns": 6, "tool_keys": []},
    },
    {
        "agent_key": "test_case_generator",
        "name": "测试用例生成智能体",
        "description": "依据真实需求和业务规划生成结构化、可执行、可断言的测试用例。",
        "instructions": (
            "你是测试用例生成智能体。requirement 是唯一需求事实源，"
            "plan 是业务规划，case_budget 是最多可生成的用例数量。"
            "根据需求实际覆盖需要生成不超过 case_budget 条互不重复的用例，不得为了凑数制造低价值变体。"
            "需求未直接声明前置条件时，preconditions 必须为空数组。"
            "需求未声明交互界面时，步骤使用实现无关的业务动作，不得臆造页面、按钮或提示文案。"
            "case_id 从 TC-001 连续编号。"
            "每条用例必须可执行、每个步骤都必须有具体预期，expected_result 必须是可观察断言。"
            "优先覆盖规划中的主流程、异常、边界、权限和生命周期风险，但不得添加需求外业务规则。"
            "priority 只能使用 P0、P1、P2。所有文本使用中文，协议名、字段名等专有名词除外。"
            "最终 JSON 顶层只能包含 test_cases，不得输出说明、统计或运行元数据。"
            "test_cases 每项必须且只能包含 case_id、title、module、priority、preconditions、"
            "steps、expected_result、tags 八个字段；preconditions 和 tags 必须是字符串数组。"
            "steps 必须是对象数组，每个对象必须且只能包含 action 和 expected；"
            "禁止使用 step、description、expected、module_name 等别名替代用例字段。"
        ),
        "model": "",
        "output_schema": GENERATION_SCHEMA,
        "runtime_config": {"model_route": "main", "max_turns": 8, "tool_keys": []},
    },
    {
        "agent_key": "test_case_grounding_reviewer",
        "name": "测试用例事实对齐智能体",
        "description": "仅依据当前需求审查草稿用例，移除从样例、常识或业务规划带入的未声明事实。",
        "instructions": (
            "你是测试用例事实对齐智能体。requirement 是唯一允许使用的业务事实源，"
            "draft_test_cases 是待审查草稿，case_budget 是最多保留的用例数量。"
            "你无法访问关联样例，也不得根据常识补充角色、权限、页面、字段、默认值、状态流转、"
            "错误文案或系统行为。逐字段检查草稿；凡是 requirement 没有直接表达或不能直接推出的"
            "前置条件、操作和断言，都必须删除或改写为不引入新事实的表述。"
            "需求未直接声明前置条件时，preconditions 必须为空数组。"
            "需求未声明交互界面时，步骤必须改写为实现无关的业务动作。"
            "不得新增草稿中不存在的业务规则；不应为了达到 case_budget 补造用例。"
            "若所有草稿都无法在不补造事实的情况下修正，test_cases 必须返回空数组。"
            "保留有独立需求依据且可执行的用例，case_id 从 TC-001 连续重排。"
            "priority 只能使用 P0、P1、P2。所有文本使用中文，协议名、字段名等专有名词除外。"
            "最终 JSON 顶层只能包含 test_cases，不得输出审查说明、证据分析或运行元数据。"
            "test_cases 每项必须且只能包含 case_id、title、module、priority、preconditions、"
            "steps、expected_result、tags 八个字段；preconditions 和 tags 必须是字符串数组。"
            "steps 必须是对象数组，每个对象必须且只能包含 action 和 expected。"
        ),
        "model": "",
        "output_schema": GROUNDING_SCHEMA,
        "runtime_config": {"model_route": "main", "max_turns": 8, "tool_keys": []},
    },
)


BUILTIN_WORKFLOW_SPECS: tuple[dict[str, Any], ...] = (
    {
        "workflow_key": "test_generation",
        "name": "Agent 原生测试用例生成",
        "description": "业务规划、用例生成、事实对齐、确定性校验和持久化组成的 Agent 原生链路。",
        "definition": {
            "input_schema": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "requirement_doc_id": {"type": ["integer", "null"], "minimum": 1},
                    "case_budget": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                    },
                },
                "required": [
                    "requirement",
                    "requirement_doc_id",
                    "case_budget",
                ],
                "anyOf": [
                    {"properties": {"requirement": {"type": "string", "minLength": 1}}},
                    {"properties": {"requirement_doc_id": {"type": "integer", "minimum": 1}}},
                ],
                "additionalProperties": False,
            },
            "nodes": [
                {
                    "node_key": "evidence",
                    "node_type": "tool",
                    "reference_key": "resolve_requirement_evidence",
                    "depends_on": [],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "input.requirement",
                        "requirement_doc_id": "input.requirement_doc_id",
                    },
                },
                {
                    "node_key": "plan",
                    "node_type": "agent",
                    "reference_key": "test_business_planner",
                    "depends_on": ["evidence"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                    },
                },
                {
                    "node_key": "generate",
                    "node_type": "agent",
                    "reference_key": "test_case_generator",
                    "depends_on": ["evidence", "plan"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "plan": "dependencies.plan",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "ground",
                    "node_type": "agent",
                    "reference_key": "test_case_grounding_reviewer",
                    "depends_on": ["evidence", "generate"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "draft_test_cases": "dependencies.generate.test_cases",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "validate",
                    "node_type": "tool",
                    "reference_key": "validate_test_cases",
                    "depends_on": ["evidence", "ground"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "case_budget": "input.case_budget",
                        "test_cases": "dependencies.ground.test_cases",
                    },
                },
                {
                    "node_key": "persist",
                    "node_type": "tool",
                    "reference_key": "persist_test_cases",
                    "depends_on": ["evidence", "validate"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "evidence_source": "dependencies.evidence.source",
                        "test_cases": "dependencies.validate.test_cases",
                    },
                },
            ],
            "output_node_key": "persist",
        },
    },
)


def register_test_generation_tools(registry: ToolRegistry) -> None:
    registry.register("testing.resolve_requirement_evidence", resolve_requirement_evidence)
    registry.register("testing.validate_test_cases", validate_generated_test_cases)
    registry.register("testing.persist_test_cases", persist_generated_test_cases)
