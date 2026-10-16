"""重放真实规划及权威回执，验证支持缺口的准确目标、保留结果与后续复核边界。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy.orm import Session

from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import AgentOutputContract
from modules.agent_platform.test_generation_planning import postprocess_planning_route_repair_item
from scripts.qa.verify.verify_persistent_input_filter_real_data import sqlite_engine
from scripts.qa.verify.verify_state_snapshot_filter_real_data import definition_for


def verify(source_path: Path):
    report_bytes = source_path.read_bytes()
    source_report = json.loads(report_bytes)
    source_db = source_path.with_suffix(".sqlite")
    original_bytes = source_db.read_bytes()
    engine = sqlite_engine(source_db)
    checked = []
    try:
        with Session(engine) as db:
            run = db.get(AgentRun, source_report["source_run_id"])
            node = db.get(AgentNodeRun, source_report["isolated_node_run_id"])
            checkpoint = deepcopy(node.sdk_state["action_checkpoint"])
            calls = list(checkpoint["validated_agent_calls"].values())
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=node.node_key, run_input=deepcopy(run.input_payload), artifacts=deepcopy(run.run_context["artifacts"]))
            generation.initialize_generation_state(context, deepcopy(node.input_payload))
            child = generation._planning_context(context, context.action_state)

            def actual_output(agent_key, item, postprocessor=None):
                matches = [call for call in calls if call["identity"]["agent_key"] == agent_key
                           and call["identity"]["input_hash"] == action_state_hash(item)]
                assert len(matches) == 1, (agent_key, action_state_hash(item))
                saved = matches[0]
                assert action_state_hash(saved["raw_output"]) == saved["raw_output_hash"]
                contract = AgentOutputContract(deepcopy(item), AgentMapConfig(item_postprocessor=postprocessor) if postprocessor else None)
                output = contract.validate(definition_for(agent_key), child, deepcopy(saved["raw_output"]))
                assert output == saved["output"]
                checked.append({"agent_key": agent_key, "input_hash": action_state_hash(item), "output_hash": action_state_hash(output)})
                return output

            action = planning.prepare_business_plan_rebuild(child, {})
            planning.apply_business_plan_rebuild(child, action, [actual_output("test_business_planner", action["items"][0])])
            action = planning.prepare_scope_routing(child, {})
            planning.apply_scope_routing(child, action, [actual_output("test_planning_scope_router", item,
                "testing.postprocess_planning_scope_routing_item") for item in action["items"]])
            action = planning.prepare_route_gap_review(child, {})
            planning.apply_route_gap_review(child, action, [actual_output("test_planning_route_gap_reviewer", item,
                "testing.postprocess_planning_route_repair_item") for item in action["items"]])
            assert planning.observe_planning_state(child)["can_finish"]
            context.action_state["planning"] = deepcopy(child.action_state)
            generation._save(context, context.action_state)
            generation.accept_generation_plan(context, {})
            action = generation.prepare_authority_action(context, {"item_indexes": list(range(len(context.action_state["authority_inputs"])))})
            outputs = [actual_output("test_authority_reconciliation_reviewer", item,
                       "testing.postprocess_authority_reconciliation_item") for item in action["items"]]
            generation.apply_authority_action(context, action, outputs)
            state = context.action_state
            assert all(state["authority_records"]) and state["effective_facts"] is not None
            assert not state["generation_inputs"] and not state["generation"]
            observation = generation.observe_generation_state(context)
            problems = observation["problems"]
            assert len(problems) == 1 and problems[0]["origin"] == "preparation"
            problem = generation.inspect_generation_scope(context, {"problem_id": problems[0]["problem_id"]})["problem"]
            gaps = problem["payload"]["design_support_gaps"]
            selections = {}
            for gap in gaps:
                selections.setdefault(gap["module_index"], []).append(gap["test_design_item_index"])
                assert set(gap["inactive_support_fact_ids"]) and set(gap["remaining_support_fact_ids"])
            child = generation._planning_context(context, state)
            child.action_state["downstream_feedback"] = generation._feedback(state, [problem["problem_id"]])
            planning._commit(child, child.action_state)
            args = {"design_selections": [{"module_index": key, "test_design_item_indexes": values} for key, values in selections.items()],
                    "question": "请依据真实权威结论重新核对完整支持集合；不得引用已排除事实，不预设有支持或无支持的结论。"}
            revision = planning.prepare_route_gap_review(child, args)
            stale_rejections = []
            for item in revision["items"]:
                for design in item["missing_test_design_items"]:
                    gap = next(gap for gap in gaps if gap["module_index"] == item["module_index"]
                               and gap["test_design_item_index"] == design["test_design_item_index"])
                    assert gap["coverage_intent"] == design["coverage_intent"]
                original = next(record["output"] for record in state["planning"]["gap_records"]
                                if record["output"]["module_index"] == item["module_index"])
                indexes = {design["test_design_item_index"] for design in item["missing_test_design_items"]}
                old_candidate = {**deepcopy(original), "decisions": [deepcopy(decision) for decision in original["decisions"]
                                                                  if decision["test_design_item_index"] in indexes]}
                try:
                    postprocess_planning_route_repair_item(child, {"item_input": item, "item_output": old_candidate})
                except OutputValidationError as error:
                    assert not set(error.details["allowed_fact_ids"]) & set(problem["payload"]["authority_inactive_fact_ids"])
                    stale_rejections.append(item["module_index"])
                else:
                    raise AssertionError("旧支持结论重新引用已排除事实却被通过")
            # 真实上游身份失效时，旧准备问题必须退出当前观察，历史回执仍保留。
            invalidated = deepcopy(state)
            generation._invalidate_downstream(invalidated)
            generation._save(context, invalidated)
            assert not generation.observe_generation_state(context)["problems"]
            assert not db.new and not db.dirty and not db.deleted
            result = {"status": "passed", "model_requests": 0, "online_database_writes": 0,
                "source_report": str(source_path.resolve()), "source_sqlite_sha256": hashlib.sha256(original_bytes).hexdigest(),
                "source_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
                "source_execution_semantic_hash": checkpoint["execution_semantic_hash"],
                "previous_budget": deepcopy(source_report["budget_usage"]),
                "raw_input_sha256": action_state_hash(node.input_payload), "validated_actual_calls": checked,
                "authority_records_retained": len(state["authority_records"]), "problem": problem,
                "targeted_review_arguments": {**args, "problem_ids": [problem["problem_id"]]},
                "targeted_review_inputs": revision["items"], "old_support_rejected_modules": stale_rejections,
                "upstream_invalidation_removes_old_problem": True, "current_generation_state": state}
    finally:
        engine.dispose()
    assert source_db.read_bytes() == original_bytes and source_path.read_bytes() == report_bytes
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("报告已存在")
    result = verify(args.source_report)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "revalidated_calls": len(result["validated_actual_calls"]),
                      "authority_records_retained": result["authority_records_retained"],
                      "gaps": result["problem"]["payload"]["design_support_gaps"]}, ensure_ascii=False))
