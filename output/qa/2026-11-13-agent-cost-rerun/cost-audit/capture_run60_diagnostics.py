"""只读归档Run60终态，并从真实失败及恢复提交提取永久回归样本。"""

from collections import Counter
from copy import deepcopy
import gzip
import json
from pathlib import Path

from prepare_read_path_draft import ROOT
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.checkpoint_storage import ArtifactStore
from modules.agent_platform.planning_dependencies import ownership_batches


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def main():
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = dict(db.execute(text("SELECT id,user_id,project_id,status,started_at,finished_at,input_payload,run_context FROM agent_runs WHERE id=60")).mappings().one())
        run["input_payload"] = decoded(run["input_payload"])
        run["run_context"] = decoded(run["run_context"])
        nodes = [dict(row) for row in db.execute(text("SELECT id,attempt,status,started_at,finished_at,error_message,sdk_state FROM agent_node_runs WHERE run_id=60")).mappings()]
        nodes.sort(key=lambda row: row["attempt"])
        diagnostics, all_requests = [], []
        for node in nodes:
            sdk = node["sdk_state"] = decoded(node["sdk_state"])
            diag = sdk.get("error_diagnostic") or {}
            requests = diag.get("model_requests") or sdk.get("model_requests") or []
            all_requests.extend(requests)
            failed = [request for request in requests if request.get("status") != "response_received"]
            diagnostics.append({key: node[key] for key in ("id", "attempt", "status", "started_at", "finished_at", "error_message")} | {
                "exception_chain": diag.get("exception_chain"), "request_records": len(requests),
                "reported_request_count": sum(request.get("status") == "response_received" for request in requests),
                "failed_requests": failed, "final_budget": sdk["action_checkpoint"]["budget"]})
        final_budget = nodes[-1]["sdk_state"]["action_checkpoint"]["budget"]
        failure_kinds = Counter(next((item["error_type"] for item in request.get("exception_chain", [])
            if item.get("error_type") in {"RateLimitError", "CancelledError"}), "other")
            for request in all_requests if request.get("status") != "response_received")
        owner = next(node for node in nodes if node["id"] == 406)
        failed_tool = next(call for call in owner["sdk_state"]["tool_calls"]
            if call.get("status") == "failed" and call["tool_key"] == "agent:test_planning_scope_router")
        store = ArtifactStore(db, user_id=1, project_id=8)
        state_ref = owner["sdk_state"]["action_checkpoint"]["state_ref"]
        state = store.get(state_ref)
        requests = ownership_batches(state["planning"]["ownership_inputs"])
        candidates = [{**item, "_user_confirmations": deepcopy(state["conflict_confirmations"])} for item in requests]
        matched = [(index, item) for index, item in enumerate(candidates) if action_state_hash(item) == failed_tool["input_hash"]]
        assert len(matched) == 1
        index, request = matched[0]
        restored = db.execute(text("SELECT c.id,c.record FROM agent_validated_calls c JOIN agent_journal_branches b ON b.id=c.branch_id JOIN agent_node_runs n ON n.id=b.owner_node_id WHERE n.run_id=60 AND c.agent_key='test_planning_scope_router' AND JSON_UNQUOTE(JSON_EXTRACT(c.record,'$.identity.input_hash'))=:identity"), {"identity": failed_tool["input_hash"]}).mappings().all()
        assert len(restored) == 1
        restored_record = decoded(restored[0]["record"])
        corrected_raw = store.get(restored_record["raw_output_ref"])
        corrected_normalized = store.get(restored_record["output_ref"])
        definition = next(agent for agent in run["run_context"]["execution_contract"]["agents"] if agent["key"] == "test_planning_scope_router")
        failed_candidate = failed_tool["submissions"][0]["arguments"]
        expected_ids = {fact["fact_id"] for scope in request["scopes"] for fact in scope["facts"]}
        actual_ids = {assignment["fact_id"] for route in failed_candidate["routes"] for assignment in route["assignments"]}
        regression = {"provenance": {"run_id": 60, "node_id": 406, "agent_key": "test_planning_scope_router",
            "batch_index": index, "source_state_sha256": state_ref["sha256"], "input_hash": failed_tool["input_hash"],
            "failed_submission_call_id": failed_tool["submissions"][0]["tool_call_id"],
            "actual_corrected_validated_call_id": restored[0]["id"], "corrected_call_record": restored_record,
            "scope_id": failed_candidate["routes"][0]["scope_id"],
            "missing_fact_ids": sorted(expected_ids - actual_ids), "unexpected_fact_ids": sorted(actual_ids - expected_ids),
            "model_requests_sent_by_extraction": 0, "database_business_writes": 0},
            "run_metadata": {key: run[key] for key in ("id", "user_id", "project_id")},
            "input_payload": request, "agent_definition": definition,
            "failed_candidate": failed_candidate, "corrected_raw_candidate": corrected_raw,
            "corrected_normalized_output": corrected_normalized}
        regression["data_sha256"] = action_state_hash(regression)
        fixture = ROOT / "backend/tests/agent_platform/fixtures/run60_ownership_local_feedback_real.json.gz"
        fixture.write_bytes(gzip.compress(json.dumps(regression, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    summary = {"run_id": 60, "status": run["status"], "case_count": 0,
        "started_at": run["started_at"], "finished_at": run["finished_at"],
        "elapsed_seconds": (run["finished_at"] - run["started_at"]).total_seconds(),
        "reported_usage": run["run_context"]["usage"], "final_budget": final_budget,
        "final_checkpoint_projection": nodes[-1]["sdk_state"]["action_checkpoint"]["projection"],
        "failed_sdk_request_types": dict(failure_kinds), "nodes": diagnostics,
        "unknown_reservation_retained_above_reported": {key: final_budget["charged"][key] - final_budget["reported_usage"][key] for key in ("input_tokens", "output_tokens", "total_tokens")},
        "routing_content_failure": regression["provenance"],
        "failure_boundary": "业务输出普通ValueError被包装成OutputPostprocessingError，未落入AutonomousToolLoop的OutputValidationError反馈分支，TaskGroup取消4个兄弟；单项源头已改为明确输出差异类型。",
        "provider_429_reason": "仅持久化HTTP429、154字节body指纹，未存JSON错误码或Retry-After；无法证实RPM/TPM或并发限制类型。",
        "model_requests_sent_by_audit": 0, "business_database_writes": 0,
        "limits": "本轮经正式API主动取消用于修复，非成功交付；113次尝试含9次未知实耗保守预留，104次报告量才是已知模型用量。第四节点取消前的1次根请求无最终request明细，Run和预算总账保留该已报告响应。"}
    output = ROOT / "output/qa/2026-11-13-agent-cost-rerun/round-01/diagnostic-summary.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "fixture": str(fixture), "failure_kinds": dict(failure_kinds),
        "routing": regression["provenance"], "final_budget": final_budget}, ensure_ascii=False))


if __name__ == "__main__":
    main()
