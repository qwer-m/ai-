"""只读审计真实运行的阶段耗时、专家并发、行动回执及持久化结果。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent
from modules.agent_platform.results import persisted_test_generation_result


def concurrency(requests):
    points = []
    for request in requests:
        if request.get("started_at") and request.get("finished_at"):
            points.extend(((request["started_at"], 1), (request["finished_at"], -1)))
    active = peak = 0
    for _, delta in sorted(points):
        active += delta
        peak = max(peak, active)
    return peak


def audit(run_ids):
    report = {"captured_at": datetime.now(timezone.utc).isoformat(),
              "mysql_transaction": "READ ONLY", "model_requests_sent": 0,
              "online_database_writes": 0, "runs": []}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        for run_id in run_ids:
            run = db.get(AgentRun, run_id)
            if run is None:
                raise LookupError(f"运行不存在: {run_id}")
            result = persisted_test_generation_result(run)
            item = {"run_id": run_id, "status": run.status, "started_at": run.started_at,
                    "finished_at": run.finished_at, "error": run.error_message,
                    "elapsed_seconds": (run.finished_at-run.started_at).total_seconds()
                        if run.finished_at and run.started_at else None,
                    "usage": (run.run_context or {}).get("usage"),
                    "delivered_case_count": len((result or {}).get("test_cases") or []),
                    "final_review": (result or {}).get("final_review"), "nodes": []}
            # 大 JSON 行不能参与数据库排序；先查主键再逐行读取。
            ids = db.query(AgentNodeRun.id).filter_by(run_id=run_id).all()
            for (node_id,) in sorted(ids):
                node = db.get(AgentNodeRun, node_id)
                sdk = node.sdk_state or {}
                diagnostic = sdk.get("error_diagnostic") or {}
                checkpoint = sdk.get("action_checkpoint") or {}
                planning = (checkpoint.get("state") or {}).get("planning") or {}
                requests = sdk.get("model_requests") or diagnostic.get("model_requests") or []
                grouped = {}
                for request in requests:
                    grouped.setdefault(request.get("action_tool") or "coordinator", []).append(request)
                item["nodes"].append({
                    "node_run_id": node_id, "node_key": node.node_key, "attempt": node.attempt,
                    "status": node.status, "started_at": node.started_at, "finished_at": node.finished_at,
                    "elapsed_seconds": (node.finished_at-node.started_at).total_seconds()
                        if node.finished_at and node.started_at else None,
                    "error": node.error_message, "budget": checkpoint.get("budget"),
                    "validated_specialist_counts": dict(Counter(
                        receipt["identity"]["agent_key"] for receipt in
                        (checkpoint.get("validated_agent_calls") or {}).values())),
                    "planning_progress": {key: {"total": len(planning.get(key) or []),
                                                 "complete": sum(value is not None for value in planning.get(key) or [])}
                                          for key in ("route_records", "gap_records")},
                    "actions": [{"tool_key": value["tool_key"], "status": value["status"],
                                 "error_type": (value.get("error") or {}).get("error_type")}
                                for value in (checkpoint.get("receipts") or {}).values()],
                    "model_request_groups": {key: {"request_count": len(values),
                        "peak_concurrency": concurrency(values),
                        "sum_request_seconds": round(sum(value.get("elapsed_seconds", 0) for value in values), 3),
                        "usage": {field: sum((value.get("usage") or {}).get(field, 0) for value in values)
                                  for field in ("input_tokens", "output_tokens", "total_tokens")}}
                        for key, values in grouped.items()},
                })
            event_types = ("agent_action_started", "agent_action_completed", "agent_action_failed", "map_item_failed")
            rows = db.query(AgentRunEvent.id, AgentRunEvent.event_type, AgentRunEvent.created_at,
                            AgentRunEvent.payload).filter(AgentRunEvent.run_id == run_id,
                            AgentRunEvent.event_type.in_(event_types)).all()
            item["action_and_failure_events"] = [dict(row._mapping) for row in sorted(rows, key=lambda row: row.id)]
            report["runs"].append(item)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-ids", type=int, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError(options.output)
    report = audit(options.run_ids)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps([{key: item[key] for key in ("run_id", "status", "elapsed_seconds", "delivered_case_count")}
                      for item in report["runs"]], ensure_ascii=False))
