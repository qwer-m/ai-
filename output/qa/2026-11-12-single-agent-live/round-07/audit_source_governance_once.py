"""Run55 仅抓取一次完整领域状态，随后全部使用本地证据核对。"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select, text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform import checkpoint_storage as storage
from modules.agent_platform import test_generation_semantics as semantics
from modules.agent_platform.action_journal import action_state_hash

FOLDER = Path(__file__).resolve().parent
CAPTURE = FOLDER / "source-governance-state.json"


def decode(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def capture_once():
    if CAPTURE.exists():
        return json.loads(CAPTURE.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        row = db.execute(select(AgentNodeRun.id, AgentNodeRun.run_id, AgentNodeRun.input_payload,
            func.json_extract(AgentNodeRun.sdk_state, "$.action_checkpoint").label("checkpoint"),
        ).where(AgentNodeRun.run_id == 55).order_by(AgentNodeRun.id.desc()).limit(1)).one()
        node = SimpleNamespace(id=row.id, run_id=row.run_id, input_payload=row.input_payload,
                               sdk_state={"action_checkpoint": decode(row.checkpoint)})
        checkpoint = storage.compact_checkpoint(node)
        assert "draft_version" in checkpoint["projection"]["versions"], "规划尚未初始化，禁止提前展开后再重复抓取"
        # 本脚本唯一的完整状态读取；后续不再沿产物树读取历史大状态。
        state = storage.hydrate_checkpoint_state(db, node)
        assert action_state_hash(state) == checkpoint["state_hash"]
        receipts = storage.receipt_records(db, node, checkpoint)
        calls = storage.call_records(db, node, checkpoint)
        gov = next(receipt for receipt in receipts.values() if receipt["tool_key"] == "govern_source")
        assert gov["status"] == "completed"
        call = next(call for call in calls.values() if call["parent_tool_call_id"] == gov["tool_call_id"]
                    and call["identity"]["agent_key"] == "test_authority_reconciliation_reviewer")
        call = storage.unpack_record(storage.store_for_node(db, node), call, ("raw_output", "output"))
        run_row = db.execute(select(AgentRun.status, AgentRun.project_id, AgentRun.user_id,
            func.json_extract(AgentRun.run_context, "$.runtime_registry_signature"),
            func.json_extract(AgentRun.run_context, "$.execution_contract.hash"),
        ).where(AgentRun.id == 55)).one()
        result = {"run_id": 55, "node_id": node.id, "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": run_row[0], "project_id": run_row[1], "user_id": run_row[2],
            "runtime_registry_signature": decode(run_row[3]), "execution_contract_hash": decode(run_row[4]),
            "checkpoint": checkpoint, "state": state, "governance_receipt": gov, "governance_call": call,
            "action_headers": [{key: receipt[key] for key in ("tool_key", "status", "started_sequence")}
                               for receipt in sorted(receipts.values(), key=lambda receipt: receipt["started_sequence"])]}
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    CAPTURE.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return result


def audit(captured):
    old = json.loads((ROOT / "backend/tests/agent_platform/fixtures/run53_confirmed_baseline_support_real.json").read_text(encoding="utf-8"))
    state = captured["state"]
    source, planning = state["source"], state["planning"]
    assert planning is not None
    baseline = source["authority_baseline"]
    original = old["accepted_authority"]["result"]
    assert source["source_semantics"] == old["source_semantics"]
    assert baseline["source_semantics_sha256"] == action_state_hash(old["source_semantics"])
    assert baseline["result"] == original
    assert action_state_hash(baseline["result"]) != action_state_hash(old["post_authority_result"])
    current_answers = state["conflict_confirmations"]
    previous_answers = old["approved_confirmation_context"]
    assert set(current_answers) == set(previous_answers)
    assert all({key: current_answers[identity][key] for key in previous} == previous
               for identity, previous in previous_answers.items())
    approval = old["approved_approval"]
    for answer in current_answers.values():
        assert answer["approval_id"] == approval["id"] == 13
        assert answer["origin_run_id"] == old["source_run_id"] == 53
        assert answer["origin_node_id"] == approval["node_run_id"]
        assert answer["approval_state_hash"] == approval["request_payload"]["state_hash"]
        assert answer["decided_by_user_id"] == approval["decided_by_user_id"]
        assert datetime.fromisoformat(answer["decided_at"]) == datetime.fromisoformat(approval["decided_at"])
        assert answer["source_snapshot"] == old["source_ref"]
    raw = {fact["fact_id"]: fact for fact in source["source_semantics"]["authoritative_facts"]}
    accepted = {fact["fact_id"]: fact for fact in original["authoritative_facts"]}
    polluted = {fact["fact_id"]: fact for fact in old["post_authority_result"]["authoritative_facts"]}
    result = source["source_authority"]["result"]
    actual = {fact["fact_id"]: fact for fact in result["authoritative_facts"]}
    assert len(raw) == len(accepted) == len(actual) == 332
    assert set(raw) == set(accepted) == set(actual)
    governance_fields = {"status", "value_policy", "governed_values", "governed_by"}
    assert all({key: value for key, value in fact.items() if key not in governance_fields}
               == {key: value for key, value in raw[identity].items() if key not in governance_fields}
               for identity, fact in actual.items())
    changed = {identity for identity in actual if actual[identity] != accepted[identity]}
    allowed = set(baseline["revision_fact_ids"])
    assert changed <= allowed
    previously_reverted = {identity for identity in accepted if polluted[identity] == raw[identity] != accepted[identity]}
    assert len(previously_reverted) == 25
    assert all(actual[identity] == accepted[identity] for identity in previously_reverted)
    call = captured["governance_call"]
    prepared = source["authority_inputs"][0]
    assert action_state_hash({**prepared, "_user_confirmations": state["conflict_confirmations"]}) == call["identity"]["input_hash"]
    normalized = semantics.postprocess_authority_reconciliation_item(SimpleNamespace(artifacts={}), {
        "item_input": {**prepared, "_user_confirmations": state["conflict_confirmations"]}, "item_output": call["raw_output"],
    })
    assert normalized == call["output"] == source["authority_records"][0]["output"]
    assert source["source_authority"]["prepared_items"] == source["authority_inputs"]
    assert planning["source_authority"]["result"] == result
    assert planning["source_semantics"] == source["source_semantics"]
    assert state["prepared_input"]["source_authority"] == source["source_authority"]
    assert planning["input_sha256"] == action_state_hash(state["prepared_input"])
    assert all(record is not None for record in source["planning_records"])
    for kind in ("text", "vision"):
        assert [record["output"] for record in planning["source_records"][kind]] == [record["output"] for record in source[f"{kind}_records"]]
    report = {"run_id": 55, "node_id": captured["node_id"], "observed_at": captured["observed_at"],
        "scope": "一次真实DB完整state读取后本地复用；只核对来源治理和规划初始化链，不代表最终用例业务验收",
        "source_ref": source["source_ref"], "raw_fact_count": len(raw), "raw_facts_match_run53": True,
        "initial_source_baseline_result_sha256": action_state_hash(original),
        "polluted_planning_result_sha256": action_state_hash(old["post_authority_result"]),
        "used_initial_source_baseline": True, "used_polluted_planning_baseline": False,
        "allowed_revision_fact_ids": sorted(allowed), "actually_changed_fact_ids": sorted(changed),
        "immutable_source_fields_exact": True, "prior_25_judgments_preserved": True,
        "approved_context_original_business_fields_exact": True,
        "approved_context_provenance_verified": True,
        "approval_id": approval["id"], "approval_origin_run_id": old["source_run_id"],
        "source_merge_passed_online": captured["governance_receipt"]["status"] == "completed",
        "planning_initialization_second_merge_passed_online": planning is not None,
        "source_and_planning_authority_equal": True, "initial_draft_count": len(source["planning_records"]),
        "analysis_records": {kind: len(source[f"{kind}_records"]) for kind in ("text", "vision")},
        "gov_raw_patch_count": len(call["raw_output"]["decisions"]), "gov_normalized_patch_count": len(normalized["decisions"]),
        "real_gov_input_hash": call["identity"]["input_hash"], "action_headers": captured["action_headers"],
        "database_writes": 0, "model_requests_sent_by_audit": 0, "full_state_reads": 1}
    (FOLDER / "source-governance-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    audit(capture_once())
