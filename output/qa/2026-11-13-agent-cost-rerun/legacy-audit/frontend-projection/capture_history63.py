"""从真实63不可变终态快照生成轻量展示fixture，不迁移state或产生模型结果。"""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.serialization import serialize_event, serialize_run

source = ROOT / "output/qa/2026-11-13-agent-cost-rerun/round-04/final-snapshot.json"
raw = source.read_bytes()
saved = json.loads(raw.decode("utf-8-sig"))
assert saved["run"]["id"] == 63 and saved["run"]["status"] == "failed"


def actual_row(value):
    data = dict(value)
    for key in ("started_at", "finished_at", "created_at", "updated_at"):
        if isinstance(data.get(key), str):
            data[key] = datetime.fromisoformat(data[key])
    return SimpleNamespace(**data)


nodes = []
for row in saved["nodes"]:
    value = dict(row)
    value["sdk_state"] = {key: item for key, item in row["sdk_state"].items() if key in {
        "action_checkpoint", "model", "checkpoint_restore", "result_cache", "confirmation_wait_seconds"}}
    nodes.append(actual_row(value))
run = serialize_run(actual_row(saved["run"]), node_runs=nodes, approvals=[])
result = {"provenance": {"kind": "actual_historical_run63_presentation_subset", "run_id": 63,
    "source_file": str(source.relative_to(ROOT)), "source_file_sha256": hashlib.sha256(raw).hexdigest(),
    "source_observed_at": saved["observed_at"], "source_status": saved["run"]["status"],
    "serialization": "正式serialize_run对真实元数据的纯只读序列化；SDK仅保留真实展示头/模型/时间元数据",
    "domain_state_migrated": False, "new_business_or_model_results": False, "database_writes": 0},
    "historical_run": run,
    "historical_events": [serialize_event(actual_row(row)) for row in saved["events"][-8:]]}
result = json.loads(json.dumps(result, ensure_ascii=False, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)))
result["data_sha256"] = action_state_hash(result)
target = ROOT / "frontend/src/components/agent-platform/fixtures/run63.presentation.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
assert hashlib.sha256(source.read_bytes()).hexdigest() == result["provenance"]["source_file_sha256"]
print(json.dumps({"bytes": target.stat().st_size, "data_sha256": result["data_sha256"],
                  "node_ids": [node["id"] for node in run["nodes"]]}, ensure_ascii=False))
