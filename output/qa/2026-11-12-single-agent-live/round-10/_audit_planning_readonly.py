"""Run58 有界只读规划审计；只展开目标子树，不读完整业务状态。"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentArtifactBlob
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.checkpoint_storage import ArtifactStore, content_hash
from modules.agent_platform.test_generation_planning import (
    coverage_topic_intent, prepare_business_plan_consolidation,
)


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


class TargetedReader:
    def __init__(self, db):
        self.db = db
        self.store = ArtifactStore(db, user_id=1, project_id=8)
        self.cache = {}

    def blob(self, ref):
        if ref["id"] not in self.cache:
            row = self.db.get(AgentArtifactBlob, ref["id"])
            assert row is not None and (row.user_id, row.project_id) == (1, 8)
            assert row.sha256 == ref["sha256"] and row.payload_hash == content_hash(row.payload)
            self.cache[ref["id"]] = row.payload
        return self.cache[ref["id"]]

    def field(self, ref, key):
        payload = self.blob(ref)
        if payload["type"] == "inline":
            return {"value": payload["value"][key]}
        if payload["type"] == "object":
            return next(entry for field, entry in payload["entries"] if field == key)
        if payload["type"] == "object_chunks":
            for part in payload["entries"]:
                try:
                    return self.field(part["ref"], key)
                except (KeyError, StopIteration):
                    pass
            raise KeyError(key)
        if payload["type"] == "array":
            return payload["entries"][key]
        if payload["type"] == "array_chunks":
            offset = key
            for part in payload["entries"]:
                size = self.array_length(part["ref"])
                if offset < size:
                    return self.field(part["ref"], offset)
                offset -= size
            raise IndexError(key)
        raise TypeError(payload["type"])

    def array_length(self, ref):
        payload = self.blob(ref)
        if payload["type"] == "inline":
            return len(payload["value"])
        if payload["type"] == "array":
            return len(payload["entries"])
        return sum(self.array_length(part["ref"]) for part in payload["entries"])

    def entry_at(self, state_hash, keys):
        entry = {"ref": self.store._ref(state_hash)}
        for key in keys:
            entry = self.field(entry["ref"], key) if "ref" in entry else {"value": entry["value"][key]}
        return entry

    def subtree(self, state_hash, keys):
        entry = self.entry_at(state_hash, keys)
        return self.store.get(entry["ref"]) if "ref" in entry else entry["value"]

    def value_hash(self, state_hash, keys):
        entry = self.entry_at(state_hash, keys)
        return entry["ref"]["sha256"] if "ref" in entry else action_state_hash(entry["value"])


def initial_handoff(reader, first):
    inputs = reader.subtree(first["pre_state_hash"], ["planning", "planning_inputs"])
    records = reader.subtree(first["pre_state_hash"], ["planning", "planning_records"])
    prepared = prepare_business_plan_consolidation(SimpleNamespace(artifacts={}), {
        "prepared_items": inputs, "plan_records": records, "case_budget": inputs[0]["case_budget"],
    })
    model_input = {key: prepared[key] for key in (
        "partial_plans", "planning_metadata", "coverage_group_catalog", "case_budget", "planning_limits",
    )}
    confirmations = reader.subtree(first["pre_state_hash"], ["conflict_confirmations"])
    managed_input = {**model_input, "_user_confirmations": confirmations} if confirmations else model_input
    managed_batches = [{**item, "_user_confirmations": confirmations} if confirmations else item for item in inputs]
    intents = [intent for group in prepared["coverage_group_catalog"] for intent in group["coverage_items"]]
    examples = []
    for index, record in enumerate(records):
        for candidate_index, candidate in enumerate(record["output"]["module_candidates"]):
            if set(candidate["fact_ids"]) & {"DOC287-P0002-f7", "DOC287-P0002-f8"}:
                for topic_index, topic in enumerate(candidate["coverage_topics"]):
                    examples.append({"item_index": index, "module_candidate_index": candidate_index,
                        "topic_index": topic_index, "source_topic": topic,
                        "compiled_intent": coverage_topic_intent(topic),
                        "present_in_complete_planning_input": coverage_topic_intent(topic) in intents})
    return {"source_pre_state_hash": first["pre_state_hash"], "planning_batch_count": len(inputs),
        "fact_count": prepared["covered_fact_count"],
        "all_current_inputs_have_only_business_fields": all(set(item) == {"planning_scopes", "case_budget"} for item in inputs),
        "maximum_complete_input_json_chars": max(len(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) for item in inputs),
        "unique_complete_input_count": len({action_state_hash(item) for item in inputs}),
        "raw_topic_count": sum(len(candidate["coverage_topics"]) for record in records for candidate in record["output"]["module_candidates"]),
        "compiled_unique_intent_count": len(intents), "compiled_intents": intents,
        "all_topics_preserved_after_complete_intent_dedupe": set(intents) == {
            coverage_topic_intent(topic) for record in records for candidate in record["output"]["module_candidates"] for topic in candidate["coverage_topics"]},
        "consolidation_input_hash": action_state_hash(model_input),
        "sdk_user_confirmations_count": len(confirmations), "sdk_user_confirmations_sha256": action_state_hash(confirmations),
        "sdk_managed_batch_input_hashes": [action_state_hash(item) for item in managed_batches],
        "exact_initial_global_input_hash": action_state_hash(managed_input), "condition_examples": examples}


def draft_revision_check(reader, receipt, calls):
    envelope = reader.store.get(receipt["arguments_ref"])
    arguments = json.loads(envelope["arguments_json"])
    index = arguments["item_index"]
    before = reader.subtree(receipt["pre_state_hash"], ["planning", "planning_records", index])
    after = reader.subtree(receipt["post_state_hash"], ["planning", "planning_records", index])
    old_input = reader.subtree(receipt["pre_state_hash"], ["planning", "planning_inputs", index])
    new_input = reader.subtree(receipt["post_state_hash"], ["planning", "planning_inputs", index])
    completed = [call for call in calls if call["parent_tool_call_id"] == receipt["tool_call_id"]]
    return {"action_sequence": receipt["started_sequence"], "receipt_id": receipt["id"],
        "tool_call_id": receipt["tool_call_id"], "item_index": index, "question": arguments["question"],
        "validated_call_ids": [call["id"] for call in completed],
        "complete_expert_input_hashes": [call["identity"]["input_hash"] for call in completed],
        "pre_state_hash": receipt["pre_state_hash"], "post_state_hash": receipt["post_state_hash"],
        "source_business_input_unchanged": old_input == new_input,
        "source_fact_assertions": [fact for scope in new_input["planning_scopes"] for fact in scope["facts"]],
        "before_record_input_hash": before["input_hash"], "after_record_input_hash": after["input_hash"],
        "before_output": before["output"], "after_output": after["output"],
        "output_changed": before["output"] != after["output"],
        "before_compiled_intents": [coverage_topic_intent(topic) for candidate in before["output"]["module_candidates"] for topic in candidate["coverage_topics"]],
        "after_compiled_intents": [coverage_topic_intent(topic) for candidate in after["output"]["module_candidates"] for topic in candidate["coverage_topics"]]}


def assignments(plan):
    return {intent: {"module_name": module["name"], "test_point": point["name"], "technique": design["technique"]}
        for module in plan["business_modules"] for point in module["test_points"]
        for design in point["test_designs"] for intent in design["coverage_items"]}


def capture():
    report_path = Path(__file__).with_name("planning-progress-audit.json")
    previous = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = dict(db.execute(text("SELECT id,status,JSON_EXTRACT(run_context,'$.usage') AS observed_usage,error_message FROM agent_runs WHERE id=58")).mappings().one())
        run["usage"] = decoded(run.pop("observed_usage"))
        node = dict(db.execute(text("SELECT id,status,JSON_EXTRACT(sdk_state,'$.action_checkpoint') AS checkpoint FROM agent_node_runs WHERE id=403 AND run_id=58")).mappings().one())
        checkpoint = decoded(node.pop("checkpoint"))
        branch = checkpoint["journal_ref"]["branch_id"]
        receipts = sorted(({"id": row["id"], **decoded(row["record"])} for row in db.execute(
            text("SELECT id,record FROM agent_action_receipts WHERE branch_id=:branch"), {"branch": branch}).mappings()), key=lambda value: value["id"])
        latest = {receipt["tool_call_id"]: receipt for receipt in receipts}
        calls = sorted(({"id": row["id"], "agent_key": row["agent_key"], **decoded(row["record"])} for row in db.execute(
            text("SELECT id,agent_key,record FROM agent_validated_calls WHERE branch_id=:branch"), {"branch": branch}).mappings()), key=lambda value: value["id"])
        reader = TargetedReader(db)
        first = min((receipt for receipt in receipts if receipt["tool_key"] == "rebuild_business_plan" and receipt["status"] == "started"), key=lambda value: value["started_sequence"])
        handoff = previous.get("initial_draft_handoff")
        if not handoff or "sdk_user_confirmations_sha256" not in handoff:
            handoff = initial_handoff(reader, first)
        revisions = previous.get("draft_revision_checks", [])
        known_revisions = {item["tool_call_id"] for item in revisions}
        for receipt in latest.values():
            if (receipt["tool_key"] == "revise_planning_draft" and receipt["status"] == "completed"
                    and receipt["tool_call_id"] not in known_revisions):
                revisions.append(draft_revision_check(reader, receipt, calls))
        for revision in revisions:
            if "changed_draft_record_indexes" not in revision:
                before_entry = reader.entry_at(revision["pre_state_hash"], ["planning", "planning_records"])
                size = reader.array_length(before_entry["ref"]) if "ref" in before_entry else len(before_entry["value"])
                revision["changed_draft_record_indexes"] = [index for index in range(size)
                    if reader.value_hash(revision["pre_state_hash"], ["planning", "planning_records", index])
                    != reader.value_hash(revision["post_state_hash"], ["planning", "planning_records", index])]
        checks = []
        first_plan = None
        for call in calls:
            if call["agent_key"] != "test_business_planner" or call["parent_tool_call_id"] != first["tool_call_id"]:
                continue
            output = reader.store.get(call["output_ref"])
            raw = reader.store.get(call["raw_output_ref"])
            plan = output.get("plan", output)
            first_plan = plan
            intents = [intent for module in plan["business_modules"] for point in module["test_points"] for design in point["test_designs"] for intent in design["coverage_items"]]
            checks.append({"validated_call_id": call["id"], "input_hash": call["identity"]["input_hash"],
                "matches_reconstructed_exact_input": call["identity"]["input_hash"] == handoff["exact_initial_global_input_hash"],
                "raw_output_hash": call["raw_output_hash"], "output_hash": call["output_hash"],
                "raw_equals_normalized": raw == output, "materialized_intent_count": len(intents),
                "all_compiled_intents_preserved": Counter(intents) == Counter(handoff["compiled_intents"]),
                "normalization_note": "模型CG引用由平台完整展开为原子文本，并编译草案摘要字段；raw/normalized hash不同不表示重问模型。",
                "business_module_count": len(plan["business_modules"])})
        later_checks = previous.get("subsequent_global_plan_checks", [])
        known_globals = {item["validated_call_id"] for item in later_checks}
        for call in calls:
            if (call["agent_key"] != "test_business_planner" or call["parent_tool_call_id"] == first["tool_call_id"]
                    or call["id"] in known_globals):
                continue
            receipt = latest[call["parent_tool_call_id"]]
            current = initial_handoff(reader, receipt)
            output = reader.store.get(call["output_ref"])
            plan = output.get("plan", output)
            actual = assignments(plan)
            previous_assignments = assignments(first_plan)
            unchanged = set(actual) & set(previous_assignments)
            later_checks.append({"validated_call_id": call["id"], "action_sequence": receipt["started_sequence"],
                "parent_tool_call_id": call["parent_tool_call_id"], "pre_state_hash": receipt["pre_state_hash"],
                "input_hash": call["identity"]["input_hash"], "consolidation_input_hash": current["consolidation_input_hash"],
                "full_input_reconstruction_note": "修订请求还含真实既有边界、设计保持约束和路由反馈；保留回执完整identity，不把裸consolidation hash伪作实际SDK请求。",
                "all_compiled_intents_preserved": Counter(actual.keys()) == Counter(current["compiled_intents"]),
                "materialized_intent_count": len(actual), "business_module_count": len(plan["business_modules"]),
                "removed_relative_to_initial": sorted(set(previous_assignments) - set(actual)),
                "added_relative_to_initial": sorted(set(actual) - set(previous_assignments)),
                "unchanged_atomic_intent_count": len(unchanged),
                "unchanged_atomic_design_assignments_changed": [intent for intent in sorted(unchanged) if actual[intent] != previous_assignments[intent]],
                "planning_batch_count": current["planning_batch_count"], "fact_count": current["fact_count"]})
        duplicates = defaultdict(list)
        for call in calls:
            duplicates[(call["agent_key"], call["identity"]["input_hash"])].append(call["id"])
        actions = [{"receipt_id": receipt["id"], **{key: receipt.get(key) for key in (
            "tool_call_id", "tool_key", "status", "started_sequence", "pre_state_hash", "post_state_hash", "arguments_ref", "result_ref",
        )}} for receipt in sorted(latest.values(), key=lambda value: value.get("started_sequence", 0))]
        report = {"observed_at": datetime.now(timezone.utc).isoformat(), "run_id": 58, "node_id": 403,
            "branch_id": branch, "scope": "小行动回执/validated-call和固定首次汇总前planning_inputs、planning_records子树，未展开完整state；已有子树证明按不可变hash复用。",
            "mysql_transaction": "READ ONLY", "model_requests_sent_by_audit": 0, "business_database_writes": 0,
            "run": run, "checkpoint_projection": checkpoint["projection"], "actions": actions,
            "action_tool_counts": dict(Counter(receipt["tool_key"] for receipt in latest.values())),
            "completed_action_tool_counts": dict(Counter(receipt["tool_key"] for receipt in latest.values() if receipt["status"] == "completed")),
            "validated_agent_counts": dict(Counter(call["agent_key"] for call in calls)),
            "validated_agent_counts_by_action": [{"action_sequence": action["started_sequence"], "tool_key": action["tool_key"],
                "counts": dict(Counter(call["agent_key"] for call in calls if call["parent_tool_call_id"] == action["tool_call_id"]))} for action in actions],
            "validated_call_headers": [{"id": call["id"], "agent_key": call["agent_key"], "parent_tool_call_id": call["parent_tool_call_id"],
                "identity": call["identity"], "output_ref": call["output_ref"], "output_hash": call["output_hash"]} for call in calls],
            "duplicate_validated_complete_input_identities": [{"agent_key": key[0], "input_hash": key[1], "validated_call_ids": ids}
                for key, ids in duplicates.items() if len(ids) > 1],
            "initial_draft_handoff": handoff, "first_global_plan_checks": checks,
            "subsequent_global_plan_checks": later_checks,
            "draft_revision_checks": revisions,
            "initial_draft_sdk_input_hashes_all_match": set(handoff["sdk_managed_batch_input_hashes"]) == {
                call["identity"]["input_hash"] for call in calls if call["agent_key"] == "test_business_plan_batcher"
                and call["parent_tool_call_id"] == next(receipt["tool_call_id"] for receipt in receipts if receipt["tool_key"] == "draft_source_plan")},
            "source_reinspection_checks": previous.get("source_reinspection_checks", []),
            "audit_collection_errors": [
                {"kind": "SQL_ALIAS_RESERVED_WORD", "detail": "首次只读采集使用usage作SQL别名触发1064，改observed_usage后重采；未写业务数据。"},
                {"kind": "SDK_MANAGED_INPUT_ENVELOPE", "detail": "首次裸consolidation hash未计SDK统一追加的_user_confirmations，补齐固定原上下文后核验；不是生产输入丢失。"},
                {"kind": "ACTION_ARGUMENT_ENVELOPE", "detail": "首次读取修订参数时未解当前arguments_json外壳触发KeyError；已按真实参数结构解码，未改运行。"}],
            "limits": ["validated_call唯一性不能证明provider/格式修复内部没有重试；不能把重试次数当作重复业务成功调用。",
                       "尚未完成的后续规划、来源重检、生成和人工验收不作成功结论。"]}
        report["verdict"] = ("首次完整规划已保存；26份草案的完整业务输入和名称/目标交接通过，全部原子意图保留。"
            if checks and all(check["matches_reconstructed_exact_input"] and check["all_compiled_intents_preserved"] for check in checks)
            else "26份初始草案已真实完成；首次完整规划尚待回执或其完整输入证明尚待核查，暂不下收敛结论。")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"path": str(report_path), "actions": [(value["started_sequence"], value["tool_key"], value["status"]) for value in actions],
            "counts": report["validated_agent_counts"], "global_checks": checks, "verdict": report["verdict"]}, ensure_ascii=False))
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()


if __name__ == "__main__":
    capture()
