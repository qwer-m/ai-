from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.exceptions import ModelBehaviorError
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from jsonschema import validate as validate_json_schema
from pydantic import ValidationError

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentDefinition,
    AgentNodeRun,
    AgentRun,
    AgentToolDefinition,
    AgentWorkflowDefinition,
    KnowledgeDocument,
)
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
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.runtime import _node_input
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sources import historical_source_snapshot
from modules.agent_platform.serialization import serialize_node_run, serialize_run_summary
from modules.agent_platform.service import _initial_run_context, _resolved_execution_limits
from modules.agent_platform.service import _restorable_checkpoints, _restored_checkpoint_sdk_state
from modules.agent_platform.sdk_adapter import (
    StructuredOutputJSONError,
    StructuredOutputValidationError,
    ToolArgumentsJSONError,
    ToolArgumentsValidationError,
    ToolOutputValidationError,
    effective_max_turns,
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
from modules.agent_platform.test_generation_schemas import (
    AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA,
    AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA,
    BUSINESS_PLANNING_BATCH_MAX_FACTS,
    BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS,
    BUSINESS_PLAN_DRAFT_SCHEMA,
    BUSINESS_MODULE_OWNERSHIP_AGENT_OUTPUT_SCHEMA,
    FINAL_REVIEW_REPAIR_INPUT_SCHEMA,
    FINAL_REVIEW_REPAIR_RESULT_SCHEMA,
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
    MERGED_GENERATION_SCHEMA,
)
from modules.agent_platform.test_generation_planning import (
    merge_planning_scope_routes,
    postprocess_planning_support_decisions,
    postprocess_planning_scope_routing_item,
    prepare_business_plan_consolidation,
    prepare_planning_route_repairs,
    prepare_planning_scope_routes,
    _summary_item_text,
    _business_planning_limits,
)
from modules.agent_platform.test_generation_workflow import (
    validate_scenario_design_guidance,
)


def test_execution_definition_distinguishes_dag_and_agent_network() -> None:
    dag = parse_execution_definition(
        {
            "execution_mode": "dag",
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

    assert isinstance(dag, WorkflowGraph)
    assert dag.execution_mode == "dag"
    assert isinstance(agent_network, AgentProgramDefinition)
    assert agent_network.required_artifact_key == "test_generation"

    with pytest.raises(ValidationError, match="union_tag_invalid"):
        parse_execution_definition(
            {
                "execution_mode": "unknown",
                "nodes": dag.model_dump()["nodes"],
                "output_node_key": "generate",
            }
        )


def test_dag_can_wrap_dynamic_agent_between_fixed_nodes() -> None:
    graph = WorkflowGraph.model_validate(
        {
            "execution_mode": "dag",
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
    repository = AgentRunRepository(db)
    repository._definitions = SimpleNamespace(
        list_workflow_definition_ids=lambda **_: [11],
    )  # type: ignore[assignment]

    assert repository.get_active_run(
        project_id=2, user_id=1, workflow_key="test_generation",
    ) is running

    db.query = lambda _model: Query([pending_new, pending_old])
    assert repository.get_active_run(
        project_id=2, user_id=1, workflow_key="test_generation",
    ) is pending_old


def test_active_run_query_can_scope_to_workflow_definition() -> None:
    class Query:
        def __init__(self, rows: list[SimpleNamespace]) -> None:
            self.rows = rows

        def filter(self, *args: object) -> "Query":
            return self

        def options(self, *args: object) -> "Query":
            return self

        def all(self) -> list[SimpleNamespace]:
            return self.rows

    class Definitions:
        def list_workflow_definition_ids(self, *, project_id: int, workflow_key: str) -> list[int]:
            assert (project_id, workflow_key) == (2, "test_generation")
            return [11]

    scoped = SimpleNamespace(id=7, status="running", workflow_definition_id=11)
    db = SimpleNamespace(query=lambda _model: Query([scoped]))
    repository = AgentRunRepository(db)
    repository._definitions = Definitions()  # type: ignore[assignment]

    assert repository.get_active_run(
        project_id=2,
        user_id=1,
        workflow_key="test_generation",
    ) is scoped




def test_document_run_without_snapshot_cannot_infer_historical_source() -> None:
    assert historical_source_snapshot({"requirement_doc_id": 300}) is None






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


def test_run_summary_does_not_copy_loaded_runtime_context() -> None:
    class Context(dict):
        def __iter__(self):
            raise AssertionError("摘要不应遍历完整运行上下文")

    run = SimpleNamespace(
        id=33,
        project_id=2,
        workflow_definition_id=1,
        status="running",
        current_node_key="plan",
        input_payload={},
        run_context=Context(run_attempt=4),
        output_payload={"large": "x" * 1000},
        error_message="",
        parent_run_id=None,
        task_id="task-33",
        created_at=None,
        started_at=None,
        finished_at=None,
    )

    summary = serialize_run_summary(run)

    assert summary["run_attempt"] == 4
    assert summary["run_context"] == {}


def test_run_summary_uses_scalar_identity_projection_without_loading_context() -> None:
    run = SimpleNamespace(
        id=35,
        project_id=2,
        workflow_definition_id=1,
        status="running",
        current_node_key="plan",
        input_payload={},
        output_payload={},
        error_message="",
        parent_run_id=None,
        task_id="task-35",
        created_at=None,
        started_at=None,
        finished_at=None,
        _summary_context={
            "run_attempt": 3,
            "input_hash": "input-hash",
            "logical_task_id": "logical-35",
            "attempt_id": "logical-35/attempt-3",
            "checkpoint_schema_version": 1,
            "execution_contract": {"hash": "contract-hash"},
        },
    )

    summary = serialize_run_summary(run)

    assert summary["run_attempt"] == 3
    assert summary["input_hash"] == "input-hash"
    assert summary["logical_task_id"] == "logical-35"
    assert summary["attempt_id"] == "logical-35/attempt-3"
    assert summary["checkpoint_schema_version"] == 1
    assert summary["execution_contract_hash"] == "contract-hash"
    assert summary["run_context"] == {}


def test_owned_run_summary_projects_only_small_columns() -> None:
    queried: list[tuple[object, ...]] = []

    class Query:
        def options(self, *args: object) -> "Query":
            queried.append(args)
            return self

        def filter(self, *args: object) -> "Query":
            return self

        def first(self) -> SimpleNamespace:
            return SimpleNamespace(id=34, status="running")

    db = SimpleNamespace(query=lambda _model: Query())
    repository = AgentRunRepository(db)

    result = repository.get_owned_run_summary(run_id=34, user_id=2)

    assert result.id == 34
    assert queried


def test_owned_run_metrics_projects_context_without_run_payload() -> None:
    queried: list[tuple[object, ...]] = []

    class Query:
        def options(self, *args: object) -> "Query":
            queried.append(args)
            return self

        def filter(self, *args: object) -> "Query":
            return self

        def first(self) -> SimpleNamespace:
            return SimpleNamespace(id=35, status="success", run_context={"artifacts": {}})

    db = SimpleNamespace(query=lambda _model: Query())
    repository = AgentRunRepository(db)

    result = repository.get_owned_run_metrics(run_id=35, user_id=2)

    assert result.id == 35
    assert queried


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


def _support_records_for_routes(
    plan: dict,
    prepared_items: list[dict],
    route_records: list[dict],
    *,
    all_candidate_facts: bool = False,
) -> list[dict]:
    """为当前 v11 规划路由构造逐意图支持复核回执。"""

    repair_input = prepare_planning_route_repairs(
        SimpleNamespace(artifacts={}),
        {
            "plan": plan,
            "prepared_items": prepared_items,
            "route_records": route_records,
        },
    )
    records = []
    for item_index, item in enumerate(repair_input["items"]):
        candidate_ids = [fact["fact_id"] for fact in item["candidate_facts"]]
        fact_ids = candidate_ids if all_candidate_facts else candidate_ids[:1]
        output = {
            "module_index": item["module_index"],
            "decisions": [
                {
                    "test_design_item_index": design["test_design_item_index"],
                    "fact_ids": fact_ids,
                    "reason": "真实候选事实支持当前覆盖意图。",
                    "support_gaps": [],
                }
                for design in item["missing_test_design_items"]
            ],
        }
        normalized = postprocess_planning_support_decisions(
            SimpleNamespace(),
            {"item_input": item, "item_output": output},
        )
        records.append({
            "item_index": item_index,
            "input_hash": action_state_hash(item),
            "output": normalized,
        })
    return records


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
        asyncio.run(sdk_adapter._run_sdk_agent_async(
            agent=SimpleNamespace(),
            runner_input="{}",
            execution_context=ToolExecutionContext(db=None, user_id=0, project_id=0, run_id=0,
                                                   node_key="timeout_lifecycle", run_input={}),
            max_turns=1,
            run_config=SimpleNamespace(),
            request_timeout_seconds=0.01,
        ))

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
                execution_context=ToolExecutionContext(db=None, user_id=1, project_id=0, run_id=0,
                                                       node_key="client_lifecycle", run_input={}),
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
        action_journal=None,
        action_state={},
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

    result = asyncio.run(sdk_adapter._run_sdk_agent_async(
        agent=SimpleNamespace(),
        runner_input="{}",
        execution_context=ToolExecutionContext(db=None, user_id=0, project_id=0, run_id=0,
                                               node_key="timeout_lifecycle", run_input={}),
        max_turns=1,
        run_config=SimpleNamespace(),
        request_timeout_seconds=1,
    ))

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
    from modules.agent_platform.sdk_adapter import _model_settings

    from modules.agent_platform.test_generation_escalation import (
        GENERATION_SCOPE_FEEDBACK_SCHEMA,
        GENERATION_SCOPE_FEEDBACK_OUTPUT_SCHEMA,
    )

    agents = {str(item["agent_key"]): item for item in BUILTIN_AGENT_SPECS}
    tools = {str(item["tool_key"]): item for item in BUILTIN_TOOL_SPECS}
    agent_keys, tool_keys = set(agents), set(tools)
    assert len(agents) == len(BUILTIN_AGENT_SPECS)
    assert len(tools) == len(BUILTIN_TOOL_SPECS)
    assert {
        "test_generation_coordinator", "test_generation_final_reviewer",
        "test_generation_batch_repairer", "test_generation_global_reviewer",
        "test_generation_scenario_designer",
    } <= agent_keys
    assert {
        "test_generation_planning_coordinator", "test_generation_review_coordinator",
        "test_plan_evidence_routing_reviewer", "test_evidence_continuity_auditor",
    }.isdisjoint(agent_keys)

    generation_agent = agents["test_case_generator"]
    generation_config = generation_agent["runtime_config"]
    assert generation_config["model_route"] == "review"
    assert generation_config["max_turns"] == 6
    assert generation_config["validate_before_submit"] is True
    assert generation_config["subagent_keys"] == ["test_generation_scenario_designer"]
    assert generation_config["tool_keys"] == [
        "submit_generation_batch", "report_generation_scope_problem",
    ]
    assert generation_config["stop_at_tool_keys"] == generation_config["tool_keys"]
    assert generation_config["force_terminal_tool_choice"] is False
    assert generation_agent["output_schema"] is MODEL_GROUNDING_SCHEMA
    assert tools["submit_generation_batch"]["input_schema"] is MODEL_GROUNDING_SCHEMA
    assert tools["report_generation_scope_problem"]["input_schema"] is GENERATION_SCOPE_FEEDBACK_SCHEMA
    assert tools["report_generation_scope_problem"]["output_schema"] is GENERATION_SCOPE_FEEDBACK_OUTPUT_SCHEMA
    assert generation_config["input_projection"]["case_fact_contract"] == {
        "required_fact_ids": True,
        "available_fact_ids": True,
        "design_support_requirements": True,
    }
    assert generation_config["input_projection"]["_planning_feedback"] is True
    assert "报告不是新增事实、已批准结论或增加预算的请求授权" in generation_agent["instructions"]
    assert "一次候选超量、遗漏事实、绑定错误或结构错误本身不是范围矛盾" in generation_agent["instructions"]
    assert "禁止使用 json.dumps 或其他方式将数组二次序列化为字符串" in generation_agent["instructions"]
    assert "repair_targets" in generation_agent["instructions"]
    assert "P0 仅用于" in generation_agent["instructions"]
    assert tool_registry.resolve(tools["report_generation_scope_problem"]["handler_key"])

    scenario_config = agents["test_generation_scenario_designer"]["runtime_config"]
    assert scenario_config["model_route"] == "main"
    assert scenario_config["max_turns"] == 1
    assert scenario_config["tool_keys"] == ["submit_scenario_design_guidance"]
    assert scenario_config["stop_at_tool_keys"] == ["submit_scenario_design_guidance"]
    business_planner = agents["test_business_planner"]
    assert business_planner["runtime_config"]["tool_keys"] == ["submit_business_plan"]
    assert business_planner["runtime_config"]["stop_at_tool_keys"] == ["submit_business_plan"]
    assert business_planner["runtime_config"]["validate_before_submit"] is True
    assert "只有完整验收通过才完成本次规划" in business_planner["instructions"]
    assert agents["test_planning_route_gap_reviewer"]["runtime_config"]["result_cache"] == {"enabled": True}

    generation_spec = next(item for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] == "test_generation")
    generation_workflow = parse_execution_definition(generation_spec["definition"])
    assert isinstance(generation_workflow, AgentProgramDefinition)
    assert generation_workflow.input_schema["properties"]["disable_result_cache"] == {"type": "boolean"}
    assert generation_spec["version"] == 3
    assert generation_workflow.entry_agent_key == "test_generation_coordinator"
    assert generation_workflow.max_attempts == 2
    assert generation_workflow.time_budget_seconds == 7200
    assert generation_workflow.required_artifact_key == "test_generation"
    assert "nodes" not in generation_spec["definition"]
    assert "display_stages" not in generation_spec["definition"]
    assert "work_assignments" not in SOURCE_SEMANTICS_INPUT_SCHEMA["properties"]
    assert tool_registry.is_parallel_safe("testing.submit_source_semantics") is True
    for agent_key in ("test_source_text_semantics_analyst", "test_source_semantics_analyst"):
        config = agents[agent_key]["runtime_config"]
        assert config["max_turns"] == 1
        assert config["tool_keys"] == config["stop_at_tool_keys"] == ["submit_source_semantics"]

    coordinator_config = agents["test_generation_coordinator"]["runtime_config"]
    action_keys = {
        "analyze_source", "govern_source", "draft_source_plan",
        "retrieve_context", "read_planning_evidence", "reinspect_planning_source", "revise_planning_draft",
        "rebuild_business_plan", "route_planning_scopes", "review_planning_route_gaps",
        "accept_generation_plan", "reconcile_generation_authority", "generate_cases",
        "review_generated_cases", "revise_generation_layout", "inspect_generation_scope",
        "read_review_evidence", "repair_review_batch", "recheck_review_batch",
        "review_all_cases", "finish_generation",
    }
    assert set(coordinator_config["tool_keys"]) == action_keys
    assert len(coordinator_config["tool_keys"]) == 21
    assert coordinator_config["stop_at_tool_keys"] == ["finish_generation"]
    assert coordinator_config["validate_before_submit"] is True
    assert coordinator_config["state_initializer"] == "testing.initialize_generation_state"
    assert coordinator_config["state_observer"] == "testing.observe_generation_state"
    assert coordinator_config["output_postprocessor"] == "testing.validate_generation_resolution"
    assert coordinator_config["max_turns"] == 160
    # 协调者需要完整提交首次布局，输出空间与当前结构化工具协议一致。
    assert coordinator_config["max_output_tokens"] == 12000
    assert coordinator_config["request_timeout_seconds"] == 3600
    assert set(coordinator_config["managed_agent_keys"]) == {
        "test_source_text_semantics_analyst", "test_source_semantics_analyst",
        "test_business_plan_batcher", "test_business_planner",
        "test_planning_scope_router", "test_planning_route_gap_reviewer",
        "test_authority_reconciliation_reviewer", "test_case_generator",
        "test_generation_batch_repairer", "test_generation_final_reviewer", "test_generation_global_reviewer",
    }
    for key in action_keys:
        expected_handler = "agent.context.retrieve" if key == "retrieve_context" else f"testing.generation.{key}"
        assert tools[key]["handler_key"] == expected_handler
        assert tool_registry.is_state_only(tools[key]["handler_key"])
    assert coordinator_config["managed_artifacts_provider"] == "testing.managed_generation_artifacts"
    for key in ("state_initializer", "state_observer", "managed_artifacts_provider", "output_postprocessor"):
        assert tool_registry.resolve(coordinator_config[key])
    for key in (
        "test_planning_scope_router", "test_planning_route_gap_reviewer",
        "test_authority_reconciliation_reviewer", "test_generation_final_reviewer",
        "test_generation_global_reviewer", "test_generation_batch_repairer",
    ):
        config = agents[key]["runtime_config"]
        assert config["tool_keys"] == []
        assert config["validate_before_submit"] is True
        assert config["stop_at_tool_keys"] == ["submit_result"]
        assert _model_settings(config).tool_choice == "required"
        assert effective_max_turns(config, has_tools=False) == 6

    # 被统一状态行动取代的阶段不再作为另一套公开工具或固定节点保留。
    assert {
        "finish_planning", "finish_review", "prepare_authority_reconciliation",
        "merge_authority_reconciliation", "prepare_test_case_batches",
        "merge_grounded_generation_batches", "build_generation_audit_summary", "prepare_final_review_batches",
        "prepare_final_review_repairs", "merge_final_review_recheck_records",
        "merge_final_review_repairs", "prepare_final_review_rechecks",
        "merge_final_review_batches", "prepare_global_final_review",
        "prepare_terminal_final_review_repairs", "prepare_business_plan_consolidation",
        "prepare_planning_scope_routes", "prepare_planning_route_repairs", "merge_planning_scope_routes",
    }.isdisjoint(tool_keys)
    assert {
        "resolve_requirement_evidence", "prepare_source_semantics", "merge_source_semantics",
        "prepare_source_authority", "merge_source_authority", "prepare_business_plan_batches",
        "merge_business_plan_batches", "approve_synthesized_test_cases", "validate_test_cases",
        "build_test_execution_chain_context", "build_test_execution_chains", "persist_test_cases",
    }.isdisjoint(tool_keys)

    for spec in BUILTIN_WORKFLOW_SPECS:
        execution = parse_execution_definition(spec["definition"])
        if isinstance(execution, AgentProgramDefinition):
            assert execution.entry_agent_key in agent_keys
            continue
        assert execution.execution_order()
        for node in execution.nodes:
            allowed = agent_keys if node.node_type in {"agent", "agent_network", "agent_map"} else tool_keys
            assert node.reference_key in allowed
            if node.node_type == "tool":
                required_inputs = set(tools[node.reference_key]["input_schema"].get("required", []))
                assert required_inputs <= set(node.input_mapping), (
                    f"工作流 {spec['workflow_key']} 的节点 {node.node_key} "
                    f"缺少必填工具输入映射: {sorted(required_inputs - set(node.input_mapping))}"
                )
    for agent_spec in BUILTIN_AGENT_SPECS:
        config = agent_spec["runtime_config"]
        for key in ("subagent_keys", "managed_agent_keys"):
            assert set(config.get(key, [])) <= agent_keys
            assert len(config.get(key, [])) == len(set(config.get(key, [])))
        for key in config.get("tool_keys", []):
            assert key in tool_keys
            assert tool_registry.resolve(tools[key]["handler_key"])



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
    global_review_tool = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "review_all_cases"
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

    assert global_review_tool["input_schema"] == {
        "type": "object", "properties": {}, "additionalProperties": False,
    }
    assert tool_registry.is_state_only("testing.generation.review_all_cases") is True
    assert global_agent["runtime_config"]["output_postprocessor"] == (
        "testing.postprocess_global_final_review_output"
    )


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


def test_retry_checkpoints_keep_real_dag_success_nodes_and_partial_agent_map() -> None:
    snapshot = json.loads(
        (Path(__file__).parent / "fixtures" / "run48.presentation.json").read_text(encoding="utf-8")
    )
    execution = parse_execution_definition(snapshot["run"]["run_context"]["execution_contract"]["workflow"]["definition"])
    assert isinstance(execution, WorkflowGraph)
    nodes = {item["node_key"]: item for item in snapshot["nodes"]}
    evidence = AgentNodeRun(**deepcopy(nodes["evidence"]))
    source_text = AgentNodeRun(**deepcopy(nodes["source_text"]))
    assert evidence.status == source_text.status == "success"

    # 仅在真实记录副本注入任务中断，保留原运行已验收的第一条真实结果，不调用模型或写库。
    source_text.status = "failed"
    checkpoints = _restorable_checkpoints(execution=execution, node_runs=[evidence, source_text])
    assert [item.node_run.node_key for item in checkpoints] == ["evidence", "source_text"]
    assert checkpoints[1].has_partial_map_output is True

    # 没有任何已完成结果的失败 map 不可恢复，不能只凭节点类型认定已有进度。
    source_text.output_payload = {"items": []}
    checkpoints = _restorable_checkpoints(execution=execution, node_runs=[evidence, source_text])
    assert [item.node_run.node_key for item in checkpoints] == ["evidence"]


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


def test_execution_limits_are_clamped_to_runtime_ceiling(
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
    assert planner["version"] == 5
    assert planner["runtime_config"]["max_turns"] == 6
    assert planner["runtime_config"]["request_timeout_seconds"] == 360
    assert planner["runtime_config"]["model_route"] == "review"
    assert planner["runtime_config"]["max_output_tokens"] == 12000
    assert planner["runtime_config"]["result_cache"] == {
        "enabled": True,
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
    assert scope_router["version"] == 3
    assert scope_router["runtime_config"]["result_cache"] == {
        "enabled": True,
    }
    assert scope_router["output_schema"] is BUSINESS_MODULE_OWNERSHIP_AGENT_OUTPUT_SCHEMA
    route_assignment_schema = scope_router["output_schema"]["properties"][
        "routes"
    ]["items"]["properties"]["assignments"]["items"]
    assert "reason" not in route_assignment_schema["properties"]
    assert "reason" not in route_assignment_schema["required"]
    assert "fact_id" in route_assignment_schema["properties"]
    assert "fact_ref" not in route_assignment_schema["properties"]
    route_schema = route_assignment_schema["properties"]["module_routes"]["items"]
    assert set(route_schema["properties"]) == {"module_id", "relation"}
    assert "覆盖意图、测试点及测试技法不定义业务模块" in scope_router["instructions"]
    assert "不要输出测试设计映射" in scope_router["instructions"]
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
    assert "prepare_test_case_batches" not in {
        item["tool_key"] for item in BUILTIN_TOOL_SPECS
    }
    route_gap_reviewer = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "review_planning_route_gaps"
    )
    assert route_gap_reviewer["handler_key"] == "testing.generation.review_planning_route_gaps"


# 规划批次的真实事实、身份与无损意图由 test_planning_business_inputs_real_data.py 验证。
def test_business_planning_limits_follow_complete_validated_catalog() -> None:
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


# 规划候选与来源边界由 verify_business_plan_feedback_real_data.py 回放真实 Run 28/29；
# 单元测试只保留具备正式覆盖组目录和来源绑定的当前输入。
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
    repair_records = _support_records_for_routes(
        plan,
        prepared["batch_items"],
        batch_records,
        all_candidate_facts=True,
    )
    merged = merge_planning_scope_routes(
        context,
        {
            "plan": plan,
            "prepared_items": prepared["batch_items"],
            "route_records": batch_records,
            "repair_records": repair_records,
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
        "initial_gap_module_count": 2,
        "initial_gap_design_item_count": 2,
        "route_repair_record_count": 2,
        "repaired_design_item_count": 2,
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
    route_records = [{"item_index": 0, "output": output}]
    repair_records = _support_records_for_routes(plan, prepared["items"], route_records)
    merged = merge_planning_scope_routes(
        context,
        {
            "plan": plan,
            "prepared_items": prepared["items"],
            "route_records": route_records,
            "repair_records": repair_records,
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


def test_scope_routes_reject_incomplete_support_review_records() -> None:
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

    with pytest.raises(ValueError, match="完整意图支持复核记录数量不一致"):
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
        SimpleNamespace(artifacts={}),
        {
            "plan": plan,
            "prepared_items": prepared["items"],
            "route_records": route_records,
        },
    )

    assert repair_input["gap_module_count"] == 1
    assert repair_input["gap_design_item_count"] == 2
    assert repair_input["items"][0]["missing_test_design_items"] == [
        {
            "test_design_item_index": 0,
            "test_point": "完成反馈",
            "technique": "状态迁移",
            "coverage_intent": "完成后点亮",
        },
        {
            "test_design_item_index": 1,
            "test_point": "完成反馈",
            "technique": "状态迁移",
            "coverage_intent": "弹窗展示详情",
        },
    ]
    assert repair_input["items"][0]["candidate_facts"][0]["fact_id"] == "F-DETAIL"
    repair_output = postprocess_planning_support_decisions(
        SimpleNamespace(),
        {
            "item_input": repair_input["items"][0],
            "item_output": {
                "module_index": 0,
                "decisions": [
                    {
                        "test_design_item_index": 0,
                        "fact_ids": ["F-DETAIL"],
                        "reason": "事实明确要求完成后图标点亮。",
                        "support_gaps": [],
                    },
                    {
                        "test_design_item_index": 1,
                        "fact_ids": ["F-DETAIL"],
                        "reason": "事实明确要求完成后打开包含说明的详情弹窗。",
                        "support_gaps": [],
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
            "repair_records": [{
                "item_index": 0,
                "input_hash": action_state_hash(repair_input["items"][0]),
                "output": repair_output,
            }],
        },
    )

    assert merged["business_modules"][0]["fact_design_routes"] == [
        {"fact_id": "F-DETAIL", "test_design_item_indexes": [0, 1]}
    ]
    assert context.artifacts["planning_fact_routes"]["initial_gap_design_item_count"] == 2
    assert context.artifacts["planning_fact_routes"]["repaired_design_item_count"] == 2


def test_scope_route_gap_review_rejects_unknown_fact_and_unsupported_gap() -> None:
    prepared = {
        "module_index": 0,
        "missing_test_design_items": [{
            "test_design_item_index": 1,
            "coverage_intent": "验证结果",
        }],
        "candidate_facts": [{"fact_id": "F-REAL"}],
    }
    with pytest.raises(ValueError, match="结论无效"):
        postprocess_planning_support_decisions(
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

    normalized = postprocess_planning_support_decisions(
        SimpleNamespace(),
        {
            "item_input": prepared,
            "item_output": {
                "module_index": 0,
                "decisions": [
                    {
                        "test_design_item_index": 1,
                        "fact_ids": [],
                        "reason": "候选事实没有描述该覆盖意图。",
                        "support_gaps": [{
                            "coverage_fragment": "验证结果",
                            "kind": "missing_support",
                            "evidence_fact_ids": ["F-REAL"],
                            "reason": "候选事实没有描述该覆盖意图。",
                        }],
                    }
                ],
            },
        },
    )
    validate_json_schema(
        instance=normalized,
        schema=PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA,
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
    route_records = [
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
    ]
    repair_records = _support_records_for_routes(plan, prepared["items"], route_records)
    merged = merge_planning_scope_routes(
        SimpleNamespace(artifacts={}),
        {
            "plan": plan,
            "prepared_items": prepared["items"],
            "route_records": route_records,
            "repair_records": repair_records,
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
    assert set(block_anchor["properties"]) == {
        "document_id", "page_number", "block_id", "table_cell",
    }
    assert block_anchor["required"] == [
        "document_id", "page_number", "block_id", "table_cell",
    ]
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
    assert "table_cell" in analyst["instructions"]
    assert analyst["runtime_config"]["max_output_tokens"] == 15000
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
        "enabled": True,
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
                    "table_cell": None,
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
        "table_cell": None,
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
    assert "反馈的冲突位置" in planner_instructions
    assert "只有完整验收通过才完成本次规划" in planner_instructions


def test_no_tool_agent_uses_single_sdk_turn_and_keeps_node_retry_boundary() -> None:
    assert effective_max_turns({}, has_tools=False) == 1
    assert effective_max_turns({"max_turns": 4}, has_tools=True) == 4
    with pytest.raises(ValueError, match="无工具 Agent"):
        effective_max_turns({"max_turns": 4}, has_tools=False)


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

    assert "thinking" not in generator["runtime_config"].get("extra_body", {})
    assert generator["runtime_config"]["disable_server_output_schema"] is True
    assert "不要输出 case_fact_bindings" in generator["instructions"]
    assert "不要输出 case_id 和 module" in generator["instructions"]
    assert "平台会根据数组位置确定性拆分绑定" in generator["instructions"]
    assert "前置条件和 expected 的 fact_ids 均禁止空数组" in generator["instructions"]
    assert "非空 test_input 必须绑定至少一个支持其输入的事实" in generator["instructions"]
    assert 'test_input 必须为 {"text":"","fact_ids":[]}' in generator["instructions"]
    assert "中性操作可以使用空数组" in generator["instructions"]
    assert "case_budget 是本包必须精确生成的用例数量，可以大于 1" in generator["instructions"]
    assert "preconditions 每项只能包含 text 和 fact_ids" in generator["instructions"]
    assert "steps 每项固定为 action、expected、fact_bindings 三个字段" in generator["instructions"]
    assert "authoritative_facts 仍是唯一事实源" in generator["instructions"]
    assert "coverage_slots" not in generator["instructions"]
    assert "每条用例的事实组合必须依据真实条件、角色和生命周期确定" in generator["instructions"]
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
        "generation-model-v11-design-support"
    )
    assert "requirement" not in projection
    assert "fact_ids" not in projection["plan"]["business_module"]
    assert "test_design_item_id" in projection["plan"]["test_design_items"]
    assert "batch_number" not in projection["batch"]
    assert projection["case_fact_contract"] == {
        "required_fact_ids": True,
        "available_fact_ids": True,
        "design_support_requirements": True,
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
    assert "结构重组" in repairer["instructions"]
    assert "required_fact_ids" in repairer["instructions"]
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


def test_final_review_repair_result_keeps_noop_marker_out_of_model_contract() -> None:
    assert FINAL_REVIEW_REPAIR_RESULT_SCHEMA["properties"]["review_noop"] == {"type": "boolean"}
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
    missing_fact_bindings_step = {
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
                "test_input": {
                    "text": "技法状态=未获得",
                    "fact_ids": ["DOC259-P0025-259-002"],
                },
                "steps": [missing_fact_bindings_step],
                "tags": [],
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


def test_local_json_parser_rejects_structural_syntax_even_when_repair_could_match_schema() -> None:
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

    with pytest.raises(StructuredOutputJSONError) as captured:
        _normalize_final_output(
            '{"authoritative_facts":[{"fact_id":"F-001" "assertion":"展示投稿入口"}]}',
            schema,
        )

    assert captured.value.diagnostic["error_kind"] == "invalid_json_syntax"


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
    context = SimpleNamespace(
        user_id=1,
        tool_calls=[],
        action_journal=None,
        action_state={},
    )
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
        generated_submission = (runtime_config.get("validate_before_submit")
                                and "submit_result" in runtime_config.get("stop_at_tool_keys", []))
        if not runtime_config.get("tool_keys") and not runtime_config.get("subagent_keys") and not generated_submission:
            assert spec["runtime_config"]["max_turns"] == 1


def test_workflow_rejects_cycles() -> None:
    with pytest.raises(ValidationError, match="循环依赖"):
        WorkflowGraph.model_validate(
            {
                "execution_mode": "dag",
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


@pytest.mark.parametrize("error_type", [TimeoutError, ConnectionError])
@pytest.mark.parametrize("return_subtree_failures", [False, True])
def test_invoke_agents_drains_independent_tasks_before_raising_transport_failure(
    error_type, return_subtree_failures: bool,
) -> None:
    """只测试调度器的真实协程与异常，不构造模型或业务结果。"""

    async def run_batch() -> None:
        context = ToolExecutionContext(
            db=None, user_id=1, project_id=9, run_id=42,
            node_key="resolve_generation", run_input={}, agent_concurrency=2,
        )
        sibling_started = asyncio.Event()
        failure_raised = asyncio.Event()
        completed = []
        transport_failure = error_type("连接失败")

        async def invoke_agent(**kwargs):
            index = kwargs["input_payload"]["index"]
            if index == 0:
                await sibling_started.wait()
                failure_raised.set()
                raise transport_failure
            sibling_started.set()
            await failure_raised.wait()
            await asyncio.sleep(0)
            completed.append(index)

        context.invoke_agent = invoke_agent
        with pytest.raises(error_type) as captured:
            await context.invoke_agents(
                agent_key="specialist", inputs=[{"index": i} for i in range(3)],
                item_postprocessor="identity", return_subtree_failures=return_subtree_failures,
            )
        assert captured.value is transport_failure
        assert sorted(completed) == [1, 2]
        assert captured.value.diagnostic["batch_progress"] == {
            "total_count": 3, "failed_indexes": [0], "validated_indexes": [1, 2],
            "not_started_indexes": [],
        }

    asyncio.run(run_batch())


@pytest.mark.parametrize("parent_cancelled", [False, True])
def test_invoke_agents_still_cancels_siblings_on_fatal_or_parent_failure(parent_cancelled: bool) -> None:
    """源输入错误及父调用取消仍立即结束整组任务。"""

    async def run_batch() -> None:
        context = ToolExecutionContext(
            db=None, user_id=1, project_id=9, run_id=42,
            node_key="resolve_generation", run_input={}, agent_concurrency=2,
        )
        sibling_started = asyncio.Event()
        sibling_cancelled = asyncio.Event()

        async def invoke_agent(**kwargs):
            if kwargs["input_payload"]["index"] == 0:
                await sibling_started.wait()
                if parent_cancelled:
                    await asyncio.Event().wait()
                raise ValueError("源输入不满足契约")
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise

        context.invoke_agent = invoke_agent
        task = asyncio.create_task(context.invoke_agents(
            agent_key="specialist", inputs=[{"index": i} for i in range(2)],
            item_postprocessor="identity",
        ))
        if parent_cancelled:
            await sibling_started.wait()
            task.cancel()
        with pytest.raises(asyncio.CancelledError if parent_cancelled else ValueError):
            await task
        assert sibling_cancelled.is_set()

    asyncio.run(run_batch())


def test_invoke_agents_propagates_child_cancellation_and_stops_siblings() -> None:
    """子 Agent 主动取消必须回到重规划链，不能被 TaskGroup 静默收敛成 None。"""

    async def run_batch() -> bool:
        context = ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=66,
            run_id=1,
            node_key="agent_network",
            run_input={},
            agent_concurrency=2,
        )
        second_started = asyncio.Event()
        sibling_cancelled = False

        async def invoke_agent(**kwargs):
            nonlocal sibling_cancelled
            item_id = kwargs["input_payload"]["id"]
            if item_id == 1:
                await second_started.wait()
                raise asyncio.CancelledError()
            second_started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                sibling_cancelled = True
                raise
            return {"id": item_id}

        context.invoke_agent = invoke_agent
        with pytest.raises(asyncio.CancelledError):
            await context.invoke_agents(
                agent_key="specialist",
                inputs=[{"id": 1}, {"id": 2}],
                item_postprocessor="identity",
            )
        return sibling_cancelled

    assert asyncio.run(run_batch()) is True


def test_builtin_tool_can_be_constructed_as_strict_sdk_function_tool() -> None:
    spec = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "analyze_source"
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
            node_key="agent_network",
            run_input={},
        )

        tool = _function_tool(definition, context)

        assert tool.name == "analyze_source"
        assert set(tool.params_json_schema["required"]) == {
            "source_kind",
            "item_indexes",
        }
    finally:
        db.close()


def test_function_tool_records_bounded_diagnostic_for_malformed_arguments() -> None:
    spec = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "analyze_source"
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
        node_key="agent_network",
        run_input={},
    )
    tool = _function_tool(definition, context)
    malformed = '{"requirement" "缺少冒号' + ("内容" * 300) + '"}'

    with pytest.raises(ToolArgumentsJSONError) as captured:
        asyncio.run(tool.on_invoke_tool(None, malformed))

    diagnostic = captured.value.diagnostic
    assert diagnostic["tool_key"] == "analyze_source"
    assert diagnostic["arguments_chars"] == len(malformed)
    assert diagnostic["error_position"] == 15
    assert len(diagnostic["arguments_sha256"]) == 64
    assert len(diagnostic["arguments_excerpt"]) <= 240
    assert diagnostic["excerpt_truncated_after"] is True
    assert "near_error=" in str(captured.value)


def test_function_tool_records_compact_diagnostic_for_invalid_argument_schema() -> None:
    spec = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "analyze_source"
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
        node_key="agent_network",
        run_input={},
    )
    tool = _function_tool(definition, context)

    with pytest.raises(ToolArgumentsValidationError) as captured:
        asyncio.run(tool.on_invoke_tool(None, "{}"))

    diagnostic = captured.value.diagnostic
    assert diagnostic["tool_key"] == "analyze_source"
    assert diagnostic["validation_keyword"] == "required"
    assert diagnostic["instance_path"] == []
    assert diagnostic["schema_path"] == ["required"]
    assert diagnostic["missing_fields"] == [
        "source_kind",
        "item_indexes",
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


def test_tool_argument_normalizer_retains_malformed_encoded_structure_for_schema_feedback() -> None:
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

    assert normalized["test_cases"] == '[{"title":"复制全文","expected":"弹出toast提示"复制成功""}]'


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
