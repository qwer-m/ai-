"""真实70条候选的确定性审查触发；不制造独立审查成功回执。"""

import asyncio
from copy import deepcopy
from functools import partial
from types import SimpleNamespace

import pytest

from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.sdk_adapter import _PersistentStateInputFilter, _function_tool
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from test_single_agent_direct_protocol_real_data import context_from_real, persisted, real


def platform_tools(context):
    spec = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == "test_generation_coordinator")
    state_filter = _PersistentStateInputFilter(context=context, runtime_config=deepcopy(spec["runtime_config"]))
    loop = AutonomousToolLoop(terminal_tool_names={"finish_generation"},
        validate_submission=partial(generation.validate_generation_resolution, context),
        recoverable_errors=(OutputValidationError,), max_tool_calls=120, action_context=context)
    tools = [loop.wrap_tool(_function_tool(SimpleNamespace(**item), context, state_input_filter=state_filter),
                           allow_feedback=True) for item in BUILTIN_TOOL_SPECS]
    state_filter.validate_platform_actions(tools)
    return state_filter, loop, tools


def test_real_complete_candidates_trigger_original_review_tool_without_main_model(real, persisted):
    db, state = persisted
    context = context_from_real(real, db, state=state)
    state_filter, loop, tools = platform_tools(context)
    assert state_filter.observe()["automatic_action"] == "request_review"
    before = action_state_hash(context.action_state)
    # 真实工具已被触发，缺少实际评审运行能力便明确失败；不能把离线候选当作已审查。
    with pytest.raises(ValueError, match="缺少唯一独立评审能力"):
        asyncio.run(state_filter.complete_if_ready(tools, loop))
    assert loop.events[-1]["tool_key"] == "request_review"
    assert loop.events[-1]["status"] == "error"
    assert action_state_hash(context.action_state) == before
    assert not loop.has_submission
    assert "test_generation" not in context.artifacts


def test_real_partial_candidates_do_not_trigger_premature_review(real, persisted):
    db, state = persisted
    context = context_from_real(real, db, state=state)
    # 仅截取真实已生成的前69条，不补造用例或批准回执。
    context.action_state["cases"]["items"].pop(next(reversed(context.action_state["cases"]["items"])))
    generation.refresh_generation_state(context.action_state)
    state_filter, loop, tools = platform_tools(context)
    assert state_filter.observe()["automatic_action"] is None
    assert asyncio.run(state_filter.complete_if_ready(tools, loop)) is False
    assert loop.events == []


def test_platform_action_rejects_parameterized_or_unbound_tools(real, persisted):
    db, state = persisted
    context = context_from_real(real, db, state=state)
    state_filter, _, tools = platform_tools(context)
    state_filter._runtime_config["automatic_action_tools"] = ["append_cases"]
    with pytest.raises(ValueError, match="无业务参数"):
        state_filter.validate_platform_actions(tools)
    state_filter._runtime_config["automatic_action_tools"] = ["request_review"]
    with pytest.raises(ValueError, match="无业务参数"):
        state_filter.validate_platform_actions([tool for tool in tools if tool.name != "request_review"])
