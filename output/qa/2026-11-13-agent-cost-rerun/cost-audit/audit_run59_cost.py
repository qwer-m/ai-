"""核对真实 Run59 的用量和墙钟；只读数据库，不发送模型请求。"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from core.db.database import SessionLocal
from sqlalchemy import text


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def interval_union(intervals):
    total = 0.0
    current = None
    for start, end in sorted(intervals):
        if current is None:
            current = [start, end]
        elif start <= current[1]:
            current[1] = max(current[1], end)
        else:
            total += (current[1] - current[0]).total_seconds()
            current = [start, end]
    return total + ((current[1] - current[0]).total_seconds() if current else 0)


def main():
    original = ROOT / "output/qa/2026-11-12-single-agent-live/round-11"
    snapshot = json.loads((original / "final-snapshot.json").read_text(encoding="utf-8"))
    sdk = snapshot["nodes"][-1]["sdk_state"]
    by_agent = defaultdict(Counter)
    by_child_calls = defaultdict(list)
    for tool in sdk["tool_calls"]:
        if "usage" not in tool:
            continue
        key = tool["tool_key"].removeprefix("agent:")
        by_agent[key]["validated_sessions"] += 1
        by_agent[key].update(tool["usage"])
        by_agent[key]["recorded_tool_calls"] += len(tool.get("submissions", []))
        by_agent[key]["rejected_submissions"] += sum(
            item.get("status") == "rejected" for item in tool.get("tool_observations", []))
        by_child_calls[key].append({"input_hash": tool.get("input_hash"),
            "usage": tool["usage"], "projection": tool.get("input_projection"),
            "recorded_tool_call_names": dict(Counter(item["tool_key"] for item in tool.get("submissions", [])))})
    root = Counter(sdk["usage"])
    for value in by_agent.values():
        for key in sdk["usage"]:
            root[key] -= value[key]
    by_agent["test_generation_coordinator"] = root
    total = sdk["usage"]
    for value in by_agent.values():
        value["input_share_percent"] = round(value["input_tokens"] / total["input_tokens"] * 100, 3)

    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run_header = dict(db.execute(text("SELECT id,status,started_at,finished_at FROM agent_runs WHERE id=59")).mappings().one())
        raw_events = [dict(row) for row in db.execute(text("SELECT sequence,event_type,created_at,payload FROM agent_run_events WHERE run_id=59 AND event_type IN ('agent_action_started','agent_action_completed','agent_action_failed')")).mappings()]
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    for event in raw_events:
        event["payload"] = decoded(event["payload"])
    action_events = defaultdict(dict)
    for event in raw_events:
        action_events[event["payload"]["tool_call_id"]][event["event_type"]] = event
    actions = []
    for call_id, events in action_events.items():
        started = events["agent_action_started"]
        ended = events.get("agent_action_completed", events.get("agent_action_failed"))
        item = {"tool_call_id": call_id, "action_sequence": started["payload"]["started_sequence"],
            "tool_key": started["payload"]["tool_key"], "status": ended["payload"]["status"],
            "started_at_local_db": started["created_at"], "finished_at_local_db": ended["created_at"],
            "elapsed_seconds": (ended["created_at"] - started["created_at"]).total_seconds()}
        known_requests = [request for request in sdk["model_requests"] if request["action_call_id"] == call_id]
        item["retained_model_request_count"] = len(known_requests)
        item["retained_model_usage"] = dict(sum((Counter(request["usage"]) for request in known_requests), Counter()))
        item["retained_model_elapsed_sum"] = round(sum(request["elapsed_seconds"] for request in known_requests), 3)
        actions.append(item)
    actions.sort(key=lambda item: item["action_sequence"])

    by_diagnostic = defaultdict(Counter)
    intervals = []
    for request in sdk["model_requests"]:
        value = by_diagnostic[request["agent_key"]]
        value.update(request["usage"])
        value["requests_retained"] += 1
        value["elapsed_sum_seconds"] += request["elapsed_seconds"]
        value["context_bytes_sum"] += request["measured_context_bytes"]
        value["context_bytes_max"] = max(value["context_bytes_max"], request["measured_context_bytes"])
        intervals.append((datetime.fromisoformat(request["started_at"]), datetime.fromisoformat(request["finished_at"])))
    root_requests = [request for request in sdk["model_requests"] if request["agent_key"] == "test_generation_coordinator"]
    root_known = sum((Counter(request["usage"]) for request in root_requests), Counter())
    root_missing = {key: root[key] - root_known[key] for key in sdk["usage"]}
    first, last = min(start for start, _ in intervals), max(end for _, end in intervals)
    union = interval_union(intervals)
    report = {"run": run_header, "total_usage": total,
        "accounting_reconciled": all(sum(row[key] for row in by_agent.values()) == total[key] for key in total),
        "usage_by_agent_complete": dict(by_agent),
        "child_session_usage_includes_nested_agents": True,
        "root_derived_as_total_minus_child_sessions": True,
        "last_200_request_diagnostics": {"record_count": len(sdk["model_requests"]),
            "sequence_first": sdk["model_requests"][0]["sequence"], "sequence_last": sdk["model_requests"][-1]["sequence"],
            "by_actual_agent": dict(by_diagnostic), "first_started_utc": first, "last_finished_utc": last,
            "span_seconds": (last - first).total_seconds(), "model_interval_union_seconds": round(union, 3),
            "no_model_request_in_flight_seconds": round((last - first).total_seconds() - union, 3),
            "limits": "232次请求只有最后200条详细边界持久化，最早32条不可从此快照恢复；子会话聚合用量和总账仍完整。"},
        "coordinator_retained_requests": root_requests, "coordinator_unretained_4_usage": root_missing,
        "actions": actions, "child_session_headers": dict(by_child_calls),
        "audit_model_requests_sent": 0, "database_business_writes": 0,
        "timing_limits": "模型耗时可并发，不能把sum当作墙钟；事件created_at精度为秒，SDK时间UTC，DB时间Asia/Shanghai。无模型在途时间包括CPU/数据库/持久化/SDK工作，不直接归因供应商延迟。"}
    output = Path(__file__).with_name("run59-cost-audit.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "reconciled": report["accounting_reconciled"],
        "by_agent": dict(by_agent), "last200_model_union_seconds": round(union, 3),
        "last200_no_model_in_flight_seconds": round((last-first).total_seconds()-union, 3),
        "actions": [{key: item[key] for key in ("action_sequence", "tool_key", "elapsed_seconds", "retained_model_request_count", "retained_model_elapsed_sum")} for item in actions]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
