"""Run61轻量只读成本观察；不展开完整state，不发送模型或业务写请求。"""

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import runpy
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.checkpoint_storage import ArtifactStore
from modules.agent_platform.test_generation_semantics import planning_support_fact_ref_catalog
from modules.agent_platform.test_generation_planning import postprocess_planning_support_decisions

HELPER = runpy.run_path(str(ROOT / "output/qa/2026-11-12-single-agent-live/round-10/_audit_planning_readonly.py"))
TargetedReader = HELPER["TargetedReader"]
MILESTONES = ["govern_source", "draft_source_plan", "rebuild_business_plan", "route_planning_scopes",
    "review_planning_route_gaps", "accept_generation_plan", "generate_cases", "review_generated_cases",
    "review_all_cases", "finish_generation"]


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def read_observations(path):
    if path.parent == Path(__file__).parent and path.name == "observations.json":
        snapshots = {}
        for candidate in sorted(path.parent.glob("observations*.json")):
            for row in json.loads(candidate.read_text(encoding="utf-8")).get("snapshots", []):
                snapshots[row["observed_at"]] = row
        return {"snapshots": sorted(snapshots.values(), key=lambda row: row["observed_at"])}
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"snapshots": []}


def milestone_samples(observations):
    selected = {}
    for row in observations["snapshots"]:
        snapshot = row["snapshot"]
        checkpoint = snapshot["latest_node"]["checkpoint"]
        current = (checkpoint.get("projection") or {}).get("current_action") or {}
        key = current.get("tool_key")
        if key not in MILESTONES or key in selected or current.get("status") != "completed":
            continue
        selected[key] = {"call_id": current["tool_call_id"], "observed_at_utc": row["observed_at"],
            "reported_usage_snapshot": snapshot["run"]["usage"], "pending_requests": checkpoint.get("budget_pending_requests"),
            "unknown_requests": checkpoint.get("budget_interrupted_requests")}
    return selected


def no_inflight_lower_bound(observations):
    total = 0.0
    for previous, current in zip(observations["snapshots"], observations["snapshots"][1:]):
        a, b = previous["snapshot"], current["snapshot"]
        if (a["run"]["status"] == b["run"]["status"] == "running"
                and a["latest_node"]["id"] == b["latest_node"]["id"]
                and a["latest_node"]["checkpoint"].get("budget_pending_requests") == 0
                and b["latest_node"]["checkpoint"].get("budget_pending_requests") == 0
                and a["run"]["usage"]["attempted_requests"] == b["run"]["usage"]["attempted_requests"]):
            total += (datetime.fromisoformat(current["observed_at"]) - datetime.fromisoformat(previous["observed_at"])).total_seconds()
    return round(total, 3)


def event_headers(db, run_id):
    rows = [dict(row) for row in db.execute(text("SELECT sequence,event_type,created_at,payload FROM agent_run_events WHERE run_id=:run AND event_type IN ('run_started','agent_action_started','agent_action_completed','agent_action_failed','managed_concurrency_adjusted')"), {"run": run_id}).mappings()]
    for row in rows:
        row["payload"] = decoded(row["payload"])
    rows.sort(key=lambda row: row["sequence"])
    return rows


def compare_milestones(db, current_observations):
    result = {}
    for run_id, path in ((59, ROOT / "output/qa/2026-11-12-single-agent-live/round-11/observations.json"),
        (60, ROOT / "output/qa/2026-11-13-agent-cost-rerun/round-01/observations.json"), (61, None)):
        samples = milestone_samples(current_observations if path is None else read_observations(path))
        events = event_headers(db, run_id)
        started = db.execute(text("SELECT started_at FROM agent_runs WHERE id=:run"), {"run": run_id}).scalar_one()
        for key, sample in samples.items():
            completed = next((row for row in events if row["event_type"] == "agent_action_completed"
                and row["payload"]["tool_call_id"] == sample["call_id"]), None)
            if completed:
                sample.update(completed_at_local=str(completed["created_at"]),
                    elapsed_from_start_seconds=(completed["created_at"] - timedelta(hours=8) - started).total_seconds())
        result[str(run_id)] = samples
    return {key: {run: value[key] for run, value in result.items() if key in value}
        for key in MILESTONES if key in result["61"]}


def receipt_record(db, call_id):
    identity = db.execute(text("SELECT MAX(r.id) FROM agent_action_receipts r JOIN agent_journal_branches b ON b.id=r.branch_id JOIN agent_node_runs n ON n.id=b.owner_node_id WHERE n.run_id=61 AND r.tool_call_id=:call"), {"call": call_id}).scalar_one()
    return decoded(db.execute(text("SELECT record FROM agent_action_receipts WHERE id=:identity"), {"identity": identity}).scalar_one())


def verify_support(db, record, reader, catalog_cache):
    store = reader.store
    raw = store.get(record["raw_output_ref"])
    normalized = store.get(record["output_ref"])
    assert action_state_hash(raw) == record["raw_output_hash"]
    assert action_state_hash(normalized) == record["output_hash"]
    refs = [value for decision in raw["decisions"] for value in decision["fact_refs"]]
    refs += [value for decision in raw["decisions"] for gap in decision["support_gaps"] for value in gap["evidence_fact_refs"]]
    refs += [side["fact_ref"] for decision in raw["decisions"] for gap in decision["support_gaps"]
        if gap["kind"] == "source_conflict" for side in gap["conflict"]["sides"]]
    assert all(type(value) is int for value in refs)
    assert all("fact_ids" not in decision and "fact_refs" not in normal
        for decision, normal in zip(raw["decisions"], normalized["decisions"], strict=True))
    assert all(isinstance(identity, str) for decision in normalized["decisions"] for identity in decision["fact_ids"])
    receipt = receipt_record(db, record["parent_tool_call_id"])
    arguments = json.loads(store.get(receipt["arguments_ref"])["arguments_json"])
    checked = False
    if not arguments.get("design_selections") and not arguments.get("additional_scope_ids"):
        key = (receipt["pre_state_hash"], raw["module_index"])
        if key not in catalog_cache:
            entry = reader.entry_at(receipt["pre_state_hash"], ["planning", "gap_inputs"])
            count = reader.array_length(entry["ref"]) if "ref" in entry else len(entry["value"])
            index = next(index for index in range(count) if reader.subtree(receipt["pre_state_hash"],
                ["planning", "gap_inputs", index, "module_index"]) == raw["module_index"])
            catalog_cache[key] = reader.subtree(receipt["pre_state_hash"], ["planning", "gap_inputs", index])
        item = deepcopy(catalog_cache[key])
        selected = {decision["test_design_item_index"] for decision in raw["decisions"]}
        item["missing_test_design_items"] = [design for design in item["missing_test_design_items"]
            if design["test_design_item_index"] in selected]
        catalog = planning_support_fact_ref_catalog(item["candidate_facts"])
        decoded_output = deepcopy(raw)
        for decision in decoded_output["decisions"]:
            decision["fact_ids"] = [catalog[value] for value in decision.pop("fact_refs")]
            for gap in decision["support_gaps"]:
                gap["evidence_fact_ids"] = [catalog[value] for value in gap.pop("evidence_fact_refs")]
                if gap["kind"] == "source_conflict":
                    for side in gap["conflict"]["sides"]:
                        side["fact_id"] = catalog[side.pop("fact_ref")]
        assert postprocess_planning_support_decisions(None, {"item_input": item, "item_output": decoded_output}) == normalized
        checked = True
    return {"raw_integer_refs": True, "normalized_stable_ids": True,
        "full_original_catalog_mapping_and_domain_validation": checked, "decision_count": len(raw["decisions"]),
        "raw_output_hash": record["raw_output_hash"], "normalized_output_hash": record["output_hash"],
        "gap_kind_counts": dict(Counter(gap["kind"] for decision in raw["decisions"] for gap in decision["support_gaps"]))}


def capture(directory, previous):
    observations = read_observations(directory / "observations.json")
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = dict(db.execute(text("SELECT id,status,started_at,finished_at,JSON_EXTRACT(run_context,'$.usage') AS observed_usage FROM agent_runs WHERE id=61")).mappings().one())
        run["usage"] = decoded(run.pop("observed_usage"))
        human_wait_windows = []
        for approval in db.execute(text("SELECT id,status,JSON_UNQUOTE(JSON_EXTRACT(request_payload,'$.paused_at')) AS paused_at,decided_at FROM agent_approvals WHERE run_id=61")).mappings():
            if not approval["paused_at"]:
                continue
            start = datetime.fromisoformat(approval["paused_at"]).replace(tzinfo=None)
            end = approval["decided_at"] or datetime.now(timezone.utc).replace(tzinfo=None)
            human_wait_windows.append({"approval_id": approval["id"], "status": approval["status"],
                "paused_at_utc": start.isoformat(), "ended_at_utc": end.isoformat(),
                "human_wait_seconds": max(0, (end - start).total_seconds()),
                "end_kind": "approval_decided_at" if approval["decided_at"] else "still_waiting"})
        human_wait_seconds = sum(window["human_wait_seconds"] for window in human_wait_windows)
        run_elapsed = ((run["finished_at"] or datetime.now(timezone.utc).replace(tzinfo=None)) - run["started_at"]).total_seconds()
        nodes = [dict(row) for row in db.execute(text("SELECT id,attempt,status,error_message,JSON_EXTRACT(sdk_state,'$.action_checkpoint.budget') AS observed_budget,JSON_EXTRACT(sdk_state,'$.action_checkpoint.projection') AS observed_projection,JSON_EXTRACT(sdk_state,'$.action_checkpoint.managed_parallelism') AS observed_parallelism,JSON_EXTRACT(sdk_state,'$.error_diagnostic') AS observed_diagnostic FROM agent_node_runs WHERE run_id=61")).mappings()]
        nodes.sort(key=lambda row: row["id"])
        failures = []
        for node in nodes:
            for key in ("observed_budget", "observed_projection", "observed_parallelism", "observed_diagnostic"):
                node[key] = decoded(node[key])
            diag = node.pop("observed_diagnostic") or {}
            for request in diag.get("model_requests", []):
                if request["status"] == "ended_without_model_response":
                    failures.append({"node_id": node["id"], "sequence": request["sequence"],
                        "agent_key": request["agent_key"], "exception_chain": request.get("exception_chain"),
                        "elapsed_seconds": request.get("elapsed_seconds")})
        events = event_headers(db, 61)
        saved = dict(previous.get("support_receipts", {}))
        checked_ids = [int(identity) for identity in saved]
        rows = db.execute(text("SELECT c.id,c.record FROM agent_validated_calls c JOIN agent_journal_branches b ON b.id=c.branch_id JOIN agent_node_runs n ON n.id=b.owner_node_id WHERE n.run_id=61 AND c.agent_key='test_planning_route_gap_reviewer' AND c.id>:cursor"), {"cursor": max(checked_ids, default=0)}).mappings().all()
        reader, catalog_cache = TargetedReader(db), {}
        for row in sorted(rows, key=lambda row: row["id"]):
            saved[str(row["id"])] = verify_support(db, decoded(row["record"]), reader, catalog_cache)
        default_packing = previous.get("generation_default_packing")
        accepted = [event for event in events if event["event_type"] == "agent_action_completed" and event["payload"]["tool_key"] == "accept_generation_plan"]
        if accepted and default_packing is None:
            receipt = receipt_record(db, accepted[0]["payload"]["tool_call_id"])
            result = reader.store.get(receipt["result_ref"])["output"]
            result = json.loads(result) if isinstance(result, str) else result
            batches = result["generation"]["batches"]
            default_packing = {"accepted_event_sequence": accepted[0]["sequence"], "batch_count": len(batches),
                "case_budget": sum(item["case_budget"] for item in batches), "layout_version": result["generation"]["layout_version"],
                "manual_layout_actions_before_accept": sum(event["event_type"] == "agent_action_started" and event["payload"]["tool_key"] == "revise_generation_layout"
                    for event in events if event["sequence"] < accepted[0]["sequence"]), "result_ref": receipt["result_ref"]}
            assert default_packing["case_budget"] == 70
        comparisons = compare_milestones(db, observations)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    return {"run_id": 61, "observed_at": datetime.now(timezone.utc).isoformat(), "run": run,
        "elapsed_seconds": run_elapsed,
        "human_wait_seconds": human_wait_seconds, "human_wait_windows": human_wait_windows,
        "active_elapsed_seconds": max(0, run_elapsed - human_wait_seconds),
        "first_pause_usage_baseline": {"requests":95,"attempted_requests":95,"input_tokens":1184343,"output_tokens":49041,"total_tokens":1233384},
        "sdk_timing_coverage_limit": "首次审批暂停时95条SDK model_requests未由runtime持久化，不能由后续诊断补造这些请求的精确在途/本地耗时；审批后仅统计实际保存的后续诊断。",
        "nodes": nodes, "failed_sdk_requests": failures,
        "concurrency_adjustments": [event for event in events if event["event_type"] == "managed_concurrency_adjusted"],
        "stage_comparison": comparisons, "support_receipts": saved,
        "support_decision_count_max": max((value["decision_count"] for value in saved.values()), default=0),
        "generation_default_packing": default_packing,
        "sampled_no_sdk_inflight_seconds_lower_bound": no_inflight_lower_bound(observations),
        "model_requests_sent_by_audit": 0, "business_database_writes": 0,
        "scope": "只读取轻量Run/Node预算投影、行动事件和指定专家输出/规划叶子；不展开完整state。阶段用量来自完成后首次观察快照，非供应商分字段账单。模型准确在途区间等待终态SDK诊断，当前无在途秒数仅保守采样下界。"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch-seconds", type=int, default=0)
    args = parser.parse_args()
    directory = Path(__file__).parent
    output = directory / "cost-progress.json"
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    deadline = time.monotonic() + args.watch_seconds
    last = None
    while True:
        current = capture(directory, previous)
        output.write_text(json.dumps(current, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        latest = current["nodes"][-1]
        signature = (current["run"]["status"], ((latest["observed_projection"] or {}).get("current_action") or {}).get("started_sequence"), len(current["support_receipts"]), len(current["failed_sdk_requests"]))
        if signature != last:
            print(json.dumps({"status": current["run"]["status"], "usage": current["run"]["usage"],
                "active_elapsed_seconds": current["active_elapsed_seconds"], "human_wait_seconds": current["human_wait_seconds"], "action": (latest["observed_projection"] or {}).get("current_action"),
                "support_receipts": len(current["support_receipts"]), "default_packing": current["generation_default_packing"]}, ensure_ascii=False), flush=True)
            last = signature
        previous = current
        if current["run"]["status"] in {"succeeded", "failed", "cancelled", "waiting_approval"} or time.monotonic() >= deadline:
            break
        time.sleep(10)


if __name__ == "__main__":
    main()
