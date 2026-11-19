"""从真实Run59终态提取当前读取链路回归所需数据，不存认证或租约。"""

import gzip
import json
from pathlib import Path

from prepare_read_path_draft import ROOT
from modules.agent_platform.action_journal import action_state_hash


original = ROOT / "output/qa/2026-11-12-single-agent-live/round-11"
snapshot = json.loads((original / "final-snapshot.json").read_text(encoding="utf-8"))
created = json.loads((original / "created-run.json").read_text(encoding="utf-8"))["run"]
run = snapshot["run"]
checkpoint = snapshot["expanded_checkpoints"]["404"]
value = {"provenance": {"run_id": 59, "node_id": 404, "status": run["status"],
    "source_state_sha256": checkpoint["state_hash"], "source_document_id": 287,
    "evidence_file": "output/qa/2026-11-12-single-agent-live/round-11/final-snapshot.json",
    "model_requests_sent_by_extraction": 0, "database_business_writes": 0},
    "run_metadata": {key: run[key] for key in ("id", "user_id", "project_id", "workflow_definition_id", "status")},
    "input_payload": run["input_payload"],
    "initial_run_context": {"artifacts": created["run_context"]["artifacts"]},
    "state": checkpoint["state"]}
assert action_state_hash(value["state"]) == value["provenance"]["source_state_sha256"]
value["data_sha256"] = action_state_hash(value)
output = ROOT / "backend/tests/agent_platform/fixtures/run59_artifact_read_cost_real.json.gz"
output.write_bytes(gzip.compress(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
print(json.dumps({"path": str(output), "bytes": output.stat().st_size, "data_sha256": value["data_sha256"]}, ensure_ascii=False))
