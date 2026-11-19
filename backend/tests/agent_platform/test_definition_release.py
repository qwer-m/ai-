from types import SimpleNamespace

from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform.definition_repository import AgentDefinitionRepository


class _Query:
    def __init__(self, rows: list[object]):
        self.rows = rows

    def filter(self, *_args: object) -> "_Query":
        return self

    def options(self, *_args: object) -> "_Query":
        return self

    def order_by(self, *_args: object) -> "_Query":
        return self

    def all(self) -> list[object]:
        return list(self.rows)

    def first(self) -> object | None:
        return self.rows[0] if self.rows else None


class _Db:
    def __init__(self, *, agents: list[object], agent_refs: list[object] | None = None):
        self.agents = agents
        self.agent_refs = list(agent_refs or [])
        self.added: list[object] = []

    def query(self, model: object) -> _Query:
        if model is AgentDefinition:
            return _Query(self.agents)
        if model is AgentNodeRun or getattr(model, "class_", None) is AgentNodeRun:
            return _Query(self.agent_refs)
        if model is AgentRun or getattr(model, "class_", None) is AgentRun:
            return _Query([])
        raise AssertionError(f"未预期的查询模型: {model}")

    def add(self, value: object) -> None:
        self.added.append(value)
        if isinstance(value, AgentDefinition) and value not in self.agents:
            self.agents.append(value)


def _agent_spec(instructions: str = "新指令") -> dict[str, object]:
    return {
        "agent_key": "planner",
        "version": 1,
        "name": "规划 Agent",
        "description": "",
        "instructions": instructions,
        "model": "model-v2",
        "output_schema": {},
        "runtime_config": {},
    }


def test_referenced_agent_definition_is_released_as_new_version() -> None:
    old = SimpleNamespace(
        id=11,
        project_id=None,
        agent_key="planner",
        version=1,
        name="规划 Agent",
        description="",
        instructions="旧指令",
        model="model-v1",
        output_schema={},
        runtime_config={},
        enabled=True,
    )
    db = _Db(agents=[old], agent_refs=[SimpleNamespace(id=100)])

    AgentDefinitionRepository(db).sync_builtin_agent(user_id=1, spec=_agent_spec())

    released = [item for item in db.added if isinstance(item, AgentDefinition)]
    assert len(released) == 1
    assert released[0].version == 2
    assert released[0].enabled is True
    assert old.enabled is False


def test_same_released_agent_definition_is_not_duplicated_on_next_seed() -> None:
    old = SimpleNamespace(
        id=11,
        project_id=None,
        agent_key="planner",
        version=1,
        name="规划 Agent",
        description="",
        instructions="旧指令",
        model="model-v1",
        output_schema={},
        runtime_config={},
        enabled=False,
    )
    current = AgentDefinition(
        id=12,
        project_id=None,
        agent_key="planner",
        version=2,
        name="规划 Agent",
        description="",
        instructions="新指令",
        model="model-v2",
        output_schema={},
        runtime_config={},
        enabled=True,
        builtin=True,
    )
    db = _Db(agents=[old, current], agent_refs=[SimpleNamespace(id=100)])
    repository = AgentDefinitionRepository(db)

    repository.sync_builtin_agent(user_id=1, spec=_agent_spec())

    assert [item for item in db.added if isinstance(item, AgentDefinition)] == [current]
    assert current.enabled is True


def test_referenced_workflow_definition_is_released_as_new_version() -> None:
    old = AgentWorkflowDefinition(
        id=31,
        project_id=2,
        user_id=1,
        workflow_key="test_generation",
        version=1,
        name="测试生成",
        description="旧版本",
        definition={"execution_mode": "dag", "nodes": [], "output_node_key": "out"},
        enabled=True,
        builtin=True,
    )

    class WorkflowDb:
        def __init__(self) -> None:
            self.added: list[object] = []

        def query(self, model: object) -> _Query:
            if model is AgentWorkflowDefinition:
                return _Query([old])
            if model is AgentRun or getattr(model, "class_", None) is AgentRun:
                return _Query([SimpleNamespace(id=501)])
            raise AssertionError(f"未预期的查询模型: {model}")

        def add(self, value: object) -> None:
            self.added.append(value)

    db = WorkflowDb()
    repository = AgentDefinitionRepository(db)
    repository.sync_builtin_workflow(
        project_id=2,
        user_id=1,
        spec={
            "workflow_key": "test_generation",
            "version": 1,
            "name": "测试生成",
            "description": "新版本",
        },
        definition={"execution_mode": "dag", "nodes": [], "output_node_key": "out"},
    )

    released = [item for item in db.added if isinstance(item, AgentWorkflowDefinition)]
    assert len(released) == 1
    assert released[0].version == 2
    assert released[0].enabled is True
    assert old.enabled is False

