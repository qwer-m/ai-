"""只读核验 Run61 人工恢复、历史保护哈希与新口径提取边界。"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy
from types import SimpleNamespace


BASE = Path(__file__).resolve().parent
helpers = runpy.run_path(str(BASE.parent / "capture_cost_progress.py"))
SessionLocal, TargetedReader = helpers["SessionLocal"], helpers["TargetedReader"]
text, action_state_hash = helpers["text"], helpers["action_state_hash"]
from core.db.model_defs import AgentRun
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sources import persisted_source_snapshot
from modules.agent_platform.test_generation_source_context import _confirmed_decisions


def load(name):
    return json.loads((BASE / name).read_text(encoding="utf-8-sig"))


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def iso(value):
    return datetime.fromisoformat(str(value))


before, request, response = (load(name) for name in
    ("before-approval.json", "approval-request.json", "approval-response.json"))
historical_path = BASE.parents[1] / "historical-runs-before-repairs.json"
historical_before = json.loads(historical_path.read_text(encoding="utf-8-sig"))

with SessionLocal(autoflush=False) as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    run = db.get(AgentRun, 61)
    rc = run.run_context
    node_ids = list(db.execute(text("SELECT id FROM agent_node_runs WHERE run_id=61 ORDER BY id")).scalars())
    node_rows = [dict(db.execute(text("""
        SELECT id,node_key,node_type,status,attempt,started_at,finished_at,
            JSON_EXTRACT(sdk_state,'$.action_checkpoint') checkpoint,
            JSON_EXTRACT(sdk_state,'$.confirmation_wait_seconds') confirmation_wait_seconds,
            JSON_LENGTH(JSON_EXTRACT(sdk_state,'$.error_diagnostic.model_requests')) failed_request_diagnostics,
            JSON_LENGTH(JSON_EXTRACT(sdk_state,'$.model_requests')) saved_request_diagnostics
        FROM agent_node_runs WHERE id=:identity
    """), {"identity": identity}).mappings().one()) for identity in node_ids]
    assert len(node_rows) == 1 and node_rows[0]["id"] == before["node_id"] == 413
    node = node_rows[0]
    checkpoint = decoded(node.pop("checkpoint"))
    node_wait = float(decoded(node["confirmation_wait_seconds"]))
    approval = dict(db.execute(text("SELECT * FROM agent_approvals WHERE id=15")).mappings().one())
    approval_request = decoded(approval["request_payload"])
    approval_decision = decoded(approval["decision_payload"])

    # 行动回执快照使用规范JSON摘要；18个历史Run另有原始SQL摘要，分别保持原口径。
    record_results = {}
    for table, field in (("agent_action_receipts", "receipt_record_hashes"),
                         ("agent_validated_calls", "validated_call_record_hashes")):
        expected = before[field]
        ids = ",".join(str(int(value)) for value in expected)
        current = {str(row.id): action_state_hash(decoded(row.record)) for row in db.execute(text(
            f"SELECT id,record FROM {table} WHERE id IN ({ids})"))}
        mismatch = {identity: {"before": value, "current": current.get(identity)}
                    for identity, value in expected.items() if current.get(identity) != value}
        record_results[field] = {"hash_algorithm": "action_state_hash规范JSON", "count": len(expected), "matched_count": len(expected) - len(mismatch),
                                 "mismatches": mismatch, "current_hashes": current}

    ids = ",".join(str(int(value["id"])) for value in historical_before)
    historical_current = [dict(row) for row in db.execute(text(f"""
        SELECT id,status,SHA2(CAST(input_payload AS CHAR),256) input_hash,
            SHA2(CAST(run_context AS CHAR),256) context_hash,
            SHA2(CAST(output_payload AS CHAR),256) output_hash
        FROM agent_runs WHERE id IN ({ids}) ORDER BY id
    """)).mappings()]
    historical_mismatches = []
    current_by_id = {row["id"]: row for row in historical_current}
    for row in historical_before:
        if row != current_by_id.get(row["id"]):
            historical_mismatches.append({"before": row, "current": current_by_id.get(row["id"])})

    reader = TargetedReader(db)
    confirmations = reader.subtree(checkpoint["state_hash"], ["conflict_confirmations"])
    source_confirmed = reader.subtree(checkpoint["state_hash"], ["source", "confirmed_source_decisions"])
    source_state = reader.subtree(checkpoint["state_hash"], ["source"])
    source_facts = source_state["source_semantics"]["authoritative_facts"]
    fact_ids = {fact["fact_id"] for fact in source_facts}
    source_reuse_check = {"outer_confirmation_count": len(confirmations),
        "source_confirmed_count": len(source_confirmed),
        "new_answer_in_outer": all(confirmations[key]["resolution"] == value
            for key, value in request["request"]["conflict_answers"].items()),
        "approved_forms_in_run61": [row.id for row in db.execute(text(
            "SELECT id FROM agent_approvals WHERE run_id=61 AND status='approved' ORDER BY id"))]}
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
        run_id=run.id, node_key=node["node_key"], run_input=run.input_payload)
    try:
        answers = _confirmed_decisions(context, run, {"source": source_state, "conflict_confirmations": confirmations},
                                      fact_ids, persisted_source_snapshot(run))
        source_reuse_check.update(current_extractor_status="accepted", count=len(answers), answers=answers)
    except ValueError as error:
        source_reuse_check.update(current_extractor_status="rejected", reason=str(error))

    approved = approval_decision["conflict_answers"]
    same_answers = approved == request["request"]["conflict_answers"]
    paused_at = iso(approval_request["paused_at"])
    wait_seconds = float(rc["confirmation_wait_seconds"])
    old_deadline, new_deadline = iso(before["run_context"]["deadline_at"]), iso(rc["deadline_at"])
    deadline_shift = (new_deadline - old_deadline).total_seconds()
    old_stage, new_stage = iso(before["run_context"]["stage_deadline_at"]), iso(rc["stage_deadline_at"])
    decided_at = approval["decided_at"]
    decided_elapsed = (decided_at - paused_at).total_seconds()
    response_at = iso(response["at"]).replace(tzinfo=None)
    budget_before, budget_now = before["checkpoint"]["budget"], checkpoint["budget"]
    post_approval_requests = int(node["failed_request_diagnostics"] or node["saved_request_diagnostics"] or 0)
    verified = {
        "original_input_unchanged": action_state_hash(run.input_payload) == before["input_sha256"]
            == checkpoint["input_hash"] == checkpoint["input_ref"]["sha256"],
        "frozen_contract_unchanged": rc["execution_contract"] == before["run_context"]["execution_contract"],
        "execution_limits_unchanged": rc["execution_limits"] == before["run_context"]["execution_limits"],
        "checkpoint_limits_unchanged": budget_now["limits"] == budget_before["limits"],
        "original_approval_request_unchanged": approval_request == before["approval_request"],
        "answer_verbatim_unchanged": same_answers and set(approved.values()) == {request["user_answer_verbatim"]},
        "formal_approval_response_ok": response["http_status"] == 200
            and response["body"] == {"approval_id": 15, "status": "approved"},
        "same_single_node": len(node_rows) == 1 and approval["node_run_id"] == 413,
        "same_branch": checkpoint["journal_ref"]["branch_id"] == before["checkpoint"]["journal_ref"]["branch_id"],
        "same_attempt": node["attempt"] == 1 and rc["attempt_id"] == before["run_context"]["attempt_id"],
        "budget_counter_continues": budget_now["reported_usage"]["requests"] ==
            budget_before["reported_usage"]["requests"] + post_approval_requests,
        "used_budget_not_reset": all(budget_now["charged"][key] >= value
            for key, value in budget_before["charged"].items()),
        "quota_baseline_not_reset": checkpoint["quota_base_usage"] == before["checkpoint"]["quota_base_usage"],
        "old_receipts_unchanged": not any(row["mismatches"] for row in record_results.values()),
        "deadline_extended_by_stored_wait": abs(deadline_shift - wait_seconds) < 1e-6
            and abs((new_stage - old_stage).total_seconds() - wait_seconds) < 1e-6
            and node_wait == wait_seconds,
        "18_historical_runs_unchanged": len(historical_current) == len(historical_before) == 18
            and not historical_mismatches,
    }
    report = {"run_id": 61, "read_at_utc": datetime.now(timezone.utc).isoformat(),
        "terminal_status": run.status, "passed": all(verified.values()), "checks": verified,
        "business_database_writes": 0, "paid_model_requests": 0,
        "source_files": [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in (BASE / "before-approval.json", BASE / "approval-request.json",
                         BASE / "approval-response.json", historical_path)],
        "node": node,
        "input_sha256": action_state_hash(run.input_payload),
        "frozen_contract_sha256": action_state_hash(rc["execution_contract"]),
        "execution_limits": rc["execution_limits"],
        "approval": {"id": 15, "status": approval["status"], "node_id": approval["node_run_id"],
            "request_state_hash": approval_request["state_hash"], "user_answer_verbatim": request["user_answer_verbatim"],
            "saved_answers": approved, "decided_at_utc": str(decided_at)},
        "budget": {"before": budget_before, "terminal": budget_now,
            "actual_terminal_post_approval_request_diagnostics": post_approval_requests,
            "identity": f"{budget_before['reported_usage']['requests']} + {post_approval_requests} = {budget_now['reported_usage']['requests']}",
            "missing_95_fine_timing_not_reconstructed": True},
        "wait": {"paused_at_utc": paused_at.isoformat(), "old_deadline_utc": old_deadline.isoformat(),
            "new_deadline_utc": new_deadline.isoformat(), "deadline_extension_seconds": deadline_shift,
            "stored_confirmation_wait_seconds": wait_seconds,
            "paused_to_decided_at_seconds": decided_elapsed,
            "paused_to_client_response_capture_seconds": (response_at - paused_at).total_seconds(),
            "approval_processing_time_included_in_execution_seconds": decided_elapsed - wait_seconds,
            "explanation": "截止时间只补回apply_conflict_decision入口计算的真实人工等待，审批处理和HTTP往返仍计执行时间；未重置为新一整轮时限",
            "requested_2909_704659_provenance": "实际为paused_at到秒精度approval.decided_at，包含审批处理耗时，不能声称全部已从执行预算扣除"},
        "record_hashes": record_results,
        "historical_runs": {"hash_algorithm": "MySQL SHA2(CAST(JSON AS CHAR),256)，与原始baseline完全相同",
            "expected_count": len(historical_before), "current_count": len(historical_current),
            "matched_count": len(historical_before) - len(historical_mismatches),
            "mismatches": historical_mismatches, "current": historical_current},
        "source_context_reuse_current_extractor": source_reuse_check,
        "boundaries": "只读当前事实；恢复完整性通过不表示Run61业务生成成功。来源上下文提取失败单列，未更改或迁移旧状态。"}
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()

(BASE / "integrity-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(json.dumps({"passed": report["passed"], "checks": verified,
    "wait": report["wait"], "budget": report["budget"]["identity"],
    "source_context": source_reuse_check}, ensure_ascii=False, indent=2, default=str))
