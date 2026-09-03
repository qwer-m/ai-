from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from agents.exceptions import ModelBehaviorError
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from jsonschema import validate as validate_json_schema
from pydantic import ValidationError

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentToolDefinition
from modules.agent_platform.contracts import (
    AgentProgramDefinition,
    AgentRunCreate,
    AgentRunExecutionLimits,
    WorkflowGraph,
    WorkflowNode,
    parse_execution_definition,
)
from modules.agent_platform.registry import (
    BUILTIN_AGENT_SPECS,
    BUILTIN_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS,
    ToolExecutionContext,
    tool_registry,
)
from modules.agent_platform.document_agent_tools import _public_layout_blocks
from modules.agent_platform import sdk_adapter
from modules.agent_platform.runtime import _node_input
from modules.agent_platform.repository import AgentPlatformRepository
from modules.agent_platform.serialization import serialize_node_run, serialize_run_summary
from modules.agent_platform.service import _initial_run_context, _resolved_execution_limits
from modules.agent_platform.service import _restorable_node_runs, _restored_checkpoint_sdk_state
from modules.agent_platform.sdk_adapter import (
    StructuredOutputJSONError,
    StructuredOutputValidationError,
    ToolArgumentsJSONError,
    ToolArgumentsValidationError,
    ToolOutputValidationError,
    _effective_max_turns,
    _function_tool,
    _model_settings,
    _normalize_json_encoded_schema_values,
    _normalize_final_output,
    _runner_input,
    _sdk_output_type,
    _should_use_json_object_output,
    _terminal_tool_result,
    _tool_use_behavior,
    _validate_runtime_config,
)
from modules.agent_platform.test_generation_workflow import (
    AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA,
    AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA,
    BUSINESS_PLANNING_BATCH_MAX_FACTS,
    BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS,
    BUSINESS_PLAN_DRAFT_SCHEMA,
    FINAL_REVIEW_REPAIR_INPUT_SCHEMA,
    MODEL_GROUNDING_SCHEMA,
    MODEL_REPAIR_PATCH_SCHEMA,
    PLANNER_AGENT_OUTPUT_SCHEMA,
    PLANNER_AGENT_SUBMISSION_SCHEMA,
    PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA,
    PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA,
    PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA,
    PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA,
    PLANNING_SCOPE_ROUTING_OUTPUT_SCHEMA,
    SCENARIO_DESIGN_GUIDANCE_SCHEMA,
    SOURCE_SEMANTICS_INPUT_SCHEMA,
    SOURCE_SEMANTICS_OUTPUT_SCHEMA,
)
from modules.agent_platform.test_generation_workflow import MERGED_GENERATION_SCHEMA
from modules.agent_platform.test_generation_workflow import merge_planning_scope_routes
from modules.agent_platform.test_generation_workflow import postprocess_planning_route_repair_item
from modules.agent_platform.test_generation_workflow import postprocess_planning_scope_routing_item
from modules.agent_platform.test_generation_workflow import prepare_business_plan_batches
from modules.agent_platform.test_generation_workflow import prepare_business_plan_consolidation
from modules.agent_platform.test_generation_workflow import prepare_planning_route_repairs
from modules.agent_platform.test_generation_workflow import prepare_planning_scope_routes
from modules.agent_platform.test_generation_workflow import validate_business_plan_draft_output
from modules.agent_platform.test_generation_workflow import validate_business_plan_output
from modules.agent_platform.test_generation_workflow import validate_scenario_design_guidance
from modules.agent_platform.test_generation_workflow import _summary_item_text
from modules.agent_platform.test_generation_workflow import _business_planning_limits


def test_execution_definition_distinguishes_legacy_dag_and_agent_network() -> None:
    legacy_dag = parse_execution_definition(
        {
            "nodes": [
                {
                    "node_key": "generate",
                    "node_type": "agent",
                    "reference_key": "generator",
                }
            ],
            "output_node_key": "generate",
        }
    )
    agent_network = parse_execution_definition(
        {
            "execution_mode": "agent_network",
            "entry_agent_key": "coordinator",
            "required_artifact_key": "test_generation",
        }
    )

    assert isinstance(legacy_dag, WorkflowGraph)
    assert legacy_dag.execution_mode == "dag"
    assert isinstance(agent_network, AgentProgramDefinition)
    assert agent_network.required_artifact_key == "test_generation"

    with pytest.raises(ValueError, match="未知的工作流执行模式"):
        parse_execution_definition(
            {
                "execution_mode": "unknown",
                "nodes": legacy_dag.model_dump()["nodes"],
                "output_node_key": "generate",
            }
        )


def test_dag_can_wrap_dynamic_agent_between_fixed_nodes() -> None:
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {
                    "node_key": "evidence",
                    "node_type": "tool",
                    "reference_key": "load_evidence",
                },
                {
                    "node_key": "generate",
                    "node_type": "agent_network",
                    "reference_key": "generation_coordinator",
                    "depends_on": ["evidence"],
                },
                {
                    "node_key": "validate",
                    "node_type": "tool",
                    "reference_key": "validate_output",
                    "depends_on": ["generate"],
                },
            ],
            "output_node_key": "validate",
        }
    )

    assert graph.execution_mode == "dag"
    assert [node.node_key for node in graph.execution_order()] == [
        "evidence",
        "generate",
        "validate",
    ]
    assert graph.nodes[1].node_type == "agent_network"


def test_active_run_query_prefers_running_then_oldest_pending() -> None:
    class Query:
        def __init__(self, rows: list[SimpleNamespace]) -> None:
            self.rows = rows

        def filter(self, *args: object) -> "Query":
            return self

        def options(self, *args: object) -> "Query":
            return self

        def all(self) -> list[SimpleNamespace]:
            return self.rows

    pending_old = SimpleNamespace(id=3, status="pending")
    pending_new = SimpleNamespace(id=5, status="pending")
    running = SimpleNamespace(id=4, status="running")
    db = SimpleNamespace(query=lambda _model: Query([pending_new, running, pending_old]))
    repository = AgentPlatformRepository(db)

    assert repository.get_active_run(project_id=2, user_id=1) is running

    db.query = lambda _model: Query([pending_new, pending_old])
    assert repository.get_active_run(project_id=2, user_id=1) is pending_old


def test_run_history_pruning_does_not_load_large_run_payloads() -> None:
    queried_entities: list[tuple[object, ...]] = []

    class Query:
        def filter(self, *args: object) -> "Query":
            return self

        def order_by(self, *args: object) -> "Query":
            return self

        def all(self) -> list[tuple[int, datetime]]:
            return [(3, datetime(2026, 8, 27, 10, 32, 51))]

    class Database:
        def query(self, *entities: object) -> Query:
            queried_entities.append(entities)
            return Query()

    repository = AgentPlatformRepository(Database())

    deleted_ids = repository.prune_terminal_run_history(
        project_id=2,
        user_id=1,
        workflow_definition_id=16,
        keep_run_id=3,
        limit=1,
    )

    assert deleted_ids == []
    assert queried_entities == [(AgentRun.id, AgentRun.finished_at)]


def test_run_summary_does_not_serialize_large_runtime_fields() -> None:
    run = SimpleNamespace(
        id=31,
        project_id=2,
        workflow_definition_id=1,
        status="success",
        current_node_key="done",
        input_payload={"requirement_doc_id": 268, "case_budget": 80},
        run_context={"run_attempt": 2, "large": "x" * 1000},
        output_payload={"large": "x" * 1000},
        error_message="",
        parent_run_id=None,
        task_id="task-31",
        created_at=None,
        started_at=None,
        finished_at=None,
    )

    summary = serialize_run_summary(run)

    assert summary["run_attempt"] == 2
    assert summary["input_payload"]["requirement_doc_id"] == 268
    assert summary["run_context"] == {}
    assert summary["output_payload"] == {}
    assert summary["nodes"] == []
    assert summary["approvals"] == []


def test_run_summary_serializes_execution_timestamps_as_utc() -> None:
    run = SimpleNamespace(
        id=32,
        project_id=2,
        workflow_definition_id=1,
        status="running",
        current_node_key="source_vision",
        input_payload={},
        run_context={},
        output_payload={},
        error_message="",
        parent_run_id=None,
        task_id="task-32",
        created_at=datetime(2026, 8, 26, 15, 22, 6),
        started_at=datetime(2026, 8, 26, 7, 22, 7),
        finished_at=None,
    )

    summary = serialize_run_summary(run)

    assert summary["created_at"] == datetime(2026, 8, 26, 15, 22, 6)
    assert summary["started_at"] == "2026-08-26T07:22:07Z"
    assert summary["finished_at"] is None


def _route_test_points(name: str) -> list[dict]:
    return [
        {
            "name": name,
            "objective": f"验证{name}",
            "test_designs": [
                {
                    "technique": "场景法",
                    "rationale": "覆盖事实支持的业务流程",
                    "coverage_items": [name],
                }
            ],
        }
    ]


def _module_route(
    module_index: int,
    *,
    relation: str = "primary",
    design_indexes: list[int] | None = None,
) -> dict:
    return {
        "module_index": module_index,
        "relation": relation,
        "test_design_item_indexes": [0] if design_indexes is None else design_indexes,
    }


def test_final_review_repair_schema_accepts_compact_document_anchor() -> None:
    fact_schema = FINAL_REVIEW_REPAIR_INPUT_SCHEMA["properties"][
        "authoritative_facts"
    ]["items"]
    validate_json_schema(
        instance={
            "fact_id": "DOC266-P0001-F0001",
            "assertion": "课程名称为乐乐作文",
            "scope_id": "EV-0001",
            "source_anchor": {
                "source_kind": "document",
                "document_id": 266,
                "page_number": 1,
            },
            "status": "effective",
            "value_policy": "exact",
            "governed_values": [],
            "governed_by": [],
        },
        schema=fact_schema,
    )


def test_sdk_agent_hard_timeout_cancels_blocked_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"cancelled": False}

    async def blocked_run(*args: object, **kwargs: object) -> object:
        try:
            await asyncio.Event().wait()
        finally:
            state["cancelled"] = True

    monkeypatch.setattr(sdk_adapter, "Runner", SimpleNamespace(run=blocked_run))

    with pytest.raises(TimeoutError, match="Agent 调用超过硬超时"):
        sdk_adapter._run_sdk_agent_sync(
            agent=SimpleNamespace(),
            runner_input="{}",
            execution_context=SimpleNamespace(),
            max_turns=1,
            run_config=SimpleNamespace(),
            request_timeout_seconds=0.01,
        )

    assert state["cancelled"] is True


def test_run_agent_closes_allocated_http_client_when_agent_build_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"closed": False}

    class FakeOpenAIClient:
        async def close(self) -> None:
            state["closed"] = True

    def build_agent(**arguments: object) -> tuple[object, bool]:
        allocated_clients = arguments["allocated_clients"]
        assert isinstance(allocated_clients, list)
        allocated_clients.append(FakeOpenAIClient())
        raise ValueError("构建失败")

    monkeypatch.setattr(sdk_adapter, "get_client_for_user", lambda *_: object())
    monkeypatch.setattr(sdk_adapter, "_build_sdk_agent", build_agent)

    with pytest.raises(ValueError, match="构建失败"):
        asyncio.run(
            sdk_adapter.run_agent_async(
                db=object(),
                agent_definition=SimpleNamespace(
                    agent_key="test_agent",
                    runtime_config={},
                ),
                tool_definitions=[],
                execution_context=SimpleNamespace(user_id=1, tool_calls=[]),
                input_payload={},
            )
        )

    assert state["closed"] is True


def test_run_agent_async_can_defer_output_postprocessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行时投影后，允许上层用完整原始输入统一执行后处理。"""

    definition = SimpleNamespace(
        agent_key="projected_agent",
        name="投影智能体",
        instructions="",
        model="",
        output_schema={},
        runtime_config={"output_postprocessor": "testing.postprocess"},
    )
    context = SimpleNamespace(
        user_id=1,
        tool_calls=[],
        executed_tools=[],
    )
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(sdk_adapter, "get_client_for_user", lambda *_: object())
    monkeypatch.setattr(
        sdk_adapter,
        "_build_sdk_agent",
        lambda **_: (SimpleNamespace(), False),
    )

    async def run_sdk_agent(**_: object) -> object:
        return SimpleNamespace(
            interruptions=[],
            final_output={"ok": True},
            last_agent=SimpleNamespace(name="投影智能体"),
            context_wrapper=SimpleNamespace(usage=None),
        )

    monkeypatch.setattr(sdk_adapter, "_run_sdk_agent_async", run_sdk_agent)

    def postprocess(**arguments: object) -> dict[str, object]:
        calls.append(dict(arguments))
        return {"ok": True, "processed": True}

    monkeypatch.setattr(sdk_adapter, "_postprocess_agent_output", postprocess)

    raw_input = {"secret": "完整原始字段"}
    deferred = asyncio.run(
        sdk_adapter.run_agent_async(
            db=object(),
            agent_definition=definition,
            tool_definitions=[],
            execution_context=context,
            input_payload={"ok": "模型视图"},
            skip_output_postprocessor=True,
        )
    )
    assert deferred.output == {"ok": True}
    assert calls == []

    processed = asyncio.run(
        sdk_adapter.run_agent_async(
            db=object(),
            agent_definition=definition,
            tool_definitions=[],
            execution_context=context,
            input_payload=raw_input,
        )
    )
    assert processed.output == {"ok": True, "processed": True}
    assert len(calls) == 1
    assert calls[0]["input_payload"] == raw_input


def test_sdk_agent_hard_timeout_returns_completed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = SimpleNamespace(final_output={"ok": True})

    async def completed_run(*args: object, **kwargs: object) -> object:
        return expected

    monkeypatch.setattr(sdk_adapter, "Runner", SimpleNamespace(run=completed_run))

    result = sdk_adapter._run_sdk_agent_sync(
        agent=SimpleNamespace(),
        runner_input="{}",
        execution_context=SimpleNamespace(),
        max_turns=1,
        run_config=SimpleNamespace(),
        request_timeout_seconds=1,
    )

    assert result is expected


def test_runner_input_adds_retry_feedback_without_mutating_business_payload() -> None:
    payload = {"source_kind": "inline", "requirement": "真实需求"}
    runner_input = _runner_input(
        execution_context=SimpleNamespace(),
        input_payload=payload,
        runtime_config={"input_mode": "text"},
        retry_feedback=(
            "上次输出未通过平台校验：source_anchor.source_span 坐标无效: "
            "start=387, end=387"
        ),
    )

    assert isinstance(runner_input, str)
    assert runner_input.startswith('{"source_kind":"inline","requirement":"真实需求"}')
    assert "【上次输出校验反馈】" in runner_input
    assert "start=387, end=387" in runner_input
    assert "不要回显本段反馈" in runner_input
    assert payload == {"source_kind": "inline", "requirement": "真实需求"}


def test_builtin_workflow_is_data_driven_and_references_registered_definitions() -> None:
    agent_keys = {str(item["agent_key"]) for item in BUILTIN_AGENT_SPECS}
    tool_keys = {str(item["tool_key"]) for item in BUILTIN_TOOL_SPECS}
    assert "test_generation_final_reviewer" in agent_keys
    assert "test_generation_batch_repairer" in agent_keys
    assert "test_generation_global_reviewer" in agent_keys
    assert "test_generation_scenario_designer" in agent_keys
    assert "test_plan_evidence_routing_reviewer" not in agent_keys
    assert "test_evidence_continuity_auditor" not in agent_keys

    generation_spec = next(
        item for item in BUILTIN_WORKFLOW_SPECS
        if item["workflow_key"] == "test_generation"
    )
    generation_agent = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_case_generator"
    )
    assert generation_agent["runtime_config"]["model_route"] == "main"
    assert generation_agent["runtime_config"]["max_turns"] == 4
    assert generation_agent["runtime_config"]["subagent_keys"] == [
        "test_generation_scenario_designer"
    ]
    assert generation_agent["runtime_config"]["tool_keys"] == [
        "submit_generation_batch"
    ]
    assert generation_agent["runtime_config"]["stop_at_tool_keys"] == [
        "submit_generation_batch"
    ]
    assert generation_agent["runtime_config"]["force_terminal_tool_choice"] is False
    assert "必须且只能调用一次 submit_generation_batch" in generation_agent["instructions"]
    assert "禁止使用 json.dumps 或其他方式将数组二次序列化为字符串" in generation_agent[
        "instructions"
    ]
    assert "每个批次最多调用一次" in generation_agent["instructions"]
    assert "禁止再次调用专业 Agent" in generation_agent["instructions"]
    scenario_designer = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_generation_scenario_designer"
    )
    assert scenario_designer["runtime_config"]["model_route"] == "main"
    assert scenario_designer["runtime_config"]["tool_keys"] == [
        "submit_scenario_design_guidance"
    ]
    assert scenario_designer["runtime_config"]["stop_at_tool_keys"] == [
        "submit_scenario_design_guidance"
    ]
    assert "repair_targets" in generation_agent["instructions"]
    assert "P0 仅用于" in generation_agent["instructions"]
    business_planner = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_business_planner"
    )
    assert business_planner["runtime_config"]["tool_keys"] == [
        "submit_business_plan"
    ]
    assert business_planner["runtime_config"]["stop_at_tool_keys"] == [
        "submit_business_plan"
    ]
    assert "必须且只能调用一次 submit_business_plan" in business_planner["instructions"]
    route_gap_reviewer = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_planning_route_gap_reviewer"
    )
    assert route_gap_reviewer["runtime_config"]["result_cache"] == {
        "version": "planning-route-gap-review-v1",
        "accept_legacy": False,
    }
    generation_workflow = parse_execution_definition(generation_spec["definition"])
    assert isinstance(generation_workflow, WorkflowGraph)
    assert generation_workflow.input_schema["properties"]["disable_result_cache"] == {
        "type": "boolean"
    }
    assert generation_spec["version"] == 1
    assert generation_workflow.output_node_key == "persist"
    assert [stage.stage_key for stage in generation_workflow.display_stages] == [
        "planning",
        "generation",
        "audit",
        "review_delivery",
    ]
    workflow_node_keys = {node.node_key for node in generation_workflow.nodes}
    assert all(
        set(stage.node_keys).issubset(workflow_node_keys)
        for stage in generation_workflow.display_stages
    )

    assert "test_generation_coordinator" not in agent_keys

    nodes = {node.node_key: node for node in generation_workflow.nodes}
    assert "coordination" not in nodes
    assert nodes["prepare_source_semantics"].depends_on == ["evidence"]
    assert "coordination" not in nodes["prepare_source_semantics"].input_mapping
    prepare_source_tool = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "prepare_source_semantics"
    )
    assert prepare_source_tool["input_schema"]["required"] == [
        "requirement",
        "evidence_source",
        "evidence_catalog",
    ]
    assert "work_assignments" not in SOURCE_SEMANTICS_INPUT_SCHEMA["properties"]
    assert tool_registry.is_parallel_safe("testing.submit_source_semantics") is True
    assert tool_registry.is_parallel_safe("testing.resolve_requirement_evidence") is False
    assert nodes["prepare_plan_batches"].reference_key == "prepare_business_plan_batches"
    assert nodes["plan_batches"].reference_key == "test_business_plan_batcher"
    assert nodes["plan_batches"].depends_on == ["prepare_plan_batches"]
    assert nodes["prepare_plan_consolidation"].reference_key == (
        "prepare_business_plan_consolidation"
    )
    assert nodes["plan"].reference_key == "test_business_planner"
    assert nodes["plan"].depends_on == ["prepare_plan_consolidation"]
    assert nodes["plan"].input_mapping == {
        "partial_plans": "dependencies.prepare_plan_consolidation.partial_plans",
        "planning_metadata": "dependencies.prepare_plan_consolidation.planning_metadata",
        "coverage_group_catalog": (
            "dependencies.prepare_plan_consolidation.coverage_group_catalog"
        ),
        "case_budget": "dependencies.prepare_plan_consolidation.case_budget",
        "planning_limits": "dependencies.prepare_plan_consolidation.planning_limits",
    }
    assert nodes["chain_context"].depends_on == ["routed_plan", "validated_cases"]
    assert nodes["chain_context"].input_mapping["plan"] == "dependencies.routed_plan"
    assert nodes["final_review_batches"].reference_key == "test_generation_final_reviewer"
    assert nodes["final_review_repairs"].reference_key == "test_generation_batch_repairer"
    assert nodes["global_review"].reference_key == "test_generation_global_reviewer"
    assert nodes["prepare_terminal_final_review_repairs"].reference_key == (
        "prepare_terminal_final_review_repairs"
    )
    assert {
        "source_text",
        "source_vision",
        "plan_batches",
        "plan_routes",
        "plan_route_repairs",
        "authority",
        "generation",
        "final_review_batches",
        "final_review_repairs",
        "final_review_rechecks",
        "followup_final_review_repairs",
        "final_review_final_rechecks",
        "terminal_final_review_repairs",
        "terminal_final_review_rechecks",
    } == {
        node.node_key
        for node in generation_workflow.nodes
        if node.node_type == "agent_map"
    }
    expected_concurrency = {
        "source_text": 6,
        "source_vision": 3,
        "plan_batches": 6,
        "plan_routes": 4,
        "plan_route_repairs": 2,
    }
    for key, max_concurrency in expected_concurrency.items():
        assert nodes[key].max_attempts == 2
        assert nodes[key].map_config is not None
        assert nodes[key].map_config.max_concurrency == max_concurrency
    assert nodes["generation"].max_attempts == 3
    assert nodes["generation"].map_config is not None
    assert nodes["generation"].map_config.max_concurrency == 6
    for key in (
        "final_review_repairs",
        "followup_final_review_repairs",
        "terminal_final_review_repairs",
    ):
        assert nodes[key].max_attempts == 3
        assert nodes[key].map_config is not None
        assert nodes[key].map_config.max_concurrency == 3
    assert nodes["authority"].max_attempts == 2
    assert nodes["authority"].map_config is not None
    assert nodes["authority"].map_config.max_concurrency == 2
    for key in (
        "final_review_batches",
        "final_review_rechecks",
        "final_review_final_rechecks",
        "terminal_final_review_rechecks",
    ):
        assert nodes[key].max_attempts == 2
        assert nodes[key].map_config is not None
        assert nodes[key].map_config.max_concurrency == 3
        assert nodes[key].map_config.item_postprocessor == (
            "testing.postprocess_final_review_batch_item"
        )
    for key in (
        "final_review_repairs",
        "followup_final_review_repairs",
        "terminal_final_review_repairs",
    ):
        assert nodes[key].map_config.item_postprocessor == (
            "testing.postprocess_final_review_repair_item"
        )
    assert nodes["source_text"].map_config.item_postprocessor == (
        "testing.postprocess_source_semantics_item"
    )
    assert nodes["source_vision"].map_config.item_postprocessor == (
        "testing.postprocess_source_semantics_item"
    )
    assert nodes["authority"].map_config.item_postprocessor == (
        "testing.postprocess_authority_reconciliation_item"
    )
    assert nodes["generation"].map_config.item_postprocessor == (
        "testing.postprocess_generation_batch_item"
    )
    assert nodes["plan_routes"].map_config.item_postprocessor == (
        "testing.postprocess_planning_scope_routing_item"
    )
    assert nodes["plan_route_repairs"].reference_key == (
        "test_planning_route_gap_reviewer"
    )
    assert nodes["plan_route_repairs"].map_config.item_postprocessor == (
        "testing.postprocess_planning_route_repair_item"
    )
    audit_tool = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "build_generation_audit_summary"
    )
    approval_tool = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "approve_synthesized_test_cases"
    )
    repair_prepare_tool = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "prepare_final_review_repairs"
    )
    assert audit_tool["input_schema"]["properties"]["generation"] is MERGED_GENERATION_SCHEMA
    assert approval_tool["input_schema"]["properties"]["generation"] is MERGED_GENERATION_SCHEMA
    assert "minItems" not in repair_prepare_tool["input_schema"]["properties"]["review_inputs"]
    assert "minItems" not in repair_prepare_tool["input_schema"]["properties"]["review_records"]
    assert "stabilized_final_review_rechecks" not in nodes
    assert "final_review" not in nodes
    assert nodes["preterminal_review_summary"].input_mapping["recheck_records"] == (
        "dependencies.merged_final_review_rechecks.items"
    )
    assert nodes["global_review_input"].input_mapping["generation"] == (
        "dependencies.final_repaired_cases"
    )
    assert "global_review" in nodes["prepare_terminal_final_review_repairs"].depends_on
    assert nodes["batch_review_summary"].input_mapping["review_records"] == (
        "dependencies.prepare_terminal_final_review_repairs.review_records"
    )
    assert nodes["approved_cases"].input_mapping["generation"] == (
        "dependencies.terminal_repaired_cases"
    )
    assert nodes["approved_cases"].input_mapping["final_review"] == (
        "dependencies.batch_review_summary"
    )

    for spec in BUILTIN_WORKFLOW_SPECS:
        execution = parse_execution_definition(spec["definition"])
        if isinstance(execution, AgentProgramDefinition):
            assert execution.entry_agent_key in agent_keys
            continue
        assert execution.execution_order()
        for node in execution.nodes:
            allowed = (
                agent_keys
                if node.node_type in {"agent", "agent_network", "agent_map"}
                else tool_keys
            )
            assert node.reference_key in allowed
            if node.node_type == "tool":
                tool_spec = next(
                    item for item in BUILTIN_TOOL_SPECS
                    if item["tool_key"] == node.reference_key
                )
                required_inputs = set(tool_spec["input_schema"].get("required", []))
                assert required_inputs <= set(node.input_mapping), (
                    f"工作流 {spec['workflow_key']} 的节点 {node.node_key} "
                    f"缺少必填工具输入映射: "
                    f"{sorted(required_inputs - set(node.input_mapping))}"
                )

    for agent_spec in BUILTIN_AGENT_SPECS:
        for subagent_key in agent_spec["runtime_config"].get("subagent_keys", []):
            assert subagent_key in agent_keys


def test_scenario_designer_only_routes_current_batch_facts_and_design_items() -> None:
    input_payload = {
        "case_budget": 2,
        "authoritative_facts": [
            {"fact_id": "FACT-001", "status": "effective"},
            {"fact_id": "FACT-002", "status": "effective"},
        ],
        "plan": {
            "test_design_items": [
                {"test_design_item_id": "TD-001-001-001"},
                {"test_design_item_id": "TD-001-001-002"},
            ]
        },
    }
    output = {
        "recommended_case_count": 2,
        "scenario_groups": [
            {
                "scenario_key": "正常提交",
                "scenario_type": "main",
                "precondition_fact_ids": [],
                "action_fact_ids": ["FACT-001"],
                "expected_fact_ids": ["FACT-002"],
                "test_design_item_ids": ["TD-001-001-001"],
            }
        ],
        "warnings": [],
    }

    validate_json_schema(instance=output, schema=SCENARIO_DESIGN_GUIDANCE_SCHEMA)
    assert validate_scenario_design_guidance(
        SimpleNamespace(),
        {"input_payload": input_payload, "output": output},
    ) == output

    unknown_fact_output = deepcopy(output)
    unknown_fact_output["scenario_groups"][0]["expected_fact_ids"] = ["FACT-999"]
    with pytest.raises(ValueError, match="当前批次之外的事实"):
        validate_scenario_design_guidance(
            SimpleNamespace(),
            {"input_payload": input_payload, "output": unknown_fact_output},
        )

    unknown_design_output = deepcopy(output)
    unknown_design_output["scenario_groups"][0]["test_design_item_ids"] = [
        "TD-999-999-999"
    ]
    with pytest.raises(ValueError, match="当前批次之外的测试设计项"):
        validate_scenario_design_guidance(
            SimpleNamespace(),
            {"input_payload": input_payload, "output": unknown_design_output},
        )

    over_budget_output = deepcopy(output)
    over_budget_output["recommended_case_count"] = 3
    with pytest.raises(ValueError, match="超过当前批次用例额度"):
        validate_scenario_design_guidance(
            SimpleNamespace(),
            {"input_payload": input_payload, "output": over_budget_output},
        )


def test_global_final_review_contract_accepts_its_own_review_dimensions() -> None:
    batch_agent = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_generation_final_reviewer"
    )
    global_agent = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_generation_global_reviewer"
    )
    terminal_repair_tool = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "prepare_terminal_final_review_repairs"
    )

    def difference(category: str) -> dict[str, object]:
        return {
            "case_id": "TC-001",
            "category": category,
            "field_path": "priority",
            "detail": "同一业务风险的优先级互相冲突",
            "related_fact_ids": [],
            "repair_instruction": "统一同一业务风险的优先级",
        }

    for category in ("coverage_imbalance", "priority_conflict"):
        validate_json_schema(
            instance={"approved": False, "differences": [difference(category)]},
            schema=global_agent["output_schema"],
        )

    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(
            instance={
                "approved": False,
                "differences": [difference("priority_conflict")],
            },
            schema=batch_agent["output_schema"],
        )

    assert terminal_repair_tool["input_schema"]["required"] == [
        "generation_inputs",
        "generation",
        "batch_case_limit",
        "batch_review",
        "global_review",
    ]
    assert terminal_repair_tool["output_schema"]["required"] == [
        "items",
        "repair_batch_count",
        "review_inputs",
        "review_records",
    ]


def test_node_run_serialization_exposes_actual_agent_definition() -> None:
    payload = serialize_node_run(SimpleNamespace(
        id=7,
        node_key="plan",
        node_type="agent",
        agent_definition_id=19,
        status="running",
        attempt=1,
        input_payload={},
        output_payload={},
        sdk_state={},
        error_message="",
        started_at=None,
        finished_at=None,
        created_at=None,
    ))

    assert payload["agent_definition_id"] == 19


def test_run_context_initializes_first_attempt_and_accepts_retry_attempt() -> None:
    run_context = _initial_run_context(execution_limits={})
    retry_context = _initial_run_context(execution_limits={}, run_attempt=2)

    assert run_context["run_attempt"] == 1
    assert retry_context["run_attempt"] == 2
    assert "node_outputs" not in run_context
    assert "node_outputs" not in retry_context


def test_retry_checkpoints_keep_success_nodes_and_partial_agent_map_only() -> None:
    workflow_spec = next(
        item for item in BUILTIN_WORKFLOW_SPECS
        if item["workflow_key"] == "test_generation"
    )
    execution = parse_execution_definition(workflow_spec["definition"])
    assert isinstance(execution, WorkflowGraph)
    node_runs = [
        AgentNodeRun(
            id=1,
            run_id=9,
            node_key="evidence",
            node_type="tool",
            status="success",
            attempt=1,
            output_payload={"requirement": "真实需求"},
        ),
        AgentNodeRun(
            id=2,
            run_id=9,
            node_key="source_text",
            node_type="agent_map",
            status="failed",
            attempt=1,
            output_payload={"items": [{"item_index": 0, "output": {}}]},
        ),
        AgentNodeRun(
            id=3,
            run_id=9,
            node_key="plan",
            node_type="agent",
            status="failed",
            attempt=1,
            output_payload={},
        ),
    ]

    checkpoints = _restorable_node_runs(
        execution=execution,
        node_runs=node_runs,
    )

    assert [item.node_key for item in checkpoints] == ["evidence", "source_text"]


def test_restored_checkpoint_keeps_source_execution_duration() -> None:
    checkpoint = AgentNodeRun(
        id=12,
        run_id=9,
        node_key="source_text",
        node_type="agent_map",
        status="success",
        attempt=1,
        sdk_state={"parallelism": {"max_concurrency": 6}},
        started_at=datetime(2026, 8, 27, 2, 40, 0),
        finished_at=datetime(2026, 8, 27, 2, 43, 15),
    )

    state = _restored_checkpoint_sdk_state(checkpoint)

    assert state["parallelism"] == {"max_concurrency": 6}
    assert state["checkpoint_restore"] == {
        "source_run_id": 9,
        "source_node_run_id": 12,
        "source_started_at": "2026-08-27T02:40:00",
        "source_finished_at": "2026-08-27T02:43:15",
        "source_duration_seconds": 195,
    }


def test_legacy_execution_limits_are_normalized_for_compatibility(
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


def test_planner_and_scope_router_have_separate_contracts() -> None:
    batch_planner = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_business_plan_batcher"
    )
    assert batch_planner["output_schema"] is BUSINESS_PLAN_DRAFT_SCHEMA
    assert batch_planner["runtime_config"]["max_output_tokens"] == 5000
    assert batch_planner["runtime_config"]["output_postprocessor"] == (
        "testing.validate_business_plan_draft_output"
    )
    assert "每个真实 fact_id 必须至少出现在" in batch_planner["instructions"]
    planner = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_business_planner"
    )
    module_schema = planner["output_schema"]["properties"]["business_modules"]["items"]

    assert "evidence_ids" not in module_schema["properties"]
    assert "evidence_ids" not in module_schema["required"]
    assert "fact_ids" not in module_schema["properties"]
    assert "fact_ids" not in module_schema["required"]
    assert "不负责输出证据路由" in planner["instructions"]
    assert "coordination.assignments" not in planner["instructions"]
    assert planner["version"] == 3
    assert planner["runtime_config"]["model_route"] == "review"
    assert planner["runtime_config"]["max_output_tokens"] == 12000
    assert planner["runtime_config"]["result_cache"] == {
        "version": "business-plan-v5-coverage-groups",
        "accept_legacy": False,
    }
    assert planner["runtime_config"]["output_postprocessor"] == (
        "testing.validate_business_plan_output"
    )
    assert "partial_plans" in planner["instructions"]
    assert planner["runtime_config"]["disable_server_output_schema"] is True
    scope_router = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_planning_scope_router"
    )
    assert scope_router["runtime_config"]["disable_server_output_schema"] is True
    assert scope_router["version"] == 2
    assert scope_router["runtime_config"]["result_cache"] == {
        "version": "planning-scope-routes-v4-fact-refs",
        "accept_legacy": False,
    }
    assert scope_router["output_schema"] is PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA
    route_assignment_schema = scope_router["output_schema"]["properties"][
        "routes"
    ]["items"]["properties"]["assignments"]["items"]
    assert "reason" not in route_assignment_schema["properties"]
    assert "reason" not in route_assignment_schema["required"]
    assert "fact_id" not in route_assignment_schema["properties"]
    assert route_assignment_schema["properties"]["fact_ref"] == {
        "type": "string",
        "pattern": "^RF-[0-9]{3,}$",
    }
    assert "reason" not in scope_router["instructions"]
    route_schema = route_assignment_schema["properties"]["module_routes"]["items"]
    assert "minItems" not in route_schema["properties"]["test_design_item_indexes"]
    assert "目录中没有任何测试设计项受到该事实直接支持" in scope_router[
        "instructions"
    ]
    assert "不得只凭词面相似或为了补齐编号" in scope_router["instructions"]
    authority_reviewer = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_authority_reconciliation_reviewer"
    )
    assert authority_reviewer["output_schema"] is (
        AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA
    )
    strict_relation_schema = (
        AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA["properties"]["decisions"]["items"]
        ["properties"]["governed_by"]["items"]["properties"]["relation"]
    )
    agent_relation_schema = (
        AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA["properties"]["decisions"]
        ["items"]["properties"]["governed_by"]["items"]["properties"]["relation"]
    )
    strict_governance_schema = (
        AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA["properties"]["decisions"]["items"]
        ["properties"]["governed_by"]["items"]
    )
    agent_governance_schema = (
        AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA["properties"]["decisions"]
        ["items"]["properties"]["governed_by"]["items"]
    )
    assert "superseded_by" not in strict_relation_schema["enum"]
    assert "superseded_by" in agent_relation_schema["enum"]
    assert strict_governance_schema["required"] == [
        "relation",
        "directive_fact_id",
    ]
    assert agent_governance_schema["required"] == ["relation", "fact_id"]
    assert "directive_fact_id" not in agent_governance_schema["properties"]
    assert "governed_by 每项只能包含 relation 和 fact_id" in authority_reviewer[
        "instructions"
    ]
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


def test_business_planning_batches_preserve_all_facts_with_bounded_inputs() -> None:
    planning_scopes = [
        {
            "scope_id": f"EV-{scope_index + 1:04d}",
            "facts": [
                {
                    "fact_id": f"F-{scope_index + 1:02d}-{fact_index + 1:03d}",
                    "assertion": f"范围 {scope_index + 1} 的真实事实 {fact_index + 1}",
                    "value_policy": "exact",
                    "governed_values": ["示例值"],
                }
                for fact_index in range(95 if scope_index == 0 else 45)
            ],
        }
        for scope_index in range(2)
    ]
    expected_fact_ids = [
        fact["fact_id"]
        for scope in planning_scopes
        for fact in scope["facts"]
    ]
    context = SimpleNamespace(artifacts={})

    result = prepare_business_plan_batches(
        context,
        {"planning_scopes": planning_scopes, "case_budget": 80},
    )

    actual_fact_ids = [
        fact["fact_id"]
        for item in result["items"]
        for scope in item["planning_scopes"]
        for fact in scope["facts"]
    ]
    assert actual_fact_ids == expected_fact_ids
    assert result["batch_count"] >= 2
    assert all(
        item["planning_batch"]["fact_count"] <= BUSINESS_PLANNING_BATCH_MAX_FACTS
        for item in result["items"]
    )
    assert all(
        len(json.dumps({"planning_scopes": item["planning_scopes"]}, ensure_ascii=False))
        <= BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS
        for item in result["items"]
    )
    assert context.artifacts["business_planning_batch_plan"]["fact_count"] == 140
    assert context.artifacts["business_planning_batch_plan"]["model_input_reduction_ratio"] > 0
    assert set(result["items"][0]["planning_scopes"][0]["facts"][0]) == {
        "fact_id",
        "assertion",
    }


def test_business_plan_drafts_require_complete_fact_coverage_and_compact_merge() -> None:
    planning_scopes = [
        {
            "scope_id": "EV-0001",
            "facts": [
                {"fact_id": "F-001", "assertion": "可进入课程"},
                {"fact_id": "F-002", "assertion": "可提交作文"},
            ],
        }
    ]
    prepared = {
        "planning_scopes": planning_scopes,
        "case_budget": 20,
        "planning_batch": {"batch_number": 1, "batch_count": 1},
    }
    draft = {
        "batch_summary": "课程学习与作文提交",
        "module_candidates": [
            {
                "name": "课程学习",
                "objective": "完成课程学习并提交作文",
                "actors": ["学生"],
                "lifecycle": None,
                "coverage_topics": [
                    {"name": "学习与提交", "objective": "验证进入课程和提交作文"}
                ],
                "fact_ids": ["F-001", "F-002"],
            }
        ],
        "coverage_focus": ["主流程"],
        "risks": ["提交失败"],
    }
    validate_json_schema(instance=draft, schema=BUSINESS_PLAN_DRAFT_SCHEMA)

    with pytest.raises(ValueError, match="没有完整承接真实事实"):
        validate_business_plan_draft_output(
            SimpleNamespace(),
            {
                "input_payload": prepared,
                "output": {
                    **draft,
                    "module_candidates": [
                        {**draft["module_candidates"][0], "fact_ids": ["F-001"]}
                    ],
                },
            },
        )

    context = SimpleNamespace(artifacts={})
    result = prepare_business_plan_consolidation(
        context,
        {
            "prepared_items": [prepared],
            "plan_records": [{"item_index": 0, "output": draft}],
            "case_budget": 20,
        },
    )

    assert result["covered_fact_count"] == 2
    assert result["case_budget"] == 20
    assert result["planning_limits"] == {
        "max_business_modules": 1,
        "max_test_points": 1,
        "max_test_designs": 1,
        "max_coverage_items": 2,
    }
    compact_candidate = result["partial_plans"][0]["draft"]["module_candidates"][0]
    assert "fact_ids" not in compact_candidate
    assert compact_candidate["coverage_topics"] == ["CG-0001"]
    assert result["coverage_group_catalog"] == [
        {
            "coverage_group_id": "CG-0001",
            "name": "课程学习",
            "objective": "完成课程学习并提交作文",
            "coverage_items": ["验证进入课程和提交作文"],
        }
    ]
    assert set(result["partial_plans"][0]["draft"]) == {"module_candidates"}
    assert result["planning_metadata"] == {
        "coverage_focus": ["主流程"],
        "risks": ["提交失败"],
    }
    assert context.artifacts["business_planning_batch_results"] == {
        "batch_count": 1,
        "covered_fact_count": 2,
    }

    large_result = prepare_business_plan_consolidation(
        SimpleNamespace(artifacts={}),
        {
            "prepared_items": [prepared],
            "plan_records": [{"item_index": 0, "output": draft}],
            "case_budget": 80,
        },
    )
    assert large_result["planning_limits"] == {
        "max_business_modules": 1,
        "max_test_points": 1,
        "max_test_designs": 1,
        "max_coverage_items": 2,
    }

    duplicate_result = prepare_business_plan_consolidation(
        SimpleNamespace(artifacts={}),
        {
            "prepared_items": [prepared, prepared],
            "plan_records": [
                {"item_index": 0, "output": draft},
                {"item_index": 1, "output": draft},
            ],
            "case_budget": 20,
        },
    )
    assert len(duplicate_result["coverage_group_catalog"]) == 1
    assert duplicate_result["partial_plans"][0]["draft"]["module_candidates"][0][
        "coverage_topics"
    ] == ["CG-0001"]
    assert duplicate_result["partial_plans"][1]["draft"]["module_candidates"] == []
    referenced_topic_ids = [
        topic_id
        for partial in duplicate_result["partial_plans"]
        for candidate in partial["draft"]["module_candidates"]
        for topic_id in candidate["coverage_topics"]
    ]
    assert referenced_topic_ids == ["CG-0001"]

    assert _business_planning_limits(
        module_candidate_count=83,
        coverage_topic_count=213,
        covered_fact_count=372,
    ) == {
        "max_business_modules": 83,
        "max_test_points": 213,
        "max_test_designs": 213,
        "max_coverage_items": 372,
    }


def test_business_plan_output_enforces_data_driven_planning_limits() -> None:
    test_points = [
        {
            "name": f"测试点 {index + 1}",
            "objective": "验证真实业务行为",
            "test_designs": [
                {
                    "technique": "场景法",
                    "rationale": "覆盖主流程",
                    "coverage_items": [f"覆盖意图 {index + 1}"],
                }
            ],
        }
        for index in range(9)
    ]
    output = {
        "requirement_summary": "真实需求",
        "business_modules": [
            {
                "name": "课程学习",
                "objective": "完成课程学习",
                "actors": ["学生"],
                "lifecycle": None,
                "test_points": test_points,
            }
        ],
        "coverage_focus": ["主流程"],
        "risks": ["服务异常"],
    }
    planning_limits = {
        "max_business_modules": 4,
        "max_test_points": 8,
        "max_test_designs": 8,
        "max_coverage_items": 20,
    }
    partial_plans = [
        {
            "batch_number": 1,
            "batch_count": 1,
            "draft": {
                "coverage_focus": ["主流程", "异常路径"],
                "risks": ["服务异常"],
            },
        }
    ]

    with pytest.raises(ValueError, match="超过动态容量"):
        validate_business_plan_output(
            SimpleNamespace(),
            {
                "input_payload": {
                    "planning_limits": planning_limits,
                    "partial_plans": partial_plans,
                },
                "output": output,
            },
        )

    valid = deepcopy(output)
    valid["business_modules"][0]["test_points"] = test_points[:8]
    assert validate_business_plan_output(
        SimpleNamespace(),
        {
            "input_payload": {
                "planning_limits": planning_limits,
                "partial_plans": partial_plans,
            },
            "output": valid,
        },
    ) == {
        **valid,
        "coverage_focus": ["主流程", "异常路径"],
        "risks": ["服务异常"],
    }


def test_business_plan_rejects_over_budget_coverage_without_rewriting_atomic_items() -> None:
    output = {
        "requirement_summary": "真实需求",
        "business_modules": [
            {
                "name": "课程学习",
                "objective": "完成课程学习",
                "actors": ["学生"],
                "lifecycle": None,
                "test_points": [
                    {
                        "name": "主流程",
                        "objective": "验证学习流程",
                        "test_designs": [
                            {
                                "technique": "场景法",
                                "rationale": "覆盖主流程",
                                "coverage_items": ["打开课程", "完成学习", "提交结果"],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="超过动态容量"):
        validate_business_plan_output(
            SimpleNamespace(artifacts={}),
            {
            "input_payload": {
                "planning_limits": {
                    "max_business_modules": 4,
                    "max_test_points": 8,
                    "max_test_designs": 8,
                    "max_coverage_items": 2,
                },
                "partial_plans": [{"draft": {"coverage_focus": ["主流程"]}}],
            },
            "output": output,
            },
        )
    assert output["business_modules"][0]["test_points"][0]["test_designs"][0][
        "coverage_items"
    ] == ["打开课程", "完成学习", "提交结果"]


def test_business_plan_expands_groups_and_rejects_missing_or_duplicate_groups() -> None:
    input_payload = {
        "partial_plans": [{"draft": {"module_candidates": [{}]}}],
        "planning_metadata": {"coverage_focus": ["主流程"], "risks": []},
        "coverage_group_catalog": [
            {
                "coverage_group_id": "CG-0001",
                "name": "课程学习",
                "objective": "验证学生学习课程",
                "coverage_items": ["验证学生可进入课程", "验证学生完成学习"],
            },
            {
                "coverage_group_id": "CG-0002",
                "name": "提交作文",
                "objective": "验证学生可提交作文",
                "coverage_items": ["验证学生可提交作文"],
            },
        ],
        "planning_limits": {
            "max_business_modules": 2,
            "max_test_points": 2,
            "max_test_designs": 2,
            "max_coverage_items": 3,
        },
    }
    output = {
        "requirement_summary": "课程学习与作文提交",
        "business_modules": [
            {
                "name": "课程学习",
                "objective": "完成课程学习并提交作文",
                "actors": ["学生"],
                "lifecycle": None,
                "test_points": [
                    {
                        "name": "学习与提交",
                        "objective": "验证学习和提交主流程",
                        "test_designs": [
                            {
                                "technique": "场景法",
                                "rationale": "覆盖主流程",
                                "coverage_items": ["CG-0001", "CG-0002"],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    normalized = validate_business_plan_output(
        SimpleNamespace(),
        {"input_payload": input_payload, "output": output},
    )
    assert normalized["business_modules"][0]["test_points"][0]["test_designs"][0][
        "coverage_items"
    ] == ["验证学生可进入课程", "验证学生完成学习", "验证学生可提交作文"]
    assert normalized["coverage_focus"] == ["主流程"]
    assert normalized["risks"] == []
    assert output["business_modules"][0]["test_points"][0]["test_designs"][0][
        "coverage_items"
    ] == ["CG-0001", "CG-0002"]

    duplicate = deepcopy(output)
    duplicate["business_modules"][0]["test_points"][0]["test_designs"][0][
        "coverage_items"
    ] = ["CG-0001", "CG-0001"]
    with pytest.raises(ValueError, match="必须且只能承接一次全部覆盖语义组"):
        validate_business_plan_output(
            SimpleNamespace(),
            {"input_payload": input_payload, "output": duplicate},
        )


def test_business_planner_compiles_summary_fields_from_validated_batches() -> None:
    planner = next(
        item
        for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_business_planner"
    )

    assert planner["output_schema"] is PLANNER_AGENT_SUBMISSION_SCHEMA
    assert set(PLANNER_AGENT_OUTPUT_SCHEMA["required"]) == {
        "requirement_summary",
        "business_modules",
    }
    assert "coverage_focus" not in PLANNER_AGENT_OUTPUT_SCHEMA["properties"]
    assert "risks" not in PLANNER_AGENT_OUTPUT_SCHEMA["properties"]
    coverage_item_schema = PLANNER_AGENT_SUBMISSION_SCHEMA["properties"][
        "business_modules"
    ]["items"]["properties"]["test_points"]["items"]["properties"][
        "test_designs"
    ]["items"]["properties"]["coverage_items"]["items"]
    assert coverage_item_schema == {
        "type": "string",
        "pattern": "^CG-[0-9]{4,}$",
    }
    assert "不得重复输出" in planner["instructions"]


def test_scope_routes_merge_fact_level_primary_and_shared_assignments() -> None:
    plan = {
        "requirement_summary": "真实需求",
        "business_modules": [
            {
                "name": "入口",
                "objective": "进入功能",
                "actors": ["用户"],
                "lifecycle": None,
                "test_points": _route_test_points("入口流程"),
            },
            {
                "name": "内容",
                "objective": "使用内容",
                "actors": ["用户"],
                "lifecycle": None,
                "test_points": _route_test_points("内容流程"),
            },
        ],
        "coverage_focus": ["入口"],
        "risks": ["内容边界"],
    }
    planning_scopes = [
        {"scope_id": "EV-0001", "facts": [{"fact_id": "F-001", "assertion": "入口一"}]},
        {"scope_id": "EV-0002", "facts": [{"fact_id": "F-002", "assertion": "入口二"}]},
    ]
    prepared = prepare_planning_scope_routes(
        SimpleNamespace(),
        {"planning_scopes": planning_scopes, "plan": plan},
    )
    records = [
        {
            "item_index": 0,
            "output": {
                "scope_id": "EV-0001",
                "assignments": [{
                    "fact_id": "F-001",
                    "module_routes": [_module_route(0)],
                }],
            },
        },
        {
            "item_index": 1,
            "output": {
                "scope_id": "EV-0002",
                "assignments": [{
                    "fact_id": "F-002",
                    "module_routes": [
                        _module_route(0),
                        _module_route(1, relation="shared"),
                    ],
                }],
            },
        },
    ]
    validate_json_schema(
        instance=records[0]["output"],
        schema=PLANNING_SCOPE_ROUTING_OUTPUT_SCHEMA,
    )
    agent_batch_output = {
        "routes": [
            {
                "scope_id": "EV-0001",
                "assignments": [{"fact_ref": "RF-001", "module_routes": [_module_route(0)]}],
            },
            {
                "scope_id": "EV-0002",
                "assignments": [
                    {
                        "fact_ref": "RF-001",
                        "module_routes": [
                            _module_route(0),
                            _module_route(1, relation="shared"),
                        ],
                    }
                ],
            },
        ]
    }
    validate_json_schema(
        instance=agent_batch_output,
        schema=PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA,
    )
    normalized_batch_output = postprocess_planning_scope_routing_item(
        SimpleNamespace(),
        {
            "item_input": prepared["batch_items"][0],
            "item_output": agent_batch_output,
        },
    )
    batch_records = [
        {
            "item_index": 0,
            "output": normalized_batch_output,
        }
    ]
    validate_json_schema(
        instance=batch_records[0]["output"],
        schema=PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA,
    )
    assert prepared["scope_count"] == 2
    assert prepared["batch_count"] == 1
    assert all(
        set(fact) == {"fact_ref", "fact_id", "assertion"}
        for fact in prepared["batch_items"][0]["scopes"][0]["facts"]
    )
    assert all(
        "test_points" not in module
        for module in prepared["batch_items"][0]["business_modules"]
    )

    context = SimpleNamespace(artifacts={})
    merged = merge_planning_scope_routes(
        context,
        {
            "plan": plan,
            "prepared_items": prepared["batch_items"],
            "route_records": batch_records,
        },
    )

    assert merged["business_modules"][0]["evidence_ids"] == ["EV-0001", "EV-0002"]
    assert merged["business_modules"][1]["evidence_ids"] == ["EV-0002"]
    assert merged["business_modules"][0]["fact_ids"] == ["F-001", "F-002"]
    assert merged["business_modules"][1]["fact_ids"] == ["F-002"]
    assert merged["business_modules"][0]["fact_design_routes"] == [
        {"fact_id": "F-001", "test_design_item_indexes": [0]},
        {"fact_id": "F-002", "test_design_item_indexes": [0]},
    ]
    assert merged["business_modules"][1]["fact_design_routes"] == [
        {"fact_id": "F-002", "test_design_item_indexes": [0]}
    ]
    assert context.artifacts["planning_fact_routes"] == {
        "input_module_count": 2,
        "routed_module_count": 2,
        "fact_count": 2,
        "fact_assignment_count": 3,
        "shared_fact_count": 1,
        "max_fact_reuse": 2,
        "fact_design_assignment_count": 3,
        "multi_design_fact_route_count": 0,
        "initial_gap_module_count": 0,
        "initial_gap_design_item_count": 0,
        "route_repair_record_count": 0,
        "repaired_design_item_count": 0,
        "removed_unsupported_design_item_count": 0,
        "unsupported_design_items": [],
        "unmatched_test_design_fact_count": 0,
        "unmatched_test_design_fact_ids": [],
    }


def test_scope_routes_keep_fact_without_matching_planned_design() -> None:
    plan = {
        "requirement_summary": "功能保留展示，同时下线导出入口。",
        "business_modules": [
            {
                "name": "结果展示",
                "objective": "展示处理结果",
                "actors": ["用户"],
                "lifecycle": None,
                "test_points": _route_test_points("结果展示"),
            }
        ],
        "coverage_focus": ["结果展示"],
        "risks": [],
    }
    prepared = prepare_planning_scope_routes(
        SimpleNamespace(),
        {
            "planning_scopes": [
                {
                    "scope_id": "EV-0001",
                    "facts": [
                        {"fact_id": "F-SHOW", "assertion": "展示处理结果"},
                        {"fact_id": "F-REMOVED", "assertion": "导出入口不再提供"},
                    ],
                }
            ],
            "plan": plan,
        },
    )
    output = {
        "scope_id": "EV-0001",
        "assignments": [
            {
                "fact_id": "F-SHOW",
                "module_routes": [_module_route(0, design_indexes=[0])],
            },
            {
                "fact_id": "F-REMOVED",
                "module_routes": [_module_route(0, design_indexes=[])],
            },
        ],
    }
    validate_json_schema(
        instance=output,
        schema=PLANNING_SCOPE_ROUTING_OUTPUT_SCHEMA,
    )

    context = SimpleNamespace(artifacts={})
    merged = merge_planning_scope_routes(
        context,
        {
            "plan": plan,
            "prepared_items": prepared["items"],
            "route_records": [{"item_index": 0, "output": output}],
        },
    )

    module = merged["business_modules"][0]
    assert module["fact_ids"] == ["F-SHOW", "F-REMOVED"]
    assert module["fact_design_routes"] == [
        {"fact_id": "F-SHOW", "test_design_item_indexes": [0]},
        {"fact_id": "F-REMOVED", "test_design_item_indexes": []},
    ]
    assert context.artifacts["planning_fact_routes"][
        "unmatched_test_design_fact_ids"
    ] == ["F-REMOVED"]
    assert context.artifacts["planning_fact_routes"][
        "unmatched_test_design_fact_count"
    ] == 1


def test_scope_routes_split_large_model_view_without_breaking_scope_boundaries() -> None:
    plan = {
        "business_modules": [
            {
                "name": "课程内容",
                "objective": "验证课程内容",
                "actors": ["用户"],
                "lifecycle": None,
                "test_points": _route_test_points("课程内容"),
            }
        ]
    }
    long_assertion = "真实课程规则" * 150
    planning_scopes = [
        {
            "scope_id": "EV-0101",
            "facts": [
                {"fact_id": f"F-101-{index:02d}", "assertion": long_assertion}
                for index in range(12)
            ],
        },
        {
            "scope_id": "EV-0102",
            "facts": [
                {"fact_id": f"F-102-{index:02d}", "assertion": long_assertion}
                for index in range(12)
            ],
        },
    ]

    context = SimpleNamespace(artifacts={})
    prepared = prepare_planning_scope_routes(
        context,
        {"planning_scopes": planning_scopes, "plan": plan},
    )

    assert prepared["scope_count"] == 2
    assert prepared["batch_count"] == 2
    assert [len(item["scopes"]) for item in prepared["batch_items"]] == [1, 1]
    assert context.artifacts["planning_scope_route_plan"]["max_model_input_chars"] == 16000
    assert context.artifacts["planning_scope_route_plan"]["oversized_single_scope_count"] == 0


def test_scope_route_postprocessor_normalizes_primary_route_first() -> None:
    model_output = {
        "scope_id": "EV-0028",
        "assignments": [
            {
                "fact_ref": "RF-001",
                "module_routes": [
                    _module_route(1, relation="shared"),
                    _module_route(2),
                ],
            }
        ],
    }
    item_input = {
        "scope_id": "EV-0028",
        "facts": [
            {
                "fact_ref": "RF-001",
                "fact_id": "DOC259-P0028-fact2",
            }
        ],
        "business_modules": [
            {
                "module_index": index,
                "name": f"模块{index}",
                "test_design_items": [{"test_design_item_index": 0}],
            }
            for index in range(3)
        ],
    }
    validate_json_schema(
        instance={"routes": [model_output]},
        schema=PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA,
    )

    normalized = postprocess_planning_scope_routing_item(
        SimpleNamespace(),
        {"item_input": item_input, "item_output": model_output},
    )

    assert normalized["assignments"][0]["module_routes"] == [
        _module_route(2),
        _module_route(1, relation="shared"),
    ]
    assert normalized["assignments"][0]["fact_id"] == "DOC259-P0028-fact2"
    assert "fact_ref" not in normalized["assignments"][0]
    validate_json_schema(
        instance=normalized,
        schema=PLANNING_SCOPE_ROUTING_OUTPUT_SCHEMA,
    )


def test_scope_route_postprocessor_merges_redundant_route_for_same_module() -> None:
    item_input = {
        "scope_id": "EV-0001",
        "facts": [{"fact_id": "F-001"}],
        "business_modules": [
            {
                "module_index": 0,
                "name": "订单处理",
                "test_design_items": [
                    {"test_design_item_index": 0},
                    {"test_design_item_index": 1},
                ],
            }
        ],
    }
    model_output = {
        "scope_id": "EV-0001",
        "assignments": [
            {
                "fact_id": "F-001",
                "module_routes": [
                    _module_route(0, design_indexes=[1]),
                    _module_route(0, relation="shared", design_indexes=[0]),
                ],
            }
        ],
    }

    normalized = postprocess_planning_scope_routing_item(
        SimpleNamespace(),
        {"item_input": item_input, "item_output": model_output},
    )

    assert normalized["assignments"][0]["module_routes"] == [
        _module_route(0, design_indexes=[0, 1])
    ]


def test_scope_route_postprocessor_rejects_real_out_of_range_module_index() -> None:
    fact_ids = [
        "DOC259-P0016-fact_001",
        "DOC259-P0016-fact_002",
        "DOC259-P0016-fact_003",
    ]
    item_input = {
        "scope_id": "EV-0016",
        "facts": [{"fact_id": fact_id} for fact_id in fact_ids],
        "business_modules": [
            {
                "module_index": index,
                "name": f"模块{index}",
                "test_design_items": [{"test_design_item_index": 0}],
            }
            for index in range(8)
        ],
    }
    model_output = {
        "scope_id": "EV-0016",
        "assignments": [
            {
                "fact_id": fact_id,
                "module_routes": [_module_route(8)],
            }
            for fact_id in fact_ids
        ],
    }

    with pytest.raises(ValueError, match="模块或测试设计映射无效"):
        postprocess_planning_scope_routing_item(
            SimpleNamespace(),
            {"item_input": item_input, "item_output": model_output},
        )


def test_scope_route_postprocessor_rejects_out_of_range_design_index() -> None:
    item_input = {
        "scope_id": "EV-0001",
        "facts": [{"fact_id": "F-001"}],
        "business_modules": [
            {
                "module_index": 0,
                "name": "订单处理",
                "test_design_items": [{"test_design_item_index": 0}],
            }
        ],
    }
    model_output = {
        "scope_id": "EV-0001",
        "assignments": [
            {
                "fact_id": "F-001",
                "module_routes": [_module_route(0, design_indexes=[1])],
            }
        ],
    }

    with pytest.raises(ValueError, match="测试设计项下标越界"):
        postprocess_planning_scope_routing_item(
            SimpleNamespace(),
            {"item_input": item_input, "item_output": model_output},
        )


def test_scope_routes_reject_uncovered_planned_design_item() -> None:
    plan = {
        "requirement_summary": "真实需求",
        "business_modules": [
            {
                "name": "订单处理",
                "objective": "验证订单提交与取消",
                "actors": ["用户"],
                "lifecycle": None,
                "test_points": [
                    {
                        "name": "订单流程",
                        "objective": "验证订单流程",
                        "test_designs": [
                            {
                                "technique": "场景法",
                                "rationale": "覆盖订单状态变化",
                                "coverage_items": ["提交订单", "取消订单"],
                            }
                        ],
                    }
                ],
            }
        ],
        "coverage_focus": ["订单流程"],
        "risks": [],
    }
    prepared = prepare_planning_scope_routes(
        SimpleNamespace(),
        {
            "planning_scopes": [
                {"scope_id": "EV-0001", "facts": [{"fact_id": "F-001"}]}
            ],
            "plan": plan,
        },
    )

    with pytest.raises(ValueError, match="没有承接模块的全部测试设计项"):
        merge_planning_scope_routes(
            SimpleNamespace(artifacts={}),
            {
                "plan": plan,
                "prepared_items": prepared["items"],
                "route_records": [
                    {
                        "item_index": 0,
                        "output": {
                            "scope_id": "EV-0001",
                            "assignments": [
                                {
                                    "fact_id": "F-001",
                                    "module_routes": [
                                        _module_route(0, design_indexes=[0])
                                    ],
                                }
                            ],
                        },
                    }
                ],
            },
        )


def test_scope_route_gap_review_repairs_missing_design_with_real_fact() -> None:
    plan = {
        "requirement_summary": "完成学习后展示详情弹窗。",
        "business_modules": [
            {
                "name": "学习结果",
                "objective": "验证完成状态和详情弹窗",
                "actors": ["用户"],
                "lifecycle": "未完成->已完成",
                "test_points": [
                    {
                        "name": "完成反馈",
                        "objective": "验证完成后的反馈",
                        "test_designs": [
                            {
                                "technique": "状态迁移",
                                "rationale": "覆盖完成后的两个结果",
                                "coverage_items": ["完成后点亮", "弹窗展示详情"],
                            }
                        ],
                    }
                ],
            }
        ],
        "coverage_focus": ["完成反馈"],
        "risks": [],
    }
    prepared = prepare_planning_scope_routes(
        SimpleNamespace(),
        {
            "planning_scopes": [
                {
                    "scope_id": "EV-0001",
                    "facts": [
                        {
                            "fact_id": "F-DETAIL",
                            "assertion": "完成学习后图标点亮，并打开包含技法说明的详情弹窗。",
                        }
                    ],
                }
            ],
            "plan": plan,
        },
    )
    route_records = [
        {
            "item_index": 0,
            "output": {
                "scope_id": "EV-0001",
                "assignments": [
                    {
                        "fact_id": "F-DETAIL",
                        "module_routes": [_module_route(0, design_indexes=[0])],
                    }
                ],
            },
        }
    ]

    repair_input = prepare_planning_route_repairs(
        SimpleNamespace(),
        {
            "plan": plan,
            "prepared_items": prepared["items"],
            "route_records": route_records,
        },
    )

    assert repair_input["gap_module_count"] == 1
    assert repair_input["gap_design_item_count"] == 1
    assert repair_input["items"][0]["missing_test_design_items"] == [
        {
            "test_design_item_index": 1,
            "test_point": "完成反馈",
            "technique": "状态迁移",
            "coverage_intent": "弹窗展示详情",
        }
    ]
    assert repair_input["items"][0]["candidate_facts"][0]["fact_id"] == "F-DETAIL"
    repair_output = postprocess_planning_route_repair_item(
        SimpleNamespace(),
        {
            "item_input": repair_input["items"][0],
            "item_output": {
                "module_index": 0,
                "decisions": [
                    {
                        "test_design_item_index": 1,
                        "disposition": "supported",
                        "fact_ids": ["F-DETAIL"],
                        "reason": "事实明确要求完成后打开包含说明的详情弹窗。",
                    }
                ],
            },
        },
    )
    validate_json_schema(
        instance=repair_output,
        schema=PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA,
    )

    context = SimpleNamespace(artifacts={})
    merged = merge_planning_scope_routes(
        context,
        {
            "plan": plan,
            "prepared_items": prepared["items"],
            "route_records": route_records,
            "repair_records": [{"item_index": 0, "output": repair_output}],
        },
    )

    assert merged["business_modules"][0]["fact_design_routes"] == [
        {"fact_id": "F-DETAIL", "test_design_item_indexes": [0, 1]}
    ]
    assert context.artifacts["planning_fact_routes"]["initial_gap_design_item_count"] == 1
    assert context.artifacts["planning_fact_routes"]["repaired_design_item_count"] == 1


def test_scope_route_gap_review_prunes_unsupported_design_and_reindexes_routes() -> None:
    plan = {
        "requirement_summary": "真实需求只定义提交和完成。",
        "business_modules": [
            {
                "name": "学习提交",
                "objective": "验证学习提交闭环",
                "actors": ["用户"],
                "lifecycle": None,
                "test_points": [
                    {
                        "name": "提交结果",
                        "objective": "验证提交与完成",
                        "test_designs": [
                            {
                                "technique": "场景法",
                                "rationale": "覆盖真实流程",
                                "coverage_items": [
                                    "提交内容",
                                    "来源未定义的自动退款",
                                    "完成学习",
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
        "coverage_focus": ["提交结果"],
        "risks": [],
    }
    prepared = prepare_planning_scope_routes(
        SimpleNamespace(),
        {
            "planning_scopes": [
                {
                    "scope_id": "EV-0001",
                    "facts": [
                        {
                            "fact_id": "F-REAL",
                            "assertion": "用户提交内容后完成学习。",
                        }
                    ],
                }
            ],
            "plan": plan,
        },
    )
    route_records = [
        {
            "item_index": 0,
            "output": {
                "scope_id": "EV-0001",
                "assignments": [
                    {
                        "fact_id": "F-REAL",
                        "module_routes": [
                            _module_route(0, design_indexes=[0, 2])
                        ],
                    }
                ],
            },
        }
    ]
    repair_input = prepare_planning_route_repairs(
        SimpleNamespace(),
        {
            "plan": plan,
            "prepared_items": prepared["items"],
            "route_records": route_records,
        },
    )
    repair_output = {
        "module_index": 0,
        "decisions": [
            {
                "test_design_item_index": 1,
                "disposition": "unsupported",
                "fact_ids": [],
                "reason": "真实事实没有定义退款行为。",
            }
        ],
    }
    context = SimpleNamespace(artifacts={})

    merged = merge_planning_scope_routes(
        context,
        {
            "plan": plan,
            "prepared_items": prepared["items"],
            "route_records": route_records,
            "repair_records": [{"item_index": 0, "output": repair_output}],
        },
    )

    assert merged["business_modules"][0]["test_points"][0]["test_designs"][0][
        "coverage_items"
    ] == ["提交内容", "完成学习"]
    assert merged["business_modules"][0]["fact_design_routes"] == [
        {"fact_id": "F-REAL", "test_design_item_indexes": [0, 1]}
    ]
    route_artifact = context.artifacts["planning_fact_routes"]
    assert route_artifact["removed_unsupported_design_item_count"] == 1
    assert route_artifact["unsupported_design_items"][0]["design_index"] == 1


def test_scope_route_gap_review_rejects_unknown_fact_and_unsupported_gap() -> None:
    prepared = {
        "module_index": 0,
        "missing_test_design_items": [{"test_design_item_index": 1}],
        "candidate_facts": [{"fact_id": "F-REAL"}],
    }
    with pytest.raises(ValueError, match="结论无效"):
        postprocess_planning_route_repair_item(
            SimpleNamespace(),
            {
                "item_input": prepared,
                "item_output": {
                    "module_index": 0,
                    "decisions": [
                        {
                            "test_design_item_index": 1,
                            "disposition": "supported",
                            "fact_ids": ["F-UNKNOWN"],
                            "reason": "引用了输入外事实。",
                        }
                    ],
                },
            },
        )

    normalized = postprocess_planning_route_repair_item(
        SimpleNamespace(),
        {
            "item_input": prepared,
            "item_output": {
                "module_index": 0,
                "decisions": [
                    {
                        "test_design_item_index": 1,
                        "disposition": "unsupported",
                        "fact_ids": [],
                        "reason": "候选事实没有描述该覆盖意图。",
                    }
                ],
            },
        },
    )
    validate_json_schema(
        instance=normalized,
        schema=PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA,
    )


def test_scope_routes_merge_redundant_primary_and_shared_module_routes() -> None:
    plan = {
        "requirement_summary": "真实需求",
        "business_modules": [
            {
                "name": "批改券与购买",
                "objective": "验证批改券获取与购买",
                "actors": ["用户"],
                "lifecycle": None,
                "test_points": _route_test_points("批改券发放"),
            }
        ],
        "coverage_focus": ["批改券购买"],
        "risks": ["权益未到账"],
    }
    prepared = prepare_planning_scope_routes(
        SimpleNamespace(),
        {
            "planning_scopes": [
                {
                    "scope_id": "EV-0026",
                    "facts": [
                        {
                            "fact_id": "F-REWARD",
                            "assertion": "活动奖励批改券由客服发放到用户账号",
                        }
                    ],
                }
            ],
            "plan": plan,
        },
    )
    merged = merge_planning_scope_routes(
        SimpleNamespace(artifacts={}),
        {
            "plan": plan,
            "prepared_items": prepared["items"],
            "route_records": [
                {
                    "item_index": 0,
                    "output": {
                        "scope_id": "EV-0026",
                        "assignments": [
                            {
                                "fact_id": "F-REWARD",
                                "module_routes": [
                                    _module_route(0),
                                    _module_route(0, relation="shared"),
                                ],
                            }
                        ],
                    },
                }
            ],
        },
    )

    assert merged["business_modules"][0]["fact_design_routes"] == [
        {"fact_id": "F-REWARD", "test_design_item_indexes": [0]}
    ]


def test_scope_routes_reject_multiple_primary_modules() -> None:
    plan = {
        "requirement_summary": "真实需求",
        "business_modules": [
            {
                "name": "模块一",
                "objective": "目标一",
                "actors": ["用户"],
                "lifecycle": None,
                "test_points": _route_test_points("流程一"),
            },
            {
                "name": "模块二",
                "objective": "目标二",
                "actors": ["用户"],
                "lifecycle": None,
                "test_points": _route_test_points("流程二"),
            },
        ],
        "coverage_focus": ["主流程"],
        "risks": ["路由错误"],
    }
    prepared = prepare_planning_scope_routes(
        SimpleNamespace(),
        {
            "planning_scopes": [
                {"scope_id": "EV-0001", "facts": [{"fact_id": "F-001"}]}
            ],
            "plan": plan,
        },
    )

    with pytest.raises(ValueError, match="只能包含一个主模块"):
        merge_planning_scope_routes(
            SimpleNamespace(artifacts={}),
            {
                "plan": plan,
                "prepared_items": prepared["items"],
                "route_records": [
                    {
                        "item_index": 0,
                        "output": {
                            "scope_id": "EV-0001",
                            "assignments": [
                                {
                                    "fact_id": "F-001",
                                    "module_routes": [
                                        _module_route(0),
                                        _module_route(1),
                                    ],
                                }
                            ],
                        },
                    }
                ],
            },
        )


def test_scope_routes_reject_missing_fact_assignment() -> None:
    plan = {
        "requirement_summary": "真实需求",
        "business_modules": [{
            "name": "入口",
            "objective": "进入功能",
            "actors": ["用户"],
            "lifecycle": None,
            "test_points": _route_test_points("入口流程"),
        }],
        "coverage_focus": ["入口"],
        "risks": ["入口不可用"],
    }
    prepared = prepare_planning_scope_routes(
        SimpleNamespace(),
        {
            "planning_scopes": [{
                "scope_id": "EV-0001",
                "facts": [{"fact_id": "F-001"}, {"fact_id": "F-002"}],
            }],
            "plan": plan,
        },
    )

    with pytest.raises(ValueError, match="必须逐条且仅路由"):
        merge_planning_scope_routes(
            SimpleNamespace(artifacts={}),
            {
                "plan": plan,
                "prepared_items": prepared["items"],
                "route_records": [{
                    "item_index": 0,
                    "output": {
                        "scope_id": "EV-0001",
                        "assignments": [{
                            "fact_id": "F-001",
                            "module_routes": [_module_route(0)],
                        }],
                    },
                }],
            },
        )


def test_source_semantics_contract_uses_compact_anchor_and_canonical_merge() -> None:
    analyst = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_source_semantics_analyst"
    )
    source_fact_schema = analyst["output_schema"]["properties"]["authoritative_facts"][
        "items"
    ]
    agent_anchors = source_fact_schema["properties"]["source_anchor"]["oneOf"]
    block_anchor = agent_anchors[0]
    assert set(block_anchor["properties"]) == {"document_id", "page_number", "block_id"}
    assert block_anchor["required"] == ["document_id", "page_number", "block_id"]
    assert block_anchor["properties"]["block_id"] == {"type": "string", "minLength": 1}
    canonical_anchor = SOURCE_SEMANTICS_OUTPUT_SCHEMA["properties"][
        "authoritative_facts"
    ]["items"]["properties"]["source_anchor"]["oneOf"][0]
    assert all("source_kind" not in anchor["properties"] for anchor in agent_anchors)
    assert canonical_anchor["properties"]["source_kind"] == {
        "type": "string",
        "const": "document",
    }
    assert "source_kind" in canonical_anchor["required"]
    assert canonical_anchor["properties"]["quote"]["type"] == "string"
    assert canonical_anchor["properties"]["block_id"]["oneOf"][1]["minItems"] == 2
    assert "禁止输出 quote、block_id 数组或 source_span" in analyst["instructions"]
    assert analyst["runtime_config"]["max_output_tokens"] == 5000
    assert "disable_server_output_schema" not in analyst["runtime_config"]
    assert analyst["runtime_config"]["tool_keys"] == ["submit_source_semantics"]
    assert analyst["runtime_config"]["stop_at_tool_keys"] == [
        "submit_source_semantics"
    ]
    assert "必须且只能调用一次 submit_source_semantics" in analyst["instructions"]
    assert analyst["version"] == 1
    assert "oneOf" not in source_fact_schema
    assert "scope_id" not in source_fact_schema["properties"]
    assert "scope_id" not in source_fact_schema["required"]
    assert "平台会根据校验后的真实来源锚点派生唯一 scope_id" in analyst["instructions"]
    assert source_fact_schema["properties"]["value_policy"]["enum"] == [
        "exact",
        "runtime_configured",
    ]
    assert source_fact_schema["properties"]["governed_value_spans"]["type"] == "array"
    assert "maxItems" not in source_fact_schema["properties"]["governed_value_spans"]

    text_analyst = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_source_text_semantics_analyst"
    )
    assert text_analyst["runtime_config"]["tool_keys"] == ["submit_source_semantics"]
    assert text_analyst["runtime_config"]["stop_at_tool_keys"] == [
        "submit_source_semantics"
    ]
    assert text_analyst["runtime_config"]["result_cache"] == {
        "version": "source-text-semantics-v2-terminal-tool",
        "accept_legacy": False,
    }
    assert "必须且只能调用一次 submit_source_semantics" in text_analyst["instructions"]
    assert "禁止使用空对象 {}" in text_analyst["instructions"]

    compact_output = {
        "authoritative_facts": [
            {
                "fact_id": "F-001",
                "assertion": "原子事实",
                "source_anchor": {
                    "document_id": 1,
                    "page_number": 1,
                    "block_id": "P0001-T0001",
                },
                "status": "effective",
                "value_policy": "exact",
                "governed_value_spans": [],
                "governed_by": [],
            }
        ]
    }
    normalized, _ = _normalize_final_output(compact_output, analyst["output_schema"])
    assert normalized["authoritative_facts"][0]["source_anchor"] == {
        "document_id": 1,
        "page_number": 1,
        "block_id": "P0001-T0001",
    }

    cross_field_policy_pair = deepcopy(compact_output)
    cross_field_policy_pair["authoritative_facts"][0]["governed_value_spans"] = [
        {"start": 0, "end": 2}
    ]
    validate_json_schema(
        instance=cross_field_policy_pair,
        schema=analyst["output_schema"],
    )

    wrapped_anchor = dict(compact_output)
    wrapped_anchor["authoritative_facts"] = [
        {
            **compact_output["authoritative_facts"][0],
            "source_anchor": {
                    "document": {
                        "document_id": 1,
                        "page_number": 1,
                        "block_id": "P0001-T0001",
                }
            },
        }
    ]
    with pytest.raises(ModelBehaviorError, match="契约校验失败"):
        _normalize_final_output(wrapped_anchor, analyst["output_schema"])

    model_declared_source_kind = dict(compact_output)
    model_declared_source_kind["authoritative_facts"] = [
        {
            **compact_output["authoritative_facts"][0],
            "source_anchor": {
                "source_kind": "document",
                "document_id": 1,
                "page_number": 1,
                "block_id": "P0001-T0001",
            },
        }
    ]
    with pytest.raises(ModelBehaviorError, match="契约校验失败"):
        _normalize_final_output(model_declared_source_kind, analyst["output_schema"])

    redundant_anchor = deepcopy(compact_output)
    redundant_anchor["authoritative_facts"][0]["source_anchor"]["quote"] = "原子事实"
    with pytest.raises(ModelBehaviorError, match="契约校验失败"):
        _normalize_final_output(redundant_anchor, analyst["output_schema"])


def test_planner_separates_business_modules_from_test_points() -> None:
    planner = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_business_planner"
    )

    planner_instructions = planner["instructions"]
    assert "必须逐批审查 draft.module_candidates" in planner_instructions
    assert "逐批校验 fact_id 无遗漏" in planner_instructions
    assert "共享同一业务目标、核心数据或生命周期的粗粒度能力域" in planner_instructions
    assert "不能仅因验证路径独立就升级为业务模块" in planner_instructions
    assert "必须逐项写入 test_points" in planner_instructions
    assert "可选范围、内容矩阵、配置枚举和数量边界" in planner_instructions
    assert "模块数量由需求语义决定" in planner_instructions
    assert "等价类" in planner_instructions
    assert "边界值" in planner_instructions
    assert "每个 coverage_group_id 必须且只能出现一次" in planner_instructions
    assert "平台会在严格校验后展开组内全部原子覆盖意图" in planner_instructions
    assert "planning_limits 对应上限" in planner_instructions
    assert "每个测试点只保留最适用的一种测试方法" in planner_instructions
    assert "必须且只能调用一次 submit_business_plan" in planner_instructions


def test_no_tool_agent_uses_single_sdk_turn_and_keeps_node_retry_boundary() -> None:
    assert _effective_max_turns({}, has_tools=False) == 1
    assert _effective_max_turns({"max_turns": 4}, has_tools=True) == 4
    with pytest.raises(ValueError, match="无工具 Agent"):
        _effective_max_turns({"max_turns": 4}, has_tools=False)


def test_dynamic_agent_can_call_specialist_before_terminal_submission() -> None:
    settings = _model_settings(
        {
            "stop_at_tool_keys": ["submit_generation_batch"],
            "force_terminal_tool_choice": False,
        }
    )

    assert settings.tool_choice == "required"
    assert _tool_use_behavior(
        {"stop_at_tool_keys": ["submit_generation_batch"]},
        available_tool_names={
            "test_generation_scenario_designer",
            "submit_generation_batch",
        },
    ) == {"stop_at_tool_names": ["submit_generation_batch"]}


def test_case_generator_uses_inline_fact_bindings_without_model_owned_indexes() -> None:
    generator = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_case_generator"
    )

    assert generator["runtime_config"]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    assert generator["runtime_config"]["disable_server_output_schema"] is True
    assert "不要输出 case_fact_bindings" in generator["instructions"]
    assert "不要输出 case_id 和 module" in generator["instructions"]
    assert "平台会根据数组位置确定性拆分绑定" in generator["instructions"]
    assert "前置条件和 expected 的 fact_ids 均禁止空数组" in generator["instructions"]
    assert "中性操作可以使用空数组" in generator["instructions"]
    assert "case_budget 是本包必须精确生成的用例数量，可以大于 1" in generator["instructions"]
    assert "preconditions 每项只能包含 text 和 fact_ids" in generator["instructions"]
    assert "steps 每项固定为 action、expected、fact_bindings 三个字段" in generator["instructions"]
    assert "authoritative_facts 仍是唯一事实源" in generator["instructions"]
    assert "coverage_slots 按数组顺序提供每条用例的初始事实负载参考" in generator[
        "instructions"
    ]
    assert "允许按业务语义在用例之间重新分配" in generator["instructions"]
    assert "测试设计项编号由平台依据事实路由确定性派生" in generator[
        "instructions"
    ]
    assert "一次性修正 validation_feedback 列出的全部违规项" in generator["instructions"]
    assert "本批最多可生成数量" not in generator["instructions"]
    assert "不超过 case_budget" not in generator["instructions"]

    assert MODEL_GROUNDING_SCHEMA["required"] == ["test_cases"]
    assert "case_fact_bindings" not in MODEL_GROUNDING_SCHEMA["properties"]
    case_schema = MODEL_GROUNDING_SCHEMA["properties"]["test_cases"]["items"]
    assert "case_id" not in case_schema["properties"]
    assert "module" not in case_schema["properties"]
    assert set(case_schema["properties"]["preconditions"]["items"]["required"]) == {
        "text",
        "fact_ids",
    }
    assert set(case_schema["properties"]["steps"]["items"]["required"]) == {
        "action",
        "expected",
        "fact_bindings",
    }
    step_properties = case_schema["properties"]["steps"]["items"]["properties"]
    assert step_properties["action"]["type"] == "string"
    assert step_properties["expected"]["type"] == "string"
    assert set(step_properties["fact_bindings"]["required"]) == {
        "action",
        "expected",
    }
    assert "minItems" not in step_properties["fact_bindings"]["properties"]["action"]
    assert step_properties["fact_bindings"]["properties"]["expected"]["minItems"] == 1

    projection = generator["runtime_config"]["input_projection"]
    assert generator["runtime_config"]["input_projection_version"] == (
        "generation-model-v5-dynamic-scenario-design"
    )
    assert "requirement" not in projection
    assert "fact_ids" not in projection["plan"]["business_module"]
    assert "test_design_item_id" in projection["plan"]["test_design_items"]
    assert "batch_number" not in projection["batch"]
    assert projection["case_fact_contract"] == {
        "required_fact_ids": True,
        "coverage_slots": {"required_fact_ids": True},
    }


def test_batch_final_reviewer_does_not_copy_fact_ids_from_model_output() -> None:
    reviewer = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_generation_final_reviewer"
    )
    difference_schema = reviewer["output_schema"]["properties"]["differences"]["items"]

    assert "related_fact_ids" not in difference_schema["properties"]
    assert "related_fact_ids" not in difference_schema["required"]
    assert "不要输出 related_fact_ids" in reviewer["instructions"]


def test_batch_repairer_can_redistribute_lifecycle_across_fixed_case_slots() -> None:
    repairer = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_generation_batch_repairer"
    )

    assert repairer["runtime_config"]["model_route"] == "main"
    assert repairer["runtime_config"]["disable_server_output_schema"] is True
    assert repairer["output_schema"] is MODEL_REPAIR_PATCH_SCHEMA
    generator = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_case_generator"
    )
    assert generator["output_schema"] is MODEL_GROUNDING_SCHEMA
    repair_patch_schema = repairer["output_schema"]["properties"]["case_patches"]["items"]
    assert "case_id" in repair_patch_schema["required"]
    assert "module" not in repair_patch_schema["properties"]
    assert "test_cases" not in repairer["output_schema"]["properties"]
    assert repairer["runtime_config"]["max_output_tokens"] == 10000
    assert "允许在 target_case_ids 之间重新分配步骤、事实和测试设计项" in repairer["instructions"]
    assert "related_fact_ids 是平台从原始绑定派生的事实指针" in repairer["instructions"]
    assert "事实保留清单是逐条核对表" in repairer["instructions"]
    assert "异步前后阶段应分配到不同 case 槽位" in repairer["instructions"]
    assert "repair_cycle 大于1" in repairer["instructions"]
    reviewer = next(
        item
        for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_generation_final_reviewer"
    )
    difference_schema = reviewer["output_schema"]["properties"]["differences"][
        "items"
    ]
    assert "repair_scope" in difference_schema["required"]
    assert difference_schema["properties"]["repair_scope"]["enum"] == [
        "case",
        "cohort",
    ]
    assert "repair_scope=cohort" in reviewer["instructions"]


def test_final_review_repair_merge_accepts_platform_noop_marker() -> None:
    merge_tool = next(
        item
        for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "merge_final_review_repairs"
    )
    repair_output_schema = merge_tool["input_schema"]["properties"]["repair_records"][
        "items"
    ]["properties"]["output"]

    assert repair_output_schema["properties"]["review_noop"] == {"type": "boolean"}
    assert "review_noop" not in MODEL_GROUNDING_SCHEMA["properties"]


def test_business_plan_batcher_preserves_conflicting_facts_for_later_authority_review() -> None:
    batcher = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_business_plan_batcher"
    )

    assert BUSINESS_PLANNING_BATCH_MAX_FACTS == 20
    assert BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS == 4500
    assert "互相冲突" in batcher["instructions"]
    assert "本节点不裁决生效性" in batcher["instructions"]


def test_business_planning_accepts_empty_actors_without_weakening_other_summaries() -> None:
    draft = {
        "batch_summary": "课程内容批次",
        "module_candidates": [
            {
                "name": "作文题目",
                "objective": "验证作文题目配置",
                "actors": [],
                "lifecycle": None,
                "coverage_topics": [
                    {"name": "题目配置", "objective": "验证题目与类型"}
                ],
                "fact_ids": ["F-001"],
            }
        ],
        "coverage_focus": "课程内容",
        "risks": "题目配置错误",
    }

    validate_json_schema(draft, BUSINESS_PLAN_DRAFT_SCHEMA)
    invalid = deepcopy(draft)
    invalid["coverage_focus"] = []
    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(invalid, BUSINESS_PLAN_DRAFT_SCHEMA)


def test_business_planning_compiles_structured_risk_details() -> None:
    structured_risk = {
        "risk_id": "R27-001",
        "description": "免费批改次数规则存在版本冲突",
        "severity": "high",
        "related_fact_ids": ["F-001", "F-002"],
    }
    draft = {
        "batch_summary": "批次摘要",
        "module_candidates": [
            {
                "name": "批改规则",
                "objective": "验证批改规则",
                "actors": [],
                "lifecycle": None,
                "coverage_topics": [{"name": "次数规则", "objective": "验证次数"}],
                "fact_ids": ["F-001"],
            }
        ],
        "coverage_focus": "批改次数规则",
        "risks": [structured_risk],
    }
    validate_json_schema(draft, BUSINESS_PLAN_DRAFT_SCHEMA)

    result = prepare_business_plan_consolidation(
        SimpleNamespace(artifacts={}),
        {
            "prepared_items": [
                {
                    "planning_scopes": [
                        {"scope_id": "EV-0001", "facts": [{"fact_id": "F-001", "assertion": "规则"}]}
                    ],
                    "case_budget": 20,
                }
            ],
            "plan_records": [{"item_index": 0, "output": draft}],
            "case_budget": 20,
        },
    )

    expected = "R27-001（级别：high）：免费批改次数规则存在版本冲突（关联事实：F-001、F-002）"
    assert _summary_item_text(structured_risk) == expected
    assert result["planning_metadata"]["risks"] == [expected]

    empty_risk_draft = deepcopy(draft)
    empty_risk_draft["risks"] = []
    validate_json_schema(empty_risk_draft, BUSINESS_PLAN_DRAFT_SCHEMA)
    empty_result = prepare_business_plan_consolidation(
        SimpleNamespace(artifacts={}),
        {
            "prepared_items": [
                {
                    "planning_scopes": [
                        {"scope_id": "EV-0001", "facts": [{"fact_id": "F-001", "assertion": "规则"}]}
                    ],
                    "case_budget": 20,
                }
            ],
            "plan_records": [{"item_index": 0, "output": empty_risk_draft}],
            "case_budget": 20,
        },
    )
    assert empty_result["planning_metadata"]["risks"] == []


def test_case_generator_rejects_detached_or_missing_step_fact_bindings() -> None:
    legacy_step = {
        "action": "点击置灰的秘籍图标",
        "action_fact_ids": ["DOC259-P0025-259-002"],
        "expected": "打开未获得提示弹窗",
    }
    output = {
        "test_cases": [
            {
                "title": "未获得技法时查看秘籍",
                "priority": "P0",
                "preconditions": [],
                "steps": [legacy_step],
                "tags": [],
                "test_design_item_ids": [],
            }
        ]
    }

    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(instance=output, schema=MODEL_GROUNDING_SCHEMA)

    output["test_cases"][0]["steps"] = [
        {
            "action": "点击置灰的秘籍图标",
            "expected": "打开未获得提示弹窗",
            "fact_bindings": {
                "action": ["DOC259-P0025-259-002"],
                "expected": ["DOC259-P0025-259-002"],
            },
        }
    ]
    validate_json_schema(instance=output, schema=MODEL_GROUNDING_SCHEMA)

    output["test_cases"][0]["steps"][0]["fact_bindings"]["action"] = []
    validate_json_schema(instance=output, schema=MODEL_GROUNDING_SCHEMA)

    output["test_cases"][0]["steps"][0]["fact_bindings"]["expected"] = []
    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(instance=output, schema=MODEL_GROUNDING_SCHEMA)


def test_server_output_schema_can_be_disabled_without_relaxing_local_validation() -> None:
    definition = SimpleNamespace(
        agent_key="reviewer",
        output_schema={
            "type": "object",
            "properties": {"approved": {"type": "boolean"}},
            "required": ["approved"],
            "additionalProperties": False,
        },
    )

    assert _sdk_output_type(definition, has_tools=False) is not None
    assert _sdk_output_type(
        definition,
        has_tools=False,
        disable_server_output_schema=True,
    ) is None
    settings = _model_settings({}, use_json_object_output=True)
    assert settings.extra_args == {"response_format": {"type": "json_object"}}
    with pytest.raises(ModelBehaviorError, match="契约校验失败"):
        _normalize_final_output('{"approved":true,"unexpected":1}', definition.output_schema)


def test_tool_agent_does_not_mix_json_response_mode_with_tool_selection() -> None:
    assert _should_use_json_object_output(
        disable_server_output_schema=True,
        has_tools=False,
    ) is True
    assert _should_use_json_object_output(
        disable_server_output_schema=True,
        has_tools=True,
    ) is False


def test_local_schema_error_includes_expected_nested_shape() -> None:
    schema = {
        "type": "object",
        "properties": {
            "links": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "relation": {"type": "string"},
                        "directive_fact_id": {"type": "string"},
                    },
                    "required": ["relation", "directive_fact_id"],
                },
            }
        },
        "required": ["links"],
    }

    with pytest.raises(StructuredOutputValidationError) as error:
        _normalize_final_output('{"links":["F-NEW"]}', schema)

    message = str(error.value)
    assert "字段=links.0" in message
    assert '"relation"' in message
    assert '"directive_fact_id"' in message


def test_local_json_parser_only_escapes_raw_control_characters_inside_strings() -> None:
    schema = {
        "type": "object",
        "properties": {"description": {"type": "string"}},
        "required": ["description"],
        "additionalProperties": False,
    }

    normalized, final_text = _normalize_final_output(
        '{"description":"第一行\n第二行\t末尾"}',
        schema,
    )

    assert normalized == {"description": "第一行\n第二行\t末尾"}
    assert "\\u000a" in final_text
    assert "\\u0009" in final_text
    assert "\n" not in final_text
    assert "\t" not in final_text


def test_local_json_parser_rejects_structural_damage_with_actionable_feedback() -> None:
    schema = {
        "type": "object",
        "properties": {"authoritative_facts": {"type": "array"}},
        "required": ["authoritative_facts"],
        "additionalProperties": False,
    }

    with pytest.raises(ModelBehaviorError) as error:
        _normalize_final_output(
            '{"hallucinated_field" [1, 2]}',
            schema,
        )

    message = str(error.value)
    assert "不是合法 JSON" in message
    assert "第 1 行第" in message
    assert error.value.diagnostic["output_chars"] == 29
    assert error.value.diagnostic["output_excerpt"] == '{"hallucinated_field" [1, 2]}'


def test_local_json_parser_marks_real_repeated_control_character_failure_as_degeneration() -> None:
    schema = {
        "type": "object",
        "properties": {
            "test_cases": {"type": "array"},
            "case_fact_bindings": {"type": "array"},
        },
        "required": ["test_cases", "case_fact_bindings"],
        "additionalProperties": False,
    }
    damaged_output = '{"test_cases":[{"title":"' + ("\t" * 40)

    with pytest.raises(StructuredOutputJSONError) as captured:
        _normalize_final_output(damaged_output, schema)

    diagnostic = captured.value.diagnostic
    assert diagnostic["is_output_degeneration"] is True
    assert diagnostic["control_character_count"] == 40
    assert diagnostic["output_sha256"]


def test_local_schema_error_exposes_repairable_candidate_without_schema_dump() -> None:
    schema = {
        "type": "object",
        "properties": {
            "test_cases": {"type": "array"},
            "case_fact_bindings": {"type": "array"},
        },
        "required": ["test_cases", "case_fact_bindings"],
        "additionalProperties": False,
    }

    with pytest.raises(StructuredOutputValidationError) as captured:
        _normalize_final_output('{"test_cases":[]}', schema)

    assert captured.value.candidate_output == {"test_cases": []}
    assert captured.value.output_schema == schema
    assert captured.value.diagnostic["missing_fields"] == ["case_fact_bindings"]
    assert "期望结构=" not in str(captured.value)


def test_local_json_parser_repairs_syntax_only_when_schema_remains_valid() -> None:
    schema = {
        "type": "object",
        "properties": {
            "authoritative_facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_id": {"type": "string"},
                        "assertion": {"type": "string"},
                    },
                    "required": ["fact_id", "assertion"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["authoritative_facts"],
        "additionalProperties": False,
    }

    normalized, final_text = _normalize_final_output(
        '{"authoritative_facts":[{"fact_id":"F-001" "assertion":"展示投稿入口"}]}',
        schema,
    )

    assert normalized == {
        "authoritative_facts": [
            {"fact_id": "F-001", "assertion": "展示投稿入口"},
        ]
    }
    assert json.loads(final_text) == normalized


def test_local_json_parser_does_not_accept_repair_that_violates_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "authoritative_facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_id": {"type": "string"},
                        "assertion": {"type": "string"},
                    },
                    "required": ["fact_id", "assertion"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["authoritative_facts"],
        "additionalProperties": False,
    }

    with pytest.raises(ModelBehaviorError, match="不是合法 JSON"):
        _normalize_final_output(
            '{"authoritative_facts":[{"fact_id":"F-001" "unknown":"越界字段"}]}',
            schema,
        )


def test_reviewers_use_structured_single_turn_outputs() -> None:
    reviewer_spec = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_generation_final_reviewer"
    )
    reviewer = AgentDefinition(
        agent_key=reviewer_spec["agent_key"],
        output_schema=reviewer_spec["output_schema"],
    )
    authority_spec = next(
        item for item in BUILTIN_AGENT_SPECS
        if item["agent_key"] == "test_authority_reconciliation_reviewer"
    )

    assert _sdk_output_type(reviewer, has_tools=False) is not None
    assert reviewer_spec["output_schema"]["required"] == [
        "approved",
        "differences",
    ]
    assert "phase 固定为 final_review" in reviewer_spec["instructions"]
    assert authority_spec["runtime_config"]["disable_server_output_schema"] is True


def test_terminal_tool_is_the_deterministic_agent_output() -> None:
    assert _tool_use_behavior(
        {"stop_at_tool_keys": ["persist_test_cases"]},
        available_tool_names={"validate_test_cases", "persist_test_cases"},
    ) == {"stop_at_tool_names": ["persist_test_cases"]}
    settings = _model_settings({"stop_at_tool_keys": ["persist_test_cases"]})
    assert settings.tool_choice == "persist_test_cases"
    assert _terminal_tool_result(
        {"stop_at_tool_keys": ["persist_test_cases"]},
        [
            {
                "tool_key": "persist_test_cases",
                "result": {"created_count": 3},
            }
        ],
    ) == {"created_count": 3}
    with pytest.raises(ModelBehaviorError, match="未调用约定的终止工具"):
        _terminal_tool_result(
            {"stop_at_tool_keys": ["persist_test_cases"]},
            [],
        )
    with pytest.raises(ModelBehaviorError, match="重复调用终止工具"):
        _terminal_tool_result(
            {"stop_at_tool_keys": ["persist_test_cases"]},
            [
                {"tool_key": "persist_test_cases", "result": {"created_count": 1}},
                {"tool_key": "persist_test_cases", "result": {"created_count": 2}},
            ],
        )
    with pytest.raises(ValueError, match="未绑定工具"):
        _tool_use_behavior(
            {"stop_at_tool_keys": ["missing_tool"]},
            available_tool_names={"persist_test_cases"},
        )


def test_terminal_tool_result_ignores_malformed_model_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = {"authoritative_facts": []}
    context = SimpleNamespace(user_id=1, tool_calls=[])
    definition = SimpleNamespace(
        agent_key="test_source_semantics_analyst",
        runtime_config={
            "input_mode": "text",
            "model_route": "vision",
            "max_turns": 1,
            "stop_at_tool_keys": ["submit_source_semantics"],
        },
        output_schema={
            "type": "object",
            "properties": {"authoritative_facts": {"type": "array"}},
            "required": ["authoritative_facts"],
            "additionalProperties": False,
        },
    )

    monkeypatch.setattr(sdk_adapter, "get_client_for_user", lambda *_: object())
    monkeypatch.setattr(
        sdk_adapter,
        "_build_sdk_agent",
        lambda **_: (SimpleNamespace(), True),
    )

    async def run_sdk_agent(**_: object) -> object:
        context.tool_calls.append(
            {
                "tool_key": "submit_source_semantics",
                "arguments": output,
                "result": output,
            }
        )
        return SimpleNamespace(
            interruptions=[],
            final_output='{"authoritative_facts": [',
            last_agent=SimpleNamespace(name="来源语义分析智能体"),
            context_wrapper=SimpleNamespace(usage=None),
        )

    monkeypatch.setattr(sdk_adapter, "_run_sdk_agent_async", run_sdk_agent)

    result = asyncio.run(
        sdk_adapter.run_agent_async(
            db=object(),
            agent_definition=definition,
            tool_definitions=[],
            execution_context=context,
            input_payload={},
        )
    )

    assert result.output == output
    assert json.loads(result.final_text) == output


def test_page_image_agent_only_allows_terminal_tools() -> None:
    runtime_config = {
        "input_mode": "document_page_optional_image",
        "model_route": "vision",
        "stop_at_tool_keys": ["submit_source_semantics"],
    }

    _validate_runtime_config(runtime_config, has_tools=True)
    with pytest.raises(ValueError, match="必须声明终止工具"):
        _validate_runtime_config(
            {
                "input_mode": "document_page_optional_image",
                "model_route": "vision",
            },
            has_tools=True,
        )


def test_document_page_tool_exposes_stable_layout_anchor_contract() -> None:
    blocks = _public_layout_blocks(
        [
            {
                "block_id": "P0001-T0001",
                "type": "text_line",
                "text": "真实需求",
                "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1},
                "source": "pdf_text",
                "source_span": {"start": 3, "end": 7},
                "font_name": "InternalFont",
                "text_runs": [{"text": "真实需求"}],
            },
            {
                "block_id": "P0001-I0001",
                "type": "image",
                "text": "",
                "bbox": {"x": 0.4, "y": 0.5, "width": 0.2, "height": 0.2},
                "source": "pdf_image",
                "asset_source_sha256": "a" * 64,
            },
        ]
    )

    assert blocks[0]["source_span"] == {"start": 3, "end": 7}
    assert "font_name" not in blocks[0]
    assert "text_runs" not in blocks[0]
    assert "source_span" not in blocks[1]


def test_sdk_hidden_retry_is_rejected() -> None:
    with pytest.raises(ValueError, match="禁止 SDK 隐式重试"):
        _validate_runtime_config({"max_retries": 1}, has_tools=False)


def test_builtin_no_tool_agents_declare_their_real_single_turn_limit() -> None:
    for spec in BUILTIN_AGENT_SPECS:
        runtime_config = spec["runtime_config"]
        if not runtime_config.get("tool_keys") and not runtime_config.get("subagent_keys"):
            assert spec["runtime_config"]["max_turns"] == 1


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


def test_function_tool_records_bounded_diagnostic_for_malformed_arguments() -> None:
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
    context = ToolExecutionContext(
        db=None,
        user_id=1,
        project_id=66,
        run_id=1,
        node_key="validate",
        run_input={},
    )
    tool = _function_tool(definition, context)
    malformed = '{"requirement" "缺少冒号' + ("内容" * 300) + '"}'

    with pytest.raises(ToolArgumentsJSONError) as captured:
        asyncio.run(tool.on_invoke_tool(None, malformed))

    diagnostic = captured.value.diagnostic
    assert diagnostic["tool_key"] == "validate_test_cases"
    assert diagnostic["arguments_chars"] == len(malformed)
    assert diagnostic["error_position"] == 15
    assert len(diagnostic["arguments_sha256"]) == 64
    assert len(diagnostic["arguments_excerpt"]) <= 240
    assert diagnostic["excerpt_truncated_after"] is True
    assert "near_error=" in str(captured.value)


def test_function_tool_records_compact_diagnostic_for_invalid_argument_schema() -> None:
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
    context = ToolExecutionContext(
        db=None,
        user_id=1,
        project_id=66,
        run_id=1,
        node_key="validate",
        run_input={},
    )
    tool = _function_tool(definition, context)

    with pytest.raises(ToolArgumentsValidationError) as captured:
        asyncio.run(tool.on_invoke_tool(None, "{}"))

    diagnostic = captured.value.diagnostic
    assert diagnostic["tool_key"] == "validate_test_cases"
    assert diagnostic["validation_keyword"] == "required"
    assert diagnostic["instance_path"] == []
    assert diagnostic["schema_path"] == ["required"]
    assert diagnostic["missing_fields"] == [
        "requirement",
        "case_budget",
        "test_cases",
    ]
    assert diagnostic["arguments_chars"] == 2
    assert diagnostic["arguments_excerpt"] == "{}"
    assert diagnostic["excerpt_truncated_after"] is False
    assert "Failed validating" not in str(captured.value)

    with pytest.raises(ToolArgumentsValidationError) as non_object:
        asyncio.run(tool.on_invoke_tool(None, "[]"))

    assert non_object.value.diagnostic["validation_keyword"] == "type"
    assert non_object.value.diagnostic["arguments_excerpt"] == "[]"


def test_tool_argument_normalizer_decodes_only_schema_declared_structures() -> None:
    value = {
        "test_cases": '[{"title":"真实用例"}]',
        "note": '[{"title":"保持字符串"}]',
    }
    normalized = _normalize_json_encoded_schema_values(
        value,
        {
            "type": "object",
            "properties": {
                "test_cases": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "note": {"type": "string"},
            },
        },
    )

    assert normalized["test_cases"] == [{"title": "真实用例"}]
    assert normalized["note"] == value["note"]


def test_tool_argument_normalizer_repairs_json_encoded_structure_syntax() -> None:
    normalized = _normalize_json_encoded_schema_values(
        {
            "test_cases": (
                '[{"title":"复制全文","expected":"弹出toast提示"复制成功""}]'
            )
        },
        {
            "type": "object",
            "properties": {
                "test_cases": {
                    "type": "array",
                    "items": {"type": "object"},
                }
            },
        },
    )

    assert normalized["test_cases"] == [
        {"title": "复制全文", "expected": '弹出toast提示"复制成功"'}
    ]


def test_function_tool_rejects_invalid_handler_output_as_platform_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler_key = "testing.contract_output_probe"
    calls: list[dict[str, object]] = []

    def invalid_output_handler(
        _context: ToolExecutionContext,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        calls.append(arguments)
        return {}

    monkeypatch.setitem(tool_registry._handlers, handler_key, invalid_output_handler)
    definition = AgentToolDefinition(
        project_id=66,
        user_id=1,
        tool_key="contract_output_probe",
        name="输出契约探针",
        description="验证工具处理器输出契约边界",
        handler_key=handler_key,
        input_schema={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["normalized"],
            "properties": {"normalized": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    context = ToolExecutionContext(
        db=None,
        user_id=1,
        project_id=66,
        run_id=1,
        node_key="contract_probe",
        run_input={},
    )
    tool = _function_tool(definition, context)

    with pytest.raises(ToolOutputValidationError) as captured:
        asyncio.run(tool.on_invoke_tool(None, '{"value":"真实值"}'))

    assert calls == [{"value": "真实值"}]
    assert context.executed_tools == []
    assert context.tool_calls == []
    diagnostic = captured.value.diagnostic
    assert diagnostic["tool_key"] == "contract_output_probe"
    assert diagnostic["validation_keyword"] == "required"
    assert diagnostic["output_path"] == []
    assert diagnostic["schema_path"] == ["required"]
    assert diagnostic["missing_fields"] == ["normalized"]
    assert diagnostic["output_type"] == "dict"
    assert diagnostic["output_chars"] == 2
    assert diagnostic["output_excerpt"] == "{}"
    assert diagnostic["excerpt_truncated_after"] is False
    assert len(diagnostic["output_sha256"]) == 64
    assert "Failed validating" not in str(captured.value)
