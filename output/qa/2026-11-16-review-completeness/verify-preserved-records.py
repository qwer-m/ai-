"""与发布前摘要逐项比较终态运行和人工批准，只读、不发模型请求。"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentApproval

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--baseline", type=Path, required=True)
parser.add_argument("--output-file", type=Path, required=True)
args = parser.parse_args()
baseline = json.loads(args.baseline.read_text(encoding="utf8"))["preserved"]
observed = {}
with SessionLocal() as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    for kind, model in [("runs", AgentRun), ("approvals", AgentApproval)]:
        observed[kind] = {}
        for key in baseline[kind]:
            row = db.get(model, int(key))
            payload = {column.name: getattr(row, column.name) for column in row.__table__.columns}
            observed[kind][key] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                default=str, separators=(",", ":")).encode()).hexdigest()
    db.rollback()
assert observed == baseline, "历史记录摘要发生变化"
result = {"preserved": observed, "matched": True, "database_writes": False, "model_requests_sent": 0}
args.output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({"matched": True, "run_ids": list(observed["runs"]),
                  "approval_ids": list(observed["approvals"])}, ensure_ascii=False))
