from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from agents.exceptions import ModelBehaviorError, UserError
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from openai import BadRequestError
from httpx import Request, Response
from pydantic import BaseModel, ValidationError

from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform import runtime
from modules.agent_platform.contracts import WorkflowNode
from modules.agent_platform.sdk_adapter import (
    AgentExecutionResult,
    StructuredOutputJSONError,
    StructuredOutputValidationError,
    ToolArgumentsJSONError,
    ToolArgumentsValidationError,
    ToolOutputValidationError,
)
from modules.agent_platform.registry import ToolExecutionContext


@pytest.fixture(autouse=True)
def _isolate_model_configuration_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime,
        "resolve_agent_model_metadata",
        lambda **_: {
            "name": "test-model",
            "route": "main",
            "source": "测试模型路由",
        },
    )


class _HTTPError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _wrapped_tool_arguments_error() -> UserError:
    raw_arguments = '{"authoritative_facts" []}'
    json_error = json.JSONDecodeError("Expecting ':' delimiter", raw_arguments, 23)
    model_error = ToolArgumentsJSONError(
        tool_key="submit_source_semantics",
        arguments_json=raw_arguments,
        json_error=json_error,
    )
    try:
        raise model_error
    except ToolArgumentsJSONError as exc:
        try:
            raise UserError(
                f"Error running tool submit_source_semantics: {exc}"
            ) from exc
        except UserError as wrapped:
            return wrapped


def _wrapped_tool_arguments_validation_error() -> UserError:
    raw_arguments = "{}"
    model_error = ToolArgumentsValidationError(
        tool_key="submit_source_semantics",
        arguments_json=raw_arguments,
        validation_error=JSONSchemaValidationError(
            "'authoritative_facts' is a required property",
            validator="required",
            validator_value=["authoritative_facts"],
            instance={},
            schema={"required": ["authoritative_facts"]},
            schema_path=["required"],
        ),
    )
    try:
        raise model_error
    except ToolArgumentsValidationError as exc:
        try:
            raise UserError(
                f"Error running tool submit_source_semantics: {exc}"
            ) from exc
        except UserError as wrapped:
            return wrapped


def _wrapped_tool_output_validation_error() -> UserError:
    contract_error = ToolOutputValidationError(
        tool_key="submit_source_semantics",
        output={},
        validation_error=JSONSchemaValidationError(
            "'authoritative_facts' is a required property",
            validator="required",
            validator_value=["authoritative_facts"],
            instance={},
            schema={"required": ["authoritative_facts"]},
            schema_path=["required"],
        ),
    )
    try:
        raise contract_error
    except ToolOutputValidationError as exc:
        try:
            raise UserError(
                f"Error running tool submit_source_semantics: {exc}"
            ) from exc
        except UserError as wrapped:
            return wrapped


class _FakeDB:
    def __init__(self, repo: "_FakeRepository") -> None:
        self.repo = repo
        self.rollback_count = 0

    def add(self, value: Any) -> None:
        if isinstance(value, AgentNodeRun) and value not in self.repo.node_runs:
            value.id = len(self.repo.node_runs) + 1
            self.repo.node_runs.append(value)

    def flush(self) -> None:
        return None

    def rollback(self) -> None:
        self.rollback_count += 1

    def commit(self) -> None:
        return None


class _FakeRepository:
    def __init__(self, run: AgentRun) -> None:
        self.run = run
        self.node_runs: list[AgentNodeRun] = []
        self.events: list[dict[str, Any]] = []
        self.db = _FakeDB(self)

    def get_run(self, *, run_id: int) -> AgentRun | None:
        return self.run if self.run.id == run_id else None

    def refresh(self, value: Any) -> None:
        return None

    def list_node_runs(self, *, run_id: int) -> list[AgentNodeRun]:
        if self.run.id != run_id:
            return []
        return sorted(self.node_runs, key=lambda item: int(item.id or 0))

    def latest_node_run(self, *, run_id: int, node_key: str) -> AgentNodeRun | None:
        matches = [
            item
            for item in self.node_runs
            if item.run_id == run_id and item.node_key == node_key
        ]
        return max(matches, key=lambda item: item.attempt) if matches else None

    def next_node_attempt(self, *, run_id: int, node_key: str) -> int:
        latest = self.latest_node_run(run_id=run_id, node_key=node_key)
        return int(latest.attempt if latest is not None else 0) + 1

    def get_agent(self, *, project_id: int, agent_key: str) -> Any:
        return SimpleNamespace(id=9, name="真实调用智能体")

    def list_agent_tools(self, agent_definition_id: int) -> list[Any]:
        return []

    def append_event(
        self,
        *,
        run_id: int,
        event_type: str,
        payload: dict[str, Any],
        node_run_id: int | None = None,
    ) -> None:
        self.events.append(
            {
                "run_id": run_id,
                "event_type": event_type,
                "payload": payload,
                "node_run_id": node_run_id,
            }
        )

    def commit(self) -> None:
        return None


def _run() -> AgentRun:
    return AgentRun(
        id=41,
        user_id=1,
        project_id=2,
        workflow_definition_id=7,
        status="running",
        input_payload={"requirement": "真实需求"},
        run_context={},
        output_payload={},
    )


def _agent_node(*, node_type: str = "agent", max_attempts: int = 3) -> WorkflowNode:
    values: dict[str, Any] = {
        "node_key": "plan",
        "node_type": node_type,
        "reference_key": "business_planner",
        "max_attempts": max_attempts,
        "input_mapping": {"requirement": "input.requirement"},
    }
    if node_type == "agent_map":
        values["map_config"] = {"items_key": "items", "output_key": "items"}
    return WorkflowNode.model_validate(values)


def test_dependency_outputs_are_rebuilt_from_latest_successful_node_checkpoints() -> None:
    run = _run()
    repo = _FakeRepository(run)
    repo.node_runs = [
        AgentNodeRun(
            id=1,
            run_id=run.id,
            node_key="plan",
            node_type="agent",
            attempt=1,
            status="success",
            output_payload={"value": "旧计划"},
        ),
        AgentNodeRun(
            id=2,
            run_id=run.id,
            node_key="plan",
            node_type="agent",
            attempt=2,
            status="failed",
            output_payload={"value": "失败输出"},
        ),
        AgentNodeRun(
            id=3,
            run_id=run.id,
            node_key="evidence",
            node_type="tool",
            attempt=1,
            status="success",
            output_payload={"value": "真实证据"},
        ),
    ]

    outputs = runtime._persisted_dependency_outputs(repo, run_id=run.id)

    assert outputs == {"evidence": {"value": "真实证据"}}


def test_persistent_error_message_does_not_write_full_validation_payload() -> None:
    message = runtime._persistent_error_message(ValueError("错误详情" * 3000))

    assert message.startswith("ValueError: 错误详情")
    assert len(message) == 4000


def test_node_duration_seconds_uses_source_node_timestamps() -> None:
    node_run = AgentNodeRun(
        started_at=datetime(2026, 8, 27, 2, 40, 0),
        finished_at=datetime(2026, 8, 27, 2, 43, 15),
    )

    assert runtime._node_duration_seconds(node_run) == 195


def test_reusable_agent_node_output_requires_exact_input_and_cache_version() -> None:
    run = _run()
    node = _agent_node(node_type="agent_map")
    node_input = {"items": [{"page_number": 1, "text": "真实正文"}]}
    candidate = AgentNodeRun(
        id=88,
        run_id=28,
        node_key=node.node_key,
        node_type=node.node_type,
        agent_definition_id=9,
        status="success",
        input_payload=node_input,
        output_payload={"items": [{"output": {"fact_id": "F-001"}}]},
        sdk_state={"result_cache": {"version": "source-semantics-v1"}},
    )

    class CandidateDB:
        def get(self, model: Any, candidate_id: int) -> AgentNodeRun | None:
            assert model is AgentNodeRun
            return candidate if candidate_id == candidate.id else None

    class CandidateRepository:
        db = CandidateDB()

        def list_reusable_node_run_ids(self, **_: Any) -> list[int]:
            return [candidate.id]

    definition = SimpleNamespace(
        id=9,
        agent_key="source_analyst",
        runtime_config={
            "result_cache": {"version": "source-semantics-v1"}
        },
    )

    reusable = runtime._reusable_agent_node_output(
        repo=CandidateRepository(),
        run=run,
        node=node,
        definition=definition,
        node_input=node_input,
    )

    assert reusable is not None
    assert reusable[0].id == 88
    assert reusable[1] == candidate.output_payload
    assert reusable[2] == "source-semantics-v1"
    assert reusable[3] == runtime._payload_hash(node_input)

    run.input_payload = {"disable_result_cache": True}
    assert runtime._reusable_agent_node_output(
        repo=CandidateRepository(),
        run=run,
        node=node,
        definition=definition,
        node_input=node_input,
    ) is None
    run.input_payload = {}

    definition.runtime_config["result_cache"]["version"] = "source-semantics-v2"
    assert runtime._reusable_agent_node_output(
        repo=CandidateRepository(),
        run=run,
        node=node,
        definition=definition,
        node_input=node_input,
    ) is None

    definition.runtime_config["result_cache"]["version"] = "source-semantics-v1"
    assert runtime._reusable_agent_node_output(
        repo=CandidateRepository(),
        run=run,
        node=node,
        definition=definition,
        node_input={"items": [{"page_number": 2, "text": "其他正文"}]},
    ) is None


def test_result_cache_identity_includes_input_projection_contract() -> None:
    run = _run()
    node = _agent_node(node_type="agent_map")
    node_input = {"items": [{"id": "F-001", "text": "真实正文"}]}
    definition = SimpleNamespace(
        id=9,
        agent_key="source_analyst",
        runtime_config={
            "input_projection_version": "projection-v1",
            "input_projection": {"items": ["id"]},
            "result_cache": {"version": "source-semantics-v2"},
        },
    )
    candidate = AgentNodeRun(
        id=90,
        run_id=28,
        node_key=node.node_key,
        node_type=node.node_type,
        agent_definition_id=9,
        status="success",
        input_payload=node_input,
        output_payload={"items": [{"output": {"fact_id": "F-001"}}]},
        sdk_state={
            "result_cache": {
                "version": "source-semantics-v2",
                "input_hash": runtime._agent_result_cache_input_hash(
                    definition,
                    node_input,
                ),
            }
        },
    )
    repo = SimpleNamespace(
        db=SimpleNamespace(get=lambda _model, _id: candidate),
        list_reusable_node_run_ids=lambda **_: [candidate.id],
    )

    assert runtime._reusable_agent_node_output(
        repo=repo,
        run=run,
        node=node,
        definition=definition,
        node_input=node_input,
    ) is not None

    definition.runtime_config["input_projection"] = {"items": ["id", "text"]}
    assert runtime._reusable_agent_node_output(
        repo=repo,
        run=run,
        node=node,
        definition=definition,
        node_input=node_input,
    ) is None


def test_planning_route_repair_targets_only_invalid_scope() -> None:
    item_input = {
        "scopes": [
            {
                "scope_id": "EV-0001",
                "facts": [
                    {"fact_id": "F-001", "assertion": "入口"},
                    {"fact_id": "F-002", "assertion": "列表"},
                ],
            },
            {
                "scope_id": "EV-0002",
                "facts": [{"fact_id": "F-003", "assertion": "详情"}],
            },
        ]
    }
    candidate = {
        "routes": [
            {
                "scope_id": "EV-0001",
                "assignments": [
                    {"fact_id": "F-001", "module_routes": [{"module_index": 0}]},
                    {"fact_id": "F-002", "module_routes": [{"module_index": 0}]},
                ],
            },
            {
                "scope_id": "EV-0002",
                "assignments": [
                    {"fact_id": "F-003", "module_routes": []},
                ],
            },
        ]
    }

    context = runtime._planning_route_repair_target_context(
        item_input=item_input,
        candidate=candidate,
        validation_feedback="规划路由模块映射无效: scope_id=EV-0002",
    )

    assert context == {
        "route_repair_targets": [
            {
                "scope_id": "EV-0002",
                "missing_fact_ids": [],
                "invalid_fact_ids": ["F-003"],
            }
        ],
        "protected_scope_ids": ["EV-0001"],
    }


def test_agent_map_input_projection_keeps_raw_item_separate() -> None:
    raw_item = {
        "plan": {"name": "课程学习", "fact_ids": ["F-001", "F-002"]},
        "authoritative_facts": [
            {"fact_id": "F-001", "assertion": "用户进入课程", "source_anchor": {"page": 1}}
        ],
    }
    definition = SimpleNamespace(
        runtime_config={
            "input_projection_version": "projection-v1",
            "input_projection": {
                "plan": ["name"],
                "authoritative_facts": ["fact_id", "assertion"],
            },
        }
    )

    projected, diagnostics = runtime._project_agent_map_input(
        definition=definition,
        raw_item=raw_item,
    )

    assert projected == {
        "plan": {"name": "课程学习"},
        "authoritative_facts": [
            {"fact_id": "F-001", "assertion": "用户进入课程"}
        ],
    }
    assert raw_item["plan"]["fact_ids"] == ["F-001", "F-002"]
    assert diagnostics["model_json_chars"] < diagnostics["raw_json_chars"]


def test_agent_map_postprocessor_receives_raw_item_after_model_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    run.run_context = {
        "execution_limits": {
            "max_requests": 10,
            "max_input_tokens": 999999,
            "max_output_tokens": 999999,
            "max_total_tokens": 999999,
        }
    }
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "generation",
            "node_type": "agent_map",
            "reference_key": "generator",
            "max_attempts": 1,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    raw_item = {
        "model_text": "只供模型使用的正文",
        "source_anchor": {"document_id": 239, "page_number": 1},
        "case_fact_contract": {
            "fact_design_item_ids": {"F-001": ["TD-001-001-001"]},
            "coverage_slots": [{"case_id": "TC-001"}],
        },
    }
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [raw_item]},
    )
    repo.node_runs.append(node_run)
    model_inputs: list[dict[str, Any]] = []
    postprocess_inputs: list[dict[str, Any]] = []

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        model_inputs.append(dict(arguments["item_input"]))
        return AgentExecutionResult(
            output={"ok": True},
            final_text='{"ok":true}',
            last_agent_name="生成智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    def postprocess(**arguments: Any) -> dict[str, Any]:
        postprocess_inputs.append(dict(arguments["item_input"]))
        return dict(arguments["item_output"])

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_postprocess_agent_map_output", postprocess)

    output, _ = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(
            id=9,
            name="生成智能体",
            runtime_config={
                "input_projection_version": "projection-test-v1",
                "input_projection": {"model_text": True},
            },
        ),
        model_metadata={"name": "test-model", "route": "main", "source": "主模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": [raw_item]},
        previous=None,
    )

    assert model_inputs == [{"model_text": "只供模型使用的正文"}]
    assert postprocess_inputs == [raw_item]
    assert output["items"][0]["output"] == {"ok": True}


def test_reusable_agent_node_output_rejects_legacy_without_explicit_permission() -> None:
    run = _run()
    node = _agent_node()
    node_input = {"requirement": "真实需求"}
    candidate = AgentNodeRun(
        id=89,
        run_id=28,
        node_key=node.node_key,
        node_type=node.node_type,
        agent_definition_id=9,
        status="success",
        input_payload=node_input,
        output_payload={"summary": "历史规划"},
        sdk_state={},
    )

    repo = SimpleNamespace(
        db=SimpleNamespace(get=lambda _model, _id: candidate),
        list_reusable_node_run_ids=lambda **_: [candidate.id],
    )
    definition = SimpleNamespace(
        id=9,
        agent_key="business_planner",
        runtime_config={"result_cache": {"version": "business-plan-v1"}},
    )

    assert runtime._reusable_agent_node_output(
        repo=repo,
        run=run,
        node=node,
        definition=definition,
        node_input=node_input,
    ) is None

    definition.runtime_config["result_cache"]["accept_legacy"] = True
    assert runtime._reusable_agent_node_output(
        repo=repo,
        run=run,
        node=node,
        definition=definition,
        node_input=node_input,
    ) is not None

    definition.runtime_config["result_cache"]["version"] = "business-plan-v2"
    assert runtime._reusable_agent_node_output(
        repo=repo,
        run=run,
        node=node,
        definition=definition,
        node_input=node_input,
    ) is None


def test_standard_agent_postprocessor_runs_once_with_raw_node_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    raw_input = {"requirement": "完整原始需求字段"}
    run.input_payload = dict(raw_input)
    definition = SimpleNamespace(
        id=9,
        name="普通规划智能体",
        agent_key="business_planner",
        instructions="",
        output_schema={},
        runtime_config={
            "max_turns": 1,
            "max_output_tokens": 100,
            "output_postprocessor": "testing.postprocess_once",
        },
    )
    repo.get_agent = lambda **_: definition
    calls: list[dict[str, Any]] = []

    def execute_agent(**arguments: Any) -> AgentExecutionResult:
        assert arguments["skip_output_postprocessor"] is True
        return AgentExecutionResult(
            output={"ok": True},
            final_text='{"ok":true}',
            last_agent_name="普通规划智能体",
            usage={"requests": 1, "input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )

    def postprocess(_context: object, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(arguments)
        return {"ok": True, "processed_with": arguments["input_payload"]}

    monkeypatch.setattr(runtime, "run_agent", execute_agent)
    monkeypatch.setattr(runtime.tool_registry, "resolve", lambda _key: postprocess)

    node = _agent_node()
    node.input_mapping = {"requirement": "input.requirement"}
    executed = runtime._execute_node_with_retry(repo, run, node, {})

    assert executed is not None
    assert executed[1] == {"ok": True, "processed_with": raw_input}
    assert len(calls) == 1
    assert calls[0]["input_payload"] == raw_input


def test_agent_network_node_uses_standard_agent_runtime_inside_dag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    received_node_types: list[str] = []

    def execute_agent(**_: Any) -> AgentExecutionResult:
        received_node_types.append(repo.node_runs[-1].node_type)
        return AgentExecutionResult(
            output={"test_cases": [{"title": "真实生成结果"}]},
            final_text='{"test_cases":[{"title":"真实生成结果"}]}',
            last_agent_name="生成协调智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "run_agent", execute_agent)

    executed = runtime._execute_node_with_retry(
        repo,
        run,
        _agent_node(node_type="agent_network"),
        {},
    )

    assert executed is not None
    assert executed[1] == {"test_cases": [{"title": "真实生成结果"}]}
    assert received_node_types == ["agent_network"]
    assert [(item.attempt, item.status) for item in repo.node_runs] == [(1, "success")]


def test_standard_agent_retries_504_as_new_node_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run()
    repo = _FakeRepository(run)
    calls = 0

    def execute_agent(**_: Any) -> AgentExecutionResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _HTTPError(504, "上游网关超时")
        return AgentExecutionResult(
            output={"summary": "处理完成"},
            final_text='{"summary":"处理完成"}',
            last_agent_name="真实调用智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "run_agent", execute_agent)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)

    executed = runtime._execute_node_with_retry(repo, run, _agent_node(), {})

    assert executed is not None
    assert executed[1] == {"summary": "处理完成"}
    assert calls == 2
    assert [(item.attempt, item.status) for item in repo.node_runs] == [
        (1, "failed"),
        (2, "success"),
    ]
    assert [item["event_type"] for item in repo.events] == [
        "node_started",
        "node_failed",
        "node_retry_scheduled",
        "node_started",
        "node_completed",
    ]
    assert repo.events[1]["payload"]["retryable"] is True
    assert repo.events[2]["payload"] == {
        "node_key": "plan",
        "failed_attempt": 1,
        "next_attempt": 2,
    }
    assert run.run_context["usage"] == {
        "attempted_requests": 2,
        "requests": 1,
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }
    instance_quota = run.run_context["agent_instance_quota_usage"]["plan-instance-001"]
    assert instance_quota["attempted_requests"] == 2
    assert instance_quota["input_tokens"] > 10
    assert instance_quota["output_tokens"] > 5


def test_standard_agent_disables_server_schema_after_capability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    received_modes: list[bool] = []

    received_feedback: list[str | None] = []

    def execute_agent(**arguments: Any) -> AgentExecutionResult:
        received_modes.append(bool(arguments.get("disable_server_output_schema")))
        received_feedback.append(arguments.get("retry_feedback"))
        if len(received_modes) == 1:
            raise _HTTPError(400, "This response_format type is unavailable now.")
        if len(received_modes) == 2:
            raise ModelBehaviorError("本地 JSON Schema 校验失败: 缺少 review_summary")
        return AgentExecutionResult(
            output={"review_summary": "终审通过"},
            final_text='{"review_summary":"终审通过"}',
            last_agent_name="终审智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "run_agent", execute_agent)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)

    executed = runtime._execute_node_with_retry(
        repo,
        run,
        _agent_node(max_attempts=2),
        {},
    )

    assert executed is not None
    assert executed[1] == {"review_summary": "终审通过"}
    assert received_modes == [False, True, True]
    assert received_feedback[:2] == [None, None]
    assert "缺少 review_summary" in str(received_feedback[2])
    assert [(item.attempt, item.status) for item in repo.node_runs] == [
        (1, "failed"),
        (2, "failed"),
        (3, "success"),
    ]
    assert all(
        item.sdk_state["server_output_schema_disabled"] is True
        for item in repo.node_runs
    )


def test_standard_agent_separates_capability_transient_and_content_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    received_modes: list[tuple[bool, bool]] = []
    received_feedback: list[str | None] = []

    def execute_agent(**arguments: Any) -> AgentExecutionResult:
        received_modes.append(
            (
                bool(arguments.get("disable_server_output_schema")),
                bool(arguments.get("disable_model_thinking")),
            )
        )
        received_feedback.append(arguments.get("retry_feedback"))
        attempt = len(received_modes)
        if attempt == 1:
            raise _HTTPError(400, "This response_format type is unavailable now.")
        if attempt == 2:
            raise _HTTPError(504, "上游网关超时")
        if attempt == 3:
            raise ModelBehaviorError("智能体结构化输出正文为空")
        if attempt == 4:
            raise ModelBehaviorError("智能体最终输出契约校验失败: 缺少 phase")
        return AgentExecutionResult(
            output={"phase": "final_review", "approved": True},
            final_text='{"phase":"final_review","approved":true}',
            last_agent_name="终审智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "run_agent", execute_agent)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)

    executed = runtime._execute_node_with_retry(
        repo,
        run,
        _agent_node(max_attempts=2),
        {},
    )

    assert executed is not None
    assert received_modes == [
        (False, False),
        (True, False),
        (True, False),
        (True, True),
        (True, True),
    ]
    assert received_feedback[:3] == [None, None, None]
    assert "结构化输出正文为空" in str(received_feedback[3])
    assert "缺少 phase" in str(received_feedback[4])
    final_state = repo.node_runs[-1].sdk_state
    assert final_state["server_output_schema_disabled"] is True
    assert final_state["model_thinking_disabled"] is True


def test_standard_agent_hard_error_fails_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run()
    repo = _FakeRepository(run)
    calls = 0

    def execute_agent(**_: Any) -> AgentExecutionResult:
        nonlocal calls
        calls += 1
        raise ValueError("输出契约错误")

    monkeypatch.setattr(runtime, "run_agent", execute_agent)

    with pytest.raises(ValueError, match="输出契约错误"):
        runtime._execute_node_with_retry(repo, run, _agent_node(), {})

    assert calls == 1
    assert [(item.attempt, item.status) for item in repo.node_runs] == [(1, "failed")]


def test_single_agent_instance_quota_blocks_model_call_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run()
    run.run_context = {
        "execution_limits": {
            "max_requests": 1,
            "max_input_tokens": 999999,
            "max_output_tokens": 999999,
            "max_total_tokens": 999999,
        },
        "usage": {
            "attempted_requests": 1,
            "requests": 1,
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        },
        "agent_instance_quota_usage": {
            "plan-instance-001": {
                "attempted_requests": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            }
        },
    }
    repo = _FakeRepository(run)
    calls = 0

    def execute_agent(**_: Any) -> AgentExecutionResult:
        nonlocal calls
        calls += 1
        return AgentExecutionResult(
            output={"summary": "超过历史额度后仍完成"},
            final_text='{"summary":"超过历史额度后仍完成"}',
            last_agent_name="真实调用智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "run_agent", execute_agent)

    with pytest.raises(runtime._AgentQuotaExceeded, match="请求次数 2/1"):
        runtime._execute_node_with_retry(repo, run, _agent_node(), {})

    assert calls == 0
    assert run.run_context["usage"]["attempted_requests"] == 1
    instance_quota = run.run_context["agent_instance_quota_usage"]["plan-instance-001"]
    assert instance_quota["attempted_requests"] == 1
    assert instance_quota["total_tokens"] == 15
    assert [item["event_type"] for item in repo.events] == [
        "node_started",
        "agent_instance_quota_blocked",
        "node_failed",
    ]


def test_tool_agent_reserves_all_possible_sdk_turns_within_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    run.run_context = {
        "execution_limits": {
            "max_requests": 3,
            "max_input_tokens": 999999,
            "max_output_tokens": 999999,
            "max_total_tokens": 999999,
        },
        "agent_instance_quota_usage": {
            "plan-instance-001": {
                "attempted_requests": 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
        },
    }
    repo = _FakeRepository(run)
    repo.get_agent = lambda **_: SimpleNamespace(
        id=9,
        name="工具智能体",
        instructions="按工具结果输出",
        output_schema={},
        runtime_config={"max_turns": 2, "max_output_tokens": 100},
    )
    repo.list_agent_tools = lambda _agent_id: [
        SimpleNamespace(
            tool_key="lookup",
            name="查询",
            description="读取真实数据",
            input_schema={},
            output_schema={},
        )
    ]
    calls = 0

    def execute_agent(**_: Any) -> AgentExecutionResult:
        nonlocal calls
        calls += 1
        return AgentExecutionResult(
            output={"summary": "工具智能体完成"},
            final_text='{"summary":"工具智能体完成"}',
            last_agent_name="工具智能体",
            usage={"requests": 2, "input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
        )

    monkeypatch.setattr(runtime, "run_agent", execute_agent)

    executed = runtime._execute_node_with_retry(repo, run, _agent_node(), {})

    assert calls == 1
    assert executed is not None
    assert executed[1] == {"summary": "工具智能体完成"}
    assert [item["event_type"] for item in repo.events] == [
        "node_started",
        "node_completed",
    ]
    instance_quota = run.run_context["agent_instance_quota_usage"]["plan-instance-001"]
    assert instance_quota["attempted_requests"] == 3
    assert instance_quota["total_tokens"] == 40


@pytest.mark.parametrize("status_code", [429, 500, 502, 504])
def test_http_transient_status_is_retryable(status_code: int) -> None:
    assert runtime._is_retryable_agent_error(_HTTPError(status_code, "暂时不可用"))


def test_timeout_and_connection_errors_are_retryable() -> None:
    assert runtime._is_retryable_agent_error(TimeoutError("读取超时"))
    assert runtime._is_retryable_agent_error(ConnectionError("连接中断"))
    assert runtime._agent_retry_feedback(TimeoutError("读取超时")) is None
    assert runtime._agent_retry_feedback(ConnectionError("连接中断")) is None
    assert runtime._agent_map_attempt_limit(
        exc=TimeoutError("读取超时"),
        configured_max_attempts=2,
    ) == 4
    assert not runtime._is_retryable_agent_error(_HTTPError(400, "请求错误"))
    assert runtime._agent_map_attempt_limit(
        exc=_HTTPError(400, "请求错误"),
        configured_max_attempts=2,
    ) == 2


def test_agent_map_first_attempt_uses_shorter_timeout_and_retry_restores_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    definition = SimpleNamespace(runtime_config={"request_timeout_seconds": 180})
    monkeypatch.setattr(
        runtime.settings,
        "AGENT_MAP_FIRST_ATTEMPT_TIMEOUT_SECONDS",
        120.0,
    )

    assert runtime._agent_map_request_timeout_seconds(
        run,
        definition,
        node_key="source_text",
        item_attempt=1,
    ) == 120.0
    assert runtime._agent_map_request_timeout_seconds(
        run,
        definition,
        node_key="source_text",
        item_attempt=2,
    ) == 180.0


def test_model_structured_output_error_is_retryable() -> None:
    error = ModelBehaviorError("结构化输出缺少必填字段")
    assert runtime._is_retryable_agent_error(error)
    assert runtime._agent_map_attempt_limit(
        exc=error,
        configured_max_attempts=2,
    ) == 2
    assert runtime._agent_content_attempt_budget(
        failure_kind="tool_arguments_validation",
        configured_max_attempts=2,
    ) == 3

    class SameNameButUnrelatedError(Exception):
        pass

    SameNameButUnrelatedError.__name__ = "ModelBehaviorError"
    assert not runtime._is_retryable_agent_error(
        SameNameButUnrelatedError("不应按类名误判")
    )


def test_agent_retry_feedback_keeps_complete_aggregated_coverage_ids() -> None:
    fact_ids = [f"FACT-{index:03d}" for index in range(1, 81)]
    design_ids = [f"TD-001-001-{index:03d}" for index in range(1, 41)]
    message = (
        f"生成批次未完整覆盖平台要求的事实: missing={fact_ids}；"
        f"生成批次测试设计覆盖不符合平台契约: missing={design_ids}"
    )

    feedback = runtime._agent_retry_feedback(ModelBehaviorError(message))

    assert feedback is not None
    assert "FACT-040" in feedback
    assert "TD-001-001-020" in feedback
    assert " … " not in feedback


def test_agent_map_item_retry_feedback_explains_missing_fact_relocation() -> None:
    error = ModelBehaviorError(
        "agent_map 单项结果校验失败: 修复批次仍未覆盖要求事实: "
        "['DOC269-P0004-F002']"
    )

    feedback = runtime._agent_map_item_retry_feedback(
        previous_feedback=None,
        exc=error,
        item_input={
            "authoritative_facts": [
                {
                    "fact_id": "DOC269-P0004-F002",
                    "assertion": "错题本点击后进入同步作文的错题本列表页",
                }
            ]
        },
    )

    assert feedback is not None
    assert "DOC269-P0004-F002=错题本点击后进入同步作文的错题本列表页" in feedback
    assert "必须把事实迁移" in feedback


def test_generation_retry_feedback_includes_missing_fact_assertion() -> None:
    feedback = runtime._agent_map_item_retry_feedback(
        previous_feedback=None,
        exc=ModelBehaviorError(
            "agent_map 单项结果校验失败: 生成批次未完整覆盖平台要求的事实: "
            "missing=['DOC269-P0006-F269-035']"
        ),
        item_input={
            "authoritative_facts": [
                {
                    "fact_id": "DOC269-P0006-F269-035",
                    "assertion": "课程列表展示同步作文入口",
                }
            ]
        },
    )

    assert feedback is not None
    assert "DOC269-P0006-F269-035=课程列表展示同步作文入口" in feedback


def test_generation_retry_feedback_includes_missing_design_intent() -> None:
    feedback = runtime._agent_map_item_retry_feedback(
        previous_feedback="上次输出未通过平台校验：旧错误 TD-OLD",
        exc=ModelBehaviorError(
            "agent_map 单项结果校验失败: 生成批次测试设计覆盖不符合平台契约: "
            "missing=['TD-003-003-002'], invalid=[]"
        ),
        item_input={
            "plan": {
                "test_design_items": [
                    {
                        "test_design_item_id": "TD-003-003-002",
                        "coverage_intent": "点击回复打开窗口",
                    }
                ]
            }
        },
    )

    assert feedback is not None
    assert "TD-003-003-002=点击回复打开窗口" in feedback
    assert "TD-OLD" not in feedback


def test_new_content_validation_replaces_stale_retry_feedback() -> None:
    previous = runtime._agent_map_item_retry_feedback(
        previous_feedback=None,
        exc=ModelBehaviorError("第一次缺少 TD-001"),
        item_input={},
    )
    current = runtime._agent_map_item_retry_feedback(
        previous_feedback=previous,
        exc=ModelBehaviorError("第二次仅缺少 TD-002"),
        item_input={},
    )

    assert current is not None
    assert "TD-002" in current
    assert "TD-001" not in current


def test_final_review_retry_feedback_accumulates_requirements_and_missing_fact() -> None:
    item_input = {
        "repair_requirements": [
            "补充前置条件，明确区分有作品与无作品状态。",
            "不得降低修复前已经通过的确定性事实覆盖。",
        ],
        "authoritative_facts": [
            {
                "fact_id": "DOC269-P0008-F0016",
                "assertion": "缺省状态显示缺省图",
            }
        ],
    }
    first_feedback = runtime._agent_map_item_retry_feedback(
        previous_feedback=None,
        exc=ModelBehaviorError(
            "agent_map 单项结果校验失败: 终审修复未产生任何实质变化"
        ),
        item_input=item_input,
    )
    second_feedback = runtime._agent_map_item_retry_feedback(
        previous_feedback=first_feedback,
        exc=ModelBehaviorError(
            "agent_map 单项结果校验失败: 修复批次仍未覆盖要求事实: "
            "['DOC269-P0008-F0016']"
        ),
        item_input=item_input,
    )

    assert first_feedback is not None
    assert "明确区分有作品与无作品状态" in first_feedback
    assert "不能原样返回" in first_feedback
    assert second_feedback is not None
    assert "明确区分有作品与无作品状态" in second_feedback
    assert "DOC269-P0008-F0016=缺省状态显示缺省图" in second_feedback


def test_parallel_agent_map_stops_repeating_identical_invalid_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "final_review_repairs",
            "node_type": "agent_map",
            "reference_key": "repairer",
            "max_attempts": 3,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_input = {
        "authoritative_facts": [
            {
                "fact_id": "DOC269-P0004-F002",
                "assertion": "错题本点击后进入同步作文的错题本列表页",
            }
        ]
    }
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [item_input]},
    )
    repo.node_runs.append(node_run)
    received_feedback: list[str | None] = []
    received_inputs: list[dict[str, Any]] = []

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        received_feedback.append(arguments.get("retry_feedback"))
        received_inputs.append(dict(arguments.get("item_input") or {}))
        return AgentExecutionResult(
            output={"test_cases": []},
            final_text='{"test_cases":[]}',
            last_agent_name="终审修复智能体",
            usage={
                "requests": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        )

    def postprocess(**_arguments: Any) -> dict[str, Any]:
        raise ModelBehaviorError(
            "agent_map 单项结果校验失败: 修复批次仍未覆盖要求事实: "
            "['DOC269-P0004-F002']"
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_postprocess_agent_map_output", postprocess)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    with pytest.raises(ModelBehaviorError, match="完全相同"):
        runtime._execute_agent_map(
            repo=repo,
            run=run,
            node=node,
            node_run=node_run,
            definition=SimpleNamespace(id=9, name="终审修复智能体", runtime_config={}),
            model_metadata={"name": "test-model", "route": "review", "source": "评审模型路由"},
            tools=[],
            execution_context=ToolExecutionContext(
                db=None,
                user_id=1,
                project_id=2,
                run_id=run.id,
                node_key=node.node_key,
                run_input={},
                artifacts={},
            ),
            node_input={"items": [item_input]},
            previous=None,
        )

    assert len(received_feedback) == 3
    assert "必须把事实迁移" in str(received_feedback[1])
    assert received_inputs[1]["_platform_repair"]["mode"] == "minimal_patch"
    assert received_inputs[2]["_platform_repair"]["mode"] == "full_regeneration"
    assert "candidate_output" not in received_inputs[2]["_platform_repair"]
    assert (
        received_inputs[2]["_platform_repair"]["candidate_rejection"]["reason"]
        == "repeated_invalid_after_minimal_patch"
    )
    assert len(node_run.sdk_state["items"][0]["validation_diagnostics"]) == 3


def test_structured_output_errors_have_stable_failure_categories_and_budgets() -> None:
    damaged = '{"test_cases":["' + ("\t" * 40)
    syntax_error = StructuredOutputJSONError(
        output_text=damaged,
        json_error=json.JSONDecodeError("Unterminated string", damaged, 15),
    )
    validation_error = StructuredOutputValidationError(
        output={"test_cases": []},
        output_schema={
            "type": "object",
            "properties": {
                "test_cases": {"type": "array"},
                "case_fact_bindings": {"type": "array"},
            },
            "required": ["test_cases", "case_fact_bindings"],
        },
        validation_error=JSONSchemaValidationError(
            "'case_fact_bindings' is a required property",
            validator="required",
            validator_value=["test_cases", "case_fact_bindings"],
            instance={"test_cases": []},
            schema={"required": ["test_cases", "case_fact_bindings"]},
            schema_path=["required"],
        ),
    )

    assert runtime._agent_failure_kind(syntax_error) == "output_degeneration"
    assert runtime._agent_failure_kind(validation_error) == "output_validation"
    assert runtime._agent_failure_kind(
        ModelBehaviorError(
            "agent_map 单项结果校验失败: "
            "postprocessor=testing.postprocess_generation_batch_item; 事实覆盖不完整"
        )
    ) == "postprocess_validation"
    assert runtime._agent_content_attempt_budget(
        failure_kind="output_degeneration",
        configured_max_attempts=2,
    ) == 3
    assert runtime._agent_content_attempt_budget(
        failure_kind="output_validation",
        configured_max_attempts=2,
    ) == 2
    repair = runtime._agent_map_repair_context(
        result=None,
        exc=validation_error,
        validation_feedback="缺少 case_fact_bindings",
    )
    assert repair is not None
    assert repair["mode"] == "minimal_patch"
    assert repair["candidate_output"] == {"test_cases": []}


def test_structured_output_retry_feedback_lists_all_schema_violations() -> None:
    schema = {
        "type": "object",
        "properties": {
            "test_cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "platform_id": {
                            "type": "string",
                            "x-platform-derived": True,
                        },
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string"},
                                    "expected": {"type": "string"},
                                },
                                "required": ["action", "expected"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["title", "steps"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["test_cases"],
        "additionalProperties": False,
    }
    candidate = {
        "test_cases": [
            {"steps": [{"action": "执行动作", "unexpected": "多余字段"}]}
        ],
        "unexpected_root": True,
    }
    validator = runtime.validator_for(schema)(schema)
    first_error = next(validator.iter_errors(candidate))
    error = StructuredOutputValidationError(
        output=candidate,
        output_schema=schema,
        validation_error=first_error,
    )

    feedback = runtime._agent_retry_feedback(error)

    assert feedback is not None
    assert "同一候选发现 4 个结构错误" in feedback
    assert "test_cases.0.title" in feedback
    assert "test_cases.0.steps.0.expected" in feedback
    assert "顶层只允许字段：test_cases" in feedback
    assert "platform_id" not in feedback
    assert "每个步骤必须包含：action, expected" in feedback


def test_structured_output_retry_feedback_deduplicates_required_fields() -> None:
    required_fields = [f"field_{index}" for index in range(6)]
    schema = {
        "type": "object",
        "properties": {field: {"type": "string"} for field in required_fields},
        "required": required_fields,
        "additionalProperties": False,
    }
    candidate: dict[str, Any] = {}
    validator = runtime.validator_for(schema)(schema)
    violations = list(validator.iter_errors(candidate))
    assert len(violations) == len(required_fields)

    error = StructuredOutputValidationError(
        output=candidate,
        output_schema=schema,
        validation_error=violations[0],
    )

    feedback = runtime._agent_retry_feedback(error)

    assert feedback is not None
    assert "同一候选发现 6 个结构错误" in feedback
    for field in required_fields[:5]:
        assert feedback.count(f"<root>.{field}: 缺少必填字段") == 1
    assert "其余 1 个结构错误未展开" in feedback


def test_generation_repair_context_targets_only_contract_slots_named_by_feedback() -> None:
    candidate = {
        "test_cases": [
            {"title": "用例一"},
            {"title": "用例二"},
            {"title": "用例三"},
        ]
    }
    item_input = {
        "case_fact_contract": {
            "coverage_slots": [
                {
                    "case_id": "TC-001",
                    "required_fact_ids": ["F-001"],
                    "required_test_design_item_ids": ["TD-001"],
                },
                {
                    "case_id": "TC-002",
                    "required_fact_ids": ["F-002"],
                    "required_test_design_item_ids": ["TD-002"],
                },
                {
                    "case_id": "TC-003",
                    "required_fact_ids": ["F-003"],
                    "required_test_design_item_ids": ["TD-003"],
                },
            ]
        }
    }

    repair = runtime._agent_map_repair_context(
        result=SimpleNamespace(output=candidate),
        validation_feedback="生成批次未完整覆盖平台要求的事实: missing=['F-002']",
        item_input=item_input,
    )

    assert repair is not None
    assert repair["mode"] == "minimal_patch"
    assert repair["repair_targets"] == [
        {
            "case_id": "TC-002",
            "test_cases_array_index": 1,
            "missing_fact_ids": ["F-002"],
            "missing_test_design_item_ids": [],
        }
    ]
    assert repair["protected_case_ids"] == ["TC-001", "TC-003"]
    assert "只能修改这些槽位" in repair["instruction"]
    assert repair["candidate_output"] == candidate


def test_generation_repair_restores_every_protected_case_slot() -> None:
    candidate = {
        "test_cases": [
            {"title": "保留用例一", "steps": [{"action": "原动作一"}]},
            {"title": "待修用例", "steps": [{"action": "原动作二"}]},
            {"title": "保留用例三", "steps": [{"action": "原动作三"}]},
        ]
    }
    repaired = runtime._restore_protected_repair_slots(
        item_output={
            "test_cases": [
                {"title": "错误改写一", "steps": []},
                {"title": "已修用例", "steps": [{"action": "修复动作"}]},
                {"title": "错误改写三", "steps": []},
            ]
        },
        repair_context={
            "mode": "minimal_patch",
            "candidate_output": candidate,
            "repair_targets": [
                {
                    "case_id": "TC-002",
                    "test_cases_array_index": 1,
                }
            ],
            "protected_case_ids": ["TC-001", "TC-003"],
        },
    )

    assert repaired["test_cases"] == [
        candidate["test_cases"][0],
        {"title": "已修用例", "steps": [{"action": "修复动作"}]},
        candidate["test_cases"][2],
    ]


def test_container_type_damage_uses_full_regeneration_without_candidate() -> None:
    schema = {
        "type": "object",
        "properties": {
            "test_cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            }
        },
        "required": ["test_cases"],
    }
    candidate = {
        "test_cases": [
            {"title": "验证第28页存在条目"},
            "priority",
        ]
    }
    validator = runtime.validator_for(schema)(schema)
    first_error = next(validator.iter_errors(candidate))
    validation_error = StructuredOutputValidationError(
        output=candidate,
        output_schema=schema,
        validation_error=first_error,
    )

    repair = runtime._agent_map_repair_context(
        result=None,
        exc=validation_error,
        validation_feedback="数组元素必须是对象",
    )

    assert repair is not None
    assert repair["mode"] == "full_regeneration"
    assert "candidate_output" not in repair
    assert repair["candidate_rejection"]["reason"] == "container_type_mismatch"
    assert runtime._agent_failure_kind(validation_error) == "output_degeneration"
    assert runtime._agent_content_attempt_budget(
        failure_kind=runtime._agent_failure_kind(validation_error),
        configured_max_attempts=2,
    ) == 3
    event_fields = runtime._repair_retry_event_fields(repair)
    assert event_fields == {
        "repair_mode": "full_regeneration",
        "has_repair_candidate": False,
        "candidate_rejection_reason": "container_type_mismatch",
    }


def test_multiple_schema_violations_use_full_regeneration() -> None:
    schema = {
        "type": "object",
        "properties": {
            "test_case": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "steps": {"type": "array"},
                    "test_design_item_ids": {"type": "array"},
                },
                "required": ["title", "steps", "test_design_item_ids"],
            }
        },
        "required": ["test_case"],
    }
    candidate = {"test_case": {"title": "残缺用例"}}
    validator = runtime.validator_for(schema)(schema)
    first_error = next(validator.iter_errors(candidate))
    validation_error = StructuredOutputValidationError(
        output=candidate,
        output_schema=schema,
        validation_error=first_error,
    )

    repair = runtime._agent_map_repair_context(
        result=None,
        exc=validation_error,
        validation_feedback="用例缺少多个必要字段",
    )

    assert repair is not None
    assert repair["mode"] == "full_regeneration"
    assert "candidate_output" not in repair
    assert repair["candidate_rejection"]["reason"] == "multiple_schema_violations"
    assert repair["candidate_rejection"]["violation_count"] == 2
    assert runtime._agent_failure_kind(validation_error) == "output_degeneration"


def test_wrapped_tool_arguments_error_is_retryable_model_output_failure() -> None:
    error = _wrapped_tool_arguments_error()

    assert runtime._is_retryable_agent_error(error)
    assert runtime._agent_failure_kind(error) == "json_syntax"
    assert runtime._agent_map_attempt_limit(
        exc=error,
        configured_max_attempts=2,
    ) == 2
    assert "工具参数不是合法 JSON" in str(runtime._agent_retry_feedback(error))
    diagnostic = runtime._agent_error_diagnostic(error)
    assert diagnostic is not None
    assert diagnostic["tool_key"] == "submit_source_semantics"
    assert diagnostic["arguments_chars"] == 26


def test_wrapped_tool_argument_schema_error_is_retryable_model_output_failure() -> None:
    error = _wrapped_tool_arguments_validation_error()

    assert runtime._is_retryable_agent_error(error)
    assert runtime._agent_failure_kind(error) == "tool_arguments_validation"
    assert runtime._agent_map_attempt_limit(
        exc=error,
        configured_max_attempts=2,
    ) == 2
    feedback = runtime._agent_retry_feedback(error)
    assert "authoritative_facts" in str(feedback)
    assert "Failed validating" not in str(feedback)
    diagnostic = runtime._agent_error_diagnostic(error)
    assert diagnostic is not None
    assert diagnostic["validation_keyword"] == "required"
    assert diagnostic["missing_fields"] == ["authoritative_facts"]


def test_wrapped_tool_output_schema_error_is_non_retryable_platform_failure() -> None:
    error = _wrapped_tool_output_validation_error()

    assert not runtime._is_retryable_agent_error(error)
    assert runtime._agent_failure_kind(error) == "tool_contract_violation"
    assert runtime._agent_retry_feedback(error) is None
    diagnostic = runtime._agent_error_diagnostic(error)
    assert diagnostic is not None
    assert diagnostic["tool_key"] == "submit_source_semantics"
    assert diagnostic["validation_keyword"] == "required"
    assert diagnostic["missing_fields"] == ["authoritative_facts"]
    assert diagnostic["output_excerpt"] == "{}"


def test_agent_retry_feedback_prioritizes_field_validation_error() -> None:
    class RetryFeedbackContract(BaseModel):
        differences: list[str]

    with pytest.raises(ValidationError) as captured:
        RetryFeedbackContract.model_validate({"differences": ""})

    try:
        raise ModelBehaviorError("模型输出：" + "x" * 5000) from captured.value
    except ModelBehaviorError as model_error:
        feedback = runtime._agent_retry_feedback(model_error)

    assert feedback is not None
    assert "字段 differences" in feedback
    assert "valid list" in feedback
    assert len(feedback) < 1100


def test_server_output_schema_capability_error_uses_two_attempt_budget() -> None:
    error = _HTTPError(400, "This response_format type is unavailable now.")

    assert runtime._is_server_output_schema_unsupported(error)
    assert runtime._is_retryable_agent_error(error)
    assert runtime._agent_map_attempt_limit(
        exc=error,
        configured_max_attempts=1,
    ) == 2
    transient = BadRequestError(
        "400001 We encountered some issues",
        response=Response(400, request=Request("POST", "https://example.test/v1/chat/completions")),
        body={"code": 400001},
    )
    assert not runtime._is_server_output_schema_unsupported(transient)
    assert runtime._agent_map_attempt_limit(
        exc=transient,
        configured_max_attempts=2,
    ) == 4
    assert runtime._agent_failure_kind(transient) == "upstream_transient"
    assert runtime._is_concurrency_pressure_failure("upstream_transient") is True
    assert runtime._is_concurrency_pressure_failure("upstream_server") is True
    assert runtime._is_concurrency_pressure_failure("postprocess_validation") is False
    assert runtime._is_concurrency_pressure_failure("json_syntax") is False


def test_agent_map_can_explicitly_accept_empty_optional_items() -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "supplement",
            "node_type": "agent_map",
            "reference_key": "generator",
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "allow_empty": True,
            },
        }
    )
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": []},
    )

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(name="可选补充智能体"),
        model_metadata={"name": "test-model", "route": "main", "source": "测试模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": []},
        previous=None,
    )

    assert output == {"items": [], "completed_count": 0, "total_count": 0}
    assert sdk_state["usage"] == {}


def test_agent_map_runs_independent_instances_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    run.run_context = {
        "execution_limits": {
            "max_requests": 1,
            "max_input_tokens": 999999,
            "max_output_tokens": 999999,
            "max_total_tokens": 999999,
        }
    }
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "source_semantics",
            "node_type": "agent_map",
            "reference_key": "source_analyst",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 3,
            },
        }
    )
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [{"page": index + 1} for index in range(6)]},
    )
    repo.node_runs.append(node_run)
    lock = threading.Lock()
    active = 0
    peak = 0

    def execute_instance(**arguments: Any) -> AgentExecutionResult:
        nonlocal active, peak
        instance_id = str(arguments["instance_id"])
        assert run.run_context["agent_instance_quota_usage"][instance_id][
            "attempted_requests"
        ] == 1
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        page = int(arguments["item_input"]["page"])
        return AgentExecutionResult(
            output={"page": page},
            final_text=f'{{"page":{page}}}',
            last_agent_name="并发来源分析实例",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(
            id=9,
            name="来源分析智能体",
            runtime_config={"max_turns": 1},
        ),
        model_metadata={"name": "test-model", "route": "vision", "source": "视觉模型路由"},
        tools=[
            SimpleNamespace(
                tool_key="submit_source_semantics",
                handler_key="testing.submit_source_semantics",
            )
        ],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": [{"page": index + 1} for index in range(6)]},
        previous=None,
    )

    assert peak >= 2
    assert [item["item_index"] for item in output["items"]] == list(range(6))
    assert [item["output"]["page"] for item in output["items"]] == list(range(1, 7))
    assert sdk_state["parallelism"] == {
        "max_concurrency": 3,
        "min_concurrency": 2,
        "effective_concurrency": 3,
        "pressure_failures": 0,
        "active_instances": 0,
        "retry_waiting_instances": 0,
        "completed_instances": 6,
        "total_instances": 6,
    }
    assert len({item["instance_id"] for item in sdk_state["items"]}) == 6
    ledgers = run.run_context["agent_instance_quota_usage"]
    assert sorted(ledgers) == [
        f"source_semantics-instance-{index:03d}"
        for index in range(1, 7)
    ]
    assert all(ledger["attempted_requests"] == 1 for ledger in ledgers.values())


@pytest.mark.parametrize(
    ("pressure_threshold", "expected_concurrency"),
    [(1, 3), (2, 4)],
)
def test_agent_map_reduces_concurrency_only_after_pressure_threshold(
    monkeypatch: pytest.MonkeyPatch,
    pressure_threshold: int,
    expected_concurrency: int,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "source_text",
            "node_type": "agent_map",
            "reference_key": "source_analyst",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 4,
            },
        }
    )
    item_inputs = [{"page": index} for index in range(1, 9)]
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": item_inputs},
    )
    repo.node_runs.append(node_run)
    attempts: dict[int, int] = {}

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        page = int(arguments["item_input"]["page"])
        attempts[page] = attempts.get(page, 0) + 1
        if page == 1 and attempts[page] == 1:
            await asyncio.sleep(0)
            raise RuntimeError("upstream pressure")
        await asyncio.sleep(0.01)
        return AgentExecutionResult(
            output={"page": page},
            final_text=json.dumps({"page": page}),
            last_agent_name="来源分析智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_agent_failure_kind", lambda _exc: "upstream_server")
    monkeypatch.setattr(runtime, "_is_retryable_agent_error", lambda _exc: True)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)
    monkeypatch.setattr(
        runtime.settings,
        "AGENT_MAP_CONCURRENCY_RECOVERY_SUCCESSES",
        100,
    )
    monkeypatch.setattr(
        runtime.settings,
        "AGENT_MAP_CONCURRENCY_PRESSURE_FAILURES",
        pressure_threshold,
    )

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(id=9, name="来源分析智能体", runtime_config={}),
        model_metadata={"name": "test-model", "route": "turbo", "source": "快速模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": item_inputs},
        previous=None,
    )

    assert [item["output"]["page"] for item in output["items"]] == list(range(1, 9))
    assert attempts[1] == 2
    assert sdk_state["parallelism"]["max_concurrency"] == 4
    assert sdk_state["parallelism"]["effective_concurrency"] == expected_concurrency
    adjustment_events = [
        event for event in repo.events
        if event["event_type"] == "map_concurrency_adjusted"
    ]
    if pressure_threshold == 1:
        assert sdk_state["concurrency_adjustments"] == [
            {
                "from": 4,
                "to": 3,
                "reason": "upstream_pressure",
                "failure_kind": "upstream_server",
                "completed_instances": 0,
            }
        ]
        assert adjustment_events[0]["payload"]["to"] == 3
    else:
        assert sdk_state["concurrency_adjustments"] == []
        assert adjustment_events == []


def test_agent_map_switches_to_declared_fallback_route_after_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "source_text",
            "node_type": "agent_map",
            "reference_key": "source_analyst",
            "max_attempts": 2,
                "map_config": {
                    "items_key": "items",
                    "output_key": "items",
                    "max_concurrency": 2,
                },
        }
    )
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [{"page": 1}, {"page": 2}]},
    )
    repo.node_runs.append(node_run)
    routes: dict[int, list[str | None]] = {1: [], 2: []}

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        page = int(arguments["item_input"]["page"])
        routes[page].append(arguments.get("model_route_override"))
        if page == 1 and len(routes[page]) <= 2:
            raise RuntimeError("上游瞬态失败")
        return AgentExecutionResult(
            output={"page": page},
            final_text=json.dumps({"page": page}),
            last_agent_name="来源分析智能体",
            usage={"requests": 1, "input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_agent_failure_kind", lambda _exc: "upstream_transient")
    monkeypatch.setattr(runtime, "_is_retryable_agent_error", lambda _exc: True)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(
            id=9,
            name="来源分析智能体",
            runtime_config={
                "model_route": "turbo",
                "transient_fallback_model_route": "main",
                "transient_fallback_after_failures": 2,
            },
        ),
        model_metadata={"name": "fast-model", "route": "turbo", "source": "快速模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": [{"page": 1}, {"page": 2}]},
        previous=None,
    )

    assert output["completed_count"] == 2
    assert routes[1] == [None, None, "main"]
    assert routes[2] == [None]
    state = sdk_state["items"][0]
    assert state["model_route"] == "main"
    assert state["model_route_override"] == "main"
    assert state["transient_failure_count"] == 2
    assert state["route_health_failure_count"] == 2
    assert [item["model_route"] for item in state["retry_history"]] == [
        "turbo",
        "turbo",
    ]


def test_agent_map_switches_route_after_mixed_json_and_timeout_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "source_text",
            "node_type": "agent_map",
            "reference_key": "source_analyst",
            "max_attempts": 2,
                "map_config": {
                    "items_key": "items",
                    "output_key": "items",
                    "max_concurrency": 2,
                },
        }
    )
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [{"page": 20}, {"page": 21}]},
    )
    repo.node_runs.append(node_run)
    routes: dict[int, list[str | None]] = {20: [], 21: []}

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        page = int(arguments["item_input"]["page"])
        routes[page].append(arguments.get("model_route_override"))
        if page == 20 and len(routes[page]) == 1:
            raise ModelBehaviorError("Invalid JSON when parsing truncated output")
        if page == 20 and len(routes[page]) == 2:
            raise TimeoutError("Agent 调用超过硬超时 90 秒")
        return AgentExecutionResult(
            output={"page": page},
            final_text=json.dumps({"page": page}),
            last_agent_name="来源分析智能体",
            usage={"requests": 1, "input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(
            id=9,
            name="来源分析智能体",
            runtime_config={
                "model_route": "turbo",
                "transient_fallback_model_route": "main",
                "transient_fallback_after_failures": 2,
            },
        ),
        model_metadata={"name": "fast-model", "route": "turbo", "source": "快速模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": [{"page": 20}, {"page": 21}]},
        previous=None,
    )

    assert output["completed_count"] == 2
    assert routes[20] == [None, None, "main"]
    assert routes[21] == [None]
    state = sdk_state["items"][0]
    assert state["model_route_override"] == "main"
    assert state["transient_failure_count"] == 1
    assert state["route_health_failure_count"] == 2
    assert state["content_failure_counts"] == {"json_syntax": 1}


def test_route_health_count_recovers_from_legacy_mixed_failure_state() -> None:
    assert runtime._persisted_route_health_failure_count(
        {
            "transient_failure_count": 1,
            "content_failure_counts": {
                "json_syntax": 3,
                "output_validation": 2,
            },
        }
    ) == 4


def test_agent_map_prioritizes_due_retry_before_untouched_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "generation",
            "node_type": "agent_map",
            "reference_key": "generator",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_inputs = [{"batch": index} for index in range(1, 4)]
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": item_inputs},
    )
    repo.node_runs.append(node_run)
    call_order: list[int] = []

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        batch = int(arguments["item_input"]["batch"])
        call_order.append(batch)
        if batch == 1 and call_order.count(1) == 1:
            raise ModelBehaviorError("智能体最终输出不是合法 JSON")
        return AgentExecutionResult(
            output={"batch": batch},
            final_text=json.dumps({"batch": batch}),
            last_agent_name="用例生成智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    output, _ = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(id=9, name="用例生成智能体", runtime_config={}),
        model_metadata={"name": "test-model", "route": "main", "source": "主模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": item_inputs},
        previous=None,
    )

    assert call_order == [1, 2, 1, 3]
    assert [item["output"]["batch"] for item in output["items"]] == [1, 2, 3]


def test_parallel_agent_map_rejects_tools_without_safe_declaration() -> None:
    assert runtime._unsafe_parallel_tool_keys(
        [
            SimpleNamespace(
                tool_key="submit_source_semantics",
                handler_key="testing.submit_source_semantics",
            )
        ]
    ) == []
    assert runtime._unsafe_parallel_tool_keys(
        [
            SimpleNamespace(
                tool_key="resolve_requirement_evidence",
                handler_key="testing.resolve_requirement_evidence",
            )
        ]
    ) == ["resolve_requirement_evidence"]


def test_parallel_agent_instance_loads_its_own_bound_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = SimpleNamespace(id=29, enabled=True)
    bound_tools = [SimpleNamespace(tool_key="submit_source_semantics")]
    captured: dict[str, Any] = {}

    class WorkerDB:
        closed = False

        def get(self, model: object, definition_id: int) -> object:
            assert model is runtime.AgentDefinition
            assert definition_id == definition.id
            return definition

        def close(self) -> None:
            self.closed = True

    worker_db = WorkerDB()
    monkeypatch.setattr(runtime, "SessionLocal", lambda: worker_db)
    monkeypatch.setattr(
        runtime,
        "AgentPlatformRepository",
        lambda db: SimpleNamespace(
            list_agent_tools=lambda definition_id: bound_tools,
        ),
    )

    async def run_agent_async(**arguments: Any) -> AgentExecutionResult:
        captured.update(arguments)
        return AgentExecutionResult(
            output={"authoritative_facts": []},
            final_text='{"authoritative_facts":[]}',
            last_agent_name="视觉来源分析智能体",
            usage={},
        )

    monkeypatch.setattr(runtime, "run_agent_async", run_agent_async)
    execution_context = ToolExecutionContext(
        db=None,
        user_id=1,
        project_id=2,
        run_id=10,
        node_key="source_vision",
        run_input={"requirement_doc_id": 259},
        artifacts={"source": "真实文档"},
    )

    result = asyncio.run(
        runtime._run_parallel_agent_instance(
            definition_id=definition.id,
            execution_context=execution_context,
            item_input={"page_number": 12},
            instance_id="source_vision-instance-001",
            request_timeout_seconds=180,
        )
    )

    assert result.output == {"authoritative_facts": []}
    assert captured["tool_definitions"] is bound_tools
    assert captured["execution_context"].node_key == "source_vision-instance-001"
    assert worker_db.closed is True


def test_parallel_agent_map_passes_validation_error_to_item_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    run.run_context = {
        "execution_limits": {
            "max_requests": 2,
            "max_input_tokens": 999999,
            "max_output_tokens": 999999,
            "max_total_tokens": 999999,
        }
    }
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "source_text",
            "node_type": "agent_map",
            "reference_key": "source_analyst",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_input = {"page_number": 25}
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [item_input]},
    )
    repo.node_runs.append(node_run)
    received_feedback: list[str | None] = []
    received_inputs: list[dict[str, Any]] = []

    def execute_instance(**arguments: Any) -> AgentExecutionResult:
        received_feedback.append(arguments.get("retry_feedback"))
        received_inputs.append(dict(arguments["item_input"]))
        attempt = len(received_feedback)
        return AgentExecutionResult(
            output={"attempt": attempt},
            final_text=f'{{"attempt":{attempt}}}',
            last_agent_name="来源分析智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    def postprocess(**arguments: Any) -> dict[str, Any]:
        if arguments["item_output"]["attempt"] == 1:
            raise ModelBehaviorError(
                "source_anchor.source_span 坐标无效: start=387, end=387"
            )
        return dict(arguments["item_output"])

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_postprocess_agent_map_output", postprocess)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(id=9, name="来源分析智能体", runtime_config={}),
        model_metadata={"name": "test-model", "route": "turbo", "source": "快速模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": [item_input]},
        previous=None,
    )

    assert received_feedback[0] is None
    assert "start=387, end=387" in str(received_feedback[1])
    assert "_platform_repair" not in received_inputs[0]
    repair = received_inputs[1]["_platform_repair"]
    assert repair["mode"] == "minimal_patch"
    assert repair["candidate_output"] == {"attempt": 1}
    assert "start=387, end=387" in repair["validation_feedback"]
    assert "不得从头重写" in repair["instruction"]
    assert output["items"][0]["output"] == {"attempt": 2}
    assert sdk_state["items"][0]["item_attempt"] == 2
    assert sdk_state["failure_counts"] == {"model_behavior": 1}
    assert sdk_state["parallelism"]["active_instances"] == 0
    assert sdk_state["parallelism"]["retry_waiting_instances"] == 0
    diagnostics = sdk_state["items"][0]["validation_diagnostics"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["item_attempt"] == 1
    assert diagnostics[0]["normalized_model_output_text"] == '{"attempt":1}'
    assert diagnostics[0]["output_text_chars"] == 13
    assert diagnostics[0]["output_text_truncated"] is False
    assert len(diagnostics[0]["output_text_sha256"]) == 64
    failed_events = [
        event for event in repo.events
        if event["event_type"] == "map_item_failed"
    ]
    assert failed_events[0]["payload"]["failure_kind"] == "model_behavior"
    assert failed_events[0]["payload"]["diagnostic_recorded"] is True
    retry_events = [
        event for event in repo.events
        if event["event_type"] == "map_item_retry_scheduled"
    ]
    assert retry_events[0]["payload"]["has_validation_feedback"] is True
    assert retry_events[0]["payload"]["repair_mode"] == "minimal_patch"
    assert retry_events[0]["payload"]["has_repair_candidate"] is True
    assert retry_events[0]["payload"]["candidate_rejection_reason"] == ""


@pytest.mark.parametrize(
    ("error_factory", "failure_kind", "feedback_marker"),
    [
        (_wrapped_tool_arguments_error, "json_syntax", "工具参数不是合法 JSON"),
        (
            _wrapped_tool_arguments_validation_error,
            "tool_arguments_validation",
            "authoritative_facts",
        ),
    ],
    ids=["json-syntax", "schema-validation"],
)
def test_parallel_agent_map_retries_wrapped_tool_argument_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_factory: Any,
    failure_kind: str,
    feedback_marker: str,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "source_vision",
            "node_type": "agent_map",
            "reference_key": "source_analyst",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_input = {"page_number": 28}
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [item_input]},
    )
    repo.node_runs.append(node_run)
    received_feedback: list[str | None] = []

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        received_feedback.append(arguments.get("retry_feedback"))
        if len(received_feedback) == 1:
            raise error_factory()
        return AgentExecutionResult(
            output={"authoritative_facts": []},
            final_text='{"authoritative_facts":[]}',
            last_agent_name="来源分析智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(id=9, name="来源分析智能体", runtime_config={}),
        model_metadata={"name": "test-model", "route": "vision", "source": "视觉模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": [item_input]},
        previous=None,
    )

    assert output["completed_count"] == 1
    assert len(received_feedback) == 2
    assert feedback_marker in str(received_feedback[1])
    assert sdk_state["failure_counts"] == {failure_kind: 1}
    failed_event = next(
        event for event in repo.events if event["event_type"] == "map_item_failed"
    )
    assert failed_event["payload"]["retryable"] is True
    assert failed_event["payload"]["failure_kind"] == failure_kind
    assert failed_event["payload"]["error_diagnostic"]["tool_key"] == (
        "submit_source_semantics"
    )


def test_parallel_agent_map_gives_distinct_content_failures_separate_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "generation",
            "node_type": "agent_map",
            "reference_key": "generator",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_input = {"batch": {"batch_id": "M005-B002"}}
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [item_input]},
    )
    repo.node_runs.append(node_run)
    received_feedback: list[str | None] = []

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        received_feedback.append(arguments.get("retry_feedback"))
        attempt = len(received_feedback)
        if attempt == 1:
            schema = {
                "type": "object",
                "properties": {"attempt": {"type": "integer"}},
                "required": ["attempt", "test_cases"],
            }
            raise StructuredOutputValidationError(
                output={"attempt": attempt},
                output_schema=schema,
                validation_error=JSONSchemaValidationError(
                    "'test_cases' is a required property",
                    validator="required",
                    validator_value=["attempt", "test_cases"],
                    instance={"attempt": attempt},
                    schema=schema,
                    schema_path=["required"],
                ),
            )
        return AgentExecutionResult(
            output={"attempt": attempt},
            final_text=f'{{"attempt":{attempt}}}',
            last_agent_name="用例生成智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    def postprocess(**arguments: Any) -> dict[str, Any]:
        if arguments["item_output"]["attempt"] == 2:
            raise ModelBehaviorError(
                "agent_map 单项结果校验失败: "
                "postprocessor=testing.postprocess_generation_batch_item; "
                "生成批次未完整覆盖平台要求的事实"
            )
        return dict(arguments["item_output"])

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_postprocess_agent_map_output", postprocess)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(id=9, name="用例生成智能体", runtime_config={}),
        model_metadata={"name": "test-model", "route": "main", "source": "主模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": [item_input]},
        previous=None,
    )

    assert output["items"][0]["output"] == {"attempt": 3}
    assert len(received_feedback) == 3
    assert "test_cases" in str(received_feedback[1])
    assert "未完整覆盖" in str(received_feedback[2])
    assert sdk_state["failure_counts"] == {
        "output_validation": 1,
        "postprocess_validation": 1,
    }
    assert sdk_state["items"][0]["content_failure_counts"] == {
        "output_validation": 1,
        "postprocess_validation": 1,
    }
    retry_events = [
        event for event in repo.events
        if event["event_type"] == "map_item_retry_scheduled"
    ]
    assert [event["payload"]["next_attempt"] for event in retry_events] == [2, 3]


def test_parallel_agent_map_allows_third_attempt_after_repeated_structure_degeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "generation",
            "node_type": "agent_map",
            "reference_key": "generator",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_input = {"batch": {"batch_id": "M001-B004"}}
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [item_input]},
    )
    repo.node_runs.append(node_run)
    received_inputs: list[dict[str, Any]] = []
    schema = {
        "type": "object",
        "properties": {
            "test_cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["test_cases"],
        "additionalProperties": False,
    }

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        received_inputs.append(dict(arguments["item_input"]))
        attempt = len(received_inputs)
        if attempt <= 2:
            candidate = {
                "test_cases": [
                    {"title": "验证第28页存在条目"},
                    "priority",
                    "preconditions",
                ]
            }
            validator = runtime.validator_for(schema)(schema)
            raise StructuredOutputValidationError(
                output=candidate,
                output_schema=schema,
                validation_error=next(validator.iter_errors(candidate)),
            )
        output = {"test_cases": [{"title": "验证第28页存在条目"}]}
        return AgentExecutionResult(
            output=output,
            final_text=json.dumps(output, ensure_ascii=False),
            last_agent_name="用例生成智能体",
            usage={
                "requests": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(id=9, name="用例生成智能体", runtime_config={}),
        model_metadata={"name": "test-model", "route": "main", "source": "主模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": [item_input]},
        previous=None,
    )

    assert output["items"][0]["output"]["test_cases"][0]["title"] == (
        "验证第28页存在条目"
    )
    assert len(received_inputs) == 3
    assert "_platform_repair" not in received_inputs[0]
    assert received_inputs[1]["_platform_repair"]["mode"] == "full_regeneration"
    assert received_inputs[2]["_platform_repair"]["mode"] == "full_regeneration"
    assert sdk_state["failure_counts"] == {"output_degeneration": 2}
    assert sdk_state["items"][0]["content_failure_counts"] == {
        "output_degeneration": 2
    }
    retry_events = [
        event
        for event in repo.events
        if event["event_type"] == "map_item_retry_scheduled"
    ]
    assert [event["payload"]["next_attempt"] for event in retry_events] == [2, 3]
    assert all(
        event["payload"]["candidate_rejection_reason"]
        == "multiple_schema_violations"
        for event in retry_events
    )


def test_parallel_agent_map_persists_cancelled_sibling_after_fatal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "source_vision",
            "node_type": "agent_map",
            "reference_key": "source_analyst",
            "max_attempts": 1,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_inputs = [{"page_number": 28}, {"page_number": 19}]
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": item_inputs},
    )
    repo.node_runs.append(node_run)
    sibling_started = asyncio.Event()
    sibling_cancelled = False

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        nonlocal sibling_cancelled
        page_number = int(arguments["item_input"]["page_number"])
        if page_number == 19:
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                sibling_cancelled = True
                raise
        await sibling_started.wait()
        raise ValueError("不可重试的确定性失败")

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)

    with pytest.raises(ValueError, match="不可重试的确定性失败"):
        runtime._execute_agent_map(
            repo=repo,
            run=run,
            node=node,
            node_run=node_run,
            definition=SimpleNamespace(id=9, name="来源分析智能体", runtime_config={}),
            model_metadata={"name": "test-model", "route": "vision", "source": "视觉模型路由"},
            tools=[],
            execution_context=ToolExecutionContext(
                db=None,
                user_id=1,
                project_id=2,
                run_id=run.id,
                node_key=node.node_key,
                run_input={},
                artifacts={},
            ),
            node_input={"items": item_inputs},
            previous=None,
        )

    assert sibling_cancelled is True
    states = {item["item_index"]: item for item in node_run.sdk_state["items"]}
    assert states[0]["status"] == "failed"
    assert states[1]["status"] == "cancelled"
    assert states[1]["cancellation_reason"] == "sibling_failed"
    assert states[1]["task_cancelled"] is True
    assert node_run.sdk_state["parallelism"]["active_instances"] == 0
    cancelled_event = next(
        event for event in repo.events if event["event_type"] == "map_item_cancelled"
    )
    assert cancelled_event["payload"]["item_index"] == 1
    assert cancelled_event["payload"]["task_cancelled"] is True


def test_parallel_agent_map_cancels_queued_retry_after_sibling_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "generation",
            "node_type": "agent_map",
            "reference_key": "generator",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_inputs = [{"batch": 1}, {"batch": 2}]
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": item_inputs},
    )
    repo.node_runs.append(node_run)
    retry_failure_returned = asyncio.Event()

    async def execute_instance(**arguments: Any) -> AgentExecutionResult:
        batch = int(arguments["item_input"]["batch"])
        if batch == 1:
            retry_failure_returned.set()
            raise ModelBehaviorError("Invalid JSON when parsing generation output")
        await retry_failure_returned.wait()
        await asyncio.sleep(0.03)
        raise ValueError("不可重试的兄弟实例失败")

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 60.0)

    with pytest.raises(ValueError, match="不可重试的兄弟实例失败"):
        runtime._execute_agent_map(
            repo=repo,
            run=run,
            node=node,
            node_run=node_run,
            definition=SimpleNamespace(id=9, name="用例生成智能体", runtime_config={}),
            model_metadata={"name": "test-model", "route": "main", "source": "主模型路由"},
            tools=[],
            execution_context=ToolExecutionContext(
                db=None,
                user_id=1,
                project_id=2,
                run_id=run.id,
                node_key=node.node_key,
                run_input={},
                artifacts={},
            ),
            node_input={"items": item_inputs},
            previous=None,
        )

    states = {item["item_index"]: item for item in node_run.sdk_state["items"]}
    assert states[0]["status"] == "cancelled"
    assert states[0]["queued_retry_cancelled"] is True
    assert states[0]["task_cancelled"] is False
    assert states[1]["status"] == "failed"
    assert node_run.sdk_state["parallelism"]["active_instances"] == 0
    queued_event = next(
        event for event in repo.events
        if event["event_type"] == "map_item_cancelled"
        and event["payload"].get("queued_retry_cancelled")
    )
    assert queued_event["payload"]["item_index"] == 0
    assert queued_event["payload"]["cancellation_requested"] is False


def test_parallel_agent_map_disables_server_schema_only_after_capability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "authority",
            "node_type": "agent_map",
            "reference_key": "authority_reviewer",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_inputs = [{"scope_id": "EV-0001"}, {"scope_id": "EV-0002"}]
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": item_inputs},
    )
    repo.node_runs.append(node_run)
    received_modes: dict[str, list[bool]] = {"EV-0001": [], "EV-0002": []}

    def execute_instance(**arguments: Any) -> AgentExecutionResult:
        scope_id = str(arguments["item_input"]["scope_id"])
        received_modes[scope_id].append(bool(arguments.get("disable_server_output_schema")))
        if scope_id == "EV-0001" and len(received_modes[scope_id]) == 1:
            raise _HTTPError(400, "This response_format type is unavailable now.")
        if scope_id == "EV-0001" and len(received_modes[scope_id]) == 2:
            raise ModelBehaviorError("decisions.0 缺少 value_policy")
        return AgentExecutionResult(
            output={"approved": True},
            final_text='{"approved":true}',
            last_agent_name="权威性评审智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    output, _ = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(id=9, name="权威性评审智能体", runtime_config={}),
        model_metadata={"name": "deepseek-v4-pro", "route": "review", "source": "评审模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": item_inputs},
        previous=None,
    )

    assert received_modes == {"EV-0001": [False, True, True], "EV-0002": [False]}
    assert output["items"][0]["output"] == {"approved": True}


def test_parallel_agent_map_keeps_content_retry_after_transient_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "source_text",
            "node_type": "agent_map",
            "reference_key": "source_analyst",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    item_inputs = [{"page_number": 7}, {"page_number": 8}]
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": item_inputs},
    )
    repo.node_runs.append(node_run)
    page7_attempts: list[str | None] = []

    def execute_instance(**arguments: Any) -> AgentExecutionResult:
        page_number = int(arguments["item_input"]["page_number"])
        if page_number == 7:
            page7_attempts.append(arguments.get("retry_feedback"))
            if len(page7_attempts) <= 3:
                raise _HTTPError(500, "临时网关错误")
            if len(page7_attempts) == 4:
                raise ModelBehaviorError("governed_value_span 超出事实来源范围")
        return AgentExecutionResult(
            output={"page_number": page_number},
            final_text="{}",
            last_agent_name="来源分析智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)
    monkeypatch.setattr(runtime, "_agent_map_retry_delay", lambda **_: 0.0)

    output, _ = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(id=9, name="来源分析智能体", runtime_config={}),
        model_metadata={"name": "test-model", "route": "turbo", "source": "快速模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": item_inputs},
        previous=None,
    )

    assert len(page7_attempts) == 5
    assert page7_attempts[:4] == [None, None, None, None]
    assert "governed_value_span" in str(page7_attempts[4])
    assert output["completed_count"] == 2


def test_validation_feedback_survives_transient_error() -> None:
    validation_error = ModelBehaviorError("最终输出缺少 phase")
    feedback = runtime._preserved_agent_retry_feedback(None, validation_error)

    assert "缺少 phase" in str(feedback)
    assert runtime._preserved_agent_retry_feedback(
        feedback,
        ConnectionError("临时连接失败"),
    ) == feedback


def test_parallel_agent_map_resumes_from_persisted_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "source_semantics",
            "node_type": "agent_map",
            "reference_key": "source_analyst",
            "max_attempts": 2,
            "map_config": {
                "items_key": "items",
                "output_key": "items",
                "max_concurrency": 2,
            },
        }
    )
    raw_items = [{"page": 1}, {"page": 2}, {"page": 3}]
    previous = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="failed",
        attempt=1,
        input_payload={"items": raw_items},
        output_payload={
            "items": [
                {
                    "item_index": 0,
                    "input_hash": runtime._payload_hash(raw_items[0]),
                    "output": {"page": 1},
                }
            ],
            "completed_count": 1,
            "total_count": 3,
        },
        sdk_state={
            "usage": {"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "items": [
                {
                    "item_index": 0,
                    "instance_id": "source_semantics-instance-001",
                    "status": "success",
                    "last_agent_name": "来源分析智能体",
                }
            ],
        },
    )
    node_run = AgentNodeRun(
        id=2,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=2,
        input_payload={"items": raw_items},
    )
    repo.node_runs.extend([previous, node_run])
    executed_pages: list[int] = []

    def execute_instance(**arguments: Any) -> AgentExecutionResult:
        page = int(arguments["item_input"]["page"])
        executed_pages.append(page)
        return AgentExecutionResult(
            output={"page": page},
            final_text=f'{{"page":{page}}}',
            last_agent_name="来源分析智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "_run_parallel_agent_instance", execute_instance)

    output, sdk_state = runtime._execute_agent_map(
        repo=repo,
        run=run,
        node=node,
        node_run=node_run,
        definition=SimpleNamespace(id=9, name="来源分析智能体", runtime_config={}),
        model_metadata={"name": "test-model", "route": "vision", "source": "视觉模型路由"},
        tools=[],
        execution_context=ToolExecutionContext(
            db=None,
            user_id=1,
            project_id=2,
            run_id=run.id,
            node_key=node.node_key,
            run_input={},
            artifacts={},
        ),
        node_input={"items": raw_items},
        previous=previous,
    )

    assert sorted(executed_pages) == [2, 3]
    assert [item["output"]["page"] for item in output["items"]] == [1, 2, 3]
    assert output["completed_count"] == 3
    assert sdk_state["parallelism"]["completed_instances"] == 3


def test_agent_map_stops_before_next_item_after_run_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "generate",
            "node_type": "agent_map",
            "reference_key": "generator",
            "max_attempts": 3,
            "map_config": {"items_key": "items", "output_key": "items"},
        }
    )
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [{"batch": 1}, {"batch": 2}]},
    )
    repo.node_runs.append(node_run)
    calls = 0

    def execute_agent(**_: Any) -> AgentExecutionResult:
        nonlocal calls
        calls += 1
        run.status = "cancelled"
        return AgentExecutionResult(
            output={"test_cases": [{"case_id": "TC-001"}]},
            final_text='{"test_cases":[{"case_id":"TC-001"}]}',
            last_agent_name="真实调用智能体",
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(runtime, "run_agent", execute_agent)

    with pytest.raises(runtime._RunCancelled, match="已取消"):
        runtime._execute_agent_map(
            repo=repo,
            run=run,
            node=node,
            node_run=node_run,
            definition=SimpleNamespace(name="真实调用智能体"),
            model_metadata={"name": "test-model", "route": "main", "source": "测试模型路由"},
            tools=[],
            execution_context=ToolExecutionContext(
                db=None,
                user_id=1,
                project_id=2,
                run_id=run.id,
                node_key=node.node_key,
                run_input={},
                artifacts={},
            ),
            node_input={"items": [{"batch": 1}, {"batch": 2}]},
            previous=None,
        )

    assert calls == 1
    assert node_run.status == "cancelled"
    assert node_run.output_payload["completed_count"] == 1
    assert node_run.output_payload["total_count"] == 2
    assert len(node_run.output_payload["items"]) == 1
    assert [item["event_type"] for item in repo.events] == [
        "map_item_started",
        "map_item_completed",
        "node_cancelled",
    ]
    assert not any(
        item["event_type"] == "map_item_started"
        and item["payload"]["item_index"] == 1
        for item in repo.events
    )


def test_agent_map_refreshes_cancellation_before_item_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    node = WorkflowNode.model_validate(
        {
            "node_key": "generate",
            "node_type": "agent_map",
            "reference_key": "generator",
            "max_attempts": 3,
            "map_config": {"items_key": "items", "output_key": "items"},
        }
    )
    node_run = AgentNodeRun(
        id=1,
        run_id=run.id,
        node_key=node.node_key,
        node_type=node.node_type,
        status="running",
        attempt=1,
        input_payload={"items": [{"batch": 1}]},
    )
    repo.node_runs.append(node_run)
    calls = 0

    def execute_agent(**_: Any) -> AgentExecutionResult:
        nonlocal calls
        calls += 1
        run.status = "cancelled"
        raise TimeoutError("取消期间映射项超时")

    monkeypatch.setattr(runtime, "run_agent", execute_agent)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)

    with pytest.raises(runtime._RunCancelled, match="已取消"):
        runtime._execute_agent_map(
            repo=repo,
            run=run,
            node=node,
            node_run=node_run,
            definition=SimpleNamespace(name="真实调用智能体"),
            model_metadata={"name": "test-model", "route": "main", "source": "测试模型路由"},
            tools=[],
            execution_context=ToolExecutionContext(
                db=None,
                user_id=1,
                project_id=2,
                run_id=run.id,
                node_key=node.node_key,
                run_input={},
                artifacts={},
            ),
            node_input={"items": [{"batch": 1}]},
            previous=None,
        )

    assert calls == 1
    assert node_run.status == "cancelled"
    assert node_run.output_payload == {
        "items": [],
        "completed_count": 0,
        "total_count": 1,
    }
    assert sum(item["event_type"] == "map_item_started" for item in repo.events) == 1
    assert repo.events[-1]["event_type"] == "node_cancelled"


def test_standard_agent_cancellation_prevents_retry_and_failure_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    calls = 0

    def execute_agent(**_: Any) -> AgentExecutionResult:
        nonlocal calls
        calls += 1
        run.status = "cancelled"
        raise TimeoutError("取消期间上游超时")

    monkeypatch.setattr(runtime, "run_agent", execute_agent)

    with pytest.raises(runtime._RunCancelled, match="已取消"):
        runtime._execute_node_with_retry(repo, run, _agent_node(), {})

    assert calls == 1
    assert run.status == "cancelled"
    assert [(item.attempt, item.status) for item in repo.node_runs] == [
        (1, "cancelled")
    ]
    assert [item["event_type"] for item in repo.events] == [
        "node_started",
        "node_cancelled",
    ]


def test_standard_agent_refreshes_cancellation_before_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repo = _FakeRepository(run)
    calls = 0

    def list_agent_tools(_: int) -> list[Any]:
        run.status = "cancelled"
        return []

    def execute_agent(**_: Any) -> AgentExecutionResult:
        nonlocal calls
        calls += 1
        raise AssertionError("取消后不应调用模型")

    monkeypatch.setattr(repo, "list_agent_tools", list_agent_tools)
    monkeypatch.setattr(runtime, "run_agent", execute_agent)

    with pytest.raises(runtime._RunCancelled, match="已取消"):
        runtime._execute_node_with_retry(repo, run, _agent_node(), {})

    assert calls == 0
    assert run.status == "cancelled"
    assert [(item.attempt, item.status) for item in repo.node_runs] == [
        (1, "cancelled")
    ]
    assert [item["event_type"] for item in repo.events] == [
        "node_started",
        "node_cancelled",
    ]


def test_standard_agent_cancels_inflight_async_sdk_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"cancelled": False}

    async def execute_agent(**_: Any) -> AgentExecutionResult:
        try:
            await asyncio.Event().wait()
        finally:
            state["cancelled"] = True

    monkeypatch.setattr(runtime, "run_agent_async", execute_agent)
    monkeypatch.setattr(runtime, "_current_run_status", lambda _run_id: "cancelled")

    with pytest.raises(runtime._RunCancelled, match="已取消"):
        asyncio.run(
            runtime._run_standard_agent_async(
                execution_context=SimpleNamespace(run_id=41),
            )
        )

    assert state["cancelled"] is True


def test_agent_map_does_not_enter_node_level_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run()
    repo = _FakeRepository(run)
    calls = 0

    def execute_node(*_: Any, **__: Any) -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("映射项超时")

    monkeypatch.setattr(runtime, "_execute_node", execute_node)

    with pytest.raises(TimeoutError, match="映射项超时"):
        runtime._execute_node_with_retry(repo, run, _agent_node(node_type="agent_map"), {})

    assert calls == 1
    assert repo.events == []
