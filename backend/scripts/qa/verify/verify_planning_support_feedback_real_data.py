"""重验真实规划、路由和支持回执，验证未决反馈投影及定向复核入口。"""

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
from scripts.qa.verify.verify_persistent_input_filter_real_data import sqlite_engine
from scripts.qa.verify.verify_state_snapshot_filter_real_data import definition_for


def verify(source_path: Path):
    report = json.loads(source_path.read_text(encoding="utf-8"))
    evidence_path = source_path.with_suffix(".sqlite")
    raw = evidence_path.read_bytes()
    engine = sqlite_engine(source_path.with_suffix(".sqlite"))
    checked = []
    try:
        with Session(engine) as db:
            run = db.get(AgentRun, report["source_run_id"])
            node = db.get(AgentNodeRun, report["isolated_node_run_id"])
            checkpoint = deepcopy(node.sdk_state["action_checkpoint"])
            calls = list(checkpoint["validated_agent_calls"].values())
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
                artifacts=deepcopy(run.run_context["artifacts"]))
            generation.initialize_generation_state(context, deepcopy(node.input_payload))
            child = generation._planning_context(context, context.action_state)

            def actual_output(agent_key, item, postprocessor=None):
                matches = [call for call in calls if call["identity"]["agent_key"] == agent_key
                           and call["identity"]["input_hash"] == action_state_hash(item)]
                assert len(matches) == 1, (agent_key, action_state_hash(item))
                saved = matches[0]
                assert action_state_hash(saved["raw_output"]) == saved["raw_output_hash"]
                contract = AgentOutputContract(deepcopy(item),
                    AgentMapConfig(item_postprocessor=postprocessor) if postprocessor else None)
                normalized = contract.validate(definition_for(agent_key), child, deepcopy(saved["raw_output"]))
                assert normalized == saved["output"]
                checked.append({"agent_key": agent_key, "input_hash": action_state_hash(item),
                                "output_hash": action_state_hash(normalized)})
                return normalized

            action = planning.prepare_business_plan_rebuild(child, {})
            output = actual_output("test_business_planner", action["items"][0])
            planning.apply_business_plan_rebuild(child, action, [output])
            action = planning.prepare_scope_routing(child, {})
            outputs = [actual_output("test_planning_scope_router", item, "testing.postprocess_planning_scope_routing_item")
                       for item in action["items"]]
            planning.apply_scope_routing(child, action, outputs)
            action = planning.prepare_route_gap_review(child, {})
            outputs = [actual_output("test_planning_route_gap_reviewer", item, "testing.postprocess_planning_route_repair_item")
                       for item in action["items"]]
            planning.apply_route_gap_review(child, action, outputs)
            observation = planning.observe_planning_state(child)
            assert observation["unsupported_designs"] and not observation["can_finish"]
            assert all(decision["disposition"] == "unsupported" for item in observation["route_gaps"]
                       for decision in item["review"]["decisions"])
            source_receipts = list(checkpoint["receipts"].values())
            initial_review = next(value for value in source_receipts
                if value["tool_key"] == "review_planning_route_gaps" and value["status"] == "completed")
            initial_observation = json.loads(initial_review["result"]["output"])
            historical_feedback = initial_observation["planning"]["route_gaps"]
            original_unsupported = initial_observation["planning"]["unsupported_designs"]
            assert observation["unsupported_designs"] == original_unsupported
            first_revision = next(value for value in source_receipts if value["tool_key"] == "revise_planning_draft")
            arguments = json.loads(first_revision["arguments"]["arguments_json"])
            current_input = planning.prepare_draft_revision(child, arguments)["items"][0]
            historical_input = deepcopy(current_input)
            historical_input["_planning_feedback"]["route_gaps"] = historical_feedback
            assert any(value["identity"]["agent_key"] == "test_business_plan_batcher"
                       and value["identity"]["input_hash"] == action_state_hash(historical_input) for value in calls)
            for item in current_input["_planning_feedback"]["route_gaps"]:
                assert all(arguments["item_index"] in design["planning_batch_indexes"]
                           for design in item["missing_test_design_items"])
            failed_review = next(value for value in source_receipts
                if value["tool_key"] == "review_planning_route_gaps" and value.get("error", {}).get("recoverable"))
            failed_args = json.loads(failed_review["arguments"]["arguments_json"])
            try:
                planning.prepare_route_gap_review(child, failed_args)
            except OutputValidationError as error:
                assert error.details["reviewable_designs"] == observation["reviewable_designs"]
            else:
                raise AssertionError("真实错误参数必须被拒绝")
            by_module = {}
            for decision in observation["unsupported_designs"]:
                by_module.setdefault(decision["module_index"], []).append(decision["test_design_item_index"])
            # 使用同一次真实核实问题，只按当前未决结论补全选择字段；不制造或应用新的专家输出。
            selected_args = {**failed_args, "design_selections": [{"module_index": index,
                "test_design_item_indexes": indexes} for index, indexes in by_module.items()]}
            selected = planning.prepare_route_gap_review(child, selected_args)
            assert selected["kind"] == "support_revision"
            assert sum(len(item["missing_test_design_items"]) for item in selected["items"]) == len(original_unsupported)
            assert all(item["_planning_feedback"]["question"] == failed_args["question"] for item in selected["items"])
            assert not db.new and not db.dirty and not db.deleted
            result = {"status": "passed", "model_requests": 0, "online_database_writes": 0,
                "source_report": str(source_path.resolve()), "source_sqlite_sha256": hashlib.sha256(raw).hexdigest(),
                "source_checkpoint_sha256": action_state_hash(checkpoint),
                "validated_actual_calls": checked, "unsupported_designs": original_unsupported,
                "historical_feedback_module_count": len(historical_feedback),
                "current_unresolved_feedback_module_count": len(observation["route_gaps"]),
                "historical_draft_input_bytes": len(json.dumps(historical_input, ensure_ascii=False).encode()),
                "current_draft_input_bytes": len(json.dumps(current_input, ensure_ascii=False).encode()),
                "targeted_review_input_prepared": True, "targeted_review_model_executed": False,
                "current_initial_planning_state": deepcopy(child.action_state),
                "raw_input_sha256": action_state_hash(node.input_payload)}
    finally:
        engine.dispose()
    assert evidence_path.read_bytes() == raw
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("验证报告已经存在")
    result = verify(args.source_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {
        "validated_actual_calls", "current_initial_planning_state", "unsupported_designs",
    }}, ensure_ascii=False))
