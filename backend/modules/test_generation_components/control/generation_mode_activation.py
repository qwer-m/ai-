from __future__ import annotations

import hashlib
import csv
import json
import math
from io import StringIO
from typing import Any

from .feedback_control_state import FeedbackControlState


def _parse_case_count(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, list):
        return int(sum(1 for item in raw if isinstance(item, dict)))
    if isinstance(raw, dict):
        for key in ("cases", "test_cases", "items", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return int(sum(1 for item in value if isinstance(item, dict)))
        return 1
    text = str(raw or "").strip()
    if not text:
        return 0
    try:
        parsed = json.loads(text)
        count = _parse_case_count(parsed)
        if count > 0:
            return count
    except Exception:
        pass
    try:
        rows = list(csv.DictReader(StringIO(text)))
        return int(sum(1 for row in rows if any(str(value or "").strip() for value in row.values())))
    except Exception:
        return 0


def resolve_linked_final_case_signal(
    *,
    db: Any,
    project_id: int | None,
    user_id: int | None,
    requirement_text: str = "",
) -> dict[str, Any]:
    """Find final test cases linked to the current requirement document."""
    if db is None or project_id is None or user_id is None:
        return {"linked_final_case_count": 0, "linked_final_case_doc_ids": [], "source_doc_ids": []}
    text = str(requirement_text or "").strip()
    if not text:
        return {"linked_final_case_count": 0, "linked_final_case_doc_ids": [], "source_doc_ids": []}
    try:
        from core.db.models import KnowledgeDocument
        from modules.knowledge_base_components.repositories.knowledge_document_repository import (
            KnowledgeDocumentRepository,
        )

        requirement_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        query = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.project_id == int(project_id),
            KnowledgeDocument.user_id == int(user_id),
            KnowledgeDocument.doc_type.in_(["requirement", "incomplete"]),
        )
        source_docs = query.filter(KnowledgeDocument.content_hash == requirement_hash).order_by(
            KnowledgeDocument.created_at.desc(),
            KnowledgeDocument.id.desc(),
        ).limit(5).all()
        if not source_docs:
            source_docs = query.filter(KnowledgeDocument.content == text).order_by(
                KnowledgeDocument.created_at.desc(),
                KnowledgeDocument.id.desc(),
            ).limit(5).all()
        if not source_docs and len(text) > 4000:
            source_docs = query.filter(KnowledgeDocument.content.like(f"{text[:4000]}%")).order_by(
                KnowledgeDocument.created_at.desc(),
                KnowledgeDocument.id.desc(),
            ).limit(5).all()
        source_ids = [int(doc.id) for doc in source_docs if getattr(doc, "id", None) is not None]
        linked_docs = KnowledgeDocumentRepository(db).list_linked_test_cases_for_sources(
            project_id=int(project_id),
            source_doc_ids=source_ids,
        )
        linked_count = int(sum(_parse_case_count(getattr(doc, "content", "")) for doc in linked_docs))
        return {
            "linked_final_case_count": linked_count,
            "linked_final_case_doc_ids": [int(doc.id) for doc in linked_docs if getattr(doc, "id", None) is not None],
            "source_doc_ids": source_ids,
        }
    except Exception:
        return {"linked_final_case_count": 0, "linked_final_case_doc_ids": [], "source_doc_ids": []}


def _normalize_target_case_range(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    try:
        minimum = max(0, int(raw.get("min") or 0))
        maximum = max(0, int(raw.get("max") or 0))
    except (TypeError, ValueError):
        return {}
    if minimum <= 0 or maximum < minimum:
        return {}
    return {"min": minimum, "max": maximum}


def _functional_architecture_from_state(state: FeedbackControlState) -> dict[str, Any]:
    project_profile = dict((state.source_meta or {}).get("project_profile") or {})
    architecture = project_profile.get("functional_architecture")
    return dict(architecture) if isinstance(architecture, dict) else {}


def _explicit_count_target_range(expected_count: int) -> dict[str, int]:
    """显式数量允许小幅浮动，避免把参考数量变成严格凑数门槛。"""
    expected = max(0, int(expected_count or 0))
    if expected <= 0:
        return {}
    return {"min": max(1, int(math.ceil(expected * 0.8))), "max": expected}


def infer_generation_coverage_profile(
    *,
    requirement_text: str = "",
    expected_count: int = 0,
    linked_final_case_count: int = 0,
    strategy_plan: dict[str, Any] | None = None,
    control_state: Any = None,
    functional_architecture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从显式数量和已有结构化控制数据组装覆盖画像，不解析业务词。"""
    expected = max(0, int(expected_count or 0))
    linked_count = max(0, int(linked_final_case_count or 0))
    state = FeedbackControlState.from_any(control_state)
    plan = dict(strategy_plan or {})
    coverage_targets = (
        dict(plan.get("coverage_targets"))
        if isinstance(plan.get("coverage_targets"), dict)
        else {}
    )
    architecture = dict(functional_architecture or {}) or _functional_architecture_from_state(state)
    functional_modules = [
        item for item in (architecture.get("functional_modules") or []) if isinstance(item, dict)
    ]
    module_interactions = [
        item for item in (architecture.get("module_interactions") or []) if isinstance(item, dict)
    ]
    target_range = (
        _explicit_count_target_range(expected)
        if expected > 0
        else _normalize_target_case_range(coverage_targets.get("target_case_range"))
    )
    layers = [
        str(item).strip()
        for item in (coverage_targets.get("layers") or [])
        if str(item).strip()
    ]
    coverage_mode = str(coverage_targets.get("mode") or "").strip()
    case_density = str(coverage_targets.get("case_density") or "").strip()
    control_signal_count = int(
        len(state.must_cover_rules)
        + len(state.must_have_scenarios)
        + len(state.rule_quota)
        + len(state.workflow_blueprints)
    )
    if expected > 0:
        source = "explicit_expected_count"
    elif coverage_targets:
        source = "strategy_plan"
    elif control_signal_count > 0:
        source = "control_state"
    elif functional_modules or module_interactions:
        source = "functional_architecture"
    else:
        source = "requirement_evidence"

    return {
        "coverage_mode": coverage_mode,
        "case_density": case_density,
        "target_case_range": target_range,
        "coverage_source": source,
        "activation_evidence": {
            "expected_count": expected,
            "expected_count_explicit": bool(expected > 0),
            "linked_final_case_count": linked_count,
            "strategy_coverage_target_present": bool(coverage_targets),
            "control_signal_count": control_signal_count,
            "functional_module_count": int(len(functional_modules)),
            "module_interaction_count": int(len(module_interactions)),
            "requirement_text_parsed_for_mode": False,
        },
        "coverage_layers": layers,
    }


def build_generation_mode_control_state(
    *,
    requirement_text: str = "",
    expected_count: int = 0,
    linked_final_case_count: int = 0,
    strategy_plan: dict[str, Any] | None = None,
    control_state: Any = None,
    functional_architecture: dict[str, Any] | None = None,
) -> FeedbackControlState:
    profile = infer_generation_coverage_profile(
        requirement_text=requirement_text,
        expected_count=expected_count,
        linked_final_case_count=linked_final_case_count,
        strategy_plan=strategy_plan,
        control_state=control_state,
        functional_architecture=functional_architecture,
    )
    layers = [str(item) for item in (profile.get("coverage_layers") or []) if str(item).strip()]

    quality_hints = [
        "每条用例必须有明确验证目标和具体可断言 expected_result。",
        "按现有功能模块、显式规则、模块交互和工作流契约组织覆盖，不从正文关键词猜测固定场景。",
        "边界、异常和非功能风险仅在需求或控制证据支持时生成，不要求所有模块套用同一分类清单。",
        "显式 expected_count 是数量目标；扩充时优先补不同规则或状态闭环，不用重复和静态展示凑数。",
    ]

    return FeedbackControlState(
        must_have_scenarios=layers,
        quality_fix_hints=quality_hints,
        source_meta={
            "sources": ["generation_coverage_profile"],
            "generation_coverage_profile": profile,
        },
    )


def merge_generation_mode_control_state(
    base_state: Any,
    *,
    requirement_text: str = "",
    expected_count: int = 0,
    linked_final_case_count: int = 0,
    strategy_plan: dict[str, Any] | None = None,
    functional_architecture: dict[str, Any] | None = None,
) -> FeedbackControlState:
    """Merge coverage profile into any existing control-state shape."""
    if isinstance(base_state, FeedbackControlState):
        normalized_base = base_state
    elif isinstance(base_state, dict):
        normalized_base = FeedbackControlState.from_dict(base_state)
    elif hasattr(base_state, "to_dict"):
        try:
            normalized_base = FeedbackControlState.from_any(base_state.to_dict())
        except Exception:
            normalized_base = FeedbackControlState.empty()
    else:
        normalized_base = FeedbackControlState.empty()

    return normalized_base.merge(
        build_generation_mode_control_state(
            requirement_text=requirement_text,
            expected_count=expected_count,
            linked_final_case_count=linked_final_case_count,
            strategy_plan=strategy_plan,
            control_state=normalized_base,
            functional_architecture=functional_architecture,
        )
    )
