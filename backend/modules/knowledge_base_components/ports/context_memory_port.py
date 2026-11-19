"""上下文来源、短期状态和长期记忆的当前可插拔契约。

这里定义 Agent 按需取数所需的身份、证据、预算和组装边界。知识库、运行态
检查点与长期记录分别实现来源 Port，主 Agent 只消费统一的 ContextManifest。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Iterable, Mapping, Protocol, Sequence


_SOURCE_KINDS = frozenset(
    {
        "project_knowledge",
        "user_preference",
        "long_term_memory",
        "validated_feedback",
        "short_term_state",
    }
)
_SCOPES = frozenset({"global", "project", "user"})
_CANDIDATE_STATUSES = frozenset({"available", "unavailable", "revoked", "stale"})
_ASSEMBLY_STATUSES = frozenset({"ready", "partial", "empty", "unavailable"})
CONTEXT_TEXT_SEPARATOR = "\n\n"


def _positive_identifier(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} 必须是正整数或 None")
    return value


def _clean_text(value: str, field_name: str, *, required: bool = True) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"{field_name} 不能为空")
    return result


@dataclass(frozen=True)
class ContextQuery:
    """一次按需取上下文的边界，不携带当前会话的完整 prompt。"""

    text: str
    user_id: int | None = None
    project_id: int | None = None
    source_kinds: tuple[str, ...] = ()
    max_candidates: int = 8

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _clean_text(self.text, "查询文本"))
        object.__setattr__(self, "user_id", _positive_identifier(self.user_id, "user_id"))
        object.__setattr__(self, "project_id", _positive_identifier(self.project_id, "project_id"))
        if type(self.max_candidates) is not int or self.max_candidates < 1:
            raise ValueError("max_candidates 必须是正整数")
        normalized = tuple(dict.fromkeys(str(item or "").strip() for item in self.source_kinds))
        unknown = sorted(item for item in normalized if item not in _SOURCE_KINDS)
        if unknown:
            raise ValueError(f"不支持的上下文来源类型: {', '.join(unknown)}")
        object.__setattr__(self, "source_kinds", normalized)

    @property
    def fingerprint(self) -> str:
        """用查询边界生成可持久化的稳定指纹，避免保存原始查询正文。"""

        payload = {
            "text": self.text,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "source_kinds": list(self.source_kinds),
            "max_candidates": self.max_candidates,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ContextSourceRef:
    """候选来源的身份、作用域、版本和撤销状态。"""

    source_kind: str
    source_id: str
    scope: str
    scope_id: int | str | None = None
    revision: str = ""
    content_hash: str = ""
    status: str = "available"

    def __post_init__(self) -> None:
        source_kind = _clean_text(self.source_kind, "source_kind")
        source_id = _clean_text(self.source_id, "source_id")
        scope = _clean_text(self.scope, "scope")
        if source_kind not in _SOURCE_KINDS:
            raise ValueError(f"不支持的上下文来源类型: {source_kind}")
        if scope not in _SCOPES:
            raise ValueError(f"不支持的上下文作用域: {scope}")
        if scope == "global" and self.scope_id is not None:
            raise ValueError("global 来源不能携带 scope_id")
        if scope != "global" and self.scope_id in (None, ""):
            raise ValueError(f"{scope} 来源必须携带 scope_id")
        status = _clean_text(self.status, "status")
        if status not in _CANDIDATE_STATUSES:
            raise ValueError(f"不支持的上下文来源状态: {status}")
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "revision", str(self.revision or "").strip())
        object.__setattr__(self, "content_hash", str(self.content_hash or "").strip().lower())
        object.__setattr__(self, "status", status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "revision": self.revision,
            "content_hash": self.content_hash,
            "status": self.status,
        }


@dataclass(frozen=True)
class ContextCandidate:
    """来源返回的一个候选片段。

    unavailable/revoked/stale 候选允许正文为空；这样来源适配器可以明确告诉组装器
    为什么某个长期记忆来源当前不可用，而不是静默当作“没有数据”。
    """

    source: ContextSourceRef
    text: str = ""
    score: float = 0.0
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        text = str(self.text or "").strip()
        if self.source.status == "available" and not text:
            raise ValueError("available 候选必须包含正文")
        if not isinstance(self.evidence, Mapping):
            raise ValueError("evidence 必须是对象")
        try:
            score = float(self.score)
        except (TypeError, ValueError) as error:
            raise ValueError("score 必须是数字") from error
        if score < 0 or score > 1:
            raise ValueError("score 必须位于 0 到 1 之间")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "evidence", dict(self.evidence))

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        value = {
            "source": self.source.to_dict(),
            "score": self.score,
            "evidence": dict(self.evidence),
        }
        if include_text:
            value["text"] = self.text
        return value

    @property
    def status(self) -> str:
        """候选状态的便捷读取，真实身份仍集中在 source。"""

        return self.source.status


@dataclass(frozen=True)
class ContextBudget:
    """组装器可消耗的候选数量和正文字符预算。"""

    max_candidates: int = 8
    max_chars: int = 12000

    def __post_init__(self) -> None:
        if type(self.max_candidates) is not int or self.max_candidates < 1:
            raise ValueError("max_candidates 必须是正整数")
        if type(self.max_chars) is not int or self.max_chars < 1:
            raise ValueError("max_chars 必须是正整数")


@dataclass(frozen=True)
class RejectedContextCandidate:
    """未注入 prompt 的候选及其可解释原因；正文默认不落盘。"""

    source: ContextSourceRef
    reason: str
    score: float
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "reason": self.reason,
            "score": self.score,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class ContextManifest:
    """一次上下文组装的可持久化清单。"""

    query_fingerprint: str
    status: str
    budget: ContextBudget
    selected: tuple[ContextCandidate, ...] = ()
    rejected: tuple[RejectedContextCandidate, ...] = ()
    unavailable_source_kinds: tuple[str, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        if len(self.query_fingerprint) != 64:
            raise ValueError("query_fingerprint 必须是 SHA256")
        if self.status not in _ASSEMBLY_STATUSES:
            raise ValueError(f"不支持的上下文组装状态: {self.status}")
        unknown = sorted(set(self.unavailable_source_kinds) - _SOURCE_KINDS)
        if unknown:
            raise ValueError(f"不支持的不可用来源类型: {', '.join(unknown)}")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("manifest version 必须是正整数")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "query_fingerprint": self.query_fingerprint,
            "status": self.status,
            "budget": {
                "max_candidates": self.budget.max_candidates,
                "max_chars": self.budget.max_chars,
            },
            "selected": [item.to_dict(include_text=False) for item in self.selected],
            "rejected": [item.to_dict() for item in self.rejected],
            "unavailable_source_kinds": list(self.unavailable_source_kinds),
        }


class ContextSourcePort(Protocol):
    """按查询边界读取一个可替换的上下文来源。"""

    source_kind: str

    def retrieve(self, query: ContextQuery, *, budget: ContextBudget) -> Sequence[ContextCandidate]:
        ...


class LongTermMemoryPort(ContextSourcePort, Protocol):
    """长期记忆额外需要反馈和撤销能力，不能复用 ActionJournal 的短事务。"""

    def record_feedback(
        self,
        *,
        run_id: int,
        source: ContextSourceRef,
        outcome: Mapping[str, Any],
    ) -> None:
        ...

    def revoke(self, *, source_id: str, revision: str, reason: str) -> None:
        ...

    def confirm_pending(self, *, source_id: str, confirmed_by_user_id: int) -> Any:
        ...


class ShortTermStatePort(Protocol):
    """短期状态端口；绑定运行/节点后的 ActionJournal 可以直接实现它。"""

    def load_state(self) -> Mapping[str, Any] | None:
        """只读取领域状态；完整动作审计不能混入短期上下文。"""
        ...

    def init(
        self,
        *,
        state: Mapping[str, Any],
        input_hash: str,
        budget: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        ...


class ContextAssemblerPort(Protocol):
    """上下文组装器端口，负责把来源候选转成带拒绝原因的 manifest。"""

    def assemble(
        self,
        *,
        query: ContextQuery,
        candidates: Iterable[ContextCandidate],
        budget: ContextBudget,
        unavailable_source_kinds: Iterable[str] = (),
    ) -> ContextManifest:
        ...


def assemble_context(
    *,
    query: ContextQuery,
    candidates: Iterable[ContextCandidate],
    budget: ContextBudget,
    unavailable_source_kinds: Iterable[str] = (),
) -> ContextManifest:
    """按来源状态、作用域和预算确定性组装候选，不访问数据库或外部服务。"""

    unavailable_list = list(
        dict.fromkeys(str(item or "").strip() for item in unavailable_source_kinds if str(item or "").strip())
    )
    unknown = sorted(set(unavailable_list) - _SOURCE_KINDS)
    if unknown:
        raise ValueError(f"不支持的不可用来源类型: {', '.join(unknown)}")
    if query.source_kinds:
        unavailable_list = [item for item in unavailable_list if item in query.source_kinds]

    selected: list[ContextCandidate] = []
    rejected: list[RejectedContextCandidate] = []
    remaining_chars = budget.max_chars
    normalized = list(candidates)
    for candidate in normalized:
        source_kind = candidate.source.source_kind
        if (
            candidate.source.status == "unavailable"
            and (not query.source_kinds or source_kind in query.source_kinds)
            and source_kind not in unavailable_list
        ):
            # 中文注释：适配器即使只返回 unavailable 候选，也不能让长期记忆缺失退化为空结果。
            unavailable_list.append(source_kind)
        elif (
            candidate.source.status in {"revoked", "stale"}
            and (not query.source_kinds or source_kind in query.source_kinds)
            and source_kind not in unavailable_list
        ):
            # 中文注释：撤销或过期是来源不可采用的明确原因，不能降级成普通空结果。
            unavailable_list.append(source_kind)
    normalized.sort(key=lambda item: (-item.score, item.source.source_kind, item.source.source_id))

    for candidate in normalized:
        source = candidate.source
        reason = ""
        if query.source_kinds and source.source_kind not in query.source_kinds:
            reason = "source_kind_not_requested"
        elif source.scope == "project" and source.scope_id != query.project_id:
            reason = "project_scope_mismatch"
        elif source.scope == "user" and source.scope_id != query.user_id:
            reason = "user_scope_mismatch"
        elif source.status != "available":
            reason = source.status
        elif len(selected) >= min(query.max_candidates, budget.max_candidates):
            reason = "candidate_budget_exceeded"
        elif len(candidate.text) + (len(CONTEXT_TEXT_SEPARATOR) if selected else 0) > remaining_chars:
            reason = "character_budget_exceeded"
        if reason:
            rejected.append(
                RejectedContextCandidate(
                    source=source,
                    reason=reason,
                    score=candidate.score,
                    evidence=candidate.evidence,
                )
            )
            continue
        remaining_chars -= len(candidate.text) + (len(CONTEXT_TEXT_SEPARATOR) if selected else 0)
        selected.append(candidate)

    if selected:
        status = "partial" if unavailable_list else "ready"
    elif unavailable_list:
        status = "unavailable"
    else:
        status = "empty"
    return ContextManifest(
        query_fingerprint=query.fingerprint,
        status=status,
        budget=budget,
        selected=tuple(selected),
        rejected=tuple(rejected),
        unavailable_source_kinds=tuple(unavailable_list),
    )


def render_selected_context(manifest: ContextManifest, *, separator: str = CONTEXT_TEXT_SEPARATOR) -> str:
    """把已通过组装的候选转成注入模型的正文，不重新检索或改变选择结果。"""

    if not isinstance(manifest, ContextManifest):
        raise TypeError("manifest 必须是 ContextManifest")
    if not isinstance(separator, str):
        raise TypeError("separator 必须是字符串")
    return separator.join(item.text for item in manifest.selected if item.text)


__all__ = [
    "CONTEXT_TEXT_SEPARATOR",
    "ContextAssemblerPort",
    "ContextBudget",
    "ContextCandidate",
    "ContextManifest",
    "ContextQuery",
    "ContextSourcePort",
    "ContextSourceRef",
    "LongTermMemoryPort",
    "RejectedContextCandidate",
    "ShortTermStatePort",
    "assemble_context",
    "render_selected_context",
]
