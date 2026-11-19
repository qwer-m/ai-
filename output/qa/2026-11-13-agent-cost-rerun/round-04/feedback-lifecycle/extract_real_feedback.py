"""只读提取Run63真实反馈生命周期；只展开相关叶子，不读取整个根状态。"""

import gzip
import hashlib
import json
import runpy
from pathlib import Path

BASE = Path(__file__).resolve().parent
ns = runpy.run_path(str(BASE.parent / "capture_cost_progress.py"))
SessionLocal, text = ns["SessionLocal"], ns["text"]
Reader, decoded, receipt = ns["TargetedReader"], ns["decoded"], ns["receipt_record"]
from modules.agent_platform.action_journal import action_state_hash


with SessionLocal(autoflush=False) as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    rd = Reader(db)
    events = [decoded(value) for value in db.execute(text(
        "SELECT payload FROM agent_run_events WHERE run_id=63 AND event_type='agent_action_started'"
    )).scalars()]
    selected = {seq: receipt(db, next(event["tool_call_id"] for event in events
                                    if event.get("started_sequence") == seq)) for seq in (18, 21, 22, 25)}
    initial = selected[18]["pre_state_hash"]
    problems = rd.subtree(initial, ["problems"])
    report = next(value for value in problems.values()
                  if value["payload"].get("result_type") == "planning_feedback")
    original_index = report["generation_indexes"][0]
    generation_input = rd.subtree(initial, ["generation_inputs", original_index])
    assert action_state_hash(generation_input) == report["input_hash"]
    shared = {key: rd.subtree(initial, ["planning", key]) for key in (
        "planning_inputs", "source_semantics", "source_authority", "goal",
    )}
    source_bindings = {}
    snapshots = {}
    for name, sha in (
        ("before18", initial),
        ("after18", selected[18]["post_state_hash"]),
        ("after21", selected[21]["post_state_hash"]),
        ("after22", selected[22]["post_state_hash"]),
        ("after25", selected[25]["post_state_hash"]),
    ):
        source_bindings[name] = {
            "source_ref": rd.subtree(sha, ["planning", "goal", "source_ref"]),
            "source_semantics_hash": rd.value_hash(sha, ["planning", "source_semantics"]),
            "source_authority_hash": rd.value_hash(sha, ["planning", "source_authority"]),
            "effective_facts_hash": rd.value_hash(sha, ["effective_facts"]),
            "confirmation_hash": rd.value_hash(sha, ["conflict_confirmations"]),
        }
        snapshots[name] = {
            "state_hash": sha,
            "planning_records": rd.subtree(sha, ["planning", "planning_records"]),
            "plan": rd.subtree(sha, ["planning", "plan"]),
            "plan_input_hash": rd.subtree(sha, ["planning", "plan_input_hash"]),
            "generation_inputs": rd.subtree(sha, ["generation_inputs"]),
            "generation_records": rd.subtree(sha, ["generation_records"]),
            "accepted_plan": rd.subtree(sha, ["accepted_plan"]),
        }
        for key in ("gap_inputs", "gap_records", "route_inputs", "route_records",
                    "route_review_revisions", "last_route_feedback", "dependency_records"):
            snapshots[name][key] = rd.subtree(sha, ["planning", key])
    artifact_keys = ("prepared_input", "accepted_plan", "effective_facts", "conflict_confirmations")
    original = {key: rd.subtree(initial, [key]) for key in artifact_keys}
    data = {
        "provenance": {"run_id": 63, "source": "真实MySQL不可变行动状态叶子", "model_requests_sent": 0,
                       "business_database_writes": 0,
                       "historical_report_interpretation": "真实模型的未评估范围问题报告，不是独立确认的业务矛盾。"},
        "receipts": selected, "original_problem": report, "original_generation_index": original_index,
        "original_generation_input": generation_input,
        "shared_planning": shared, "original": original,
        "source_bindings": source_bindings, "snapshots": snapshots,
    }
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()

raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
target = BASE / "run63_generation_feedback_closed_real_v2.json.gz"
target.write_bytes(gzip.compress(raw, mtime=0))
print(json.dumps({"fixture": str(target), "bytes": target.stat().st_size,
                  "data_sha256": hashlib.sha256(raw).hexdigest(),
                  "issues": len(report["payload"]["planning_feedback"]["issues"]),
                  "snapshot_input_counts": {key: len(value["generation_inputs"]) for key, value in snapshots.items()}},
                 ensure_ascii=False))
