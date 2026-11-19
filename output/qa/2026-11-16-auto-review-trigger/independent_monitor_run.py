"""只读记录真实运行的审查与修稿轨迹，正文仅在状态迁移时展开。"""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend/scripts/qa/verify"))
from capture_generation_run_real_data import capture, redact


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-id", type=int, required=True)
parser.add_argument("--output-dir", type=Path, required=True)
args = parser.parse_args()
summary = capture(args.run_id, summary=True)
target = args.output_dir / "independent-review-trajectory.json"
saved = json.loads(target.read_text(encoding="utf8")) if target.exists() else {
    "run_id": args.run_id, "database_writes": False, "model_requests_sent": 0,
    "summaries": [], "transitions": [],
}
now = datetime.now(timezone.utc).isoformat()
node = summary["nodes"][-1]
projection = node["checkpoint_projection"] or {}
compact = {
    "captured_at": now, "status": summary["run"]["status"],
    "usage": summary["run"]["usage"], "node_id": node["id"],
    "progress": projection.get("progress"), "versions": projection.get("versions"),
    "current_action": projection.get("current_action"),
    "latest_event_sequence": max((event["sequence"] for event in summary["events"]), default=0),
}
saved["summaries"].append(compact)
signature = (compact["status"], json.dumps(compact["versions"], sort_keys=True),
             json.dumps(compact["current_action"], sort_keys=True))
previous = saved.get("last_signature")
review_seen = any(event.get("tool_key") == "request_review" for event in summary["events"])
if review_seen and previous != list(signature):
    with redirect_stdout(io.StringIO()):
        from core.db.database import SessionLocal
        from core.db.model_defs import AgentNodeRun, AgentRunEvent
        from modules.agent_platform.checkpoint_storage import hydrate_action_checkpoint
    from sqlalchemy import select, text

    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        stored = db.get(AgentNodeRun, node["id"])
        checkpoint = hydrate_action_checkpoint(db, stored)
        state = checkpoint["state"]
        event_ids = list(db.scalars(select(AgentRunEvent.id).where(
            AgentRunEvent.run_id == args.run_id).order_by(AgentRunEvent.sequence)))
        events = [{column.name: getattr(db.get(AgentRunEvent, identity), column.name)
                   for column in AgentRunEvent.__table__.columns} for identity in event_ids]
        transition = {
            "captured_at": now, "node_id": node["id"], "state_hash": checkpoint["state_hash"],
            "projection": projection, "events": events,
            "model_requests": stored.sdk_state.get("model_requests", []),
            "receipts": checkpoint["receipts"], "review": state["review"],
            "open_issues": state["open_issues"],
            "case_versions": {key: value["version"] for key, value in state["cases"]["items"].items()},
        }
        snapshot = {"captured_at": now, "run_id": args.run_id, "state": state,
                    "database_writes": False, "model_requests_sent": 0}
        db.rollback()
    snapshot_path = args.output_dir / f"independent-state-{compact['latest_event_sequence']:04d}.json"
    snapshot_path.write_text(json.dumps(redact(snapshot, []), ensure_ascii=False, indent=2, default=str), encoding="utf8")
    transition["state_snapshot_file"] = snapshot_path.name
    saved["transitions"].append(redact(transition, []))
saved["last_signature"] = list(signature)
target.write_text(json.dumps(saved, ensure_ascii=False, indent=2, default=str), encoding="utf8")
print(json.dumps({**compact, "transition_count": len(saved["transitions"])}, ensure_ascii=False, default=str))
