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
from .request_diagnostics import exception_chain, safe_transport_message, validation_error_details
from .retry_policy import model_transport_failure_kind


@dataclass(frozen=True)
class AcceptedSubmission:
    """分别保存验收前的结构化候选与已完成业务校验的结果。"""

    raw_output: dict[str, Any]
    normalized_output: dict[str, Any]


class AutonomousToolLimitError(RuntimeError):
    """工具预算耗尽时终止本轮，不能伪装成可以继续修补的业务错误。"""


class AgentNoProgressError(RuntimeError):
    """同一版本重复已经否决的输入或相同空动作，必须停止且不能重试整节点。"""

    def __init__(self, message: str, *, diagnostic: dict[str, Any] | None = None) -> None:
        self.diagnostic = deepcopy(diagnostic or {})
        super().__init__(message)


def _value_identity(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _arguments_identity(arguments_json: str) -> str:
    """等价 JSON 的空白与键顺序不是新候选；损坏参数保留其原始字节身份。"""
    from .serialization import strict_json_loads

    try:
        return _value_identity(strict_json_loads(arguments_json))
    except ValueError:
        return hashlib.sha256(arguments_json.encode("utf-8")).hexdigest()


def repeated_domain_rejection(checkpoint: dict[str, Any], *, state_hash: str,
                              error_type: str, message: str) -> dict[str, Any] | None:
    """领域准入目录未变时，反复违反同一准入约束不构成新的业务判断。"""
    identity = _value_identity({"error_type": error_type, "message": message})
    for receipt in (checkpoint.get("receipts") or {}).values():
        error = receipt.get("error") or {}
        if (receipt.get("status") != "failed" or receipt.get("pre_state_hash") != state_hash
                or not error.get("recoverable")):
            continue
        feedback = error.get("feedback") or error
        prior = _value_identity({"error_type": feedback.get("error_type"), "message": feedback.get("message", "")})
        if prior == identity:
            return {"domain_sha256": state_hash, "error_sha256": identity,
                    "previous_tool_call_id": receipt["tool_call_id"]}
    return None


def repeated_action_failure(checkpoint: dict[str, Any], *, tool_key: str,
                            arguments_json: str, state_hash: str) -> dict[str, Any] | None:
    """只识别同一完整领域版本下原样重提的验收失败，传输中断不参与判定。"""
    identity = _arguments_identity(arguments_json)
    for receipt in (checkpoint.get("receipts") or {}).values():
        if (receipt.get("status") != "failed" or receipt.get("tool_key") != tool_key
                or receipt.get("pre_state_hash") != state_hash
                or not receipt.get("error", {}).get("recoverable")):
            continue
        previous = receipt.get("arguments", {}).get("arguments_json")
        if not isinstance(previous, str) or _arguments_identity(previous) != identity:
            continue
        error = receipt["error"]
        return {"tool_key": tool_key, "arguments_sha256": identity, "domain_sha256": state_hash,
                "previous_tool_call_id": receipt["tool_call_id"],
                "error_sha256": _value_identity(error.get("feedback") or {
                    "error_type": error["error_type"], "message": error.get("message", ""),
                })}
    return None


def _result_identity(result: Any) -> str:
    return _arguments_identity(result) if isinstance(result, str) else _value_identity(result)


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
        self._failed_candidates: dict[tuple[str, str, str | None], str] = {}
        self._unchanged_results: set[tuple[str, str, str | None, str]] = set()

    def _budget(self) -> dict[str, Any] | None:
        return self._budget_snapshot() if self._budget_snapshot is not None else None

    @property
    def events(self) -> list[dict[str, Any]]:
        return deepcopy(self._events)

    @property
    def has_submission(self) -> bool:
        """只有真实终止工具通过完整验收，专业会话才允许结束。"""
        return self._accepted is not None

    def _begin_call(self, tool_name: str, context: ToolContext[Any], arguments_json: str) -> dict[str, Any]:
        call_id = context.tool_call_id
        if not call_id or call_id in self._receipts:
            raise ModelBehaviorError("自主工具调用缺少唯一的 tool_call_id")
        if self._accepted is not None:
            raise ModelBehaviorError("本轮已经验收成功，不能再次调用工具")
        if len(self._events) >= self._max_tool_calls:
            raise AutonomousToolLimitError("本轮 Agent 已耗尽工具调用预算")
        if self._action_context is not None and self._action_context.action_journal is not None:
            journal = self._action_context.action_journal
            receipts = journal.receipts()
            if not any(item["tool_call_id"] == call_id for item in receipts) and len(receipts) >= self._max_tool_calls:
                raise AutonomousToolLimitError("该 Agent 阶段已耗尽累计工具调用预算，恢复不会重置预算")
        else:
            receipts = None
        event: dict[str, Any] = {
            "tool_call_id": call_id,
            "tool_key": tool_name,
            "status": "running",
            "arguments_chars": len(arguments_json),
            "arguments_sha256": hashlib.sha256(arguments_json.encode("utf-8")).hexdigest(),
            "arguments_identity_sha256": _arguments_identity(arguments_json),
        }
        if receipts is not None:
            from .action_journal import action_state_hash

            state_hash = action_state_hash(self._action_context.action_state)
            for header in reversed(receipts):
                if (header["status"] != "failed" or header["tool_key"] != tool_name
                        or header["pre_state_hash"] != state_hash):
                    continue
                previous = journal.receipt_by_id(header["tool_call_id"])
                repeated = repeated_action_failure({"receipts": {header["tool_call_id"]: previous}},
                    tool_key=tool_name, arguments_json=arguments_json, state_hash=state_hash)
                if repeated is not None:
                    event["no_progress"] = repeated
                    break
        self._events.append(event)
        self._receipts[call_id] = event
        return event

    def _recoverable_error(self, error: Exception) -> Exception | None:
        """只解包明确的 SDK 模型行为错误；平台工具异常不得因其 cause 被吞掉。"""
        if self._action_context is not None and self._action_context.stop_on_failure:
            return None
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

    @staticmethod
    def _failure_record(error: BaseException, *, recoverable: bool,
                        feedback: dict[str, Any] | None) -> dict[str, Any]:
        """错误事实独立于是否继续调用模型，严格停止也保留原始业务差异。"""
        details = validation_error_details(error)
        return {
            "error_type": type(error).__name__, "message": (safe_transport_message(error) or str(error))[:4000],
            "transport_failure_kind": next((kind for item in exception_chain(error)
                if (kind := model_transport_failure_kind(item)) is not None), None),
            "recoverable": recoverable,
            **({"details": details} if details is not None else {}),
            **({"feedback": feedback} if feedback is not None else {}),
            **({"agent_input_identity": deepcopy(error.agent_input_identity)}
               if feedback is not None and hasattr(error, "agent_input_identity") else {}),
        }

    def wrap_tool(self, tool: FunctionTool, *, allow_feedback: bool) -> FunctionTool:
        """仅给显式允许反馈的工具返回校验差异，其他工具保留原异常语义。"""
        if tool.name in self._terminal_names and not allow_feedback:
            raise ValueError("自主提交工具必须显式允许校验反馈")
        original_invoke = tool.on_invoke_tool

        async def invoke(context: ToolContext[Any], arguments_json: str) -> Any:
            if self._action_context is not None:
                self._action_context.require_request_allowed()
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
                    raise AgentNoProgressError("失败动作回执不能被重新执行；本次没有新的输入或证据")
            try:
                if event.get("no_progress") is not None:
                    raise AgentNoProgressError("同一领域版本再次提交已否决的相同参数，停止无进展调用",
                                               diagnostic=event["no_progress"])
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
                if tool.name not in self._terminal_names:
                    state_hash = _value_identity(previous_state) if previous_state is not None else None
                    next_hash = _value_identity(action_context.action_state) if journal is not None else None
                    key = (tool.name, event["arguments_identity_sha256"], state_hash, _result_identity(result))
                    repeated = key in self._unchanged_results
                    if journal is not None and state_hash == next_hash and not repeated:
                        for header in reversed(journal.receipts()):
                            if (header["status"] != "completed" or header["tool_key"] != tool.name
                                    or header["pre_state_hash"] != header.get("post_state_hash")
                                    or header["pre_state_hash"] != state_hash):
                                continue
                            old = journal.receipt_by_id(header["tool_call_id"])
                            arguments = old.get("arguments", {}).get("arguments_json")
                            repeated = (isinstance(arguments, str) and _arguments_identity(arguments) == key[1]
                                and "output" in old.get("result", {})
                                and _result_identity(old["result"]["output"]) == key[3])
                            if repeated:
                                break
                    if state_hash == next_hash:
                        if repeated and not (receipt is not None and receipt.replayed):
                            raise AgentNoProgressError("工具再次返回同一结果且领域状态未改变，停止无进展调用",
                                diagnostic={"tool_key": tool.name, "arguments_sha256": key[1],
                                            "domain_sha256": state_hash, "output_sha256": key[3]})
                        self._unchanged_results.add(key)
                if journal is not None and not receipt.replayed:
                    journal.complete(
                        tool_call_id=context.tool_call_id, nextstate=action_context.action_state,
                        result={"output": result}, budget=self._budget(),
                    )
                if submission is not None:
                    self._accepted = submission
                    self._accepted_call_id = context.tool_call_id
                if journal is not None:
                    from .conflict_confirmation import require_conflict_confirmation
                    require_conflict_confirmation(action_context.action_state)
                return result
            except BaseException as error:
                from .conflict_confirmation import ConflictConfirmationRequired
                if isinstance(error, ConflictConfirmationRequired):
                    raise
                if action_context is not None:
                    action_context.record_failure(error)
                recoverable = self._recoverable_error(error) if allow_feedback and isinstance(error, Exception) else None
                feedback = self._feedback(recoverable) if recoverable is not None else None
                if recoverable is not None:
                    state_hash = _value_identity(previous_state) if previous_state is not None else None
                    candidate_key = (tool.name, event["arguments_identity_sha256"], state_hash)
                    error_hash = _value_identity(feedback)
                    if self._failed_candidates.get(candidate_key) == error_hash:
                        error = AgentNoProgressError("相同候选在相同版本再次得到同一验收错误，停止重复修补",
                            diagnostic={"tool_key": tool.name, "arguments_sha256": candidate_key[1],
                                        "domain_sha256": state_hash, "error_sha256": error_hash})
                        recoverable, feedback = None, None
                    else:
                        self._failed_candidates[candidate_key] = error_hash
                failure_record = self._failure_record(error, recoverable=recoverable is not None, feedback=feedback)
                if journal is not None:
                    action_context.action_state = previous_state
                    # 失租时日志本身会拒绝迟到提交，不能让旧 Worker 覆盖新进度。
                    journal.fail(
                        tool_call_id=context.tool_call_id,
                        error=failure_record,
                        budget=self._budget(),
                    )
                if recoverable is None:
                    event.update(status="error", **{key: failure_record[key]
                                 for key in ("error_type", "message", "details") if key in failure_record})
                    raise error
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
