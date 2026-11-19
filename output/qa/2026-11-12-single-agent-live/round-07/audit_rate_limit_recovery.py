"""只读投影Run55的重试账本/不可变记录，不展开领域状态或调用业务接口。"""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select, text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform import checkpoint_storage as storage


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


with SessionLocal(autoflush=False) as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    run = db.execute(select(
        AgentRun.id, AgentRun.user_id, AgentRun.project_id, AgentRun.status,
        AgentRun.run_context["usage"], AgentRun.run_context["execution_limits"],
        AgentRun.run_context["logical_task_id"], AgentRun.run_context["attempt_id"],
        AgentRun.task_id,
    ).where(AgentRun.id == 55)).one()
    projected = db.execute(select(
        AgentNodeRun.id, AgentNodeRun.run_id, AgentNodeRun.node_key, AgentNodeRun.node_type,
        AgentNodeRun.status, AgentNodeRun.attempt, AgentNodeRun.input_payload,
        AgentNodeRun.sdk_state["action_checkpoint"], AgentNodeRun.sdk_state["error_diagnostic"],
        AgentNodeRun.sdk_state["last_failure_kind"], AgentNodeRun.sdk_state["transient_failure_count"],
        AgentNodeRun.sdk_state["retry_exhausted"],
    ).where(AgentNodeRun.run_id == 55).order_by(AgentNodeRun.id)).all()
    tool_rows = db.execute(text("""
        SELECT n.id node_id, jt.ordinal, jt.tool_key, jt.status,
               jt.replayed, jt.model_requests_sent, jt.input_hash, jt.source_parent_tool_call_id
        FROM agent_node_runs n
        JOIN JSON_TABLE(n.sdk_state, '$.tool_calls[*]' COLUMNS (
            ordinal FOR ORDINALITY,
            tool_key VARCHAR(160) PATH '$.tool_key' NULL ON EMPTY,
            status VARCHAR(40) PATH '$.status' NULL ON EMPTY,
            replayed INT PATH '$.replayed' NULL ON EMPTY,
            model_requests_sent INT PATH '$.model_requests_sent' NULL ON EMPTY,
            input_hash VARCHAR(64) PATH '$.input_hash' NULL ON EMPTY,
            source_parent_tool_call_id VARCHAR(160) PATH '$.source_parent_tool_call_id' NULL ON EMPTY
        )) jt
        WHERE n.run_id=55 ORDER BY n.id,jt.ordinal
    """)).mappings().all()
    nodes = []
    for row in projected:
        identifier, run_id, node_key, node_type, status, attempt, packed_input, checkpoint, diagnostic, failure_kind, transient, exhausted = row
        checkpoint, diagnostic = decoded(checkpoint), decoded(diagnostic) or {}
        node = SimpleNamespace(id=identifier, run_id=run_id, input_payload=decoded(packed_input),
                               sdk_state={"action_checkpoint": checkpoint})
        checkpoint = storage.compact_checkpoint(node)
        calls = storage.call_records(db, node, checkpoint)
        receipts = storage.receipt_records(db, node, checkpoint)
        nodes.append({
            "id": identifier, "attempt": attempt, "status": status,
            "failure_kind": decoded(failure_kind), "transient_failure_count": decoded(transient),
            "retry_exhausted": decoded(exhausted), "checkpoint": checkpoint,
            "requests": diagnostic.get("model_requests", []),
            "batch_progress": diagnostic.get("batch_progress"),
            "tool_calls": [dict(item) for item in tool_rows if item["node_id"] == identifier],
            "validated_calls": {key: {"record_sha256": digest(value), "identity": value["identity"],
                                       "parent_tool_call_id": value["parent_tool_call_id"],
                                       "raw_output_hash": value["raw_output_hash"], "output_hash": value["output_hash"]}
                                for key, value in calls.items()},
            "receipt_headers": {key: {field: deepcopy(value[field]) for field in (
                "tool_key", "status", "pre_state_hash", "post_state_hash", "started_sequence", "error_type", "arguments_hash",
            ) if field in value} for key, value in receipts.items()},
        })
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()

capture = {
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "run": {"id": run[0], "user_id": run[1], "project_id": run[2], "status": run[3],
            "usage": decoded(run[4]), "execution_limits": decoded(run[5]),
            "logical_task_id": decoded(run[6]), "attempt_id": decoded(run[7]), "task_id": run[8]},
    "nodes": nodes, "mysql_transaction": "READ ONLY", "domain_state_hydrations": 0,
    "model_requests_sent_by_auditor": 0, "business_writes": 0,
}
(HERE / "rate-limit-recovery-capture.json").write_text(json.dumps(capture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

old, middle, current = nodes[:3]
budgets = [item["checkpoint"]["budget"] for item in nodes[:3]]
unknowns = []
for item in nodes[:2]:
    for request in item["requests"]:
        if request["status"] != "response_received":
            unknowns.append({"node_id": item["id"], **request})
unknown_reservation = {
    "attempted_requests": len(unknowns),
    "input_tokens": sum(item["reserved_input_tokens"] for item in unknowns),
    "output_tokens": sum(item["reserved_output_tokens"] for item in unknowns),
}
unknown_reservation["total_tokens"] = unknown_reservation["input_tokens"] + unknown_reservation["output_tokens"]
route_action = next(key for key, value in old["receipt_headers"].items()
                    if value["tool_key"] == "route_planning_scopes" and value["status"] == "failed")
review_action = next(key for key, value in middle["receipt_headers"].items()
                     if value["tool_key"] == "review_planning_route_gaps" and value["status"] == "failed")
route_calls = {key: value for key, value in old["validated_calls"].items() if value["parent_tool_call_id"] == route_action}
review_calls = {key: value for key, value in middle["validated_calls"].items() if value["parent_tool_call_id"] == review_action}
route_replays = [item for item in middle["tool_calls"] if item["replayed"] == 1
                 and item["source_parent_tool_call_id"] == route_action]
review_replays = [item for item in current["tool_calls"] if item["replayed"] == 1
                  and item["source_parent_tool_call_id"] == review_action]
completed_reviews = [key for key, value in current["receipt_headers"].items()
                     if key not in middle["receipt_headers"] and value["tool_key"] == "review_planning_route_gaps"
                     and value["status"] == "completed"]
new_keys = set(current["validated_calls"]) - set(middle["validated_calls"])
review_completion_evidence = {
    "completed_recovery_review_actions": completed_reviews,
    "same_original_arguments": bool(completed_reviews) and all(
        current["receipt_headers"][key]["arguments_hash"] == middle["receipt_headers"][review_action]["arguments_hash"]
        for key in completed_reviews),
    "new_saved_professional_records_per_completed_action": {
        key: [call for call in new_keys if current["validated_calls"][call]["parent_tool_call_id"] == key]
        for key in completed_reviews},
    "interpretation": "完整参数摘要相同的已完成恢复行动，结合保留的原28调用和实际新增5调用可核对复用；不伪造尚未持久化的SDK replay标记。",
}
missing_response_statuses = Counter((entry.get("error_type"), entry.get("http_status"))
                                    for request in unknowns for entry in request.get("exception_chain", []))
checks = {
    "same_run_has_three_sequential_node_attempts": [item["attempt"] for item in nodes[:3]] == [1, 2, 3],
    "two_rate_limit_failures_are_retryable_under_existing_policy": all(item["failure_kind"] == "rate_limit" and not item["retry_exhausted"] for item in nodes[:2]) and [item["transient_failure_count"] for item in nodes[:3]] == [1, 2, 2],
    "limits_preserved_across_retries": all(item["limits"] == budgets[0]["limits"] for item in budgets),
    "charged_attempts_and_reported_usage_monotone": all(budgets[index + 1]["charged"]["attempted_requests"] >= budgets[index]["charged"]["attempted_requests"] and all(budgets[index + 1]["reported_usage"][key] >= value for key, value in budgets[index]["reported_usage"].items()) for index in range(2)),
    "route16_success_records_preserved": len(route_calls) == 16 and all(middle["validated_calls"].get(key) == value for key, value in route_calls.items()),
    "route16_direct_replayed_true_zero_model_records": len(route_replays) == 16 and all(item["model_requests_sent"] == 0 for item in route_replays) and {item["input_hash"] for item in route_replays} == {value["identity"]["input_hash"] for value in route_calls.values()},
    "review28_success_records_inherited_to_attempt3": len(review_calls) == 28 and all(current["validated_calls"].get(key) == value for key, value in review_calls.items()),
    "all_prior_success_records_preserved": all(current["validated_calls"].get(key) == value for key, value in middle["validated_calls"].items()),
    "six_missing_usage_requests_are_all_explicit429": len(unknowns) == 6 and all(any(entry.get("error_type") == "RateLimitError" and entry.get("http_status") == 429 for entry in item.get("exception_chain", [])) for item in unknowns),
    "no_sibling_cancellation_in_failed_request_evidence": all(entry.get("error_type") != "CancelledError" for request in unknowns for entry in request.get("exception_chain", [])),
    "six_unknown_upper_reservations_not_refunded": budgets[1]["interrupted_requests"] == budgets[2]["interrupted_requests"] == 6 and all(budgets[index]["charged"][key] - budgets[index]["reported_usage"][key] >= unknown_reservation[key] for index in (1, 2) for key in ("input_tokens", "output_tokens", "total_tokens")),
    "route_and_review_batches_kept_all_other_work": len(old["batch_progress"]["validated_indexes"]) == 16 and old["batch_progress"]["not_started_indexes"] == [] and len(middle["batch_progress"]["validated_indexes"]) == 28 and middle["batch_progress"]["not_started_indexes"] == [],
}
report = {
    "observed_at": capture["observed_at"], "run_id": 55,
    "status": f"bounded_recovery_audit_passed/{capture['run']['status']}" if all(checks.values()) else "bounded_recovery_audit_has_failed_checks",
    "checks": checks, "passed": sum(checks.values()), "total": len(checks),
    "run_state_at_capture": capture["run"]["status"], "node_states": [{key: item[key] for key in ("id", "attempt", "status", "failure_kind", "transient_failure_count", "retry_exhausted")} for item in nodes],
    "budget_snapshots": {str(item["id"]): item["checkpoint"]["budget"] for item in nodes},
    "unknown_reservations": unknown_reservation,
    "rate_limit_requests": [{key: value for key, value in item.items() if key in ("node_id", "sequence", "agent_key", "action_tool", "action_call_id", "started_at", "finished_at", "reserved_input_tokens", "reserved_output_tokens", "exception_chain", "status")} for item in unknowns],
    "rate_limit_counts_by_failed_node": dict(Counter(str(item["node_id"]) for item in unknowns)),
    "failure_status_counts": [{"error_type": key[0], "http_status": key[1], "count": value} for key, value in missing_response_statuses.items()],
    "route_reuse": {"original_failed_action": route_action, "saved_keys": sorted(route_calls), "direct_sdk_replays": route_replays, "interpretation": "原16已验收专业结果直接replayed:true且model_requests_sent=0；同一业务行动由新节点/主协调者继续，非伪造新模型请求。"},
    "review_reuse": {"original_failed_action": review_action, "saved_keys": sorted(review_calls), "direct_sdk_replay_count_at_capture": len(review_replays), "direct_sdk_replays": review_replays,
                     "completed_action_evidence": review_completion_evidence,
                     "interpretation": "28个原已验收结果的完整不可变记录已继承到attempt3；若运行中节点尚未保存SDK tool_calls，不能把继承记录伪称已观察到28条replayed:true。当前完成范围以实时projection/后续终态诊断为准。"},
    "http429_budget_semantics": {"verdict": "符合现有保守账本契约；HTTP拒绝明确，不等于已收到权威零消费用量回执。当前interrupted统计所有已预留但没有ModelResponse.usage的请求，429因此保留上界；不把它称作已实际消费，也不自行退款。",
        "evidence": "6条均是明确HTTP429，不是模型结果响应，也不是兄弟CancelledError。charged=已报告实耗+6条未结算上界+可能仍在途上界；reported_usage不含这些429。",
        "precision_gap": "interrupted_requests命名合并了known HTTP rejection without usage与其他未知结算；这是观测粒度局限，当前证据未证明数学计费错误或实际供应商费用。",
        "safe_source_suggestion_if_product_requires_separate_rejections": [
            {"path": "backend/modules/agent_platform/sdk_adapter.py", "line": 2539, "suggestion": "失败结算入口可记录typed failure_kind/http_status/usage_receipt，而不是丢掉429已明确拒绝这个事实；不得只凭异常字符串退款。"},
            {"path": "backend/modules/agent_platform/autonomous_budget.py", "line": 313, "suggestion": "若以后引入rejected_requests分类，保留累计attempt并继续保留未被可信用量回执确认的预留；只有提供方契约/权威usage确认零消费才可按统一结算协议释放Token。"},
        ], "backend_changed": False},
    "retry_path_explanation": "retry_policy把RateLimitError/429归rate_limit并保留transient计数；普通批次收敛所有独立工作。SDK只对APITimeoutError/APIConnectionError构造无新主决策的transport_retry行动，429进入节点重试后由当前协调者在原state/原预算选择下一行动，再由专业缓存按完整身份复用。",
    "evidence": {"capture_file": str(HERE / "rate-limit-recovery-capture.json"), "capture_sha256": hashlib.sha256((HERE / "rate-limit-recovery-capture.json").read_bytes()).hexdigest(), "large_domain_state_hydrations": 0},
    "remaining_unverified": ["当前付费运行最终结果", "运行中Node400尚未持久化的SDK replay标记（若缺）", "供应商对429是否收费或报告零消费的独立账单"],
    "side_effects": {"mysql_transaction": "READ ONLY", "business_writes": 0, "backend_py_changes": 0, "model_requests": 0, "api_writes": 0, "service_control": 0, "new_runs": 0},
}
(HERE / "rate-limit-recovery-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"passed": report["passed"], "total": report["total"], "false_checks": [key for key, value in checks.items() if not value],
                  "run_status": capture["run"]["status"], "review_direct_replay_count": len(review_replays)}, ensure_ascii=False))
