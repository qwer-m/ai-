"""预览/显式迁移 v2 内嵌检查点与节点输入；不投递或执行任何历史任务。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import inspect, select

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentRun, AgentNodeRun, AgentArtifactBlob, AgentJournalBranch,
    AgentActionReceiptRecord, AgentValidatedCallRecord,
)
from modules.agent_platform.checkpoint_storage import (
    INPUT_MARKER, CHECKPOINT_VERSION, canonical_bytes, migrate_inline_checkpoint, store_node_input,
)
from modules.agent_platform.run_repository import AgentRunRepository


TABLES = (AgentArtifactBlob, AgentJournalBranch, AgentActionReceiptRecord, AgentValidatedCallRecord)


def create_tables() -> list[str]:
    created = []
    with SessionLocal() as db:
        connection = db.connection()
        if connection.dialect.name == "mysql":
            connection.exec_driver_sql("SET SESSION lock_wait_timeout=10")
        for model in TABLES:
            if not inspect(connection).has_table(model.__tablename__):
                model.__table__.create(bind=connection, checkfirst=False)
                created.append(model.__tablename__)
        db.commit()
    return created


def migrate(*, apply=False, node_ids=None):
    rows = []
    with SessionLocal(autoflush=False) as db:
        repo = AgentRunRepository(db)
        identities = db.query(AgentNodeRun.id, AgentNodeRun.run_id).all()
        for node_id, run_id in sorted(identities):
            if node_ids and node_id not in node_ids:
                continue
            run = repo.get_run_for_update(run_id=run_id) if apply else db.get(AgentRun, run_id)
            node = db.get(AgentNodeRun, node_id)
            checkpoint = (node.sdk_state or {}).get("action_checkpoint")
            entry = {"node_id": node.id, "run_id": run.id, "status": run.status,
                     "checkpoint_version": checkpoint.get("version") if checkpoint else None,
                     "inline_checkpoint_bytes": len(canonical_bytes(checkpoint)) if checkpoint else 0,
                     "input_is_artifact": node.input_payload.get(INPUT_MARKER) == 1}
            if checkpoint and checkpoint.get("version") != CHECKPOINT_VERSION:
                entry["state_hash"] = checkpoint.get("state_hash")
                entry["missing_start_order"] = sum(type(value.get("started_sequence")) is not int
                                                   for value in checkpoint.get("receipts", {}).values())
                if apply:
                    before = deepcopy(checkpoint)
                    compact = migrate_inline_checkpoint(db, run, node)
                    entry["compact_checkpoint_bytes"] = len(canonical_bytes(compact))
                    assert compact["state_hash"] == before["state_hash"]
                    assert compact["input_hash"] == before["input_hash"]
                    assert compact["budget"] == before["budget"]
            elif node.input_payload.get(INPUT_MARKER) != 1:
                if apply:
                    if run.status == "running":
                        raise ValueError("运行仍由执行器持有，禁止迁移输入")
                    original = repo.read_node_input(node)
                    store_node_input(db, node, original)
                    db.add(node)
                    db.flush()
                    assert repo.read_node_input(node) == original
            rows.append(entry)
        if apply:
            db.commit()
        else:
            db.rollback()
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-tables", action="store_true", help="只创建4张空表，不迁移记录或启动任务")
    parser.add_argument("--apply", action="store_true", help="显式迁移已停止的真实记录，默认只读预览")
    parser.add_argument("--node-id", type=int, action="append", help="限定真实节点；省略时处理全部历史节点")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.create_tables and args.apply:
        parser.error("建表和迁移必须分开执行以保留可审查边界")
    report = {"model_requests": 0, "broker_submissions": 0, "business_status_changed": False}
    if args.create_tables:
        report["created_tables"] = create_tables()
    else:
        report["apply"] = args.apply
        report["nodes"] = migrate(apply=args.apply, node_ids=args.node_id)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
