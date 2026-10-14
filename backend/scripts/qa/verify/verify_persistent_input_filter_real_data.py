"""只读真实会话及 SQLite，验证持久父 Agent 的确定性状态输入投影。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper, Runner
from agents.items import ItemHelpers
from agents.models.fake_id import FAKE_RESPONSES_ID
from agents.run_config import CallModelData, ModelInputData, RunConfig
from agents.run_internal.turn_preparation import maybe_filter_model_input
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.db.model_defs import AgentNodeRun, AgentRun, AgentToolDefinition
from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AgentExecutionBudgetExceeded, AutonomousBudgetHooks, model_request_context_bytes
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, ObservedRunHooks
from modules.agent_platform.sdk_adapter import _PersistentStateInputFilter, _function_tool, _model_settings, _runner_input, _sdk_model
from modules.agent_platform.test_generation_agent import observe_generation_state
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sqlite_engine(path):
    return create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(
        "file:" + path.resolve().as_posix() + "?mode=ro", uri=True,
    ))


def items_for_receipt(receipt):
    call = ResponseFunctionToolCall(id=FAKE_RESPONSES_ID, call_id=receipt["tool_call_id"],
        arguments=receipt["arguments"]["arguments_json"], name=receipt["tool_key"], type="function_call")
    return [call.model_dump(exclude_unset=True), ItemHelpers.tool_call_output_item(call, receipt["result"]["output"])]


async def verify(source_report, output):
    source_sqlite = source_report.with_suffix(".sqlite")
    source_checkpoint = source_report.with_suffix(".checkpoint.json")
    paths = [source_report, source_sqlite, source_checkpoint]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = json.loads(source_report.read_text(encoding="utf-8"))
    expected = json.loads(source_checkpoint.read_text(encoding="utf-8"))["checkpoint"]
    assert report["status"] == "passed" and report["model_usage"]["requests"] == 52
    parent_spec = next(value for value in BUILTIN_AGENT_SPECS if value["agent_key"] == "test_generation_coordinator")
    config = parent_spec["runtime_config"]
    engine = sqlite_engine(source_sqlite)
    with Session(engine) as db:
        run = db.get(AgentRun, report["source_run_id"])
        node = db.get(AgentNodeRun, report["isolated_node_run_id"])
        assert node.sdk_state["action_checkpoint"] == expected
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
            action_input=deepcopy(node.input_payload), action_state=deepcopy(expected["state"]),
            artifacts=deepcopy(run.run_context["artifacts"]))
        current = observe_generation_state(context)
        assert current["completed"] and current["can_finish"]
        historical = report["invocation_initial_observation"]
        initial = _runner_input(execution_context=context, input_payload=historical, runtime_config=config)
        state_filter = _PersistentStateInputFilter(context=context, runtime_config=config)
        state_filter.bind_initial_input(initial, historical)
        inputs = ItemHelpers.input_to_new_input_list(initial)
        call_logs = {item["tool_call_id"]: item for item in report["tool_calls"] if item.get("tool_call_id")}
        # 这些真实动作在当次代码与日志中均返回完整领域观察；不将任意相似 JSON 猜成观察。
        observer_actions = {"generate_cases", "review_generated_cases", "repair_review_batch",
                            "recheck_review_batch", "review_all_cases"}
        marked = []
        for receipt in expected["receipts"].values():
            assert receipt["status"] == "completed"
            saved = call_logs[receipt["tool_call_id"]]
            assert json.loads(receipt["arguments"]["arguments_json"]) == saved["arguments"]
            assert json.loads(receipt["result"]["output"]) == saved["result"]
            if receipt["tool_key"] == "finish_generation":
                break
            inputs.extend(items_for_receipt(receipt))
            if receipt["tool_key"] in observer_actions:
                observation = saved["result"]
                assert observation["goal"]["source_ref"] == historical["goal"]["source_ref"]
                assert set(observation) == set(historical)
                state_filter.record_result(tool_key=receipt["tool_key"], tool_call_id=receipt["tool_call_id"],
                                           result=observation, observation=deepcopy(observation))
                marked.append(receipt["tool_call_id"])
        original_inputs = deepcopy(inputs)
        assert len(inputs) == report["model_requests"][-1]["sdk_input_item_count"] == 35
        bound_tools = [AgentToolDefinition(**{key: deepcopy(value) for key, value in spec.items()
                      if key not in {"version"}}) for spec in BUILTIN_TOOL_SPECS if spec["tool_key"] in config["tool_keys"]]
        agent = Agent(name=parent_spec["name"], instructions=parent_spec["instructions"],
                      tools=[_function_tool(tool, context) for tool in bound_tools],
                      model_settings=_model_settings(config, use_json_object_output=False, disable_model_thinking=False))
        instructions = parent_spec["instructions"]
        wrapper = RunContextWrapper(context)
        filtered = await maybe_filter_model_input(agent=agent, run_config=RunConfig(call_model_input_filter=state_filter),
            context_wrapper=wrapper, input_items=inputs, system_instructions=instructions)
        assert inputs == original_inputs
        assert json.loads(filtered.input[0]["content"]) == current
        assert len(filtered.input) == len(inputs)
        for before, after in zip(inputs[1:], filtered.input[1:], strict=True):
            if before["type"] != "function_call_output" or before["call_id"] not in marked:
                assert before == after
            else:
                assert before["call_id"] == after["call_id"]
                assert json.loads(after["output"])["observation_sha256"] == action_state_hash(json.loads(before["output"]))
        inspect_receipt = next(value for value in expected["receipts"].values() if value["tool_key"] == "inspect_generation_scope")
        inspect_tool = next(value for value in bound_tools if value.tool_key == "inspect_generation_scope")
        # 当前候选读取单独验证，不能用旧调用 ID 将其登记到历史输入的过滤器中。
        inspection_filter = _PersistentStateInputFilter(context=context, runtime_config=config)
        actual_tool = _function_tool(inspect_tool, context, state_input_filter=inspection_filter)
        inspect_output = await actual_tool.on_invoke_tool(ToolContext(context=context,
            tool_name=inspect_tool.tool_key, tool_call_id=inspect_receipt["tool_call_id"],
            tool_arguments=inspect_receipt["arguments"]["arguments_json"]), inspect_receipt["arguments"]["arguments_json"])
        assert json.loads(inspect_output)["input"] == call_logs[inspect_receipt["tool_call_id"]]["result"]["input"]
        assert inspect_receipt["tool_call_id"] not in inspection_filter._observations
        # 当前 observer 的完整真值可登记，不相等的真实范围读取结果不能冒充观察。
        real_last = next(value for value in reversed(list(expected["receipts"].values())) if value["tool_key"] == "review_all_cases")
        proof_filter = _PersistentStateInputFilter(context=context, runtime_config=config)
        proof_filter.record_result(tool_key=real_last["tool_key"], tool_call_id=real_last["tool_call_id"],
                                   result=current, observation=observe_generation_state(context))
        assert real_last["tool_call_id"] in proof_filter._observations
        # 旧会话没有父层失败；独立组合其真实专业拒绝反馈，验证未知 payload 原样保留。
        rejected = next(event for call in report["tool_calls"] for event in call.get("tool_observations", [])
                        if event["status"] == "rejected")
        candidate_call = next(submit for call in report["tool_calls"] for submit in call.get("submissions", [])
                              if submit["tool_call_id"] == rejected["tool_call_id"])
        rejected_pair = items_for_receipt({"tool_call_id": rejected["tool_call_id"], "tool_key": rejected["tool_key"],
            "arguments": {"arguments_json": compact(candidate_call["arguments"])},
            "result": {"output": compact({"status": "rejected", "feedback": rejected["feedback"]})}})
        with_feedback = await maybe_filter_model_input(agent=agent, run_config=RunConfig(call_model_input_filter=state_filter),
            context_wrapper=wrapper, input_items=[*inputs, *rejected_pair], system_instructions=instructions)
        assert with_feedback.input[-2:] == rejected_pair
        plain = await maybe_filter_model_input(agent=agent, run_config=RunConfig(), context_wrapper=wrapper,
                                               input_items=inputs, system_instructions=instructions)
        assert plain.input == inputs
        before_bytes = model_request_context_bytes(agent=agent, system_prompt=instructions, input_items=inputs)
        after_bytes = model_request_context_bytes(agent=agent, system_prompt=instructions, input_items=filtered.input)
        delta = before_bytes - after_bytes
        hooks = AutonomousBudgetHooks(report["request_reservation"])
        diagnostics = ModelRequestDiagnostics()
        observed = ObservedRunHooks(diagnostics, hooks)
        await observed.on_llm_start(wrapper, agent, filtered.instructions, filtered.input)
        assert diagnostics.requests[0]["measured_context_bytes"] == after_bytes
        assert hooks.snapshot()["charged"]["input_tokens"] == after_bytes + 8192
        # 实际 Runner 路径使用真实用户模型客户端，在请求前由真实预算拒绝，禁止模型联网。
        from core.db.model_defs import AgentDefinition

        allocated = []
        with SessionLocal() as online:
            online.execute(text("SET TRANSACTION READ ONLY"))
            definition = AgentDefinition(**{key: deepcopy(value) for key, value in parent_spec.items() if key != "version"})
            agent.model = _sdk_model(get_client_for_user(run.user_id, online), definition,
                                    request_timeout_seconds=30, allocated_clients=allocated)
            blocked = AutonomousBudgetHooks({**report["request_reservation"], "input_tokens": 1})
            try:
                await Runner.run(agent, input=inputs, context=context,
                    run_config=RunConfig(call_model_input_filter=state_filter, tracing_disabled=True), hooks=blocked)
            except AgentExecutionBudgetExceeded:
                assert blocked.snapshot()["charged"]["attempted_requests"] == 0
            else:
                raise AssertionError("实际 SDK Runner 未在模型请求前被预算阻断")
            finally:
                await asyncio.gather(*(client.close() for client in allocated))
            assert not online.new and not online.dirty and not online.deleted
        assert not db.new and not db.dirty and not db.deleted
        assert context.action_state == expected["state"]
        state_hash = action_state_hash(context.action_state)
        compacted_hash = action_state_hash(filtered.input)
    engine.dispose()
    # 使用新连接读取同一持久状态，不携带内存压缩标记仍获得当前完整观察。
    reopened = sqlite_engine(source_sqlite)
    with Session(reopened) as db:
        run = db.get(AgentRun, report["source_run_id"])
        node = db.get(AgentNodeRun, report["isolated_node_run_id"])
        restored = replace(context, db=db, action_state=deepcopy(node.sdk_state["action_checkpoint"]["state"]),
                           tool_calls=[], executed_tools=[])
        latest = observe_generation_state(restored)
        restart_filter = _PersistentStateInputFilter(context=restored, runtime_config=config)
        restart_input = _runner_input(execution_context=restored, input_payload=latest, runtime_config=config)
        restart_filter.bind_initial_input(restart_input, latest)
        new = restart_filter(CallModelData(agent=agent, context=restored,
            model_data=ModelInputData(instructions=instructions, input=ItemHelpers.input_to_new_input_list(restart_input))))
        assert json.loads(new.input[0]["content"]) == current
        assert action_state_hash(restored.action_state) == state_hash and not restart_filter._observations
    reopened.dispose()
    assert hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    result = {
        "status": "passed", "source_report": str(source_report.resolve()), "source_sha256": hashes,
        "source_real_model_requests": 52, "source_parent_requests": 18,
        "reconstructed_sdk_items": len(inputs), "historical_full_observations_replaced": len(marked),
        "current_observation_exact": True, "original_tool_call_pairs_unchanged": True,
        "real_inspected_scope_preserved": True, "real_specialist_rejection_preserved_in_separate_composition": True,
        "journal_tool_logs_and_source_database_unchanged": True, "sqlite_closed_and_reopened": True,
        "child_without_filter_unchanged": True, "budget_and_diagnostics_measure_filtered_input": True,
        "actual_runner_filter_context_validated_before_network": True,
        "unfiltered_current_envelope_bytes": before_bytes, "filtered_current_envelope_bytes": after_bytes,
        "exact_input_projection_saved_bytes": delta,
        "observed_historical_last_request_bytes": 489480,
        "historical_envelope_minus_projection_delta_bytes": 489480 - delta,
        "historical_tail_reduction_percent": round(delta / 489480 * 100, 2),
        "filtered_input_sha256": compacted_hash,
        "model_requests_sent": 0, "database_writes": 0,
        "verification_limits": [
            "基于真实52请求的journal和工具日志重组35项输入，未重新执行模型或旧领域变更链",
            "历史完整观察的标记回放依据真实动作产物及当次日志，未向旧journal补写压缩标记",
            "当前领域状态为已完成检查点；历史尾部估计仅替换输入投影差值，不声称恢复原系统提示及SDK所有内部字段",
            "专业拒绝反馈为单独的真实记录组合，原52请求父层没有自然失败回执",
            "保留全部非观察输出，本轮不裁剪原始取证、问题或失败内容",
        ],
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("验证报告已存在，禁止覆盖")
    result = asyncio.run(verify(args.source_report, args.output))
    print(json.dumps({key: result[key] for key in ("status", "exact_input_projection_saved_bytes",
        "historical_envelope_minus_projection_delta_bytes", "historical_tail_reduction_percent")}, ensure_ascii=False))
