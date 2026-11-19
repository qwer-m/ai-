"""只读比较真实历史备份和已落库 v3 检查点，验证原始证据保真。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select, text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent
from modules.agent_platform.checkpoint_storage import (
    canonical_bytes, content_hash, hydrate_action_checkpoint, hydrate_node_input, compact_checkpoint,
)
from modules.agent_platform.run_repository import AgentRunRepository


def assert_original_fields(original, restored):
    """新回执允许附加可验证索引，原字段必须逐项原样还原。"""
    assert all(restored.get(key) == value for key, value in original.items())
    added = set(restored) - set(original)
    assert added <= {"arguments_hash", "result_hash", "error_hash", "raw_output_hash", "output_hash", "error_type"}
    for key in added:
        if key == "error_type":
            assert restored[key] == original["error"]["error_type"]
        else:
            field = key.removesuffix("_hash")
            assert restored[key] == content_hash(original[field])


def current_statuses(db, original_nodes):
    """当前生命周期与历史备份分开比较，读取前后仍必须原样保持。"""
    node_ids = [item["id"] for item in original_nodes]
    run_ids = {item["run_id"] for item in original_nodes}
    return {
        "nodes": dict(db.execute(select(AgentNodeRun.id, AgentNodeRun.status).where(AgentNodeRun.id.in_(node_ids))).all()),
        "runs": dict(db.execute(select(AgentRun.id, AgentRun.status).where(AgentRun.id.in_(run_ids))).all()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    project_root = Path(__file__).resolve().parents[4]
    parser.add_argument("--backup", type=Path, default=project_root / "output/qa/2026-11-11-checkpoint-before-storage-migration.json")
    parser.add_argument("--output", type=Path, default=project_root / "output/qa/2026-11-11-historical-checkpoint-roundtrip.json")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("历史证据报告已存在，请指定新的输出路径")
    original_nodes = json.loads(args.backup.read_text(encoding="utf-8"))
    report = {"persisted_readback": True, "model_requests": 0, "broker_submissions": 0,
              "database_commit": False, "nodes": [], "lifecycle_changes_since_backup": []}
    tables = ("agent_artifact_blobs", "agent_journal_branches", "agent_action_receipts", "agent_validated_calls")
    with SessionLocal() as db:
        before = {name: db.execute(text("SELECT COUNT(*) FROM " + name)).scalar_one() for name in tables}
        statuses_before = current_statuses(db, original_nodes)
    for original_node in original_nodes:
        with SessionLocal(autoflush=False) as db:
            repo = AgentRunRepository(db)
            db.execute(text("SET TRANSACTION READ ONLY"))
            run = repo.get_run(run_id=original_node["run_id"])
            node = db.get(AgentNodeRun, original_node["id"])
            original = original_node["sdk_state"]["action_checkpoint"]
            if node.status != original_node["status"]:
                lifecycle = db.execute(select(AgentRunEvent.sequence, AgentRunEvent.event_type, AgentRunEvent.created_at)
                    .where(AgentRunEvent.run_id == run.id, AgentRunEvent.event_type.like("run_%"))
                    .order_by(AgentRunEvent.sequence.desc()).limit(10)).mappings().all()
                report["lifecycle_changes_since_backup"].append({
                    "node_id": node.id, "run_id": run.id, "backup_status": original_node["status"],
                    "current_status": node.status,
                    "recent_run_events": [{**dict(item), "created_at": str(item["created_at"])} for item in lifecycle],
                })
            original_status = run.status
            compact = compact_checkpoint(node)
            restored = hydrate_action_checkpoint(db, node)
            assert run.status == original_status
            assert all(restored.get(key) == value for key, value in original.items()
                       if key not in {"version", "receipts", "validated_agent_calls"})
            assert content_hash(hydrate_node_input(db, node)) == original["input_hash"]
            counts = {}
            for collection in ("receipts", "validated_agent_calls"):
                assert set(restored[collection]) == set(original.get(collection, {}))
                for key, value in original.get(collection, {}).items():
                    assert_original_fields(value, restored[collection][key])
                counts[collection] = len(restored[collection])
            report["nodes"].append({"node_id": node.id, "run_id": run.id, "run_status": run.status,
                "passed": True, "before_bytes": len(canonical_bytes(original)),
                "after_bytes": len(canonical_bytes(compact)), "state_hash": compact["state_hash"], **counts})
            print(json.dumps(report["nodes"][-1], ensure_ascii=False), flush=True)
            db.rollback()
    with SessionLocal() as db:
        after = {name: db.execute(text("SELECT COUNT(*) FROM " + name)).scalar_one() for name in tables}
        statuses_after = current_statuses(db, original_nodes)
    assert before == after
    assert statuses_before == statuses_after
    report.update(table_counts_before=before, table_counts_after=after,
                  current_statuses_before=statuses_before, current_statuses_after=statuses_after, passed=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
