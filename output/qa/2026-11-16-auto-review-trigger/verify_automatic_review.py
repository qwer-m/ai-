"""从真实终态行动及模型请求核对审查来源、用量和口径，无模型或数据库写入。"""

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path


def audit(path):
    snapshot = json.loads(path.read_text(encoding="utf8"))
    requests = {}
    for node in snapshot["nodes"]:
        for request in node["sdk_state"].get("model_requests", []):
            identity = (request["started_at"], request["agent_key"], request.get("action_call_id"))
            requests[identity] = request
    requests = sorted(requests.values(), key=lambda item: datetime.fromisoformat(item["started_at"]))
    receipts = {}
    for checkpoint in snapshot["expanded_checkpoints"].values():
        for identity, receipt in checkpoint["receipts"].items():
            receipts[identity] = receipt
    review_calls = []
    for receipt in receipts.values():
        if receipt["tool_key"] != "request_review":
            continue
        identity = receipt["tool_call_id"]
        origin = ("state" if identity.startswith("platform_action_") else
                  "transport_recovery" if identity.startswith("transport_retry_") else "model")
        child = [item for item in requests if item.get("action_call_id") == identity
                 and item["agent_key"] == "test_generation_reviewer"]
        associated = None
        if origin == "model" and child:
            earlier = [item for item in requests if item["agent_key"] == "test_generation_coordinator"
                       and datetime.fromisoformat(item["finished_at"]) <= datetime.fromisoformat(child[0]["started_at"])]
            if earlier:
                previous = earlier[-1]
                associated = {key: previous[key] for key in ("sequence", "usage", "elapsed_seconds")}
        review_calls.append({"tool_call_id": identity, "origin": origin, "status": receipt["status"],
                             "child_request_count": len(child), "associated_main_request": associated})
    grouped = {}
    for request in requests:
        group = grouped.setdefault(request["agent_key"], {"attempts": 0, "reported_responses": 0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        group["attempts"] += 1
        if request["status"] == "response_received" and request.get("usage_reported", True):
            group["reported_responses"] += 1
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                group[key] += request["usage"][key]
    last = list(snapshot["expanded_checkpoints"].values())[-1]
    state = last["state"]
    final_reviews = [item for item in review_calls if item["status"] == "completed"]
    origins = Counter(item["origin"] for item in final_reviews)
    return {"run_id": snapshot["run"]["id"], "status": snapshot["run"]["status"],
        "new_model_requests": 0, "database_writes": False, "review_calls": review_calls,
        "completed_review_count": len(final_reviews), "automatic_review_count": origins["state"],
        "automatic_recovery_review_count": origins["transport_recovery"],
        "model_requested_review_count": origins["model"],
        "automatic_rereview_count": max(0, origins["state"] - 1),
        "model_usage_by_agent": grouped, "reported_usage": snapshot["run"]["run_context"]["usage"],
        "final_case_count": len(state["cases"]["items"]),
        "final_review_verdict": state["review"]["result"]["verdict"],
        "remaining_issue_count": sum(item["status"] != "resolved" for item in state["open_issues"].values()),
        "approved_rules": [{key: item[key] for key in ("evidence_id", "resolution")}
                           for item in state["approved_decisions"]],
        "approved_decision_records": [{key: value for key, value in item.items() if key != "evidence_id"}
                                      for item in state["approved_decisions"]],
        "final_budget": last["budget"]}


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--snapshot", type=Path, required=True)
parser.add_argument("--baseline-snapshot", type=Path)
parser.add_argument("--output-file", type=Path, required=True)
parser.add_argument("--require-automatic", action="store_true")
args = parser.parse_args()
result = audit(args.snapshot)
if args.baseline_snapshot:
    baseline = audit(args.baseline_snapshot)
    # 引用编号可以随新协议简化；审批原身份、完整原问答及状态证明必须逐项原样保留。
    assert result["approved_decision_records"] == baseline["approved_decision_records"], "已批准口径发生变化"
    result["baseline"] = {key: baseline[key] for key in (
        "run_id", "model_requested_review_count", "automatic_review_count", "review_calls")}
if args.require_automatic:
    assert result["status"] == "success"
    assert result["final_case_count"] == 70 and result["final_review_verdict"] == "approved"
    assert result["remaining_issue_count"] == 0
    assert result["model_requested_review_count"] == 0, "仍有主模型发起的机械审查回合"
    assert all(item["origin"] != "model" for item in result["review_calls"])
    result["automatic_trigger_acceptance"] = True
args.output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({key: result[key] for key in ("run_id", "status", "completed_review_count",
    "automatic_review_count", "automatic_rereview_count", "model_requested_review_count")}, ensure_ascii=False))
