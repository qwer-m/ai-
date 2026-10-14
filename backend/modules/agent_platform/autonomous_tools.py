"""一次 Agent 调用中的工具反馈与验收边界，失败候选不能结束运行。"""

from __future__ import annotations

from collections.abc import Callable
from copy import copy, deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from agents import ToolsToFinalOutputResult
from agents.exceptions import ModelBehaviorError
from agents.tool import FunctionTool, FunctionToolResult
from agents.tool_context import ToolContext
from .request_diagnostics import safe_transport_message


@dataclass(frozen=True)
class AcceptedSubmission:
    """分别保存验收前的结构化候选与已完成业务校验的结果。"""

    raw_output: dict[str, Any]
    normalized_output: dict[str, Any]


class AutonomousToolLimitError(RuntimeError):
    """工具预算耗尽时终止本轮，不能伪装成可以继续修补的业务错误。"""


class AutonomousToolLoop:
    """以真实工具回执驱动观察、修正和提交，不接管模型自身的决策。"""

    def __init__(
        self,
        *,
        terminal_tool_names: set[str],
        validate_submission: Callable[[dict[str, Any]], dict[str, Any]],
        recoverable_errors: tuple[type[Exception], ...],
        max_tool_calls: int,
        action_context: Any = None,
        budget_snapshot: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        if not terminal_tool_names or any(not name.strip() for name in terminal_tool_names):
            raise ValueError("自主工具循环必须声明有效的提交工具")
        if max_tool_calls < 1:
            raise ValueError("自主工具循环调用预算必须大于零")
        self._terminal_names = frozenset(terminal_tool_names)
        self._validate = validate_submission
        self._recoverable_errors = recoverable_errors
        self._max_tool_calls = max_tool_calls
        self._events: list[dict[str, Any]] = []
        self._receipts: dict[str, dict[str, Any]] = {}
        self._accepted: AcceptedSubmission | None = None
        self._accepted_call_id: str | None = None
        self._action_context = action_context
        self._budget_snapshot = budget_snapshot

    def _budget(self) -> dict[str, Any] | None:
        return self._budget_snapshot() if self._budget_snapshot is not None else None

    @property
    def events(self) -> list[dict[str, Any]]:
        return deepcopy(self._events)

    def _begin_call(self, tool_name: str, context: ToolContext[Any], arguments_json: str) -> dict[str, Any]:
        call_id = context.tool_call_id
        if not call_id or call_id in self._receipts:
            raise ModelBehaviorError("自主工具调用缺少唯一的 tool_call_id")
        if self._accepted is not None:
            raise ModelBehaviorError("本轮已经验收成功，不能再次调用工具")
        if len(self._events) >= self._max_tool_calls:
            raise AutonomousToolLimitError("本轮 Agent 已耗尽工具调用预算")
        if self._action_context is not None and self._action_context.action_journal is not None:
            checkpoint = self._action_context.action_journal.load()
            receipts = (checkpoint or {}).get("receipts") or {}
            if call_id not in receipts and len(receipts) >= self._max_tool_calls:
                raise AutonomousToolLimitError("该 Agent 阶段已耗尽累计工具调用预算，恢复不会重置预算")
        event: dict[str, Any] = {
            "tool_call_id": call_id,
            "tool_key": tool_name,
            "status": "running",
            "arguments_chars": len(arguments_json),
            "arguments_sha256": hashlib.sha256(arguments_json.encode("utf-8")).hexdigest(),
        }
        self._events.append(event)
        self._receipts[call_id] = event
        return event

    def _recoverable_error(self, error: Exception) -> Exception | None:
        """只解包明确的 SDK 模型行为错误；平台工具异常不得因其 cause 被吞掉。"""
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, self._recoverable_errors):
                return current
            if not isinstance(current, ModelBehaviorError):
                return None
            current = current.__cause__
        return None

    @staticmethod
    def _feedback(error: Exception) -> dict[str, Any]:
        feedback: dict[str, Any] = {
            "error_type": type(error).__name__,
            "message": (safe_transport_message(error) or str(error))[:4000],
        }
        details = getattr(error, "details", None)
        if isinstance(details, dict):
            feedback["details"] = deepcopy(details)
        diagnostic = getattr(error, "diagnostic", None)
        if isinstance(diagnostic, dict):
            # 完整请求时序由 SDK 结果/异常落库；模型只接收用于修正候选的诊断，
            # 避免一次局部失败把整阶段请求记录复制进后续每一轮上下文。
            feedback["diagnostic"] = deepcopy({
                key: value for key, value in diagnostic.items() if key != "model_requests"
            })
        return feedback

    def feedback_for_error(self, error: Exception) -> dict[str, Any] | None:
        recoverable = self._recoverable_error(error)
        return self._feedback(recoverable) if recoverable is not None else None

    def wrap_tool(self, tool: FunctionTool, *, allow_feedback: bool) -> FunctionTool:
        """仅给显式允许反馈的工具返回校验差异，其他工具保留原异常语义。"""
        if tool.name in self._terminal_names and not allow_feedback:
            raise ValueError("自主提交工具必须显式允许校验反馈")
        original_invoke = tool.on_invoke_tool

        async def invoke(context: ToolContext[Any], arguments_json: str) -> Any:
            event = self._begin_call(tool.name, context, arguments_json)
            action_context = self._action_context
            journal = action_context.action_journal if action_context is not None else None
            previous_state = deepcopy(action_context.action_state) if journal is not None else None
            receipt = None
            if journal is not None:
                from .action_journal import action_state_hash

                receipt = journal.begin(
                    tool_call_id=context.tool_call_id, tool_key=tool.name,
                    args={"arguments_json": arguments_json}, prehash=action_state_hash(previous_state),
                    resume_started=action_context.restoring_action,
                )
                if receipt.replayed and receipt.status == "failed":
                    raise ModelBehaviorError("失败动作回执不能被重新执行；请依据当前状态选择新行动")
            try:
                result = (
                    receipt.result["output"] if receipt is not None and receipt.replayed
                    else await original_invoke(context, arguments_json)
                )
                submission = None
                if tool.name in self._terminal_names:
                    raw_output = json.loads(result) if isinstance(result, str) else result
                    if not isinstance(raw_output, dict):
                        raise TypeError("提交工具必须返回结构化对象")
                    normalized = self._validate(deepcopy(raw_output))
                    if not isinstance(normalized, dict):
                        raise TypeError("提交验收函数必须返回结构化对象")
                    submission = AcceptedSubmission(deepcopy(raw_output), deepcopy(normalized))
                    event["status"] = "accepted"
                else:
                    event["status"] = "observation"
                result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                event["output_chars"] = len(result_text)
                event["output_sha256"] = hashlib.sha256(result_text.encode("utf-8")).hexdigest()
                if journal is not None and not receipt.replayed:
                    journal.complete(
                        tool_call_id=context.tool_call_id, nextstate=action_context.action_state,
                        result={"output": result}, budget=self._budget(),
                    )
                if submission is not None:
                    self._accepted = submission
                    self._accepted_call_id = context.tool_call_id
                return result
            except BaseException as error:
                recoverable = self._recoverable_error(error) if allow_feedback and isinstance(error, Exception) else None
                feedback = self._feedback(recoverable) if recoverable is not None else None
                if journal is not None:
                    action_context.action_state = previous_state
                    # 失租时日志本身会拒绝迟到提交，不能让旧 Worker 覆盖新进度。
                    journal.fail(
                        tool_call_id=context.tool_call_id,
                        error={
                            "error_type": type(error).__name__, "message": (safe_transport_message(error) or str(error))[:4000],
                            "recoverable": recoverable is not None,
                            **({"feedback": feedback} if feedback is not None else {}),
                            **({"agent_input_identity": deepcopy(error.agent_input_identity)}
                               if feedback is not None and hasattr(error, "agent_input_identity") else {}),
                        },
                        budget=self._budget(),
                    )
                if recoverable is None:
                    event.update(status="error", error_type=type(error).__name__, message=(safe_transport_message(error) or str(error))[:4000])
                    raise
                event.update(status="rejected", feedback=feedback)
                return json.dumps({
                    "status": "rejected",
                    "feedback": feedback,
                    "instruction": "本次候选尚未通过验收，请根据原始事实和校验差异修正后重新提交；反馈没有列出的内容仍需核对。",
                }, ensure_ascii=False, separators=(",", ":"))

        wrapped = copy(tool)
        wrapped.on_invoke_tool = invoke
        return wrapped

    def tool_use_behavior(self, _context: Any, results: list[FunctionToolResult]) -> ToolsToFinalOutputResult:
        """只接受本轮真实工具回执，拒绝正文或历史候选冒充验收结果。"""
        accepted = False
        for result in results:
            call_id = getattr(result.run_item, "call_id", None)
            receipt = self._receipts.get(call_id)
            if receipt is None:
                raise ModelBehaviorError("自主工具结果缺少对应调用回执")
            if receipt["tool_key"] != result.tool.name:
                raise ModelBehaviorError("自主工具结果与调用回执不匹配")
            if receipt["status"] == "accepted" and call_id == self._accepted_call_id:
                accepted = True
        if accepted:
            return ToolsToFinalOutputResult(
                is_final_output=True, final_output=self.require_submission().raw_output,
            )
        return ToolsToFinalOutputResult(is_final_output=False, final_output=None)

    def require_submission(self) -> AcceptedSubmission:
        """Runner 返回正文或耗尽预算均不能代替通过校验的唯一提交。"""
        if self._accepted is None:
            raise ModelBehaviorError("Agent 未完成通过完整校验的提交")
        return deepcopy(self._accepted)
