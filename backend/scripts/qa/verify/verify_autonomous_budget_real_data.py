"""只读回放真实 Agent 配置、输入和已记录用量，验证共享预算；不调用模型。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import RunConfig, RunContextWrapper, trace
from agents.exceptions import UserError
from agents.items import ItemHelpers
from agents.run_internal.agent_bindings import bind_public_agent
from agents.run_internal.run_steps import ToolRunFunction
from agents.run_internal.tool_execution import execute_function_tool_calls
from agents.tool_context import ToolContext
from agents.usage import deserialize_usage
from openai.types.responses import ResponseFunctionToolCall
from sqlalchemy import text

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform.autonomous_budget import (
    AgentExecutionBudgetExceeded,
    AutonomousBudgetHooks,
    model_request_reservation,
)
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_request_reservation, _is_retryable_agent_error
from modules.agent_platform.sdk_adapter import _build_sdk_agent, _runner_input


async def verify(db, run: AgentRun) -> dict:
    assert RunContextWrapper.__hash__ is object.__hash__
    assert ToolContext.__hash__ is object.__hash__
    repo = AgentRunRepository(db)
    definitions = AgentDefinitionRepository(db)
    node = next(
        item for item in db.query(AgentNodeRun).filter(AgentNodeRun.run_id == run.id).all()
        if item.node_key == "generation" and item.status == "success"
    )
    definition = db.get(AgentDefinition, node.agent_definition_id)
    child_key = definition.runtime_config["subagent_keys"][0]
    child_definition = definitions.get_agent(project_id=run.project_id, agent_key=child_key)
    tools = definitions.list_agent_tools(definition.id, project_id=run.project_id)
    workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
    graph = parse_execution_definition(workflow.definition)
    model_node = next(item for item in graph.nodes if item.node_key == node.node_key)
    node_input = repo.read_node_input(node)
    states = node.sdk_state["items"]
    single_request = next(item for item in states if item.get("usage", {}).get("requests") == 1)
    source = deepcopy(node_input[model_node.map_config.items_key][single_request["item_index"]])
    observation = next(
        call["result"] for item in states for call in item.get("tool_calls", [])
        if call["tool_key"] == "submit_scenario_design_guidance"
    )
    context = ToolExecutionContext(
        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
        node_key=node.node_key, run_input=deepcopy(run.input_payload),
        artifacts=deepcopy(run.run_context.get("artifacts") or {}),
    )
    clients = []
    try:
        client = get_client_for_user(run.user_id, db)
        agent, _ = _build_sdk_agent(
            db=db, client=client, agent_definition=definition,
            execution_context=context, allocated_clients=clients, allow_subagents=True,
            trusted_input_payload=source, model_input_payload=source,
        )
        child, _ = _build_sdk_agent(
            db=db, client=client, agent_definition=child_definition,
            execution_context=context, allocated_clients=clients, allow_subagents=False,
        )
        root_context = RunContextWrapper(context=context)
        child_context = RunContextWrapper(context=context)
        prompt = await agent.get_system_prompt(root_context)
        child_prompt = await child.get_system_prompt(child_context)
        inputs = ItemHelpers.input_to_new_input_list(_runner_input(
            execution_context=context, input_payload=source, runtime_config=dict(definition.runtime_config),
        ))
        reservation = _agent_request_reservation(definition=definition, tools=tools, input_payload=source)
        estimate = model_request_reservation(agent=agent, system_prompt=prompt, input_items=inputs)
        recorded_usage = deserialize_usage(single_request["usage"])

        hooks = AutonomousBudgetHooks(reservation)
        root_observer, child_observer = hooks.observe, hooks.observe
        observed_root, _ = _build_sdk_agent(
            db=db, client=client, agent_definition=definition, execution_context=context,
            allocated_clients=clients, allow_subagents=True, budget_observer=hooks.observe,
            trusted_input_payload=source, model_input_payload=source,
        )
        observed_child, _ = _build_sdk_agent(
            db=db, client=client, agent_definition=child_definition, execution_context=context,
            allocated_clients=clients, allow_subagents=False, budget_observer=hooks.observe,
        )
        observed_prompt_requests = []

        async def check_dynamic_prompts() -> None:
            current = hooks.observe()
            encoded = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
            for observed, wrapper in ((observed_root, root_context), (observed_child, child_context)):
                assert callable(observed.instructions)
                assert encoded in await observed.get_system_prompt(wrapper)
            observed_prompt_requests.append(current["remaining"]["attempted_requests"])

        await check_dynamic_prompts()
        initial_observation = root_observer()
        assert initial_observation["scope"] == "shared_root_and_specialist_model_calls"
        assert initial_observation["remaining"] == reservation
        await hooks.on_llm_start(root_context, agent, prompt, inputs)
        hooks.settle_request(root_context, recorded_usage)
        assert hooks.snapshot()["charged"]["input_tokens"] == recorded_usage.input_tokens
        assert hooks.snapshot()["charged"]["output_tokens"] == recorded_usage.output_tokens
        after_root_observation = child_observer()
        assert after_root_observation["charged"]["attempted_requests"] == 1
        assert after_root_observation["remaining"]["input_tokens"] == reservation["input_tokens"] - recorded_usage.input_tokens
        await check_dynamic_prompts()
        await hooks.on_llm_start(child_context, child, child_prompt, inputs)
        child_snapshot = hooks.snapshot()
        assert child_snapshot["charged"]["attempted_requests"] == 2
        assert child_snapshot["pending_requests"] == 1
        after_child_observation = root_observer()
        assert after_child_observation == child_observer()
        assert after_child_observation["remaining"]["attempted_requests"] == reservation["attempted_requests"] - 2
        assert after_child_observation["pending_requests"] == 1
        await check_dynamic_prompts()
        before_observation = hooks.snapshot()
        detached_observation = hooks.observe()
        detached_observation["charged"]["attempted_requests"] = 0
        detached_observation["remaining"]["attempted_requests"] = reservation["attempted_requests"]
        assert hooks.snapshot() == before_observation
        hooks.settle_request(child_context, None)
        assert hooks.snapshot()["charged"] == child_snapshot["charged"]
        assert hooks.observe()["remaining"] == after_child_observation["remaining"]
        await check_dynamic_prompts()

        partial = AutonomousBudgetHooks(reservation)
        await partial.on_llm_start(root_context, agent, prompt, inputs)
        missing_input_usage = dict(single_request["usage"])
        missing_input_usage.pop("input_tokens")
        partial.settle_request(root_context, deserialize_usage(missing_input_usage))
        assert partial.snapshot()["charged"]["input_tokens"] == estimate["input_tokens"]

        limited = AutonomousBudgetHooks({**reservation, "attempted_requests": 1})
        await limited.on_llm_start(root_context, agent, prompt, inputs)
        limited.settle_request(root_context, recorded_usage)
        before = limited.snapshot()
        try:
            await limited.on_llm_start(child_context, child, child_prompt, inputs)
        except AgentExecutionBudgetExceeded:
            pass
        else:
            raise AssertionError("父调用后仍允许子调用越过共享请求上限")
        assert limited.snapshot() == before
        assert limited.observe()["remaining"]["attempted_requests"] == 0

        # 直接执行已记录参数对应的真实 SDK 工具链；预算在子模型发请求前拒绝。
        prior_child_call = next(
            call for item in states for call in item.get("tool_calls", [])
            if call["tool_key"] == f"agent:{child_key}"
        )
        guarded_agent, _ = _build_sdk_agent(
            db=db, client=client, agent_definition=definition,
            execution_context=context, allocated_clients=clients, allow_subagents=True,
            trusted_input_payload=source, model_input_payload=source,
            budget_hooks=limited,
        )
        guarded_tool = next(tool for tool in guarded_agent.tools if tool.name == child_key)
        replay_call = ResponseFunctionToolCall(
            type="function_call", call_id=str(uuid4()), name=child_key,
            arguments=json.dumps({"input": json.dumps(prior_child_call["arguments"], ensure_ascii=False)}),
        )
        with trace("真实数据预算拒绝验证", disabled=True):
            try:
                await execute_function_tool_calls(
                    bindings=bind_public_agent(guarded_agent),
                    tool_runs=[ToolRunFunction(tool_call=replay_call, function_tool=guarded_tool)],
                    hooks=limited, context_wrapper=root_context,
                    config=RunConfig(tracing_disabled=True, trace_include_sensitive_data=False),
                )
            except UserError as error:
                assert isinstance(error.__cause__, AgentExecutionBudgetExceeded)
                assert _is_retryable_agent_error(error) is False
            else:
                raise AssertionError("真实 SDK 子 Agent 工具吞掉了预算拒绝")
        assert limited.snapshot() == before

        expanded = [*inputs, {"role": "user", "content": json.dumps(observation, ensure_ascii=False)}]
        larger = model_request_reservation(agent=agent, system_prompt=prompt, input_items=expanded)
        assert larger["input_tokens"] > estimate["input_tokens"]
        input_limited = AutonomousBudgetHooks({**reservation, "input_tokens": estimate["input_tokens"]})
        try:
            await input_limited.on_llm_start(root_context, agent, prompt, expanded)
        except AgentExecutionBudgetExceeded:
            pass
        else:
            raise AssertionError("新增真实观察后仍允许越过输入预算")
        assert input_limited.snapshot()["charged"]["attempted_requests"] == 0

        pending = AutonomousBudgetHooks(reservation)
        await pending.on_llm_start(root_context, agent, prompt, inputs)
        await pending.on_llm_start(child_context, child, child_prompt, inputs)
        assert pending.snapshot()["pending_requests"] == 2
        assert pending.snapshot()["charged"]["attempted_requests"] == 2
        saved = pending.snapshot()
        resumed_hooks = AutonomousBudgetHooks(reservation, checkpoint=saved)
        resumed = resumed_hooks.snapshot()
        assert resumed["charged"] == saved["charged"]
        assert resumed["pending_requests"] == 0 and resumed["interrupted_requests"] == 2
        resumed_observation = resumed_hooks.observe()
        assert resumed_observation["remaining"] == pending.observe()["remaining"]
        assert resumed_observation["interrupted_requests"] == 2
        assert resumed_observation["pending_requests"] == 0
        damaged = []
        missing = deepcopy(saved)
        missing.pop("charged")
        damaged.append(missing)
        for container, key, value in (
            ("charged", "input_tokens", -1),
            ("charged", "input_tokens", str(saved["charged"]["input_tokens"])),
            ("limits", "attempted_requests", True),
            ("charged", "total_tokens", 0),
        ):
            candidate = deepcopy(saved)
            candidate[container][key] = value
            damaged.append(candidate)
        counts = deepcopy(saved)
        counts["completed_requests"] = saved["charged"]["attempted_requests"] + 1
        damaged.append(counts)
        for candidate in damaged:
            try:
                AutonomousBudgetHooks(reservation, checkpoint=candidate)
            except ValueError:
                pass
            else:
                raise AssertionError("预算检查点损坏后被归零或继续执行")
        return {
            "run_id": run.id,
            "node_key": node.node_key,
            "agents": [definition.agent_key, child_key],
            "checks": [
                "真实单请求用量增量结算", "父子共享请求总额", "缺失用量保留预留",
                "缺失用量分项不当作零消耗退款",
                "请求越额发送前拒绝且不修改计数", "真实观察增长纳入输入预算",
                "未完成父子请求同时占用额度", "真实SDK父子上下文按对象身份可作键",
                "真实SDK子工具预算拒绝包装为UserError且不可重试",
                "恢复未完成请求保留占用且损坏检查点拒绝退款",
                "父子动态观察共享余额，缺失用量与恢复均不退款",
                "修改观察结果不能篡改预算，额度耗尽显示零剩余请求",
                "实际SDK父子动态instructions按每次调用读取同一账本的最新余额",
            ],
            "sdk_prompt_remaining_requests": observed_prompt_requests,
            "budget_observations": {
                "before_root": initial_observation,
                "after_root": after_root_observation,
                "after_child_started": after_child_observation,
                "after_restore": resumed_observation,
            },
            "model_requests_sent": 0,
            "database_writes": 0,
        }
    finally:
        await asyncio.gather(*(client.close() for client in clients))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if options.output is not None and options.output.exists():
        raise FileExistsError("验证报告已存在，请指定新路径保留原证据")
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, options.run_id)
        if run is None or run.status != "success":
            raise ValueError("需要包含真实用例生成及单请求用量的成功运行")
        report = asyncio.run(verify(db, run))
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    if options.output is not None:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
