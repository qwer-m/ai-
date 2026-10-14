"""只读采集 Run31 持久状态，不调用模型、不更新业务数据库。"""

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentNodeRun, AgentRunEvent

OUTPUT = ROOT / "output/qa/2026-09-20-run31-readonly-monitor.json"
assert not OUTPUT.exists(), "保留已有证据，不覆盖其他监控记录"
report = {
    "run_id": 31,
    "monitoring_started_at": datetime.now(timezone.utc).isoformat(),
    "mysql_transaction_per_poll": "READ ONLY",
    "poll_interval_seconds": 30,
    "model_requests_by_monitor": 0,
    "online_database_writes": 0,
    "production_source_writes": 0,
    "timestamp_note": "run/node Python datetime are UTC; DB created_at defaults are Asia/Shanghai",
    "snapshots": [],
    "events": [],
}
last_event = 0
last_signature = None
while True:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, 31)
        snapshot = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": run.status,
            "current_node_key": run.current_node_key,
            "error_message": run.error_message,
            "nodes": [],
        }
        node_ids = db.query(AgentNodeRun.id).filter(AgentNodeRun.run_id == 31).order_by(AgentNodeRun.id).all()
        for (node_id,) in node_ids:
            node = db.get(AgentNodeRun, node_id)
            state = node.sdk_state or {}
            items = state.get("items") or []
            if isinstance(items, dict):
                items = list(items.values())
            entry = {
                "id": node.id, "node_key": node.node_key,
                "status": node.status, "attempt": node.attempt,
                "started_at_utc": node.started_at, "finished_at_utc": node.finished_at,
                "usage": state.get("usage"), "budget_usage": state.get("budget_usage"),
                "attempted_requests": state.get("attempted_requests"),
                "error_message": node.error_message,
                "failure_counts": state.get("failure_counts"),
                "item_status_counts": dict(Counter(i.get("status") for i in items)),
                "items": [{k: i[k] for k in (
                    "item_index", "status", "item_attempt", "error_type", "failure_kind",
                    "error_message", "budget_usage", "error_diagnostic",
                ) if k in i} for i in items],
                "tool_observations": state.get("tool_observations"),
                "error_diagnostic": state.get("error_diagnostic"),
            }
            snapshot["nodes"].append(entry)
        event_ids = db.query(AgentRunEvent.id).filter(
            AgentRunEvent.run_id == 31, AgentRunEvent.id > last_event,
        ).order_by(AgentRunEvent.id).all()
        fresh_events = []
        for (event_id,) in event_ids:
            event = db.get(AgentRunEvent, event_id)
            fresh_events.append({
                "id": event.id, "node_run_id": event.node_run_id,
                "sequence": event.sequence, "event_type": event.event_type,
                "created_at_local": event.created_at, "payload": event.payload,
            })
            last_event = event.id
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    report["snapshots"].append(snapshot)
    report["events"].extend(fresh_events)
    report["last_observed_at"] = snapshot["observed_at"]
    report["latest_status"] = snapshot["status"]
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    signature = [(n["node_key"], n["status"], n["item_status_counts"]) for n in snapshot["nodes"]]
    if signature != last_signature or snapshot["status"] != "running":
        print(json.dumps({
            "observed_at": snapshot["observed_at"], "status": snapshot["status"],
            "current_node_key": snapshot["current_node_key"],
            "nodes": [{k: n[k] for k in ("id", "node_key", "status", "usage", "item_status_counts")} for n in snapshot["nodes"]],
            "new_failures": [e for e in fresh_events if "failed" in e["event_type"]],
        }, ensure_ascii=False, default=str), flush=True)
        last_signature = signature
    if snapshot["status"] in {"success", "failed", "cancelled", "completed"}:
        break
    time.sleep(30)
