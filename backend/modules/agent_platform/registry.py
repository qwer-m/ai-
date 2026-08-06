from __future__ import annotations

import hashlib
import inspect
import json
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
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


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


def runtime_registry_signature() -> str:
    """生成 Web 与 Worker 都可独立计算的运行时注册指纹。"""

    handler_sources: dict[str, str] = {}
    for key in tool_registry.keys():
        handler = tool_registry.resolve(key)
        try:
            source = inspect.getsource(handler)
        except (OSError, TypeError):
            source = f"{handler.__module__}.{handler.__qualname__}"
        handler_sources[key] = hashlib.sha256(source.encode("utf-8")).hexdigest()
    payload = {
        "agents": BUILTIN_AGENT_SPECS,
        "tools": BUILTIN_TOOL_SPECS,
        "workflows": BUILTIN_WORKFLOW_SPECS,
        "handlers": handler_sources,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
