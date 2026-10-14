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

    async def invoke_agents(self, *, agent_key: str, inputs: list[dict[str, Any]],
                            item_postprocessor: str,
                            candidate_validators: list[Callable | None] | None = None,
                            alternate_output_validators: list[dict[str, Callable] | None] | None = None,
                            return_subtree_failures: bool = False) -> list[Any]:
        """并发等待独立专家；状态验收、回执和数据库操作仍在同一事件循环串行完成。"""
        from .autonomous_budget import AgentSubtreeRequestBudgetExceeded

        if self.invoke_agent is None or not 1 <= self.agent_concurrency <= 16:
            raise ValueError("专业批次缺少调用入口或有效并发额度")
        validators = alternate_output_validators if alternate_output_validators is not None else [None] * len(inputs)
        candidates = candidate_validators if candidate_validators is not None else [None] * len(inputs)
        if len(validators) != len(inputs) or len(candidates) != len(inputs):
            raise ValueError("专业批次的验收契约必须与完整输入逐项对应")
        semaphore = asyncio.Semaphore(self.agent_concurrency)

        async def invoke(item, alternative, candidate):
            async with semaphore:
                try:
                    return await self.invoke_agent(
                        agent_key=agent_key, input_payload=item, item_postprocessor=item_postprocessor,
                        **({"alternate_output_validators": alternative} if alternative is not None else {}),
                        **({"validate_candidate": candidate} if candidate is not None else {}),
                    )
                except AgentSubtreeRequestBudgetExceeded as error:
                    # 局部失败不取消其他已经准入的独立任务，成功回执由 SDK 原入口保存。
                    return error

        tasks = [asyncio.create_task(invoke(item, alternative, candidate))
                 for item, alternative, candidate in zip(inputs, validators, candidates, strict=True)]
        try:
            outputs = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        failures = [index for index, output in enumerate(outputs) if isinstance(output, Exception)]
        if failures and not return_subtree_failures:
            error = outputs[failures[0]]
            error.diagnostic = {
                **dict(getattr(error, "diagnostic", None) or {}),
                "batch_progress": {"total_count": len(inputs), "failed_indexes": failures,
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
        self._repair_strategies: dict[str, OutputRepairStrategy] = {}

    def register(
        self,
        handler_key: str,
        handler: ToolHandler,
        *,
        parallel_safe: bool = False,
        state_only: bool = False,
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

BUILTIN_AGENT_SPECS = (
    *TEST_GENERATION_AGENT_SPECS,
    *AUTOMATION_EVALUATION_AGENT_SPECS,
    *TEST_CASE_EVALUATION_AGENT_SPECS,
)
BUILTIN_TOOL_SPECS = (
    *DOCUMENT_AGENT_TOOL_SPECS,
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
