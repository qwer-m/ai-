"""只读真实失败及恢复记录，在隔离 SQLite 验证预算检查点与 Run 用量原子入账。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.runtime import _record_agent_usage, _reserve_agent_request, _run_usage
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.service import _initial_run_context, _restore_checkpoint_node
from modules.agent_platform.usage import action_budget_usage, initialize_action_usage
from scripts.qa.verify.verify_action_quota_real_data import activate
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record, isolated_repository


def fingerprint(record):
    return action_state_hash({column.key: str(getattr(record, column.key))
                              for column in record.__table__.columns})


def reserve(repo, run, node, definition):
    return _reserve_agent_request(
        repo=repo, run=run, node_run=node, definition=definition, tools=[],
        input_payload=repo.read_node_input(node), quota_scope_key=f"{node.node_key}-instance-001",
    )


def journal(repo, run, node):
    return AgentActionJournal(repo, run_id=run.id, node_run_id=node.id, expected_claim_token=run.claim_token)


def record(repo, run, node, reservation):
    budget = node.sdk_state["action_checkpoint"]["budget"]
    _record_agent_usage(
        repo=repo, run=run, node_run=node, current=budget["reported_usage"],
        reservation=reservation, quota_scope_key=f"{node.node_key}-instance-001",
        quota_charge=budget["charged"],
    )


def reset_stage(repo, run, node, *, upstream_usage):
    """只安排执行状态与账本起点；真实输入、动作、证据与模型消费快照保持原样。"""
    state = deepcopy(node.sdk_state)
    state.pop("action_usage_recorded", None)
    state.pop("action_usage", None)
    state["action_checkpoint"]["budget"] = AutonomousBudgetHooks(
        state["action_checkpoint"]["budget"]["limits"],
    ).snapshot()
    node.sdk_state, node.status, node.finished_at = state, "running", None
    context = deepcopy(run.run_context)
    context["usage"] = deepcopy(upstream_usage)
    context["agent_instance_quota_usage"] = {}
    context.pop("quota_usage", None)
    run.run_context = context
    repo.db.commit()
    activate(repo, run)


def verify_failure(online, source_run, source_node, workflow, definition):
    final_budget = deepcopy(source_node.sdk_state["action_checkpoint"]["budget"])
    upstream = deepcopy(source_run.run_context["usage"])
    # 旧准入将整个自主会话当作一次请求；真实消费将由逐请求预算重新回放。
    upstream["attempted_requests"] -= 1
    expected = {key: upstream[key] + action_budget_usage(final_budget)[key] for key in upstream}
    with isolated_repository(source_run, source_node, workflow) as repo:
        run, node = repo.db.get(AgentRun, source_run.id), repo.db.get(AgentNodeRun, source_node.id)
        reset_stage(repo, run, node, upstream_usage=upstream)
        reserve(repo, run, node, definition)
        assert _run_usage(run) == upstream
        journal(repo, run, node).save_budget(final_budget)
        assert _run_usage(run) == expected
        node.status = "failed"
        repo.db.commit()
        repo.db.expunge_all()
        run, node = repo.db.get(AgentRun, source_run.id), repo.db.get(AgentNodeRun, source_node.id)
        assert _run_usage(run) == expected
        assert node.sdk_state["action_checkpoint"]["budget"] == final_budget
        result = {"run_id": source_run.id, "node_id": node.id, "source_usage": source_run.run_context["usage"],
                  "replayed_usage": expected, "failed_node_usage_committed": True,
                  "new_request_not_counted_before_sdk_admission": True}
    with isolated_repository(source_run, source_node, workflow) as repo:
        run, node = repo.db.get(AgentRun, source_run.id), repo.db.get(AgentNodeRun, source_node.id)
        activate(repo, run)
        node.status, node.finished_at = "running", None
        repo.db.commit()
        reserve(repo, run, node, definition)
        assert _run_usage(run) == expected
        reserve(repo, run, node, definition)
        assert _run_usage(run) == expected
        result["existing_failed_checkpoint_same_run_reconciled_once"] = True
    return result


def verify_recovery(source_run, nodes, workflow, definition):
    budgets = [deepcopy(node.sdk_state["action_checkpoint"]["budget"]) for node in nodes]
    assert len({node.sdk_state["action_checkpoint"]["input_hash"] for node in nodes}) == 1
    assert len({node.sdk_state["action_checkpoint"]["execution_semantic_hash"] for node in nodes}) == 1
    assert nodes[-1].status == "success" and any(node.status == "failed" for node in nodes[:-1])
    zero = dict.fromkeys(_run_usage(source_run), 0)
    with isolated_repository(source_run, nodes[0], workflow) as repo:
        run, active = repo.db.get(AgentRun, source_run.id), repo.db.get(AgentNodeRun, nodes[0].id)
        reset_stage(repo, run, active, upstream_usage=zero)
        reservation = reserve(repo, run, active, definition)
        transitions = []
        for index, budget in enumerate(budgets):
            if index:
                previous = active
                previous.status = "failed"
                active = copy_record(nodes[index])
                state = deepcopy(active.sdk_state)
                state.pop("action_usage_recorded", None)
                state["action_usage"] = deepcopy(previous.sdk_state["action_usage"])
                state["action_checkpoint"]["budget"] = deepcopy(budgets[index - 1])
                active.sdk_state, active.status, active.finished_at = state, "running", None
                repo.db.add(active)
                repo.db.commit()
                assert reserve(repo, run, active, definition) == reservation
            before = deepcopy(_run_usage(run))
            current_journal = journal(repo, run, active)
            current_journal.save_budget(budget)
            assert _run_usage(run) == action_budget_usage(budget)
            after = deepcopy(run.run_context)
            current_journal.save_budget(budget)
            assert run.run_context == after
            transitions.append({"node_id": nodes[index].id, "status": nodes[index].status,
                                "before": before, "after": _run_usage(run)})
        record(repo, run, active, reservation)
        after = deepcopy(run.run_context)
        record(repo, run, active, reservation)
        assert reserve(repo, run, active, definition) == reservation
        assert run.run_context == after
        assert _run_usage(run) == action_budget_usage(budgets[-1])
        assert run.run_context["agent_instance_quota_usage"][f"{active.node_key}-instance-001"] == budgets[-1]["charged"]
        try:
            journal(repo, run, active).save_budget(budgets[0])
        except ValueError as exc:
            assert "倒退" in str(exc)
        else:
            raise AssertionError("累计用量倒退必须拒绝并回滚")
        repo.db.refresh(run)
        repo.db.refresh(active)
        assert run.run_context == after
        assert active.sdk_state["action_checkpoint"]["budget"] == budgets[-1]
        same_run = {"transitions": transitions, "duplicate_save_and_success_no_double_count": True,
                    "regressive_checkpoint_and_usage_rolled_back_together": True,
                    "charged_attempts_include_interrupted_requests": budgets[-1]["charged"]["attempted_requests"]}
        active.status = "success"
        repo.db.commit()
        completed_retry = copy_record(run)
        completed_retry.id = None
        completed_retry.run_context = _initial_run_context(execution_limits=run.run_context["execution_limits"], run_attempt=2)
        repo.db.add(completed_retry)
        repo.db.flush()
        restored_success = _restore_checkpoint_node(repo, checkpoint=active, run_id=completed_retry.id)
        repo.db.commit()
        activate(repo, completed_retry)
        assert restored_success.status == "success"
        reserve(repo, completed_retry, restored_success, definition)
        record(repo, completed_retry, restored_success, reservation)
        assert _run_usage(completed_retry) == zero
        assert completed_retry.run_context["agent_instance_quota_usage"] == {}
        same_run["completed_checkpoint_new_run_usage_remained_zero"] = True

    # 新 Run 通过现有恢复函数复制真实失败节点，历史消费保留预算但不重新汇入新 Run。
    source_partial = nodes[1]
    initial_budget = budgets[1]
    with isolated_repository(source_run, source_partial, workflow) as repo:
        parent = repo.db.get(AgentRun, source_run.id)
        partial = repo.db.get(AgentNodeRun, source_partial.id)
        initialize_action_usage(parent, partial, baseline=action_budget_usage(initial_budget))
        repo.db.commit()
        retry = copy_record(parent)
        retry.id = None
        retry.parent_run_id = parent.id
        retry.run_context = _initial_run_context(execution_limits=parent.run_context["execution_limits"], run_attempt=2)
        repo.db.add(retry)
        repo.db.flush()
        restored = _restore_checkpoint_node(repo, checkpoint=partial, run_id=retry.id)
        repo.db.commit()
        activate(repo, retry)
        restored.status = "running"
        repo.db.commit()
        assert journal(repo, retry, restored).load()["budget"] == initial_budget
        reservation = reserve(repo, retry, restored, definition)
        assert _run_usage(retry) == zero
        assert restored.sdk_state["action_checkpoint"]["budget"] == initial_budget
        journal(repo, retry, restored).save_budget(budgets[-1])
        expected = {key: action_budget_usage(budgets[-1])[key] - action_budget_usage(initial_budget)[key]
                    for key in zero}
        assert _run_usage(retry) == expected
        record(repo, retry, restored, reservation)
        after = deepcopy(retry.run_context)
        record(repo, retry, restored, reservation)
        assert retry.run_context == after
        assert after["agent_instance_quota_usage"][f"{restored.node_key}-instance-001"] == budgets[-1]["charged"]
        retry_check = {"source_node_id": source_partial.id, "history_baseline": action_budget_usage(initial_budget),
                       "new_run_usage": expected, "new_run_quota_keeps_previous_consumption": True}
    return {"same_run": same_run, "new_run": retry_check}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-run-id", type=int, default=39)
    parser.add_argument("--recovery-run-id", type=int, default=36)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    with SessionLocal() as online:
        online.execute(text("SET TRANSACTION READ ONLY"))
        failed = online.get(AgentRun, options.failed_run_id)
        recovered = online.get(AgentRun, options.recovery_run_id)
        assert failed is not None and failed.status == "failed" and recovered is not None and recovered.status == "success"
        all_records = [failed, recovered]
        original_fingerprints = {id(record): fingerprint(record) for record in all_records}
        cases = {}
        for run in (failed, recovered):
            ids = online.query(AgentNodeRun.id).filter(AgentNodeRun.run_id == run.id).order_by(AgentNodeRun.id).all()
            all_nodes = [online.get(AgentNodeRun, node_id) for (node_id,) in ids]
            action_nodes = []
            for node in all_nodes:
                if node.sdk_state.get("action_checkpoint"):
                    materialized = copy_record(node)
                    materialized.input_payload = AgentRunRepository(online).read_node_input(node)
                    action_nodes.append(materialized)
            all_records.extend(all_nodes)
            original_fingerprints.update({id(record): fingerprint(record) for record in all_nodes})
            workflow = online.get(AgentWorkflowDefinition, run.workflow_definition_id)
            if run.id == failed.id:
                selected = action_nodes[-1]
                definition = online.get(AgentDefinition, selected.agent_definition_id)
                cases["failed_coordinator"] = verify_failure(online, run, selected, workflow, definition)
            else:
                key = next(node.node_key for node in action_nodes if node.status == "failed")
                selected = [node for node in action_nodes if node.node_key == key]
                definition = online.get(AgentDefinition, selected[0].agent_definition_id)
                cases["historical_recovery"] = verify_recovery(run, selected, workflow, definition)
        assert not online.new and not online.dirty and not online.deleted
        for record in all_records:
            online.refresh(record)
            assert fingerprint(record) == original_fingerprints[id(record)]
        online.rollback()
    report = {"status": "passed", "cases": cases, "model_requests_sent": 0, "online_database_writes": 0,
              "validation_scope": "真实 Run 39 失败与 Run 36 四次恢复用量；隔离 SQLite 验证预算持久化、成功结算和新 Run 恢复；未请求模型、未验证 MySQL 并发锁。"}
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
