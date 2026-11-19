"""真实历史检查点的热路径、分支、篡改与首次初始化 MySQL 回滚验收。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from core.db.database import engine, SessionLocal
from core.db.model_defs import AgentRun, AgentNodeRun, AgentRunEvent, AgentArtifactBlob
from modules.agent_platform.action_journal import AgentActionJournal, ActionStateConflict
from modules.agent_platform.lifecycle import new_claim_token, RunLeaseLost
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.retry_policy import restorable_sdk_state
from modules.agent_platform.checkpoint_storage import (
    ArtifactStore, CheckpointStorageError, canonical_bytes, content_hash, compact_checkpoint,
    hydrate_action_checkpoint,
)
from scripts.qa.verify.verify_checkpoint_consumers_real_data import fingerprints, storage_counts, _block_external_connection

sys.addaudithook(_block_external_connection)


def activate(db, run, node):
    token = new_claim_token()
    run.status, run.claim_token = "running", token
    run.lease_expires_at = datetime.utcnow() + timedelta(minutes=10)
    node.status = "running"
    db.flush()
    db.commit()
    return AgentActionJournal(AgentRunRepository(db), run_id=run.id, node_run_id=node.id, expected_claim_token=token)


def transaction_case(callback):
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as db:
                result = callback(db, connection)
                assert outer.is_active, "journal提交不得越过验证外层事务"
                return result
        finally:
            outer.rollback()


def protected_row_counts(db):
    """临时节点、日志与存储分支均必须被外层事务完整回滚。"""
    return {**storage_counts(db), **{
        model.__tablename__: db.scalar(select(func.count()).select_from(model))
        for model in (AgentRun, AgentNodeRun, AgentRunEvent)
    }}


def protected_execution_control_hashes(db):
    """执行权、时钟和节点身份同样需要回滚，报告只保存摘要。"""
    runs = db.execute(select(AgentRun.id, AgentRun.status, AgentRun.claim_token,
        AgentRun.heartbeat_at, AgentRun.lease_expires_at, AgentRun.task_id,
        AgentRun.created_at, AgentRun.updated_at, AgentRun.started_at, AgentRun.finished_at)
        .where(AgentRun.id.in_([13, 47])).order_by(AgentRun.id)).mappings()
    nodes = db.execute(select(AgentNodeRun.id, AgentNodeRun.run_id, AgentNodeRun.node_key,
        AgentNodeRun.node_type, AgentNodeRun.status, AgentNodeRun.attempt,
        AgentNodeRun.agent_definition_id, AgentNodeRun.tool_definition_id,
        AgentNodeRun.created_at, AgentNodeRun.updated_at, AgentNodeRun.started_at, AgentNodeRun.finished_at)
        .where(AgentNodeRun.id.in_([122, 123, 378])).order_by(AgentNodeRun.id)).mappings()

    def digest(rows):
        return content_hash([{key: value.isoformat() if isinstance(value, datetime) else value
                              for key, value in row.items()} for row in rows])

    return {"runs": digest(runs), "nodes": digest(nodes)}


def isolated_node_copy(db, source, *, keep_checkpoint):
    """复用真实业务输入，创建独立执行身份；绝不抹掉历史节点已有分支。"""
    attempt = db.scalar(select(func.max(AgentNodeRun.attempt)).where(
        AgentNodeRun.run_id == source.run_id,
        AgentNodeRun.node_key == source.node_key,
    )) or 0
    sdk = restorable_sdk_state(source)
    if not keep_checkpoint:
        sdk = {key: value for key, value in sdk.items()
               if key not in {"action_checkpoint", "action_usage", "action_usage_recorded"}}
    node = AgentNodeRun(
        run_id=source.run_id, node_key=source.node_key, node_type=source.node_type,
        agent_definition_id=source.agent_definition_id,
        tool_definition_id=source.tool_definition_id, status="pending", attempt=attempt + 1,
        input_payload=deepcopy(source.input_payload), output_payload={}, sdk_state=sdk,
        error_message="", started_at=None, finished_at=None,
    )
    db.add(node)
    db.flush()
    return node


def verify():
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "model_requests": 0,
              "broker_submissions": 0, "new_persisted_runs_or_nodes": 0, "persisted_changes": 0,
              "temporary_node_copies": [], "checks": {}}
    checks = report["checks"]
    with SessionLocal() as db:
        before = {node: fingerprints(db, run, node) for run, node in [(13, 122), (13, 123), (47, 378)]}
        counts_before = protected_row_counts(db)
        controls_before = protected_execution_control_hashes(db)

    def read_and_budget(db, connection):
        run, node = db.get(AgentRun, 13), db.get(AgentNodeRun, 123)
        old = hydrate_action_checkpoint(db, node)
        compact = compact_checkpoint(node)
        journal = activate(db, run, node)
        checks["metadata_has_no_domain_or_logs"] = not {"state", "receipts", "validated_agent_calls"}.intersection(journal.metadata())
        checks["state_only_hash_matches"] = content_hash(journal.load_state()) == old["state_hash"]
        headers = journal.receipts()
        checks["receipt_headers_only"] = len(headers) == len(old["receipts"]) and all("arguments" not in item for item in headers)
        for header in headers:
            full = journal.receipt_by_id(header["tool_call_id"])
            assert all(full.get(key) == value for key, value in old["receipts"][header["tool_call_id"]].items())
        checks["targeted_receipts_preserve_original_fields"] = True
        active = [item for item in old["receipts"].values() if item["status"] == "started"]
        checks["active_receipt_matches"] = (journal.active_receipt() or {}).get("tool_call_id") == (active[0]["tool_call_id"] if active else None)
        checks["receipt_and_child_counts_match"] = journal.receipt_count() == len(old["receipts"]) and journal.child_call_count() == len(old.get("validated_agent_calls", {}))
        failures = journal.failure_projection(state_hash=old["state_hash"])
        # 用当前持久回执核对可见错误，不再调用旧内嵌检查点的投影实现。
        originals = old["receipts"]
        checks["failure_projection_references_current_failed_receipts"] = all(
            originals[item["tool_call_id"]]["status"] == "failed"
            and originals[item["tool_call_id"]]["pre_state_hash"] == old["state_hash"]
            and item["error"] == originals[item["tool_call_id"]]["error"]
            for item in failures
        )
        checks["failure_projection_excludes_later_success"] = all(
            not any(success["status"] == "completed"
                    and success["tool_key"] == item["tool_key"]
                    and success["arguments_hash"] == item["arguments_hash"]
                    and success["started_sequence"] > originals[item["tool_call_id"]]["started_sequence"]
                    for success in originals.values())
            for item in failures
        )
        if old.get("validated_agent_calls"):
            original = next(iter(old["validated_agent_calls"].values()))
            loaded = journal.validated_agent_call(original["identity"])
            checks["targeted_validated_call_is_lossless"] = all(loaded.get(key) == value for key, value in original.items())
        statements = []
        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(connection, "before_cursor_execute", capture)
        try:
            journal.save_budget(deepcopy(compact["budget"]))
        finally:
            event.remove(connection, "before_cursor_execute", capture)
        checks["budget_zero_artifact_reads_or_writes"] = not any("agent_artifact_blobs" in sql.lower() for sql in statements)
        checks["budget_no_receipt_or_call_scan"] = not any(name in sql.lower() for sql in statements for name in ("agent_action_receipts", "agent_validated_calls"))
        checks["budget_no_full_run_context_select"] = not any("select agent_runs.run_context" in sql.lower() for sql in statements)
        report["budget_save_sql"] = statements
        report["old_checkpoint_bytes"] = len(canonical_bytes(old))
        report["compact_checkpoint_bytes"] = len(canonical_bytes(journal.metadata()))
        root_ref = journal.metadata()["state_ref"]
        wrong_owner = ArtifactStore(db, user_id=run.user_id + 1, project_id=run.project_id)
        try:
            wrong_owner.get(root_ref)
        except CheckpointStorageError:
            checks["cross_tenant_artifact_ref_rejected"] = True
        else:
            raise AssertionError("跨租户引用未被拒绝")
        original_hash = db.get(AgentArtifactBlob, root_ref["id"]).payload_hash
        db.execute(update(AgentArtifactBlob).where(AgentArtifactBlob.id == root_ref["id"]).values(payload_hash="0" * 64))
        db.commit()
        try:
            journal.load_state()
        except ActionStateConflict:
            checks["corrupted_artifact_rejected"] = True
        else:
            raise AssertionError("损坏正文仍可被读取")
        db.execute(update(AgentArtifactBlob).where(AgentArtifactBlob.id == root_ref["id"]).values(payload_hash=original_hash))
        db.commit()
        run = db.get(AgentRun, 13)
        run.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        try:
            journal.save_budget(compact["budget"])
        except RunLeaseLost:
            checks["expired_claim_rejected"] = True
        else:
            raise AssertionError("过期执行器仍能写检查点")
        run = db.get(AgentRun, 13)
        run.status = "cancelled"
        db.commit()
        try:
            journal.save_budget(compact["budget"])
        except RunLeaseLost:
            checks["cancelled_run_rejected"] = True
        else:
            raise AssertionError("已取消运行仍能写检查点")
    transaction_case(read_and_budget)

    def first_initialization(db, _connection):
        run, source_node = db.get(AgentRun, 47), db.get(AgentNodeRun, 378)
        old = hydrate_action_checkpoint(db, source_node)
        source_input = AgentRunRepository(db).read_node_input(source_node)
        original_header = compact_checkpoint(source_node)
        node = isolated_node_copy(db, source_node, keep_checkpoint=False)
        report["temporary_node_copies"].append({"source_node_id": source_node.id,
            "temporary_node_id": node.id, "scenario": "first_initialization", "rolled_back": True})
        journal = activate(db, run, node)
        checks["new_node_load_state_is_none"] = journal.load_state() is None
        journal.init(state=deepcopy(old["state"]), input_hash=content_hash(source_input))
        checks["first_init_state_hash_matches_real_input"] = journal.metadata()["input_hash"] == old["input_hash"] and content_hash(journal.load_state()) == old["state_hash"]
        checks["first_init_uses_independent_node_and_branch"] = (
            node.id != source_node.id
            and journal.metadata()["journal_ref"]["branch_id"] != original_header["journal_ref"]["branch_id"]
            and compact_checkpoint(source_node) == original_header
        )
    transaction_case(first_initialization)

    def fork_history(db, _connection):
        run = db.get(AgentRun, 13)
        parent, original_child_node = db.get(AgentNodeRun, 122), db.get(AgentNodeRun, 123)
        original_child = hydrate_action_checkpoint(db, original_child_node)
        original_child_header = compact_checkpoint(original_child_node)
        parent_cp = compact_checkpoint(parent)
        assert parent_cp["input_hash"] == original_child["input_hash"]
        parent_view = hydrate_action_checkpoint(db, parent)
        # 重试复制同一祖先的完整小游标，不能把子尝试后来的已记账游标拼入较早父预算。
        child = isolated_node_copy(db, parent, keep_checkpoint=True)
        report["temporary_node_copies"].append({"source_node_id": parent.id,
            "temporary_node_id": child.id, "scenario": "retry_branch", "rolled_back": True})
        journal = activate(db, run, child)
        journal.bind_execution_identity(parent_cp["execution_semantic_hash"])
        fork = journal.metadata()
        checks["retry_forks_references_without_copying_domain"] = fork["state_ref"] == parent_cp["state_ref"] and fork["input_ref"] == parent_cp["input_ref"] and fork["journal_ref"]["branch_id"] != parent_cp["journal_ref"]["branch_id"]
        candidates = [value for key, value in original_child["receipts"].items()
                      if key not in parent_view["receipts"] and value["pre_state_hash"] == parent_cp["state_hash"]]
        if not candidates:
            raise AssertionError("真实跨尝试记录没有与父状态匹配的新增行动，不能编造替代行动")
        action = min(candidates, key=lambda value: value["started_sequence"])
        journal.begin(tool_call_id=action["tool_call_id"], tool_key=action["tool_key"], args=action["arguments"], prehash=parent_cp["state_hash"])
        checks["child_receipt_appends_independently"] = journal.receipt_count() == len(parent_view["receipts"]) + 1
        restored_parent = hydrate_action_checkpoint(db, parent)
        checks["parent_cursor_keeps_historical_view_fixed"] = restored_parent == parent_view
        checks["original_child_checkpoint_never_reassigned"] = compact_checkpoint(original_child_node) == original_child_header
        checks["fork_state_hash_unchanged"] = content_hash(journal.load_state()) == parent_cp["state_hash"]
    transaction_case(fork_history)

    snapshot = Path(__file__).resolve().parents[4] / "output/qa/2026-11-11-crm-run46-source-snapshot.json"
    source = json.loads(snapshot.read_text(encoding="utf-8"))
    original = next(node["sdk_state"]["action_checkpoint"]["state"] for node in source["nodes"] if node["id"] == 370)
    def local_change(db, _connection):
        scope = db.get(AgentRun, 47)
        store = ArtifactStore(db, user_id=scope.user_id, project_id=scope.project_id)
        first = store.put(original)
        base_bytes, base_rows = store.new_bytes, store.new_rows
        changed = deepcopy(original)
        version_field = next((key for key in ("layout_version", "generation_version", "candidate_version")
                              if type(changed.get(key)) is int), None)
        if version_field is None:
            raise AssertionError("真实状态缺少可用于局部存储验证的版本字段")
        changed[version_field] += 1
        second = store.put(changed)
        delta_bytes, delta_rows = store.new_bytes - base_bytes, store.new_rows - base_rows
        checks["local_change_roundtrips_original_and_changed_versions"] = store.get(first) == original and store.get(second) == changed
        checks["local_change_does_not_rewrite_domain"] = delta_bytes < len(canonical_bytes(original)) // 10
        report["run46_local_state_change"] = {"original_logical_bytes": len(canonical_bytes(original)), "initial_unique_stored_bytes": base_bytes, "initial_blocks": base_rows,
                                             "added_bytes": delta_bytes, "added_blocks": delta_rows,
                                             "changed_field": version_field, "scope": "仅回滚存储验证，不变更线上业务状态"}
        collections = []
        def find_collections(value, path):
            if isinstance(value, list):
                if len(value) > 32 and all(isinstance(item, dict) and isinstance(item.get("assertion"), str) for item in value):
                    collections.append((path, value))
                for index, item in enumerate(value):
                    find_collections(item, [*path, index])
            elif isinstance(value, dict):
                for key, item in value.items():
                    find_collections(item, [*path, key])
        find_collections(original, [])
        path, facts = max(collections, key=lambda item: len(canonical_bytes(item[1])))
        other = next(item for item in facts[1:] if item["assertion"] != facts[0]["assertion"])
        fact_change = deepcopy(original)
        target = fact_change
        for key in path:
            target = target[key]
        # 仅在存储反事实副本中搬用另一条已存在的真实文本，不生成或采用新的业务结论。
        target[0]["assertion"] = other["assertion"]
        previous_bytes, previous_rows = store.new_bytes, store.new_rows
        fact_ref = store.put(fact_change)
        fact_delta_bytes, fact_delta_rows = store.new_bytes - previous_bytes, store.new_rows - previous_rows
        checks["fact_collection_single_entry_roundtrips"] = store.get(fact_ref) == fact_change and store.get(first) == original
        checks["fact_collection_change_does_not_rewrite_whole_collection"] = fact_delta_bytes < len(canonical_bytes(facts)) // 10
        report["run46_fact_collection_change"] = {"path": path, "facts": len(facts), "collection_logical_bytes": len(canonical_bytes(facts)),
                                                  "added_bytes": fact_delta_bytes, "added_blocks": fact_delta_rows,
                                                  "mutation": "仅将一条assertion换成另一条已有真实assertion，未执行业务或采用该结论"}
    transaction_case(local_change)
    with SessionLocal() as db:
        after = {node: fingerprints(db, run, node) for run, node in [(13, 122), (13, 123), (47, 378)]}
        checks["original_run_and_node_fingerprints_unchanged"] = before == after
        counts_after = protected_row_counts(db)
        controls_after = protected_execution_control_hashes(db)
        checks["all_new_nodes_events_and_storage_rows_rolled_back"] = counts_before == counts_after
        checks["execution_claim_clock_and_node_identity_unchanged"] = controls_before == controls_after
    report["protected_row_counts_before"] = counts_before
    report["protected_row_counts_after"] = counts_after
    report["protected_fingerprints_before"] = before
    report["protected_fingerprints_after"] = after
    report["protected_execution_controls_before"] = controls_before
    report["protected_execution_controls_after"] = controls_after
    if not all(checks.values()):
        raise AssertionError({key: value for key, value in checks.items() if not value})
    report["passed"] = len(checks)
    report["scope_note"] = ("真实 v3 检查点与原回执的存储验收；两个新节点仅为回滚事务内的执行身份副本，"
                            "不提交新 Run/Node，不生成模型候选，不改历史检查点、来源或真实消耗。")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("历史证据报告已存在，请指定新的输出路径")
    result = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "compact_checkpoint_bytes": result["compact_checkpoint_bytes"],
                      "run46_local_state_change": result["run46_local_state_change"]}, ensure_ascii=False))
