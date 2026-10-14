"""用真实历史输入和用量验证运行时计费恢复；只写隔离 SQLite，不请求模型。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper
from agents.usage import deserialize_usage
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentToolDefinition, AgentWorkflowDefinition
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.lifecycle import new_claim_token, transition_run
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import (
    _agent_instance_quota_usage, _record_agent_usage, _reserve_agent_request, _run_usage,
)
from modules.agent_platform.sdk_adapter import _model_settings, _sdk_output_type
from modules.agent_platform.service import _initial_run_context, _restore_checkpoint_node
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from modules.agent_platform.test_generation_review_agent import finish_review, observe_review_state, validate_review_resolution
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record, isolated_repository
from scripts.qa.verify.verify_review_agent_real_data import load_nodes, replay_domain, review_arguments
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition


def activate(repo, run) -> None:
    """只在真实行的 SQLite 副本上安排待恢复生命周期。"""
    run.status, run.finished_at, run.claim_token = "pending", None, None
    repo.db.commit()
    transition_run(repo, run, "running", event_type="run_started", payload={})
    run.claim_token = new_claim_token()
    run.lease_expires_at = datetime.utcnow() + timedelta(minutes=10)
    repo.db.commit()


def copy_retry_run(repo, source_run, checkpoint):
    target = copy_record(source_run)
    target.id = None
    target.run_context = _initial_run_context(
        execution_limits=source_run.run_context["execution_limits"],
        run_attempt=int(source_run.run_context.get("run_attempt") or 1) + 1,
    )
    repo.db.add(target)
    repo.db.flush()
    restored = _restore_checkpoint_node(repo, checkpoint=checkpoint, run_id=target.id)
    repo.db.commit()
    activate(repo, target)
    return target, restored


async def replay_usage(definition, source_input, usage, limits, journal):
    """SDK 钩子接收已记录的真实用量；不执行 Runner、不建立模型连接。"""
    agent = Agent(
        name=definition.name, instructions=definition.instructions,
        model_settings=_model_settings(dict(definition.runtime_config or {})),
        output_type=_sdk_output_type(definition, has_tools=False),
    )
    context = RunContextWrapper(context=source_input)
    inputs = [{"role": "user", "content": json.dumps(source_input, ensure_ascii=False)}]
    hooks = AutonomousBudgetHooks(limits, on_change=journal.save_budget)
    await hooks.on_llm_start(context, agent, definition.instructions, inputs)
    hooks.settle_request(context, deserialize_usage(usage))
    return hooks.snapshot()


def verify(online, source_run, nodes, arguments, transitions):
    workflow = online.get(AgentWorkflowDefinition, source_run.workflow_definition_id)
    model_node = nodes["final_review_repairs"]
    definition = online.get(AgentDefinition, model_node.agent_definition_id)
    recorded = next(item for item in model_node.sdk_state["items"] if item["usage"]["requests"] == 1)
    model_input = deepcopy(nodes["prepare_final_review_repairs"].output_payload["items"][recorded["item_index"]])
    current = deepcopy(recorded["usage"])
    coordinator = builtin_definition(AgentDefinition, next(
        spec for spec in BUILTIN_AGENT_SPECS if spec["agent_key"] == "test_generation_review_coordinator"
    ))
    coordinator_tools = [builtin_definition(AgentToolDefinition, spec) for spec in BUILTIN_TOOL_SPECS
                         if spec["tool_key"] in coordinator.runtime_config["tool_keys"]]
    source_node = nodes["prepare_final_review"]
    with isolated_repository(source_run, workflow, source_node) as repo:
        run = repo.db.get(AgentRun, source_run.id)
        node = repo.db.get(AgentNodeRun, source_node.id)
        activate(repo, run)
        # 元数据映射到新持久节点，需求、用例、差异和模型用量均来自同一真实运行。
        node.node_key, node.node_type = "resolve_final_review", "agent_network"
        node.status, node.finished_at = "running", None
        node.input_payload, node.sdk_state = deepcopy(arguments), {}
        repo.db.commit()
        journal = AgentActionJournal(repo, run_id=run.id, node_run_id=node.id, expected_claim_token=run.claim_token)
        journal.init(state=transitions[0]["before"], input_hash=action_state_hash(arguments))
        scope = f"{node.node_key}-instance-001"
        model_observation = observe_review_state(ToolExecutionContext(
            db=repo.db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
            node_key=node.node_key, run_input=deepcopy(run.input_payload),
            action_input=deepcopy(arguments), action_state=deepcopy(transitions[0]["before"]),
        ), {})

        def reserve(active_run, active_node):
            return _reserve_agent_request(
                repo=repo, run=active_run, node_run=active_node, definition=coordinator,
                tools=coordinator_tools, input_payload=model_observation, quota_scope_key=scope,
            )

        initial_usage = _run_usage(run)
        reservation = reserve(run, node)
        assert _run_usage(run)["attempted_requests"] == initial_usage["attempted_requests"]
        assert _agent_instance_quota_usage(run, scope) == reservation
        budget = asyncio.run(replay_usage(definition, model_input, current, reservation, journal))
        assert budget["reported_usage"] == current
        before_resume = deepcopy(run.run_context)
        node_id, run_id = node.id, run.id
        # 丢弃所有 ORM 对象，下一次预留仅从已提交检查点和账本读取。
        repo.db.expunge_all()
        run, node = repo.db.get(AgentRun, run_id), repo.db.get(AgentNodeRun, node_id)
        assert reserve(run, node) == reservation
        assert run.run_context == before_resume
        assert node.sdk_state["action_checkpoint"]["budget"] == budget

        # 未完成检查点复制到新 Run：继承原限额与已消耗量，不能发放新预算。
        resumed_run, resumed_node = copy_retry_run(repo, run, node)
        resumed_node.status = "running"
        repo.db.commit()
        assert not any(_run_usage(resumed_run).values())
        assert reserve(resumed_run, resumed_node) == reservation
        assert _agent_instance_quota_usage(resumed_run, scope) == reservation
        restored_budget = resumed_node.sdk_state["action_checkpoint"]["budget"]
        assert AutonomousBudgetHooks(reservation, checkpoint=restored_budget).snapshot() == budget
        before_second_reserve = deepcopy(resumed_run.run_context)
        assert reserve(resumed_run, resumed_node) == reservation
        assert resumed_run.run_context == before_second_reserve

        def record(active_run, active_node):
            _record_agent_usage(
                repo=repo, run=active_run, node_run=active_node, current=current,
                reservation=reservation, quota_scope_key=scope, quota_charge=budget["charged"],
            )

        record(resumed_run, resumed_node)
        assert _agent_instance_quota_usage(resumed_run, scope) == budget["charged"]
        assert not any(_run_usage(resumed_run).values())
        assert resumed_node.sdk_state["action_usage_recorded"] is True
        settled = deepcopy(resumed_run.run_context)
        record(resumed_run, resumed_node)
        assert reserve(resumed_run, resumed_node) == reservation
        assert resumed_run.run_context == settled

        # 在生产领域校验通过后写入实际终止回执，再验证已完成检查点的迁移。
        completed_context = ToolExecutionContext(
            db=repo.db, user_id=source_run.user_id, project_id=source_run.project_id,
            run_id=resumed_run.id, node_key=resumed_node.node_key,
            run_input=deepcopy(source_run.input_payload), action_input=deepcopy(arguments),
            action_state=deepcopy(transitions[-1]["after"]),
        )
        terminal = finish_review(completed_context, {})
        output = validate_review_resolution(completed_context, {"input_payload": arguments, "output": terminal})
        completion_journal = AgentActionJournal(
            repo, run_id=resumed_run.id, node_run_id=resumed_node.id,
            expected_claim_token=resumed_run.claim_token,
        )
        for index, action in enumerate(transitions):
            loaded = completion_journal.load()
            assert loaded["state"] == action["before"]
            call_id = f"real-replay-action-{index}"
            completion_journal.begin(
                tool_call_id=call_id, tool_key=action["tool_key"],
                args={"arguments_json": json.dumps(action["arguments"], ensure_ascii=False)},
                prehash=loaded["state_hash"],
            )
            completion_journal.complete(tool_call_id=call_id, nextstate=action["after"], result=action["result"])
        loaded = completion_journal.load()
        completion_journal.begin(tool_call_id="real-replay-finish", tool_key="finish_review",
                                 args={"arguments_json": "{}"}, prehash=loaded["state_hash"])
        completion_journal.complete(tool_call_id="real-replay-finish",
                                    nextstate=completed_context.action_state, result=terminal, budget=budget)
        resumed_node.output_payload, resumed_node.status = output, "success"
        repo.db.commit()
        completed_run, completed_node = copy_retry_run(repo, resumed_run, resumed_node)
        assert completed_node.status == "success"
        assert completed_node.sdk_state["action_usage_recorded"] is True
        empty_context = deepcopy(completed_run.run_context)
        assert reserve(completed_run, completed_node) == reservation
        record(completed_run, completed_node)
        assert completed_run.run_context == empty_context
        assert not any(_run_usage(completed_run).values())
        assert not any(_agent_instance_quota_usage(completed_run, scope).values())
        return {
            "source_usage_node_id": model_node.id, "source_usage_item_index": recorded["item_index"],
            "reservation": reservation, "recorded_usage": current,
            "same_run_resume_did_not_reserve_twice": True,
            "partial_checkpoint_new_run_kept_limits_and_consumption": True,
            "partial_checkpoint_second_reserve_did_not_charge_twice": True,
            "settlement_marker_prevented_duplicate_record_and_reserve": True,
            "completed_checkpoint_new_run_usage_remained_zero": True,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    options = parser.parse_args()
    with SessionLocal() as online:
        online.execute(text("SET TRANSACTION READ ONLY"))
        run = online.get(AgentRun, options.run_id)
        if run is None or run.status != "success":
            raise ValueError("必须提供真实成功运行")
        nodes = load_nodes(online, run.id)
        arguments = review_arguments(run, nodes)
        _, transitions, _ = replay_domain(online, run, nodes, arguments)
        report = verify(online, run, nodes, arguments, transitions)
        assert not online.new and not online.dirty and not online.deleted
        online.rollback()
        print(json.dumps({
            "run_id": options.run_id, "checks": report, "model_requests_sent": 0,
            "online_database_writes": 0,
            "validation_scope": "真实历史用量回放及 SQLite 运行时计费恢复；未执行控制 Agent 或 MySQL 并发锁验证",
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
