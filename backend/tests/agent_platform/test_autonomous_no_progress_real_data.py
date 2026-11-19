"""只使用 Run 45 的真实工具回执核对无进展门禁，不请求模型或伪造业务结果。"""

from copy import deepcopy
import asyncio
import json
from pathlib import Path

import pytest
from agents.tool import FunctionTool
from agents.tool_context import ToolContext

from modules.agent_platform.autonomous_tools import (
    AgentNoProgressError, AutonomousToolLoop, repeated_action_failure, repeated_domain_rejection,
)
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext


def _receipts():
    fixture = Path(__file__).with_name("fixtures") / "run45_action_receipts_real.json"
    saved = json.loads(fixture.read_text(encoding="utf-8"))
    assert saved["source_run_id"] == 45
    return saved["receipts"]


def _checkpoint(receipts):
    return {"receipts": {item["tool_call_id"]: item for item in receipts}}


def _rejection(receipts, receipt):
    feedback = receipt.get("error", {}).get("feedback")
    if feedback is None:
        return None
    return repeated_domain_rejection(_checkpoint(receipts), state_hash=receipt["pre_state_hash"],
                                     error_type=feedback["error_type"], message=feedback["message"])


def test_same_action_directory_constraint_stops_at_second_real_violation():
    receipts = _receipts()
    previous = []
    first = None
    for receipt in receipts:
        # 目录拒绝由实际领域入口产生；其他 schema、预算错误不能假装成同一个约束。
        feedback = receipt.get("error", {}).get("feedback") or {}
        if feedback.get("details") == {"action": receipt["tool_key"]}:
            rejected = _rejection(previous, receipt)
            if rejected is not None:
                first = (receipt, rejected)
                break
        previous.append(receipt)
    assert first is not None
    receipt, diagnostic = first
    assert receipt["started_sequence"] == 120
    assert receipt["tool_key"] == "review_planning_route_gaps"
    assert diagnostic["previous_tool_call_id"] == next(
        item["tool_call_id"] for item in receipts if item["started_sequence"] == 118)


def test_real_source_or_candidate_revision_changes_domain_and_allows_new_attempt():
    receipts = _receipts()
    before = next(item for item in receipts if item["started_sequence"] == 118)
    later = next(item for item in receipts if item["started_sequence"] == 193)
    assert before["pre_state_hash"] != later["pre_state_hash"]
    assert _rejection([before], later) is None


def test_distinct_semantic_constraint_is_not_collapsed():
    receipts = _receipts()
    unavailable = next(item for item in receipts if item["started_sequence"] == 180)
    wrong_scope = next(item for item in receipts if item["started_sequence"] == 182)
    assert unavailable["pre_state_hash"] == wrong_scope["pre_state_hash"]
    assert _rejection([unavailable], wrong_scope) is None


def test_equivalent_json_is_same_rejected_candidate_but_actual_correction_is_new():
    receipts = _receipts()
    invalid = next(item for item in receipts if item["started_sequence"] == 182)
    corrected = next(item for item in receipts if item["started_sequence"] == 184)
    args = json.loads(invalid["arguments"]["arguments_json"])
    reformatted = json.dumps(dict(reversed(list(args.items()))), ensure_ascii=False, indent=2)
    duplicate = repeated_action_failure(_checkpoint([invalid]), tool_key=invalid["tool_key"],
        arguments_json=reformatted, state_hash=invalid["pre_state_hash"])
    assert duplicate is not None
    assert duplicate["previous_tool_call_id"] == invalid["tool_call_id"]
    assert repeated_action_failure(_checkpoint([invalid]), tool_key=corrected["tool_key"],
        arguments_json=corrected["arguments"]["arguments_json"], state_hash=corrected["pre_state_hash"]) is None


def test_transport_or_exhausted_budget_failure_is_not_recoverable_domain_no_progress():
    receipt = deepcopy(_receipts()[-1])
    assert receipt["error"]["recoverable"] is False
    assert repeated_action_failure(_checkpoint([receipt]), tool_key=receipt["tool_key"],
        arguments_json=receipt["arguments"]["arguments_json"], state_hash=receipt["pre_state_hash"]) is None


def _replay_tool(receipts):
    """本地工具按真实参数回读原回执，没有模型、业务系统或数据库替身。"""
    async def invoke(_context, arguments):
        value = json.loads(arguments)
        receipt = next(item for item in receipts if json.loads(item["arguments"]["arguments_json"]) == value)
        if receipt["status"] == "failed":
            feedback = receipt["error"]["feedback"]
            raise OutputValidationError(feedback["message"], details=deepcopy(feedback.get("details", {})))
        return deepcopy(receipt["result"]["output"])

    return FunctionTool(name=receipts[0]["tool_key"], description="回读原始真实工具回执",
                        params_json_schema={"type": "object"}, on_invoke_tool=invoke, strict_json_schema=False)


def _loop():
    return AutonomousToolLoop(terminal_tool_names={"submit_result"}, validate_submission=lambda value: value,
                              recoverable_errors=(OutputValidationError,), max_tool_calls=160)


def _context(receipt, suffix):
    return ToolContext(context=None, tool_name=receipt["tool_key"],
                       tool_call_id=receipt["tool_call_id"] + suffix,
                       tool_arguments=receipt["arguments"]["arguments_json"])


def test_strict_run_stops_at_first_real_rejection_and_shares_child_latch():
    from dataclasses import replace

    receipt = next(item for item in _receipts() if item["started_sequence"] == 182)
    context = ToolExecutionContext(db=None, user_id=0, project_id=0, run_id=45,
                                   node_key="real_receipt_replay", run_input={}, stop_on_failure=True)
    loop = AutonomousToolLoop(terminal_tool_names={"submit_result"}, validate_submission=lambda value: value,
                              recoverable_errors=(OutputValidationError,), max_tool_calls=160,
                              action_context=context)
    tool = loop.wrap_tool(_replay_tool([receipt]), allow_feedback=True)
    with pytest.raises(OutputValidationError) as first:
        asyncio.run(tool.on_invoke_tool(_context(receipt, "_strict_first"),
                                      receipt["arguments"]["arguments_json"]))
    assert len(loop.events) == 1 and loop.events[0]["status"] == "error"
    assert context._request_failures == [first.value]
    with pytest.raises(OutputValidationError) as child_error:
        replace(context, node_key="child").require_request_allowed()
    assert child_error.value is first.value
    with pytest.raises(OutputValidationError):
        asyncio.run(tool.on_invoke_tool(_context(receipt, "_strict_later"),
                                      receipt["arguments"]["arguments_json"]))
    assert len(loop.events) == 1


@pytest.mark.parametrize("return_subtree_failures", [False, True])
def test_strict_expert_queue_does_not_start_later_real_work_after_first_rejection(return_subtree_failures):
    receipt = next(item for item in _receipts() if item["started_sequence"] == 182)
    actual_input = json.loads(receipt["arguments"]["arguments_json"])

    async def replay():
        context = ToolExecutionContext(db=None, user_id=0, project_id=0, run_id=45,
                                       node_key="real_receipt_replay", run_input={},
                                       stop_on_failure=True, agent_concurrency=6)
        tool = _replay_tool([receipt])
        admitted = []

        async def replay_actual_receipt(**kwargs):
            admitted.append(kwargs["input_payload"])
            return await tool.on_invoke_tool(_context(receipt, "_strict_queue"),
                                             receipt["arguments"]["arguments_json"])

        context.invoke_agent = replay_actual_receipt
        with pytest.raises(OutputValidationError):
            await context.invoke_agents(agent_key=receipt["tool_key"],
                                        inputs=[actual_input, actual_input],
                                        item_postprocessor="identity",
                                        return_subtree_failures=return_subtree_failures)
        assert admitted == [actual_input]

    asyncio.run(replay())


def test_tool_loop_stops_repeated_real_rejected_candidate_without_resetting_feedback():
    invalid = next(item for item in _receipts() if item["started_sequence"] == 182)
    loop = _loop()
    tool = loop.wrap_tool(_replay_tool([invalid]), allow_feedback=True)
    async def exercise():
        first = await tool.on_invoke_tool(_context(invalid, "/first"), invalid["arguments"]["arguments_json"])
        assert json.loads(first)["status"] == "rejected"
        with pytest.raises(AgentNoProgressError):
            await tool.on_invoke_tool(_context(invalid, "/second"), invalid["arguments"]["arguments_json"])
    asyncio.run(exercise())
    assert loop.events[-1]["error_type"] == "AgentNoProgressError"
    assert loop.has_submission is False


def test_tool_loop_allows_real_corrected_parameters_and_stops_identical_readback():
    receipts = _receipts()
    invalid = next(item for item in receipts if item["started_sequence"] == 182)
    corrected = next(item for item in receipts if item["started_sequence"] == 184)
    loop = _loop()
    tool = loop.wrap_tool(_replay_tool([invalid, corrected]), allow_feedback=True)
    async def exercise():
        await tool.on_invoke_tool(_context(invalid, "/first"), invalid["arguments"]["arguments_json"])
        output = await tool.on_invoke_tool(_context(corrected, "/corrected"), corrected["arguments"]["arguments_json"])
        assert output == corrected["result"]["output"]
        with pytest.raises(AgentNoProgressError):
            await tool.on_invoke_tool(_context(corrected, "/duplicate"), corrected["arguments"]["arguments_json"])
    asyncio.run(exercise())
    assert loop.has_submission is False
