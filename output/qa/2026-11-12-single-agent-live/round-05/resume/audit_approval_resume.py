"""仅比对保存的真实暂停/恢复快照，不连接API、数据库、模型或服务。"""

from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASE = HERE.parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


before_path, after_path = BASE / "final-snapshot.json", HERE / "after-approval.json"
request_path, response_path = HERE / "approval-request.json", HERE / "approval-response.json"
before, after, request, response = [read(path) for path in (before_path, after_path, request_path, response_path)]
oldrun, newrun = before["run"], after["run"]
oldapproval = next(item for item in before["approvals"] if item["id"] == request["approval_id"])
newapproval = next(item for item in after["approvals"] if item["id"] == request["approval_id"])
oldnode = next(item for item in before["nodes"] if item["id"] == oldapproval["node_run_id"])
newnode = next(item for item in after["nodes"] if item["id"] == newapproval["node_run_id"])
oldcp, newcp = [snapshot["expanded_checkpoints"][str(node["id"])]
                for snapshot, node in ((before, oldnode), (after, newnode))]
oldcontext, newcontext = oldrun["run_context"], newrun["run_context"]
expected_ids = {item["id"] for item in oldapproval["request_payload"]["items"]}
expected_answer = request["user_answer"]
answers = request["request"]["conflict_answers"]
confirmations = newcp["state"]["conflict_confirmations"]
oldbudget, newbudget = oldcp["budget"], newcp["budget"]
new_call_keys = set(newcp["validated_agent_calls"]) - set(oldcp["validated_agent_calls"])
new_receipt_keys = set(newcp["receipts"]) - set(oldcp["receipts"])
paused_at = instant(oldapproval["request_payload"]["paused_at"])
wait_delta = newcontext["confirmation_wait_seconds"] - oldcontext.get("confirmation_wait_seconds", 0)
applied_at = paused_at + timedelta(seconds=wait_delta)
deadline_changes = {key: (instant(newcontext[key]) - instant(oldcontext[key])).total_seconds()
                    for key in ("deadline_at", "stage_deadline_at")}
new_events = after["events"][len(before["events"]):]

checks = {
    "formal_api_200_approved": response["http_status"] == 200 and response["body"] == {"approval_id": 13, "status": "approved"},
    "request_has_exact_two_expected_ids": set(answers) == expected_ids and len(expected_ids) == 2,
    "both_answers_are_exact_user_words": all(value == expected_answer for value in answers.values()),
    "persisted_approval_matches_request": newapproval["status"] == "approved" and newapproval["decision_payload"] == {key: request["request"][key] for key in ("reason", "conflict_answers")},
    "original_approval_evidence_unchanged": newapproval["request_payload"] == oldapproval["request_payload"],
    "confirmed_state_keeps_questions_and_exact_resolutions": all(confirmations.get(item["id"]) == {**item, "resolution": expected_answer} for item in oldapproval["request_payload"]["items"]),
    "same_run_owner_project_and_logical_attempt": all(oldrun[key] == newrun[key] for key in ("id", "user_id", "project_id", "parent_run_id")) and all(oldcontext[key] == newcontext[key] for key in ("logical_task_id", "attempt_id", "run_attempt")),
    "same_node_id_and_attempt": all(oldnode[key] == newnode[key] for key in ("id", "run_id", "node_key", "node_type", "attempt", "started_at")),
    "node_and_run_are_actually_running": newrun["status"] == newnode["status"] == "running" and newnode["finished_at"] is None,
    "new_delivery_and_live_claim": oldrun["task_id"] != newrun["task_id"] and oldrun["claim_token_sha256"] is None and bool(newrun["claim_token_sha256"]),
    "raw_input_and_source_not_rewritten": newrun["input_payload"] == oldrun["input_payload"] and newcp["input_hash"] == oldcp["input_hash"] == canonical_hash(newrun["input_payload"]) and newcp["state"]["source"] == oldcp["state"]["source"],
    "same_checkpoint_schema_storage_and_execution_contract": all(oldcp[key] == newcp[key] for key in ("version", "storage", "execution_semantic_hash", "input_ref", "quota_base_usage")) and oldcontext["execution_contract"]["hash"] == newcontext["execution_contract"]["hash"],
    "same_journal_branch_with_monotone_cursors": oldcp["journal_ref"]["branch_id"] == newcp["journal_ref"]["branch_id"] and all(newcp["journal_ref"][key] >= oldcp["journal_ref"][key] for key in ("call_cursor", "receipt_cursor")),
    "budget_and_project_quota_limits_unchanged": oldbudget["limits"] == newbudget["limits"] and oldcontext["execution_limits"] == newcontext["execution_limits"] and oldcontext["quota_usage"] == newcontext["quota_usage"],
    "reported_usage_and_charged_not_reset": all(newbudget[part][key] >= value for part in ("charged", "reported_usage") for key, value in oldbudget[part].items()) and newbudget["completed_requests"] >= oldbudget["completed_requests"],
    "snapshot_run_usage_matches_cumulative_budget": all(newcontext["usage"][key] == newbudget["reported_usage"][key] for key in ("requests", "input_tokens", "output_tokens", "total_tokens")) and newcontext["usage"]["attempted_requests"] == newbudget["charged"]["attempted_requests"],
    "no_unknown_or_pending_created_by_human_pause": oldbudget["pending_requests"] == newbudget["pending_requests"] == oldbudget["interrupted_requests"] == newbudget["interrupted_requests"] == 0,
    "both_deadlines_shift_by_exact_wait": all(abs(value - wait_delta) < 0.000001 for value in deadline_changes.values()),
    "run_and_node_wait_counters_agree": abs(newnode["sdk_state"]["confirmation_wait_seconds"] - oldnode["sdk_state"].get("confirmation_wait_seconds", 0) - wait_delta) < 0.000001,
    "actual_apply_time_is_between_request_and_response": instant(request["requested_at"]) <= applied_at <= instant(response["received_at"]),
    "active_time_remaining_unchanged_at_resume": instant(newcontext["deadline_at"]) - applied_at == instant(oldcontext["deadline_at"]) - paused_at,
    "all_109_successful_professional_records_identical": len(oldcp["validated_agent_calls"]) == 109 and all(newcp["validated_agent_calls"].get(key) == value for key, value in oldcp["validated_agent_calls"].items()),
    "all_24_original_action_receipts_identical": len(oldcp["receipts"]) == 24 and all(newcp["receipts"].get(key) == value for key, value in oldcp["receipts"].items()),
    "old_event_prefix_preserved_and_one_approval_transition": after["events"][:len(before["events"])] == before["events"] and sum(item["event_type"] == "approval_approved" and item["payload"].get("approval_id") == 13 for item in new_events) == 1,
    "new_action_reconciles_authority_after_decision": any(newcp["receipts"][key]["tool_key"] == "reconcile_generation_authority" and newcp["receipts"][key]["started_sequence"] == 25 for key in new_receipt_keys) and oldcp["state_hash"] != newcp["state_hash"] and bool(new_call_keys),
}

baseline = {
    "observed_at": datetime.now(timezone.utc).isoformat(), "scope": "从保存的人工确认前快照提取，不代表当前线上状态", "run_id": oldrun["id"],
    "node_id": oldnode["id"], "node_attempt": oldnode["attempt"], "approval": oldapproval,
    "budget": oldbudget, "clock": {key: oldcontext.get(key) for key in ("deadline_at", "stage_deadline_at", "confirmation_wait_seconds")},
    "checkpoint_header": {key: oldcp[key] for key in ("version", "storage", "input_hash", "state_hash", "journal_ref", "execution_semantic_hash")},
    "receipt_digests": {key: canonical_hash(value) for key, value in oldcp["receipts"].items()},
    "validated_call_digests": {key: canonical_hash(value) for key, value in oldcp["validated_agent_calls"].items()},
}
(HERE / "independent-before-baseline.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

report = {
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "status": "human_pause_resume_verified/generation_still_running" if all(checks.values()) else "human_pause_resume_verification_failed",
    "scope": "只读离线核对真实人工答复、正式API和恢复检查点；不触发新的模型或服务操作。",
    "run_id": 53, "approval_id": 13, "checks": checks, "passed": sum(checks.values()), "total": len(checks),
    "user_answer_verbatim": expected_answer, "answer_item_ids": sorted(expected_ids),
    "before": {"node_id": oldnode["id"], "attempt": oldnode["attempt"], "run_status": oldrun["status"], "node_status": oldnode["status"], "task_id": oldrun["task_id"], "checkpoint_hash": oldcp["state_hash"], "budget": oldbudget},
    "after": {"snapshot_at": after["observed_at"], "node_id": newnode["id"], "attempt": newnode["attempt"], "run_status": newrun["status"], "node_status": newnode["status"], "task_id": newrun["task_id"], "claim_sha256": newrun["claim_token_sha256"], "checkpoint_hash": newcp["state_hash"], "budget": newbudget},
    "human_wait": {"paused_at_utc": paused_at.isoformat(), "actual_apply_at_derived_utc": applied_at.isoformat(), "request_at": request["requested_at"], "response_at": response["received_at"], "excluded_seconds": wait_delta, "excluded_minutes": wait_delta / 60, "before_deadline": oldcontext["deadline_at"], "after_deadline": newcontext["deadline_at"], "both_deadline_deltas_seconds": deadline_changes, "active_seconds_preserved": (instant(oldcontext["deadline_at"]) - paused_at).total_seconds(), "interpretation": "实际等待91分17.658710秒完整从执行deadline排除；不是硬编码90分钟。paused_at为Python UTC时间，未把server默认requested_at展示时间当作UTC计算。"},
    "preserved_evidence": {"old_professional_record_count": len(oldcp["validated_agent_calls"]), "new_professional_record_count": len(newcp["validated_agent_calls"]), "old_action_receipt_count": len(oldcp["receipts"]), "new_action_receipt_count": len(newcp["receipts"]), "old_call_keys_retained": sorted(oldcp["validated_agent_calls"]), "new_calls": {key: newcp["validated_agent_calls"][key]["identity"] for key in sorted(new_call_keys)}, "new_receipts": {key: {field: value for field, value in newcp["receipts"][key].items() if field in ("tool_key", "tool_call_id", "status", "started_sequence", "pre_state_hash", "post_state_hash")} for key in sorted(new_receipt_keys)}, "interpretation": "原109条专业结果和24条动作回执完整原值保留。用户确认合法清理旧权威裁决及下游候选缓存；不把旧缓存当作新口径已通过。恢复已有新的权威复核请求/结果，不宣称旧结果全部被重复采用。"},
    "new_events": new_events,
    "evidence_files": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in (BASE / "pause-baseline.json", before_path, request_path, response_path, after_path)],
    "remaining_unverified": ["新的人工口径最终贯穿规划/生成/独立审查/交付", "本次真实生成最终完成与交付幂等", "人工执行测试及后续修改反馈"],
    "side_effects": {"business_db_access": 0, "api_writes": 0, "model_requests": 0, "backend_changes": 0, "service_control": 0},
    "audit_script_diagnostic": "首次临时提取脚本把receipts误认为list，读取时TypeError后终止，未写业务或审计结果；本脚本按真实dict结构完成提取与逐值核对。",
}
(HERE / "independent-resume-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"status": report["status"], "passed": report["passed"], "total": report["total"],
                  "false_checks": [key for key, value in checks.items() if not value]}, ensure_ascii=False))
