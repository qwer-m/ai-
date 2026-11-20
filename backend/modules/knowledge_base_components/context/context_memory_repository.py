"""长期上下文记录的权威存储与 Port 适配器。

这里只读取已确认的真实记录；没有记录时明确返回 unavailable，不能把空结果
伪装成“没有相关知识”。候选写入必须经过显式确认，运行时不会自动晋升记忆。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any, Mapping, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core.db import db_utc_now
from core.db.model_defs import AgentContextRecord, AgentRun, Project
from modules.knowledge_base_components.ports.context_memory_port import (
    ContextBudget,
    ContextCandidate,
    ContextQuery,
    ContextSourcePort,
    ContextSourceRef,
)


ACTIVE_STATUSES = frozenset({"active"})
MEMORY_SOURCE_KINDS = frozenset({
    "user_preference",
    "long_term_memory",
    "validated_feedback",
})

def _query_terms(text: str) -> tuple[str, ...]:
    raw_terms = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", str(text or "").casefold())
    terms: list[str] = []
    for token in raw_terms:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            terms.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.append(token)
    return tuple(dict.fromkeys(term for term in terms if term.strip()))


def _record_text(record: AgentContextRecord) -> str:
    content = record.content if isinstance(record.content, dict) else {"value": record.content}
    text = str(content.get("text") or content.get("content") or "").strip()
    return text or json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(content: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(content), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _owned_scope_id(*, user_id: int, project_id: int | None, scope: str,
                    scope_id: str | None) -> str:
    """作用域身份由可信用户/项目推导，提交值仅用于核对，不能扩大可访问范围。"""

    if scope == "global":
        # 当前用户模型没有平台发布权限；不能把普通用户确认等同公共知识发布。
        raise ValueError("当前接口不允许发布全局上下文，请使用用户或项目作用域")
    if scope not in {"user", "project"}:
        raise ValueError(f"不支持的长期上下文作用域: {scope}")
    if type(user_id) is not int or user_id < 1:
        raise ValueError("长期上下文必须绑定有效用户")
    if scope == "project" and (type(project_id) is not int or project_id < 1):
        raise ValueError("project 作用域记录必须绑定 project_id")
    expected = str(project_id if scope == "project" else user_id)
    if scope_id is not None and str(scope_id).strip() != expected:
        raise ValueError(f"{scope} 作用域 scope_id 必须等于当前{'项目' if scope == 'project' else '用户'}编号")
    return expected


class ContextMemoryRepository:
    """长期上下文记录的确定性读写边界。"""

    def __init__(self, db: Session):
        self.db = db

    def _locked_record(self, *, source_id: str, revision: str | None = None) -> AgentContextRecord:
        """决策前锁定并重读，避免路由预载的旧对象覆盖另一请求已提交的状态。"""

        prefix = str(source_id).removeprefix("memory:")
        if not prefix.isdigit():
            raise ValueError("长期上下文 source_id 必须是 memory:<id>")
        record = self.db.query(AgentContextRecord).filter(
            AgentContextRecord.id == int(prefix),
        ).populate_existing().with_for_update().first()
        if record is None or (revision is not None and str(record.revision) != str(revision)):
            raise LookupError("长期上下文记录不存在或版本不匹配")
        return record

    def list_active(
        self,
        *,
        query: ContextQuery,
        source_kinds: Sequence[str],
    ) -> list[AgentContextRecord]:
        kinds = tuple(dict.fromkeys(str(item).strip() for item in source_kinds if str(item).strip()))
        if not kinds:
            return []
        # 平台公共记录必须没有普通用户/项目所有者；旧的用户自建 global 记录隔离不采用。
        scope_filters = [
            (AgentContextRecord.scope == "global") & (AgentContextRecord.scope_id == "global")
            & AgentContextRecord.user_id.is_(None) & AgentContextRecord.project_id.is_(None)
            & AgentContextRecord.confirmed_by_user_id.is_(None),
        ]
        if query.project_id is not None and query.user_id is not None:
            scope_filters.append(
                (AgentContextRecord.scope == "project")
                & (AgentContextRecord.scope_id == str(query.project_id))
                & (AgentContextRecord.project_id == query.project_id)
                & (AgentContextRecord.user_id == query.user_id)
                & AgentContextRecord.project_id.in_(select(Project.id).where(Project.user_id == query.user_id))
            )
        if query.user_id is not None:
            scope_filters.append(
                (AgentContextRecord.scope == "user")
                & (AgentContextRecord.scope_id == str(query.user_id))
                & (AgentContextRecord.user_id == query.user_id)
            )
        now = db_utc_now()
        feedback_scope_filter = (
            (AgentContextRecord.source_kind != "validated_feedback")
            | (
                (AgentContextRecord.scope == "project")
                & (AgentContextRecord.scope_id == str(query.project_id))
                & (AgentContextRecord.project_id == query.project_id)
            )
        )
        return (
            self.db.query(AgentContextRecord)
            .filter(
                AgentContextRecord.source_kind.in_(kinds),
                AgentContextRecord.status.in_(ACTIVE_STATUSES),
                AgentContextRecord.revoked_at.is_(None),
                or_(AgentContextRecord.valid_from.is_(None), AgentContextRecord.valid_from <= now),
                or_(AgentContextRecord.valid_to.is_(None), AgentContextRecord.valid_to > now),
                or_(*scope_filters),
                feedback_scope_filter,
            )
            .order_by(AgentContextRecord.updated_at.desc(), AgentContextRecord.id.desc())
            .all()
        )

    def list_records(
        self,
        *,
        user_id: int,
        project_id: int | None = None,
        source_kind: str | None = None,
        status: str | None = None,
        origin_run_id: int | None = None,
    ) -> list[AgentContextRecord]:
        """读取当前用户可管理的记录；管理查询不把其他用户的全局记忆暴露出来。"""

        query = self.db.query(AgentContextRecord).filter(
            AgentContextRecord.user_id == int(user_id),
        )
        if project_id is not None:
            query = query.filter(AgentContextRecord.project_id == int(project_id))
        if origin_run_id is not None:
            query = query.filter(AgentContextRecord.origin_run_id == int(origin_run_id))
        if source_kind is not None:
            normalized_kind = str(source_kind).strip()
            if normalized_kind not in MEMORY_SOURCE_KINDS:
                raise ValueError(f"不支持的长期上下文来源类型: {normalized_kind}")
            query = query.filter(AgentContextRecord.source_kind == normalized_kind)
        if status is not None:
            normalized_status = str(status).strip()
            if normalized_status not in {"pending", "active", "revoked"}:
                raise ValueError(f"不支持的长期上下文状态: {normalized_status}")
            query = query.filter(AgentContextRecord.status == normalized_status)
        return query.order_by(AgentContextRecord.updated_at.desc(), AgentContextRecord.id.desc()).all()

    def retrieve(self, query: ContextQuery, *, budget: ContextBudget) -> list[ContextCandidate]:
        """按身份、作用域和查询文本筛选长期记录，避免全量记忆进入本轮上下文。"""

        kinds = query.source_kinds or (
            "project_knowledge",
            "user_preference",
            "long_term_memory",
            "validated_feedback",
        )
        records = self.list_active(query=query, source_kinds=kinds)
        terms = _query_terms(query.text)
        result: list[ContextCandidate] = []
        matched_kinds: set[str] = set()
        for record in records:
            text = _record_text(record)
            if not text:
                continue
            content = record.content if isinstance(record.content, dict) else {}
            searchable = text
            for key in ("keywords", "tags", "scope", "topic", "applicability", "invalidation_condition"):
                value = content.get(key)
                if isinstance(value, list):
                    searchable += " " + " ".join(str(item) for item in value)
                elif value:
                    searchable += " " + str(value)
            matched_terms = [term for term in terms if term in searchable.casefold()]
            if terms and not matched_terms:
                continue
            source_kind = str(record.source_kind)
            matched_kinds.add(source_kind)
            if content.get("kind") == "test_case_evaluation_difference":
                from modules.agent_platform.test_case_evaluation_feedback import validate_evaluation_feedback_record

                try:
                    validate_evaluation_feedback_record(self.db, record)
                except (ValueError, LookupError) as error:
                    result.append(ContextCandidate(
                        source=ContextSourceRef(
                            source_kind=source_kind, source_id=f"memory:{record.id}",
                            scope="project", scope_id=record.project_id,
                            revision=str(record.revision), content_hash=str(record.content_hash), status="stale",
                        ),
                        evidence={"record_id": int(record.id), "reason": str(error)},
                    ))
                    continue
                # 检索命中仅使用实际正文和适用条件，展示提示不参与匹配。
                text += (
                    f"\n适用范围：{content.get('applicability') or ''}"
                    f"\n失效条件：{content.get('invalidation_condition') or ''}"
                    "\n用途：已确认的历史评测反馈，供本项目核对风险与执行方法，不能自动增加产品规则。"
                )
            record_scope = str(record.scope)
            raw_scope_id = record.scope_id
            scope_id: int | str | None = None if record_scope == "global" else raw_scope_id
            if record_scope in {"project", "user"} and str(raw_scope_id or "").isdigit():
                scope_id = int(raw_scope_id)
            result.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        source_kind=source_kind,
                        source_id=f"memory:{record.id}",
                        scope=record_scope,
                        scope_id=scope_id,
                        revision=str(record.revision),
                        content_hash=str(record.content_hash),
                        status="available",
                    ),
                    text=text,
                    score=1.0 if not terms else min(1.0, len(matched_terms) / len(terms)),
                    evidence={
                        "record_id": int(record.id),
                        "evidence_refs": list(record.evidence_refs or []),
                        "confirmed_at": record.confirmed_at.isoformat() if record.confirmed_at else None,
                        "matched_terms": matched_terms,
                    },
                )
            )
        result.sort(key=lambda item: (-item.score, item.source.source_kind, item.source.source_id))
        result = result[: min(query.max_candidates, budget.max_candidates)]
        for source_kind in kinds:
            if source_kind in matched_kinds:
                continue
            result.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        source_kind=source_kind,
                        source_id="context-memory-unavailable",
                        scope="global",
                        status="unavailable",
                    ),
                    evidence={
                        "reason": "no_relevant_records" if records else "no_confirmed_records",
                        "query_terms": list(terms),
                    },
                )
            )
        return result

    def create_pending(
        self,
        *,
        user_id: int,
        project_id: int | None,
        source_kind: str,
        scope: str,
        scope_id: str | None,
        revision: str,
        content: Mapping[str, Any],
        evidence_refs: Sequence[Mapping[str, Any]],
        origin_run_id: int | None = None,
    ) -> AgentContextRecord:
        """写入待确认候选；候选不会参与检索，必须经过显式确认才能生效。"""

        if not content:
            raise ValueError("长期上下文记录内容不能为空")
        if not evidence_refs:
            raise ValueError("长期上下文记录必须包含证据引用")
        if source_kind not in MEMORY_SOURCE_KINDS:
            raise ValueError(f"不支持的长期上下文来源类型: {source_kind}")
        if scope not in {"global", "project", "user"}:
            raise ValueError(f"不支持的长期上下文作用域: {scope}")
        normalized_scope_id = _owned_scope_id(
            user_id=user_id, project_id=project_id, scope=scope, scope_id=scope_id,
        )
        if project_id is not None and self.db.query(Project.id).filter(
                Project.id == project_id, Project.user_id == user_id).first() is None:
            raise ValueError("长期上下文不能绑定未授权的项目")
        if source_kind == "validated_feedback":
            if project_id is None or scope != "project":
                raise ValueError("validated_feedback 只能按项目作用域保存")
            if normalized_scope_id != str(project_id):
                raise ValueError("validated_feedback 的 scope_id 必须等于 project_id")
        ContextSourceRef(
            source_kind=source_kind,
            source_id="memory:pending",
            scope=scope,
            scope_id=normalized_scope_id,
            revision=revision,
            content_hash=_content_hash(content),
            status="available",
        )
        if scope == "project" and project_id is None:
            raise ValueError("project 作用域记录必须绑定 project_id")
        if origin_run_id is not None:
            origin_run = self.db.get(AgentRun, int(origin_run_id))
            if origin_run is None:
                raise LookupError("长期上下文关联的来源运行不存在")
            if origin_run.user_id != int(user_id) or origin_run.project_id != project_id:
                raise ValueError("长期上下文不能跨用户或项目关联来源运行")
        record = AgentContextRecord(
            user_id=user_id,
            project_id=project_id,
            source_kind=source_kind,
            scope=scope,
            scope_id=normalized_scope_id or "global",
            revision=str(revision),
            content_hash=_content_hash(content),
            content=dict(content),
            evidence_refs=[dict(item) for item in evidence_refs],
            status="pending",
            origin_run_id=origin_run_id,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def create_confirmed(
        self,
        *,
        user_id: int,
        project_id: int | None,
        source_kind: str,
        scope: str,
        scope_id: str | None,
        revision: str,
        content: Mapping[str, Any],
        evidence_refs: Sequence[Mapping[str, Any]],
        origin_run_id: int | None = None,
    ) -> AgentContextRecord:
        """仅供已经完成确认的确定性服务使用，内部仍经过 pending 状态。"""

        record = self.create_pending(
            user_id=user_id,
            project_id=project_id,
            source_kind=source_kind,
            scope=scope,
            scope_id=scope_id,
            revision=revision,
            content=content,
            evidence_refs=evidence_refs,
            origin_run_id=origin_run_id,
        )
        return self.confirm_pending(source_id=f"memory:{record.id}", confirmed_by_user_id=user_id)

    def revoke(self, *, source_id: str, revision: str, reason: str) -> None:
        record = self._locked_record(source_id=source_id, revision=revision)
        if record.status != "active":
            raise ValueError("只有 active 长期上下文记录可以撤销")
        if not str(reason or "").strip():
            raise ValueError("撤销长期上下文必须说明原因")
        record.status = "revoked"
        record.revoked_at = db_utc_now()
        record.revocation_reason = str(reason).strip()
        self.db.add(record)
        self.db.flush()

    def replace_active(
        self,
        *,
        source_id: str,
        revision: str,
        user_id: int,
        content: Mapping[str, Any],
        evidence_refs: Sequence[Mapping[str, Any]],
        new_revision: str,
        origin_run_id: int | None = None,
        reason: str = "",
    ) -> AgentContextRecord:
        """用新版本替代旧事实，并保留旧记录的撤销与替代关系。"""

        old = self._locked_record(source_id=source_id, revision=revision)
        if old.status != "active":
            raise ValueError("只有 active 长期上下文记录可以被替代")
        if (old.content or {}).get("kind") == "test_case_evaluation_difference":
            raise ValueError("评测差异需要通过新评测形成新候选，不能改写已确认差异的来源依据")
        if old.user_id not in (None, int(user_id)):
            raise PermissionError("不能替代其他用户的长期上下文记录")
        if not str(reason or "").strip():
            raise ValueError("替代长期上下文必须说明原因")
        replacement = self.create_confirmed(
            user_id=int(user_id),
            project_id=old.project_id,
            source_kind=str(old.source_kind),
            scope=str(old.scope),
            scope_id=str(old.scope_id) if old.scope != "global" else None,
            revision=new_revision,
            content=content,
            evidence_refs=evidence_refs,
            origin_run_id=origin_run_id,
        )
        old.status = "revoked"
        old.revoked_at = db_utc_now()
        old.revocation_reason = f"{str(reason).strip()}; replaced_by:memory:{replacement.id}"
        old.replaced_by_id = replacement.id
        self.db.add(old)
        self.db.flush()
        return replacement

    def confirm_pending(
        self, *, source_id: str, confirmed_by_user_id: int,
        revision: str | None = None, text: str | None = None,
        applicability: str | None = None, invalidation_condition: str | None = None,
    ) -> AgentContextRecord:
        """把人工确认的 pending 反馈提升为 active；不会自动确认运行反馈。"""

        record = self._locked_record(source_id=source_id, revision=revision)
        expected_scope_id = _owned_scope_id(
            user_id=confirmed_by_user_id, project_id=record.project_id,
            scope=record.scope, scope_id=record.scope_id,
        )
        if record.user_id != confirmed_by_user_id or record.scope_id != expected_scope_id:
            raise PermissionError("不能确认其他用户或作用域不一致的长期上下文")
        if record.project_id is not None and self.db.query(Project.id).filter(
                Project.id == record.project_id, Project.user_id == confirmed_by_user_id).first() is None:
            raise PermissionError("不能确认未授权项目的长期上下文")
        if record.status != "pending":
            raise LookupError("待确认长期上下文记录不存在")
        if not record.evidence_refs:
            raise ValueError("待确认长期上下文记录缺少证据引用")
        content = dict(record.content or {})
        if content.get("kind") == "test_case_evaluation_difference":
            from modules.agent_platform.test_case_evaluation_feedback import validate_evaluation_feedback_record

            if revision is None or not all(str(value or "").strip() for value in (text, applicability, invalidation_condition)):
                raise ValueError("确认评测差异必须提供当前版本、可复用结论、适用范围和失效条件")
            validate_evaluation_feedback_record(self.db, record)
        if text is not None:
            content["text"] = str(text).strip()
        if applicability is not None:
            content["applicability"] = str(applicability).strip()
        if invalidation_condition is not None:
            content["invalidation_condition"] = str(invalidation_condition).strip()
        record.content = content
        record.content_hash = _content_hash(content)
        record.status = "active"
        record.confirmed_by_user_id = int(confirmed_by_user_id)
        record.confirmed_at = db_utc_now()
        record.valid_from = record.valid_from or record.confirmed_at
        self.db.add(record)
        self.db.flush()
        return record

    def reject_pending(self, *, source_id: str, revision: str, reason: str) -> AgentContextRecord:
        """拒绝待确认差异，保留原候选、版本和人工说明，不进入有效记忆。"""

        record = self._locked_record(source_id=source_id, revision=revision)
        if record.status != "pending":
            raise ValueError("只有 pending 长期上下文记录可以拒绝")
        if not str(reason or "").strip():
            raise ValueError("拒绝长期上下文候选必须说明原因")
        record.status = "revoked"
        record.revoked_at = db_utc_now()
        record.revocation_reason = str(reason).strip()
        self.db.add(record)
        self.db.flush()
        return record

    def record_feedback(
        self,
        *,
        run_id: int,
        source: ContextSourceRef,
        outcome: Mapping[str, Any],
    ) -> AgentContextRecord:
        """只记录待确认反馈候选，不自动改变 active 知识。"""

        run = self.db.get(AgentRun, int(run_id))
        if run is None:
            raise LookupError("反馈关联的运行不存在")
        if source.source_kind not in {
            "project_knowledge",
            "user_preference",
            "long_term_memory",
            "validated_feedback",
            "short_term_state",
        }:
            raise ValueError(f"不支持记录反馈的来源类型: {source.source_kind}")
        feedback = dict(outcome)
        feedback.setdefault("source", source.to_dict())
        feedback.setdefault("text", str(feedback.get("notes") or "").strip())
        # 反馈可以跨任务复用，但只能在产生它的项目内复用；原始来源作用域
        # 仍保留在 content/evidence_refs 中，便于追溯，不决定反馈的检索范围。
        record = AgentContextRecord(
            user_id=int(run.user_id),
            project_id=int(run.project_id),
            source_kind="validated_feedback",
            scope="project",
            scope_id=str(run.project_id),
            revision=source.revision or f"run:{run_id}",
            content_hash=_content_hash(feedback),
            content=feedback,
            evidence_refs=[{"source": source.to_dict(), "run_id": int(run_id)}],
            status="pending",
            origin_run_id=run_id,
        )
        self.db.add(record)
        self.db.flush()
        return record


class ContextMemorySourcePort(ContextSourcePort):
    """把同一张长期记录表按来源类型拆成可按需选择的 Port。"""

    def __init__(self, repository: ContextMemoryRepository, source_kind: str):
        self.repository = repository
        self.source_kind = str(source_kind).strip()
        if self.source_kind not in {
            "user_preference",
            "long_term_memory",
            "validated_feedback",
        }:
            raise ValueError(f"不支持的长期上下文来源类型: {self.source_kind}")

    def retrieve(self, query: ContextQuery, *, budget: ContextBudget) -> list[ContextCandidate]:
        scoped_query = replace(query, source_kinds=(self.source_kind,))
        return self.repository.retrieve(scoped_query, budget=budget)


__all__ = ["ContextMemoryRepository", "ContextMemorySourcePort"]
