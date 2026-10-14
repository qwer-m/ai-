"""只读新运行，核对动作回执的真实事件序号及当前规划观察，不干预运行。"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent
from modules.agent_platform.action_journal import action_state_hash, ordered_action_receipts, project_action_failures
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.test_generation_agent import observe_generation_state
from modules.agent_platform.test_generation_planning import planning_support_request_hash


def snapshot(run_id, previous_signature):
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            lightweight_run = db.query(AgentRun.id, AgentRun.status, AgentRun.current_node_key).filter(AgentRun.id == run_id).one()
            node_rows = db.query(AgentNodeRun.id, AgentNodeRun.status, AgentNodeRun.updated_at).filter(
                AgentNodeRun.run_id == run_id, AgentNodeRun.node_key == "resolve_generation",
            ).order_by(AgentNodeRun.id).all()
            signature = [lightweight_run.status, lightweight_run.current_node_key,
                         [[item.id, item.status, str(item.updated_at)] for item in node_rows]]
            summary = {"observed_at": datetime.now(timezone.utc).isoformat(), "run_status": lightweight_run.status,
                       "current_node_key": lightweight_run.current_node_key, "nodes": []}
            if not node_rows or signature == previous_signature:
                summary["status"] = "waiting_for_generation_node" if not node_rows else "unchanged"
                return summary, signature, None
            # 排序仅涉及主键和序号；大 JSON 均按已取得的 ID 读取，不参与 MySQL 排序。
            run = db.get(AgentRun, run_id)
            latest_checkpoint = None
            for row in node_rows:
                node = db.get(AgentNodeRun, row.id)
                checkpoint = deepcopy((node.sdk_state or {}).get("action_checkpoint"))
                if checkpoint is None:
                    summary["nodes"].append({"node_id": row.id, "status": "checkpoint_not_created"})
                    continue
                assert checkpoint["version"] == 2
                assert checkpoint["state_hash"] == action_state_hash(checkpoint["state"])
                ordered = ordered_action_receipts(checkpoint)
                event_rows = db.query(AgentRunEvent.id, AgentRunEvent.sequence).filter(
                    AgentRunEvent.run_id == run_id, AgentRunEvent.node_run_id == row.id,
                    AgentRunEvent.event_type == "agent_action_started",
                ).order_by(AgentRunEvent.sequence).all()
                events = [db.get(AgentRunEvent, item.id) for item in event_rows]
                by_call = {}
                for event in events:
                    call_id = event.payload["tool_call_id"]
                    assert call_id not in by_call
                    by_call[call_id] = event
                assert set(by_call) == set(checkpoint["receipts"])
                bindings = []
                for receipt in ordered:
                    event = by_call[receipt["tool_call_id"]]
                    assert receipt["started_sequence"] == event.sequence
                    assert all(receipt[key] == event.payload[key]
                               for key in ("tool_key", "arguments_hash", "pre_state_hash"))
                    bindings.append({"tool_call_id": receipt["tool_call_id"], "tool_key": receipt["tool_key"],
                        "status": receipt["status"], "started_sequence": receipt["started_sequence"],
                        "event_id": event.id, "event_sequence": event.sequence,
                        "arguments": json.loads(receipt["arguments"]["arguments_json"]),
                        "result": receipt.get("result"), "error": receipt.get("error")})
                failures = project_action_failures(checkpoint, state=checkpoint["state"])
                for items in (reversed(list(checkpoint["receipts"].items())), sorted(checkpoint["receipts"].items())):
                    reordered = {**checkpoint, "receipts": dict(items)}
                    assert project_action_failures(reordered, state=checkpoint["state"]) == failures
                    assert ordered_action_receipts(reordered) == ordered
                raw_input = AgentRunRepository(db).read_node_input(node)
                assert action_state_hash(raw_input) == checkpoint["input_hash"]
                context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                    run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
                    artifacts=deepcopy(run.run_context["artifacts"]), action_input=deepcopy(raw_input),
                    action_state=deepcopy(checkpoint["state"]))
                observation = observe_generation_state(context)
                assert context.action_state == checkpoint["state"]
                plan = observation["planning"]
                available = observation["available_actions"].get("review_planning_route_gaps")
                if available is not None:
                    assert "design_selections" not in available
                    assert available["available_designs"] == plan["reviewable_designs"]
                    assert available["unresolved_design_selections"] == plan["unresolved_design_selections"]
                dispositions = Counter()
                for item in plan["reviewable_designs"]:
                    for disposition, indexes in item["design_indexes_by_disposition"].items():
                        dispositions[disposition] += len(indexes)
                revisions = checkpoint["state"]["planning"]["route_review_revisions"]
                hashes = [planning_support_request_hash(record["input"]) for record in revisions]
                assert len(hashes) == len(set(hashes))
                summary["nodes"].append({"node_id": row.id, "status": "passed",
                    "checkpoint_version": checkpoint["version"], "checkpoint_sha256": action_state_hash(checkpoint),
                    "state_sha256": checkpoint["state_hash"], "receipt_count": len(ordered),
                    "failed_receipt_count": sum(item["status"] == "failed" for item in ordered),
                    "started_event_bijection": True, "bindings": bindings,
                    "json_member_order_matches_chronology": list(checkpoint["receipts"]) == [item["tool_call_id"] for item in ordered],
                    "reordered_projection_invariant": True, "current_failure_projection": failures,
                    "failure_projection_limit": "当前失败投影为空，失败投影乱序验证仅覆盖空结果" if not failures else None,
                    "planning_versions": {key: plan[key] for key in ("source_version", "draft_version", "plan_version", "support_version")},
                    "support_decision_counts": dict(dispositions), "support_review_summary": plan["support_review_summary"],
                    "unresolved_design_selections": plan["unresolved_design_selections"],
                    "route_gaps": plan["route_gaps"], "review_action_scope": available,
                    "planning_can_finish": plan["can_finish"], "plan_accepted": observation["plan_accepted"],
                    "support_revision_count": len(revisions), "support_request_hashes_unique": True,
                    "support_revisions": [{"module_index": record["input"]["module_index"],
                        "designs": record["input"]["missing_test_design_items"],
                        "question": record["input"].get("_planning_feedback", {}).get("question"),
                        "request_sha256": fingerprint, "output": record["output"]}
                        for record, fingerprint in zip(revisions, hashes, strict=True)]})
                latest_checkpoint = {"run_id": run.id, "node_id": row.id, "checkpoint": checkpoint,
                    "observed_at": summary["observed_at"], "started_events": [
                        {"id": event.id, "sequence": event.sequence, "payload": event.payload} for event in events]}
            assert not db.new and not db.dirty and not db.deleted
            summary["status"] = "passed" if summary["nodes"] and all(item["status"] == "passed" for item in summary["nodes"]) else "waiting_for_checkpoint"
            return summary, signature, latest_checkpoint
        finally:
            db.rollback()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=30)
    options = parser.parse_args()
    assert 10 <= options.interval_seconds <= 60
    assert not options.output.exists(), "请使用新输出路径保留已有审计报告"
    report = {"run_id": options.run_id, "status": "waiting", "snapshots": [],
              "model_requests_sent": 0, "online_database_writes": 0,
              "historical_checkpoint_migrated": False, "transaction_per_poll": "READ ONLY",
              "checkpoint_evidence_note": "同名 checkpoint.latest.json 保存最后一次真实原始检查点；各时点绑定与摘要保存在 snapshots"}
    previous_signature = None
    while True:
        try:
            current, signature, checkpoint = snapshot(options.run_id, previous_signature)
            if signature != previous_signature or not options.watch:
                report["snapshots"].append(current)
                previous_signature = signature
                report["status"] = current["status"]
                report["latest_run_status"] = current["run_status"]
                if checkpoint is not None:
                    options.output.with_suffix(".checkpoint.latest.json").write_text(
                        json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
                options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({"observed_at": current["observed_at"], "status": current["status"],
                    "run_status": current["run_status"], "current_node_key": current["current_node_key"],
                    "nodes": [{key: item.get(key) for key in ("node_id", "receipt_count", "failed_receipt_count",
                        "support_decision_counts", "unresolved_design_selections", "support_revision_count")}
                        for item in current["nodes"]]}, ensure_ascii=False), flush=True)
            if not options.watch or current["run_status"] not in {"running", "pending"}:
                return
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__, error=str(error))
            options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        time.sleep(options.interval_seconds)


if __name__ == "__main__":
    main()
