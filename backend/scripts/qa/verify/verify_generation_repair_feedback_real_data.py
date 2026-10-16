"""回放真实失败生成批次的全部提交，核对全批缺口与真实候选绑定位置。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.tool_context import ToolContext
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_failure_kind, _is_retryable_agent_error
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, StructuredOutputJSONError, StructuredOutputValidationError,
    ToolArgumentsJSONError, ToolArgumentsValidationError, _function_tool,
)
from scripts.qa.verify.generation_contract_history import project_historical_generation_inputs


async def verify(db, run_id: int, node_id: int, item_index: int) -> dict:
    run = db.get(AgentRun, run_id)
    node = db.get(AgentNodeRun, node_id)
    if run is None or node is None or node.run_id != run.id or node.status != "failed":
        raise ValueError("必须选择真实失败运行中的真实失败生成节点")
    saved_hash = action_state_hash({"run_context": run.run_context, "node_state": node.sdk_state,
                                    "node_input": node.input_payload, "node_output": node.output_payload})
    item = next(value for value in node.sdk_state["items"] if value["item_index"] == item_index)
    assert item["status"] == "failed" and item["error_type"] == "MaxTurnsExceeded"
    node_inputs, contract_projection = project_historical_generation_inputs(AgentRunRepository(db).read_node_input(node)["items"])
    source = node_inputs[item_index]
    definition = db.get(AgentDefinition, node.agent_definition_id)
    workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
    execution_node = next(value for value in parse_execution_definition(workflow.definition).nodes
                          if value.node_key == node.node_key)
    tools = AgentDefinitionRepository(db).list_agent_tools(definition.id, project_id=run.project_id)
    terminal_names = set(definition.runtime_config["stop_at_tool_keys"])
    assert len(terminal_names) == 1
    tool_definition = next(value for value in tools if value.tool_key in terminal_names)
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                   run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload))
    contract = AgentOutputContract(deepcopy(source), execution_node.map_config)
    saved_budget = item["error_diagnostic"]["budget_usage"]
    hooks = AutonomousBudgetHooks(saved_budget["limits"], checkpoint=deepcopy(saved_budget))
    budget_before = hooks.snapshot()
    assert definition.runtime_config["max_turns"] == saved_budget["limits"]["attempted_requests"] == 6
    loop = AutonomousToolLoop(
        terminal_tool_names=terminal_names, validate_submission=partial(contract.validate, definition, context),
        recoverable_errors=(ToolArgumentsJSONError, ToolArgumentsValidationError,
                            StructuredOutputJSONError, StructuredOutputValidationError, OutputValidationError),
        max_tool_calls=saved_budget["limits"]["attempted_requests"], budget_snapshot=hooks.snapshot,
    )
    tool = loop.wrap_tool(_function_tool(tool_definition, context), allow_feedback=True)
    calls = item["tool_calls"]
    observations = item["error_diagnostic"]["tool_observations"]
    assert len(calls) == len(observations) == 6
    source_before = action_state_hash(source)
    candidate_hashes = [action_state_hash(call["arguments"]) for call in calls]
    results = []
    facts = {value["fact_id"]: value["assertion"] for value in source["authoritative_facts"]}
    designs = {value["test_design_item_id"]: value["coverage_intent"]
               for value in source["plan"]["test_design_items"]}
    for index, call in enumerate(calls):
        encoded = json.dumps(call["arguments"], ensure_ascii=False)
        response = json.loads(await tool.on_invoke_tool(ToolContext(
            context=context, tool_name=tool.name, tool_call_id=call["tool_call_id"], tool_arguments=encoded,
        ), encoded))
        assert response["status"] == "rejected"
        feedback = response["feedback"]
        details = feedback["details"]
        historical_details = observations[index]["feedback"]["details"]
        historical_core = {key: value for key, value in historical_details.items() if key != "repair_guidance"}
        assert {key: details[key] for key in historical_core} == historical_core
        guidance = details["repair_guidance"]
        targets = guidance["context"].get("repair_targets", [])
        gaps = guidance["context"].get("batch_coverage_gaps", {})
        semantic_feedback = guidance["feedback"] or ""
        missing_facts = details.get("missing_fact_ids", [])
        missing_designs = details.get("missing_test_design_item_ids", [])
        if details.get("invalid_fact_ids"):
            assert guidance["context"]["candidate_fact_ids_by_case"] == details["candidate_fact_ids_by_case"]
            assert all(identifier in semantic_feedback for identifier in details["invalid_fact_ids"])
        for identifier in missing_facts:
            assert facts[identifier].strip() in semantic_feedback
            assert identifier in gaps["missing_fact_ids"]
        for identifier in missing_designs:
            assert designs[identifier].strip() in semantic_feedback
            assert identifier in gaps["missing_test_design_item_ids"]
        for target in targets:
            assert "missing_fact_ids" not in target and "missing_test_design_item_ids" not in target
            assert "test_cases_array_index" not in target
            assert target["case_id"] in details["invalid_fact_ids_by_case"]
            assert all(set(issue["invalid_fact_ids"]) <= set(details["invalid_fact_ids_by_case"][target["case_id"]])
                       for issue in target["field_issues"])
        results.append({"submission": index + 1, "candidate_sha256": candidate_hashes[index],
                        "missing_fact_count": len(missing_facts), "missing_design_count": len(missing_designs),
                        "repair_target_count": len(targets), "feedback": feedback})
    assert all(event["status"] == "rejected" for event in loop.events)
    # 同一失败节点中已经成功的真实批次必须仍得到完全相同的标准化产物。
    accepted_outputs = {value["item_index"]: value["output"] for value in node.output_payload["items"]}
    accepted_replays = []
    for saved_item in node.sdk_state["items"]:
        if saved_item["status"] != "success":
            continue
        accepted = [value for value in saved_item["tool_observations"] if value["status"] == "accepted"]
        assert len(accepted) == 1
        call = next(value for value in saved_item["tool_calls"] if value["tool_call_id"] == accepted[0]["tool_call_id"])
        accepted_index = saved_item["item_index"]
        output = AgentOutputContract(deepcopy(node_inputs[accepted_index]), execution_node.map_config).validate(
            definition, context, deepcopy(call["arguments"]),
        )
        assert output == accepted_outputs[accepted_index]
        accepted_replays.append(accepted_index)
    try:
        loop.require_submission()
    except ModelBehaviorError:
        pass
    else:
        raise AssertionError("历史失败候选被错误标记为成功")
    # 从持久异常类型及原消息还原分类对象，不伪造模型响应或调用。
    exhausted = MaxTurnsExceeded(item["message"])
    assert _agent_failure_kind(exhausted) == "agent_budget_exhausted"
    assert _is_retryable_agent_error(exhausted) is False
    assert hooks.snapshot() == budget_before
    assert action_state_hash(source) == source_before
    assert [action_state_hash(call["arguments"]) for call in calls] == candidate_hashes
    assert action_state_hash({"run_context": run.run_context, "node_state": node.sdk_state,
                              "node_input": node.input_payload, "node_output": node.output_payload}) == saved_hash
    assert not context.artifacts and not context.action_state
    assert not db.new and not db.dirty and not db.deleted
    return {
        "status": "passed", "verification_scope": "persisted_real_candidates_through_production_tool_feedback",
        "source_run_id": run.id, "source_node_id": node.id, "item_index": item_index,
        "submissions_replayed": len(results), "accepted_submissions": 0, "submissions": results,
        "historical_accepted_batches_unchanged": accepted_replays,
        "historical_generation_contract_projection": contract_projection,
        "raw_inputs_and_candidates_unchanged": True, "source_database_evidence_unchanged": True,
        "budget_checkpoint_unchanged": True, "historical_budget": saved_budget,
        "max_turns_classification": "agent_budget_exhausted", "max_turns_retryable": False,
        "mysql_transaction": "READ ONLY", "online_database_writes": 0, "model_requests_sent": 0,
        "new_model_correction_behavior_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--item-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise ValueError("报告已存在，拒绝覆盖历史证据")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = asyncio.run(verify(db, options.run_id, options.node_id, options.item_index))
        db.rollback()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "submissions_replayed", "model_requests_sent")}))


if __name__ == "__main__":
    main()
