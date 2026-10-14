"""记录真实 SDK 请求的边界信息；不保存认证、连接地址或模型输入正文。"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from time import perf_counter
from typing import Any

from agents import RunHooks
from agents.exceptions import MaxTurnsExceeded, UserError
from openai import APIError

from .autonomous_budget import (
    AgentExecutionBudgetExceeded, AgentSubtreeRequestBudgetExceeded, model_request_context_bytes,
    model_request_input_reservation,
)


_RESPONSE_HEADERS = (
    "content-type", "server", "x-request-id", "request-id", "cf-ray", "retry-after",
)


def exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    """保留包装关系并限制遍历长度，避免循环引用或超长异常链。"""
    pending, result, seen = [error], [], set()
    while pending and len(result) < 12:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        pending.extend(value for value in (current.__cause__, current.__context__) if value is not None)
    return tuple(result)


def safe_exception_evidence(error: BaseException) -> list[dict[str, Any]]:
    """仅提取允许持久化的字段；异常正文可能含密钥或地址，不能直接复制。"""
    evidence = []
    for current in exception_chain(error):
        item: dict[str, Any] = {"error_type": type(current).__name__}
        status = getattr(current, "status_code", None)
        if isinstance(status, int):
            item["http_status"] = status
        request_id = getattr(current, "request_id", None)
        if isinstance(request_id, str):
            item["request_id"] = request_id[:256]
        response = getattr(current, "response", None)
        if response is not None:
            item["http_status"] = response.status_code
            item["response_headers"] = {
                key: str(response.headers[key])[:256] for key in _RESPONSE_HEADERS if key in response.headers
            }
            try:
                body = response.content
            except Exception:
                body = None
            if isinstance(body, bytes):
                item["response_body_bytes"] = len(body)
                item["response_body_sha256"] = hashlib.sha256(body).hexdigest()
                # 仅保留标准 HTTP 错误标题，不把任意网关 HTML 文本当成安全摘要。
                title = re.search(r"<title>\s*(\d{3}\s+[A-Za-z -]{1,80})\s*</title>",
                                  body[:4096].decode("utf-8", errors="replace"), re.I)
                if title:
                    item["html_title"] = " ".join(title.group(1).split())
        evidence.append(item)
    return evidence


def unwrap_sdk_execution_error(error: Exception, *, local_max_turns: int | None = None) -> Exception:
    """恢复被 SDK 工具包装的执行异常，保留传输分类和局部预算的领域处理边界。"""
    if isinstance(error, UserError):
        error = next((item for item in exception_chain(error)[1:]
                      if isinstance(item, (APIError, TimeoutError, ConnectionError,
                                           AgentExecutionBudgetExceeded, MaxTurnsExceeded))), error)
    if local_max_turns is not None and isinstance(error, MaxTurnsExceeded):
        # SDK 先于请求钩子耗尽轮次时，仍属于同一专业会话的局部额度边界。
        return AgentSubtreeRequestBudgetExceeded(
            f"专业会话已耗尽 {local_max_turns} 次模型请求，尚未完成有效提交"
        )
    return error


def safe_transport_message(error: BaseException) -> str | None:
    """网络异常只保存类型和状态；保留业务异常原文供修复反馈使用。"""
    transport = next((item for item in exception_chain(error)
                      if isinstance(item, (APIError, TimeoutError, ConnectionError))), None)
    if transport is None:
        return None
    status = getattr(transport, "status_code", None)
    suffix = f"，HTTP {status}" if isinstance(status, int) else ""
    return f"模型服务调用失败：{type(transport).__name__}{suffix}；具体请求边界见结构化诊断"


class ModelRequestDiagnostics:
    """父子 Agent 共享有限长度的请求记录，失败时复用现有节点诊断落库。"""

    def __init__(self, *, journal: Any = None, max_records: int = 100,
                 on_event: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self._journal = journal
        self._max_records = max(1, min(int(max_records), 200))
        self._on_event = on_event
        self._metadata: dict[int, dict[str, Any]] = {}
        self._active: dict[Any, tuple[dict[str, Any], float]] = {}
        self._records: list[dict[str, Any]] = []
        self._sequence = 0

    @property
    def requests(self) -> list[dict[str, Any]]:
        return deepcopy(self._records)

    def register_agent(self, agent: Any, metadata: dict[str, Any]) -> None:
        self._metadata.setdefault(id(agent), {}).update(deepcopy(metadata))

    def _emit(self, event: str, item: dict[str, Any]) -> None:
        if self._on_event is not None:
            self._on_event(event, deepcopy(item))

    def start(self, context: Any, agent: Any, system_prompt: str | None, input_items: list[Any]) -> None:
        execution = context.context
        active = [] if self._journal is None else [
            receipt for receipt in self._journal.load()["receipts"].values() if receipt["status"] == "started"
        ]
        receipt = active[0] if active else None
        arguments = json.loads(receipt["arguments"]["arguments_json"]) if receipt else {}
        settings = agent.model_settings
        extra_body = settings.extra_body if isinstance(settings.extra_body, dict) else {}
        thinking = extra_body.get("thinking")
        thinking_type = thinking.get("type") if isinstance(thinking, dict) else None
        extra_args = settings.extra_args if isinstance(settings.extra_args, dict) else {}
        reasoning_effort = extra_args.get("reasoning_effort")
        self._sequence += 1
        item = {
            "sequence": self._sequence, "agent_name": agent.name,
            **self._metadata.get(id(agent), {}),
            "run_id": execution.run_id, "node_key": execution.node_key,
            "node_run_id": self._journal.node_run_id if self._journal is not None else None,
            "status": "started", "started_at": datetime.now(timezone.utc).isoformat(),
            "action_tool": receipt["tool_key"] if receipt else None,
            "action_call_id": receipt["tool_call_id"] if receipt else None,
            "batch_id": arguments.get("batch_id"),
            "measured_context_bytes": model_request_context_bytes(
                agent=agent, system_prompt=system_prompt, input_items=input_items,
            ),
            "reserved_input_tokens": model_request_input_reservation(
                agent=agent, system_prompt=system_prompt, input_items=input_items,
            ),
            "context_measurement_scope": "sdk_context_json_not_http_wire",
            "reserved_output_tokens": agent.model_settings.max_tokens,
            "sdk_input_item_count": len(input_items),
            # 只记录协议诊断所需的枚举或工具名，不复制可能含敏感信息的扩展参数。
            "model_settings": {
                "tool_choice": settings.tool_choice if isinstance(settings.tool_choice, str) else None,
                "thinking_type": thinking_type if thinking_type in ("enabled", "disabled") else None,
                "reasoning_effort": reasoning_effort if reasoning_effort in ("none", "low", "medium", "high", "max") else None,
            },
        }
        self._active[context] = (item, perf_counter())
        self._records.append(item)
        self._records = self._records[-self._max_records:]
        self._emit("request_started", item)

    def finish(self, context: Any, response: Any) -> None:
        pending = self._active.pop(context, None)
        if pending is None:
            return
        item, started = pending
        output = json.dumps([value.model_dump(mode="json") for value in response.output], ensure_ascii=False)
        item.update({
            "status": "response_received", "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(perf_counter() - started, 3),
            "sdk_response_output_bytes": len(output.encode("utf-8")),
            "sdk_response_reasoning_item_count": sum(value.type == "reasoning" for value in response.output),
            "usage": {key: int(getattr(response.usage, key, 0) or 0) for key in (
                "requests", "input_tokens", "output_tokens", "total_tokens",
            )},
        })
        self._emit("request_finished", item)

    def record_failure(self, error: BaseException, *, execution_context: Any = None) -> None:
        evidence = safe_exception_evidence(error)
        for context, (item, started) in list(self._active.items()):
            if execution_context is not None and context.context is not execution_context:
                continue
            item.update({
                "status": "ended_without_model_response", "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(perf_counter() - started, 3), "exception_chain": evidence,
            })
            self._active.pop(context)
            self._emit("request_failed", item)

    def failure_evidence(self) -> list[dict[str, Any]]:
        return [item for item in self.requests if item["status"] == "ended_without_model_response"]


class ObservedRunHooks(RunHooks):
    """观测与预算分开组合；观测不会结算、释放或重置预算。"""

    def __init__(self, diagnostics: ModelRequestDiagnostics, budget_hooks: Any = None) -> None:
        self.diagnostics, self.budget_hooks = diagnostics, budget_hooks

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        if self.budget_hooks is not None:
            await self.budget_hooks.on_llm_start(context, agent, system_prompt, input_items)
        self.diagnostics.start(context, agent, system_prompt, input_items)

    async def on_llm_end(self, context, agent, response) -> None:
        try:
            if self.budget_hooks is not None:
                await self.budget_hooks.on_llm_end(context, agent, response)
        finally:
            self.diagnostics.finish(context, response)
