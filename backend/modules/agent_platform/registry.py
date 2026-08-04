from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session


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


ToolHandler = Callable[[ToolExecutionContext, dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, handler_key: str, handler: ToolHandler) -> None:
        key = str(handler_key or "").strip()
        if not key:
            raise ValueError("工具处理器键不能为空")
        if key in self._handlers:
            raise ValueError(f"工具处理器重复注册: {key}")
        self._handlers[key] = handler

    def resolve(self, handler_key: str) -> ToolHandler:
        key = str(handler_key or "").strip()
        handler = self._handlers.get(key)
        if handler is None:
            raise KeyError(f"未注册工具处理器: {key}")
        return handler

    def keys(self) -> list[str]:
        return sorted(self._handlers)


tool_registry = ToolRegistry()


# 内置领域定义单独维护，平台注册表只负责可信处理器的索引。
from .automation_evaluation_workflow import (  # noqa: E402
    BUILTIN_AGENT_SPECS as AUTOMATION_EVALUATION_AGENT_SPECS,
    BUILTIN_TOOL_SPECS as AUTOMATION_EVALUATION_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS as AUTOMATION_EVALUATION_WORKFLOW_SPECS,
    register_automation_evaluation_tools,
)
from .test_generation_workflow import (  # noqa: E402
    BUILTIN_AGENT_SPECS as TEST_GENERATION_AGENT_SPECS,
    BUILTIN_TOOL_SPECS as TEST_GENERATION_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS as TEST_GENERATION_WORKFLOW_SPECS,
    register_test_generation_tools,
)
from .test_case_evaluation_workflow import (  # noqa: E402
    BUILTIN_AGENT_SPECS as TEST_CASE_EVALUATION_AGENT_SPECS,
    BUILTIN_TOOL_SPECS as TEST_CASE_EVALUATION_TOOL_SPECS,
    BUILTIN_WORKFLOW_SPECS as TEST_CASE_EVALUATION_WORKFLOW_SPECS,
    register_test_case_evaluation_tools,
)

BUILTIN_AGENT_SPECS = (
    *TEST_GENERATION_AGENT_SPECS,
    *AUTOMATION_EVALUATION_AGENT_SPECS,
    *TEST_CASE_EVALUATION_AGENT_SPECS,
)
BUILTIN_TOOL_SPECS = (
    *TEST_GENERATION_TOOL_SPECS,
    *AUTOMATION_EVALUATION_TOOL_SPECS,
    *TEST_CASE_EVALUATION_TOOL_SPECS,
)
BUILTIN_WORKFLOW_SPECS = (
    *TEST_GENERATION_WORKFLOW_SPECS,
    *AUTOMATION_EVALUATION_WORKFLOW_SPECS,
    *TEST_CASE_EVALUATION_WORKFLOW_SPECS,
)

register_test_generation_tools(tool_registry)
register_automation_evaluation_tools(tool_registry)
register_test_case_evaluation_tools(tool_registry)
