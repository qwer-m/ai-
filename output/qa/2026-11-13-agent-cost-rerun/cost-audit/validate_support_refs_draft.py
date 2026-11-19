"""转换56份历史真实输入及输出验证当前ref协议；不是新模型响应或旧协议兜底。"""

from collections import Counter
from copy import deepcopy
import gzip
import json
from pathlib import Path

from prepare_support_refs_draft import load_draft_modules
from prepare_read_path_draft import ROOT
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.checkpoint_storage import ArtifactStore
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning


def compact_output(output, catalog):
    refs = {identity: reference for reference, identity in catalog.items()}
    result = deepcopy(output)
    for decision in result["decisions"]:
        decision["fact_refs"] = [refs[identity] for identity in decision.pop("fact_ids")]
        for gap in decision["support_gaps"]:
            gap["evidence_fact_refs"] = [refs[identity] for identity in gap.pop("evidence_fact_ids")]
            if gap["kind"] == "source_conflict":
                for side in gap["conflict"]["sides"]:
                    side["fact_ref"] = refs[side.pop("fact_id")]
    return result


def main():
    draft = load_draft_modules()
    semantics = draft["test_generation_semantics"]
    normalizer = draft["test_generation_planning"]
    batcher = draft["test_generation_planning_agent"]
    original = ROOT / "output/qa/2026-11-12-single-agent-live/round-11"
    snapshot = json.loads((original / "final-snapshot.json").read_text(encoding="utf-8"))
    run = snapshot["run"]
    created = json.loads((original / "created-run.json").read_text(encoding="utf-8"))["run"]
    checkpoint = snapshot["expanded_checkpoints"]["404"]
    calls = {call["identity"]["input_hash"]: call for call in checkpoint["validated_agent_calls"].values()
        if call["identity"]["agent_key"] == "test_planning_route_gap_reviewer"}
    receipts = sorted([row for row in checkpoint["receipts"].values() if row["tool_key"] == "review_planning_route_gaps"],
        key=lambda row: row["started_sequence"])
    contexts, sessions, capacity = {}, [], []
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        store = ArtifactStore(db, user_id=1, project_id=8)
        for receipt in receipts:
            state = store.get(store._ref(receipt["pre_state_hash"]))
            root = ToolExecutionContext(db=db, user_id=1, project_id=8, run_id=59, node_key="agent_network",
                run_input=run["input_payload"], action_input=run["input_payload"], action_state=state,
                artifacts=deepcopy(created["run_context"]["artifacts"]), confirmed_conflicts=state["conflict_confirmations"])
            prepared = generation._support_review_arguments(root, json.loads(receipt["arguments"]["arguments_json"]))
            assert not prepared["problem_ids"]
            child = generation._planning_context(root, state)
            child.action_state.pop("downstream_feedback", None)
            planning._commit(child, child.action_state)
            action = planning.prepare_route_gap_review(child, prepared)
            previous_batches = planning._support_review_batches(child, action["items"])
            current_batches = batcher._support_review_batches(child, action["items"])
            statistics = deepcopy(child.artifacts["planning_support_batch_capacity"])
            capacity.append({"action_sequence": receipt["started_sequence"], "previous_request_count": sum(map(len, previous_batches)),
                "current_request_count": sum(map(len, current_batches)), "statistics": statistics,
                "candidate_counts_identical": [len(item["candidate_facts"]) for item in action["items"]]})
            for item in [item for group in previous_batches for item in group]:
                trusted = {**item, "_user_confirmations": deepcopy(state["conflict_confirmations"])}
                original_hash = action_state_hash(trusted)
                assert original_hash in calls
                call = calls[original_hash]
                current = deepcopy(trusted)
                current["source_review"] = semantics.planning_support_source_review(current["candidate_facts"])
                catalogue = semantics.planning_support_fact_ref_catalog(current["candidate_facts"])
                raw = compact_output(call["raw_output"], catalogue)
                before = action_state_hash([current, raw])
                decoded = normalizer.postprocess_planning_route_repair_item(child, {"item_input": current, "item_output": raw})
                assert decoded == call["output"]
                assert action_state_hash(decoded) == call["output_hash"]
                assert before == action_state_hash([current, raw])
                restored = json.loads(json.dumps(current, ensure_ascii=False, sort_keys=True))
                assert normalizer.postprocess_planning_route_repair_item(child, {"item_input": restored, "item_output": raw}) == decoded
                reversed_facts = deepcopy(current)
                reversed_facts["candidate_facts"].reverse()
                reversed_facts["source_review"] = semantics.planning_support_source_review(reversed_facts["candidate_facts"])
                assert normalizer.postprocess_planning_route_repair_item(child, {"item_input": reversed_facts, "item_output": raw}) == decoded
                base = {key: value for key, value in trusted.items() if key not in {"missing_test_design_items", "_planning_feedback"}}
                base_hash = action_state_hash(base)
                contexts.setdefault(base_hash, base)
                varying = {key: trusted[key] for key in ("missing_test_design_items", "_planning_feedback") if key in trusted}
                sessions.append({"action_sequence": receipt["started_sequence"], "historical_input_hash": original_hash,
                    "historical_call_identity": call["identity"], "historical_raw_output_hash": call["raw_output_hash"],
                    "historical_output_hash": call["output_hash"], "input_context": base_hash, "input_fields": varying,
                    "historical_raw_output": call["raw_output"], "historical_normalized_output": call["output"]})
            print(json.dumps({"action_sequence": receipt["started_sequence"], "validated_sessions": len(sessions),
                "previous_requests": capacity[-1]["previous_request_count"], "current_requests": capacity[-1]["current_request_count"]}, ensure_ascii=False), flush=True)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    assert len(sessions) == len(calls) == 56
    data = {"provenance": {"run_id": 59, "source_node_id": 404,
        "source_file": "output/qa/2026-11-12-single-agent-live/round-11/final-snapshot.json",
        "cpu_projection_only": True, "historical_receipts_preserved": True,
        "model_requests_sent": 0, "business_database_writes": 0},
        "input_contexts": contexts, "sessions": sessions}
    data["data_sha256"] = action_state_hash(data)
    fixture = ROOT / "backend/tests/agent_platform/fixtures/run59_support_fact_refs_real.json.gz"
    fixture.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
    report = {"run_id": 59, "historical_sessions": 56, "all_normalized_outputs_byte_identity_preserved": True,
        "dictionary_hydration_and_candidate_order_independent": True, "source_and_raw_candidates_unchanged": True,
        "capacity": capacity, "previous_total_sessions": sum(item["previous_request_count"] for item in capacity),
        "current_repacked_total_sessions": sum(item["current_request_count"] for item in capacity),
        "fixture": str(fixture.relative_to(ROOT)), "fixture_data_sha256": data["data_sha256"],
        "production_code_modified": False, "model_requests_sent": 0, "business_database_writes": 0,
        "limits": "CPU转换旧真实raw输出为当前唯一refs协议并重新验收，不是新模型响应；批数是同一输入估算，不保证线上相同语义决策或用量。"}
    Path(__file__).with_name("support-fact-refs-draft-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sessions": len(sessions), "fixture_bytes": fixture.stat().st_size,
        "previous_total_sessions": report["previous_total_sessions"], "current_total_sessions": report["current_repacked_total_sessions"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
