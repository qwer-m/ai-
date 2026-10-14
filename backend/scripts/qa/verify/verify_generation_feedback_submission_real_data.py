"""回放真实生成候选和真实原文诊断标注，验证 SDK 终止工具及嵌套执行身份。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper
from agents.exceptions import ModelBehaviorError
from agents.items import ToolCallOutputItem
from agents.run_internal.turn_resolution import check_for_final_output_from_tools
from agents.tool import FunctionToolResult
from agents.tool_context import ToolContext
from sqlalchemy import text

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentToolDefinition
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import AgentMapConfig, parse_execution_definition
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, StructuredOutputJSONError, StructuredOutputValidationError,
    ToolArgumentsJSONError, ToolArgumentsValidationError, _build_sdk_agent,
    _function_tool, _model_settings,
)
from modules.agent_platform.semantic_identity import agent_execution_semantic_hash
from modules.agent_platform.test_generation_builtin_specs import (
    BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS, BUILTIN_WORKFLOW_SPECS,
)
from modules.agent_platform.test_generation_escalation import validate_generation_scope_feedback
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition


def _submission_loop(definition, tools, context, item_input, *, allow_report):
    contract = AgentOutputContract(
        deepcopy(item_input), AgentMapConfig(item_postprocessor="testing.postprocess_generation_batch_item"),
        alternate_output_validators={
            "planning_feedback": partial(validate_generation_scope_feedback, deepcopy(item_input)),
        } if allow_report else None,
    )
    loop = AutonomousToolLoop(
        terminal_tool_names=set(definition.runtime_config["stop_at_tool_keys"]),
        validate_submission=partial(contract.validate, definition, context),
        recoverable_errors=(ToolArgumentsJSONError, ToolArgumentsValidationError,
                            StructuredOutputJSONError, StructuredOutputValidationError, OutputValidationError),
        max_tool_calls=definition.runtime_config["max_turns"],
    )
    actual_tools = {
        tool.tool_key: loop.wrap_tool(_function_tool(tool, context), allow_feedback=True)
        for tool in tools
    }
    agent = Agent(name=definition.name, instructions=definition.instructions, tools=list(actual_tools.values()),
                  model_settings=_model_settings(definition.runtime_config),
                  tool_use_behavior=loop.tool_use_behavior, reset_tool_choice=False)
    return loop, actual_tools, agent


async def _submit(context, agent, tool, arguments, call_id):
    encoded = json.dumps(arguments, ensure_ascii=False)
    response = await tool.on_invoke_tool(ToolContext(
        context=context, tool_name=tool.name, tool_call_id=call_id, tool_arguments=encoded,
    ), encoded)
    sdk_result = FunctionToolResult(tool=tool, output=response, run_item=ToolCallOutputItem(
        agent=agent, output=response,
        raw_item={"type": "function_call_output", "call_id": call_id, "output": response},
    ))
    decision = await check_for_final_output_from_tools(agent, [sdk_result], RunContextWrapper(context=context))
    return json.loads(response), decision


def _require_no_submission(loop):
    try:
        loop.require_submission()
    except ModelBehaviorError:
        return
    raise AssertionError("被拒绝的报告被当作有效终止结果")


async def verify(db, annotation_path: Path) -> dict:
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    assert annotation["feedback_author"] == "analyst_annotation_not_model_output"
    run = db.get(AgentRun, annotation["source_run_id"])
    node = db.get(AgentNodeRun, annotation["source_node_id"])
    assert node.run_id == run.id and node.node_key == "generation" and node.status == "success"
    inputs = AgentRunRepository(db).read_node_input(node)["items"]
    item_index = annotation["source_item_index"]
    item_input = inputs[item_index]
    saved_item = next(item for item in node.sdk_state["items"] if item["item_index"] == item_index)
    accepted = [event for event in saved_item["tool_observations"] if event["status"] == "accepted"]
    assert len(accepted) == 1
    accepted_call = next(call for call in saved_item["tool_calls"]
                         if call["tool_call_id"] == accepted[0]["tool_call_id"])
    assert accepted_call["tool_key"] == "submit_generation_batch"
    expected_output = next(item["output"] for item in node.output_payload["items"]
                           if item["item_index"] == item_index)
    before = action_state_hash({"inputs": inputs, "output": node.output_payload,
                                "sdk_state": node.sdk_state, "run_context": run.run_context})
    report = annotation["accepted_feedback"]
    assert report["planning_feedback"]["source_input_hash"] == action_state_hash(item_input)
    report_arguments = {"issues": deepcopy(report["planning_feedback"]["issues"])}
    identity = {"project_id": run.project_id, "user_id": run.user_id}
    definitions = {spec["agent_key"]: builtin_definition(AgentDefinition, spec, **identity)
                   for spec in BUILTIN_AGENT_SPECS}
    tools = {spec["tool_key"]: builtin_definition(AgentToolDefinition, spec, **identity)
             for spec in BUILTIN_TOOL_SPECS}
    generator = definitions["test_case_generator"]
    generator_tools = [tools[key] for key in generator.runtime_config["tool_keys"]]
    base_context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                        run_id=run.id, node_key="resolve_generation", run_input=deepcopy(run.input_payload))
    observations = []

    # 在同一个真实 SDK 工具循环中，未经授予的报告被拒绝后，正常用例仍可完成验收。
    child = replace(base_context, artifacts={}, tool_calls=[], executed_tools=[])
    loop, bound, agent = _submission_loop(generator, generator_tools, child, item_input, allow_report=False)
    response, decision = await _submit(child, agent, bound["report_generation_scope_problem"], report_arguments,
                                       "analyst-report-without-alternate-contract")
    assert response["status"] == "rejected" and not decision.is_final_output
    _require_no_submission(loop)
    response, decision = await _submit(child, agent, bound["submit_generation_batch"],
                                       deepcopy(accepted_call["arguments"]), accepted_call["tool_call_id"])
    assert decision.is_final_output and decision.final_output == accepted_call["arguments"]
    assert loop.require_submission().normalized_output == expected_output
    observations.append({"check": "ungranted_report_then_real_cases", "events": loop.events,
                         "sdk_stopped_on_report": False, "sdk_stopped_on_cases": True,
                         "normalized_cases_match_history": True})

    child = replace(base_context, artifacts={}, tool_calls=[], executed_tools=[])
    loop, bound, agent = _submission_loop(generator, generator_tools, child, item_input, allow_report=True)
    response, decision = await _submit(child, agent, bound["report_generation_scope_problem"], report_arguments,
                                       "analyst-report-with-granted-alternate-contract")
    assert decision.is_final_output and decision.final_output == response
    normalized = loop.require_submission().normalized_output
    assert normalized == report and normalized["result_type"] == "planning_feedback"
    assert "test_cases" not in normalized and normalized["planning_feedback"]["assessment_status"] == "unreviewed"
    observations.append({"check": "granted_report", "events": loop.events,
                         "sdk_stopped": True, "report_is_deliverable_cases": False})

    child = replace(base_context, artifacts={}, tool_calls=[], executed_tools=[])
    loop, bound, agent = _submission_loop(generator, generator_tools, child, item_input, allow_report=True)
    _, decision = await _submit(child, agent, bound["submit_generation_batch"],
                                deepcopy(accepted_call["arguments"]), accepted_call["tool_call_id"])
    assert decision.is_final_output and loop.require_submission().normalized_output == expected_output
    observations.append({"check": "granted_report_does_not_weaken_case_contract", "events": loop.events,
                         "normalized_cases_match_history": True})

    other_index = annotation["comparison_item_index"]
    child = replace(base_context, artifacts={}, tool_calls=[], executed_tools=[])
    loop, bound, agent = _submission_loop(generator, generator_tools, child, inputs[other_index], allow_report=True)
    response, decision = await _submit(child, agent, bound["report_generation_scope_problem"], report_arguments,
                                       "analyst-report-against-other-real-batch")
    assert response["status"] == "rejected" and not decision.is_final_output
    _require_no_submission(loop)
    observations.append({"check": "different_real_batch_report", "events": loop.events, "sdk_stopped": False})

    # 构建实际当前定义及其叶子专家，身份计算不得遗漏托管生成者的场景咨询能力。
    snapshots = {key: (definition, [tools[tool_key] for tool_key in definition.runtime_config.get("tool_keys", [])])
                 for key, definition in definitions.items()}
    client = get_client_for_user(run.user_id, db)
    allocated_clients = []
    try:
        for key in ("test_generation_coordinator", "test_case_generator"):
            definition, direct_tools = snapshots[key]
            built, has_tools = _build_sdk_agent(
                db=db, client=client, agent_definition=definition, execution_context=base_context,
                trusted_input_payload=deepcopy(item_input), model_input_payload=deepcopy(item_input),
                direct_tool_definitions=direct_tools, allow_subagents=True,
                allocated_clients=allocated_clients, definition_snapshots=snapshots,
            )
            assert has_tools
            if key == "test_case_generator":
                assert "test_generation_scenario_designer" in {tool.name for tool in built.tools}
        graph = parse_execution_definition(next(spec["definition"] for spec in BUILTIN_WORKFLOW_SPECS
                                                 if spec["workflow_key"] == "test_generation"))
        current_node = next(item for item in graph.nodes if item.node_key == "resolve_generation")
        owner, owner_tools = snapshots["test_generation_coordinator"]
        hash_arguments = dict(db=db, user_id=run.user_id, project_id=run.project_id, node=current_node,
                              definition=owner, tools=owner_tools, client=client)
        original_hash = agent_execution_semantic_hash(**hash_arguments, definition_snapshots=snapshots)
        changed_snapshots = deepcopy(snapshots)
        changed_snapshots["test_generation_scenario_designer"][0].instructions += "\n独立验证副本的定义变更。"
        changed_hash = agent_execution_semantic_hash(**hash_arguments, definition_snapshots=changed_snapshots)
        assert original_hash != changed_hash
        assert agent_execution_semantic_hash(**hash_arguments, definition_snapshots=snapshots) == original_hash
        missing_snapshots = deepcopy(snapshots)
        missing_snapshots.pop("test_generation_scenario_designer")
        try:
            agent_execution_semantic_hash(**hash_arguments, definition_snapshots=missing_snapshots)
        except ValueError as error:
            assert "test_generation_scenario_designer" in str(error)
        else:
            raise AssertionError("缺少实际叶子专家快照仍然生成了执行身份")
    finally:
        await asyncio.gather(*(allocated.close() for allocated in allocated_clients))

    assert before == action_state_hash({"inputs": inputs, "output": node.output_payload,
                                       "sdk_state": node.sdk_state, "run_context": run.run_context})
    assert not base_context.action_state and not base_context.artifacts
    assert not db.new and not db.dirty and not db.deleted
    return {
        "status": "passed", "source_run_id": run.id, "source_node_id": node.id, "source_item_index": item_index,
        "comparison_item_index": other_index, "annotation_path": str(annotation_path.resolve()),
        "feedback_author": annotation["feedback_author"], "actual_accepted_tool_call_id": accepted_call["tool_call_id"],
        "actual_accepted_candidate_hash": action_state_hash(accepted_call["arguments"]), "checks": observations,
        "nested_execution_identity": {"original_hash": original_hash, "changed_leaf_hash": changed_hash,
                                      "leaf_change_invalidates_parent": True, "missing_leaf_rejected": True,
                                      "original_snapshot_unchanged": True},
        "historical_candidates_budget_and_database_evidence_unchanged": True,
        "sdk_tool_final_output_resolution_verified": True,
        "run_agent_async_runner_completion_verified": False,
        "online_database_writes": 0, "mysql_transaction": "READ ONLY", "model_requests_sent": 0,
        "natural_model_escalation_verified": False, "semantic_conflict_independently_reviewed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise ValueError("报告已存在，拒绝覆盖历史证据")
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        result = asyncio.run(verify(db, options.annotation))
        db.rollback()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "checks": len(result["checks"]),
                      "nested_leaf_identity_verified": True, "model_requests_sent": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
