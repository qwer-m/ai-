"""建议在应用补丁后复制到 backend/tests/agent_platform；当前在途任务期间不应用。

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
from modules.agent_platform import runtime, test_generation_agent as generation
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.checkpoint_storage import compact_checkpoint
from modules.agent_platform.contracts import AgentProgramDefinition, WorkflowGraph, WorkflowNode, parse_execution_definition
from modules.agent_platform.lifecycle import new_claim_token
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
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
        context = ToolExecutionContext(db=db, user_id=program.user_id, project_id=program.project_id,
                                      run_id=program.id, node_key=node.node_key,
                                      run_input=deepcopy(program.input_payload),
                                      artifacts=deepcopy(program.run_context["artifacts"]))
        # 当前纯初始化器读取真实 PDF 资产，产生新输入协议的初始来源状态；没有专业结果或业务结论。
        initial = deepcopy(generation.initialize_generation_state(context, deepcopy(program.input_payload)))
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
            "node": node, "initial_state": initial,
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


@pytest.mark.parametrize("position", [0, 1])
def test_real_dag_mapped_inputs_remain_exact_including_dag_agent_network(real_inputs, position):
    example = real_inputs["dag_examples"][position]
    assert runtime._node_input(SimpleNamespace(**real_inputs["dag"]), example["node"], example["outputs"]) == example["expected"]


def test_real_source_checkpoint_survives_isolated_new_run_and_keeps_current_context_identity(real_inputs):
    shadow = create_engine("sqlite+pysqlite:///:memory:")
    for model in (AgentRun, AgentNodeRun, AgentRunEvent, AgentArtifactBlob, AgentJournalBranch,
                  AgentActionReceiptRecord, AgentValidatedCallRecord):
        model.__table__.create(shadow)
    try:
        with Session(shadow, expire_on_commit=False) as db:
            def new_shadow_run(identity, parent_id=None):
                clock = datetime.utcnow()
                run_context = deepcopy(real_inputs["run_context"])
                run_context["deadline_at"] = (clock + timedelta(minutes=10)).isoformat()
                # 逻辑任务/来源/业务输入均继承真实快照；只有隔离执行元数据为故障验证设定。
                return AgentRun(id=identity, user_id=real_inputs["user_id"], project_id=real_inputs["project_id"],
                                workflow_definition_id=real_inputs["workflow_definition_id"], status="running",
                                input_payload=deepcopy(real_inputs["input_payload"]), run_context=run_context,
                                parent_run_id=parent_id, claim_token=new_claim_token(), heartbeat_at=clock,
                                lease_expires_at=clock + timedelta(minutes=10), output_payload={}, error_message="")

            parent = new_shadow_run(real_inputs["id"])
            db.add(parent)
            db.commit()
            repo = AgentRunRepository(db)
            parent_input = runtime._node_input(parent, real_inputs["node"], {})
            assert parent_input == real_inputs["input_payload"]
            parent_node = AgentNodeRun(run_id=parent.id, node_key="agent_network", node_type="agent_network",
                                       status="running", attempt=1, input_payload={}, output_payload={}, sdk_state={})
            db.add(parent_node)
            db.flush()
            repo.write_node_input(parent_node, parent_input)
            db.commit()
            journal = AgentActionJournal(repo, run_id=parent.id, node_run_id=parent_node.id,
                                         expected_claim_token=parent.claim_token)
            checkpoint = journal.init(state=deepcopy(real_inputs["initial_state"]), input_hash=action_state_hash(parent_input))
            parent.status = parent_node.status = "failed"
            child = new_shadow_run(parent.id + 1000000, parent_id=parent.id)
            db.add(child)
            db.commit()
            restored = _restore_checkpoint_node(repo, checkpoint=parent_node, run_id=child.id)
            db.commit()
            assert restored.input_payload == parent_node.input_payload
            # 直接走真实仓库及运行时准备链，不模拟 Repository 或 Journal。
            setup = runtime._prepare_node_execution(repo, child, real_inputs["node"], {})
            assert compact_checkpoint(setup.node_run)["input_ref"] == checkpoint["input_ref"]
            assert setup.node_input == parent_input
            resumed = AgentActionJournal(repo, run_id=child.id, node_run_id=setup.node_run.id,
                                         expected_claim_token=child.claim_token)
            state = resumed.load_state()
            assert action_state_hash(state) == checkpoint["state_hash"]
            resumed.init(state=state, input_hash=action_state_hash(setup.node_input))
            context = setup.execution_context
            context.action_input, context.action_state, context.action_journal = setup.node_input, state, resumed
            generation.initialize_generation_state(context, setup.node_input)
            observed = generation.observe_generation_state(context)
            assert context.run_id == child.id and context.project_id == child.project_id
            assert "analyze_source" in observed["available_actions"]
            assert context.action_state["source"]["source_ref"] == real_inputs["initial_state"]["source"]["source_ref"]
    finally:
        shadow.dispose()
