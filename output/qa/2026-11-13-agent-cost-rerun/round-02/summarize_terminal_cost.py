"""Run61终态SDK边界与成本核对；只读轻量诊断，不恢复完整领域状态。"""

from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import text
from core.db.database import SessionLocal


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def union_seconds(intervals):
    total, current = 0.0, None
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
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = dict(db.execute(text("SELECT id,status,started_at,finished_at,JSON_EXTRACT(run_context,'$.usage') AS observed_usage FROM agent_runs WHERE id=61")).mappings().one())
        if run["finished_at"] is None:
            print(json.dumps({"run_id": 61, "status": run["status"], "terminal_cost_ready": False}, ensure_ascii=False))
            db.rollback()
            return
        run["usage"] = decode(run.pop("observed_usage"))
        rows = [dict(row) for row in db.execute(text("SELECT id,attempt,status,JSON_EXTRACT(sdk_state,'$.model_requests') AS observed_requests,JSON_EXTRACT(sdk_state,'$.error_diagnostic.model_requests') AS failed_attempt_requests,JSON_EXTRACT(sdk_state,'$.tool_calls') AS observed_tools,JSON_EXTRACT(sdk_state,'$.action_checkpoint.budget') AS observed_budget FROM agent_node_runs WHERE run_id=61")).mappings()]
        event_rows = [dict(row) for row in db.execute(text("SELECT sequence,event_type,created_at,payload FROM agent_run_events WHERE run_id=61 AND event_type IN ('agent_action_started','agent_action_completed','agent_action_failed','managed_concurrency_adjusted')")).mappings()]
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    requests, tools, budgets = [], [], []
    for row in rows:
        records = decode(row["observed_requests"]) or decode(row["failed_attempt_requests"]) or []
        requests.extend({**request, "owner_node_id": row["id"]} for request in records)
        tools.extend({**tool, "owner_node_id": row["id"]} for tool in decode(row["observed_tools"]) or [])
        budgets.append({"node_id": row["id"], "budget": decode(row["observed_budget"])})
    measured, by_agent, by_action, by_model = Counter(), defaultdict(Counter), defaultdict(list), defaultdict(Counter)
    for request in requests:
        usage = request.get("usage") or {}
        measured.update(usage)
        by_model[request["model"]["name"]].update(usage)
        agent = by_agent[request["agent_key"]]
        agent.update(usage)
        agent["diagnostic_request_count"] += 1
        agent["sdk_elapsed_sum_seconds"] += request.get("elapsed_seconds", 0)
        agent["context_bytes_sum"] += request.get("measured_context_bytes", 0)
        agent["context_bytes_max"] = max(agent["context_bytes_max"], request.get("measured_context_bytes", 0))
        by_action[request.get("action_call_id")].append(request)
    intervals = [(datetime.fromisoformat(request["started_at"]), datetime.fromisoformat(request["finished_at"]))
        for request in requests if request.get("started_at") and request.get("finished_at")]
    complete = len(requests) == run["usage"]["attempted_requests"]
    reconciled = all(measured[key] == run["usage"][key] for key in ("requests", "input_tokens", "output_tokens", "total_tokens"))
    elapsed = (run["finished_at"] - run["started_at"]).total_seconds()
    union = union_seconds(intervals)
    starts = {}
    actions = []
    for event in sorted(event_rows, key=lambda row: row["sequence"]):
        payload = decode(event["payload"])
        if event["event_type"] == "agent_action_started":
            starts[payload["tool_call_id"]] = event
        elif event["event_type"] in {"agent_action_completed", "agent_action_failed"}:
            first = starts[payload["tool_call_id"]]
            observed = by_action[payload["tool_call_id"]]
            action_intervals = [(datetime.fromisoformat(item["started_at"]), datetime.fromisoformat(item["finished_at"])) for item in observed]
            wall = (event["created_at"] - first["created_at"]).total_seconds()
            actions.append({"action_sequence": payload["started_sequence"], "tool_key": payload["tool_key"], "status": payload["status"],
                "elapsed_seconds": wall, "sdk_request_count": len(observed),
                "sdk_inflight_union_seconds": union_seconds(action_intervals),
                "usage": dict(sum((Counter(item.get("usage") or {}) for item in observed), Counter()))})
    support = [tool for tool in tools if tool.get("tool_key") == "agent:test_planning_route_gap_reviewer"]
    support_sessions = [{"status": tool.get("status"), "replayed": tool.get("replayed", False),
        "usage": tool.get("usage"), "submissions": len([item for item in tool.get("submissions", []) if item["tool_key"] == "submit_result"]),
        "observation_status_counts": dict(Counter(item["status"] for item in tool.get("tool_observations", [])))} for tool in support]
    result = {"run": run, "elapsed_seconds": elapsed, "request_diagnostics_count": len(requests),
        "all_attempt_boundaries_retained": complete, "all_reported_usage_reconciled": reconciled,
        "usage_from_request_boundaries": dict(measured), "per_agent": dict(by_agent), "per_model": dict(by_model), "actions": actions,
        "sdk_inflight_union_seconds": union,
        "outside_any_sdk_request_seconds": elapsed - union if complete else None,
        "support_sessions": support_sessions, "budgets_by_attempt_not_summed": budgets,
        "failure_requests": [request for request in requests if request["status"] != "response_received"],
        "cache_metrics_coverage": {"records": len(requests), "provider_reported_cache_count": sum(
            (request.get("token_usage") or {}).get("cached_input_tokens") is not None for request in requests)},
        "model_requests_sent_by_audit": 0, "business_database_writes": 0,
        "limits": "SDK在途边界含网络、供应商处理和SDK响应处理，不能单独等同服务器计算时长。无在途时间含CPU/DB/持久化/RAG等工具工作，不直接称全部CPU。首轮原始来源17个结果复验复用不计新请求。"}
    progress_path = Path(__file__).with_name("cost-progress.json")
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    baseline = progress["first_pause_usage_baseline"]
    combined = {key: baseline[key] + measured[key] for key in ("requests", "input_tokens", "output_tokens", "total_tokens")}
    post_complete = len(requests) + baseline["attempted_requests"] == run["usage"]["attempted_requests"]
    phase_reconciled = combined == {key: run["usage"][key] for key in combined}
    waits = progress["human_wait_windows"]
    post_start = datetime.fromisoformat(waits[-1]["ended_at_utc"]) if waits else None
    post_wall = (run["finished_at"] - post_start).total_seconds() if post_start else None
    result.update(human_wait_seconds=progress["human_wait_seconds"], active_elapsed_seconds=elapsed-progress["human_wait_seconds"],
        phase_boundary_usage_reconciliation={"pre_pause_exact_budget": baseline, "post_approval_request_boundaries": dict(measured),
            "pre_pause_plus_post_equals_exact_final_usage": phase_reconciled, "post_approval_attempt_boundaries_complete": post_complete,
            "initial_95_timing_boundaries_still_missing": True},
        post_approval_timing={"from_runtime_confirmation_wait_end_utc": post_start.isoformat() if post_start else None,
            "approval_decided_at_utc": waits[-1].get("approval_decided_at_utc") if waits else None,
            "to_finished_at_utc": run["finished_at"].isoformat(), "wall_seconds": post_wall,
            "sdk_inflight_union_seconds": union,
            "outside_any_sdk_request_seconds": post_wall-union if post_complete and phase_reconciled and post_wall is not None else None,
            "first_sdk_request_at_utc": min((start for start,_ in intervals),default=None).isoformat() if intervals else None,
            "limit":"仅审批后68请求完整边界。时间从运行时停止扣除人工等待的确认处理入口计起，包含后续17.293313秒确认应用与持久化，再到Worker恢复初始化；outside SDK包括CPU/DB等，不等于纯CPU。不得补造首95请求时序或把SDK span称为纯服务端推理。"})
    resource_path = Path(__file__).with_name("worker-resource-samples.jsonl")
    if resource_path.exists():
        resource_rows = [json.loads(line) for line in resource_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        first_resource,last_resource = resource_rows[0],resource_rows[-1]
        result["worker_resource_sample_window"] = {"first":first_resource,"last":last_resource,
            "cpu_delta_seconds":last_resource["cpu_total_seconds"]-first_resource["cpu_total_seconds"],
            "sample_wall_seconds":(datetime.fromisoformat(last_resource["observed_at_utc"])-datetime.fromisoformat(first_resource["observed_at_utc"])).total_seconds(),
            "limit":"只覆盖最后一段进程采样，CPU可与并行SDK重叠。raw采样中的active/human_wait旧派生字段被time-accounting-correction.json更正，保留原记录；CPU/采样墙钟不受影响。"}
    path = Path(__file__).with_name("terminal-cost-audit.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(path), "run": run, "elapsed_seconds": elapsed,
        "all_requests_retained": complete, "usage_reconciled": reconciled,
        "sdk_inflight_seconds": union, "outside_sdk_seconds": result["outside_any_sdk_request_seconds"]}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
