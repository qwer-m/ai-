"""真实63候选的隔离Journal CPU回归；不构造模型响应或写线上历史。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from functools import partial
import json
from types import SimpleNamespace

from agents import Agent
from agents.run_config import CallModelData, ModelInputData
from agents.tool_context import ToolContext
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db import db_utc_now
from core.db.model_defs import (
    AgentActionReceiptRecord, AgentArtifactBlob, AgentJournalBranch, AgentNodeRun,
    AgentRun, AgentRunEvent, AgentValidatedCallRecord,
)
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.lifecycle import new_claim_token
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import _PersistentStateInputFilter, _function_tool, _runner_input
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from modules.agent_platform import test_generation_agent as generation
from test_single_agent_direct_protocol_real_data import (
    actual_patch, context_from_real, coverage_from_real, model_input, real,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _size(value):
    return len(_json(value).encode("utf-8"))


@pytest.fixture(scope="module")
def committed(real):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (AgentRun, AgentNodeRun, AgentRunEvent, AgentArtifactBlob, AgentJournalBranch,
                  AgentActionReceiptRecord, AgentValidatedCallRecord):
        model.__table__.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        metadata = deepcopy(real["run_metadata"])
        for key in ("created_at", "started_at", "finished_at"):
            metadata[key] = datetime.fromisoformat(metadata[key]) if metadata[key] is not None else None
        # 只在内存隔离库创建执行权控制元数据；业务来源、候选正文均用真实fixture。
        claim = new_claim_token()
        metadata.update(status="running", finished_at=None, claim_token=claim,
                        lease_expires_at=db_utc_now() + timedelta(hours=1))
        frozen = deepcopy(real["frozen_run_context"])
        frozen["logical_task_id"] = "cpu-context-" + real["current_initial_state"]["source_ref"]["content_hash"]
        run = AgentRun(**metadata, input_payload=deepcopy(real["original_run_input"]), run_context=frozen, output_payload={})
        db.add(run)
        node = AgentNodeRun(run_id=run.id, node_key="agent_network", node_type="agent_network",
                            status="running", attempt=1, input_payload=deepcopy(real["current_input"]),
                            output_payload={}, sdk_state={})
        db.add(node)
        db.commit()
        context = context_from_real(real, db)
        context.claim_token = claim
        journal = AgentActionJournal(AgentRunRepository(db), run_id=run.id, node_run_id=node.id, expected_claim_token=claim)
        journal.init(state=context.action_state, input_hash=action_state_hash(context.action_input))
        context.action_journal = journal
        coordinator = next(spec for spec in BUILTIN_AGENT_SPECS if spec["agent_key"] == "test_generation_coordinator")
        config = deepcopy(coordinator["runtime_config"])
        state_filter = _PersistentStateInputFilter(context=context, runtime_config=config)
        initial_observation = state_filter.observe()
        initial_input = _runner_input(execution_context=context, input_payload=initial_observation, runtime_config=config)
        state_filter.bind_initial_input(initial_input, initial_observation)
        loop = AutonomousToolLoop(terminal_tool_names={"finish_generation"},
            validate_submission=partial(generation.validate_generation_resolution, context),
            recoverable_errors=(OutputValidationError,), max_tool_calls=120, action_context=context)
        tools = {spec["tool_key"]: loop.wrap_tool(_function_tool(SimpleNamespace(**spec), context,
            state_input_filter=state_filter), allow_feedback=True) for spec in BUILTIN_TOOL_SPECS}
        items = [{"role": "user", "content": initial_input}]
        actions = []

        def execute(key, arguments):
            arguments_json = _json(arguments)
            # 控制调用身份由实际动作和参数产生，明确不是模型生成的function_call记录。
            call_id = "cpu-" + action_state_hash([len(actions), key, arguments])
            tool_context = ToolContext(context=context, tool_name=key, tool_call_id=call_id, tool_arguments=arguments_json)
            output = asyncio.run(tools[key].on_invoke_tool(tool_context, arguments_json))
            pair = [{"type": "function_call", "call_id": call_id, "name": key, "arguments": arguments_json},
                    {"type": "function_call_output", "call_id": call_id, "output": output}]
            items.extend(pair)
            actions.append({"tool_key": key, "tool_call_id": call_id, "pair": pair})
            return actions[-1]

        read = execute("read_source", {"evidence_id": None, "offset": 0, "max_chars": 60000})
        # 旧真实候选只作当前协议CPU输入，删除必须按当前合同显式声明。
        execute("commit_coverage_plan", {"expected_version": 0, "items": coverage_from_real(real),
                                         "remove_coverage_ids": []})
        maximum = context.action_state["goal"]["batch_case_limit"]
        for offset in range(0, 70, maximum):
            execute("append_cases", {"expected_coverage_version": context.action_state["coverage_plan"]["version"],
                "coverage_updates": [], "remove_coverage_ids": [], "cases": [model_input(item["content"])
                for item in real["replay_cases"][offset:offset + maximum]]})
        inspected = execute("inspect_cases", {"case_ids": list(context.action_state["cases"]["items"])[:5]})
        execute("patch_cases", {"patches": [actual_patch(real, context)]})
        before_failure = deepcopy(items)
        healthy_observation = state_filter.observe()
        rejected = execute("append_cases", {"expected_coverage_version": context.action_state["coverage_plan"]["version"],
            "coverage_updates": [], "remove_coverage_ids": [], "cases": [model_input(real["current_cases_before"][0]["content"])]})
        assert json.loads(rejected["pair"][1]["output"])["status"] == "rejected"
        assert journal.receipt_by_id(rejected["tool_call_id"], include_payloads=False)["status"] == "failed"
        started_arguments = _json({"patches": [actual_patch(real, context)]})
        started_id = "cpu-started-" + action_state_hash(started_arguments)
        journal.begin(tool_call_id=started_id, tool_key="patch_cases", args={"arguments_json": started_arguments},
                      prehash=action_state_hash(context.action_state))
        yield SimpleNamespace(context=context, journal=journal, state_filter=state_filter,
                              items=items, healthy_items=before_failure, observation=healthy_observation,
                              initial_observation=initial_observation, initial_input=initial_input,
                              actions=actions, read=read, inspected=inspected, rejected=rejected,
                              started={"type": "function_call", "call_id": started_id,
                                       "name": "patch_cases", "arguments": started_arguments},
                              agent=Agent(name=coordinator["name"], instructions=coordinator["instructions"]))
        assert real["provenance"]["historical_status"] == "failed"
        assert real["provenance"]["new_model_review_performed"] is False
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def test_completed_current_domain_writes_compact_only_model_view_and_keep_real_journal(committed, record_property):
    items = deepcopy(committed.healthy_items)
    before = deepcopy(items)
    receipts = committed.journal.receipts(include_payloads=True)
    projected = committed.state_filter._compact_committed_actions(items)
    assert items == before
    assert committed.journal.receipts(include_payloads=True) == receipts
    committed_keys = {"commit_coverage_plan", "append_cases", "patch_cases"}
    assert all(item.get("name") not in committed_keys for item in projected if item.get("type") == "function_call")
    proofs = [entry for item in projected if item.get("role") == "user" and "已持久化行动回执" in item.get("content", "")
              for entry in json.loads(item["content"].split("\n", 1)[1])]
    assert len(proofs) == 16
    assert all(proof["status"] == "committed" for proof in proofs)
    for proof in proofs:
        receipt = committed.journal.receipt_by_id(proof["tool_call_id"], include_payloads=False)
        assert proof["arguments_hash"] == receipt["arguments_hash"]
        assert proof["post_state_hash"] == receipt["post_state_hash"]
    # 只比较同一组已提交写入的调用及回执，不把固定原文和未决失败混入压缩收益。
    proof_ids = {proof["tool_call_id"] for proof in proofs}
    raw_write_pairs = [item for item in before if item.get("call_id") in proof_ids]
    assert _size(proofs) < _size(raw_write_pairs) / 3
    assert _size(projected) < _size(before)
    record_property("raw_cpu_carrier_bytes", _size(before))
    record_property("compacted_model_view_bytes", _size(projected))
    record_property("actual_context_reduction_ratio", 1 - _size(projected) / _size(before))
    record_property("raw_committed_write_pair_bytes", _size(raw_write_pairs))
    record_property("compacted_committed_proof_bytes", _size(proofs))
    record_property("fixture_provenance", "真实63候选与当前领域动作；隔离Journal，无模型请求或模型日志")


def test_failed_started_and_undeclared_read_actions_preserve_original_pairs(committed):
    controls = [*deepcopy(committed.rejected["pair"]), deepcopy(committed.started), *deepcopy(committed.read["pair"])]
    before = deepcopy(controls)
    assert committed.state_filter._compact_committed_actions(controls) == before
    assert controls == before


@pytest.mark.parametrize("corruption", ["arguments", "output", "tool_name", "missing_output"])
def test_completed_journal_rejects_changed_wire_identity_or_result(committed, corruption):
    action = next(item for item in committed.actions if item["tool_key"] == "append_cases")
    pair = deepcopy(action["pair"])
    if corruption == "arguments":
        pair[0]["arguments"] += " "
    elif corruption == "output":
        pair[1]["output"] += " "
    elif corruption == "tool_name":
        pair[0]["name"] = "patch_cases"
    else:
        pair.pop()
    with pytest.raises(ValueError, match="不可变回执不一致|缺少对应工具回执"):
        committed.state_filter._compact_committed_actions(pair)


def test_full_filter_keeps_current_source_read_and_expires_old_case_snapshot(committed, record_property):
    before = deepcopy(committed.items)
    model_data = ModelInputData(input=deepcopy(before), instructions=committed.agent.instructions)
    result = committed.state_filter(CallModelData(context=committed.context, agent=committed.agent, model_data=model_data))
    assert model_data.input == before
    source_output = next(item for item in result.input if item.get("call_id") == committed.read["tool_call_id"]
                         and item.get("type") == "function_call_output")
    assert source_output == committed.read["pair"][1]
    inspected = next(item for item in result.input if item.get("call_id") == committed.inspected["tool_call_id"]
                     and item.get("type") == "function_call_output")
    assert json.loads(inspected["output"])["status"] == "historical_state_snapshot"
    # 真实全源快照有效；用例任一修改会使按整个cases版本登记的旧范围读取过期。
    initial = json.loads(result.input[0]["content"])
    assert initial["task_input"] == committed.context.action_input
    assert initial["task_input_sha256"] == action_state_hash(committed.context.action_input)
    assert "本轮唯一有效的当前状态" in result.input[-1]["content"]
    current = json.loads(result.input[-1]["content"].split("\n", 1)[1])
    assert isinstance(current["current_state"], dict)
    assert current["current_state"]["goal"] == committed.context.action_state["goal"]
    record_property("full_filtered_cpu_model_view_bytes", _size(result.input))
    record_property("current_observation_bytes", _size(committed.observation))
    record_property("current_observation_component_bytes", _json({key: _size(value) for key, value in committed.observation.items()}))
