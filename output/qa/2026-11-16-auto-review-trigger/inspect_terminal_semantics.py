"""终态文件的有限范围独立复核素材，不伪造语义审查或模型回执。"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--snapshot", type=Path, required=True)
parser.add_argument("--baseline", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
raw = args.snapshot.read_bytes()
saved = json.loads(raw)
state = list(saved["expanded_checkpoints"].values())[-1]["state"]
old = json.loads(args.baseline.read_text(encoding="utf8"))
prior = list(old["expanded_checkpoints"].values())[-1]["state"]


def clock(value):
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone(timedelta(hours=8)))


events = saved["events"]
requests = {(row["started_at"], row["agent_key"], row.get("action_call_id")): row
            for node in saved["nodes"] for row in node["sdk_state"].get("model_requests", [])}
transitions = []
for event in events:
    payload = event["payload"]
    if event["event_type"] != "agent_action_started" or payload.get("tool_key") != "request_review":
        continue
    earlier = [row for row in events if row["sequence"] < event["sequence"]
               and row["event_type"] == "agent_action_completed"]
    previous = earlier[-1] if earlier else None
    child = [row for row in requests.values() if row.get("action_call_id") == payload["tool_call_id"]
             and row["agent_key"] == "test_generation_reviewer"]
    gap_main = []
    if previous and child:
        lower, upper = clock(previous["created_at"]), min(clock(row["started_at"]) for row in child)
        gap_main = [{key: row[key] for key in ("sequence", "started_at", "usage")}
            for row in requests.values() if row["agent_key"] == "test_generation_coordinator"
            and lower <= clock(row["started_at"]) < upper]
    transitions.append({"tool_call_id": payload["tool_call_id"],
        "automatic": payload["tool_call_id"].startswith("platform_action_"),
        "review_started_at": event["created_at"],
        "preceding_action": previous["payload"]["tool_key"] if previous else None,
        "preceding_action_completed_at": previous["created_at"] if previous else None,
        "state_hash_matches_preceding_action": bool(previous and
            previous["payload"]["post_state_hash"] == payload["pre_state_hash"]),
        "intervening_main_requests": gap_main})

checks = {row["case_id"]: row for row in state["review"]["result"]["case_checks"]}
selected = {"quantity": [], "initialization": []}
for identity, record in state["cases"]["items"].items():
    content = record["content"]
    refs = {item["evidence_id"] for item in content["source_refs"]}
    body = json.dumps({key: content[key] for key in ("title", "preconditions", "test_input", "steps")},
                      ensure_ascii=False)
    categories = []
    if "EV-0016" in refs and any(word in body for word in ("数量", "词数")):
        categories.append("quantity")
    if "EV-0004" in refs and "记录" in body and any(word in body for word in ("教材", "册次")):
        categories.append("initialization")
    for category in categories:
        selected[category].append({"case_id": identity, "version": record["version"],
            "content": {key: value for key, value in content.items() if key != "source_refs"},
            "source_evidence_ids": sorted(refs), "final_case_check": checks[identity]})
approvals = lambda value: [{"evidence_id": item["evidence_id"], "resolution": item["resolution"]}
                           for item in value["approved_decisions"]]
assert approvals(state) == approvals(prior)
result = {"run_id": saved["run"]["id"], "status": saved["run"]["status"],
    "source_snapshot_sha256": hashlib.sha256(raw).hexdigest(), "new_model_requests": 0,
    "database_reads": False, "database_writes": False,
    "review_transitions": transitions, "approved_rules_unchanged": True,
    "approved_rule_count": len(approvals(state)), "case_count": len(state["cases"]["items"]),
    "review_verdict": state["review"]["result"]["verdict"],
    "remaining_issues": [item["issue_id"] for item in state["open_issues"].values()
                         if item["status"] != "resolved"],
    "selected_semantic_evidence": selected,
    "source_checks": [item for item in state["review"]["result"]["source_checks"]
                      if item["evidence_id"] in {"EV-0004", "EV-0016", "EV-0023"}],
    "semantic_conclusion": "仅提供真实素材，需工程复核后下结论，不按关键词命中宣称业务正确。"}
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({"run_id": result["run_id"], "status": result["status"],
    "review_transition_count": len(transitions), "selected_counts": {key: len(value) for key, value in selected.items()}},
    ensure_ascii=False))
