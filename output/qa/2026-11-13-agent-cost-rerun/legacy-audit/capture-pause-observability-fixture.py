"""只读真实 SDK 观测与审批题目，明确组合来源，不重建缺失请求。"""

import gzip
import hashlib
import json
from pathlib import Path
import sys


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


with SessionLocal() as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    row = db.execute(text("""SELECT id,run_id,node_key,node_type,status,attempt,input_payload,
        JSON_EXTRACT(sdk_state,'$.action_checkpoint') AS action_checkpoint,
        JSON_EXTRACT(sdk_state,'$.action_usage') AS action_usage,
        JSON_EXTRACT(sdk_state,'$.usage') AS sdk_usage,
        JSON_EXTRACT(sdk_state,'$.model_requests') AS model_requests,
        JSON_EXTRACT(sdk_state,'$.tool_calls') AS tool_calls,
        JSON_EXTRACT(sdk_state,'$.tool_observations') AS tool_observations
        FROM agent_node_runs WHERE id=404""")).mappings().one()
    node = {key: row[key] for key in ("id", "run_id", "node_key", "node_type", "status", "attempt")}
    node["input_payload"] = decoded(row["input_payload"])
    node["sdk_state"] = {key: decoded(row[key]) for key in ("action_checkpoint", "action_usage")}
    node["sdk_state"]["usage"] = decoded(row["sdk_usage"])
    metadata = {key: decoded(row[key]) for key in ("model_requests", "tool_calls", "tool_observations")}
    approval = dict(db.execute(text("SELECT id,run_id,node_run_id,status,request_payload FROM agent_approvals WHERE id=15")).mappings().one())
    approval["request_payload"] = decoded(approval["request_payload"])
    lost = dict(db.execute(text("""SELECT id,run_id,status,
        JSON_CONTAINS_PATH(sdk_state,'one','$.model_requests') AS has_model_requests,
        JSON_CONTAINS_PATH(sdk_state,'one','$.tool_calls') AS has_tool_calls,
        JSON_CONTAINS_PATH(sdk_state,'one','$.tool_observations') AS has_tool_observations,
        JSON_EXTRACT(sdk_state,'$.action_checkpoint.budget.reported_usage.requests') AS reported_requests
        FROM agent_node_runs WHERE id=:node_id"""), {"node_id": approval["node_run_id"]}).mappings().one())
    db.rollback()

pause_snapshot_path = ROOT / "output/qa/2026-11-13-agent-cost-rerun/round-02/final-snapshot.json"
pause_snapshot_bytes = pause_snapshot_path.read_bytes()
pause_snapshot = json.loads(pause_snapshot_bytes)
paused_node = next(node for node in pause_snapshot["nodes"] if node["id"] == approval["node_run_id"])
assert pause_snapshot["run"]["id"] == 61 and pause_snapshot["run"]["status"] == "waiting_approval"
paused_evidence = {"id": paused_node["id"], "run_id": paused_node["run_id"], "status": paused_node["status"],
    **{f"has_{key}": key in paused_node["sdk_state"] for key in ("model_requests", "tool_calls", "tool_observations")},
    "reported_requests": paused_node["sdk_state"]["action_checkpoint"]["budget"]["reported_usage"]["requests"],
    "snapshot_file_sha256": hashlib.sha256(pause_snapshot_bytes).hexdigest(),
    "snapshot_observed_at": pause_snapshot["observed_at"]}

value = {"provenance": {
    "composition": "异源真实metadata与真实审批items的纯控制测试；不是Run61首95事件重建",
    "sdk_metadata_source": {"run_id": 59, "node_id": 404,
        "retained_model_request_count": len(metadata["model_requests"]),
        "reported_run59_requests": node["sdk_state"]["usage"]["requests"],
        "retention_note": "SDK只保留真实末200条；本fixture不补造其余记录"},
    "pause_items_source": {"run_id": approval["run_id"], "node_id": approval["node_run_id"],
        "approval_id": approval["id"], "status": approval["status"]},
    "actual_run61_paused_node_observation": paused_evidence,
    "later_live_node_read_after_approval_resume": lost,
    "first_failure_note": "首次fixture把复用Node413恢复后的150请求误认作暂停95；已保留失败，改读不可变首次暂停快照",
    "current_phase_test_slices_are_control_inputs_not_real_task_stages": True,
    "does_not_reconstruct_run61_missing_95_requests": True,
    "business_database_writes": 0, "model_requests_sent": 0,
}, "source_node": node, "metadata": metadata, "approval": approval}
value["data_sha256"] = action_state_hash(value)
target = BASE / "pause-observability-draft-tree/backend/tests/agent_platform/fixtures/pause_observability_cross_source_real.json.gz"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(gzip.compress(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
print(json.dumps({"fixture_bytes": target.stat().st_size, "metadata_counts": {key: len(items) for key, items in metadata.items()},
                  "data_sha256": value["data_sha256"], "composition": value["provenance"]["composition"]}, ensure_ascii=False))
