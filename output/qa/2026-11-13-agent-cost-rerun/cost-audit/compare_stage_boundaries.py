"""按真实成功阶段边界对齐两轮，失败和重试保留在同一阶段。"""

from collections import defaultdict
from datetime import timedelta
import json
from pathlib import Path

from prepare_read_path_draft import ROOT
from sqlalchemy import text
from core.db.database import SessionLocal


PHASES = ["govern_source", "draft_source_plan", "rebuild_business_plan", "route_planning_scopes", "review_planning_route_gaps"]


def capture(run_id, path, db):
    observations = json.loads(path.read_text(encoding="utf-8"))
    events = [dict(row) for row in db.execute(text(
        "SELECT sequence,event_type,created_at,payload FROM agent_run_events WHERE run_id=:run AND event_type IN ('run_started','agent_action_started','agent_action_completed','agent_action_failed')"), {"run": run_id}).mappings()]
    for event in events:
        event["payload"] = json.loads(event["payload"]) if isinstance(event["payload"], str) else event["payload"]
    events.sort(key=lambda item: item["sequence"])
    started_at = next(item["created_at"] for item in events if item["event_type"] == "run_started")
    completed, failed = {}, defaultdict(list)
    for event in events:
        key = event["payload"].get("tool_key")
        if key not in PHASES:
            continue
        if event["event_type"] == "agent_action_completed" and key not in completed:
            completed[key] = event
        if event["event_type"] == "agent_action_failed" and key not in completed:
            failed[key].append(event["payload"])
    result = {}
    prior_time, prior_usage = started_at, dict.fromkeys(["requests", "input_tokens", "output_tokens", "total_tokens", "attempted_requests"], 0)
    for phase in PHASES:
        event = completed.get(phase)
        if event is None:
            break
        call_id = event["payload"]["tool_call_id"]
        selected = None
        for row in observations["snapshots"]:
            snapshot = row["snapshot"]
            checkpoint = snapshot["latest_node"]["checkpoint"]
            current = (checkpoint.get("projection") or {}).get("current_action") or {}
            if current.get("tool_call_id") == call_id and current.get("status") == "completed":
                selected = row
                break
        if selected is None:
            result[phase] = {"completed": True, "usage_snapshot_available": False}
            continue
        snapshot = selected["snapshot"]
        checkpoint = snapshot["latest_node"]["checkpoint"]
        usage = snapshot["run"]["usage"]
        result[phase] = {"completed_at_local": event["created_at"],
            "elapsed_from_run_start_seconds": (event["created_at"] - started_at).total_seconds(),
            "phase_wall_including_decision_gap_and_retries_seconds": (event["created_at"] - prior_time).total_seconds(),
            "cumulative_reported_usage": usage,
            "phase_usage_delta": {key: usage[key] - prior_usage[key] for key in usage},
            "pending_requests_at_selected_snapshot": checkpoint["budget_pending_requests"],
            "unknown_requests_at_selected_snapshot": checkpoint["budget_interrupted_requests"],
            "observation_selected_utc": selected["observed_at"], "failed_action_attempts": len(failed[phase]),
            "business_children_saved": snapshot["latest_node"]["journal"]["validated_agent_counts"]}
        prior_time, prior_usage = event["created_at"], usage
    return result


def main():
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        first = capture(59, ROOT / "output/qa/2026-11-12-single-agent-live/round-11/observations.json", db)
        second = capture(60, ROOT / "output/qa/2026-11-13-agent-cost-rerun/round-01/observations.json", db)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    common = {phase: {"run59": first[phase], "run60": second[phase]} for phase in PHASES if phase in first and phase in second}
    report = {"runs": [59, 60], "common_completed_stage_boundaries": common,
        "database_business_writes": 0, "model_requests_sent": 0,
        "limits": "时间来自真实事件边界精确到秒。用量取成功行动首次可见的15秒观察快照，均列出pending及unknown；不把未知预留称作实耗。边界包括阶段前根协调器选择及阶段内失败恢复。Run60仍执行中，非最终比较。"}
    output = Path(__file__).with_name("run59-run60-stage-boundaries.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(common, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
