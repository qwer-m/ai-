"""回放真实失败候选的支持差额，并可调用真实模型验证原契约下的定向修复。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from agents.exceptions import ModelBehaviorError

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentNodeRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputRepairError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from modules.agent_platform.test_generation_escalation import validate_generation_scope_feedback
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions
from scripts.qa.verify.verify_generation_session_failure_real_data import fingerprint
from scripts.qa.verify.verify_planning_support_contract_real_data import production_hashes, save_report
from scripts.qa.verify.verify_table_support_context_real_model import SupportTranscript


async def verify(options):
    frozen = production_hashes()
    report = {"status": "preparing", "source_run_id": options.run_id, "source_node_id": options.node_id,
              "item_index": options.item_index, "online_database_writes": 0, "formal_workflow_run": False,
              "natural_parent_action_selection": False, "production_sha256": frozen, "replays": []}
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run, node = db.get(AgentRun, options.run_id), db.get(AgentNodeRun, options.node_id)
        assert node.run_id == run.id and node.status == run.status == "failed"
        before = fingerprint(run, node)
        checkpoint = node.sdk_state["action_checkpoint"]
        item = deepcopy(checkpoint["state"]["generation_inputs"][options.item_index])
        actions = [value for value in checkpoint["receipts"].values()
                   if value["tool_key"] == "generate_cases" and value["status"] == "failed"
                   and options.item_index in json.loads(value["arguments"]["arguments_json"])["item_indexes"]]
        assert len(actions) == 1
        args = json.loads(actions[0]["arguments"]["arguments_json"])
        request = deepcopy(item)
        if args.get("guidance"):
            request["_planning_feedback"] = {"instruction": args["guidance"], "source": "协调者建议，不是新增事实或预算"}
        calls = [call for call in node.sdk_state["tool_calls"]
                 if call.get("tool_key") == "agent:test_case_generator" and call.get("status") == "failed"
                 and call["input_hash"] == action_state_hash(request)]
        assert len(calls) == 1
        saved = calls[0]
        snapshots, _, descriptions = _capture_current_definitions(db, run, "test_case_generator")
        definition, tools = snapshots["test_case_generator"]
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                      run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload))
        contract = AgentOutputContract(deepcopy(request), AgentMapConfig(item_postprocessor="testing.postprocess_generation_batch_item"),
            alternate_output_validators={"planning_feedback": partial(validate_generation_scope_feedback, item)})
        historical = {event["tool_call_id"]: event for event in saved["tool_observations"]}
        for call in saved["submissions"]:
            if call["tool_key"] != "submit_generation_batch":
                continue
            candidate = deepcopy(call["arguments"])
            try:
                contract.validate(definition, context, candidate)
            except (OutputRepairError, ModelBehaviorError) as wrapped:
                error = wrapped
                while isinstance(error, ModelBehaviorError) and error.__cause__ is not None:
                    error = error.__cause__
                assert isinstance(error, OutputRepairError)
                details = error.details
                old = historical[call["tool_call_id"]]["feedback"]["details"]
                assert {key: value for key, value in details.items() if key != "repair_guidance"} == {
                    key: value for key, value in old.items() if key != "repair_guidance"}
                guidance = details["repair_guidance"]["context"]
                gaps = guidance["batch_coverage_gaps"]["design_support_gaps"]
                assert gaps
                for gap in gaps:
                    support = {fact_id for fact_id, ids in request["case_fact_contract"]["fact_design_item_ids"].items()
                               if gap["test_design_item_id"] in ids}
                    assert gap["support_fact_ids"] == sorted(support)
                    assert gap["missing_fact_ids_by_case"] == {
                        case_id: sorted(support - set(facts))
                        for case_id, facts in guidance["candidate_fact_ids_by_case"].items()}
                feedback = {"message": str(error), "error_type": type(error).__name__, "details": details}
                report["replays"].append({"tool_call_id": call["tool_call_id"], "candidate": candidate,
                                           "feedback": feedback, "original_rejection_preserved": True})
            else:
                raise AssertionError("历史不合格候选被错误接纳")
        assert report["replays"]
        model_input, projection = _project_agent_map_input(definition=definition, raw_item=request)
        budget = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
        report.update(status="prepared", input=request, input_hash=action_state_hash(request),
                      input_projection=projection, request_budget=budget, definition_snapshots=descriptions)
        save_report(options.output, report)
        if options.execute:
            diagnostics = SupportTranscript(options.output.with_suffix(".requests.jsonl"), options.item_index)

            def save_budget(snapshot):
                report["budget_usage"] = snapshot
                save_report(options.output, report)

            hooks = AutonomousBudgetHooks(budget, on_change=save_budget)
            latest = report["replays"][-1]
            try:
                result = await run_agent_async(
                    db=db, agent_definition=definition, tool_definitions=tools, execution_context=context,
                    input_payload=model_input, request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                    disable_server_output_schema=True, skip_output_postprocessor=True,
                    retry_feedback=json.dumps({"candidate_output": latest["candidate"], "feedback": latest["feedback"]}, ensure_ascii=False),
                    output_contract=contract, request_budget=budget, shared_budget_hooks=hooks,
                    require_validated_submission=True, captured_definitions=snapshots, request_diagnostics=diagnostics,
                )
                assert result.validated_output == contract.validate(definition, context, deepcopy(result.output))
                report.update(status="contract_passed", raw_output=result.output, normalized_output=result.validated_output,
                              tool_observations=result.tool_observations, tool_calls=result.tool_calls)
            except Exception as error:
                report.update(status="failed", error_type=type(error).__name__, diagnostic=getattr(error, "diagnostic", {}))
            finally:
                report.update(budget_usage=hooks.snapshot(), model_requests=diagnostics.requests)
        assert not db.new and not db.dirty and not db.deleted
        db.expire_all()
        assert fingerprint(db.get(AgentRun, run.id), db.get(AgentNodeRun, node.id)) == before
        assert frozen == production_hashes()
        report["historical_run_unchanged"] = True
        db.rollback()
        save_report(options.output, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--item-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError("报告已存在，禁止覆盖")
    result = asyncio.run(verify(options))
    print(json.dumps({key: result.get(key) for key in ("status", "source_run_id", "item_index", "budget_usage")}, ensure_ascii=False))
    if result["status"] == "failed":
        raise SystemExit(1)
