"""Run59 真实规划/旧误报目标的只读回执审计；不展开完整业务状态。"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
common = runpy.run_path(str(Path(__file__).parent.parent / "round-10" / "_audit_planning_readonly.py"))

from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.test_generation_planning import coverage_topic_intent
from modules.agent_platform.test_generation_batching import _test_design_items_by_module


def capture():
    path = Path(__file__).with_name("planning-progress-audit.json")
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = dict(db.execute(text("SELECT id,status,error_message,JSON_EXTRACT(run_context,'$.usage') AS observed_usage FROM agent_runs WHERE id=59")).mappings().one())
        run["usage"] = common["decoded"](run.pop("observed_usage"))
        compact = db.execute(text("SELECT JSON_EXTRACT(sdk_state,'$.action_checkpoint') FROM agent_node_runs WHERE id=404 AND run_id=59")).scalar_one()
        checkpoint = common["decoded"](compact)
        branch = checkpoint["journal_ref"]["branch_id"]
        receipts = sorted(({"id": row["id"], **common["decoded"](row["record"])} for row in db.execute(
            text("SELECT id,record FROM agent_action_receipts WHERE branch_id=:branch"), {"branch": branch}).mappings()), key=lambda value: value["id"])
        latest = {receipt["tool_call_id"]: receipt for receipt in receipts}
        starts = [receipt for receipt in receipts if receipt["tool_key"] == "rebuild_business_plan" and receipt["status"] == "started"]
        if not starts:
            print("尚无完整规划行动；不采空快照。")
            db.rollback()
            return
        first = min(starts, key=lambda value: value["started_sequence"])
        calls = sorted(({"id": row["id"], "agent_key": row["agent_key"], **common["decoded"](row["record"])} for row in db.execute(
            text("SELECT id,agent_key,record FROM agent_validated_calls WHERE branch_id=:branch"), {"branch": branch}).mappings()), key=lambda value: value["id"])
        reader = common["TargetedReader"](db)
        handoff = previous.get("initial_draft_handoff") or common["initial_handoff"](reader, first)
        target = previous.get("original_button_target")
        if not target:
            records = reader.subtree(first["pre_state_hash"], ["planning", "planning_records"])
            target = {"source_fact_id": "DOC287-P0008-S0-E326-f9", "draft_candidates": []}
            for index, record in enumerate(records):
                for candidate_index, candidate in enumerate(record["output"]["module_candidates"]):
                    if target["source_fact_id"] in candidate["fact_ids"]:
                        target["draft_candidates"].append({"item_index": index, "candidate_index": candidate_index,
                            "candidate_name": candidate["name"], "topics": candidate["coverage_topics"],
                            "compiled_intents": [coverage_topic_intent(topic) for topic in candidate["coverage_topics"]]})
        globals_ = previous.get("global_plan_checks", [])
        known_globals = {item["validated_call_id"] for item in globals_}
        for call in calls:
            if call["agent_key"] != "test_business_planner" or call["id"] in known_globals:
                continue
            receipt = latest[call["parent_tool_call_id"]]
            source = handoff if receipt["tool_call_id"] == first["tool_call_id"] else common["initial_handoff"](reader, receipt)
            output = reader.store.get(call["output_ref"])
            plan = output.get("plan", output)
            intents = [intent for module in plan["business_modules"] for point in module["test_points"] for design in point["test_designs"] for intent in design["coverage_items"]]
            globals_.append({"validated_call_id": call["id"], "action_sequence": receipt["started_sequence"],
                "parent_tool_call_id": receipt["tool_call_id"], "pre_state_hash": receipt["pre_state_hash"],
                "input_hash": call["identity"]["input_hash"], "output_hash": call["output_hash"],
                "all_compiled_intents_preserved": Counter(intents) == Counter(source["compiled_intents"]),
                "compiled_intent_count": len(source["compiled_intents"]), "materialized_intent_count": len(intents),
                "business_module_count": len(plan["business_modules"]), "fact_count": source["fact_count"],
                "first_exact_sdk_input_hash_matches": call["identity"]["input_hash"] == source["exact_initial_global_input_hash"]
                    if receipt["tool_call_id"] == first["tool_call_id"] else None,
                "later_input_note": "修订请求另含真实保持约束及反馈；不把裸consolidation hash冒充完整SDK输入。"})
        revisions = previous.get("draft_revision_checks", [])
        known_revisions = {item["tool_call_id"] for item in revisions}
        for receipt in latest.values():
            if receipt["tool_key"] == "revise_planning_draft" and receipt["status"] == "completed" and receipt["tool_call_id"] not in known_revisions:
                revision = common["draft_revision_check"](reader, receipt, calls)
                entry = reader.entry_at(receipt["pre_state_hash"], ["planning", "planning_records"])
                size = reader.array_length(entry["ref"]) if "ref" in entry else len(entry["value"])
                revision["changed_draft_record_indexes"] = [index for index in range(size) if
                    reader.value_hash(receipt["pre_state_hash"], ["planning", "planning_records", index]) !=
                    reader.value_hash(receipt["post_state_hash"], ["planning", "planning_records", index])]
                revisions.append(revision)
        target_intents = {intent for candidate in target["draft_candidates"] for intent in candidate["compiled_intents"]}
        target_indexes = {candidate["item_index"] for candidate in target["draft_candidates"]}
        target_intents.update(intent for revision in revisions if revision["item_index"] in target_indexes
                              for intent in revision["after_compiled_intents"])
        target_reviews = previous.get("button_candidate_support_checks", [])
        scanned_reviews = set(previous.get("scanned_support_call_ids", []))
        plans = {}
        for call in calls:
            if call["agent_key"] != "test_planning_route_gap_reviewer" or call["id"] in scanned_reviews:
                continue
            receipt = latest[call["parent_tool_call_id"]]
            if receipt["tool_call_id"] not in plans:
                plans[receipt["tool_call_id"]] = reader.subtree(receipt["pre_state_hash"], ["planning", "plan"])
            catalog = _test_design_items_by_module(modules=plans[receipt["tool_call_id"]]["business_modules"])
            output = reader.store.get(call["output_ref"])
            selected = [{"coverage_intent": catalog[output["module_index"]][decision["test_design_item_index"]]["coverage_intent"],
                **decision} for decision in output["decisions"] if
                catalog[output["module_index"]][decision["test_design_item_index"]]["coverage_intent"] in target_intents]
            scanned_reviews.add(call["id"])
            if selected:
                target_reviews.append({"validated_call_id": call["id"], "parent_action_sequence": receipt["started_sequence"],
                    "module_index": output["module_index"], "input_hash": call["identity"]["input_hash"],
                    "raw_output_ref": call["raw_output_ref"], "output_ref": call["output_ref"], "decisions": selected})
        duplicates = defaultdict(list)
        for call in calls:
            duplicates[(call["agent_key"], call["identity"]["input_hash"])].append(call["id"])
        layout_failures = previous.get("layout_action_failures", [])
        known_failures = {entry["receipt_id"] for entry in layout_failures}
        for receipt in latest.values():
            if receipt["tool_key"] == "revise_generation_layout" and receipt["status"] == "failed" and receipt["id"] not in known_failures:
                envelope = reader.store.get(receipt["arguments_ref"])
                arguments = json.loads(envelope["arguments_json"])
                layout_failures.append({"action_sequence": receipt["started_sequence"], "receipt_id": receipt["id"],
                    "tool_call_id": receipt["tool_call_id"], "pre_state_hash": receipt["pre_state_hash"],
                    "post_state_hash": receipt.get("post_state_hash"), "parsed_arguments_hash": action_state_hash(arguments),
                    "error": reader.store.get(receipt["error_ref"])})
        actions = [{"receipt_id": receipt["id"], **{key: receipt.get(key) for key in (
            "tool_key", "tool_call_id", "status", "started_sequence", "pre_state_hash", "post_state_hash", "arguments_ref", "result_ref",
        )}} for receipt in sorted(latest.values(), key=lambda value: value.get("started_sequence", 0))]
        report = {"observed_at": datetime.now(timezone.utc).isoformat(), "run_id": 59, "node_id": 404, "branch_id": branch,
            "mysql_transaction": "READ ONLY", "business_database_writes": 0, "model_requests_sent_by_audit": 0,
            "scope": "常规仅小回执/指定不可变子树；实际动作目录单次完整内存恢复另记于actual_action_catalog_verification，未输出或存档完整state。",
            "run": run, "checkpoint_projection": checkpoint["projection"], "actions": actions,
            "validated_agent_counts": dict(Counter(call["agent_key"] for call in calls)),
            "validated_call_headers": [{"id": call["id"], "agent_key": call["agent_key"], "parent_tool_call_id": call["parent_tool_call_id"],
                "identity": call["identity"], "raw_output_ref": call["raw_output_ref"], "output_ref": call["output_ref"]} for call in calls],
            "duplicate_validated_complete_input_identities": [{"agent_key": key[0], "input_hash": key[1], "call_ids": ids} for key, ids in duplicates.items() if len(ids) > 1],
            "initial_draft_handoff": handoff, "global_plan_checks": globals_, "draft_revision_checks": revisions,
            "original_button_target": target, "button_candidate_support_checks": target_reviews,
            "scanned_support_call_ids": sorted(scanned_reviews),
            "source_reinspection_checks": previous.get("source_reinspection_checks", []),
            "source_reinspection_actually_occurred": any(receipt["tool_key"] == "reinspect_planning_source" for receipt in latest.values()),
            "semantic_assessment": previous.get("semantic_assessment", {"status": "尚待实际目标裁决，不提前判断新reviewer成功。"}),
            "latest_support_summary": previous.get("latest_support_summary"),
            "formal_generation_preflight": previous.get("formal_generation_preflight"),
            "actual_action_catalog_verification": previous.get("actual_action_catalog_verification"),
            "layout_action_failures": layout_failures,
            "limits": ["validated-call身份未重复不能证明provider内部没有重试。", "无实际来源重检时不声明在线迁移复用通过。"]}
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"path": str(path), "run_status": run["status"], "actions": [(action["started_sequence"], action["tool_key"], action["status"]) for action in actions],
            "validated_counts": report["validated_agent_counts"], "global_plans": len(globals_), "button_review_calls": [entry["validated_call_id"] for entry in target_reviews]}, ensure_ascii=False))
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()


if __name__ == "__main__":
    capture()
