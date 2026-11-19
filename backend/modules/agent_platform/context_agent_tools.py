"""主 Agent 按需读取项目知识、记忆和当前短期状态的工具。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any, TYPE_CHECKING

from modules.knowledge_base_components.context.context_provider import (
    ShortTermStateContextSourcePort,
    build_context_provider,
)
from modules.knowledge_base_components.ports.context_memory_port import (
    ContextBudget,
    ContextQuery,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext, ToolRegistry


_SOURCE_KINDS = [
    "project_knowledge",
    "user_preference",
    "long_term_memory",
    "validated_feedback",
    "short_term_state",
]


def retrieve_context(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """按当前 Agent 的明确知识缺口取数，并保存可重建的 manifest。"""

    query_text = str(arguments.get("query") or "").strip()
    source_kinds = tuple(str(value or "").strip() for value in list(arguments.get("source_kinds") or []))
    if not query_text:
        raise ValueError("上下文查询必须说明要解决的知识缺口")
    if not source_kinds or any(value not in _SOURCE_KINDS for value in source_kinds):
        raise ValueError("上下文查询必须选择当前允许的来源类型")
    if len(source_kinds) != len(set(source_kinds)):
        raise ValueError("上下文查询来源不能重复")
    max_candidates = int(arguments.get("max_candidates") or 5)
    max_chars = int(arguments.get("max_chars") or 8000)
    query = ContextQuery(
        text=query_text,
        user_id=context.user_id,
        project_id=context.project_id,
        source_kinds=source_kinds,
        max_candidates=max_candidates,
    )
    provider = getattr(context, "context_provider", None)
    if provider is None:
        journal = getattr(context, "action_journal", None)

        def load_short_term_state() -> dict[str, Any]:
            if journal is not None:
                state = journal.load_state()
                if not isinstance(state, dict):
                    raise ValueError("短期上下文缺少已初始化的真实领域状态")
                return state
            return deepcopy(getattr(context, "action_state", {}) or {})

        short_term = ShortTermStateContextSourcePort(
            load_short_term_state,
            run_id=context.run_id,
            node_key=context.node_key,
            user_id=context.user_id,
            project_id=context.project_id,
        )
        provider = build_context_provider(context.db, short_term_state=short_term)
        context.context_provider = provider
    bundle = provider.retrieve(query=query, budget=ContextBudget(
        max_candidates=max_candidates,
        max_chars=max_chars,
    ))
    rendered_hash = hashlib.sha256(bundle.rendered_text.encode("utf-8")).hexdigest()
    manifest_record = {
        "query_fingerprint": bundle.manifest.query_fingerprint,
        "query_source_kinds": list(source_kinds),
        "status": bundle.manifest.status,
        "manifest": bundle.manifest.to_dict(),
        "rendered_text_sha256": rendered_hash,
        "rendered_text_chars": len(bundle.rendered_text),
    }
    state = getattr(context, "action_state", None)
    # 已初始化领域状态随动作日志原子保存，是中断恢复后检索轨迹的权威来源。
    # 普通无状态工具调用仍把轨迹保存到当前运行产物，避免引入生成领域依赖。
    history = state.get("context_retrievals") if isinstance(state, dict) and state else context.artifacts.get("context_retrievals")
    if history is not None and not isinstance(history, list):
        raise ValueError("上下文检索轨迹必须是数组")
    retrievals = list(history or [])
    retrievals.append(deepcopy(manifest_record))
    if isinstance(state, dict) and state:
        state["context_retrievals"] = deepcopy(retrievals)
    context.artifacts["context_retrievals"] = retrievals
    return {
        "status": bundle.manifest.status,
        "query_fingerprint": bundle.manifest.query_fingerprint,
        "manifest": bundle.manifest.to_dict(),
        "context_text": bundle.rendered_text,
        "context_text_sha256": rendered_hash,
    }


BUILTIN_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tool_key": "retrieve_context",
        "name": "按需读取上下文",
        "description": "按当前知识缺口选择项目知识、用户偏好、长期记忆、历史反馈或短期状态，并返回可审计 manifest。",
        "handler_key": "agent.context.retrieve",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "source_kinds": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": _SOURCE_KINDS},
                },
                "max_candidates": {"type": "integer", "minimum": 1, "maximum": 20},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 20000},
            },
            "required": ["query", "source_kinds", "max_candidates", "max_chars"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["ready", "partial", "empty", "unavailable"]},
                "query_fingerprint": {"type": "string", "minLength": 64, "maxLength": 64},
                "manifest": {"type": "object"},
                "context_text": {"type": "string"},
                "context_text_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
            },
            "required": ["status", "query_fingerprint", "manifest", "context_text", "context_text_sha256"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
)


def register_context_tools(registry: ToolRegistry) -> None:
    # 上下文读取只观察真实来源并更新当前运行的可审计 manifest；它不修改外部业务状态。
    # 持久自主节点需要据此判断中断恢复是否可以安全重放，因此必须明确声明为 state_only。
    registry.register("agent.context.retrieve", retrieve_context, state_only=True)


__all__ = ["BUILTIN_TOOL_SPECS", "register_context_tools", "retrieve_context"]
