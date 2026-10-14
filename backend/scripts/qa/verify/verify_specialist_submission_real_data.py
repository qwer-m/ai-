"""回放真实修复产物的批次错配与正确提交；验证专业 Agent 同会话验收，不调用模型。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper
from agents.exceptions import ModelBehaviorError
from agents.items import ItemHelpers, ToolCallOutputItem
from agents.run_internal.turn_resolution import check_for_final_output_from_tools
from agents.tool import FunctionToolResult
from agents.tool_context import ToolContext
from agents.usage import deserialize_usage
from sqlalchemy import text

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentToolDefinition
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, StructuredOutputJSONError, StructuredOutputValidationError,
    ToolArgumentsJSONError, ToolArgumentsValidationError, _build_sdk_agent,
    _model_settings, _output_submission_tool, _runner_input,
)
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from modules.agent_platform.test_generation_review import _inline_repair_cases
from modules.agent_platform.test_generation_review_agent import initialize_review_state, observe_review_state
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition


def historical_patch(output: dict, schema: dict) -> dict:
    """只转换历史正式产物的表示形式，不删改事实、步骤或任何业务内容。"""
    fields = schema["properties"]["case_patches"]["items"]["properties"]
    return {"case_patches": [
        {key: deepcopy(value) for key, value in case.items() if key in fields}
        for case in _inline_repair_cases(test_cases=output["test_cases"], bindings=output["case_fact_bindings"])
    ]}


async def verify(db) -> dict:
    workspace = Path(__file__).resolve().parents[4]
    source_report = workspace / "output/qa/2026-09-20-review-agent-run28-global-real-model.json"
    report = json.loads(source_report.read_text(encoding="utf-8"))
    source_sqlite = Path(report["isolated_action_database"])
    connection = sqlite3.connect(source_sqlite.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        stage_input = json.loads(connection.execute("select input_payload from agent_node_runs").fetchone()[0])
    finally:
        connection.close()
    run = db.get(AgentRun, report["source_run_id"])
    node = next(item for item in db.query(AgentNodeRun).filter(AgentNodeRun.run_id == run.id).all()
                if item.node_key == "final_review_repairs" and item.status == "success")
    repo = AgentRunRepository(db)
    sources = repo.read_node_input(node)["items"]
    target_index = next(i for i, value in enumerate(sources) if value["review_batch"]["batch_id"] == "R-002")
    other_index = next(i for i, value in enumerate(sources) if value["review_batch"]["batch_id"] == "R-001")
    outputs = {item["item_index"]: item["output"] for item in node.output_payload["items"]}
    usages = {item["item_index"]: item["usage"] for item in node.sdk_state["items"]}
    definition = db.get(AgentDefinition, node.agent_definition_id)
    parent = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                  run_id=run.id, node_key="resolve_final_review", run_input=deepcopy(run.input_payload))
    initialize_review_state(parent, stage_input)
    parent_before = action_state_hash(parent.action_state)
    child = replace(parent, action_input={}, action_state={}, artifacts=deepcopy(parent.artifacts),
                    executed_tools=[], tool_calls=[], action_journal=None, invoke_agent=None)
    contract = AgentOutputContract(deepcopy(sources[target_index]),
                                   AgentMapConfig(item_postprocessor="testing.postprocess_final_review_repair_item"))
    accepted_candidate = historical_patch(outputs[target_index], definition.output_schema)
    wrong_batch_candidate = historical_patch(outputs[other_index], definition.output_schema)
    assert contract.validate(definition, child, deepcopy(accepted_candidate)) == outputs[target_index]
    shared = AutonomousBudgetHooks(report["request_reservation"])
    loop = AutonomousToolLoop(
        terminal_tool_names={"submit_result"}, validate_submission=partial(contract.validate, definition, child),
        recoverable_errors=(ToolArgumentsJSONError, ToolArgumentsValidationError, StructuredOutputJSONError,
                            StructuredOutputValidationError, OutputValidationError),
        max_tool_calls=report["request_reservation"]["attempted_requests"], budget_snapshot=shared.snapshot,
    )
    original_tool = _output_submission_tool(definition, child)
    assert original_tool.params_json_schema == definition.output_schema
    assert original_tool.strict_json_schema is False
    assert original_tool.params_json_schema["properties"]["case_patches"]["items"]["required"] == ["case_id"]
    tool = loop.wrap_tool(original_tool, allow_feedback=True)
    agent = Agent(name=definition.name, instructions=definition.instructions, tools=[tool],
                  model_settings=_model_settings(definition.runtime_config),
                  tool_use_behavior=loop.tool_use_behavior, reset_tool_choice=False)
    child_wrapper = RunContextWrapper(context=child)
    inputs = ItemHelpers.input_to_new_input_list(_runner_input(
        execution_context=child, input_payload=sources[target_index], runtime_config=definition.runtime_config,
    ))
    allocated_clients = []
    try:
        coordinator_spec = next(item for item in BUILTIN_AGENT_SPECS
                                if item["agent_key"] == "test_generation_review_coordinator")
        coordinator = builtin_definition(AgentDefinition, coordinator_spec,
                                         project_id=run.project_id, user_id=run.user_id)
        tools = [builtin_definition(AgentToolDefinition, spec, project_id=run.project_id, user_id=run.user_id)
                 for spec in BUILTIN_TOOL_SPECS if spec["tool_key"] in coordinator_spec["runtime_config"]["tool_keys"]]
        owner, _ = _build_sdk_agent(
            db=db, client=get_client_for_user(run.user_id, db), agent_definition=coordinator,
            execution_context=parent, direct_tool_definitions=tools, allow_subagents=False,
            allocated_clients=allocated_clients, budget_hooks=shared,
        )
        root_wrapper = RunContextWrapper(context=parent)
        root_inputs = ItemHelpers.input_to_new_input_list(json.dumps(observe_review_state(parent, {}), ensure_ascii=False))
        await shared.on_llm_start(root_wrapper, owner, await owner.get_system_prompt(root_wrapper), root_inputs)
        shared.settle_request(root_wrapper, deserialize_usage(report["model_requests"][0]["usage"]))
        budget_observations = [shared.observe()]
        for attempt, (candidate, usage) in enumerate((
            (wrong_batch_candidate, usages[other_index]), (accepted_candidate, usages[target_index]),
        ), start=1):
            await shared.on_llm_start(child_wrapper, agent, await agent.get_system_prompt(child_wrapper), inputs)
            shared.settle_request(child_wrapper, deserialize_usage(usage))
            encoded = json.dumps(candidate, ensure_ascii=False)
            call_id = f"historical-node-{node.id}-submission-{attempt}"
            result = await tool.on_invoke_tool(ToolContext(
                context=child, tool_name=tool.name, tool_call_id=call_id, tool_arguments=encoded,
            ), encoded)
            sdk_result = FunctionToolResult(tool=tool, output=result, run_item=ToolCallOutputItem(
                agent=agent, output=result,
                raw_item={"type": "function_call_output", "call_id": call_id, "output": result},
            ))
            decision = await check_for_final_output_from_tools(agent, [sdk_result], child_wrapper)
            assert action_state_hash(parent.action_state) == parent_before
            budget_observations.append(shared.observe())
            if attempt == 1:
                assert json.loads(result)["status"] == "rejected" and not decision.is_final_output
                try:
                    loop.require_submission()
                except ModelBehaviorError:
                    pass
                else:
                    raise AssertionError("错批次真实产物被当作专业 Agent 最终结果")
            else:
                assert decision.is_final_output and decision.final_output == accepted_candidate
        accepted = loop.require_submission()
        assert accepted.normalized_output == outputs[target_index]
        assert [event["status"] for event in loop.events] == ["rejected", "accepted"]
        assert shared.snapshot()["completed_requests"] == 3
        assert shared.snapshot()["charged"]["attempted_requests"] == 3
        assert [item["remaining"]["attempted_requests"] for item in budget_observations] == [
            report["request_reservation"]["attempted_requests"] - count for count in (1, 2, 3)
        ]
        return {
            "status": "passed", "verification_scope": "historical_real_outputs_mismatched_then_corrected",
            "natural_model_rejection_replayed": False, "source_run_id": run.id, "source_node_id": node.id,
            "target_batch_id": "R-002", "mismatched_actual_batch_id": "R-001",
            "schema_preserved": True, "optional_patch_fields_preserved": True,
            "same_loop_events": loop.events, "validated_output_matches_history": True,
            "parent_domain_state_unchanged": True, "shared_budget_observations": budget_observations,
            "model_requests_sent": 0, "database_writes": 0,
        }
    finally:
        await asyncio.gather(*(client.close() for client in allocated_clients))


if __name__ == "__main__":
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        result = asyncio.run(verify(db))
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    output = Path(__file__).resolve().parents[3] / "artifacts/verification/specialist_submission_run28.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "report": str(output), "model_requests_sent": 0}, ensure_ascii=False))
