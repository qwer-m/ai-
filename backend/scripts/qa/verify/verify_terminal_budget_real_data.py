"""以真实取消记录在隔离 SQLite 回放终态预算收尾；线上仅只读，不请求模型。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent, AgentWorkflowDefinition
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import interrupt_pending_budget
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.lifecycle import RunLeaseLost, transition_run
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import (
    _NodeExecutionSetup, _RunCancelled, _complete_node_execution, _handle_workflow_exception,
    _refresh_run_is_cancelled, _renew_progress_lease,
)
from modules.agent_platform.usage import interrupt_node_pending_requests
from scripts.qa.verify.verify_action_quota_real_data import activate
from scripts.qa.verify.verify_action_usage_real_data import fingerprint
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record, isolated_repository


def budget_counts(value):
    return {key: value[key] for key in ("pending_requests", "completed_requests", "interrupted_requests")}


def verify_transition(source_run, source_node, other_nodes, workflow, *, target, premarked=False, rollback=False):
    records = [source_run, source_node, *other_nodes, workflow]
    original_budget = deepcopy(source_node.sdk_state["action_checkpoint"]["budget"])
    original_usage = deepcopy(source_run.run_context["usage"])
    with isolated_repository(*records) as repo:
        run = repo.db.get(AgentRun, source_run.id)
        node = repo.db.get(AgentNodeRun, source_node.id)
        # 仅在真实副本中回到终止入口；原模型消费、事实、检查点和回执均不制造或改写。
        activate(repo, run)
        node.status, node.finished_at, node.error_message = "running", None, ""
        if premarked:
            # 实际异常处理器先将当前节点标为 failed，再进入统一 Run 终态入口。
            node.status, node.finished_at = target, datetime.utcnow()
        repo.db.commit()
        original_state = deepcopy(node.sdk_state)
        before_run = fingerprint(run)
        before_node = fingerprint(node)
        other_hashes = {row.id: fingerprint(repo.db.get(AgentNodeRun, row.id)) for row in other_nodes}
        journal = AgentActionJournal(repo, run_id=run.id, node_run_id=node.id, expected_claim_token=run.claim_token)
        assert transition_run(repo, run, target, event_type="run_" + target, payload={},
                              node_run_id=node.id if premarked else None)
        if rollback:
            repo.db.rollback()
            repo.db.refresh(run)
            repo.db.refresh(node)
            assert fingerprint(run) == before_run and fingerprint(node) == before_node
            assert repo.db.query(AgentRunEvent).filter(AgentRunEvent.event_type == "run_" + target).count() == 0
            return {"terminal_budget_and_status_rolled_back_together": True}
        repo.db.commit()
        repo.db.refresh(run)
        repo.db.refresh(node)
        finalized = node.sdk_state["action_checkpoint"]["budget"]
        expected = deepcopy(original_state)
        expected["action_checkpoint"]["budget"] = interrupt_pending_budget(original_budget)
        assert node.sdk_state == expected
        assert node.status == run.status == target and run.claim_token is None
        assert finalized["pending_requests"] == 0
        assert finalized["interrupted_requests"] == original_budget["interrupted_requests"] + original_budget["pending_requests"]
        assert finalized["charged"] == original_budget["charged"]
        assert finalized["reported_usage"] == original_budget["reported_usage"]
        assert sum(budget_counts(finalized).values()) == finalized["charged"]["attempted_requests"]
        assert run.run_context["usage"] == original_usage
        assert node.sdk_state["action_usage"] == original_state["action_usage"]
        # 同一终态重复请求不增事件、不改预算，也不能让原 worker 回填旧预算。
        saved_run, saved_node = fingerprint(run), fingerprint(node)
        events = repo.db.query(AgentRunEvent).count()
        assert not transition_run(repo, run, target, event_type="run_" + target, payload={})
        repo.db.commit()
        assert fingerprint(run) == saved_run and fingerprint(node) == saved_node
        assert repo.db.query(AgentRunEvent).count() == events
        try:
            journal.save_budget(original_budget)
        except RunLeaseLost:
            pass
        else:
            raise AssertionError("取消后旧 worker 不能继续写检查点")
        repo.db.refresh(run)
        repo.db.refresh(node)
        assert fingerprint(run) == saved_run and fingerprint(node) == saved_node
        assert all(fingerprint(repo.db.get(AgentNodeRun, row.id)) == other_hashes[row.id] for row in other_nodes)
        return {"target": target, "current_node_premarked": premarked,
                "before": budget_counts(original_budget), "after": budget_counts(finalized),
                "charged_and_reported_usage_unchanged": True, "run_usage_unchanged": True,
                "action_usage_cursor_unchanged": True, "pending_count_conserved": True,
                "repeat_terminal_idempotent": True, "stale_worker_write_rejected": True,
                "other_nodes_unchanged": True}


def real_execution_setup(repo, run, node, workflow):
    """只从真实工作流、节点输入和运行元数据重建本地调用上下文。"""
    definition = parse_execution_definition(workflow.definition)
    workflow_node = next(item for item in definition.execution_order() if item.node_key == node.node_key)
    context = ToolExecutionContext(db=repo.db, user_id=run.user_id, project_id=run.project_id,
        run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
        artifacts=deepcopy(run.run_context.get("artifacts") or {}), claim_token=run.claim_token)
    setup = _NodeExecutionSetup(previous=None, node_run=node, node_input=repo.read_node_input(node),
        attempt=node.attempt, execution_context=context)
    return workflow_node, setup


def verify_late_cancel_snapshot(source_run, source_node, other_nodes, workflow, *, entry, autoflush):
    """重放真实旧快照迟到顺序；只安排生命周期，不制造模型响应或预算。"""
    stale_state = deepcopy(source_node.sdk_state)
    stale_output = deepcopy(source_node.output_payload)
    with isolated_repository(source_run, source_node, *other_nodes, workflow) as repo:
        repo.db.autoflush = autoflush
        run = repo.db.get(AgentRun, source_run.id)
        node = repo.db.get(AgentNodeRun, source_node.id)
        activate(repo, run)
        node.status, node.finished_at, node.error_message = "running", None, ""
        repo.db.commit()
        workflow_node, setup = real_execution_setup(repo, run, node, workflow)
        claim_token = run.claim_token
        # 服务独立会话正常取消；worker 的 Run 对象仍是取消前的真实副本。
        with Session(repo.db.get_bind(), autoflush=False, expire_on_commit=False) as service_db:
            service_repo = AgentRunRepository(service_db)
            service_run = service_repo.get_run_for_update(run_id=run.id)
            assert transition_run(service_repo, service_run, "cancelled", event_type="run_cancelled", payload={})
            service_db.commit()
            finalized = deepcopy(service_db.get(AgentNodeRun, node.id).sdk_state)
            finalized_usage = deepcopy(service_run.run_context["usage"])
        # 节点已读到终态；迟到结果仍携带原始 pending 快照，构成真实新旧状态边界。
        repo.db.refresh(node)
        assert node.sdk_state == finalized
        assert finalized["action_checkpoint"]["budget"]["pending_requests"] == 0
        writes = []

        def record_node_update(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("UPDATE AGENT_NODE_RUNS"):
                writes.append("UPDATE agent_node_runs")

        event.listen(repo.db.get_bind(), "before_cursor_execute", record_node_update)
        returned = None
        caught = None
        try:
            # 使用原 ORM JSON 赋值路径，明确产生未提交的迟到写入，不使用 mock/patch。
            node.sdk_state = deepcopy(stale_state)
            try:
                if entry == "complete_node_execution":
                    _complete_node_execution(repo, run, workflow_node, setup, stale_output)
                elif entry == "handle_workflow_exception":
                    returned = _handle_workflow_exception(repo, run.id, claim_token,
                        _RunCancelled(f"Agent Run {run.id} 已取消"))
                elif entry == "refresh_run_is_cancelled":
                    returned = _refresh_run_is_cancelled(repo, run)
                elif entry == "renew_progress_lease":
                    _renew_progress_lease(repo, run)
                else:
                    raise ValueError(entry)
            except (_RunCancelled, RunLeaseLost) as exc:
                caught = type(exc).__name__
            # Core 查询读取实际事务内容，不通过 ORM 自动刷新；不记录 SQL 参数或大对象正文。
            after_entry = deepcopy(repo.db.connection().execute(
                select(AgentNodeRun.sdk_state).where(AgentNodeRun.id == node.id)
            ).scalar_one())
            entry_writes = len(writes)
            # 验证读取取消后是否仍残留可被后续提交冲回的 dirty 快照。
            repo.db.commit()
            after_commit = deepcopy(repo.db.connection().execute(
                select(AgentNodeRun.sdk_state).where(AgentNodeRun.id == node.id)
            ).scalar_one())
            saved_usage = deepcopy(repo.db.connection().execute(
                select(AgentRun.run_context).where(AgentRun.id == run.id)
            ).scalar_one()["usage"])
        finally:
            event.remove(repo.db.get_bind(), "before_cursor_execute", record_node_update)
        assert saved_usage == finalized_usage == source_run.run_context["usage"]
        for state in (after_entry, after_commit):
            budget = state["action_checkpoint"]["budget"]
            assert budget["charged"] == stale_state["action_checkpoint"]["budget"]["charged"]
            assert budget["reported_usage"] == stale_state["action_checkpoint"]["budget"]["reported_usage"]
        return {
            "entry": entry, "session_autoflush": autoflush,
            "matches_production_session_autoflush": autoflush == SessionLocal.kw["autoflush"],
            "return_value": returned, "caught_exception": caught,
            "finalized_before_late_entry": budget_counts(finalized["action_checkpoint"]["budget"]),
            "after_entry": budget_counts(after_entry["action_checkpoint"]["budget"]),
            "after_following_commit": budget_counts(after_commit["action_checkpoint"]["budget"]),
            "node_updates_during_entry": entry_writes,
            "node_updates_including_following_commit": len(writes),
            "terminal_state_preserved_during_entry": after_entry == finalized,
            "terminal_state_preserved_after_following_commit": after_commit == finalized,
            "charged_reported_and_run_usage_unchanged": True,
            "stale_state_sha256": action_state_hash(stale_state),
            "finalized_state_sha256": action_state_hash(finalized),
            "after_commit_state_sha256": action_state_hash(after_commit),
        }


def verify_completion_ledger_refresh(source_run, nodes, workflow, history, *, autoflush):
    """使用本轮真实早期用量和成功节点，验证完成提交从数据库读取当前完整账本。"""
    assert history["run_id"] == source_run.id
    current_usage = source_run.run_context["usage"]
    earlier = next(item for item in history["snapshots"] if item["usage"] != current_usage)
    completed_node = next(node for node in reversed(nodes) if node.status == "success")
    with isolated_repository(source_run, *nodes, workflow) as repo:
        repo.db.autoflush = autoflush
        run = repo.db.get(AgentRun, source_run.id)
        node = repo.db.get(AgentNodeRun, completed_node.id)
        activate(repo, run)
        node.status, node.finished_at = "running", None
        repo.db.commit()
        expected_context = deepcopy(run.run_context)
        output = deepcopy(node.output_payload)
        workflow_node, setup = real_execution_setup(repo, run, node, workflow)
        # 只将真实较早用量放回内存，模拟旧 Run 账本；数据库保留本轮真实最新用量。
        run.run_context = {**deepcopy(expected_context), "usage": deepcopy(earlier["usage"])}
        _complete_node_execution(repo, run, workflow_node, setup, output)
        repo.db.refresh(run)
        repo.db.refresh(node)
        assert run.run_context == expected_context
        assert node.status == "success" and node.output_payload == output
        return {"session_autoflush": autoflush, "source_completed_node_id": completed_node.id,
                "earlier_usage_observed_at": earlier["observed_at"], "earlier_usage": earlier["usage"],
                "current_usage": current_usage, "whole_run_context_preserved": True,
                "source_node_output_unchanged": True,
                "limit": "使用真实成功节点及真实早晚账本，在隔离副本重新安排完成入口；没有模型执行或新业务结果"}


def verify(options):
    history_bytes = options.usage_history.read_bytes()
    history = json.loads(history_bytes)
    with SessionLocal(autoflush=False) as online:
        online.execute(text("SET TRANSACTION READ ONLY"))
        source_run = online.get(AgentRun, options.run_id)
        assert source_run is not None and source_run.status == "cancelled"
        ids = online.query(AgentNodeRun.id).filter(AgentNodeRun.run_id == source_run.id).order_by(AgentNodeRun.id).all()
        nodes = [online.get(AgentNodeRun, node_id) for (node_id,) in ids]
        selected = [node for node in nodes if (node.sdk_state.get("action_checkpoint") or {}).get("budget", {}).get("pending_requests", 0)]
        assert len(selected) == 1
        source_node = selected[0]
        others = [node for node in nodes if node.id != source_node.id]
        workflow = online.get(AgentWorkflowDefinition, source_run.workflow_definition_id)
        hashes = {row.id: fingerprint(row) for row in nodes}
        run_hash = fingerprint(source_run)
        budget = deepcopy(source_node.sdk_state["action_checkpoint"]["budget"])
        expected = interrupt_pending_budget(budget)
        assert budget == source_node.sdk_state["action_checkpoint"]["budget"]
        assert interrupt_pending_budget(expected) == expected
        cases = {
            "cancelled": verify_transition(source_run, source_node, others, workflow, target="cancelled"),
            "failed_already_marked_node": verify_transition(source_run, source_node, others, workflow, target="failed", premarked=True),
            "transaction_rollback": verify_transition(source_run, source_node, others, workflow, target="cancelled", rollback=True),
        }
        late_cases = [
            verify_late_cancel_snapshot(source_run, source_node, others, workflow, entry=entry, autoflush=autoflush)
            for autoflush in (False, True)
            for entry in ("complete_node_execution", "handle_workflow_exception",
                          "refresh_run_is_cancelled", "renew_progress_lease")
        ]
        late_protected = all(case["terminal_state_preserved_during_entry"] and
                             case["terminal_state_preserved_after_following_commit"] for case in late_cases)
        assert late_protected, "取消边界仍允许旧快照覆盖终态预算"
        assert all(case["node_updates_including_following_commit"] == 0 for case in late_cases)
        ledger_cases = [verify_completion_ledger_refresh(source_run, nodes, workflow, history, autoflush=value)
                        for value in (False, True)]
        map_checks = []
        for source_map in nodes:
            if source_map.node_type != "agent_map":
                continue
            copied = copy_record(source_map)
            before = deepcopy(copied.sdk_state)
            snapshots = [item["budget_usage"] for item in before.get("items", []) if item.get("budget_usage")]
            assert all(item["pending_requests"] == 0 for item in snapshots)
            interrupt_node_pending_requests(copy_record(source_run), copied)
            assert copied.sdk_state == before
            map_checks.append({"node_id": source_map.id, "node_key": source_map.node_key,
                               "actual_item_budget_count": len(snapshots), "actual_pending_budget_count": 0,
                               "completed_item_budgets_unchanged": True})
        assert not online.new and not online.dirty and not online.deleted
        online.refresh(source_run)
        assert fingerprint(source_run) == run_hash
        for row in nodes:
            online.refresh(row)
            assert fingerprint(row) == hashes[row.id]
        source_ids = (source_run.id, source_node.id)
        source_usage = deepcopy(source_run.run_context["usage"])
        online.rollback()
    root = Path(__file__).resolve().parents[3]
    files = [root / "modules/agent_platform" / name for name in (
        "autonomous_budget.py", "usage.py", "run_repository.py", "lifecycle.py", "runtime.py")]
    files.append(root / "core/db/database.py")
    return {"status": "passed" if late_protected else "baseline_vulnerability_confirmed",
            "source_run_id": source_ids[0], "source_node_id": source_ids[1],
            "original_run_fingerprint": run_hash, "original_node_fingerprint": hashes[source_ids[1]],
            "original_budget": budget, "finalized_budget": expected, "original_run_usage": source_usage,
            "cases": cases, "agent_map_checks": map_checks,
            "stale_worker_cases": late_cases, "stale_worker_terminal_state_protected": late_protected,
            "completion_ledger_refresh_cases": ledger_cases,
            "usage_history_source": {"path": str(options.usage_history.resolve()),
                                     "sha256": hashlib.sha256(history_bytes).hexdigest()},
            "production_session_autoflush": SessionLocal.kw["autoflush"],
            "production_sha256": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
            "online_database_writes": 0, "model_requests_sent": 0,
            "verification_limit": "真实 Run40 取消快照在隔离 SQLite 回放。验证正常取消、预先标记失败的当前节点、重复终态、事务回滚和 journal 旧租约拒绝。额外安排服务正常终止后旧原始 SDK 快照迟到的确定性顺序，直接调用 runtime 节点完成、异常处理及两个刷新入口；并非当前 Run40 实际经过这些入口或 MySQL 并发竞态的证明。生产 SessionLocal 的 autoflush=False，True 仅为通用 Session 边界；读取后的显式提交用于检查是否残留可写回的 dirty 快照。真实 agent_map 只有已完成预算（18项）及无预算来源项，未声称覆盖正在请求中的 agent_map 取消。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--usage-history", type=Path, required=True)
    parser.add_argument("--stale-worker-expectation", choices=("protected",), default="protected")
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError(options.output)
    result = verify(options)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "source_run_id", "source_node_id", "cases", "agent_map_checks", "stale_worker_cases")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
