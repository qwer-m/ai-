from __future__ import annotations

import asyncio
import gzip
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
    from modules.agent_platform.test_generation_builtin_specs import (
        BUILTIN_AGENT_SPECS as generation_agents, BUILTIN_TOOL_SPECS as generation_tools,
        BUILTIN_WORKFLOW_SPECS as generation_workflows,
    )
    from modules.agent_platform.test_generation_schemas import GENERATION_EXECUTION_LIMITS

    # 直接核对当前真实注册定义，不保留已删除专家或阶段工具满足旧测试。
    agents = {item["agent_key"]: item for item in BUILTIN_AGENT_SPECS}
    tools = {item["tool_key"]: item for item in BUILTIN_TOOL_SPECS}
    assert len(agents) == len(BUILTIN_AGENT_SPECS)
    assert len(tools) == len(BUILTIN_TOOL_SPECS)
    assert {item["agent_key"] for item in generation_agents} == {
        "test_generation_coordinator", "test_generation_reviewer",
    }
    action_keys = {"read_source", "view_source_image", "commit_coverage_plan", "append_cases", "patch_cases",
                   "inspect_cases", "report_issue", "request_review", "request_confirmation", "finish_generation"}
    assert {item["tool_key"] for item in generation_tools} == action_keys
    coordinator = agents["test_generation_coordinator"]["runtime_config"]
    assert set(coordinator["tool_keys"]) == action_keys | {"retrieve_context"}
    assert coordinator["managed_agent_keys"] == ["test_generation_reviewer"]
    assert coordinator["managed_agent_max_concurrency"] == 1
    assert coordinator["stop_at_tool_keys"] == ["finish_generation"]
    assert coordinator["max_turns"] == 24
    for key in ("state_initializer", "state_observer", "managed_artifacts_provider", "output_postprocessor", "model_material_provider"):
        assert tool_registry.resolve(coordinator[key])
    for item in generation_tools:
        assert tool_registry.is_state_only(item["handler_key"])
    assert len(generation_workflows) == 1
    generation = parse_execution_definition(generation_workflows[0]["definition"])
    assert isinstance(generation, AgentProgramDefinition)
    assert generation.entry_agent_key == "test_generation_coordinator"
    assert generation.time_budget_seconds == 1200
    assert generation.required_artifact_key == "test_generation"
    assert generation.input_schema["additionalProperties"] is False
    assert not {"source_context_run_id", "enable_context_compression", "context_compression_max_tokens"} & set(generation.input_schema["properties"])
    assert GENERATION_EXECUTION_LIMITS["max_total_tokens"] == 300000
    assert GENERATION_EXECUTION_LIMITS["max_requests"] == 32
    evaluation_workflows = [item for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] != "test_generation"]
    assert len(evaluation_workflows) == 3
    for spec in BUILTIN_WORKFLOW_SPECS:
        execution = parse_execution_definition(spec["definition"])
        if isinstance(execution, AgentProgramDefinition):
            assert execution.entry_agent_key in agents
            continue
        assert isinstance(execution, WorkflowGraph)
        assert execution.execution_order()
        for node in execution.nodes:
            allowed = agents if node.node_type in {"agent", "agent_network", "agent_map"} else tools
            assert node.reference_key in allowed
    for spec in BUILTIN_AGENT_SPECS:
        config = spec["runtime_config"]
        for name in ("subagent_keys", "managed_agent_keys"):
            assert set(config.get(name, [])) <= set(agents)
        for key in config.get("tool_keys", []):
            assert key in tools
            assert tool_registry.resolve(tools[key]["handler_key"])








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
        "max_total_tokens": 300000,
    }




# 规划批次的真实事实、身份与无损意图由 test_planning_business_inputs_real_data.py 验证。


# 规划候选与来源边界由 verify_business_plan_feedback_real_data.py 回放真实 Run 28/29；
# 单元测试只保留具备正式覆盖组目录和来源绑定的当前输入。








































def test_no_tool_agent_uses_single_sdk_turn_and_keeps_node_retry_boundary() -> None:
    assert effective_max_turns({}, has_tools=False) == 1
    assert effective_max_turns({"max_turns": 4}, has_tools=True) == 4
    with pytest.raises(ValueError, match="无工具 Agent"):
        effective_max_turns({"max_turns": 4}, has_tools=False)


def test_dynamic_agent_can_call_specialist_before_terminal_submission() -> None:
    settings = _model_settings(
        {
            "stop_at_tool_keys": ["finish_generation"],
            "force_terminal_tool_choice": False,
        }
    )

    assert settings.tool_choice == "required"
    assert _tool_use_behavior(
        {"stop_at_tool_keys": ["finish_generation"]},
        available_tool_names={
            "test_generation_reviewer",
            "finish_generation",
        },
    ) == {"stop_at_tool_names": ["finish_generation"]}


















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
    from modules.agent_platform.test_generation_schemas import REVIEW_SCHEMA

    reviewer = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == "test_generation_reviewer")
    definition = AgentDefinition(agent_key=reviewer["agent_key"], output_schema=reviewer["output_schema"])
    assert _sdk_output_type(definition, has_tools=False) is not None
    assert reviewer["output_schema"] == REVIEW_SCHEMA
    assert reviewer["runtime_config"]["stop_at_tool_keys"] == ["submit_result"]
    assert reviewer["runtime_config"]["tool_keys"] == []
    assert reviewer["runtime_config"]["validate_before_submit"] is True
    assert reviewer["runtime_config"]["disable_server_output_schema"] is True



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
        if item["tool_key"] == "read_source"
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

        assert tool.name == "read_source"
        assert set(tool.params_json_schema["required"]) == {
            "evidence_id",
            "offset",
            "max_chars",
        }
    finally:
        db.close()


def test_function_tool_records_bounded_diagnostic_for_malformed_arguments() -> None:
    spec = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "read_source"
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
    assert diagnostic["tool_key"] == "read_source"
    assert diagnostic["arguments_chars"] == len(malformed)
    assert diagnostic["error_position"] == 15
    assert len(diagnostic["arguments_sha256"]) == 64
    assert len(diagnostic["arguments_excerpt"]) <= 240
    assert diagnostic["excerpt_truncated_after"] is True
    assert "near_error=" in str(captured.value)


def test_function_tool_records_compact_diagnostic_for_invalid_argument_schema() -> None:
    spec = next(
        item for item in BUILTIN_TOOL_SPECS
        if item["tool_key"] == "read_source"
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
    assert diagnostic["tool_key"] == "read_source"
    assert diagnostic["validation_keyword"] == "required"
    assert diagnostic["instance_path"] == []
    assert diagnostic["schema_path"] == ["required"]
    assert diagnostic["missing_fields"] == [
        "evidence_id",
        "offset",
        "max_chars",
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
