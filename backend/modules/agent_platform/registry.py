from __future__ import annotations

import hashlib
import inspect
import json
import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from .output_repair import OutputRepairStrategy
from .retry_policy import model_transport_failure_kind


@dataclass
class ToolExecutionContext:
    db: Session
    user_id: int
    project_id: int
    run_id: int
    node_key: str
    run_input: dict[str, Any]
    artifacts: dict[str, Any] = field(default_factory=dict)
    executed_tools: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    claim_token: str = ""
    action_input: dict[str, Any] = field(default_factory=dict)
    action_state: dict[str, Any] = field(default_factory=dict)
    action_journal: Any = None
    restoring_action: bool = False
    invoke_agent: Callable[..., Awaitable[dict[str, Any]]] | None = None
    agent_concurrency: int = 1
    confirmed_conflicts: dict[str, Any] = field(default_factory=dict)
    context_provider: Any = None
    stop_on_failure: bool = False
    _request_failures: list[BaseException] = field(default_factory=list, repr=False)

    def record_failure(self, error: BaseException) -> None:
        """严格运行共享首错闩锁；子上下文复制引用，不能重新发放请求准入。"""
        if self.stop_on_failure and not self._request_failures:
            self._request_failures.append(error)

    def require_request_allowed(self) -> None:
        if self.stop_on_failure and self._request_failures:
            raise self._request_failures[0]

    async def invoke_agents(self, *, agent_key: str, inputs: list[dict[str, Any]],
                            item_postprocessor: str | None = None,
                            candidate_validators: list[Callable | None] | None = None,
                            alternate_output_validators: list[dict[str, Callable] | None] | None = None,
                            return_subtree_failures: bool = False) -> list[Any]:
        """并发等待独立专家；状态验收、回执和数据库操作仍在同一事件循环串行完成。"""
        from .autonomous_budget import AgentExecutionBudgetExceeded, AgentSubtreeRequestBudgetExceeded

        if self.invoke_agent is None or not 1 <= self.agent_concurrency <= 16:
            raise ValueError("专业批次缺少调用入口或有效并发额度")
        validators = alternate_output_validators if alternate_output_validators is not None else [None] * len(inputs)
        candidates = candidate_validators if candidate_validators is not None else [None] * len(inputs)
        if len(validators) != len(inputs) or len(candidates) != len(inputs):
            raise ValueError("专业批次的验收契约必须与完整输入逐项对应")
        semaphore = asyncio.Semaphore(1 if self.stop_on_failure else self.agent_concurrency)

        class _SubtreeCancelled(Exception):
            """把子 Agent 主动取消从 TaskGroup 的正常取消语义中提升出来。"""

        budget_failure: AgentExecutionBudgetExceeded | None = None
        not_started_indexes: set[int] = set()

        async def invoke(item, alternative, candidate, position):
            nonlocal budget_failure
            async with semaphore:
                # 全局预算已确定拒绝后，不再启动本批排队中的专业会话。
                if budget_failure is not None:
                    not_started_indexes.add(position)
                    return budget_failure
                self.require_request_allowed()
                try:
                    return await self.invoke_agent(
                        agent_key=agent_key, input_payload=item, item_postprocessor=item_postprocessor,
                        **({"alternate_output_validators": alternative} if alternative is not None else {}),
                        **({"validate_candidate": candidate} if candidate is not None else {}),
                    )
                except AgentSubtreeRequestBudgetExceeded as error:
                    if self.stop_on_failure:
                        self.record_failure(error)
                        raise
                    # 局部失败不取消其他已经准入的独立任务，成功回执由 SDK 原入口保存。
                    return error
                except AgentExecutionBudgetExceeded as error:
                    if self.stop_on_failure:
                        self.record_failure(error)
                        raise
                    # 拒绝新增请求不取消已经付费的兄弟；让真实响应及验收回执收敛。
                    if budget_failure is None:
                        budget_failure = error
                    return error
                except asyncio.CancelledError as error:
                    self.record_failure(error)
                    # TaskGroup 会把 CancelledError 当作正常收敛并吞掉；子 Agent
                    # 自身取消必须回到上层重规划链，不能变成一个 None 结果。
                    raise _SubtreeCancelled() from error
                except Exception as error:
                    if self.stop_on_failure:
                        self.record_failure(error)
                        raise
                    if model_transport_failure_kind(error) is None:
                        raise
                    # 上游暂态故障只终止当前请求，其他独立任务继续保存完整验收回执。
                    # 整批收敛后仍抛出原故障，由原节点预算决定是否续跑。
                    return error

        # 使用结构化并发保证父调用结束时所有子任务都已收敛；TaskGroup
        # 会在任一未处理异常时取消兄弟任务，避免脱离请求生命周期的后台协程。
        outputs: list[Any] = [None] * len(inputs)
        try:
            async with asyncio.TaskGroup() as group:
                for index, (item, alternative, candidate) in enumerate(
                    zip(inputs, validators, candidates, strict=True)
                ):
                    async def collect(
                        position: int,
                        current: dict[str, Any],
                        alternate: dict[str, Callable] | None,
                        validator: Callable | None,
                    ) -> None:
                        outputs[position] = await invoke(current, alternate, validator, position)

                    group.create_task(collect(index, item, alternative, candidate))
        except BaseExceptionGroup as error_group:
            # 保持原有调用契约：单个子任务失败时向上游抛出原异常，
            # 不把结构化并发容器暴露给重试与诊断链。
            def flatten(error: BaseException) -> list[BaseException]:
                if isinstance(error, BaseExceptionGroup):
                    return [child for item in error.exceptions for child in flatten(item)]
                return [error]

            errors = flatten(error_group)
            ordinary = [error for error in errors if not isinstance(error, _SubtreeCancelled)]
            if ordinary:
                raise ordinary[0]
            if errors:
                raise asyncio.CancelledError()
            raise
        failures = [index for index, output in enumerate(outputs) if isinstance(output, Exception)]
        transport_failures = [
            index for index in failures if model_transport_failure_kind(outputs[index]) is not None
        ]
        if budget_failure is not None or transport_failures or (failures and not return_subtree_failures):
            # 全局预算拒绝不能作为领域成功返回；仅在已准入兄弟完成后交回运行时。
            error = budget_failure if budget_failure is not None else outputs[(transport_failures or failures)[0]]
            error.diagnostic = {
                **dict(getattr(error, "diagnostic", None) or {}),
                "batch_progress": {"total_count": len(inputs),
                                   "failed_indexes": [i for i in failures if i not in not_started_indexes],
                                   "not_started_indexes": sorted(not_started_indexes),
                                   "validated_indexes": [i for i in range(len(inputs)) if i not in failures]},
            }
            raise error
        return outputs


ToolHandler = Callable[[ToolExecutionContext, dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        self._parallel_safe_handlers: set[str] = set()
        self._state_only_handlers: set[str] = set()
        self._cache_safe_handlers: set[str] = set()
        self._repair_strategies: dict[str, OutputRepairStrategy] = {}

    def register(
        self,
        handler_key: str,
        handler: ToolHandler,
        *,
        parallel_safe: bool = False,
        state_only: bool = False,
        cache_safe: bool = False,
    ) -> None:
        key = str(handler_key or "").strip()
        if not key:
            raise ValueError("工具处理器键不能为空")
        if key in self._handlers:
            raise ValueError(f"工具处理器重复注册: {key}")
        self._handlers[key] = handler
        if parallel_safe:
            self._parallel_safe_handlers.add(key)
        if state_only:
            self._state_only_handlers.add(key)
        if cache_safe:
            self._cache_safe_handlers.add(key)

    def is_cache_safe(self, handler_key: str) -> bool:
        """纯计算声明来自可信注册代码，不能由用户配置自行授予。"""
        self.resolve(handler_key)
        return handler_key in self._cache_safe_handlers

    def cache_safe_keys(self) -> list[str]:
        return sorted(self._cache_safe_handlers)

    def is_state_only(self, handler_key: str) -> bool:
        """仅声明为无外部副作用的领域状态动作，才允许在中断后重新计算。"""
        self.resolve(handler_key)
        return handler_key in self._state_only_handlers

    def state_only_keys(self) -> list[str]:
        return sorted(self._state_only_handlers)

    def resolve(self, handler_key: str) -> ToolHandler:
        key = str(handler_key or "").strip()
        handler = self._handlers.get(key)
        if handler is None:
            raise KeyError(f"未注册工具处理器: {key}")
        return handler

    def keys(self) -> list[str]:
        return sorted(self._handlers)

    def is_parallel_safe(self, handler_key: str) -> bool:
        key = str(handler_key or "").strip()
        self.resolve(key)
        return key in self._parallel_safe_handlers

    def parallel_safe_keys(self) -> list[str]:
        return sorted(self._parallel_safe_handlers)

    def register_repair_strategy(self, key: str, strategy: OutputRepairStrategy) -> None:
        if not key or key in self._repair_strategies:
            raise ValueError(f"修复策略键为空或重复注册: {key}")
        self._repair_strategies[key] = strategy

    def resolve_repair_strategy(self, key: str) -> OutputRepairStrategy:
        strategy = self._repair_strategies.get(key)
        if strategy is None:
            raise KeyError(f"未注册修复策略: {key}")
        return strategy

    def repair_strategy_keys(self) -> list[str]:
        return sorted(self._repair_strategies)


tool_registry = ToolRegistry()


# 内置领域定义单独维护，平台注册表只负责可信处理器的索引。
from .automation_evaluation_workflow import (  # noqa: E402
    BUILTIN_AGENT_SPECS as AUTOMATION_EVALUATION_AGENT_SPECS,
    BUILTIN_TOOL_SPECS as AUTOMATION_EVALUATION_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS as AUTOMATION_EVALUATION_WORKFLOW_SPECS,
    register_automation_evaluation_tools,
)
from .test_generation_builtin_specs import (  # noqa: E402
    BUILTIN_AGENT_SPECS as TEST_GENERATION_AGENT_SPECS,
    BUILTIN_TOOL_SPECS as TEST_GENERATION_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS as TEST_GENERATION_WORKFLOW_SPECS,
)
from .test_generation_workflow import (  # noqa: E402
    register_test_generation_tools,
)
from .test_case_evaluation_workflow import (  # noqa: E402
    BUILTIN_AGENT_SPECS as TEST_CASE_EVALUATION_AGENT_SPECS,
    BUILTIN_TOOL_SPECS as TEST_CASE_EVALUATION_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS as TEST_CASE_EVALUATION_WORKFLOW_SPECS,
    register_test_case_evaluation_tools,
)
from .document_agent_tools import (  # noqa: E402
    BUILTIN_TOOL_SPECS as DOCUMENT_AGENT_TOOL_SPECS,
    register_document_agent_tools,
)
from .context_agent_tools import (  # noqa: E402
    BUILTIN_TOOL_SPECS as CONTEXT_AGENT_TOOL_SPECS,
    register_context_tools,
)

BUILTIN_AGENT_SPECS = (
    *TEST_GENERATION_AGENT_SPECS,
    *AUTOMATION_EVALUATION_AGENT_SPECS,
    *TEST_CASE_EVALUATION_AGENT_SPECS,
)
BUILTIN_TOOL_SPECS = (
    *DOCUMENT_AGENT_TOOL_SPECS,
    *CONTEXT_AGENT_TOOL_SPECS,
    *TEST_GENERATION_TOOL_SPECS,
    *AUTOMATION_EVALUATION_TOOL_SPECS,
    *TEST_CASE_EVALUATION_TOOL_SPECS,
)
BUILTIN_WORKFLOW_SPECS = (
    *TEST_GENERATION_WORKFLOW_SPECS,
    *AUTOMATION_EVALUATION_WORKFLOW_SPECS,
    *TEST_CASE_EVALUATION_WORKFLOW_SPECS,
)

register_document_agent_tools(tool_registry)
register_context_tools(tool_registry)
register_test_generation_tools(tool_registry)
register_automation_evaluation_tools(tool_registry)
register_test_case_evaluation_tools(tool_registry)


@lru_cache(maxsize=4096)
def _source_file_hash(path: str, modified_ns: int, size: int) -> str:
    """文件未变化时复用摘要，统一换行避免不同部署平台产生假性差异。"""

    source = Path(path).read_text(encoding="utf-8-sig")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _callable_identity(callback: Callable[..., Any]) -> str:
    return f"{callback.__module__}.{callback.__qualname__}"


def execution_source_signature() -> str:
    """恢复、缓存和持久行动共用完整源码身份，包含处理器间接调用的私有实现。"""

    backend_root = Path(__file__).resolve().parents[2]
    source_files: set[Path] = set()
    for directory in ("modules/agent_platform", "core/ai", "modules/knowledge_base_components"):
        source_files.update((backend_root / directory).rglob("*.py"))
    callbacks = [tool_registry.resolve(key) for key in tool_registry.keys()]
    for key in tool_registry.repair_strategy_keys():
        strategy = tool_registry.resolve_repair_strategy(key)
        callbacks.extend(getattr(strategy, name) for name in ("build_context", "feedback"))
    for callback in callbacks:
        module = inspect.getmodule(callback)
        source_path = inspect.getsourcefile(module) if module is not None else None
        if source_path is None:
            raise ValueError(f"执行处理器缺少可验证的源代码: {_callable_identity(callback)}")
        source_files.add(Path(source_path).resolve())
    source_hashes = []
    for path in sorted(source_files):
        stat = path.stat()
        relative_name = path.relative_to(backend_root).as_posix() if path.is_relative_to(backend_root) else path.as_posix()
        source_hashes.append((relative_name, _source_file_hash(str(path), stat.st_mtime_ns, stat.st_size)))
    encoded = json.dumps(source_hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def runtime_registry_signature() -> str:
    """生成 Web 与 Worker 都可独立计算的源码及注册契约指纹。"""

    payload = {
        "agents": BUILTIN_AGENT_SPECS,
        "tools": BUILTIN_TOOL_SPECS,
        "workflows": BUILTIN_WORKFLOW_SPECS,
        "handlers": {
            key: _callable_identity(tool_registry.resolve(key))
            for key in tool_registry.keys()
        },
        "parallel_safe_handlers": tool_registry.parallel_safe_keys(),
        "state_only_handlers": tool_registry.state_only_keys(),
        "cache_safe_handlers": tool_registry.cache_safe_keys(),
        "repair_strategies": {
            key: {
                name: _callable_identity(getattr(tool_registry.resolve_repair_strategy(key), name))
                for name in ("build_context", "feedback")
            }
            for key in tool_registry.repair_strategy_keys()
        },
        "execution_source_signature": execution_source_signature(),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
