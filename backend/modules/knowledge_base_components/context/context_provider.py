"""按需组装 RAG、长期记忆和项目上下文的当前阶段入口。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable, Mapping

from sqlalchemy.orm import Session

from modules.knowledge_base_components.context.context_memory_repository import (
    ContextMemoryRepository,
    ContextMemorySourcePort,
    _query_terms,
)
from modules.knowledge_base_components.context.knowledge_context_source import (
    KnowledgeBaseContextSourcePort,
)
from modules.knowledge_base_components.ports.context_memory_port import (
    CONTEXT_TEXT_SEPARATOR,
    ContextBudget,
    ContextManifest,
    ContextQuery,
    ContextSourcePort,
    ContextSourceRef,
    ContextCandidate,
    assemble_context,
    render_selected_context,
)


@dataclass(frozen=True)
class ContextBundle:
    """一次按需取数的正文和可审计 manifest。"""

    manifest: ContextManifest
    rendered_text: str


class ShortTermStateContextSourcePort(ContextSourcePort):
    """把当前执行上下文的动作状态作为显式的短期来源。

    短期状态不写入长期记录表，也不从数据库猜测。调用方必须绑定当前运行
    的状态读取器；没有绑定或状态为空时返回 unavailable，便于 Agent 区分
    “当前没有状态”和“当前状态没有命中”。
    """

    source_kind = "short_term_state"

    def __init__(
        self, state_loader: Callable[[], Mapping[str, object] | None], *,
        run_id: int, node_key: str, user_id: int, project_id: int,
    ):
        if not callable(state_loader):
            raise ValueError("短期状态读取器必须可调用")
        if type(run_id) is not int or run_id < 1:
            raise ValueError("短期状态读取器必须绑定正整数 run_id")
        if not str(node_key or "").strip():
            raise ValueError("短期状态读取器必须绑定 node_key")
        if type(user_id) is not int or user_id < 1 or type(project_id) is not int or project_id < 1:
            raise ValueError("短期状态读取器必须绑定有效用户和项目")
        self._state_loader = state_loader
        self._run_id = run_id
        self._node_key = str(node_key).strip()
        self._user_id = user_id
        self._project_id = project_id

    @staticmethod
    def _safe_state(state: Mapping[str, object]) -> tuple[dict[str, object], int]:
        """在结构边界排除凭据字段；不能把完整运行态直接当作模型上下文。"""

        sensitive = re.compile(
            r"(?:password|passwd|secret|authorization|credential|client_?secret|api_?key|private_?key|"
            r"access_?token|refresh_?token|session_?token|claim_?token|token|密码|密钥|令牌)",
            re.IGNORECASE,
        )
        result: dict[str, object] = {}
        stack: list[tuple[object, object]] = [(state, result)]
        excluded = 0
        while stack:
            current, target = stack.pop()
            entries = current.items() if isinstance(current, Mapping) else enumerate(current)
            for key, value in entries:
                if isinstance(current, Mapping) and sensitive.fullmatch(str(key)):
                    excluded += 1
                    continue
                if isinstance(value, Mapping):
                    copied: object = {}
                elif isinstance(value, (list, tuple)):
                    copied = []
                else:
                    copied = value
                if isinstance(target, dict):
                    target[str(key)] = copied
                else:
                    target.append(copied)
                if isinstance(value, (Mapping, list, tuple)):
                    stack.append((value, copied))
        return result, excluded

    @staticmethod
    def _encode(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def retrieve(
        self, query: ContextQuery, *, budget: ContextBudget,
    ) -> list[ContextCandidate]:
        if query.user_id != self._user_id or query.project_id != self._project_id:
            return [self._unavailable("context_scope_mismatch")]
        try:
            state = self._state_loader()
        except Exception as error:
            return [self._unavailable("state_loader_error", error_type=type(error).__name__)]
        if not isinstance(state, Mapping) or not state:
            return [self._unavailable("state_unavailable")]
        state_hash = hashlib.sha256(self._encode(dict(state)).encode("utf-8")).hexdigest()
        safe_state, excluded = self._safe_state(state)
        text = self._encode(safe_state)

        def candidate(path: str, text: str, score: float, *, kind: str, unit_hash: str = "") -> ContextCandidate:
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return ContextCandidate(
                source=ContextSourceRef(
                    source_kind=self.source_kind,
                    source_id=f"run:{self._run_id}:node:{self._node_key}" + (f"#{path}" if path else ""),
                    scope="project",
                    scope_id=self._project_id,
                    revision=state_hash,
                    content_hash=content_hash,
                ),
                text=text,
                score=score,
                evidence={
                    "run_id": self._run_id,
                    "node_key": self._node_key,
                    "state_hash": state_hash,
                    "state_path": path,
                    "fragment_hash": content_hash,
                    "fragment_kind": kind,
                    "unit_hash": unit_hash or content_hash,
                    "excluded_sensitive_field_count": excluded,
                },
            )

        if len(text) <= budget.max_chars:
            return [candidate("", text, 1.0, kind="complete_state")]

        terms = _query_terms(query.text)
        matches: list[tuple[float, str, str, str]] = []
        # 迭代扫描容器，数组中的每个记录是完整语义单元，不能为凑预算拆开规则。
        # 只在字典容器之间继续下钻；不递归展开同一条记录中的条件和动作。
        stack: list[tuple[str, object, dict[str, object], bool]] = [("", safe_state, {}, False)]
        while stack:
            path, value, ancestors, atomic = stack.pop()
            body = self._encode(value)
            searchable = f"{path} {body}".casefold()
            hits = sum(term in searchable for term in terms)
            if path and hits:
                rendered = self._encode({"state_path": path, "ancestor_fields": ancestors, "value": value})
                if len(rendered) <= budget.max_chars:
                    matches.append((hits / len(terms), path, rendered, hashlib.sha256(body.encode()).hexdigest()))
            if atomic:
                continue
            if isinstance(value, dict):
                fields = {key: item for key, item in value.items() if not isinstance(item, (dict, list))}
                inherited = {**ancestors, **({path: fields} if fields else {})}
                for key, item in value.items():
                    escaped = str(key).replace("~", "~0").replace("/", "~1")
                    child_path = f"{path}/{escaped}"
                    if isinstance(item, (dict, list)):
                        stack.append((child_path, item, inherited, False))
            elif isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value):
                stack.extend((f"{path}/{index}", item, ancestors, True) for index, item in enumerate(value))

        selected: list[ContextCandidate] = []
        paths: set[str] = set()
        units: set[str] = set()
        remaining = budget.max_chars
        limit = min(query.max_candidates, budget.max_candidates)
        for score, path, rendered, unit_hash in sorted(matches, key=lambda item: (-item[0], len(item[2]), item[1])):
            if unit_hash in units:
                continue
            if any(path == old or path.startswith(old + "/") or old.startswith(path + "/") for old in paths):
                continue
            if len(rendered) > remaining or len(selected) >= limit:
                continue
            selected.append(candidate(path, rendered, score, kind="complete_structured_unit", unit_hash=unit_hash))
            paths.add(path)
            units.add(unit_hash)
            remaining -= len(rendered) + len(CONTEXT_TEXT_SEPARATOR)

        shape = {
            key: {"type": type(value).__name__, **(
                {"item_count": len(value)} if isinstance(value, (dict, list))
                else {"char_count": len(value)} if isinstance(value, str) else {"value": value}
            )}
            for key, value in safe_state.items()
        }
        summary = self._encode({"state_sha256": state_hash, "state_fields": shape,
                                "matched_unit_count": len(matches)})
        if not selected or (len(selected) < limit and len(summary) <= remaining):
            selected.append(candidate("", summary, 0.0, kind="structure_index"))
        return selected

    def _unavailable(self, reason: str, **details: object) -> ContextCandidate:
        return ContextCandidate(
            source=ContextSourceRef(
                source_kind=self.source_kind,
                source_id=f"run:{self._run_id}:node:{self._node_key}",
                scope="project",
                scope_id=self._project_id,
                status="unavailable",
            ),
            evidence={"reason": reason, **details},
        )


class OnDemandContextProvider:
    """只调用查询声明的来源，并把所有结果交给同一个确定性组装器。"""

    def __init__(self, sources: Mapping[str, ContextSourcePort]):
        normalized = {
            str(source_kind).strip(): source
            for source_kind, source in sources.items()
            if str(source_kind).strip()
        }
        if not normalized:
            raise ValueError("按需上下文至少需要一个来源 Port")
        for source_kind, source in normalized.items():
            if str(getattr(source, "source_kind", "")).strip() != source_kind:
                raise ValueError(f"上下文来源 Port 身份不一致: {source_kind}")
        self._sources = normalized

    @property
    def source_kinds(self) -> tuple[str, ...]:
        return tuple(self._sources)

    def retrieve(self, *, query: ContextQuery, budget: ContextBudget) -> ContextBundle:
        requested = query.source_kinds or self.source_kinds
        candidates = []
        unavailable: list[str] = []
        for source_kind in requested:
            source = self._sources.get(source_kind)
            if source is None:
                unavailable.append(source_kind)
                continue
            source_candidates = source.retrieve(query, budget=budget)
            candidates.extend(source_candidates)
        manifest = assemble_context(
            query=query,
            candidates=candidates,
            budget=budget,
            unavailable_source_kinds=unavailable,
        )
        return ContextBundle(
            manifest=manifest,
            rendered_text=render_selected_context(manifest),
        )


def build_context_provider(
    db: Session,
    *,
    short_term_state: ShortTermStateContextSourcePort | None = None,
) -> OnDemandContextProvider:
    """用当前数据库装配真实来源；短期状态必须由当前运行显式注入。"""

    memory = ContextMemoryRepository(db)
    sources: dict[str, ContextSourcePort] = {
        "project_knowledge": KnowledgeBaseContextSourcePort(db),
    }
    for source_kind in ("user_preference", "long_term_memory", "validated_feedback"):
        sources[source_kind] = ContextMemorySourcePort(memory, source_kind)
    if short_term_state is not None:
        if short_term_state.source_kind != "short_term_state":
            raise ValueError("短期状态 Port 身份不一致")
        sources[short_term_state.source_kind] = short_term_state
    return OnDemandContextProvider(sources)


__all__ = [
    "ContextBundle",
    "OnDemandContextProvider",
    "ShortTermStateContextSourcePort",
    "build_context_provider",
]
