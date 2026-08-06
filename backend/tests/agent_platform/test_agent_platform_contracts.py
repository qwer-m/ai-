from __future__ import annotations

from types import SimpleNamespace

import pytest
from agents.exceptions import ModelBehaviorError
from pydantic import ValidationError

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentToolDefinition
from modules.agent_platform.contracts import (
    AgentRunCreate,
    AgentRunExecutionLimits,
    WorkflowGraph,
    WorkflowNode,
)
from modules.agent_platform.registry import (
    BUILTIN_AGENT_SPECS,
    BUILTIN_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS,
    ToolExecutionContext,
)
from modules.agent_platform.runtime import _node_input
from modules.agent_platform.service import _resolved_execution_limits
from modules.agent_platform.sdk_adapter import (
    _effective_max_turns,
    _function_tool,
    _raise_no_tool_invalid_final_output_error,
    _validate_runtime_config,
)
from modules.agent_platform.test_generation_workflow import (
    EVIDENCE_ACCOUNTING_BATCH_INPUT_SCHEMA,
    EVIDENCE_ACCOUNTING_BATCH_OUTPUT_SCHEMA,
    REVIEWED_EVIDENCE_ROUTING_OUTPUT_SCHEMA,
)


def test_builtin_workflow_is_data_driven_and_references_registered_definitions() -> None:
    agent_keys = {str(item["agent_key"]) for item in BUILTIN_AGENT_SPECS}
    tool_keys = {str(item["tool_key"]) for item in BUILTIN_TOOL_SPECS}
    assert len(BUILTIN_AGENT_SPECS) == 10
    assert "test_business_plan_reviewer" not in agent_keys

    for spec in BUILTIN_WORKFLOW_SPECS:
        graph = WorkflowGraph.model_validate(spec["definition"])
        assert graph.execution_order()
        for node in graph.nodes:
            allowed = agent_keys if node.node_type in {"agent", "agent_map"} else tool_keys
            assert node.reference_key in allowed

    graph = WorkflowGraph.model_validate(BUILTIN_WORKFLOW_SPECS[0]["definition"])
    assert [node.node_key for node in graph.execution_order()] == [
        "evidence",
        "prepare_source_semantics",
        "source_semantics",
        "merge_source_semantics",
        "plan",
        "prepare_review_routing",
        "review_routing",
        "merge_review_routing",
        "prepare_continuity_audit",
        "audit_continuity",
        "merge_continuity_audit",
        "merge_plan",
        "prepare_authority_reconciliation",
        "authority_reconciliation",
        "merge_authority_reconciliation",
        "prepare_batches",
        "generate",
        "merge_generated",
        "validate",
        "prepare_chain",
        "chain",
        "validate_chain",
        "persist",
    ]
    merge_generated = next(
        node for node in graph.nodes if node.node_key == "merge_generated"
    )
    assert merge_generated.node_type == "tool"
    assert merge_generated.reference_key == "merge_grounded_generation_batches"
    assert merge_generated.depends_on == ["prepare_batches", "generate"]

    plan_node = next(node for node in graph.nodes if node.node_key == "plan")
    assert plan_node.input_mapping == {
        "effective_facts": "dependencies.merge_source_semantics.effective_facts",
    }
    source_semantics = next(
        node for node in graph.nodes if node.node_key == "source_semantics"
    )
    assert source_semantics.node_type == "agent_map"
    assert source_semantics.depends_on == ["prepare_source_semantics"]
    assert source_semantics.input_mapping == {
        "items": "dependencies.prepare_source_semantics.items"
    }
    review_routing = next(
        node for node in graph.nodes if node.node_key == "review_routing"
    )
    assert review_routing.reference_key == "test_plan_evidence_routing_reviewer"
    assert review_routing.node_type == "agent_map"
    assert review_routing.max_attempts == 2
    assert review_routing.input_mapping == {
        "items": "dependencies.prepare_review_routing.items",
    }
    assert review_routing.map_config is not None
    assert review_routing.map_config.items_key == "items"
    assert review_routing.map_config.output_key == "items"
    assert review_routing.map_config.max_items == 100
    prepare_review_routing = next(
        node for node in graph.nodes if node.node_key == "prepare_review_routing"
    )
    assert prepare_review_routing.input_mapping == {
        "draft_plan": "dependencies.plan",
        "evidence_catalog": "dependencies.evidence.evidence_catalog",
    }
    assert prepare_review_routing.depends_on == ["evidence", "plan"]
    merge_review_routing = next(
        node for node in graph.nodes if node.node_key == "merge_review_routing"
    )
    assert merge_review_routing.input_mapping == {
        "prepared_items": "dependencies.prepare_review_routing.items",
        "routing_records": "dependencies.review_routing.items",
    }
    merge_plan = next(node for node in graph.nodes if node.node_key == "merge_plan")
    assert merge_plan.input_mapping == {
        "draft_plan": "dependencies.plan",
        "evidence_catalog": "dependencies.evidence.evidence_catalog",
        "routing": "dependencies.merge_continuity_audit",
    }
    prepare_continuity = next(
        node for node in graph.nodes if node.node_key == "prepare_continuity_audit"
    )
    assert prepare_continuity.input_mapping == {
        "draft_plan": "dependencies.plan",
        "evidence_catalog": "dependencies.evidence.evidence_catalog",
        "routing": "dependencies.merge_review_routing",
    }
    audit_continuity = next(
        node for node in graph.nodes if node.node_key == "audit_continuity"
    )
    assert audit_continuity.node_type == "agent_map"
    assert audit_continuity.map_config is not None
    assert audit_continuity.map_config.allow_empty is True
    assert audit_continuity.map_config.max_items == 100
    assert audit_continuity.max_attempts == 2
    merge_continuity = next(
        node for node in graph.nodes if node.node_key == "merge_continuity_audit"
    )
    assert merge_continuity.input_mapping["routing"] == (
        "dependencies.merge_review_routing"
    )
    assert merge_continuity.input_mapping["draft_plan"] == "dependencies.plan"
    continuity_agent = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_evidence_continuity_auditor"
    )
    assert continuity_agent["runtime_config"]["max_output_tokens"] == 5000
    assert "governing_module_indexes" not in continuity_agent["output_schema"][
        "properties"
    ]
    assert continuity_agent["output_schema"]["properties"]["governing_scopes"][
        "maxItems"
    ] == 1
    continuity_prepare_tool = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "prepare_continuity_audit_items"
    )
    assert continuity_prepare_tool["output_schema"]["properties"]["items"][
        "maxItems"
    ] == 100
    prepare_batches = next(
        node for node in graph.nodes if node.node_key == "prepare_batches"
    )
    assert prepare_batches.input_mapping["effective_facts"] == (
        "dependencies.merge_authority_reconciliation.effective_facts"
    )
    authority_review = next(
        node for node in graph.nodes if node.node_key == "authority_reconciliation"
    )
    assert authority_review.node_type == "agent_map"
    assert authority_review.map_config is not None
    assert authority_review.map_config.allow_empty is True
    assert prepare_batches.input_mapping["plan"] == "dependencies.merge_plan"
    prepare_chain = next(
        node for node in graph.nodes if node.node_key == "prepare_chain"
    )
    assert prepare_chain.input_mapping["plan"] == "dependencies.merge_plan"


def test_run_execution_limits_can_only_tighten_platform_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.agent_platform import service

    monkeypatch.setattr(service.settings, "AGENT_RUN_MAX_REQUESTS", 80)
    monkeypatch.setattr(service.settings, "AGENT_RUN_MAX_INPUT_TOKENS", 300000)
    monkeypatch.setattr(service.settings, "AGENT_RUN_MAX_OUTPUT_TOKENS", 120000)
    monkeypatch.setattr(service.settings, "AGENT_RUN_MAX_TOTAL_TOKENS", 400000)
    request = AgentRunCreate(
        project_id=2,
        workflow_key="test_generation",
        input_payload={},
        execution_limits=AgentRunExecutionLimits(
            max_requests=20,
            max_input_tokens=500000,
            max_output_tokens=60000,
            max_total_tokens=900000,
        ),
    )
    assert _resolved_execution_limits(request) == {
        "max_requests": 20,
        "max_input_tokens": 300000,
        "max_output_tokens": 60000,
        "max_total_tokens": 400000,
    }


def test_planner_and_routing_reviewer_have_separate_contracts() -> None:
    planner = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_business_planner"
    )
    module_schema = planner["output_schema"]["properties"]["business_modules"]["items"]

    assert "evidence_ids" not in module_schema["properties"]
    assert "证据总账 Reviewer" in planner["instructions"]
    assert planner["runtime_config"]["max_output_tokens"] == 8000
    assert "effective_facts" in planner["instructions"]
    routing_reviewer = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_plan_evidence_routing_reviewer"
    )
    assert routing_reviewer["output_schema"] is EVIDENCE_ACCOUNTING_BATCH_OUTPUT_SCHEMA
    assert routing_reviewer["output_schema"]["required"] == [
        "evidence_accounting"
    ]
    assert "module_evidence" not in routing_reviewer["output_schema"]["properties"]
    accounting_schema = routing_reviewer["output_schema"]["properties"][
        "evidence_accounting"
    ]["items"]
    assert accounting_schema["required"] == [
        "evidence_id",
        "module_indexes",
        "disposition",
        "reason",
    ]
    assert accounting_schema["properties"]["evidence_id"]["pattern"] == (
        "^EV-[0-9]{4,}$"
    )
    assert accounting_schema["properties"]["module_indexes"]["uniqueItems"] is True
    assert accounting_schema["properties"]["disposition"]["enum"] == [
        "assigned",
        "context_only",
        "plan_gap",
    ]
    assert accounting_schema["properties"]["reason"]["maxLength"] == 160
    assert routing_reviewer["runtime_config"]["max_turns"] == 1
    assert routing_reviewer["runtime_config"]["model_route"] == "review"
    assert routing_reviewer["runtime_config"]["max_output_tokens"] == 6000
    assert "不存在上游预选模块" in routing_reviewer["instructions"]
    assert "只输出当前 target_evidence_items" in routing_reviewer["instructions"]
    assert "每个 evidence_id 恰好输出一条 evidence_accounting" in routing_reviewer[
        "instructions"
    ]
    assert "禁止输出 neighbor_context 的 evidence_id" in routing_reviewer["instructions"]
    assert "text_truncated" in routing_reviewer["instructions"]
    assert "不得把规划漏能力标为 context_only" in routing_reviewer["instructions"]
    prepare_accounting = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "prepare_evidence_accounting_batches"
    )
    assert prepare_accounting["input_schema"]["required"] == [
        "draft_plan",
        "evidence_catalog",
    ]
    assert EVIDENCE_ACCOUNTING_BATCH_INPUT_SCHEMA["required"] == [
        "draft_plan",
        "target_evidence_items",
        "neighbor_context",
    ]
    assert prepare_accounting["output_schema"]["properties"]["items"]["items"] is (
        EVIDENCE_ACCOUNTING_BATCH_INPUT_SCHEMA
    )
    assert prepare_accounting["output_schema"]["properties"]["items"]["maxItems"] == 100
    merge_accounting = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "merge_evidence_accounting_batches"
    )
    assert merge_accounting["input_schema"]["required"] == [
        "prepared_items",
        "routing_records",
    ]
    assert merge_accounting["output_schema"] is REVIEWED_EVIDENCE_ROUTING_OUTPUT_SCHEMA
    merge_plan = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "merge_plan_evidence_routing"
    )
    assert merge_plan["input_schema"]["required"] == [
        "draft_plan",
        "evidence_catalog",
        "routing",
    ]
    assert merge_plan["input_schema"]["properties"]["routing"] is (
        REVIEWED_EVIDENCE_ROUTING_OUTPUT_SCHEMA
    )
    prepare_batches = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "prepare_test_case_batches"
    )
    assert prepare_batches["input_schema"]["required"] == [
        "plan",
        "effective_facts",
        "case_budget",
        "batch_case_limit",
    ]


def test_planner_preserves_independent_capabilities_and_reviewer_continuity() -> None:
    planner = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_business_planner"
    )
    routing_reviewer = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_plan_evidence_routing_reviewer"
    )

    planner_instructions = planner["instructions"]
    assert "按 effective_facts 的输入顺序完成能力清单审查" in planner_instructions
    assert "可独立进入、操作或验证" in planner_instructions
    assert "禁止仅写入 requirement_summary、coverage_focus 或 risks 后遗漏" in planner_instructions
    assert "独立入口即使正文只有一个短句、一个列表项或只说明目标页面" in planner_instructions
    assert "只要进入后的页面、用户动作或验证路径独立" in planner_instructions
    assert "可选范围、内容矩阵、配置枚举和数量边界" in planner_instructions
    assert "即使这些目录项没有动作词" in planner_instructions
    assert "模块数量由真实需求中的独立能力决定" in planner_instructions
    assert "模块总数不得超过" not in planner_instructions

    routing_instructions = routing_reviewer["instructions"]
    assert "未完句、续表、编号或跨页承接" in routing_instructions
    assert "短句、标题、表格行、编号列表项" in routing_instructions
    assert "不得假定任何预选结果" in routing_instructions


def test_routing_reviewer_covers_continuous_evidence() -> None:
    routing_reviewer = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_plan_evidence_routing_reviewer"
    )

    routing_instructions = routing_reviewer["instructions"]
    assert "target_evidence_items 是当前分片唯一需要记账" in routing_instructions
    assert "未完句、续表、编号或跨页承接" in routing_instructions
    assert "只读的边界摘录" in routing_instructions
    assert "固定页码或固定证据 ID" in routing_instructions
    assert "不存在上游预选模块" in routing_instructions
    assert "按输入顺序逐项复核" in routing_instructions
    assert "plan_gap" in routing_instructions
    assert "安全排除且移除后不改变任何已分配事实的含义" in routing_instructions
    assert "必须 assigned 到与该事实相同的模块" in routing_instructions


def test_no_tool_agent_uses_single_sdk_turn_and_keeps_node_retry_boundary() -> None:
    assert _effective_max_turns({}, has_tools=False) == 1
    assert _effective_max_turns({"max_turns": 4}, has_tools=True) == 4
    with pytest.raises(ValueError, match="无工具 Agent"):
        _effective_max_turns({"max_turns": 4}, has_tools=False)


def test_sdk_hidden_retry_is_rejected() -> None:
    with pytest.raises(ValueError, match="禁止 SDK 隐式重试"):
        _validate_runtime_config({"max_retries": 1}, has_tools=False)


def test_builtin_no_tool_agents_declare_their_real_single_turn_limit() -> None:
    for spec in BUILTIN_AGENT_SPECS:
        if not spec["runtime_config"].get("tool_keys"):
            assert spec["runtime_config"]["max_turns"] == 1


def test_no_tool_invalid_output_keeps_safe_response_diagnostics() -> None:
    usage = SimpleNamespace(
        output_tokens=8000,
        output_tokens_details=SimpleNamespace(reasoning_tokens=8000),
    )
    handler_input = SimpleNamespace(
        run_data=SimpleNamespace(
            raw_responses=[SimpleNamespace(output=[], usage=usage)],
        )
    )

    with pytest.raises(
        ModelBehaviorError,
        match="response_count=1.*output_tokens=8000.*reasoning_tokens=8000",
    ):
        _raise_no_tool_invalid_final_output_error(handler_input)


def test_workflow_rejects_cycles() -> None:
    with pytest.raises(ValidationError, match="循环依赖"):
        WorkflowGraph.model_validate(
            {
                "nodes": [
                    {
                        "node_key": "a",
                        "node_type": "agent",
                        "reference_key": "agent_a",
                        "depends_on": ["b"],
                    },
                    {
                        "node_key": "b",
                        "node_type": "agent",
                        "reference_key": "agent_b",
                        "depends_on": ["a"],
                    },
                ],
                "output_node_key": "b",
            }
        )


def test_node_input_mapping_reads_run_and_dependency_data() -> None:
    run = AgentRun(
        id=18,
        project_id=66,
        user_id=1,
        workflow_definition_id=1,
        input_payload={"requirement": "真实需求"},
    )
    node = WorkflowNode(
        node_key="review",
        node_type="tool",
        reference_key="review_cases",
        depends_on=["generate"],
        input_mapping={
            "requirement": "input.requirement",
            "cases": "dependencies.generate.cases",
            "project_id": "run.project_id",
        },
    )

    value = _node_input(run, node, {"generate": {"cases": [{"title": "登录"}]}})

    assert value == {
        "requirement": "真实需求",
        "cases": [{"title": "登录"}],
        "project_id": 66,
    }


def test_builtin_tool_can_be_constructed_as_strict_sdk_function_tool() -> None:
    spec = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "validate_test_cases"
    )
    definition = AgentToolDefinition(
        project_id=66,
        user_id=1,
        tool_key=spec["tool_key"],
        name=spec["name"],
        description=spec["description"],
        handler_key=spec["handler_key"],
        input_schema=spec["input_schema"],
        output_schema=spec["output_schema"],
    )
    db = SessionLocal()
    try:
        context = ToolExecutionContext(
            db=db,
            user_id=1,
            project_id=66,
            run_id=1,
            node_key="validate",
            run_input={},
        )

        tool = _function_tool(definition, context)

        assert tool.name == "validate_test_cases"
        assert set(tool.params_json_schema["required"]) == {
            "requirement",
            "case_budget",
            "test_cases",
        }
    finally:
        db.close()
