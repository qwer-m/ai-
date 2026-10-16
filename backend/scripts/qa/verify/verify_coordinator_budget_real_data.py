"""用真实运行账本、SDK 输入和历史咨询调用验证阶段预算及专业子树上限。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import RunConfig, RunContextWrapper, trace
from agents.exceptions import UserError
from agents.items import ItemHelpers
from agents.run_internal.agent_bindings import bind_public_agent
from agents.run_internal.run_steps import ToolRunFunction
from agents.run_internal.tool_execution import execute_function_tool_calls
from agents.usage import deserialize_usage
from openai.types.responses import ResponseFunctionToolCall
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentRunEvent
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import (
    AgentExecutionBudgetExceeded, AgentSubtreeRequestBudgetExceeded, AutonomousBudgetHooks,
)
from modules.agent_platform.contracts import AgentRunCreate, AgentRunExecutionLimits
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import (
    _agent_instance_quota_usage, _agent_request_reservation, _autonomous_request_reservation,
    _is_retryable_agent_error, _record_agent_usage, _reserve_agent_request,
)
from modules.agent_platform.sdk_adapter import AgentOutputContract, _build_sdk_agent, _runner_input, run_agent_async
from modules.agent_platform.service import _resolved_execution_limits
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record, sqlite_longtext as _sqlite_longtext


def verify_reservation(db, run, planning, definition, tools, sqlite_path: Path) -> dict:
    request = AgentRunCreate(project_id=run.project_id, workflow_key="test_case_generation", input_payload=run.input_payload)
    defaults = _resolved_execution_limits(request)
    lower = _resolved_execution_limits(request.model_copy(update={
        "execution_limits": AgentRunExecutionLimits(max_requests=20, max_total_tokens=100000),
    }))
    assert lower["max_requests"] <= 20 and lower["max_total_tokens"] <= 100000
    assert defaults["max_input_tokens"] == 2000000 and defaults["max_output_tokens"] == 800000
    assert defaults["max_total_tokens"] == 2800000
    parent = copy_record(definition)
    parent.runtime_config = deepcopy(next(spec["runtime_config"] for spec in BUILTIN_AGENT_SPECS
                                          if spec["agent_key"] == "test_generation_coordinator"))
    limited_run = copy_record(run)
    limited_run.run_context = {**deepcopy(run.run_context), "execution_limits": lower}
    zero = dict.fromkeys(("attempted_requests", "input_tokens", "output_tokens", "total_tokens"), 0)
    requested = _autonomous_request_reservation(run=limited_run, definition=parent, tools=tools, quota_usage=zero)
    assert requested["attempted_requests"] == lower["max_requests"]
    assert requested["total_tokens"] == lower["max_total_tokens"]

    if sqlite_path.exists():
        raise FileExistsError("隔离账本已存在，拒绝覆盖")
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite+pysqlite:///" + sqlite_path.resolve().as_posix())
    scope = "resolve_planning-instance-001"
    baseline = deepcopy(run.run_context["agent_instance_quota_usage"][scope])
    assert baseline["attempted_requests"] < defaults["max_requests"]
    source_input = AgentRunRepository(db).read_node_input(planning)
    token = uuid4().hex
    try:
        for model in (AgentRun, AgentNodeRun, AgentRunEvent):
            model.__table__.create(engine)
        with Session(engine, expire_on_commit=False) as isolated:
            copied_run, copied_node = copy_record(run), copy_record(planning)
            copied_run.status, copied_run.claim_token = "running", token
            copied_run.lease_expires_at = datetime.utcnow() + timedelta(minutes=10)
            copied_run.run_context = {**deepcopy(run.run_context), "execution_limits": defaults}
            copied_node.status, copied_node.input_payload = "running", deepcopy(source_input)
            checkpoint = deepcopy(planning.sdk_state["action_checkpoint"])
            checkpoint["budget"] = {}
            copied_node.sdk_state = {"action_checkpoint": checkpoint}
            isolated.add_all([copied_run, copied_node])
            isolated.commit()
            repo = AgentRunRepository(isolated)
            args = dict(repo=repo, run=copied_run, node_run=copied_node, definition=parent,
                        tools=tools, input_payload=source_input, quota_scope_key=scope)
            reservation = _reserve_agent_request(**args)
            assert reservation["attempted_requests"] == defaults["max_requests"] - baseline["attempted_requests"]
            before = deepcopy(copied_run.run_context)
            assert _reserve_agent_request(**args) == reservation
            assert copied_run.run_context == before
            assert copied_node.sdk_state["action_checkpoint"]["quota_base_usage"] == baseline
            # 当前新增消费取历史已记录的真实 usage；只验证结算公式，不伪造模型请求。
            actual = deepcopy(planning.sdk_state["action_checkpoint"]["budget"]["charged"])
            _record_agent_usage(repo=repo, run=copied_run, node_run=copied_node,
                                current=deepcopy(planning.sdk_state["usage"]), reservation=reservation,
                                quota_scope_key=scope, quota_charge=actual)
            settled = _agent_instance_quota_usage(copied_run, scope)
            assert settled == {key: baseline[key] + actual[key] for key in baseline}
    finally:
        engine.dispose()
    reopened = create_engine("sqlite+pysqlite:///" + sqlite_path.resolve().as_posix())
    try:
        with Session(reopened, expire_on_commit=False) as isolated:
            repo = AgentRunRepository(isolated)
            copied_run, copied_node = isolated.get(AgentRun, run.id), isolated.get(AgentNodeRun, planning.id)
            before = deepcopy(copied_run.run_context)
            assert _reserve_agent_request(repo=repo, run=copied_run, node_run=copied_node, definition=parent,
                                           tools=tools, input_payload=source_input, quota_scope_key=scope) == reservation
            assert copied_run.run_context == before
            assert _agent_instance_quota_usage(copied_run, scope) == settled
    finally:
        reopened.dispose()
    return {"default_limits": defaults, "explicit_lower_limits": lower,
            "shared_reservation_with_prior_consumption": reservation, "prior_consumption": baseline,
            "settled_usage": settled, "repeated_and_reopened_restore_unchanged": True,
            "sqlite_path": str(sqlite_path), "historical_run_limits_unchanged": run.run_context["execution_limits"]}


async def verify(db, run_id: int, sqlite_path: Path) -> dict:
    repo = AgentRunRepository(db)
    run = db.get(AgentRun, run_id)
    planning = repo.latest_node_run(run_id=run_id, node_key="resolve_planning")
    generation = repo.latest_node_run(run_id=run_id, node_key="generation")
    if run is None or run.status != "success" or planning is None or generation is None:
        raise ValueError("需要真实成功的规划与生成运行")
    before = action_state_hash({"run_context": run.run_context, "planning": planning.sdk_state, "generation": generation.sdk_state})
    definition = db.get(AgentDefinition, generation.agent_definition_id)
    tools = AgentDefinitionRepository(db).list_agent_tools(definition.id, project_id=run.project_id)
    reservation_report = verify_reservation(db, run, planning, definition, tools, sqlite_path)
    inputs = repo.read_node_input(generation)["items"]
    records = [item for item in generation.sdk_state["items"] if item.get("usage", {}).get("requests") == 1]
    assert len(records) >= 6
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                                   node_key=generation.node_key, run_input=deepcopy(run.input_payload))
    clients = []
    try:
        client = get_client_for_user(run.user_id, db)
        global_hooks = AutonomousBudgetHooks({
            "attempted_requests": reservation_report["default_limits"]["max_requests"],
            "input_tokens": reservation_report["default_limits"]["max_input_tokens"],
            "output_tokens": reservation_report["default_limits"]["max_output_tokens"],
            "total_tokens": reservation_report["default_limits"]["max_total_tokens"],
        })
        local = global_hooks.scoped_requests(int(definition.runtime_config["max_turns"]))
        assert local.observe()["local_request_scope"]["limit"] == 6
        for record in records[:6]:
            source = deepcopy(inputs[record["item_index"]])
            agent, _ = _build_sdk_agent(db=db, client=client, agent_definition=definition,
                                       execution_context=context, allocated_clients=clients, allow_subagents=True,
                                       trusted_input_payload=source, model_input_payload=source, budget_hooks=local)
            wrapper = RunContextWrapper(context=context)
            prompt = await agent.get_system_prompt(wrapper)
            request_input = ItemHelpers.input_to_new_input_list(_runner_input(
                execution_context=context, input_payload=source, runtime_config=dict(definition.runtime_config),
            ))
            await local.on_llm_start(wrapper, agent, prompt, request_input)
            local.settle_request(wrapper, deserialize_usage(record["usage"]))
        charged = global_hooks.snapshot()["charged"]
        assert charged["attempted_requests"] == 6
        assert charged["total_tokens"] == sum(record["usage"]["total_tokens"] for record in records[:6])
        prior = deepcopy(global_hooks.snapshot())
        try:
            await local.on_llm_start(wrapper, agent, prompt, request_input)
        except AgentSubtreeRequestBudgetExceeded as error:
            assert _is_retryable_agent_error(error) is False
        else:
            raise AssertionError("专业会话突破自己的六次上限")
        assert global_hooks.snapshot() == prior and global_hooks.observe()["remaining"]["attempted_requests"] > 0

        # Run31 的真实咨询参数经过真实 SDK as_tool 链路，局部已满时必须在联网前拒绝。
        historical = repo.latest_node_run(run_id=31, node_key="generation")
        prior_call = next(call for item in historical.sdk_state["items"] for call in item.get("tool_calls", [])
                          if call["tool_key"].startswith("agent:"))
        child_key = prior_call["tool_key"].removeprefix("agent:")
        child_tool = next(tool for tool in agent.tools if tool.name == child_key)
        replay_call = ResponseFunctionToolCall(type="function_call", call_id=uuid4().hex, name=child_key,
                                               arguments=json.dumps(prior_call["arguments"], ensure_ascii=False))
        with trace("真实咨询调用的局部请求预算拒绝验证", disabled=True):
            try:
                await execute_function_tool_calls(bindings=bind_public_agent(agent),
                    tool_runs=[ToolRunFunction(tool_call=replay_call, function_tool=child_tool)],
                    hooks=local, context_wrapper=wrapper,
                    config=RunConfig(tracing_disabled=True, trace_include_sensitive_data=False))
            except UserError as error:
                assert isinstance(error.__cause__, AgentSubtreeRequestBudgetExceeded)
                assert _is_retryable_agent_error(error) is False
            else:
                raise AssertionError("SDK 咨询子树突破了父专业会话局部上限")
        assert global_hooks.snapshot() == prior

        # 直接进入托管专业会话，极低显式输入额度让真实 SDK 在联网前拒绝，核对局部上限没有变成160。
        blocked_limits = {**global_hooks.snapshot()["limits"], "input_tokens": 1}
        blocked_hooks = AutonomousBudgetHooks(blocked_limits)
        captured = {definition.agent_key: (definition, tools)}
        definitions = AgentDefinitionRepository(db)
        for key in definition.runtime_config.get("subagent_keys", []):
            child = definitions.get_agent(project_id=run.project_id, agent_key=key)
            captured[key] = (child, definitions.list_agent_tools(child.id, project_id=run.project_id))
        try:
            await run_agent_async(db=db, agent_definition=definition, tool_definitions=tools,
                execution_context=context, input_payload=source, model_client=client,
                request_budget=blocked_limits, shared_budget_hooks=blocked_hooks,
                output_contract=AgentOutputContract(deepcopy(source)), require_validated_submission=True,
                captured_definitions=captured)
        except AgentExecutionBudgetExceeded as error:
            managed_budget = error.diagnostic["budget_usage"]
            assert managed_budget["local_request_scope"]["limit"] == 6
            assert managed_budget["charged"]["attempted_requests"] == 0
            assert error.diagnostic["failed_model_requests"] == []
        else:
            raise AssertionError("托管子会话没有在显式极低输入额度前拒绝")

        old_failure = db.query(AgentNodeRun.id).filter(AgentNodeRun.run_id == run_id,
            AgentNodeRun.node_key == "resolve_planning", AgentNodeRun.status == "failed").order_by(AgentNodeRun.id).first()
        assert old_failure is not None
        original_budget = deepcopy(db.get(AgentNodeRun, old_failure[0]).sdk_state["action_checkpoint"]["budget"])
        restored = AutonomousBudgetHooks(original_budget["limits"], checkpoint=original_budget).snapshot()
        restored_again = AutonomousBudgetHooks(original_budget["limits"], checkpoint=restored).snapshot()
        assert restored["charged"] == original_budget["charged"] and restored_again == restored
        assert restored["interrupted_requests"] == original_budget["pending_requests"] + original_budget["interrupted_requests"]
        # 非持久生成实例仍使用原有每会话预留，不能被父协调的160覆盖。
        regular = _agent_request_reservation(definition=definition, tools=tools, input_payload=source)
        assert regular["attempted_requests"] == 6
    finally:
        for allocated in clients:
            await allocated.close()
    assert action_state_hash({"run_context": run.run_context, "planning": planning.sdk_state, "generation": generation.sdk_state}) == before
    assert not db.new and not db.dirty and not db.deleted
    return {"status": "passed", "source_run_id": run.id, "reservation": reservation_report,
            "real_usage_record_indexes": [record["item_index"] for record in records[:6]],
            "local_scope": local.observe()["local_request_scope"], "global_charge_counted_once": charged,
            "sdk_consultation_rejected_before_network": True, "global_budget_unchanged_by_local_rejection": True,
            "managed_child_local_scope": managed_budget["local_request_scope"],
            "captured_specialist_tree_built_before_budget_rejection": True,
            "historical_failure_node_id": old_failure[0], "interrupted_budget_preserved": restored,
            "regular_generation_reservation": regular, "mysql_transaction": "READ ONLY",
            "online_database_writes": 0, "model_requests_sent": 0,
            "verification_scope": "real_usage_and_sdk_tool_replay_with_isolated_sqlite_accounting",
            "verification_limits": ["未执行新的模型请求；SQLite 账本验证仅改变真实副本的执行元数据",
                                    "六份已有单请求usage用于回放计数与结算，不代表新模型六轮会话"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".sqlite").exists():
        raise ValueError("验证产物已存在，拒绝覆盖")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = asyncio.run(verify(db, args.run_id, args.output.with_suffix(".sqlite")))
        db.rollback()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(args.output), "model_requests_sent": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
