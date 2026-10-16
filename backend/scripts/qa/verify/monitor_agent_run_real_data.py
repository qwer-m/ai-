"""只读记录指定真实运行的状态变化和追加事件，保留超时后的节点及预算证据。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent


def snapshot(run_id: int, last_event: int) -> tuple[dict, list[dict]]:
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, run_id)
        if run is None:
            raise ValueError(f"运行不存在: {run_id}")
        result = {"observed_at": datetime.now(timezone.utc).isoformat(), "status": run.status,
                  "current_node_key": run.current_node_key, "error_message": run.error_message,
                  "usage": (run.run_context or {}).get("usage"), "nodes": []}
        # 先排序轻量主键，再读 JSON，避免完整大字段排序耗尽 MySQL sort buffer。
        node_ids = db.query(AgentNodeRun.id).filter(AgentNodeRun.run_id == run_id).order_by(AgentNodeRun.id).all()
        for (node_id,) in node_ids:
            node = db.get(AgentNodeRun, node_id)
            state = node.sdk_state or {}
            items = state.get("items") or []
            checkpoint = state.get("action_checkpoint") or {}
            domain = checkpoint.get("state") or {}
            result["nodes"].append({
                "id": node.id, "node_key": node.node_key, "status": node.status, "attempt": node.attempt,
                "started_at_utc": node.started_at, "finished_at_utc": node.finished_at,
                "usage": state.get("usage"), "budget_usage": state.get("budget_usage"),
                "error_message": node.error_message, "error_diagnostic": state.get("error_diagnostic"),
                "failure_counts": state.get("failure_counts"),
                "item_status_counts": dict(Counter(item.get("status") for item in items)),
                "items": [{key: item[key] for key in (
                    "item_index", "status", "item_attempt", "error_type", "failure_kind",
                    "error_message", "budget_usage", "error_diagnostic",
                ) if key in item} for item in items],
                "checkpoint": {
                    "budget": checkpoint.get("budget"), "state_hash": checkpoint.get("state_hash"),
                    "completed": domain.get("completed"),
                    "versions": {key: domain[key] for key in ("source_version", "draft_version", "plan_version", "candidate_version") if key in domain},
                    "actions": [{"call_id": key, "tool_key": receipt["tool_key"], "status": receipt["status"]}
                                for key, receipt in (checkpoint.get("receipts") or {}).items()],
                } if checkpoint else None,
            })
        event_ids = db.query(AgentRunEvent.id).filter(
            AgentRunEvent.run_id == run_id, AgentRunEvent.id > last_event,
        ).order_by(AgentRunEvent.id).all()
        events = []
        for (event_id,) in event_ids:
            event = db.get(AgentRunEvent, event_id)
            events.append({"id": event.id, "node_run_id": event.node_run_id, "event_type": event.event_type,
                           "created_at_local": event.created_at, "payload": event.payload})
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    return result, events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("报告已存在，请使用新路径保留证据")
    if not 5 <= args.interval_seconds <= 60:
        parser.error("采集间隔必须在 5 至 60 秒内")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"run_id": args.run_id, "mysql_transaction_per_poll": "READ ONLY", "model_requests_sent": 0,
              "online_database_writes": 0, "snapshots": [], "events": [],
              "timestamp_note": "Python started/finished datetime 使用 UTC；数据库默认 created_at 使用 Asia/Shanghai。"}
    last_event, last_signature = 0, None
    while True:
        current, events = snapshot(args.run_id, last_event)
        report["snapshots"].append(current)
        report["events"].extend(events)
        if events:
            last_event = events[-1]["id"]
        report["latest_status"] = current["status"]
        report["last_observed_at"] = current["observed_at"]
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        signature = [(node["node_key"], node["status"], node["item_status_counts"],
                      (node["checkpoint"] or {}).get("state_hash")) for node in current["nodes"]]
        if signature != last_signature or current["status"] not in {"running", "pending"}:
            print(json.dumps({"observed_at": current["observed_at"], "status": current["status"],
                              "current_node_key": current["current_node_key"],
                              "nodes": [{key: node[key] for key in ("id", "node_key", "status", "item_status_counts")}
                                        for node in current["nodes"]],
                              "new_failed_event_ids": [event["id"] for event in events if "failed" in event["event_type"]]},
                             ensure_ascii=False), flush=True)
            last_signature = signature
        if current["status"] not in {"running", "pending"}:
            break
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
