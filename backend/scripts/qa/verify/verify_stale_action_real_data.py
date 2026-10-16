"""回放真实失败节点的过期工具调用，验证 SDK 注册、领域拒绝和纠错反馈边界。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper
from agents.tool_context import ToolContext
from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import AgentOutputContract, _PersistentStateInputFilter, _function_tool
from scripts.qa.verify.prepare_agent_workflow_e2e_real_data import source_evidence
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions


async def verify(run_id):
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, run_id)
        ids = db.query(AgentNodeRun.id).filter_by(run_id=run_id, node_key="resolve_generation").all()
        node = db.get(AgentNodeRun, max(value for value, in ids))
        assert node.status == "failed" and "Tool review_planning_route_gaps not found" in node.error_message
        original = source_evidence(db, run_id)
        checkpoint = node.sdk_state["action_checkpoint"]
        snapshots, _, _ = _capture_current_definitions(db, run, "test_generation_coordinator")
        definition, definitions = snapshots["test_generation_coordinator"]
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
            artifacts=deepcopy(run.run_context.get("artifacts") or {}),
            action_input=AgentRunRepository(db).read_node_input(node),
            action_state=deepcopy(checkpoint["state"]))
        state_hash = action_state_hash(context.action_state)
        state_filter = _PersistentStateInputFilter(context=context, runtime_config=definition.runtime_config)
        observation = state_filter.observe()
        key = "review_planning_route_gaps"
        assert key not in observation["available_actions"]
        tools = {item.tool_key: _function_tool(item, context, state_input_filter=state_filter) for item in definitions}
        agent = Agent(name=definition.name, tools=list(tools.values()))
        registered = {tool.name for tool in await agent.get_all_tools(RunContextWrapper(context))}
        assert registered == set(tools) and key in registered
        # 复用同一真实运行此前已执行的参数，重现规划改变后再次调用原工具的边界。
        receipt = next(item for item in checkpoint["receipts"].values()
                       if item["tool_key"] == key and item["status"] == "completed")
        arguments = receipt["arguments"]["arguments_json"]
        loop = AutonomousToolLoop(
            terminal_tool_names=set(definition.runtime_config["stop_at_tool_keys"]),
            validate_submission=partial(AgentOutputContract(context.action_input).validate, definition, context),
            recoverable_errors=(OutputValidationError,), max_tool_calls=definition.runtime_config["max_turns"],
        )
        result = json.loads(await loop.wrap_tool(tools[key], allow_feedback=True).on_invoke_tool(
            ToolContext(context=context, tool_name=key, tool_call_id=receipt["tool_call_id"],
                        tool_arguments=arguments), arguments))
        assert result["status"] == "rejected"
        assert result["feedback"]["details"]["available_actions"] == observation["available_actions"]
        assert loop.events[0]["status"] == "rejected"
        assert not context.executed_tools and not context.tool_calls
        assert state_hash == action_state_hash(context.action_state)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        db.execute(text("SET TRANSACTION READ ONLY"))
        assert original == source_evidence(db, run_id)
        db.rollback()
        return {"status": "passed", "source_run_id": run_id, "source_node_run_id": node.id,
                "source_evidence": original, "state_sha256": state_hash,
                "historical_failure": node.error_message, "replayed_tool": key,
                "replayed_historical_arguments": arguments,
                "registered_tools": sorted(registered), "feedback": result,
                "unavailable_action_executed": False, "state_unchanged": True,
                "source_database_unchanged": True, "model_requests_sent": 0, "online_database_writes": 0,
                "verification_limit": "真实状态与旧调用参数回放，不代表模型自然完成整条生成链路"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError(options.output)
    report = asyncio.run(verify(options.run_id))
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "source_run_id", "state_unchanged", "model_requests_sent")}, ensure_ascii=False))
