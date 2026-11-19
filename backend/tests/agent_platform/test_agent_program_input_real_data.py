"""使用真实任务输入验证主 Agent 的业务输入与执行身份隔离。

真实数据库只读；检查点写入仅在独立 SQLite 内存库，不创建真实重试任务、
不调用模型，也不制造用例、模型评审或人工答案。
"""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentActionReceiptRecord, AgentArtifactBlob, AgentJournalBranch,
    AgentNodeRun, AgentRun, AgentRunEvent, AgentValidatedCallRecord,
)
from modules.agent_platform import runtime
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.checkpoint_storage import compact_checkpoint
from modules.agent_platform.contracts import AgentProgramDefinition, WorkflowGraph, WorkflowNode, parse_execution_definition
from modules.agent_platform.lifecycle import new_claim_token
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.retry_policy import RetryAttemptState
from modules.agent_platform.service import _restore_checkpoint_node


@pytest.fixture(scope="module")
def real_inputs():
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        program = db.get(AgentRun, 51)
        dag = db.get(AgentRun, 48)
        assert program is not None and dag is not None
        execution = parse_execution_definition(program.run_context["execution_contract"]["workflow"]["definition"])
        dag_execution = parse_execution_definition(dag.run_context["execution_contract"]["workflow"]["definition"])
        assert isinstance(execution, AgentProgramDefinition) and isinstance(dag_execution, WorkflowGraph)
        node = WorkflowNode(node_key="agent_network", node_type="agent_network", reference_key=execution.entry_agent_key,
                            max_attempts=execution.max_attempts, time_budget_seconds=execution.time_budget_seconds)
        repo = AgentRunRepository(db)
        dag_examples = []
        for key in ("evidence", "resolve_generation"):
            dag_node = next(item for item in dag_execution.nodes if item.node_key == key)
            recorded = repo.latest_node_run(run_id=dag.id, node_key=key)
            outputs = {dependency: deepcopy(repo.latest_node_run(run_id=dag.id, node_key=dependency).output_payload)
                       for dependency in dag_node.depends_on}
            dag_examples.append({"node": dag_node, "outputs": outputs, "expected": deepcopy(repo.read_node_input(recorded))})
        value = {
            "id": program.id, "user_id": program.user_id, "project_id": program.project_id,
            "workflow_definition_id": program.workflow_definition_id,
            "input_payload": deepcopy(program.input_payload), "run_context": deepcopy(program.run_context),
            "node": node,
            "main_runtime_config": deepcopy(next(item["runtime_config"]
                for item in program.run_context["execution_contract"]["agents"] if item["key"] == execution.entry_agent_key)),
            "dag": {"id": dag.id, "project_id": dag.project_id, "input_payload": deepcopy(dag.input_payload),
                    "run_context": deepcopy(dag.run_context)}, "dag_examples": dag_examples,
        }
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        return value


def program_run(source, identity=None, payload=None):
    return SimpleNamespace(id=source["id"] if identity is None else identity,
                           project_id=source["project_id"], user_id=source["user_id"],
                           input_payload=deepcopy(source["input_payload"] if payload is None else payload),
                           run_context=deepcopy(source["run_context"]))


def test_program_raw_input_has_no_execution_identity_and_stable_hash(real_inputs):
    parent = program_run(real_inputs)
    # 只在隔离对象改变执行编号，不宣称该编号是真实运行。
    child = program_run(real_inputs, identity=parent.id + 1000000)
    first = runtime._node_input(parent, real_inputs["node"], {})
    second = runtime._node_input(child, real_inputs["node"], {})
    assert first == second == real_inputs["input_payload"]
    assert action_state_hash(first) == action_state_hash(second)
    assert not {"run_id", "project_id", "dependency_outputs"} & first.keys()
    context = ToolExecutionContext(db=None, user_id=child.user_id, project_id=child.project_id,
                                  run_id=child.id, node_key="agent_network", run_input=second, action_input=second)
    assert context.run_id == child.id and context.project_id == child.project_id


def test_program_empty_payload_stays_empty_without_generic_dag_fallback(real_inputs):
    # 仅验证通用输入投影，不把空需求作为合法生成任务执行。
    assert runtime._node_input(program_run(real_inputs, payload={}), real_inputs["node"], {}) == {}


def test_shared_run_request_limit_is_independent_of_recorded_main_session_turns(real_inputs):
    run = program_run(real_inputs)
    assert real_inputs["main_runtime_config"]["max_turns"] == 160
    # 明确的新执行策略属于执行元数据；业务输入仍完全来自真实任务。
    run.run_context["execution_limits"]["max_requests"] = 240
    fresh_usage = dict.fromkeys(("attempted_requests", "input_tokens", "output_tokens", "total_tokens"), 0)
    reservation = runtime._autonomous_request_reservation(run=run, quota_usage=fresh_usage)
    assert reservation["attempted_requests"] == 240
    recorded_usage = {key: run.run_context["usage"][key] for key in fresh_usage}
    remaining = runtime._autonomous_request_reservation(run=run, quota_usage=recorded_usage)
    assert remaining["attempted_requests"] == 240 - recorded_usage["attempted_requests"]


@pytest.mark.parametrize("position", [0, 1])
def test_real_dag_mapped_inputs_remain_exact_including_dag_agent_network(real_inputs, position):
    example = real_inputs["dag_examples"][position]
    assert runtime._node_input(SimpleNamespace(**real_inputs["dag"]), example["node"], example["outputs"]) == example["expected"]


