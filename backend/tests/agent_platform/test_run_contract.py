from types import SimpleNamespace
from datetime import datetime

import pytest

from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentToolDefinition
from modules.agent_platform.contracts import AgentProgramDefinition
from modules.agent_platform.run_contract import (
    attempt_id,
    build_execution_contract,
    new_logical_task_id,
)
from modules.agent_platform.service import (
    _checkpoint_execution,
    _initial_run_context,
    _restorable_checkpoints,
    _restored_checkpoint_sdk_state,
)
from modules.agent_platform.runtime import (
    _contract_agent_tools,
    _contract_definition,
    _contract_definition_snapshots,
)


def test_attempt_identity_is_stable_across_retries() -> None:
    logical_task_id = new_logical_task_id()

    assert logical_task_id.startswith("task-")
    assert attempt_id(logical_task_id, 1) != attempt_id(logical_task_id, 2)
    assert attempt_id(logical_task_id, 1).startswith(logical_task_id)


def test_initial_run_context_keeps_logical_task_and_attempt_identity() -> None:
    context = _initial_run_context(
        execution_limits={"max_requests": 10},
        input_payload={"requirement": "真实需求"},
        run_attempt=2,
        logical_task_id="task-fixed",
        execution_contract={"hash": "contract-fixed"},
    )

    assert context["logical_task_id"] == "task-fixed"
    assert context["attempt_id"] == "task-fixed/attempt-2"
    assert context["checkpoint_schema_version"] == 1
    assert context["input_hash"]
    assert context["execution_contract"]["hash"] == "contract-fixed"


def test_agent_program_retry_uses_its_runtime_agent_network_checkpoint() -> None:
    execution = AgentProgramDefinition(
        execution_mode="agent_network",
        entry_agent_key="main",
        max_attempts=3,
        time_budget_seconds=120,
    )

    projected = _checkpoint_execution(execution)

    assert projected.output_node_key == "agent_network"
    assert len(projected.nodes) == 1
    assert projected.nodes[0].node_type == "agent_network"
    assert projected.nodes[0].reference_key == "main"
    assert projected.nodes[0].max_attempts == 3


def test_agent_program_retry_rejects_legacy_inline_action_checkpoint() -> None:
    execution = AgentProgramDefinition(
        execution_mode="agent_network",
        entry_agent_key="main",
        max_attempts=2,
    )
    parent_context = _initial_run_context(
        execution_limits={"max_requests": 10},
        logical_task_id="task-fixed",
        execution_contract={"hash": "contract-fixed"},
    )
    parent_node = AgentNodeRun(
        id=101,
        run_id=9,
        node_key="agent_network",
        node_type="agent_network",
        status="failed",
        attempt=1,
        input_payload={"requirement": "真实需求"},
        output_payload={},
        sdk_state={
            "action_checkpoint": {
                "action_key": "observe_generation_state",
                "state_hash": "state-fixed",
            },
            "usage": {"requests": 1},
        },
        started_at=datetime(2026, 11, 5, 1, 0, 0),
        finished_at=datetime(2026, 11, 5, 1, 0, 5),
    )

    checkpoints = _restorable_checkpoints(
        execution=_checkpoint_execution(execution),
        node_runs=[parent_node],
    )

    assert len(checkpoints) == 1
    assert checkpoints[0].has_action_checkpoint is True

    # 旧协议只能用于明确拒绝，不能用补版本号的 fixture 冒充已迁移状态。
    with pytest.raises(ValueError, match="检查点协议不受支持"):
        _restored_checkpoint_sdk_state(parent_node)
    assert parent_context["attempt_id"] == "task-fixed/attempt-1"


def test_runtime_resolves_definitions_from_the_run_contract_snapshot() -> None:
    agent = SimpleNamespace(id=7, agent_key="main")
    tool = SimpleNamespace(id=8, tool_key="submit")

    class Db:
        def get(self, model: object, value: int):
            if model.__name__ == "AgentDefinition" and value == 7:
                return agent
            if model.__name__ == "AgentToolDefinition" and value == 8:
                return tool
            return None

    run = SimpleNamespace(run_context={
        "execution_contract": {
            "agents": [{"id": 7, "key": "main", "tool_ids": [8]}],
            "tools": [{"id": 8, "key": "submit"}],
        }
    })
    repo = SimpleNamespace(db=Db())

    resolved_agent = _contract_definition(
        repo,
        run,
        "main",
        definition_type=AgentDefinition,
        collection_key="agents",
    )

    assert resolved_agent is not agent
    assert resolved_agent.id == agent.id
    assert resolved_agent.agent_key == "main"
    resolved_tools = _contract_agent_tools(repo, run, agent)
    assert len(resolved_tools or []) == 1
    assert resolved_tools[0].id == tool.id
    assert resolved_tools[0].tool_key == "submit"


def test_execution_contract_hash_changes_when_definition_changes() -> None:
    workflow = SimpleNamespace(
        id=10,
        project_id=2,
        workflow_key="test_generation",
        version=1,
        definition={"execution_mode": "agent_network", "entry_agent_key": "main"},
    )
    agent = SimpleNamespace(
        id=20,
        project_id=None,
        agent_key="main",
        version=1,
        name="主 Agent",
        description="",
        instructions="按目标规划",
        model="test-model",
        output_schema={},
        runtime_config={"tool_keys": []},
    )

    class Definitions:
        def get_agent(self, *, project_id: int, agent_key: str):
            assert (project_id, agent_key) == (2, "main")
            return agent

        def list_agent_tools(self, agent_definition_id: int, *, project_id: int):
            assert (agent_definition_id, project_id) == (20, 2)
            return []

    execution = SimpleNamespace(entry_agent_key="main", nodes=[])
    definitions = Definitions()
    first = build_execution_contract(
        workflow=workflow,
        execution=execution,
        definitions=definitions,
        runtime_registry_signature="registry-a",
    )

    workflow.definition = {
        "execution_mode": "agent_network",
        "entry_agent_key": "main",
        "max_attempts": 3,
    }
    second = build_execution_contract(
        workflow=workflow,
        execution=execution,
        definitions=definitions,
        runtime_registry_signature="registry-a",
    )

    assert first["hash"] != second["hash"]
    assert first["workflow"]["definition"] != second["workflow"]["definition"]


def test_execution_contract_freezes_reachable_subagents_and_runtime_tools() -> None:
    def agent(agent_id: int, key: str, runtime_config: dict[str, object] | None = None):
        return SimpleNamespace(
            id=agent_id,
            project_id=2,
            agent_key=key,
            version=1,
            name=key,
            description="",
            instructions=f"执行 {key}",
            model="test-model",
            output_schema={},
            runtime_config=dict(runtime_config or {}),
        )

    parent = agent(
        20,
        "main",
        {"subagent_keys": ["leaf"], "managed_agent_keys": ["managed"]},
    )
    leaf = agent(21, "leaf")
    managed = agent(22, "managed")
    parent_tool = SimpleNamespace(
        id=30,
        project_id=2,
        tool_key="parent_tool",
        version=1,
        name="父工具",
        description="",
        handler_key="testing.parent_tool",
        input_schema={},
        output_schema={},
        risk_level="low",
        requires_approval=False,
    )
    leaf_tool = SimpleNamespace(
        id=31,
        project_id=2,
        tool_key="leaf_tool",
        version=1,
        name="叶子工具",
        description="",
        handler_key="testing.leaf_tool",
        input_schema={},
        output_schema={},
        risk_level="low",
        requires_approval=False,
    )

    class Definitions:
        agents = {item.agent_key: item for item in (parent, leaf, managed)}
        tools = {parent.id: [parent_tool], leaf.id: [leaf_tool], managed.id: []}

        def get_agent(self, *, project_id: int, agent_key: str):
            assert project_id == 2
            return self.agents.get(agent_key)

        def list_agent_tools(self, agent_definition_id: int, *, project_id: int):
            assert project_id == 2
            return list(self.tools[agent_definition_id])

        def get_tool(self, *, project_id: int, tool_key: str):
            return None

    workflow = SimpleNamespace(
        id=10,
        project_id=2,
        workflow_key="test_generation",
        version=1,
        definition={"execution_mode": "agent_network", "entry_agent_key": "main"},
    )
    execution = SimpleNamespace(entry_agent_key="main", nodes=[])
    contract = build_execution_contract(
        workflow=workflow,
        execution=execution,
        definitions=Definitions(),
        runtime_registry_signature="registry-a",
    )

    assert {item["key"] for item in contract["agents"]} == {"main", "leaf", "managed"}
    assert {item["key"] for item in contract["tools"]} == {"parent_tool", "leaf_tool"}
    parent_snapshot = next(item for item in contract["agents"] if item["key"] == "main")
    assert parent_snapshot["tool_ids"] == [30]

    run = SimpleNamespace(run_context={"execution_contract": contract})
    captured = _contract_definition_snapshots(SimpleNamespace(db=None), run)
    assert set(captured or {}) == {"main", "leaf", "managed"}
    assert captured["leaf"][1][0].tool_key == "leaf_tool"


def test_execution_contract_records_deidentified_model_route_identity() -> None:
    workflow = SimpleNamespace(
        id=10,
        project_id=2,
        workflow_key="test_generation",
        version=1,
        definition={"execution_mode": "agent_network", "entry_agent_key": "main"},
    )
    agent = SimpleNamespace(
        id=20,
        project_id=2,
        agent_key="main",
        version=1,
        name="主 Agent",
        description="",
        instructions="",
        model="fixed-model",
        output_schema={},
        runtime_config={"model_route": "main", "transient_fallback_model_route": "turbo"},
    )

    class Provider:
        model = "route-model"
        wire_api = "chat_completions"

    class Client:
        provider = Provider()
        turbo_provider = Provider()

        @staticmethod
        def _provider_cache_identity(provider: object) -> str:
            return "provider-hash"

    class Definitions:
        def get_agent(self, *, project_id: int, agent_key: str):
            return agent

        def list_agent_tools(self, agent_definition_id: int, *, project_id: int):
            return []

    contract = build_execution_contract(
        workflow=workflow,
        execution=SimpleNamespace(entry_agent_key="main", nodes=[]),
        definitions=Definitions(),
        runtime_registry_signature="registry-a",
        model_client=Client(),
    )

    snapshot = contract["agents"][0]
    assert snapshot["model_routes"]["main"] == {
        "model": "fixed-model",
        "provider_hash": "provider-hash",
        "provider_class": f"{Provider.__module__}.{Provider.__qualname__}",
        "wire_api": "chat_completions",
        "reasoning_effort": None,
    }
    assert snapshot["model_routes"]["turbo"]["model"] == "fixed-model"


def test_execution_contract_rejects_missing_direct_reference_and_cycle() -> None:
    workflow = SimpleNamespace(
        id=10,
        project_id=2,
        workflow_key="test_generation",
        version=1,
        definition={"execution_mode": "dag", "nodes": []},
    )

    class MissingDefinitions:
        def get_agent(self, *, project_id: int, agent_key: str):
            return None

        def get_tool(self, *, project_id: int, tool_key: str):
            return None

    with pytest.raises(LookupError, match="工作流 Agent"):
        build_execution_contract(
            workflow=workflow,
            execution=SimpleNamespace(
                nodes=[SimpleNamespace(node_type="agent", reference_key="missing")],
            ),
            definitions=MissingDefinitions(),
            runtime_registry_signature="registry-a",
        )

    first = SimpleNamespace(
        id=20,
        project_id=2,
        agent_key="first",
        version=1,
        name="first",
        description="",
        instructions="",
        model="",
        output_schema={},
        runtime_config={"subagent_keys": ["second"]},
    )
    second = SimpleNamespace(
        id=21,
        project_id=2,
        agent_key="second",
        version=1,
        name="second",
        description="",
        instructions="",
        model="",
        output_schema={},
        runtime_config={"subagent_keys": ["first"]},
    )

    class CyclicDefinitions:
        agents = {"first": first, "second": second}

        def get_agent(self, *, project_id: int, agent_key: str):
            return self.agents.get(agent_key)

        def list_agent_tools(self, agent_definition_id: int, *, project_id: int):
            return []

        def get_tool(self, *, project_id: int, tool_key: str):
            return None

    with pytest.raises(ValueError, match="循环"):
        build_execution_contract(
            workflow=workflow,
            execution=SimpleNamespace(entry_agent_key="first", nodes=[]),
            definitions=CyclicDefinitions(),
            runtime_registry_signature="registry-a",
        )

