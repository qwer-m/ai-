"""隔离回放真实成功候选和既有504回执，验证批量行动中已完成专业结果的恢复。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import ActionStateConflict, AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AgentExecutionBudgetExceeded, AutonomousBudgetHooks
from modules.agent_platform.contracts import AgentMapConfig, parse_execution_definition
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _node_input
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, _managed_agent_call_identity, _revalidate_managed_agent_call, run_agent_async,
)
from modules.agent_platform.semantic_identity import agent_execution_semantic_hash
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_WORKFLOW_SPECS
from modules.agent_platform.test_generation_escalation import validate_generation_scope_feedback
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions, _prepare_real_initial_state
from scripts.qa.verify.verify_planning_agent_real_model import planning_journal


def rejected(operation):
    try:
        operation()
    except ActionStateConflict as error:
        return str(error)
    raise AssertionError("过期或改变的专业契约没有被拒绝")


async def verify(db, run_id: int, sqlite_path: Path) -> dict:
    repo = AgentRunRepository(db)
    run = db.get(AgentRun, run_id)
    assert run is not None and run.status == "success"
    workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
    graph_spec = next(item for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] == workflow.workflow_key)
    node = next(item for item in parse_execution_definition(graph_spec["definition"]).nodes if item.node_key == "resolve_generation")
    keys = {*node.depends_on, "resolve_planning", "authority", "effective_facts", "generation"}
    nodes = {key: repo.latest_node_run(run_id=run_id, node_key=key) for key in keys}
    raw_input = _node_input(run, node, {key: value.output_payload for key, value in nodes.items()})
    before = action_state_hash([run.run_context, [[value.input_payload, value.output_payload, value.sdk_state] for value in nodes.values()]])
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
        node_key=node.node_key, run_input=deepcopy(run.input_payload), artifacts=deepcopy(run.run_context["artifacts"]))
    _prepare_real_initial_state(context, raw_input, nodes, repo, initial_stage="generation_all_pending", pending_indexes=[])
    snapshots, originals, _ = _capture_current_definitions(db, run, node.reference_key)
    parent, tools = snapshots[node.reference_key]
    child, _ = snapshots["test_case_generator"]
    client = get_client_for_user(run.user_id, db)
    semantic = agent_execution_semantic_hash(db=db, user_id=run.user_id, project_id=run.project_id,
        node=node, definition=parent, tools=tools, client=client, definition_snapshots=snapshots)
    prepared = generation.prepare_generation_action(context, {"item_indexes": [0, 1], "guidance": ""})
    item, request = prepared["items"][0], prepared["requests"][0]
    contract = AgentOutputContract(deepcopy(request), AgentMapConfig(item_postprocessor="testing.postprocess_generation_batch_item"),
        alternate_output_validators={"planning_feedback": partial(validate_generation_scope_feedback, item)})
    identity = _managed_agent_call_identity(execution_semantic_hash=semantic, agent_key=child.agent_key, contract=contract)
    historical = next(value for value in nodes["generation"].sdk_state["items"] if value["item_index"] == 0)
    accepted_id = next(value["tool_call_id"] for value in historical["tool_observations"] if value["status"] == "accepted")
    raw_candidate = deepcopy(next(value["arguments"] for value in historical["tool_calls"] if value["tool_call_id"] == accepted_id))
    output = contract.validate(child, context, raw_candidate)
    assert output == next(value["output"] for value in nodes["generation"].output_payload["items"] if value["item_index"] == 0)
    failed_ids = [value for (value,) in db.query(AgentNodeRun.id).filter(AgentNodeRun.run_id == run_id,
        AgentNodeRun.node_key == "resolve_planning", AgentNodeRun.status == "failed").order_by(AgentNodeRun.id).all()]
    failed_source = next((value, receipt) for value in (db.get(AgentNodeRun, ident) for ident in failed_ids)
        for receipt in value.sdk_state["action_checkpoint"]["receipts"].values()
        if receipt["status"] == "failed" and "504" in receipt["error"]["message"])
    failure_node, failure = failed_source
    real_budget = deepcopy(failure_node.sdk_state["action_checkpoint"]["budget"])
    initial_state = deepcopy(context.action_state)
    first_action_id = uuid4().hex
    with planning_journal(sqlite_path, run=run, workflow=workflow, source_node=nodes["resolve_planning"],
        graph_spec=graph_spec, node_spec=node, raw_input=raw_input, timeout=3600,
        verification="real_candidates_and_504_receipt_replay") as (journal, isolated_node, token):
        journal.init(state=initial_state, input_hash=action_state_hash(raw_input))
        journal.bind_execution_identity(semantic)
        journal.save_budget(real_budget)
        journal.begin(tool_call_id=first_action_id, tool_key="generate_cases",
            args={"arguments_json": json.dumps({"item_indexes": [0, 1], "guidance": ""})}, prehash=action_state_hash(initial_state))
        journal.save_validated_agent_call(identity=identity, raw_output=raw_candidate, output=output)
        saved = journal.load()
        assert saved["state"] == initial_state and saved["budget"] == real_budget
        journal.fail(tool_call_id=first_action_id, error=deepcopy(failure["error"]), budget=real_budget)
        assert journal.load()["state"] == initial_state
        local_node_id = isolated_node.id

    engine = create_engine("sqlite+pysqlite:///" + sqlite_path.resolve().as_posix())
    try:
        with Session(engine, expire_on_commit=False) as isolated:
            journal = AgentActionJournal(AgentRunRepository(isolated), run_id=run.id,
                node_run_id=local_node_id, expected_claim_token=token)
            reopened = journal.load()
            assert reopened["budget"] == real_budget and reopened["state"] == initial_state
            context.action_journal, context.claim_token = journal, token
            context.action_state = deepcopy(initial_state)
            # 极低真实配置额度只用于阻断父模型，初始化真实 invoke_agent 闭包，不会执行任何模型。
            blocked_limits = {**real_budget["limits"], "input_tokens": 1}
            blocked = AutonomousBudgetHooks(blocked_limits)
            try:
                await run_agent_async(db=db, agent_definition=parent, tool_definitions=tools, execution_context=context,
                    input_payload=generation.observe_generation_state(context), output_contract=AgentOutputContract(raw_input),
                    request_budget=blocked_limits, shared_budget_hooks=blocked, model_client=client,
                    execution_node=node, captured_definitions=snapshots)
            except AgentExecutionBudgetExceeded as error:
                assert error.diagnostic["failed_model_requests"] == []
            else:
                raise AssertionError("父模型未在联网前被验证预算阻断")
            assert journal.load()["budget"] == real_budget
            retry_action_id = uuid4().hex
            journal.begin(tool_call_id=retry_action_id, tool_key="generate_cases",
                args={"arguments_json": json.dumps({"item_indexes": [0, 1], "guidance": ""})}, prehash=action_state_hash(initial_state))
            budget_before = deepcopy(blocked.snapshot())
            replayed = await context.invoke_agent(agent_key=child.agent_key, input_payload=deepcopy(request),
                item_postprocessor="testing.postprocess_generation_batch_item",
                alternate_output_validators={"planning_feedback": partial(validate_generation_scope_feedback, item)})
            assert replayed == output and blocked.snapshot() == budget_before
            assert context.tool_calls[-1]["replayed"] is True and context.tool_calls[-1]["model_requests_sent"] == 0
            saved_call = journal.validated_agent_call(identity)
            assert saved_call["raw_output"] == raw_candidate and saved_call["output"] == output
            assert saved_call["result_type"] == "output"
            changed_input = deepcopy(request)
            changed_input["case_budget"] += 1
            changed_contract = replace(contract, input_payload=changed_input)
            changed_identity = _managed_agent_call_identity(execution_semantic_hash=semantic,
                agent_key=child.agent_key, contract=changed_contract)
            assert journal.validated_agent_call(changed_identity) is None
            failures = [rejected(lambda: _revalidate_managed_agent_call(saved=saved_call,
                contract=changed_contract, definition=child, context=context))]
            failures.append(rejected(lambda: journal.validated_agent_call({**identity,
                "execution_semantic_hash": failure_node.sdk_state["action_checkpoint"]["execution_semantic_hash"]})))
            validator_calls = []

            def validate_current(candidate):
                assert candidate == output
                validator_calls.append(action_state_hash(candidate))

            _revalidate_managed_agent_call(saved=saved_call, contract=replace(contract, candidate_validator=validate_current),
                                          definition=child, context=context)
            assert len(validator_calls) == 1
            other_contract = replace(contract, input_payload=prepared["requests"][1])
            other_identity = _managed_agent_call_identity(execution_semantic_hash=semantic,
                agent_key=child.agent_key, contract=other_contract)
            assert journal.validated_agent_call(other_identity) is None
            guidance_contract = replace(contract, input_payload={**request, "_planning_feedback": {
                "instruction": failure["error"]["message"], "source": "历史失败回执，仅验证请求身份，不是业务事实"}})
            guidance_identity = _managed_agent_call_identity(execution_semantic_hash=semantic,
                agent_key=child.agent_key, contract=guidance_contract)
            assert journal.validated_agent_call(guidance_identity) is None
            assert journal.load()["state"] == initial_state and journal.load()["budget"] == real_budget
            journal.fail(tool_call_id=retry_action_id, error=deepcopy(failure["error"]), budget=real_budget)
            assert len(journal.load()["validated_agent_calls"]) == 1
    finally:
        engine.dispose()
    assert action_state_hash([run.run_context, [[value.input_payload, value.output_payload, value.sdk_state] for value in nodes.values()]]) == before
    assert not db.new and not db.dirty and not db.deleted
    return {"status": "passed", "source_run_id": run.id, "generation_node_id": nodes["generation"].id,
        "generation_item_index": 0, "historical_504_node_id": failure_node.id,
        "historical_504_receipt_id": failure["tool_call_id"], "sqlite_node_id": local_node_id,
        "receipt_identity": identity, "raw_output_sha256": action_state_hash(raw_candidate),
        "normalized_output_sha256": action_state_hash(output), "sdk_managed_cache_hit": True,
        "parent_domain_not_partially_applied": True, "saved_and_shared_budget_unchanged": True,
        "current_candidate_validator_invoked": True, "changed_contract_rejections": failures,
        "changed_input_and_guidance_cache_misses": True, "failed_item_has_no_validated_receipt": True,
        "closed_and_reopened_sqlite": str(sqlite_path), "model_requests_sent": 0, "online_database_writes": 0,
        "mysql_transaction": "READ ONLY", "verification_limits": [
            "隔离组合真实成功候选与既有504回执，不是复现新的在线第二项504",
            "未覆盖在线进程强杀；模型返回到专业回执落盘之间仍有单请求重做窗口",
            "本次没有自然planning_feedback样本；回执保留结果类型，领域原有分流负责禁止问题报告进入用例缓存"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".sqlite").exists():
        raise ValueError("验证产物已存在，拒绝覆盖")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = asyncio.run(verify(db, args.run_id, args.output.with_suffix(".sqlite")))
        db.rollback()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(args.output), "model_requests_sent": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
