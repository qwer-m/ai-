"""用真实候选的跨批次错配回放专业提交反馈与来源硬失败，不调用模型。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.tool_context import ToolContext
from sqlalchemy import text

from core.db.database import SessionLocal
from core.ai.ai_client import get_client_for_user
from core.db.model_defs import AgentDefinition, AgentToolDefinition
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.sdk_adapter import AgentOutputContract, _build_sdk_agent, _function_tool, _output_submission_tool
from modules.agent_platform.runtime import _agent_request_reservation
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from modules.agent_platform.test_generation_planning import prepare_planning_scope_routes
from scripts.qa.verify.verify_planning_agent_real_data import (
    context_for, load_planning_fixture, regroup_historical_routes,
)
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition


def make_loop(contract, definition, context):
    terminal = (definition.runtime_config or {}).get("stop_at_tool_keys") or ["submit_result"]
    return AutonomousToolLoop(terminal_tool_names=set(terminal),
                              validate_submission=partial(contract.validate, definition, context),
                              recoverable_errors=(OutputValidationError,), max_tool_calls=4)


async def verify(db):
    fixtures = {run_id: load_planning_fixture(db, run_id) for run_id in (31, 30)}
    run, nodes, arguments = fixtures[31]
    source_node = nodes["source_text"]
    source_candidates = [next(call["arguments"] for call in reversed(item["tool_calls"])
                              if call["tool_key"] == "submit_source_semantics")
                         for item in source_node["sdk_state"]["items"]]
    gap_candidates = [record["output"] for _, fixture_nodes, _ in fixtures.values()
                      for record in fixture_nodes["plan_route_repairs"]["output"]["items"]]
    cases = [
        ("source", "test_source_text_semantics_analyst", "testing.postprocess_source_semantics_item",
         source_node["input"]["items"][0], source_candidates[0], source_candidates,
         source_node["output"]["items"][0]["output"]),
        ("draft", "test_business_plan_batcher", None,
         arguments["planning_inputs"][0], arguments["planning_records"][0]["output"],
         [record["output"] for record in arguments["planning_records"]], arguments["planning_records"][0]["output"]),
        ("gap", "test_planning_route_gap_reviewer", "testing.postprocess_planning_route_repair_item",
         nodes["plan_route_repairs"]["input"]["items"][0],
         nodes["plan_route_repairs"]["output"]["items"][0]["output"], gap_candidates,
         nodes["plan_route_repairs"]["output"]["items"][0]["output"]),
    ]
    checks = []
    for label, key, processor, source, good, candidates, expected in cases:
        context = context_for(db, run)
        definition = builtin_definition(AgentDefinition, next(spec for spec in BUILTIN_AGENT_SPECS if spec["agent_key"] == key),
                                        user_id=run.user_id, project_id=run.project_id)
        contract = AgentOutputContract(deepcopy(source), AgentMapConfig(item_postprocessor=processor) if processor else None)
        loop = make_loop(contract, definition, context)
        bad = None
        for candidate in candidates:
            try:
                contract.validate(definition, context, deepcopy(candidate))
            except Exception as error:
                if loop.feedback_for_error(error) is not None:
                    bad = candidate
                    break
        assert bad is not None, f"{label} 没有可复用的真实跨批次候选"
        if label == "source":
            spec = next(spec for spec in BUILTIN_TOOL_SPECS if spec["tool_key"] == "submit_source_semantics")
            tool = _function_tool(builtin_definition(AgentToolDefinition, spec, user_id=run.user_id, project_id=run.project_id), context)
        elif label == "draft":
            reservation = _agent_request_reservation(definition=definition, tools=[], input_payload=source)
            assert reservation["attempted_requests"] == definition.runtime_config["max_turns"] == 6
            clients = []
            try:
                agent, has_tools = _build_sdk_agent(
                    db=db, client=get_client_for_user(run.user_id, db), agent_definition=definition,
                    execution_context=context, direct_tool_definitions=[], allow_subagents=False,
                    autonomous_loop=loop, allocated_clients=clients,
                )
                assert has_tools and [tool.name for tool in agent.tools] == ["submit_result"]
                tool = agent.tools[0]
            finally:
                for client in clients:
                    await client.close()
        else:
            tool = _output_submission_tool(definition, context)
        wrapped = tool if label == "draft" else loop.wrap_tool(tool, allow_feedback=True)
        before = action_state_hash(context.artifacts)
        for index, candidate in enumerate((bad, good)):
            encoded = json.dumps(candidate, ensure_ascii=False)
            result = await wrapped.on_invoke_tool(ToolContext(context=context, tool_name=tool.name,
                tool_call_id=f"real-mismatch-{label}-{index}", tool_arguments=encoded), encoded)
            if index == 0:
                assert json.loads(result)["status"] == "rejected"
            assert action_state_hash(context.artifacts) == before
        assert loop.require_submission().normalized_output == expected
        assert [event["status"] for event in loop.events] == ["rejected", "accepted"]
        damaged_context = context_for(db, run)
        damaged = deepcopy(source)
        if label == "source":
            maps = damaged_context.artifacts["source_semantics_coordinate_maps"]
            first = next(iter(maps.values()))[0]
            first["original_span"]["end"] += 1
        elif label == "draft":
            damaged["planning_scopes"][0]["facts"].append(deepcopy(damaged["planning_scopes"][0]["facts"][0]))
        else:
            damaged["module_index"] = -1
        damaged_contract = AgentOutputContract(damaged, contract.map_config)
        try:
            damaged_contract.validate(definition, damaged_context, deepcopy(good))
        except Exception as error:
            assert loop.feedback_for_error(error) is None, "真实输入损坏被当成模型可修正输出"
            hard_error = type(error).__name__
        else:
            raise AssertionError(f"{label} 未拒绝损坏输入")
        checks.append({"kind": label, "events": loop.events, "accepted_unchanged": True,
                       "source_damage_hard_failure": hard_error,
                       "rejection_kind": "mismatched_actual_historical_candidate_not_natural_failure"})
    # 原失败专项没有保留子候选正文，仅有错误和摘要；不伪造该候选用于回放。
    artifact = Path(__file__).resolve().parents[4] / "output/qa/2026-09-21-planning-agent-run31-original-real-model.sqlite"
    with sqlite3.connect(artifact.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        state = json.loads(connection.execute("select sdk_state from agent_node_runs").fetchone()[0])["action_checkpoint"]["state"]
    prepared = prepare_planning_scope_routes(context_for(db, run), {
        "plan": state["plan"], "planning_scopes": state["source_semantics"]["planning_scopes"],
    })
    outputs = regroup_historical_routes({"items": prepared["batch_items"]}, state["route_inputs"], state["route_records"])
    old_routes = [route for record in state["route_records"] for route in record["output"]["routes"]]
    assert [route for output in outputs for route in output["routes"]] == old_routes
    assert len(prepared["batch_items"]) < len(state["route_inputs"])
    return {"status": "passed", "model_requests": 0, "database_writes": 0, "submission_checks": checks,
            "route_projection": {"old_batches": len(state["route_inputs"]), "new_batches": len(prepared["batch_items"]),
                                 "all_facts_and_coverage_intents_unchanged": True, "historical_assignments_unchanged": True},
            "historical_failed_gap_candidate_body_available": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("报告已存在，请指定新路径")
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = asyncio.run(verify(db))
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "route_projection": report["route_projection"], "output": str(args.output)}, ensure_ascii=False))
