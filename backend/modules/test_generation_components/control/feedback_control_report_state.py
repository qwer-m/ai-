from __future__ import annotations

import re
from collections import Counter
from typing import Any

from core.db.models import KnowledgeDocument
from modules.memory_fabric.contracts.memory_context import MemoryContext
from modules.memory_fabric.contracts.memory_fabric import MemoryFabric
from modules.memory_fabric.runtime.diagnostics import record_memory_read

from .feedback_control_config import (
    _MAX_AGENT_LEARNING_DOCS,
    _MAX_EVAL_REPORT_DOCS,
    _MAX_FORBIDDEN_PATTERNS,
    _MAX_MUST_COVER_RULES,
    _MAX_PREFERRED_PATTERNS,
    _MAX_QUALITY_HINTS,
    _MAX_SCENARIOS,
)
from .feedback_control_pattern_policy import _extract_reuse_risks as _extract_reuse_risks_impl
from .feedback_control_sample_access import (
    doc_value as _doc_value,
    extract_rule_ids as _extract_rule_ids,
)
from .feedback_control_state import FeedbackControlState


def extract_forbidden_patterns(text: str) -> list[str]:
    patterns: list[str] = []
    normalized = str(text or "").strip()
    if not normalized:
        return patterns
    lines = [line.strip() for line in re.split(r"[\n\r]+", normalized) if line.strip()]
    for line in lines:
        lowered = line.lower()
        if any(token in lowered for token in ("禁止", "避免", "不要", "不得", "严禁", "must not", "forbidden")):
            snippet = re.sub(r"^[\-*•\d\.\s]+", "", line)
            if len(snippet) >= 4:
                patterns.append(snippet[:120])
    return patterns


def extract_quality_hints(text: str) -> list[str]:
    hints: list[str] = []
    normalized = str(text or "").strip()
    if not normalized:
        return hints
    lines = [line.strip() for line in re.split(r"[\n\r]+", normalized) if line.strip()]
    quality_keywords = (
        "预期",
        "断言",
        "步骤",
        "边界",
        "异常",
        "状态",
        "覆盖",
        "去重",
        "可验证",
        "完整",
        "quality",
        "coverage",
        "assert",
        "duplicate",
    )
    for line in lines:
        cleaned = re.sub(r"^[\-*•\d\.\s]+", "", line)
        lowered = cleaned.lower()
        if len(cleaned) < 6:
            continue
        if any(keyword in lowered for keyword in quality_keywords):
            hints.append(cleaned[:140])
    return hints


def extract_scenarios_from_text(text: str) -> list[str]:
    lowered = str(text or "").lower()
    scenarios: list[str] = []
    rules = [
        ("权限/鉴权异常场景", ("权限", "鉴权", "越权", "auth", "permission")),
        ("失败重试与错误处理场景", ("失败", "错误", "重试", "exception", "error", "retry")),
        ("边界值与极端输入场景", ("边界", "最大", "最小", "临界", "boundary", "max", "min")),
        ("状态流转一致性场景", ("状态", "流转", "state", "transition")),
        ("性能与稳定性场景", ("性能", "超时", "并发", "performance", "timeout", "concurrent")),
    ]
    for label, keys in rules:
        if any(key in lowered for key in keys):
            scenarios.append(label)
    return scenarios


def build_from_reports(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    include_agent_learning: bool = True,
    memory_fabric: MemoryFabric | None = None,
    memory_ctx: MemoryContext | None = None,
    memory_diag: dict[str, Any] | None = None,
) -> FeedbackControlState:
    if db is None or not project_id or not user_id:
        return FeedbackControlState.empty()

    eval_docs: list[Any] = []
    agent_docs: list[Any] = []
    memory_read_ok = False
    if memory_fabric is not None and memory_ctx is not None:
        try:
            eval_docs = list(
                memory_fabric.read_semantic(
                    {
                        "kind": "knowledge_documents",
                        "db": db,
                        "project_id": int(project_id),
                        "user_id": int(user_id),
                        "doc_types": ["evaluation_report"],
                        "limit": _MAX_EVAL_REPORT_DOCS,
                    },
                    memory_ctx,
                )
                or []
            )
            if include_agent_learning:
                agent_docs = list(
                    memory_fabric.read_semantic(
                        {
                            "kind": "knowledge_documents",
                            "db": db,
                            "project_id": int(project_id),
                            "user_id": int(user_id),
                            "doc_types": ["agent_learning"],
                            "limit": _MAX_AGENT_LEARNING_DOCS,
                        },
                        memory_ctx,
                    )
                    or []
                )
            memory_read_ok = True
            record_memory_read(memory_diag, "semantic", via_memory_fabric=True)
        except Exception:
            eval_docs = []
            agent_docs = []

    if (not memory_read_ok) and (not eval_docs and not agent_docs):
        if not hasattr(db, "query"):
            return FeedbackControlState.empty()
        eval_docs = (
            db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == int(project_id),
                KnowledgeDocument.user_id == int(user_id),
                KnowledgeDocument.doc_type == "evaluation_report",
            )
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
            .limit(_MAX_EVAL_REPORT_DOCS)
            .all()
        )

        if include_agent_learning:
            agent_docs = (
                db.query(KnowledgeDocument)
                .filter(
                    KnowledgeDocument.project_id == int(project_id),
                    KnowledgeDocument.user_id == int(user_id),
                    KnowledgeDocument.doc_type == "agent_learning",
                )
                .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
                .limit(_MAX_AGENT_LEARNING_DOCS)
                .all()
            )
        record_memory_read(memory_diag, "semantic", via_memory_fabric=False)

    all_docs = [*eval_docs, *agent_docs]
    if not all_docs:
        return FeedbackControlState.empty()

    rule_counter: Counter[str] = Counter()
    forbidden_patterns: list[str] = []
    reuse_risks: list[str] = []
    reuse_risk_seen: set[str] = set()
    quality_hints: list[str] = []
    must_have_scenarios: list[str] = []
    doc_types: Counter[str] = Counter()

    for doc in all_docs:
        text = str(_doc_value(doc, "content", "") or "")
        doc_types[str(_doc_value(doc, "doc_type", "unknown") or "unknown")] += 1
        for rule in _extract_rule_ids(text):
            rule_counter[rule] += 1
        forbidden_patterns.extend(extract_forbidden_patterns(text))
        quality_hints.extend(extract_quality_hints(text))
        must_have_scenarios.extend(extract_scenarios_from_text(text))
        for reuse_risk in _extract_reuse_risks_impl(text):
            normalized_risk = str(reuse_risk or "").strip().lower()
            if not normalized_risk or normalized_risk in reuse_risk_seen:
                continue
            reuse_risk_seen.add(normalized_risk)
            reuse_risks.append(reuse_risk)

    must_cover = [rule for rule, _ in rule_counter.most_common(_MAX_MUST_COVER_RULES)]
    scenario_counter = Counter(must_have_scenarios)

    return FeedbackControlState(
        must_cover_rules=must_cover,
        must_have_scenarios=[name for name, _ in scenario_counter.most_common(_MAX_SCENARIOS)],
        forbidden_patterns=forbidden_patterns[:_MAX_FORBIDDEN_PATTERNS],
        reuse_risks=reuse_risks[:_MAX_PREFERRED_PATTERNS],
        soft_constraints=[],
        rule_quota={rule: 1 for rule in must_cover},
        quality_fix_hints=quality_hints[:_MAX_QUALITY_HINTS],
        source_meta={
            "sources": ["evaluation_report", "agent_learning"] if agent_docs else ["evaluation_report"],
            "doc_count": int(len(all_docs)),
            "doc_types": dict(doc_types),
        },
    )
