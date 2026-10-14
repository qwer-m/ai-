"""只读真实检查点，重放已验收修复，验证范围快照仅在领域状态变化后压缩。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper
from agents.items import ItemHelpers
from agents.run_config import RunConfig
from agents.run_internal.turn_preparation import maybe_filter_model_input
from agents.tool_context import ToolContext
from sqlalchemy.orm import Session

from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentRunEvent, AgentToolDefinition
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_review_agent as review
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import model_request_context_bytes
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, _PersistentStateInputFilter, _function_tool, _model_settings,
    _revalidate_managed_agent_call, _runner_input, _validate_runtime_config,
)
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from scripts.qa.verify.verify_persistent_input_filter_real_data import compact, items_for_receipt, sqlite_engine


def definition_for(key):
    spec = next(value for value in BUILTIN_AGENT_SPECS if value["agent_key"] == key)
    return AgentDefinition(**{name: deepcopy(value) for name, value in spec.items() if name != "version"})


def tool_context(context, receipt):
    return ToolContext(context=context, tool_name=receipt["tool_key"],
                       tool_call_id=receipt["tool_call_id"], tool_arguments=receipt["arguments"]["arguments_json"])


async def verify(source_report: Path, output: Path):
    paths = [source_report, source_report.with_suffix(".sqlite"), source_report.with_suffix(".checkpoint.json")]
    production = [Path(__file__).resolve().parents[3] / "modules/agent_platform" / name
                  for name in ("sdk_adapter.py", "test_generation_agent.py", "test_generation_builtin_specs.py")]
    source_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    production_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in production}
    report = json.loads(source_report.read_text(encoding="utf-8"))
    saved = json.loads(paths[2].read_text(encoding="utf-8"))["checkpoint"]
    parent = definition_for("test_generation_coordinator")
    config = deepcopy(parent.runtime_config)
    assert config["state_snapshot_tools"] == ["inspect_generation_scope"]
    _validate_runtime_config(config, has_tools=True)
    bound_tools = [AgentToolDefinition(**{key: deepcopy(value) for key, value in spec.items() if key != "version"})
                   for spec in BUILTIN_TOOL_SPECS if spec["tool_key"] in config["tool_keys"]]
    inspect_definition = next(value for value in bound_tools if value.tool_key == "inspect_generation_scope")
    engine = sqlite_engine(paths[1])
    with Session(engine) as db:
        run = db.get(AgentRun, report["source_run_id"])
        node = db.get(AgentNodeRun, report["isolated_node_run_id"])
        assert node.sdk_state["action_checkpoint"] == saved
        # 按持久事件编号取真实先后顺序，不依赖 JSON 对象的键顺序。
        events = db.query(AgentRunEvent).order_by(AgentRunEvent.id).all()
        receipts = saved["receipts"]
        ordered = [receipts[event.payload["tool_call_id"]] for event in events
                   if event.event_type in {"agent_action_completed", "agent_action_failed"}]
        repairs = [value for value in ordered if value["tool_key"] == "repair_review_batch"]
        inspections = [value for value in ordered if value["tool_key"] == "inspect_generation_scope"
                       and value["status"] == "completed"]
        assert len(inspections) == 2 and len(repairs) == 3
        target = inspections[-1]
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
            action_input=deepcopy(node.input_payload), action_state=deepcopy(saved["state"]),
            artifacts=deepcopy(run.run_context["artifacts"]))
        # review.input 保留真实初审输入；领域初始化器重建首次修复之前的精确状态。
        initial_state = deepcopy(saved["state"])
        child = review.compose_review_context(context, input_payload=initial_state["review"]["input"])
        initial_state["review"]["state"] = deepcopy(child.action_state)
        initial_state["review_domains"][action_state_hash(initial_state["review"]["input"])] = deepcopy(child.action_state)
        generation._save(context, initial_state)
        initial_hash = action_state_hash(context.action_state)
        assert initial_hash == target["pre_state_hash"] == target["post_state_hash"] == repairs[0]["pre_state_hash"]
        historical = generation.observe_generation_state(context)
        initial = _runner_input(execution_context=context, input_payload=historical, runtime_config=config)
        state_filter = _PersistentStateInputFilter(context=context, runtime_config=config)
        state_filter.bind_initial_input(initial, historical)
        baseline_config = {key: deepcopy(value) for key, value in config.items() if key != "state_snapshot_tools"}
        baseline_filter = _PersistentStateInputFilter(context=context, runtime_config=baseline_config)
        baseline_filter.bind_initial_input(initial, historical)
        tool = _function_tool(inspect_definition, context, state_input_filter=state_filter)
        actual_output = await tool.on_invoke_tool(tool_context(context, target), target["arguments"]["arguments_json"])
        assert json.loads(actual_output) == json.loads(target["result"]["output"])
        assert action_state_hash(context.action_state) == initial_hash
        assert state_filter._snapshots[target["tool_call_id"]]["domain_sha256"] == initial_hash
        unconfigured_tool = _function_tool(inspect_definition, context, state_input_filter=baseline_filter)
        assert await unconfigured_tool.on_invoke_tool(tool_context(context, target), target["arguments"]["arguments_json"]) == actual_output
        assert not baseline_filter._snapshots and not baseline_filter._observations

        # 重放原失败参数，让真实工具及既有反馈包装器生成拒绝原文；不构造模型响应。
        rejected = next(value for value in ordered if value["tool_key"] == "inspect_generation_scope"
                        and value["status"] == "failed")
        contract = AgentOutputContract(deepcopy(context.action_input))
        loop = AutonomousToolLoop(terminal_tool_names=set(config["stop_at_tool_keys"]),
            validate_submission=partial(contract.validate, parent, context),
            recoverable_errors=(OutputValidationError,), max_tool_calls=report["request_reservation"]["attempted_requests"])
        wrapped = loop.wrap_tool(tool, allow_feedback=True)
        marks_before = deepcopy(state_filter._snapshots)
        rejected_output = await wrapped.on_invoke_tool(tool_context(context, rejected), rejected["arguments"]["arguments_json"])
        assert json.loads(rejected_output)["feedback"] == rejected["error"]["feedback"]
        assert state_filter._snapshots == marks_before and not state_filter._observations
        assert action_state_hash(context.action_state) == initial_hash
        rejected_pair = items_for_receipt({**deepcopy(rejected), "result": {"output": rejected_output}})
        # 首次读取没有本过滤器的可信标记，必须保留；第二次是真实 _function_tool 登记。
        inputs = [*ItemHelpers.input_to_new_input_list(initial), *items_for_receipt(inspections[0]),
                  *rejected_pair, *items_for_receipt(target)]
        original_inputs = deepcopy(inputs)
        agent = Agent(name=parent.name, instructions=parent.instructions,
            tools=[_function_tool(value, context) for value in bound_tools],
            model_settings=_model_settings(config, use_json_object_output=False, disable_model_thinking=False))
        wrapper = RunContextWrapper(context)

        async def project(filter_value, values):
            return await maybe_filter_model_input(agent=agent,
                run_config=RunConfig(call_model_input_filter=filter_value), context_wrapper=wrapper,
                input_items=values, system_instructions=parent.instructions)

        same_version = await project(state_filter, inputs)
        assert same_version.input == inputs
        target_index = next(index for index, item in enumerate(inputs) if item.get("type") == "function_call_output"
                            and item.get("call_id") == target["tool_call_id"])
        # 负向校验只改变真实读取副本中的索引；既有记录或领域状态均不得被修改。
        tampered = deepcopy(inputs)
        damaged_output = json.loads(tampered[target_index]["output"])
        damaged_output["item_index"] += 1
        tampered[target_index]["output"] = compact(damaged_output)
        try:
            await project(state_filter, tampered)
        except ValueError:
            pass
        else:
            raise AssertionError("当前版本已登记读取的内容被篡改后仍被接受")
        assert action_state_hash(context.action_state) == initial_hash

        replayed = []
        repair_definition = definition_for("test_generation_batch_repairer")
        for receipt in repairs:
            assert action_state_hash(context.action_state) == receipt["pre_state_hash"]
            arguments = json.loads(receipt["arguments"]["arguments_json"])
            action = review.prepare_review_repair(child, arguments)
            outputs = []
            for index, item in enumerate(action["items"]):
                record = next(value for value in saved["validated_agent_calls"].values()
                    if value["parent_tool_call_id"] == receipt["tool_call_id"]
                    and value["identity"]["agent_key"] == repair_definition.agent_key
                    and value["identity"]["input_hash"] == action_state_hash(item))
                assert record["identity"]["execution_semantic_hash"] == saved["execution_semantic_hash"]
                from modules.agent_platform.contracts import AgentMapConfig

                repair_contract = AgentOutputContract(item,
                    AgentMapConfig(item_postprocessor="testing.postprocess_final_review_repair_item"),
                    candidate_validator=partial(review.validate_review_repair_candidate, child, action, index))
                outputs.append(_revalidate_managed_agent_call(saved=record, contract=repair_contract,
                                                              definition=repair_definition, context=child))
            review.apply_review_repair(child, action, outputs)
            next_state = deepcopy(context.action_state)
            next_state["review"]["state"] = deepcopy(child.action_state)
            next_state["review_domains"][action_state_hash(next_state["review"]["input"])] = deepcopy(child.action_state)
            generation._save(context, next_state)
            assert action_state_hash(context.action_state) == receipt["post_state_hash"]
            observation = generation.observe_generation_state(context)
            assert observation == json.loads(receipt["result"]["output"])
            for filter_value in (state_filter, baseline_filter):
                filter_value.record_result(tool_key=receipt["tool_key"], tool_call_id=receipt["tool_call_id"],
                    result=observation, observation=deepcopy(observation), arguments=arguments)
            inputs.extend(items_for_receipt(receipt))
            filtered = await project(state_filter, inputs)
            expired = json.loads(filtered.input[target_index]["output"])
            assert expired["status"] == "historical_state_snapshot"
            assert expired["domain_sha256"] == initial_hash
            assert expired["arguments"] == json.loads(target["arguments"]["arguments_json"])
            assert expired["output_sha256"] == action_state_hash(json.loads(actual_output))
            assert json.loads(filtered.input[0]["content"]) == observation
            replayed.append({"batch_id": arguments["batch_id"], "tool_call_id": receipt["tool_call_id"],
                             "pre_state_hash": receipt["pre_state_hash"], "post_state_hash": receipt["post_state_hash"],
                             "validated_candidates": len(outputs)})
        assert context.action_state == saved["state"]
        current_scope = generation.inspect_generation_scope(context, json.loads(target["arguments"]["arguments_json"]))
        old_cases = json.loads(actual_output)["review_scopes"][0]["input"]["test_cases"]
        new_cases = current_scope["review_scopes"][0]["input"]["test_cases"]
        changed_ids = [before["case_id"] for before, after in zip(old_cases, new_cases, strict=True) if before != after]
        assert changed_ids and current_scope["review_scopes"][0]["status"] == "awaiting_recheck"
        assert current_scope["review_scopes"][0]["review_result"] is None
        assert filtered.input[1:5] == inputs[1:5]
        assert inputs[:len(original_inputs)] == original_inputs
        assert len(filtered.input) == len(inputs)
        for before, after in zip(inputs[1:], filtered.input[1:], strict=True):
            assert {key: value for key, value in before.items() if key != "output"} == {
                key: value for key, value in after.items() if key != "output"}
        baseline = await project(baseline_filter, inputs)
        differences = [index for index, pair in enumerate(zip(baseline.input, filtered.input, strict=True)) if pair[0] != pair[1]]
        assert differences == [target_index]
        before_bytes = model_request_context_bytes(agent=agent, system_prompt=parent.instructions, input_items=baseline.input)
        after_bytes = model_request_context_bytes(agent=agent, system_prompt=parent.instructions, input_items=filtered.input)
        old_output_bytes = len(actual_output.encode("utf-8"))
        new_output_bytes = len(filtered.input[target_index]["output"].encode("utf-8"))
        # SDK 输入将工具 output 作为 JSON 字符串再次编码，所以另报嵌入后的精确差值。
        embedded_before = len(compact(baseline.input[target_index]["output"]).encode("utf-8"))
        embedded_after = len(compact(filtered.input[target_index]["output"]).encode("utf-8"))
        assert before_bytes - after_bytes == embedded_before - embedded_after
        try:
            await project(state_filter, tampered)
        except ValueError:
            pass
        else:
            raise AssertionError("过期的已登记读取被篡改后仍被接受")
        restarted = _PersistentStateInputFilter(context=context, runtime_config=config)
        restarted.bind_initial_input(initial, historical)
        restored_view = await project(restarted, inputs)
        assert restored_view.input[1:] == inputs[1:]
        assert json.loads(restored_view.input[0]["content"]) == generation.observe_generation_state(context)
        assert not restarted._snapshots and not restarted._observations
        assert context.action_state == saved["state"] and node.sdk_state["action_checkpoint"] == saved
        assert not db.new and not db.dirty and not db.deleted
    engine.dispose()
    assert source_hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert production_hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in production}
    result = {
        "status": "passed", "source_report": str(source_report.resolve()), "source_hashes": source_hashes,
        "production_hashes": production_hashes, "source_run_id": report["source_run_id"],
        "source_initial_requests": report["initial_state"]["source_budget"]["charged"]["attempted_requests"],
        "source_reported_requests": report["model_usage"]["requests"],
        "inspected_tool_call_id": target["tool_call_id"], "inspected_arguments": json.loads(target["arguments"]["arguments_json"]),
        "replayed_validated_repairs": replayed, "changed_case_ids": changed_ids,
        "same_version_exact_output_preserved": True, "changed_version_snapshot_expired": True,
        "unconfigured_read_and_unknown_historical_read_preserved": True,
        "real_rejected_inspect_not_registered_and_preserved": True,
        "tampered_known_output_rejected_before_and_after_change": True,
        "current_observation_exact": True, "all_call_output_pairs_and_order_preserved": True,
        "restart_unknown_marks_preserved": True, "source_state_budget_logs_unchanged": True,
        "reconstructed_sdk_items": len(inputs), "current_envelope_before_bytes": before_bytes,
        "current_envelope_after_bytes": after_bytes, "exact_snapshot_projection_saved_bytes": before_bytes - after_bytes,
        "snapshot_output_before_bytes": old_output_bytes, "snapshot_output_after_bytes": new_output_bytes,
        "snapshot_embedded_before_bytes": embedded_before, "snapshot_embedded_after_bytes": embedded_after,
        "model_requests_sent": 0, "online_database_reads": 0, "database_writes": 0,
        "verification_limits": [
            "真实初审输入重建的领域状态及三次修复前后摘要均与历史回执完全一致；修复使用已保存候选重新完整验收，未调用模型",
            "只重组两次真实读取、一条由原失败参数重现的拒绝及三次真实修复，不冒充完整历史 SDK 会话",
            "字节数为当前实际工具声明下的 SDK 上下文 JSON；仅比较同一输入中第二次读取的投影变化，不推算实际模型 token 或网关报文",
            "当前版本不去重；重启后没有可信内存登记的历史读取保持原文",
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
    print(json.dumps({key: result[key] for key in ("status", "exact_snapshot_projection_saved_bytes",
        "snapshot_output_before_bytes", "snapshot_output_after_bytes", "model_requests_sent")}, ensure_ascii=False))
