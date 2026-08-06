from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agents.exceptions import ModelBehaviorError

from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform import runtime
from modules.agent_platform.contracts import WorkflowNode
from modules.agent_platform.sdk_adapter import AgentExecutionResult
from modules.agent_platform.registry import ToolExecutionContext


class _HTTPError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


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


def _agent_node(*, node_type: str = "agent") -> WorkflowNode:
    values: dict[str, Any] = {
        "node_key": "plan",
        "node_type": node_type,
        "reference_key": "business_planner",
        "max_attempts": 3,
        "input_mapping": {"requirement": "input.requirement"},
    }
    if node_type == "agent_map":
        values["map_config"] = {"items_key": "items", "output_key": "items"}
    return WorkflowNode.model_validate(values)


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
    assert run.run_context["quota_usage"]["attempted_requests"] == 2
    assert run.run_context["quota_usage"]["input_tokens"] > 10
    assert run.run_context["quota_usage"]["output_tokens"] > 5


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


def test_agent_run_quota_blocks_before_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
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
    }
    repo = _FakeRepository(run)
    calls = 0

    def execute_agent(**_: Any) -> AgentExecutionResult:
        nonlocal calls
        calls += 1
        raise AssertionError("额度阻断后不应调用模型")

    monkeypatch.setattr(runtime, "run_agent", execute_agent)

    with pytest.raises(runtime._RunQuotaExceeded, match="调用模型前阻断"):
        runtime._execute_node_with_retry(repo, run, _agent_node(), {})

    assert calls == 0
    assert run.run_context["usage"]["attempted_requests"] == 1
    assert "run_quota_blocked" in [event["event_type"] for event in repo.events]
    assert [item["event_type"] for item in repo.events] == [
        "node_started",
        "run_quota_blocked",
        "node_failed",
    ]
    assert repo.events[-1]["payload"]["retryable"] is False


def test_tool_agent_reserves_all_possible_sdk_turns_before_call(
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
        raise AssertionError("额度不足时不得进入 SDK")

    monkeypatch.setattr(runtime, "run_agent", execute_agent)

    with pytest.raises(runtime._RunQuotaExceeded, match="调用模型前阻断"):
        runtime._execute_node_with_retry(repo, run, _agent_node(), {})

    assert calls == 0
    blocked = next(item for item in repo.events if item["event_type"] == "run_quota_blocked")
    assert blocked["payload"]["reservation"]["attempted_requests"] == 2


@pytest.mark.parametrize("status_code", [429, 500, 502, 504])
def test_http_transient_status_is_retryable(status_code: int) -> None:
    assert runtime._is_retryable_agent_error(_HTTPError(status_code, "暂时不可用"))


def test_timeout_and_connection_errors_are_retryable() -> None:
    assert runtime._is_retryable_agent_error(TimeoutError("读取超时"))
    assert runtime._is_retryable_agent_error(ConnectionError("连接中断"))
    assert not runtime._is_retryable_agent_error(_HTTPError(400, "请求错误"))


def test_model_structured_output_error_is_retryable() -> None:
    assert runtime._is_retryable_agent_error(
        ModelBehaviorError("结构化输出缺少必填字段")
    )

    class SameNameButUnrelatedError(Exception):
        pass

    SameNameButUnrelatedError.__name__ = "ModelBehaviorError"
    assert not runtime._is_retryable_agent_error(
        SameNameButUnrelatedError("不应按类名误判")
    )


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
