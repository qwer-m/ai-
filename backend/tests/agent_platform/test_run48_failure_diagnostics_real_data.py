"""回放 Run48 真实首错及支持决定，验证类型和诊断协议，不执行模型或数据库。"""

import asyncio
from copy import deepcopy
import json
from pathlib import Path

from agents.exceptions import UserError
from agents.tool import FunctionTool
from agents.tool_context import ToolContext
import pytest

from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import (
    exception_chain, unwrap_sdk_execution_error, validation_error_details,
)
from modules.agent_platform.runtime import _agent_error_diagnostic, _agent_failure_kind


@pytest.fixture(scope="module")
def real_failure():
    path = Path(__file__).with_name("fixtures") / "run48_first_failure_diagnostic_real.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert (saved["source_run_id"], saved["source_node_id"]) == (48, 389)
    assert saved["receipt"]["error"]["recoverable"] is False
    assert saved["support_provenance"]["review_decision"]["disposition"] == "supported"
    details = saved["reconstructed_validation_details"]
    assert details["test_design_item_id"] == saved["support_provenance"]["design"]["test_design_item_id"]
    assert details["support_fact_ids"] == sorted(saved["support_provenance"]["review_decision"]["fact_ids"])
    return saved


def _error_chain(saved, error_type=OutputValidationError):
    """重建真实 SDK 包装关系；错误内容及差异全部来自存档，不构造模型响应。"""
    details = deepcopy(saved["reconstructed_validation_details"])
    source = (error_type(saved["receipt"]["error"]["message"], details=details)
              if issubclass(error_type, OutputValidationError)
              else error_type(saved["receipt"]["error"]["message"]))
    try:
        raise source
    except error_type:
        try:
            raise UserError(saved["sdk_error_message"]) from source
        except UserError as wrapped:
            return source, wrapped


def test_real_sdk_wrapper_unwraps_typed_business_error(real_failure):
    source, wrapped = _error_chain(real_failure)
    assert [{"error_type": type(error).__name__} for error in exception_chain(wrapped)] == real_failure["exception_chain"]
    assert unwrap_sdk_execution_error(wrapped) is source
    assert unwrap_sdk_execution_error(source) is source
    assert _agent_failure_kind(wrapped) == _agent_failure_kind(source) == "output_validation"


def test_business_details_survive_outer_sdk_diagnostic_without_aliasing(real_failure):
    source, wrapped = _error_chain(real_failure)
    wrapped.diagnostic = {"exception_chain": deepcopy(real_failure["exception_chain"])}
    diagnostic = _agent_error_diagnostic(wrapped)
    assert diagnostic == {**wrapped.diagnostic, "details": real_failure["reconstructed_validation_details"]}
    assert _agent_error_diagnostic(source) == {"details": real_failure["reconstructed_validation_details"]}
    diagnostic["details"]["support_fact_ids"].clear()
    assert source.details == real_failure["reconstructed_validation_details"]
    assert "details" not in wrapped.diagnostic


def test_strict_journal_failure_payload_keeps_real_details_without_feedback(real_failure):
    source, _ = _error_chain(real_failure)
    record = AutonomousToolLoop._failure_record(source, recoverable=False, feedback=None)
    assert record["recoverable"] is False and "feedback" not in record
    assert record["error_type"] == real_failure["receipt"]["error"]["error_type"]
    assert record["details"] == real_failure["reconstructed_validation_details"]
    # 这是 journal.fail 实际使用的 JSON 负载构造函数，不设置替身 journal 或数据库。
    assert json.loads(json.dumps(record, ensure_ascii=False))["details"] == source.details
    record["details"]["support_fact_ids"].clear()
    assert source.details == real_failure["reconstructed_validation_details"]


def test_strict_real_tool_error_still_stops_once_and_exposes_details(real_failure):
    source, _ = _error_chain(real_failure)
    receipt = real_failure["receipt"]
    context = ToolExecutionContext(
        db=None, user_id=real_failure["user_id"], project_id=real_failure["project_id"],
        run_id=real_failure["source_run_id"], node_key=real_failure["node_key"], run_input={},
        stop_on_failure=real_failure["stop_on_failure"],
    )
    admitted = []

    async def replay(_context, arguments):
        assert arguments == receipt["arguments"]["arguments_json"]
        admitted.append(receipt["tool_call_id"])
        raise source

    tool = FunctionTool(name=receipt["tool_key"], description="回放真实首错，不访问模型或业务系统",
                        params_json_schema={"type": "object"}, on_invoke_tool=replay, strict_json_schema=False)
    loop = AutonomousToolLoop(
        terminal_tool_names=set(real_failure["terminal_tool_keys"]), validate_submission=lambda value: value,
        recoverable_errors=(OutputValidationError,), max_tool_calls=real_failure["max_tool_calls"],
        action_context=context,
    )
    wrapped = loop.wrap_tool(tool, allow_feedback=True)
    tool_context = ToolContext(context=context, tool_name=receipt["tool_key"], tool_call_id=receipt["tool_call_id"],
                               tool_arguments=receipt["arguments"]["arguments_json"])
    with pytest.raises(OutputValidationError) as first:
        asyncio.run(wrapped.on_invoke_tool(tool_context, tool_context.tool_arguments))
    assert first.value is source
    assert loop.events[0]["status"] == "error"
    assert loop.events[0]["details"] == real_failure["reconstructed_validation_details"]
    with pytest.raises(OutputValidationError) as later:
        asyncio.run(wrapped.on_invoke_tool(tool_context, tool_context.tool_arguments))
    assert later.value is source and admitted == [receipt["tool_call_id"]]
    assert len(loop.events) == 1


def test_identical_real_message_does_not_turn_generic_error_into_business_error(real_failure):
    source, wrapped = _error_chain(real_failure, error_type=ValueError)
    assert unwrap_sdk_execution_error(wrapped) is wrapped
    assert validation_error_details(wrapped) is None
    assert _agent_failure_kind(source) == _agent_failure_kind(wrapped) == "unknown"
