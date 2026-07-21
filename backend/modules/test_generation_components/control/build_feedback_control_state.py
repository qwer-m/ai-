from __future__ import annotations

from collections import Counter
from typing import Any

from core.db.models import RagDataset, RagDatasetSample
from modules.memory_fabric.contracts.memory_context import MemoryContext
from modules.memory_fabric.contracts.memory_fabric import MemoryFabric
from modules.memory_fabric.runtime.diagnostics import record_memory_read
from modules.memory_fabric.runtime.factory import get_memory_fabric
from .feedback_control_state import FeedbackControlState
from .feedback_control_config import (
    _ASCII_TOKEN_PATTERN as _ASCII_TOKEN_PATTERN,
    _CJK_CHAR_PATTERN as _CJK_CHAR_PATTERN,
    _MAX_AGENT_LEARNING_DOCS as _MAX_AGENT_LEARNING_DOCS,
    _MAX_DATASET_SAMPLES as _MAX_DATASET_SAMPLES,
    _MAX_EVAL_REPORT_DOCS as _MAX_EVAL_REPORT_DOCS,
    _MAX_FORBIDDEN_PATTERNS as _MAX_FORBIDDEN_PATTERNS,
    _MAX_MUST_COVER_RULES as _MAX_MUST_COVER_RULES,
    _MAX_PREFERRED_PATTERNS as _MAX_PREFERRED_PATTERNS,
    _MAX_PRIORITY_POOL_CLUSTER_CAP as _MAX_PRIORITY_POOL_CLUSTER_CAP,
    _MAX_PRIORITY_POOL_FORBIDDEN_PATTERNS as _MAX_PRIORITY_POOL_FORBIDDEN_PATTERNS,
    _MAX_PRIORITY_POOL_HINTS as _MAX_PRIORITY_POOL_HINTS,
    _MAX_PRIORITY_POOL_RETRIEVAL_TOP_K as _MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
    _MAX_PRIORITY_POOL_SAMPLES as _MAX_PRIORITY_POOL_SAMPLES,
    _MAX_PRIORITY_POOL_SCENARIOS as _MAX_PRIORITY_POOL_SCENARIOS,
    _MAX_PRIORITY_POOL_SOFT_CONSTRAINTS as _MAX_PRIORITY_POOL_SOFT_CONSTRAINTS,
    _MAX_QUALITY_HINTS as _MAX_QUALITY_HINTS,
    _MAX_SCENARIOS as _MAX_SCENARIOS,
    _MAX_SOFT_CONSTRAINTS as _MAX_SOFT_CONSTRAINTS,
    _MAX_WORKFLOW_BLUEPRINTS as _MAX_WORKFLOW_BLUEPRINTS,
    _MIN_PRIORITY_POOL_PATTERN_CONFIDENCE as _MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
    _PRIORITY_POOL_MAX_NEGATIVE_TOP_K as _PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
    _PRIORITY_POOL_MIN_POSITIVE_TOP_K as _PRIORITY_POOL_MIN_POSITIVE_TOP_K,
    _SYNC_PRIORITY_INDEX_ON_READ as _SYNC_PRIORITY_INDEX_ON_READ,
    _env_float as _env_float,
    _env_int as _env_int,
)
from .feedback_control_sample_access import (
    extract_forbidden_pattern_from_sample as _extract_forbidden_pattern_from_sample,
    extract_rule_ids as _extract_rule_ids,
    normalize_comment_hint as _normalize_comment_hint,
    normalize_expected_priority as _normalize_expected_priority,
    normalize_pattern_category as _normalize_pattern_category,
    normalize_pattern_usage as _normalize_pattern_usage,
    normalize_reason_category as _normalize_reason_category,
    normalize_signal_type as _normalize_signal_type,
    safe_float as _safe_float,
    safe_int as _safe_int,
    sample_case_id as _sample_case_id,
    sample_value as _sample_value,
)
from .feedback_control_pattern_policy import (
    _REUSE_RISK_DESCRIPTIONS as _REUSE_RISK_DESCRIPTIONS,
    _REUSE_RISK_PATTERNS as _REUSE_RISK_PATTERNS,
    _UI_FORBIDDEN_GUARDRAILS as _UI_FORBIDDEN_GUARDRAILS,
    _UI_LOW_VALUE_PATTERN_TOKENS as _UI_LOW_VALUE_PATTERN_TOKENS,
    _build_negative_forbidden_patterns as _build_negative_forbidden_patterns_impl,
    _is_ui_low_value_pattern as _is_ui_low_value_pattern_impl,
)
from .feedback_control_report_state import (
    build_from_reports as _build_from_reports,
    extract_forbidden_patterns as _extract_forbidden_patterns,
    extract_quality_hints as _extract_quality_hints,
    extract_scenarios_from_text as _extract_scenarios_from_text,
)
from .feedback_control_workflow_repository import (
    build_from_workflow_blueprint_repository as _build_from_workflow_blueprint_repository_impl,
)
from .feedback_control_priority_retrieval import (
    _apply_signal_quota as _apply_signal_quota_impl,
    _select_priority_pool_samples_by_requirement as _select_priority_pool_samples_by_requirement_impl,
)
from .feedback_control_priority_retrieval_text import (
    _sample_matches_primary_domain as _sample_matches_primary_domain,
    _sample_text_for_retrieval as _sample_text_for_retrieval,
)
from .feedback_control_priority_workflows import (
    _priority_pool_sample_identity as _priority_pool_sample_identity_impl,
    _select_priority_pool_workflow_blueprint_samples as _select_priority_pool_workflow_blueprint_samples_impl,
    _workflow_blueprint_from_sample as _workflow_blueprint_from_sample_impl,
)
from .feedback_control_priority_pool_state import (
    build_from_priority_sample_pool as _build_from_priority_sample_pool_impl,
)
from .feedback_control_priority_signals import (
    _count_signal_split as _count_signal_split,
    _has_explicit_negative_signal as _has_explicit_negative_signal,
    _is_manual_verified_negative_sample as _is_manual_verified_negative_sample,
    _is_manual_verified_sample as _is_manual_verified_sample,
    _is_pattern_active as _is_pattern_active,
    _is_preferred_signal_sample as _is_preferred_signal_sample,
    _pattern_confidence as _pattern_confidence,
)
from .workflow_blueprint_repository import WorkflowBlueprintRepository
from ..coverage.scenario_registry import (
    classify_registered_scenario_family,
)
from modules.testing.priority_sample_pool_store import (
    ensure_priority_pool_pattern_index,
    load_priority_sample_pool,
    retrieve_priority_sample_patterns,
)
from modules.testing.manual_quality_profile import (
    build_manual_quality_profile,
    manual_quality_profile_hints,
)
from modules.testing.sample_case_access import sample_case_text as _sample_case_text



_REASON_TO_SCENARIO = {
    "core_flow": "核心流程稳定性场景",
    "exception_path": "异常路径与错误处理场景",
    "boundary_condition": "边界值与极端输入场景",
    "state_transition": "状态迁移一致性场景",
}

_REASON_HINTS = {
    "core_flow": "核心流程失败场景优先级不低于P1，阻断风险优先P0。",
    "exception_path": "补充失败重试、异常返回与错误恢复路径。",
    "boundary_condition": "补充边界值、空值、最大最小值与非法输入校验。",
    "state_transition": "补充状态迁移前后约束、回退与幂等验证。",
    "redundant_case": "避免仅措辞差异的语义重复用例，优先保留覆盖增益高的用例。",
    "display_issue": "优先级展示值必须与最终判定一致，以finalPriority为准。",
}


def _manual_priority_hint(label: str, expected_priority: str) -> str:
    return (
        f"{label} 期望优先级 {expected_priority}"
        "（人工标注）。"
    )

def _is_ui_low_value_pattern(*parts: Any) -> bool:
    return _is_ui_low_value_pattern_impl(*parts)


def _build_negative_forbidden_patterns(
    *,
    sample: dict[str, Any],
    title: str,
    comment: str,
    reason: str,
) -> tuple[list[str], bool]:
    return _build_negative_forbidden_patterns_impl(
        sample=sample,
        title=title,
        comment=comment,
        reason=reason,
        sample_value_fn=_sample_value,
        extract_forbidden_pattern_from_sample_fn=_extract_forbidden_pattern_from_sample,
        is_ui_low_value_pattern_fn=_is_ui_low_value_pattern,
    )



def _apply_signal_quota(
    candidates: list[dict[str, Any]],
    *,
    retrieval_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    return _apply_signal_quota_impl(
        candidates,
        retrieval_meta=retrieval_meta,
        max_retrieval_top_k=_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
        min_positive_top_k=_PRIORITY_POOL_MIN_POSITIVE_TOP_K,
        max_negative_top_k=_PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
    )


def _select_priority_pool_samples_by_requirement(
    *,
    samples: list[dict[str, Any]],
    project_id: int,
    user_id: int,
    generation_id: int | None = None,
    pattern_index_token: str = "",
    requirement_text: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _select_priority_pool_samples_by_requirement_impl(
        samples=samples,
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id,
        pattern_index_token=pattern_index_token,
        requirement_text=requirement_text,
        retrieve_priority_sample_patterns_fn=retrieve_priority_sample_patterns,
        max_retrieval_top_k=_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
        max_cluster_cap=_MAX_PRIORITY_POOL_CLUSTER_CAP,
        min_positive_top_k=_PRIORITY_POOL_MIN_POSITIVE_TOP_K,
        max_negative_top_k=_PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
        min_pattern_confidence=_MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
    )


def _build_from_workflow_blueprint_repository(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    requirement_text: str = "",
    current_source_doc_ids: list[int] | tuple[int, ...] | None = None,
    current_content_hash: str = "",
) -> FeedbackControlState:
    return _build_from_workflow_blueprint_repository_impl(
        db=db,
        project_id=project_id,
        user_id=user_id,
        requirement_text=requirement_text,
        current_source_doc_ids=current_source_doc_ids,
        current_content_hash=current_content_hash,
        max_workflow_blueprints=_MAX_WORKFLOW_BLUEPRINTS,
        repository_cls=WorkflowBlueprintRepository,
    )



def _workflow_blueprint_from_sample(sample: dict[str, Any]) -> dict[str, Any] | None:
    return _workflow_blueprint_from_sample_impl(
        sample,
        sample_value_fn=_sample_value,
        sample_case_id_fn=_sample_case_id,
    )

def _priority_pool_sample_identity(sample: dict[str, Any]) -> str:
    return _priority_pool_sample_identity_impl(
        sample,
        sample_value_fn=_sample_value,
        sample_case_id_fn=_sample_case_id,
    )

def _select_priority_pool_workflow_blueprint_samples(
    *,
    samples: list[dict[str, Any]],
    requirement_text: str,
) -> list[dict[str, Any]]:
    return _select_priority_pool_workflow_blueprint_samples_impl(
        samples=samples,
        requirement_text=requirement_text,
        workflow_blueprint_from_sample_fn=_workflow_blueprint_from_sample,
        priority_pool_sample_identity_fn=_priority_pool_sample_identity,
        sample_value_fn=_sample_value,
        is_pattern_active_fn=_is_pattern_active,
        pattern_confidence_fn=_pattern_confidence,
        sample_matches_primary_domain_fn=_sample_matches_primary_domain,
        max_workflow_blueprints=_MAX_WORKFLOW_BLUEPRINTS,
        min_pattern_confidence=_MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
    )
def _build_from_priority_sample_pool(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    requirement_text: str = "",
) -> FeedbackControlState:
    return _build_from_priority_sample_pool_impl(
        db=db,
        project_id=project_id,
        user_id=user_id,
        requirement_text=requirement_text,
        load_priority_sample_pool_fn=load_priority_sample_pool,
        retrieve_priority_sample_patterns_fn=retrieve_priority_sample_patterns,
        ensure_priority_pool_pattern_index_fn=ensure_priority_pool_pattern_index,
        build_manual_quality_profile_fn=build_manual_quality_profile,
        manual_quality_profile_hints_fn=manual_quality_profile_hints,
        reason_to_scenario=_REASON_TO_SCENARIO,
        reason_hints=_REASON_HINTS,
        manual_priority_hint_fn=_manual_priority_hint,
        sync_priority_index_on_read=_SYNC_PRIORITY_INDEX_ON_READ,
        max_priority_pool_samples=_MAX_PRIORITY_POOL_SAMPLES,
        max_priority_pool_retrieval_top_k=_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
        max_priority_pool_cluster_cap=_MAX_PRIORITY_POOL_CLUSTER_CAP,
        min_positive_top_k=_PRIORITY_POOL_MIN_POSITIVE_TOP_K,
        max_negative_top_k=_PRIORITY_POOL_MAX_NEGATIVE_TOP_K,
        min_pattern_confidence=_MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
        max_priority_pool_hints=_MAX_PRIORITY_POOL_HINTS,
        max_must_cover_rules=_MAX_MUST_COVER_RULES,
        max_priority_pool_scenarios=_MAX_PRIORITY_POOL_SCENARIOS,
        max_priority_pool_forbidden_patterns=_MAX_PRIORITY_POOL_FORBIDDEN_PATTERNS,
        max_preferred_patterns=_MAX_PREFERRED_PATTERNS,
        max_priority_pool_soft_constraints=_MAX_PRIORITY_POOL_SOFT_CONSTRAINTS,
        max_workflow_blueprints=_MAX_WORKFLOW_BLUEPRINTS,
    )

def _build_from_anomaly_pool(
    *,
    db: Any,
    user_id: int,
    max_samples: int = _MAX_DATASET_SAMPLES,
    memory_fabric: MemoryFabric | None = None,
    memory_ctx: MemoryContext | None = None,
    memory_diag: dict[str, Any] | None = None,
) -> FeedbackControlState:
    if db is None or not user_id:
        return FeedbackControlState.empty()

    dataset_map: dict[int, str] = {}
    sample_rows: list[Any] = []
    memory_read_ok = False
    if memory_fabric is not None and memory_ctx is not None:
        try:
            payload = memory_fabric.read_rule(
                {
                    "kind": "anomaly_pool_samples",
                    "db": db,
                    "user_id": int(user_id),
                    "limit": max(1, int(max_samples)),
                },
                memory_ctx,
            )
            if isinstance(payload, dict):
                dataset_map = {int(k): str(v or "").strip().lower() for k, v in (payload.get("dataset_map") or {}).items()}
                sample_rows = [item for item in (payload.get("samples") or [])]
            memory_read_ok = True
            record_memory_read(memory_diag, "rule", via_memory_fabric=True)
        except Exception:
            dataset_map = {}
            sample_rows = []

    if (not memory_read_ok) and (not dataset_map and not sample_rows):
        if not hasattr(db, "query"):
            return FeedbackControlState.empty()
        dataset_rows = (
            db.query(RagDataset.id, RagDataset.type)
            .filter(
                RagDataset.user_id == int(user_id),
                RagDataset.type.in_(["challenge", "regression"]),
            )
            .all()
        )
        dataset_map = {int(row.id): str(row.type or "").strip().lower() for row in dataset_rows}
        if not dataset_map:
            return FeedbackControlState.empty()
        sample_rows = (
            db.query(RagDatasetSample)
            .filter(
                RagDatasetSample.dataset_id.in_(list(dataset_map.keys())),
                RagDatasetSample.enabled.is_(True),
            )
            .order_by(RagDatasetSample.updated_at.desc(), RagDatasetSample.id.desc())
            .limit(max(1, int(max_samples)))
            .all()
        )
        record_memory_read(memory_diag, "rule", via_memory_fabric=False)

    weighted_rule_counter: Counter[str] = Counter()
    scenario_counter: Counter[str] = Counter()
    source_counts = {"challenge_samples": 0, "regression_samples": 0}

    for sample in sample_rows:
        dataset_type = dataset_map.get(_safe_int(_sample_value(sample, "dataset_id")), "challenge")
        weight = 2 if dataset_type == "regression" else 1
        source_counts[f"{dataset_type}_samples"] = int(source_counts.get(f"{dataset_type}_samples", 0)) + 1

        answer_points = " ".join(str(item) for item in (_sample_value(sample, "answer_points", []) or []))
        tags = " ".join(str(item) for item in (_sample_value(sample, "tags", []) or []))
        merged_text = " ".join(
            [
                str(_sample_value(sample, "query", "") or ""),
                str(_sample_value(sample, "gold_answer", "") or ""),
                answer_points,
                tags,
            ]
        )
        for rule in _extract_rule_ids(merged_text):
            weighted_rule_counter[rule] += int(weight)
        for scenario in _extract_scenarios_from_text(merged_text):
            scenario_counter[scenario] += int(weight)

    must_cover = [rule for rule, _ in weighted_rule_counter.most_common(_MAX_MUST_COVER_RULES)]
    must_have_scenarios = [name for name, _ in scenario_counter.most_common(_MAX_SCENARIOS)]
    rule_quota = {
        rule: (2 if int(score) >= 3 else 1)
        for rule, score in weighted_rule_counter.items()
        if rule in must_cover
    }

    return FeedbackControlState(
        must_cover_rules=must_cover,
        must_have_scenarios=must_have_scenarios,
        forbidden_patterns=[],
        soft_constraints=[],
        rule_quota=rule_quota,
        quality_fix_hints=[],
        source_meta={
            "sources": ["anomaly_pool"],
            "sample_count": int(len(sample_rows)),
            "challenge_sample_count": int(source_counts.get("challenge_samples", 0)),
            "regression_sample_count": int(source_counts.get("regression_samples", 0)),
            "rule_frequency": dict(weighted_rule_counter),
        },
    )


def _compact_state(state: FeedbackControlState) -> FeedbackControlState:
    normalized = FeedbackControlState.from_any(state)
    normalized.must_cover_rules = normalized.must_cover_rules[:_MAX_MUST_COVER_RULES]
    normalized.must_have_scenarios = normalized.must_have_scenarios[:_MAX_SCENARIOS]
    normalized.forbidden_patterns = normalized.forbidden_patterns[:_MAX_FORBIDDEN_PATTERNS]
    normalized.preferred_patterns = normalized.preferred_patterns[:_MAX_PREFERRED_PATTERNS]
    normalized.reuse_risks = normalized.reuse_risks[:_MAX_PREFERRED_PATTERNS]
    normalized.soft_constraints = normalized.soft_constraints[:_MAX_SOFT_CONSTRAINTS]
    normalized.quality_fix_hints = normalized.quality_fix_hints[:_MAX_QUALITY_HINTS]
    normalized.workflow_blueprints = normalized.workflow_blueprints[:_MAX_WORKFLOW_BLUEPRINTS]
    normalized.rule_quota = {
        rule: max(1, int(quota))
        for rule, quota in normalized.rule_quota.items()
        if rule in set(normalized.must_cover_rules)
    }
    return normalized


def build_feedback_control_state(
    *,
    db: Any = None,
    project_id: int | None = None,
    user_id: int | None = None,
    requirement_text: str = "",
    current_source_doc_ids: list[int] | tuple[int, ...] | None = None,
    current_content_hash: str = "",
    enable_priority_sample_pool: bool = True,
    include_agent_learning: bool = True,
    memory_fabric: MemoryFabric | None = None,
    memory_ctx: MemoryContext | None = None,
    memory_diag: dict[str, Any] | None = None,
) -> FeedbackControlState:
    """
    中文注释：聚合异常样本池与评估知识，形成生成前可执行控制状态。
    """
    state = FeedbackControlState.empty()

    resolved_memory_fabric = memory_fabric
    if resolved_memory_fabric is None:
        try:
            resolved_memory_fabric = get_memory_fabric()
        except Exception:
            resolved_memory_fabric = None
    resolved_memory_ctx = memory_ctx or MemoryContext.from_runtime(
        user_id=user_id,
        project_id=project_id,
        run_id="legacy-control-state",
        request_id="legacy-control-state",
    )

    workflow_blueprint_state = _build_from_workflow_blueprint_repository(
        db=db,
        project_id=int(project_id or 0),
        user_id=int(user_id or 0),
        requirement_text=str(requirement_text or ""),
        current_source_doc_ids=current_source_doc_ids or [],
        current_content_hash=str(current_content_hash or ""),
    )
    state = state.merge(workflow_blueprint_state)

    if enable_priority_sample_pool:
        priority_pool_state = _build_from_priority_sample_pool(
            db=db,
            project_id=int(project_id or 0),
            user_id=int(user_id or 0),
            requirement_text=str(requirement_text or ""),
        )
        state = state.merge(priority_pool_state)

    anomaly_state = _build_from_anomaly_pool(
        db=db,
        user_id=int(user_id or 0),
        memory_fabric=resolved_memory_fabric,
        memory_ctx=resolved_memory_ctx,
        memory_diag=memory_diag,
    )
    state = state.merge(anomaly_state)

    report_state = _build_from_reports(
        db=db,
        project_id=int(project_id or 0),
        user_id=int(user_id or 0),
        include_agent_learning=bool(include_agent_learning),
        memory_fabric=resolved_memory_fabric,
        memory_ctx=resolved_memory_ctx,
        memory_diag=memory_diag,
    )
    state = state.merge(report_state)

    return _compact_state(state)
