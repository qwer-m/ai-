"""只读组合59已保留尾段与62四次尝试的真实轻边界，专用于CPU保留策略。"""

import gzip
import hashlib
import json
from pathlib import Path
import runpy

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
h = runpy.run_path(str(ROOT / "output/qa/2026-11-13-agent-cost-rerun/round-03/capture_cost_progress.py"))
source59 = ROOT / "backend/tests/agent_platform/fixtures/pause_observability_cross_source_real.json.gz"
actual59 = json.loads(gzip.decompress(source59.read_bytes()))["metadata"]["model_requests"]
actual62, origins = [], []
with h["SessionLocal"](autoflush=False) as db:
    db.execute(h["text"]("SET TRANSACTION READ ONLY"))
    for identity in (414, 415, 416, 417):
        row = db.execute(h["text"]("""SELECT id,run_id,
            JSON_EXTRACT(sdk_state,'$.model_requests') saved,
            JSON_EXTRACT(sdk_state,'$.error_diagnostic.model_requests') failed,
            JSON_EXTRACT(sdk_state,'$.action_checkpoint.budget') budget
            FROM agent_node_runs WHERE id=:identity"""), {"identity": identity}).mappings().one()
        field = "saved" if row["saved"] is not None else "failed"
        requests = h["decoded"](row[field])
        assert isinstance(requests, list) and row["run_id"] == 62
        assert all(value["run_id"] == 62 and value["node_run_id"] == identity for value in requests)
        origins.append({"run_id": 62, "node_id": identity, "source_field": field,
            "row_count": len(requests), "source_array_sha256": h["action_state_hash"](requests),
            "budget": h["decoded"](row["budget"])})
        actual62.extend(requests)
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()
assert len(actual59) == 200 and len(actual62) == 83
data = {"provenance": {"kind": "cross_history_cpu_retention_only", "business_database_writes": 0,
    "new_model_requests": 0, "source59_fixture": str(source59.relative_to(ROOT)),
    "source59_file_sha256": hashlib.sha256(source59.read_bytes()).hexdigest(),
    "source59_retained_sequence_range": [actual59[0]["sequence"], actual59[-1]["sequence"]],
    "source59_original_missing_first_32_not_recreated": True,
    "run63_missing_early_boundaries_not_recreated": True,
    "source62_arrays": origins,
    "control_order": "先62真实83条，再59真实已保留200条；为了测试较早429越过原200窗口。并非单Run实际时间序列，原run/node/sequence/timestamp保持原值",
    "not_a_new_business_run_or_new_model_trace": True},
    "records62": actual62, "records59": actual59}
data["data_sha256"] = h["action_state_hash"](data)
target = BASE / "draft-tree/backend/tests/agent_platform/fixtures/request_boundary_retention_cross_history_real.json.gz"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
print(json.dumps({"bytes": target.stat().st_size, "count59": len(actual59), "count62": len(actual62),
    "failed62": sum(value["status"] == "ended_without_model_response" for value in actual62),
    "data_sha256": data["data_sha256"]}, ensure_ascii=False, indent=2))
