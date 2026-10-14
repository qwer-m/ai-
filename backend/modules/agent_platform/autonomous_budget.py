"""父子 Agent 共用一次已持久化预留，在每次模型请求前执行本地准入。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import asyncio
from copy import deepcopy
import json
import math
from threading import Lock
from typing import Any

from agents import Agent, RunContextWrapper, RunHooks
from agents.agent_output import AgentOutputSchema, AgentOutputSchemaBase
from agents.items import ModelResponse
from agents.tool import FunctionTool
from agents.usage import Usage


_BUDGET_KEYS = ("attempted_requests", "input_tokens", "output_tokens", "total_tokens")


class AgentExecutionBudgetExceeded(RuntimeError):
    """本轮额度耗尽，不能转换为让模型继续修补的工具观察。"""


class AgentSubtreeRequestBudgetExceeded(AgentExecutionBudgetExceeded):
    """一次专业会话及其咨询子树耗尽请求数，全局共享额度可能仍有余额。"""


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    raise TypeError(f"模型输入包含无法计量的对象: {type(value).__name__}")


def _model_request_envelope(
    *,
    agent: Agent[Any],
    system_prompt: str | None,
    input_items: list[Any],
) -> dict[str, Any]:
    """保留 SDK 上下文和声明的完整文本契约。"""
    tools = []
    for tool in agent.tools:
        if not isinstance(tool, FunctionTool):
            raise ValueError("自主请求预算目前仅支持可完整计量的本地 FunctionTool")
        tools.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.params_json_schema,
            "strict": tool.strict_json_schema,
            "output_schema": tool.output_json_schema,
        })
    if agent.handoffs:
        raise ValueError("自主请求预算尚未支持 handoff，请通过已计量的专业 Agent 工具调用")
    output_schema = agent.output_type
    if not isinstance(output_schema, AgentOutputSchemaBase):
        output_schema = AgentOutputSchema(output_schema)
    return {
        "instructions": system_prompt,
        "input": input_items,
        "tools": tools,
        "output_schema": None if output_schema.is_plain_text() else output_schema.json_schema(),
    }


def _context_bytes(envelope: dict[str, Any]) -> int:
    return len(json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=_json_value).encode("utf-8"))


def _context_token_upper_bound(envelope: dict[str, Any]) -> int:
    """按文本内容估算保守 token 上界，避免把 UTF-8 多字节长度当成 token 数。"""
    serialized = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=_json_value,
    )
    ascii_bytes = sum(1 for character in serialized if ord(character) < 128)
    non_ascii_characters = len(serialized) - ascii_bytes
    # ASCII 文本按约 4 字节/token，中文等非 ASCII 字符按 2 token/字符，
    # 两项相加保留 JSON 标点、字段名和中英文混合内容的余量。
    return max(
        1,
        math.ceil(ascii_bytes / 4) + non_ascii_characters * 2,
    )


def model_request_context_bytes(
    *, agent: Agent[Any], system_prompt: str | None, input_items: list[Any],
) -> int:
    """诊断仍记录完整 SDK JSON 大小，不把它混同为图片 Token 数。"""
    return _context_bytes(_model_request_envelope(agent=agent, system_prompt=system_prompt, input_items=input_items))


def input_protocol_reservation(image_count: int = 0) -> int:
    """复用既有文本 8192、单图 32768 的预留口径，按实际图片数量累加。"""
    if type(image_count) is not int or image_count < 0:
        raise ValueError("图片预留数量必须是非负整数")
    return 8192 + image_count * (32768 - 8192)


def _input_budget_view(value: Any) -> tuple[Any, int]:
    if isinstance(value, list):
        views = [_input_budget_view(item) for item in value]
        return [item for item, _ in views], sum(count for _, count in views)
    if isinstance(value, dict):
        if value.get("type") == "input_image":
            # 仅处理 SDK 明示的图片内容；文本或工具输出字符串中的 URL 仍原样计量。
            return {key: item for key, item in value.items() if key not in {"image_url", "file_id"}}, 1
        views = {key: _input_budget_view(item) for key, item in value.items()}
        return {key: item for key, (item, _) in views.items()}, sum(count for _, count in views.values())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value, 0
    return _input_budget_view(_json_value(value))


def model_request_input_reservation(
    *, agent: Agent[Any], system_prompt: str | None, input_items: list[Any],
) -> int:
    """文本按字节保守预留，图片按视觉额度预留，禁止把 Base64 编码逐字当作 Token。"""
    envelope = _model_request_envelope(agent=agent, system_prompt=system_prompt, input_items=input_items)
    envelope["input"], image_count = _input_budget_view(input_items)
    return _context_token_upper_bound(envelope) + input_protocol_reservation(image_count)


def model_request_reservation(
    *, agent: Agent[Any], system_prompt: str | None, input_items: list[Any],
) -> dict[str, int]:
    """沿用原保守预算口径，同时纳入本轮新增的工具观察。"""
    output_limit = agent.model_settings.max_tokens
    if output_limit is None or output_limit <= 0:
        raise ValueError("自主 Agent 必须向模型明确发送正整数 max_tokens")
    input_tokens = model_request_input_reservation(
        agent=agent, system_prompt=system_prompt, input_items=input_items,
    )
    return {
        "attempted_requests": 1,
        "input_tokens": input_tokens,
        "output_tokens": int(output_limit),
        "total_tokens": input_tokens + int(output_limit),
    }


def _validated_budget_state(
    reservation: Mapping[str, int], checkpoint: dict[str, Any] | None,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    """恢复和终态收尾共用账本校验；是否仍可准入新请求由运行钩子判断。"""

    def counters(value: Any, keys: tuple[str, ...], label: str) -> dict[str, int]:
        if not isinstance(value, Mapping) or any(key not in value for key in keys):
            raise ValueError(f"自主预算{label}缺少完整计数字段")
        if any(type(value[key]) is not int or value[key] < 0 for key in keys):
            raise ValueError(f"自主预算{label}必须是非负整数，不能从无效状态退款")
        return {key: value[key] for key in keys}

    limits = counters(reservation, _BUDGET_KEYS, "限额")
    if any(value <= 0 for value in limits.values()):
        raise ValueError("自主请求预算各项必须为正整数")
    if checkpoint is not None and not isinstance(checkpoint, dict):
        raise ValueError("自主预算检查点必须是对象")
    saved = checkpoint or {}
    count_keys = ("pending_requests", "completed_requests", "interrupted_requests")
    usage_keys = ("requests", "input_tokens", "output_tokens", "total_tokens")
    if saved:
        if counters(saved.get("limits"), _BUDGET_KEYS, "检查点限额") != limits:
            raise ValueError("恢复中的自主请求预算与原始预留不一致")
        charged = counters(saved.get("charged"), _BUDGET_KEYS, "已占用量")
        counts = counters(saved, count_keys, "请求状态")
        reported = counters(saved.get("reported_usage"), usage_keys, "已报告用量")
        if saved.get("request_scope") != "sdk_model_calls":
            raise ValueError("自主预算检查点的请求口径不匹配")
        if charged["total_tokens"] < charged["input_tokens"] + charged["output_tokens"]:
            raise ValueError("自主预算检查点总量低于输入与输出之和")
        if sum(counts.values()) > charged["attempted_requests"]:
            raise ValueError("自主预算检查点请求状态计数超过已占用请求数")
        if any(
            reported[key] > charged["attempted_requests" if key == "requests" else key]
            for key in usage_keys
        ):
            raise ValueError("自主预算检查点已报告用量超过已占用量")
    else:
        charged = dict.fromkeys(_BUDGET_KEYS, 0)
        counts = dict.fromkeys(count_keys, 0)
        reported = dict.fromkeys(usage_keys, 0)
    return limits, charged, counts, reported


def interrupt_pending_budget(snapshot: dict[str, Any]) -> dict[str, Any]:
    """终止持久请求等待，保留未知实耗的预留，不对已报告用量增减或退款。"""

    _, _, counts, _ = _validated_budget_state(snapshot.get("limits"), snapshot)
    result = deepcopy(snapshot)
    result["pending_requests"] = 0
    result["interrupted_requests"] = counts["interrupted_requests"] + counts["pending_requests"]
    return result


class AutonomousBudgetHooks(RunHooks[Any]):
    """根运行与所有专业 Agent 必须共享同一实例；不访问数据库或重写外层账本。"""

    def __init__(
        self, reservation: Mapping[str, int], *, checkpoint: dict[str, Any] | None = None,
        on_change: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._limits, self._charged, counts, reported = _validated_budget_state(reservation, checkpoint)
        self._pending: dict[RunContextWrapper[Any], dict[str, int]] = {}
        self._completed = counts["completed_requests"]
        # 旧进程中未收到回执的请求继续占用原额度，不能恢复旧 Python 上下文对象或退款。
        self._interrupted = counts["interrupted_requests"] + counts["pending_requests"]
        self._reported = reported
        self._on_change = on_change
        self._lock = Lock()
        self._require_budget(self._charged)

    def _persist(self) -> None:
        if self._on_change is not None:
            self._on_change(self.snapshot())

    def _require_budget(self, projected: dict[str, int]) -> None:
        exceeded = [
            f"{key}={projected[key]}/{self._limits[key]}"
            for key in _BUDGET_KEYS if projected[key] > self._limits[key]
        ]
        if exceeded:
            raise AgentExecutionBudgetExceeded("本轮父子 Agent 共享预算耗尽: " + ", ".join(exceeded))

    async def on_llm_start(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        system_prompt: str | None,
        input_items: list[Any],
    ) -> None:
        reservation = model_request_reservation(
            agent=agent, system_prompt=system_prompt, input_items=input_items,
        )
        with self._lock:
            if context in self._pending:
                raise RuntimeError("同一模型上下文存在尚未结算的请求")
            projected = {name: self._charged[name] + reservation[name] for name in _BUDGET_KEYS}
            self._require_budget(projected)
            self._charged = projected
            self._pending[context] = reservation
        # 在发送请求前保存预留；持久化失败时不得继续请求模型。
        self._persist()

    def settle_request(self, context: RunContextWrapper[Any], usage: Usage | None) -> None:
        """仅按当前响应的增量结算；共享 context.usage 已含子调用，不能重复累加。"""

        with self._lock:
            reservation = self._pending.pop(context, None)
            if reservation is None:
                raise RuntimeError("模型响应缺少对应的预算预留")
            charge = dict(reservation)
            if usage is not None:
                for name in self._reported:
                    self._reported[name] += max(0, int(getattr(usage, name)))
                # 只约束 SDK 模型请求。HTTP 传输重试不在本地 hook 边界内，接入方必须禁用。
                charge["attempted_requests"] = max(1, int(usage.requests))
                if any(int(getattr(usage, name)) > 0 for name in _BUDGET_KEYS[1:]):
                    # 部分网关只返回总量，未报告的分项不能当成零消耗退款。
                    for name in ("input_tokens", "output_tokens"):
                        reported = int(getattr(usage, name))
                        if reported > 0:
                            charge[name] = reported
                    charge["total_tokens"] = max(
                        int(usage.total_tokens), charge["input_tokens"] + charge["output_tokens"],
                    )
            self._charged = {
                name: self._charged[name] - reservation[name] + charge[name]
                for name in _BUDGET_KEYS
            }
            self._completed += 1
        self._persist()
        self._require_budget(self._charged)

    def interrupt_requests(self, *, execution_context: Any) -> None:
        """结束当前调用域的未知用量请求，保留原预留且不影响其他并行调用。"""
        with self._lock:
            contexts = [context for context in self._pending if context.context is execution_context]
            for context in contexts:
                del self._pending[context]
            self._interrupted += len(contexts)
        if contexts:
            self._persist()

    async def on_llm_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        response: ModelResponse,
    ) -> None:
        self.settle_request(context, response.usage)

    def observe(self) -> dict[str, Any]:
        """读取根与专业 Agent 共用的即时余额；观察不预留、不结算，也不重置额度。"""

        saved = self.snapshot()
        return {
            "scope": "shared_root_and_specialist_model_calls",
            "limits": saved["limits"],
            "charged": saved["charged"],
            "remaining": {
                key: max(0, saved["limits"][key] - saved["charged"][key])
                for key in _BUDGET_KEYS
            },
            "pending_requests": saved["pending_requests"],
            "completed_requests": saved["completed_requests"],
            "interrupted_requests": saved["interrupted_requests"],
        }

    def scoped_requests(self, max_requests: int) -> ScopedRequestBudgetHooks:
        """专业会话另设局部请求数，Token 和阶段总请求仍只在当前账本计费。"""
        return ScopedRequestBudgetHooks(self, max_requests=max_requests)

    def snapshot(self) -> dict[str, Any]:
        """诊断只包含额度，不携带模型输入、工具内容或认证信息。"""

        with self._lock:
            return {
                "limits": dict(self._limits),
                "charged": dict(self._charged),
                "pending_requests": len(self._pending),
                "completed_requests": self._completed,
                "interrupted_requests": self._interrupted,
                "reported_usage": dict(self._reported),
                "request_scope": "sdk_model_calls",
            }


class ScopedRequestBudgetHooks(RunHooks[Any]):
    """局部限制沿专业 Agent 的工具咨询传递，不建立第二份 Token 账本。"""

    def __init__(self, parent: AutonomousBudgetHooks | ScopedRequestBudgetHooks, *, max_requests: int) -> None:
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("专业会话子树请求上限必须是正整数")
        self._parent = parent
        self._limit = max_requests
        self._attempted_requests = 0
        self._start_lock = asyncio.Lock()

    async def on_llm_start(
        self, context: RunContextWrapper[Any], agent: Agent[Any],
        system_prompt: str | None, input_items: list[Any],
    ) -> None:
        async with self._start_lock:
            if self._attempted_requests >= self._limit:
                raise AgentSubtreeRequestBudgetExceeded(
                    f"专业会话及咨询子树请求预算耗尽: attempted_requests={self._attempted_requests}/{self._limit}"
                )
            # 父级准入先成功才记局部次数；局部拒绝也不能占用阶段额度。
            await self._parent.on_llm_start(context, agent, system_prompt, input_items)
            self._attempted_requests += 1

    def settle_request(self, context: RunContextWrapper[Any], usage: Usage | None) -> None:
        self._parent.settle_request(context, usage)

    def interrupt_requests(self, *, execution_context: Any) -> None:
        self._parent.interrupt_requests(execution_context=execution_context)

    async def on_llm_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], response: ModelResponse,
    ) -> None:
        self.settle_request(context, response.usage)

    def scoped_requests(self, max_requests: int) -> ScopedRequestBudgetHooks:
        return ScopedRequestBudgetHooks(self, max_requests=max_requests)

    def _local_scope(self) -> dict[str, int]:
        return {"limit": self._limit, "attempted_requests": self._attempted_requests,
                "remaining_requests": self._limit - self._attempted_requests}

    def observe(self) -> dict[str, Any]:
        return {**self._parent.observe(), "local_request_scope": self._local_scope()}

    def snapshot(self) -> dict[str, Any]:
        return {**self._parent.snapshot(), "local_request_scope": self._local_scope()}
