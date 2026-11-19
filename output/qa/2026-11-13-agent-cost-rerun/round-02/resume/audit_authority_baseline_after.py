"""只读重验真实61初始治理，区分原始回执与当前规划视图重编译。"""

import json
from pathlib import Path
import runpy

BASE = Path(__file__).resolve().parent
h = runpy.run_path(str(BASE.parent / "capture_cost_progress.py"))
from core.db.model_defs import AgentRun, AgentNodeRun
from modules.agent_platform import checkpoint_storage as storage
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_source_context import _authority_baseline
from modules.agent_platform.test_generation_semantics import _governed_source_semantics

with h["SessionLocal"](autoflush=False) as db:
    db.execute(h["text"]("SET TRANSACTION READ ONLY"))
    run, node = db.get(AgentRun, 61), db.get(AgentNodeRun, 413)
    checkpoint = storage.compact_checkpoint(node)
    reader = h["TargetedReader"](db)
    source = reader.subtree(checkpoint["state_hash"], ["source"])
    confirmed = reader.subtree(checkpoint["state_hash"], ["conflict_confirmations"])
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
        run_id=run.id, node_key=node.node_key, run_input=run.input_payload, artifacts=source["artifacts"])
    baseline, proof = _authority_baseline(context, source, confirmed,
        storage.call_records(db, node, checkpoint), storage.receipt_records(db, node, checkpoint),
        storage.store_for_node(db, node), checkpoint)
    current = _governed_source_semantics(source["source_semantics"], baseline["result"])
    old = source["source_authority"]["source_semantics"]
    old_ids = {fact["fact_id"] for scope in old["planning_scopes"] for fact in scope["facts"]}
    new_ids = {fact["fact_id"] for scope in current["planning_scopes"] for fact in scope["facts"]}
    report = {"status": "accepted", "business_database_writes": 0, "model_requests_sent": 0,
        "proof": proof, "old_scope_fact_count": len(old_ids), "current_scope_fact_count": len(new_ids),
        "added_scope_fact_ids": sorted(new_ids - old_ids), "removed_scope_fact_ids": sorted(old_ids - new_ids),
        "baseline_result_matches_old": baseline["result"] == source["source_authority"]["result"]}
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()
(BASE / "authority-baseline-after-lineage-fix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
