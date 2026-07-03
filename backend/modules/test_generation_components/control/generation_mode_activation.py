from __future__ import annotations

import hashlib
import re
import csv
import json
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


_FULL_REGRESSION_TOKENS = (
    "全功能",
    "完整功能",
    "完整覆盖",
    "全量回归",
    "全回归",
    "功能测试",
    "系统测试",
    "全链路",
    "full regression",
    "full functional",
    "full coverage",
    "system test",
)
_STANDARD_REGRESSION_TOKENS = (
    "回归",
    "改版",
    "重构",
    "调整",
    "兼容",
    "standard regression",
    "regression",
)
_EXPANDED_REGRESSION_TOKENS = (
    "中高密度",
    "主要功能全覆盖",
    "完整覆盖",
    "覆盖充分",
    "扩展回归",
    "较完整",
    "expanded regression",
    "broad coverage",
    "major feature coverage",
)

_FULL_REGRESSION_SCENARIOS = (
    "入口与导航：覆盖入口展示、跳转、返回、空状态和旧入口残留。",
    "核心流程：按主业务流程拆分步骤级用例，不把多功能点合并为一条。",
    "状态矩阵：覆盖未开始、进行中、已完成、已下架、已删除、无数据等状态。",
    "配置与权限：覆盖开关配置、版本兼容、角色/端差异和不可操作状态。",
    "边界异常：覆盖数量不足、时间冲突、无数据、失败提示、不可删除/不可编辑等异常。",
    "跨模块回归：覆盖首页、详情页、管理页、历史记录、报告等关联页面的数据流转。",
)
_STANDARD_REGRESSION_SCENARIOS = (
    "入口、主流程、编辑/删除/保存、异常提示、跨页返回至少各覆盖一类。",
    "状态变化用例必须验证操作前后差异，不只验证静态展示。",
    "保留少量原有模块回归，避免新增功能影响既有链路。",
)
_EXPANDED_REGRESSION_SCENARIOS = (
    "Preserve broad module coverage while pruning near-duplicate cases.",
    "Keep core happy paths, permission boundaries, state transitions, and high-value exceptions.",
    "Prefer requirement-specific business details over generic UI display checks.",
    "Keep enough cross-module cases to validate end-to-end business closure.",
)
_CORE_SMOKE_SCENARIOS = (
    "优先覆盖最高价值主流程、关键异常和一个回归风险点。",
)


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(str(token).lower() in lowered for token in tokens)


def infer_generation_coverage_profile(
    *,
    requirement_text: str = "",
    expected_count: int = 0,
    linked_final_case_count: int = 0,
) -> dict[str, Any]:
    """Infer coverage profile without hard-coding product-specific modules."""
    text = re.sub(r"\s+", " ", str(requirement_text or "").strip())
    expected = max(0, int(expected_count or 0))
    linked_count = max(0, int(linked_final_case_count or 0))

    if expected >= 80 or linked_count >= 80 or _contains_any(text, _FULL_REGRESSION_TOKENS):
        mode = "full_functional_regression"
        target_range = {"min": 80, "max": 120}
        scenarios = list(_FULL_REGRESSION_SCENARIOS)
        density = "high"
    elif (
        60 <= expected < 80
        or 60 <= linked_count < 80
        or _contains_any(text, _EXPANDED_REGRESSION_TOKENS)
    ):
        mode = "expanded_regression"
        target_range = {"min": 60, "max": 80}
        scenarios = list(_EXPANDED_REGRESSION_SCENARIOS)
        density = "medium_high"
    elif expected >= 30 or linked_count >= 30 or _contains_any(text, _STANDARD_REGRESSION_TOKENS):
        mode = "standard_regression"
        target_range = {"min": 30, "max": 50}
        scenarios = list(_STANDARD_REGRESSION_SCENARIOS)
        density = "medium"
    else:
        mode = "core_smoke"
        target_range = {"min": 10, "max": 20}
        scenarios = list(_CORE_SMOKE_SCENARIOS)
        density = "low"

    return {
        "coverage_mode": mode,
        "case_density": density,
        "target_case_range": target_range,
        "activation_evidence": {
            "expected_count": expected,
            "linked_final_case_count": linked_count,
            "full_regression_token_hit": bool(_contains_any(text, _FULL_REGRESSION_TOKENS)),
            "expanded_regression_token_hit": bool(_contains_any(text, _EXPANDED_REGRESSION_TOKENS)),
            "standard_regression_token_hit": bool(_contains_any(text, _STANDARD_REGRESSION_TOKENS)),
        },
        "coverage_layers": scenarios,
    }


def build_generation_mode_control_state(
    *,
    requirement_text: str = "",
    expected_count: int = 0,
    linked_final_case_count: int = 0,
) -> FeedbackControlState:
    profile = infer_generation_coverage_profile(
        requirement_text=requirement_text,
        expected_count=expected_count,
        linked_final_case_count=linked_final_case_count,
    )
    mode = str(profile.get("coverage_mode") or "core_smoke")
    layers = [str(item) for item in (profile.get("coverage_layers") or []) if str(item).strip()]

    quality_hints = [
        "每条用例必须有明确验证目标和具体可断言 expected_result。",
        "不要为了达到数量而补低价值静态展示、纯样式或重复列表用例。",
    ]
    soft_constraints: list[str] = []
    if mode == "full_functional_regression":
        quality_hints.append("全功能回归模式下按模块/状态/异常/跨模块分层生成，允许数量显著高于核心冒烟集。")
        soft_constraints.append("不要把全功能回归压缩成只覆盖核心流程的 10-20 条核心集。")
        quality_hints.append("Full regression must keep high-value negative and exception coverage: validation failure, submit failure, retry, load failure, permission denial, audit/reject rules, notification/state sync, and configurable thresholds when supported by requirements.")
        quality_hints.append("For modules with community, comment/reply, publish, or review concepts, keep at least one moderation/audit-state case and one front-end state-sync case when the requirement mentions those concepts.")
        quality_hints.append("For modules with generated or processed results, keep both successful result display and failure/retry paths; do not replace them with generic UI display checks.")
        quality_hints.append("For file/media or generated-result flows, include supported upload permission, format/size, upload failure, processing interruption, processing failure, retry, and result recovery cases before adding more display-only cases.")
        quality_hints.append("For moderation/audit flows, split materially different rejection reasons such as topic mismatch, privacy/safety issue, plagiarism/duplicate content, and large source-content deviation when those reasons are present in the requirement.")
        quality_hints.append("For downloadable or playable resources, keep load failure, reload/retry, storage permission, and download failure cases when the requirement includes those resource operations.")
    elif mode == "expanded_regression":
        quality_hints.append("Expanded regression mode should keep broad requirement coverage, remove near-duplicates, and avoid collapsing a 60-70 case draft into a compact smoke set.")
        quality_hints.append("For 60-80 case output, keep a small P0 minority for supported blocking main-path, submit/publish, permission, payment, or state-closing risks; keep ordinary coverage at P1/P2.")
        quality_hints.append("Current requirement semantics override legacy regression assumptions; drop cases that assert an older opposite rule unless the current requirement explicitly keeps it.")
    elif mode == "standard_regression":
        quality_hints.append("标准回归模式下优先覆盖主流程、状态变化、异常边界和既有功能回归。")
    else:
        quality_hints.append("核心冒烟模式下保留最小高价值集合，避免展开全量排列组合。")

    return FeedbackControlState(
        must_have_scenarios=layers,
        soft_constraints=soft_constraints,
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
        )
    )
