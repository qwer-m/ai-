"""按节点主键读取实际行动状态；仅为只读工程核查，不执行模型或修改运行。"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.checkpoint_storage import hydrate_action_checkpoint
from scripts.qa.verify.capture_generation_run_real_data import redact

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-id", required=True, type=int)
parser.add_argument("--node-id", required=True, type=int)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
with SessionLocal(autoflush=False) as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    node, run = db.get(AgentNodeRun, args.node_id), db.get(AgentRun, args.run_id)
    assert node.run_id == run.id
    checkpoint = hydrate_action_checkpoint(db, node)
    result = {"capture": {"captured_at": datetime.now(timezone.utc).isoformat(),
        "transaction_read_only": True, "database_writes": False, "model_requests_sent": 0},
        "run_id": run.id, "node_id": node.id, "status": run.status,
        "usage": run.run_context.get("usage", {}), "state": checkpoint["state"],
        "receipts": checkpoint["receipts"], "budget": checkpoint["budget"]}
    result = redact(result, [])
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf8")
    db.rollback()
print(json.dumps({"run_id": args.run_id, "node_id": args.node_id, "status": result["status"],
    "case_count": len(result["state"]["cases"]["items"]), "model_requests_sent": 0}, ensure_ascii=False))
