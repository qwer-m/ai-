"""真实历史节点显式迁移及预算增量的 MySQL 回滚验证，不发送模型请求。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import Text, cast, event, func, inspect, literal, select, update
from sqlalchemy.orm import Session, load_only

from core.db.database import engine, SessionLocal
from core.db.model_defs import (AgentActionReceiptRecord, AgentArtifactBlob, AgentJournalBranch,
                                AgentNodeRun, AgentRun, AgentValidatedCallRecord)
from modules.agent_platform.serialization import serialize_node_run
from modules.agent_platform.service import _restored_checkpoint_sdk_state
from modules.agent_platform.usage import action_budget_usage, sync_action_usage_projection
from scripts.qa.verify.audit_generation_timing_real_data import action_summary


def _block_external_connection(name, arguments):
    if name == "socket.connect":
        address = arguments[1]
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("本验证禁止外部网络连接")


sys.addaudithook(_block_external_connection)


def fingerprints(db, run_id, node_id):
    run = db.execute(select(
        AgentRun.status,
        func.sha2(cast(AgentRun.run_context, Text), 256),
        func.sha2(cast(AgentRun.run_context["artifacts"], Text), 256),
        func.sha2(cast(AgentRun.run_context["execution_contract"], Text), 256),
        AgentRun.run_context["usage"],
    ).where(AgentRun.id == run_id)).one()
    node = db.execute(select(
        AgentNodeRun.status, func.sha2(cast(AgentNodeRun.sdk_state, Text), 256),
        func.sha2(cast(AgentNodeRun.input_payload, Text), 256),
        func.sha2(cast(AgentNodeRun.output_payload, Text), 256),
    ).where(AgentNodeRun.id == node_id, AgentNodeRun.run_id == run_id)).one()
    return {"run_status": run[0], "run_context_sha256": run[1], "artifacts_sha256": run[2],
            "execution_contract_sha256": run[3], "usage": run[4], "node_status": node[0],
            "sdk_state_sha256": node[1], "input_sha256": node[2], "output_sha256": node[3]}


def _set_usage(db, run_id, usage):
    encoded = json.dumps(usage, ensure_ascii=False, separators=(",", ":"))
    db.execute(update(AgentRun).where(AgentRun.id == run_id).values(
        run_context=func.json_set(AgentRun.run_context, "$.usage", func.json_extract(literal(encoded), "$")),
    ).execution_options(synchronize_session=False))


def storage_counts(db):
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (AgentArtifactBlob, AgentJournalBranch, AgentActionReceiptRecord, AgentValidatedCallRecord)}


def verify(run_id, node_id):
    # 迁移入口是人工显式调用的离线边界，不能在生产恢复路径里自动升级旧状态。
    from modules.agent_platform.checkpoint_storage import migrate_inline_checkpoint

    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
              "node_id": node_id, "model_requests": 0, "persisted_database_changes": 0,
              "transaction": "real_mysql_rollback", "checks": {}}
    checks = report["checks"]
    with SessionLocal() as db:
        db.execute(select(AgentRun.id).where(AgentRun.id == run_id))
        before = fingerprints(db, run_id, node_id)
        counts_before = storage_counts(db)
        if before["run_status"] not in {"failed", "cancelled"}:
            raise ValueError("只能重放真实失败或取消节点，不能触碰活动运行")
    report["before"] = before
    report["storage_counts_before"] = counts_before
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection, expire_on_commit=False) as db:
                run = db.scalar(select(AgentRun).options(load_only(AgentRun.id, AgentRun.status))
                                .where(AgentRun.id == run_id).with_for_update())
                node = db.get(AgentNodeRun, node_id)
                if node is None or node.run_id != run_id:
                    raise LookupError("真实节点与运行不匹配")
                old_sdk = deepcopy(node.sdk_state)
                old_cp = old_sdk["action_checkpoint"]
                old_summary = action_summary(db, node)
                requests = (old_sdk.get("error_diagnostic") or {}).get("model_requests") or old_sdk.get("model_requests") or []
                actual = next(item for item in requests if item.get("status") == "response_received"
                              and (item.get("usage") or {}).get("requests") == 1)
                usage = actual["usage"]
                delta = {"attempted_requests": 1, **{key: usage[key] for key in
                         ("requests", "input_tokens", "output_tokens", "total_tokens")}}
                if any(type(value) is not int or value < 0 for value in delta.values()):
                    raise ValueError("真实请求缺少完整非负用量，不允许补造报告值")
                report["source_request_sequence"] = actual["sequence"]
                report["replayed_actual_delta"] = delta
                compact = migrate_inline_checkpoint(db, run, node)
                db.flush()
                checks["explicit_v3_migration"] = compact["version"] == 3 and compact["storage"] == "artifact-journal-v1"
                checks["no_embedded_domain_or_journal"] = not {"state", "receipts", "validated_agent_calls"}.intersection(compact)
                checks["migration_preserves_usage_and_input_hash"] = (compact["budget"] == old_cp["budget"]
                                                                        and compact["input_hash"] == old_cp["input_hash"])
                report["old_checkpoint_bytes"] = len(json.dumps(old_cp, ensure_ascii=False).encode())
                report["compact_checkpoint_bytes"] = len(json.dumps(compact, ensure_ascii=False).encode())
                report["node_sdk_state_bytes_after_migration"] = len(json.dumps(node.sdk_state, ensure_ascii=False).encode())
                # 从这条实际成功回执之前的累计计数重放，不构造新的模型输出或使用量。
                current = action_budget_usage(compact["budget"])
                prior_cursor = {key: current[key] - delta[key] for key in current}
                prior_total = {key: before["usage"][key] - delta[key] for key in current}
                if any(value < 0 for value in [*prior_cursor.values(), *prior_total.values()]):
                    raise ValueError("真实回执不属于当前累计用量")
                sdk = dict(node.sdk_state)
                sdk["action_usage"] = {**sdk["action_usage"], "recorded": prior_cursor}
                node.sdk_state = sdk
                _set_usage(db, run.id, prior_total)
                db.flush()
                db.expire(run, ["run_context"])
                statements = []

                def capture(_connection, _cursor, statement, _parameters, _context, _many):
                    statements.append(statement)

                event.listen(connection, "before_cursor_execute", capture)
                try:
                    sync_action_usage_projection(db, run, node)
                    checks["orm_run_context_expired"] = "run_context" in inspect(run).expired_attributes
                    db.flush()
                    first_statements = list(statements)
                    statements.clear()
                    sync_action_usage_projection(db, run, node)
                    db.flush()
                    checks["second_sync_is_no_sql_no_double_charge"] = statements == []
                    statements.clear()
                    serialized = serialize_node_run(node)
                    restored = _restored_checkpoint_sdk_state(node)
                    checks["serializer_and_restore_do_not_read_artifacts"] = not statements
                    timing_summary = action_summary(db, node)
                    checks["timing_audit_reads_no_artifact_bodies"] = not any("agent_artifact_blobs" in sql.lower()
                                                                                 for sql in statements)
                    checks["timing_audit_keeps_real_progress_and_counts"] = all(
                        old_summary[key] == timing_summary[key]
                        for key in ("validated_specialist_counts", "planning_progress"))
                    checks["timing_audit_keeps_all_action_statuses"] = (
                        sorted((row["tool_key"], row["status"]) for row in old_summary["actions"])
                        == sorted((row["tool_key"], row["status"]) for row in timing_summary["actions"]))
                finally:
                    event.remove(connection, "before_cursor_execute", capture)
                sql = "\n".join(first_statements).lower()
                checks["hot_usage_selects_json_extract"] = "json_extract(agent_runs.run_context" in sql
                checks["hot_usage_updates_json_path"] = "json_set(agent_runs.run_context" in sql
                checks["hot_usage_no_artifact_or_journal_select"] = all(
                    name not in sql for name in ("agent_artifact_blobs", "agent_action_receipts", "agent_validated_calls"))
                checks["hot_usage_no_full_context_select"] = all(
                    "select agent_runs.run_context" not in statement.lower() for statement in first_statements)
                report["usage_sql_without_parameters"] = first_statements
                checks["frontend_returns_input_ref"] = (serialized["input_payload_is_reference"] is True
                                                          and serialized["input_payload"] == node.input_payload)
                checks["retry_keeps_same_refs_and_cursor"] = all(
                    restored["action_checkpoint"][key] == compact[key]
                    for key in ("input_ref", "state_ref", "journal_ref"))
                checks["retry_omits_historical_large_diagnostics"] = all(key not in restored for key in
                    ("model_requests", "tool_calls", "tool_observations", "error_diagnostic"))
                during = fingerprints(db, run_id, node_id)
                checks["actual_delta_restores_original_total_once"] = during["usage"] == before["usage"]
                checks["unrelated_run_json_and_status_unchanged"] = all(during[key] == before[key] for key in
                    ("artifacts_sha256", "execution_contract_sha256", "run_status", "node_status", "output_sha256"))
                checks["run_context_byte_hash_restored"] = during["run_context_sha256"] == before["run_context_sha256"]
                # 生命周期后续若确需完整上下文，必须重新取到已入账值，不能沿用旧 ORM 副本。
                checks["later_lifecycle_reads_fresh_total"] = (run.run_context or {}).get("usage") == before["usage"]
                assert all(checks.values()), checks
        finally:
            if transaction.is_active:
                transaction.rollback()
    with SessionLocal() as db:
        after = fingerprints(db, run_id, node_id)
        counts_after = storage_counts(db)
    checks["rollback_restores_original_rows"] = before == after
    checks["rollback_removes_all_migration_artifacts_and_logs"] = counts_before == counts_after
    assert all(checks.values()), checks
    report["after"] = after
    report["storage_counts_after"] = counts_after
    report["scope_note"] = ("仅证明真实历史状态的显式存储迁移和消费者协议；不执行当前业务模型，"
                            "不创建 Run/Node，不宣称旧状态满足新业务 schema。生命周期最终 artifacts/output 仍沿既有边界。")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError(options.output)
    evidence = verify(options.run_id, options.node_id)
    options.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"report": str(options.output.resolve()), "checks": evidence["checks"]}, ensure_ascii=False))
