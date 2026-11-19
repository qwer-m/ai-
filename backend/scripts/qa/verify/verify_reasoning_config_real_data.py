"""用已有运行的真实输入验证思考配置和 SDK 请求，不执行工具、不改写运行状态。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.models.interface import ModelTracing
from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun
from modules.agent_platform.input_snapshots import restore_node_input
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import safe_exception_evidence
from modules.agent_platform.runtime import _project_agent_map_input
from modules.agent_platform.sdk_adapter import _build_sdk_agent, _parse_function_arguments


async def verify(run_id: int, node_key: str, output: Path, item_index: int | None = None) -> None:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if run is None:
            raise ValueError("运行不存在")
        node = db.query(AgentNodeRun).filter_by(run_id=run_id, node_key=node_key).order_by(
            AgentNodeRun.id.desc(),
        ).first()
        if node is None:
            raise ValueError("节点不存在")
        definition = db.get(AgentDefinition, node.agent_definition_id)
        restored = restore_node_input(node, lambda key: db.get(AgentNodeRun, key))
        if item_index is None:
            item_index = (node.sdk_state or {}).get("failed_item", {}).get("item_index", 0)
        raw_input = restored["items"][item_index] if "items" in restored else restored
        model_input, _ = _project_agent_map_input(definition=definition, raw_item=raw_input)
        client = get_client_for_user(run.user_id, db)
        routes = {}
        for route, provider, model in (
            ("main", client.provider, client.model), ("turbo", client.turbo_provider, client.turbo_model),
            ("review", client.review_provider, client.review_model), ("vision", client.vl_provider, client.vl_model),
        ):
            routes[route] = {"model": model, "effort": getattr(provider, "reasoning_effort", None)}
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                       run_id=run.id, node_key=node.node_key, run_input=run.input_payload)
        allocated = []
        agent = None
        try:
            agent, _ = _build_sdk_agent(
                db=db, client=client, agent_definition=definition, execution_context=context,
                trusted_input_payload=raw_input, model_input_payload=model_input,
                allow_subagents=False, allocated_clients=allocated,
            )
            settings = agent.model_settings
            assert "thinking" not in (settings.extra_body or {})
            assert "thinking" not in (settings.extra_args or {})
            response = await agent.model.get_response(
                system_instructions=agent.instructions, input=json.dumps(model_input, ensure_ascii=False),
                model_settings=settings, tools=agent.tools, output_schema=None, handoffs=[],
                tracing=ModelTracing.DISABLED,
            )
            tool_calls = [item for item in response.output if item.type == "function_call"]
            assert tool_calls, "真实请求没有返回预期的工具调用"
            tools_by_name = {tool.name: tool for tool in agent.tools}
            for call in tool_calls:
                _parse_function_arguments(call.name, tools_by_name[call.name].params_json_schema,
                                          call.arguments)
            result = {
                "run_id": run_id, "node_key": node_key, "item_index": item_index,
                "routes_from_saved_config": routes,
                "sdk_settings": {"extra_body": settings.extra_body, "extra_args": settings.extra_args},
                "response_received": True, "tool_calls": [item.name for item in tool_calls],
                "tool_arguments_schema_valid": True,
                "tool_strict_modes": {tool.name: tool.strict_json_schema for tool in agent.tools},
                "usage": {key: getattr(response.usage, key) for key in
                          ("requests", "input_tokens", "output_tokens", "total_tokens")},
                "tools_executed": False, "run_status_modified": False,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
        except Exception as error:
            failure = {
                "run_id": run_id, "node_key": node_key, "item_index": item_index,
                "routes_from_saved_config": routes,
                "sdk_settings": {"extra_body": agent.model_settings.extra_body,
                                 "extra_args": agent.model_settings.extra_args} if agent is not None else {},
                "response_received": False, "exception_chain": safe_exception_evidence(error),
                "tools_executed": False, "run_status_modified": False,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        finally:
            for http_client in allocated:
                await http_client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--node-key", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--item-index", type=int, help="指定真实输入项，默认复验失败项")
    args = parser.parse_args()
    asyncio.run(verify(args.run_id, args.node_key, args.output, args.item_index))
