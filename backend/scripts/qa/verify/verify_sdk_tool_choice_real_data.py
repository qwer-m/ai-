"""离线回放真实支持复核响应，核查自动工具协议、严格提交和安全诊断；不请求模型。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper
from agents.exceptions import ModelBehaviorError
from agents.items import ModelResponse, ToolCallOutputItem
from agents.models.chatcmpl_converter import Converter
from agents.run_internal.turn_resolution import check_for_final_output_from_tools
from agents.tool import FunctionToolResult
from agents.tool_context import ToolContext
from agents.usage import deserialize_usage
from openai.types.responses import ResponseOutputItem
from pydantic import TypeAdapter
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, ObservedRunHooks
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, StructuredOutputJSONError, StructuredOutputValidationError,
    ToolArgumentsJSONError, ToolArgumentsValidationError, _model_settings, _output_submission_tool,
)
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions


async def verify(options):
    source_bytes = options.source.read_bytes()
    source = json.loads(source_bytes)
    transcript = options.source.with_suffix(".requests.jsonl")
    transcript_bytes = transcript.read_bytes()
    events = [json.loads(line) for line in transcript_bytes.decode("utf-8").splitlines()]
    requests = [event for event in events if event["event"] == "sdk_request_content"]
    responses = [event["response"] for event in events if event["event"] == "sdk_response_content"]
    assert source["status"] == "contract_passed" and len(requests) == len(responses) == 2
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, source["source_run_id"])
            run_hash = action_state_hash([run.run_context, run.input_payload, run.output_payload, run.status])
            definitions, _, _ = _capture_current_definitions(db, run, "test_planning_route_gap_reviewer")
            definition, _ = definitions["test_planning_route_gap_reviewer"]
            current_config = deepcopy(definition.runtime_config)
            assert current_config["tool_choice"] == "auto"
            assert current_config["extra_body"]["thinking"]["type"] == "enabled"
            settings = _model_settings(current_config)
            assert Converter.convert_tool_choice(settings.tool_choice) == "auto"
            historical_config = source["definition_snapshots"][0]["current_runtime_config"]
            assert "tool_choice" not in historical_config
            assert _model_settings(historical_config).tool_choice == "required"
            rejected_configs = []
            # 反事实仅变更真实配置的协议选项，业务输入与模型响应保持原样。
            for choice in ("none", "", None, ["auto"]):
                candidate = {**current_config, "tool_choice": choice}
                try:
                    _model_settings(candidate)
                except ValueError as error:
                    rejected_configs.append({"tool_choice": choice, "error": str(error)})
                else:
                    raise AssertionError("无效或禁止终止提交的工具配置未被拒绝")
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key="sdk-tool-choice-offline-replay", run_input=deepcopy(run.input_payload))
            contract = AgentOutputContract(deepcopy(source["input"]),
                AgentMapConfig(item_postprocessor="testing.postprocess_planning_route_repair_item"))
            budget = AutonomousBudgetHooks(source["request_budget"])
            loop = AutonomousToolLoop(terminal_tool_names={"submit_result"},
                validate_submission=partial(contract.validate, definition, context),
                recoverable_errors=(ToolArgumentsJSONError, ToolArgumentsValidationError,
                    StructuredOutputJSONError, StructuredOutputValidationError, OutputValidationError),
                max_tool_calls=source["request_budget"]["attempted_requests"], budget_snapshot=budget.snapshot)
            tool = loop.wrap_tool(_output_submission_tool(definition, context), allow_feedback=True)
            agent = Agent(name=definition.name, instructions=definition.instructions, tools=[tool],
                model_settings=settings, tool_use_behavior=loop.tool_use_behavior, reset_tool_choice=False)
            wrapper = RunContextWrapper(context=context)
            diagnostics = ModelRequestDiagnostics()
            hooks = ObservedRunHooks(diagnostics, budget)
            checkpoints = []
            for index, (request, saved_response) in enumerate(zip(requests, responses)):
                try:
                    loop.require_submission()
                except ModelBehaviorError:
                    pass
                else:
                    raise AssertionError("无成功提交回执却被验收通过")
                await hooks.on_llm_start(wrapper, agent, request["system_prompt"], request["input_items"])
                output = TypeAdapter(list[ResponseOutputItem]).validate_python(saved_response["output"])
                response = ModelResponse(output=output, usage=deserialize_usage(saved_response["usage"]),
                    response_id=saved_response["response_id"], request_id=saved_response["request_id"])
                await hooks.on_llm_end(wrapper, agent, response)
                converted = Converter.items_to_messages(saved_response["output"],
                    model=source["model_requests"][index]["model"]["name"], strict_feature_validation=False)
                calls = [item for item in saved_response["output"] if item["type"] == "function_call"]
                converted_calls = [call for message in converted for call in message.get("tool_calls", [])]
                assert len(calls) == len(converted_calls) == 1
                call, converted_call = calls[0], converted_calls[0]
                assert converted_call["id"] == call["call_id"]
                assert converted_call["function"] == {"name": call["name"], "arguments": call["arguments"]}
                result = await tool.on_invoke_tool(ToolContext(context=context, tool_name=call["name"],
                    tool_call_id=call["call_id"], tool_arguments=call["arguments"]), call["arguments"])
                sdk_result = FunctionToolResult(tool=tool, output=result, run_item=ToolCallOutputItem(
                    agent=agent, output=result, raw_item={"type": "function_call_output",
                        "call_id": call["call_id"], "output": result}))
                decision = await check_for_final_output_from_tools(agent, [sdk_result], wrapper)
                assert decision.is_final_output == bool(index)
                record = diagnostics.requests[-1]
                assert record["model_settings"] == {"tool_choice": "auto", "thinking_type": "enabled",
                    "reasoning_effort": current_config.get("extra_args", {}).get("reasoning_effort")}
                assert set(record["model_settings"]) == {"tool_choice", "thinking_type", "reasoning_effort"}
                assert record["sdk_response_reasoning_item_count"] == sum(item["type"] == "reasoning" for item in saved_response["output"])
                assert record["usage"] == {key: saved_response["usage"][key] for key in record["usage"]}
                checkpoints.append({"call_id": call["call_id"], "submission_status": loop.events[-1]["status"],
                    "is_final_output": decision.is_final_output, "actual_arguments_unchanged": True})
            assert [item["submission_status"] for item in checkpoints] == ["rejected", "accepted"]
            assert loop.require_submission().normalized_output == source["normalized_output"]
            assert budget.snapshot()["reported_usage"] == source["model_usage"]
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            assert run_hash == action_state_hash([run.run_context, run.input_payload, run.output_payload, run.status])
            result = {"status": "passed", "model_requests_sent": 0, "online_database_writes": 0,
                "source_report": str(options.source.resolve()), "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "source_transcript_sha256": hashlib.sha256(transcript_bytes).hexdigest(),
                "production_sha256": {name: hashlib.sha256((Path(__file__).resolve().parents[3] / "modules/agent_platform" / name).read_bytes()).hexdigest()
                    for name in ("sdk_adapter.py", "request_diagnostics.py")},
                "sdk_versions": {name: importlib.metadata.version(name) for name in ("openai", "openai-agents")},
                "explicit_auto_selected": True, "historical_default_required_preserved": True,
                "invalid_config_rejections": rejected_configs, "tool_protocol_replay": checkpoints,
                "strict_terminal_submission_preserved": True, "current_diagnostics_replayed": diagnostics.requests,
                "replayed_usage": budget.snapshot(),
                "historical_reasoning_items": sum(item["type"] == "reasoning" for response in responses for item in response["output"]),
                "reasoning_replay_verified": False,
                "scope": "当前auto协议及安全诊断作用于真实历史响应的离线回放；历史响应未启用推理，没有合成reasoning，也没有证明网关接受新参数或业务语义已修复。"}
        finally:
            db.rollback()
    assert options.source.read_bytes() == source_bytes and transcript.read_bytes() == transcript_bytes
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError(options.output)
    result = asyncio.run(verify(options))
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "report": str(options.output.resolve()), "model_requests_sent": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
