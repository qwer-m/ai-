"""只读回放真实工具参数，验证 SDK 观察、拒绝、修正和成功提交闭环，不调用模型。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
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

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentToolDefinition, AgentWorkflowDefinition
from modules.agent_platform.autonomous_tools import AutonomousToolLimitError, AutonomousToolLoop
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.output_repair import OutputRepairError, OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract,
    OutputPostprocessingError,
    StructuredOutputJSONError,
    StructuredOutputValidationError,
    ToolArgumentsJSONError,
    ToolArgumentsValidationError,
    ToolOutputValidationError,
    _agent_consultation_question,
    _function_tool,
)


def missing_fact_candidate(source: dict, original: dict, validate) -> tuple[dict, str]:
    """只删除真实候选的事实引用，选择仍符合结构但违反完整覆盖的反例。"""
    for fact_id in source["case_fact_contract"]["required_fact_ids"]:
        candidate = deepcopy(original)
        required_groups = []
        optional_groups = []
        for case in candidate["test_cases"]:
            required_groups.extend(item["fact_ids"] for item in case["preconditions"])
            required_groups.extend(step["fact_bindings"]["expected"] for step in case["steps"])
            optional_groups.extend(step["fact_bindings"]["action"] for step in case["steps"])
            optional_groups.append(case["test_input"]["fact_ids"])
        if any(group == [fact_id] for group in required_groups):
            continue
        for group in [*required_groups, *optional_groups]:
            group[:] = [value for value in group if value != fact_id]
        for case in candidate["test_cases"]:
            if not case["test_input"]["fact_ids"]:
                case["test_input"]["text"] = ""
        try:
            validate(candidate)
        except OutputPostprocessingError as error:
            cause = error.__cause__
            if isinstance(cause, OutputRepairError) and cause.details.get("missing_fact_ids") == [fact_id]:
                return candidate, fact_id
    raise AssertionError("真实批次没有适合删除引用后验证业务反馈的事实")


async def replay(db, run: AgentRun, node: AgentNodeRun) -> dict:
    repo = AgentRunRepository(db)
    definitions = AgentDefinitionRepository(db)
    definition = db.get(AgentDefinition, node.agent_definition_id)
    workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
    graph = parse_execution_definition(workflow.definition)
    model_node = next(item for item in graph.nodes if item.node_key == node.node_key)
    node_input = repo.read_node_input(node)
    state = next(item for item in node.sdk_state["items"] if item["item_index"] == 0)
    calls = {call["tool_key"]: call for call in state["tool_calls"]}
    original = deepcopy(calls["submit_generation_batch"]["arguments"])
    observation = deepcopy(calls["submit_scenario_design_guidance"]["arguments"])
    source = deepcopy(node_input[model_node.map_config.items_key][0])
    expected = next(item["output"] for item in node.output_payload["items"] if item["item_index"] == 0)
    context = ToolExecutionContext(
        db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
        node_key=node.node_key, run_input=deepcopy(run.input_payload),
        artifacts=deepcopy(run.run_context.get("artifacts") or {}),
    )

    contract = AgentOutputContract(deepcopy(source), model_node.map_config)

    def validate(candidate: dict) -> dict:
        return contract.validate(definition, context, candidate)

    assert validate(deepcopy(original)) == expected
    candidate, fact_id = missing_fact_candidate(source, original, validate)
    recoverable = (
        ToolArgumentsJSONError, ToolArgumentsValidationError, StructuredOutputJSONError,
        StructuredOutputValidationError, OutputValidationError,
    )

    def make_loop(budget: int) -> AutonomousToolLoop:
        return AutonomousToolLoop(
            terminal_tool_names={"submit_generation_batch"}, validate_submission=validate,
            recoverable_errors=recoverable, max_tool_calls=budget,
        )

    def build_tool(key: str, loop: AutonomousToolLoop, *, allow_feedback: bool = True):
        tool = definitions.get_tool(project_id=run.project_id, tool_key=key)
        if tool is None:
            raise AssertionError(f"真实工具定义不存在: {key}")
        return loop.wrap_tool(_function_tool(tool, context), allow_feedback=allow_feedback)

    loop = make_loop(4)
    submit = build_tool("submit_generation_batch", loop)
    observe = build_tool("submit_scenario_design_guidance", loop, allow_feedback=False)
    agent = Agent(
        name=definition.name, instructions=definition.instructions,
        tools=[submit, observe], tool_use_behavior=loop.tool_use_behavior, reset_tool_choice=False,
    )
    call_sequence = 0

    async def invoke(tool, arguments, *, owner=agent):
        nonlocal call_sequence
        call_sequence += 1
        encoded = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
        call_id = f"replay-node-{node.id}-call-{call_sequence}"
        tool_context = ToolContext(
            context=context, tool_name=tool.name, tool_call_id=call_id, tool_arguments=encoded,
        )
        result = await tool.on_invoke_tool(tool_context, encoded)
        sdk_result = FunctionToolResult(
            tool=tool, output=result,
            run_item=ToolCallOutputItem(
                agent=owner, output=result,
                raw_item={"type": "function_call_output", "call_id": call_id, "output": result},
            ),
        )
        decision = await check_for_final_output_from_tools(owner, [sdk_result], RunContextWrapper(context))
        return result, decision, sdk_result

    try:
        loop.require_submission()
    except ModelBehaviorError:
        pass
    else:
        raise AssertionError("没有验收回执不能通过正文完成任务")
    _, decision, _ = await invoke(observe, observation)
    assert not decision.is_final_output
    _, decision, _ = await invoke(observe, observation)
    assert not decision.is_final_output
    rejected, decision, rejected_receipt = await invoke(submit, candidate)
    assert json.loads(rejected)["status"] == "rejected" and not decision.is_final_output
    _, decision, accepted_receipt = await invoke(submit, original)
    assert decision.is_final_output and decision.final_output == original
    accepted = loop.require_submission()
    assert accepted.raw_output == original and accepted.normalized_output == expected
    assert [event["status"] for event in loop.events] == ["observation", "observation", "rejected", "accepted"]
    prior = await check_for_final_output_from_tools(agent, [rejected_receipt], RunContextWrapper(context))
    assert not prior.is_final_output
    try:
        await invoke(submit, candidate)
    except ModelBehaviorError:
        pass
    else:
        raise AssertionError("验收后不能重新提交旧候选")
    unknown_receipt = deepcopy(accepted_receipt)
    unknown_receipt.run_item.raw_item["call_id"] = f"replay-node-{node.id}-unknown-call"
    try:
        await check_for_final_output_from_tools(
            agent, [accepted_receipt, unknown_receipt], RunContextWrapper(context),
        )
    except ModelBehaviorError:
        pass
    else:
        raise AssertionError("同批存在未知回执时不能因先遇到成功回执而放行")
    assert loop.require_submission() == accepted

    limited = make_loop(1)
    limited_submit = build_tool("submit_generation_batch", limited)
    limited_agent = Agent(name=definition.name, tools=[limited_submit], tool_use_behavior=limited.tool_use_behavior)
    await invoke(limited_submit, candidate, owner=limited_agent)
    try:
        await invoke(limited_submit, original, owner=limited_agent)
    except AutonomousToolLimitError:
        pass
    else:
        raise AssertionError("预算耗尽后不能继续提交")

    unchanged = make_loop(1)
    unhandled = build_tool("submit_scenario_design_guidance", unchanged, allow_feedback=False)
    try:
        await invoke(unhandled, {}, owner=agent)
    except ToolArgumentsValidationError:
        pass
    else:
        raise AssertionError("未授权反馈的工具必须保留原异常语义")
    assert unchanged.events[0]["status"] == "error"

    # 使用两份真实工具定义制造输出契约错配，不替换处理器，不创建或修改数据库记录。
    submit_definition = definitions.get_tool(project_id=run.project_id, tool_key="submit_generation_batch")
    observation_definition = definitions.get_tool(project_id=run.project_id, tool_key="submit_scenario_design_guidance")
    mismatched_definition = AgentToolDefinition(**{
        column.key: deepcopy(getattr(submit_definition, column.key))
        for column in AgentToolDefinition.__table__.columns
    })
    mismatched_definition.output_schema = deepcopy(observation_definition.output_schema)
    output_failure = make_loop(1)
    invalid_tool = output_failure.wrap_tool(_function_tool(mismatched_definition, context), allow_feedback=True)
    try:
        await invoke(invalid_tool, original)
    except ToolOutputValidationError:
        pass
    else:
        raise AssertionError("平台工具输出契约失败不能作为模型可修复反馈被吞掉")
    assert output_failure.events[0]["status"] == "error"

    # 历史记录保留专业提交工具输出；以真实完整批次输入复验当前专业 Agent 业务契约。
    professional_keys = definition.runtime_config["subagent_keys"]
    assert len(professional_keys) == 1
    professional_definition = definitions.get_agent(
        project_id=run.project_id, agent_key=professional_keys[0],
    )
    professional_input = json.dumps(source, ensure_ascii=False)
    assert _agent_consultation_question(
        {"params": {"input": professional_input}}, tool_key=professional_definition.agent_key,
    ) == professional_input
    # 任意 JSON 及截断 JSON 都仅作为问题文本，不能被解释成专业 Agent 的事实来源。
    assert _agent_consultation_question(
        {"params": {"input": professional_input[:-1]}}, tool_key=professional_definition.agent_key,
    ) == professional_input[:-1]
    input_failures = []
    for input_value, error_class in (
        (deepcopy(source), ToolArgumentsValidationError),
        (professional_input[:0], ToolArgumentsValidationError),
    ):
        try:
            _agent_consultation_question(
                {"params": {"input": input_value}}, tool_key=professional_definition.agent_key,
            )
        except error_class as error:
            assert loop._recoverable_error(error) is error
            assert error.diagnostic["tool_key"] == professional_definition.agent_key
            input_failures.append(type(error).__name__)
        else:
            raise AssertionError("专业工具输入损坏必须反馈明确的参数差异")
    professional_contract = AgentOutputContract(deepcopy(source))
    professional_output = deepcopy(observation)
    professional_contract.validate(professional_definition, context, deepcopy(professional_output))
    professional_output["recommended_case_count"] = source["case_budget"] + 1
    try:
        professional_contract.validate(professional_definition, context, professional_output)
    except OutputPostprocessingError as error:
        business_error = loop._recoverable_error(error)
        assert isinstance(business_error, OutputValidationError)
        assert business_error.details["case_budget"] == source["case_budget"]
    else:
        raise AssertionError("专业 Agent 的超额建议必须给出明确可修复业务差异")
    missing_budget_input = deepcopy(source)
    missing_budget_input.pop("case_budget")
    try:
        AgentOutputContract(missing_budget_input).validate(professional_definition, context, observation)
    except OutputPostprocessingError as error:
        assert loop._recoverable_error(error).details["invalid_input_field"] == "case_budget"
    else:
        raise AssertionError("专业调用遗漏原始批次额度必须反馈明确业务差异")
    return {
        "run_id": run.id, "node_run_id": node.id, "item_index": 0, "removed_fact_id": fact_id,
        "events": loop.events, "raw_output_preserved": True, "normalized_output_matches_history": True,
        "budget_enforced": True, "unhandled_tool_error_preserved": True,
        "repeated_professional_tool_allowed": True, "stale_submission_rejected": True,
        "unknown_receipt_rejected": True, "platform_output_error_preserved": True,
        "professional_business_feedback_classified": True,
        "professional_input_failures_classified": input_failures,
        "model_calls": 0, "sdk_callback_executed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    options = parser.parse_args()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, options.run_id)
        if run is None or run.status != "success":
            raise ValueError("必须提供真实成功运行")
        node_id = db.query(AgentNodeRun.id).filter(
            AgentNodeRun.run_id == run.id, AgentNodeRun.node_key == "generation", AgentNodeRun.status == "success",
        ).order_by(AgentNodeRun.id.desc()).limit(1).scalar()
        node = db.get(AgentNodeRun, node_id) if node_id is not None else None
        if node is None:
            raise ValueError("真实运行缺少已成功的生成节点")
        before = deepcopy((node.input_payload, node.output_payload, node.sdk_state))
        report = asyncio.run(replay(db, run, node))
        db.refresh(node)
        assert (node.input_payload, node.output_payload, node.sdk_state) == before
        assert not db.new and not db.dirty and not db.deleted
        report["database_unchanged"] = True
        db.rollback()
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
