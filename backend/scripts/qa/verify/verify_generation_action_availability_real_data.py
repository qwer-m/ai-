"""只读真实隔离运行，重验原始候选、合法行动目录与稳定的 SDK 工具注册。"""

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

from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent, AgentToolDefinition
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_review_agent as review
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, ToolArgumentsValidationError, _PersistentStateInputFilter, _function_tool,
    _parse_function_arguments, _revalidate_managed_agent_call, _runner_input,
    _validate_runtime_config,
)
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS
from modules.agent_platform.test_generation_escalation import validate_generation_scope_feedback
from scripts.qa.verify.verify_persistent_input_filter_real_data import sqlite_engine
from scripts.qa.verify.verify_state_snapshot_filter_real_data import definition_for


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_bundle(report_path):
    paths = [report_path, report_path.with_suffix(".sqlite"), report_path.with_suffix(".checkpoint.json")]
    return {
        "paths": paths,
        "hashes": {str(path.resolve()): digest(path) for path in paths},
        "report": json.loads(report_path.read_text(encoding="utf-8")),
        "checkpoint": json.loads(paths[2].read_text(encoding="utf-8"))["checkpoint"],
    }


def context_for(db, bundle, state=None):
    report = bundle["report"]
    run = db.get(AgentRun, report["source_run_id"])
    node = db.get(AgentNodeRun, report["isolated_node_run_id"])
    assert node.sdk_state["action_checkpoint"] == bundle["checkpoint"]
    context = ToolExecutionContext(
        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
        node_key=node.node_key, run_input=deepcopy(run.input_payload),
        action_input=deepcopy(node.input_payload),
        action_state=deepcopy(state if state is not None else bundle["checkpoint"]["state"]),
        artifacts=deepcopy(run.run_context["artifacts"]),
    )
    assert context.invoke_agent is None and context.action_journal is None
    return context, run, node


def ordered_receipts(db, checkpoint):
    receipts = checkpoint["receipts"]
    events = db.query(AgentRunEvent).order_by(AgentRunEvent.id).all()
    return [receipts[event.payload["tool_call_id"]] for event in events
            if event.event_type in {"agent_action_completed", "agent_action_failed"}]


def candidate_for(checkpoint, receipt, agent_key, item):
    matches = [value for value in checkpoint["validated_agent_calls"].values()
               if value["parent_tool_call_id"] == receipt["tool_call_id"]
               and value["identity"]["agent_key"] == agent_key
               and value["identity"]["input_hash"] == action_state_hash(item)]
    assert len(matches) == 1
    saved = matches[0]
    assert saved["identity"]["execution_semantic_hash"] == checkpoint["execution_semantic_hash"]
    assert saved["raw_output_hash"] == action_state_hash(saved["raw_output"])
    assert saved["output_hash"] == action_state_hash(saved["output"])
    return saved


def revalidate(checkpoint, receipt, context, agent_key, item, postprocessor, *, validator=None, alternate=None):
    contract = AgentOutputContract(
        deepcopy(item), AgentMapConfig(item_postprocessor=postprocessor),
        candidate_validator=validator, alternate_output_validators=alternate,
    )
    saved = candidate_for(checkpoint, receipt, agent_key, item)
    output = _revalidate_managed_agent_call(
        saved=saved, contract=contract, definition=definition_for(agent_key), context=context,
    )
    assert output == saved["output"]
    return output


async def expect_rejected(callback, *, expected_type=OutputValidationError):
    try:
        result = callback()
        if hasattr(result, "__await__"):
            await result
    except expected_type as error:
        return {"error_type": type(error).__name__, "message": str(error),
                "details": deepcopy(getattr(error, "details", None))}
    raise AssertionError("当前非法行动必须被真实守卫拒绝")


async def profiled_tools(agent, wrapper):
    """用解释器计数真实 observer 调用，不替换 observer 或任何模型执行器。"""
    calls = 0
    code = generation.observe_generation_state.__code__
    prior = sys.getprofile()

    def count(frame, event, arg):
        nonlocal calls
        if event == "call" and frame.f_code is code:
            calls += 1
        if prior is not None:
            prior(frame, event, arg)

    sys.setprofile(count)
    try:
        enabled = await agent.get_all_tools(wrapper)
    finally:
        sys.setprofile(prior)
    return {tool.name for tool in enabled}, calls


async def verify(source_path, seed_path, completed_path, output_path):
    source, seed, completed = [source_bundle(path) for path in (source_path, seed_path, completed_path)]
    production_paths = [Path(__file__).resolve().parents[3] / "modules/agent_platform" / name for name in (
        "sdk_adapter.py", "test_generation_agent.py", "test_generation_builtin_specs.py",
    )]
    production_hashes = {str(path): digest(path) for path in production_paths}
    definition = definition_for("test_generation_coordinator")
    config = deepcopy(definition.runtime_config)
    assert config.get("state_action_catalog") == "available_actions", "生产目录桥接尚未就绪，不运行部分验收"
    _validate_runtime_config(config, has_tools=True)
    definitions = {spec["tool_key"]: AgentToolDefinition(**{
        key: deepcopy(value) for key, value in spec.items() if key != "version"
    }) for spec in BUILTIN_TOOL_SPECS if spec["tool_key"] in config["tool_keys"]}
    assert set(definitions) == set(config["tool_keys"])
    checkpoint = source["checkpoint"]
    assert source["report"]["source_run_id"] == seed["report"]["source_run_id"] == 38
    stages, replayed, rejected, schema_replays = [], [], {}, []
    engine = sqlite_engine(source["paths"][1])
    seed_engine = sqlite_engine(seed["paths"][1])
    completed_engine = sqlite_engine(completed["paths"][1])
    try:
        with Session(engine) as db, Session(seed_engine) as seed_db, Session(completed_engine) as done_db:
            seed_context, _, _ = context_for(seed_db, seed)
            context, _, node = context_for(db, source, seed_context.action_state)
            assert context.action_input == seed_context.action_input
            assert context.artifacts == seed_context.artifacts
            persisted_hash = action_state_hash(node.sdk_state)
            ordered = ordered_receipts(db, checkpoint)
            first_success = next(r for r in ordered if r["status"] == "completed")
            assert action_state_hash(context.action_state) == first_success["pre_state_hash"]
            state_filter = _PersistentStateInputFilter(context=context, runtime_config=config)
            initial_observation = generation.observe_generation_state(context)
            initial = _runner_input(execution_context=context, input_payload=initial_observation, runtime_config=config)
            state_filter.bind_initial_input(initial, initial_observation)
            tools = {key: _function_tool(value, context, state_input_filter=state_filter)
                     for key, value in definitions.items()}
            agent = Agent(name=definition.name, instructions=definition.instructions, tools=list(tools.values()))
            wrapper = RunContextWrapper(context)

            async def check_stage(label, *, changed=False):
                before = action_state_hash(context.action_state)
                visible, observed_calls = await profiled_tools(agent, wrapper)
                assert observed_calls == 0, "SDK 工具查询不应重复计算领域观察"
                observation = generation.observe_generation_state(context)
                catalog = observation["available_actions"]
                assert set(catalog) <= visible == set(tools)
                assert all(isinstance(value, dict) for value in catalog.values())
                again, cached_calls = await profiled_tools(agent, wrapper)
                assert again == visible and cached_calls == 0
                assert all(tool.is_enabled is True for tool in tools.values())
                filtered = await maybe_filter_model_input(
                    agent=agent, run_config=RunConfig(call_model_input_filter=state_filter),
                    context_wrapper=wrapper, input_items=ItemHelpers.input_to_new_input_list(initial),
                    system_instructions=definition.instructions,
                )
                model_observation = json.loads(filtered.input[0]["content"])
                assert model_observation["available_actions"] == catalog
                # 已完成的生成包仍然是合法读取目标，不能误过滤为 pending 集合。
                if context.action_state["generation_inputs"]:
                    assert catalog["inspect_generation_scope"]["item_indexes"] == list(range(len(context.action_state["generation_inputs"])))
                    for index in catalog["inspect_generation_scope"]["item_indexes"]:
                        read = generation.inspect_generation_scope(context, {"item_index": index, "problem_id": None})
                        assert read["item_index"] == index
                for problem_id in catalog.get("inspect_generation_scope", {}).get("problem_ids", []):
                    generation.inspect_generation_scope(context, {"item_index": None, "problem_id": problem_id})
                if not observation["completed"]:
                    assert {"read_planning_evidence", "revise_planning_draft", "rebuild_business_plan"} <= set(catalog)
                    assert catalog["revise_planning_draft"]["item_indexes"] == list(range(len(context.action_state["planning"]["planning_inputs"])))
                for action_key in catalog:
                    generation._require_action(context, action_key)
                for action_key in set(tools) - set(catalog):
                    await expect_rejected(lambda key=action_key: generation._require_action(context, key))
                if "generate_cases" in catalog:
                    assert catalog["generate_cases"]["item_indexes"] == observation["generation"]["pending_indexes"]
                if "review_generated_cases" in catalog:
                    assert catalog["review_generated_cases"]["item_indexes"] == observation["pending_initial_review_indexes"]
                    generation.prepare_initial_review_action(context, {"item_indexes": catalog["review_generated_cases"]["item_indexes"]})
                if context.action_state["review"]:
                    child = generation._review_context(context, context.action_state)
                    for batch_id in catalog.get("repair_review_batch", {}).get("batch_ids", []):
                        review.prepare_review_repair(child, {"batch_id": batch_id})
                    for batch_id in catalog.get("recheck_review_batch", {}).get("batch_ids", []):
                        review.prepare_review_recheck(child, {"batch_id": batch_id})
                assert before == action_state_hash(context.action_state)
                stages.append({"label": label, "domain_sha256": before, "changed_since_previous": changed,
                               "available_actions": deepcopy(catalog), "sdk_tool_names": sorted(visible),
                               "first_lookup_observer_calls": observed_calls, "cached_lookup_observer_calls": cached_calls})
                return observation

            await check_stage("真实局部失败后仍待生成")
            assert "generate_cases" in stages[-1]["available_actions"]
            assert "reinspect_planning_source" in stages[-1]["available_actions"]
            # 复放历史全部合法参数，不修改旧回执以迁就新 Schema。
            for receipt in ordered:
                if receipt["status"] == "completed":
                    key = receipt["tool_key"]
                    args = _parse_function_arguments(key, tools[key].params_json_schema, receipt["arguments"]["arguments_json"])
                    assert args == json.loads(receipt["arguments"]["arguments_json"])
                    schema_replays.append({"tool_call_id": receipt["tool_call_id"], "tool_key": key})
            invalid_inspect = next(r for r in ordered if r["tool_key"] == "inspect_generation_scope" and r["status"] == "failed")
            rejected["historical_null_null_schema"] = await expect_rejected(
                lambda: _parse_function_arguments("inspect_generation_scope", tools["inspect_generation_scope"].params_json_schema,
                                                  invalid_inspect["arguments"]["arguments_json"]),
                expected_type=ToolArgumentsValidationError,
            )
            # 同时保留领域 XOR 守卫，历史直接调用入口不能绕过。
            rejected["historical_null_null_domain"] = await expect_rejected(
                lambda: generation.inspect_generation_scope(context, json.loads(invalid_inspect["arguments"]["arguments_json"])))
            schema = tools["inspect_generation_scope"].params_json_schema
            assert len(schema["anyOf"]) == 2 and all(set(branch["required"]) == {"item_index", "problem_id"} for branch in schema["anyOf"])

            for receipt in ordered:
                key, status = receipt["tool_key"], receipt["status"]
                args = json.loads(receipt["arguments"]["arguments_json"])
                if status != "completed" or key == "inspect_generation_scope":
                    continue
                assert action_state_hash(context.action_state) == receipt["pre_state_hash"]
                outputs = []
                if key == "generate_cases":
                    action = generation.prepare_generation_action(context, args)
                    for item, request in zip(action["items"], action["requests"], strict=True):
                        outputs.append(revalidate(checkpoint, receipt, context, "test_case_generator", request,
                            "testing.postprocess_generation_batch_item",
                            alternate={"planning_feedback": partial(validate_generation_scope_feedback, item)}))
                    generation.apply_generation_action(context, action, outputs)
                elif key == "review_generated_cases":
                    action = generation.prepare_initial_review_action(context, args)
                    outputs = [revalidate(checkpoint, receipt, context, "test_generation_final_reviewer", item,
                                          "testing.postprocess_final_review_batch_item") for item in action["items"]]
                    generation.apply_initial_review_action(context, action, outputs)
                elif key == "repair_review_batch":
                    child = generation._review_context(context, context.action_state)
                    action = review.prepare_review_repair(child, args)
                    for index, item in enumerate(action["items"]):
                        outputs.append(revalidate(checkpoint, receipt, child, "test_generation_batch_repairer", item,
                            "testing.postprocess_final_review_repair_item",
                            validator=partial(review.validate_review_repair_candidate, child, action, index)))
                    review.apply_review_repair(child, action, outputs)
                    state = deepcopy(context.action_state)
                    state["review"]["state"] = deepcopy(child.action_state)
                    state["review_domains"][action_state_hash(state["review"]["input"])] = deepcopy(child.action_state)
                    generation._save(context, state)
                else:
                    raise AssertionError(f"未声明的真实状态变更: {key}")
                assert action_state_hash(context.action_state) == receipt["post_state_hash"]
                replayed.append({"tool_key": key, "tool_call_id": receipt["tool_call_id"],
                                 "pre_state_hash": receipt["pre_state_hash"], "post_state_hash": receipt["post_state_hash"],
                                 "revalidated_raw_candidates": len(outputs)})
                observation = await check_stage(f"{key}:{receipt['tool_call_id']}", changed=True)
                if key == "generate_cases" and not observation["generation"]["pending_indexes"]:
                    assert "generate_cases" not in observation["available_actions"]
                    assert "review_generated_cases" in observation["available_actions"]
                    duplicate = next(r for r in ordered if r["tool_key"] == "generate_cases" and r["status"] == "failed")
                    rejected["historical_completed_generation_targets"] = await expect_rejected(
                        lambda: generation.prepare_generation_action(context, json.loads(duplicate["arguments"]["arguments_json"])))
                if key == "review_generated_cases":
                    assert "review_generated_cases" not in observation["available_actions"]
                    assert "repair_review_batch" in observation["available_actions"]
                    assert "review_all_cases" not in observation["available_actions"]
                    early = next(r for r in ordered if r["tool_key"] == "review_all_cases" and r["status"] == "failed")
                    rejected["historical_early_global_direct_tool"] = await expect_rejected(lambda: tools["review_all_cases"].on_invoke_tool(
                        ToolContext(context=context, tool_name=early["tool_key"], tool_call_id=early["tool_call_id"],
                                    tool_arguments=early["arguments"]["arguments_json"]), early["arguments"]["arguments_json"]))
                if key == "repair_review_batch":
                    catalog = observation["available_actions"]
                    assert args["batch_id"] in catalog["recheck_review_batch"]["batch_ids"]
                    assert args["batch_id"] not in catalog.get("repair_review_batch", {}).get("batch_ids", [])
                    assert "review_all_cases" not in catalog
            assert context.action_state == checkpoint["state"]
            assert len(replayed) == 17 and sum(r["revalidated_raw_candidates"] for r in replayed) == 27
            assert len(context.action_state["review"]["state"]["pending_rechecks"]) == 3

            # 未声明动态目录的现有 Agent 维持 SDK 默认工具可见，不猜测业务阶段。
            unconfigured = {key: deepcopy(value) for key, value in config.items() if key != "state_action_catalog"}
            unfiltered = _PersistentStateInputFilter(context=context, runtime_config=unconfigured)
            plain_agent = Agent(name=definition.name, tools=[_function_tool(value, context, state_input_filter=unfiltered)
                                                          for value in definitions.values()])
            assert {tool.name for tool in await plain_agent.get_all_tools(wrapper)} == set(definitions)
            # 非法配置与实际观察不匹配只破坏身份边界，不生成业务事实或模型结果。
            rejected["invalid_config_type"] = await expect_rejected(
                lambda: _validate_runtime_config({**config, "state_action_catalog": []}, has_tools=True), expected_type=ValueError)
            missing_filter = _PersistentStateInputFilter(context=context, runtime_config={**config, "state_action_catalog": "missing_action_catalog"})
            rejected["missing_catalog"] = await expect_rejected(missing_filter.observe, expected_type=ValueError)
            subset_config = {**config, "tool_keys": [key for key in config["tool_keys"] if key != "read_planning_evidence"]}
            unknown_filter = _PersistentStateInputFilter(context=context, runtime_config=subset_config)
            rejected["catalog_key_not_bound"] = await expect_rejected(unknown_filter.observe, expected_type=ValueError)
            assert action_state_hash(node.sdk_state) == persisted_hash

            # 真正已完成的另一个真实运行用于验证终止后只读目录及原完成契约。
            done_context, _, done_node = context_for(done_db, completed)
            done_saved_hash = action_state_hash(done_node.sdk_state)
            assert done_context.action_state["completed"]
            done_filter = _PersistentStateInputFilter(context=done_context, runtime_config=config)
            done_agent = Agent(name=definition.name, tools=[_function_tool(value, done_context, state_input_filter=done_filter)
                                                         for value in definitions.values()])
            done_catalog = generation.observe_generation_state(done_context)["available_actions"]
            assert set(done_catalog) == {"inspect_generation_scope"}
            assert {tool.name for tool in await done_agent.get_all_tools(RunContextWrapper(done_context))} == set(definitions)
            for key in set(definitions) - set(done_catalog):
                await expect_rejected(lambda key=key: generation._require_action(done_context, key))
            done_index = done_catalog["inspect_generation_scope"]["item_indexes"][0]
            generation.inspect_generation_scope(done_context, {"item_index": done_index, "problem_id": None})
            # 历史完成态仍含已删除的 coverage_slots，只验证目录和旧执行契约拒绝，不改写历史来制造通过。
            projected = deepcopy(done_context.action_state)
            generation._prepare_generation(done_context, projected)
            historical_without_slots = deepcopy(done_context.action_state["generation_inputs"])
            for item in historical_without_slots:
                assert item["case_fact_contract"].pop("coverage_slots")
            assert projected["generation_inputs"] == historical_without_slots
            assert projected["effective_facts"] == done_context.action_state["effective_facts"]
            completed_validation = await expect_rejected(lambda: generation.validate_generation_resolution(done_context, {
                "input_payload": deepcopy(done_context.action_input), "output": {"completed": True},
            }), expected_type=ValueError)
            assert completed_validation["message"] == "生成输入没有对应当前规划和权威版本"
            assert action_state_hash(done_node.sdk_state) == done_saved_hash
            for session in (db, seed_db, done_db):
                assert not session.new and not session.dirty and not session.deleted
    finally:
        engine.dispose()
        seed_engine.dispose()
        completed_engine.dispose()
    assert production_hashes == {str(path): digest(path) for path in production_paths}
    for bundle in (source, seed, completed):
        assert bundle["hashes"] == {str(path.resolve()): digest(path) for path in bundle["paths"]}
    result = {
        "status": "passed", "source_run_id": 38,
        "sources": [bundle["hashes"] for bundle in (source, seed, completed)],
        "production_hashes": production_hashes, "stages": stages, "replayed_actions": replayed,
        "historical_schema_replays": schema_replays, "real_invalid_calls_rejected": rejected,
        "completed_run_id": completed["report"]["source_run_id"],
        "completed_actions": done_catalog, "historical_completed_contract_rejection": completed_validation,
        "sdk_registered_tools_stable": True, "domain_rejects_unavailable_actions": True,
        "current_model_observation_matches_catalog": True,
        "completed_generation_ranges_remain_inspectable": True,
        "proactive_draft_revision_and_rebuild_remain_available": True,
        "unconfigured_sdk_tools_remain_visible": True,
        "all_persisted_states_receipts_budgets_sources_unchanged": True,
        "model_requests_sent": 0, "online_database_reads": 0, "database_writes": 0,
        "verification_limits": [
            "只读历史 SQLite，以当前完整合同重新验收真实原始候选，并在内存回放领域动作；没有替换 invoke_agent",
            "目录表示存在合法目标，参数的完整请求去重、Schema 与领域验收仍独立执行",
            "Run36 历史完成态只验证只读目录；完整契约因旧 coverage_slots 而拒绝，不冒充当前身份交付或 SDK 终止恢复成功",
            "没有调用新模型，也不证明模型会选择最优动作或自然收敛",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, default=root / "output/qa/2026-09-23-run38-local-failure-real-model.json")
    parser.add_argument("--seed-report", type=Path, default=root / "output/qa/2026-09-23-run38-local-failure-diagnostic-prepared.json")
    parser.add_argument("--completed-report", type=Path, default=root / "output/qa/2026-09-23-unified-generation-run36-selected-real-model.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("报告已存在，禁止覆盖")
    result = asyncio.run(verify(args.source_report, args.seed_report, args.completed_report, args.output))
    print(json.dumps({"status": result["status"], "stages": len(result["stages"]),
                      "replayed_actions": len(result["replayed_actions"]), "model_requests_sent": 0,
                      "output": str(args.output.resolve())}, ensure_ascii=False))
