"""Persistence helpers for test-generation priority anomaly sample pool."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument
from modules.knowledge_base_components.adapters.chroma_vector_store import get_vector_store
from modules.testing.sample_pool_shadow_store import (
    shadow_write_event as _shadow_event,
    shadow_write_patterns as _shadow_patterns,
    shadow_write_samples as _shadow_samples,
)
from modules.testing.manual_quality_profile import build_manual_quality_profile
from modules.testing_components.repositories.evaluation_artifact_repository import (
    EvaluationArtifactRepository,
)

PRIORITY_SAMPLE_POOL_DOC_TYPE = "test_generation_priority_sample_pool"
PRIORITY_SAMPLE_POOL_PATTERN_DOC_TYPE = "priority_sample_pattern"
_PRIORITY_PATTERN_VECTOR_DOC_PREFIX = "priority_sample_pool_patterns"
_MAX_INDEXED_PATTERN_SAMPLES = 5000
_MAX_POOL_SAMPLES = 5000
_MAX_SAMPLES_PER_CLUSTER = 3
_MAX_SAMPLES_PER_SOURCE: dict[str, int] = {
    "priority_debug_manual_add": 1500,
    "quality_evaluation_defect": 500,
    "linked_final_case_pattern": 300,
    "linked_final_case_business_extension": 200,
    "manual_pool_input": 500,
}
_MAX_SAMPLES_PER_SIGNAL_TYPE: dict[str, int] = {
    "positive": 2000,
    "negative": 3000,
}
_MAX_RETRIEVAL_RESULTS = 8
_MAX_RETRIEVAL_CANDIDATES = 24
_MAX_EMBED_QUERY_CHARS = 1900
_MIN_PATTERN_SUMMARY_LEN = 8
_MAX_PATTERN_SUMMARY_LEN = 180
_MAX_PATTERN_CANONICAL_LEN = 160
_MAX_PATTERN_CLUSTER_LEN = 96
_MIN_PATTERN_WEIGHT_ADJUSTMENT = 0.25
_MAX_PATTERN_WEIGHT_ADJUSTMENT = 1.5
_VALID_SIGNAL_TYPES = {"positive", "negative"}
_VALID_PATTERN_USAGE = {"prefer", "avoid"}
_EXECUTION_SCAFFOLD_FIELDS = frozenset(
    {
        "execution_group",
        "execution_sequence",
        "chain_id",
        "depends_on",
        "role",
        "session_key",
        "role_switch_strategy",
        "data_state",
        "isolation_required",
        "fixture_key",
        "fixture_builder",
        "cleanup_policy",
        "group_setup",
        "group_teardown",
        "setup_hint",
        "teardown_hint",
    }
)
_VALID_SAMPLE_SOURCES = frozenset(
    {
        "priority_debug_manual_add",
        "quality_evaluation_defect",
        "linked_final_case_pattern",
        "linked_final_case_business_extension",
        "linked_final_case_workflow_blueprint",
        "manual_pool_input",
    }
)
_SOURCE_LEGACY_MAP: dict[str, str] = {
    "quality_evaluation_defect_analysis": "quality_evaluation_defect",
    "ai_only_quality_failure": "quality_evaluation_defect",
    "linked_final_test_case": "linked_final_case_pattern",
    "defect_analysis": "quality_evaluation_defect",
}
_UI_LOW_VALUE_PATTERN_TOKENS = (
    "ui-only",
    "static ui",
    "static display",
    "copy check",
    "copy-only",
    "style check",
    "layout check",
    "layout-only",
    "visual only",
    "field display",
    "list sorting",
    "placeholder",
    "ui ",
    "display",
    "analytics",
    "tracking",
    "event tracking",
    "buried point",
    "pv",
    "uv",
    "埋点",
    "上报",
    "曝光",
    "点击埋点",
    "展示埋点",
    "文案",
    "样式",
    "布局",
    "展示",
    "列表排序",
    "字段展示",
)

_ASSERTABLE_PATTERN_TOKENS = (
    "assert",
    "assertion",
    "concrete assertion",
    "expected_result_quality",
    "contains_concrete_assertion",
    "0分",
    "0 分",
    "50%",
    "12.5",
    "10/20",
    "不可用",
    "保留",
    "不重复",
    "不丢失",
    "状态为",
    "显示为",
    "跳转至",
)
_CORE_RULE_PATTERN_TOKENS = (
    "core rule",
    "core_flow",
    "main flow",
    "p0",
    "核心规则",
    "核心流程",
    "主流程",
    "阻断",
    "权限",
    "鉴权",
    "评分规则",
)
_EXCEPTION_RECOVERY_PATTERN_TOKENS = (
    "exception",
    "error",
    "fail",
    "failure",
    "timeout",
    "retry",
    "recover",
    "resume",
    "异常",
    "失败",
    "超时",
    "重试",
    "恢复",
    "保留输入",
    "网络中断",
)
_BOUNDARY_PATTERN_TOKENS = (
    "boundary",
    "limit",
    "edge",
    "max",
    "min",
    "49",
    "50",
    "3.9",
    "7.5",
    "边界",
    "上限",
    "下限",
    "恰好",
    "少于",
    "大于",
    "最多",
    "最少",
)
_CORE_REQUIREMENT_DOMAIN_TOKENS = (
    "讲错题",
    "错题",
    "ai讲错题",
    "ai 讲错题",
    "评分",
    "追问",
    "录音",
    "语音",
    "麦克风",
)
_WEAK_RELATED_DOMAIN_TOKENS = (
    "排课",
    "新增计划",
    "已有计划",
    "学习计划",
    "课堂管理",
    "防抄答案",
    "历史课程",
    "本周任务",
    "本周进度",
    "排行榜",
    "埋点",
    "上报",
    "曝光",
    "纯 ui",
    "ui-only",
)

logger = logging.getLogger(__name__)


def build_priority_sample_pool_filename(project_id: int) -> str:
    return f"priority_sample_pool_project_{project_id}.json"


def _sample_value(sample: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in sample:
            return sample.get(key)
    return None


def _sanitize_text(raw: Any, *, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    if not text:
        return ""
    return text[:max_len]


def _safe_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except Exception:
        return float(default)


def _normalize_signal_type(raw: Any) -> str:
    text = _sanitize_text(raw, max_len=24).lower()
    if text in _VALID_SIGNAL_TYPES:
        return text
    if text in {"pos", "good", "gold", "success", "best_practice"}:
        return "positive"
    return "negative"


def _normalize_pattern_usage(raw: Any, *, signal_type: str) -> str:
    text = _sanitize_text(raw, max_len=24).lower()
    if text in _VALID_PATTERN_USAGE:
        return text
    if signal_type == "positive":
        return "prefer"
    return "avoid"


def _normalize_pattern_category(raw: Any) -> str:
    return _sanitize_text(raw, max_len=64).lower()


def _normalize_sample_source(raw: Any) -> str:
    text = _sanitize_text(raw, max_len=64).lower()
    if not text:
        return "priority_debug_manual_add"
    if text in _VALID_SAMPLE_SOURCES:
        return text
    if text in _SOURCE_LEGACY_MAP:
        return _SOURCE_LEGACY_MAP[text]
    return "manual_pool_input"


def _is_ui_low_value_pattern(*parts: Any) -> bool:
    merged = " ".join(str(part or "") for part in parts).strip().lower()
    if not merged:
        return False
    return any(token in merged for token in _UI_LOW_VALUE_PATTERN_TOKENS)


def _has_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(token and token.lower() in lowered for token in tokens)


def _sample_signal_profile(sample: dict[str, Any], summary: str = "") -> dict[str, bool]:
    search_text = _sample_search_text(sample, summary)
    reason = _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=64).lower()
    category = _normalize_pattern_category(_sample_value(sample, "pattern_category", "patternCategory"))
    priority = _sanitize_text(_sample_value(sample, "expected_priority", "expectedPriority"), max_len=8).upper()
    return {
        "assertable": _has_any_token(search_text, _ASSERTABLE_PATTERN_TOKENS),
        "core_rule": bool(
            priority == "P0"
            or reason in {"core_flow", "state_transition"}
            or category in {"core_flow_closure", "critical_path_coverage", "high_value_assertion"}
            or _has_any_token(search_text, _CORE_RULE_PATTERN_TOKENS)
        ),
        "exception_recovery": bool(
            reason in {"exception_path", "state_transition"}
            or _has_any_token(search_text, _EXCEPTION_RECOVERY_PATTERN_TOKENS)
        ),
        "boundary": bool(
            reason == "boundary_condition"
            or category == "boundary_effective_coverage"
            or _has_any_token(search_text, _BOUNDARY_PATTERN_TOKENS)
        ),
        "core_requirement_domain": _has_any_token(search_text, _CORE_REQUIREMENT_DOMAIN_TOKENS),
        "weak_related_domain": _has_any_token(search_text, _WEAK_RELATED_DOMAIN_TOKENS),
        "ui_low_value": _is_ui_low_value_pattern(search_text),
    }


def _sample_intent_bucket(sample: dict[str, Any]) -> str:
    text = _sample_search_text(sample, str(sample.get("pattern_summary") or ""))
    if _has_any_token(text, ("麦克风", "microphone", "录音", "语音")) and _has_any_token(text, ("权限", "拒绝", "permission", "deny")):
        return "microphone_permission"
    if _has_any_token(text, ("网络", "中断", "恢复", "retry", "recover", "resume")):
        return "network_recovery"
    if _has_any_token(text, ("超时", "504", "timeout")):
        return "timeout_retry"
    if _has_any_token(text, ("500", "上传失败", "服务器错误")):
        return "upload_server_error"
    if _has_any_token(text, ("49", "50", "50%", "字数", "少于50", "恰好50")):
        return "answer_length_boundary"
    if _has_any_token(text, ("3.9", "7.5", "8.9", "鼓励语", "分段")):
        return "score_band_boundary"
    if _has_any_token(text, ("答非所问", "准确性", "完整性", "清晰度")):
        return "scoring_dimensions"
    if _has_any_token(text, ("追问", "轮次", "不重复")):
        return "followup_turn_flow"
    if _has_any_token(text, ("排课", "计划", "课程规划")):
        return "schedule_plan"
    if _has_any_token(text, ("埋点", "上报", "曝光", "tracking", "analytics")):
        return "analytics_tracking"
    if _has_any_token(text, ("展示", "按钮", "文案", "布局", "display", "ui")):
        return "generic_display"
    return ""


def _sample_search_text(sample: dict[str, Any], summary: str = "") -> str:
    return " ".join(
        str(part or "")
        for part in [
            summary,
            _sample_value(sample, "pattern_summary", "patternSummary"),
            _sample_value(sample, "pattern_canonical", "patternCanonical"),
            _sample_value(sample, "pattern_category", "patternCategory"),
            _sample_value(sample, "reason_category", "reasonCategory"),
            _sample_value(sample, "expected_priority", "expectedPriority"),
            _sample_value(sample, "expected_result_quality", "expectedResultQuality"),
            _sample_value(sample, "expected_result_quality_reason", "expectedResultQualityReason"),
            _sample_value(sample, "title"),
            _sample_value(sample, "source_case_title", "sourceCaseTitle"),
            _sample_value(sample, "source_case_module", "sourceCaseModule", "test_module", "testModule"),
            _sample_value(sample, "source_case_steps", "sourceCaseSteps", "steps"),
            _sample_value(sample, "business_assertion", "businessAssertion"),
            _sample_value(sample, "source_case_expected_result", "sourceCaseExpectedResult", "expected_result", "expectedResult"),
            _sample_value(sample, "user_comment", "userComment"),
        ]
        if str(part or "").strip()
    ).lower()


def _canonicalize_pattern_text(raw: Any) -> str:
    text = _sanitize_text(raw, max_len=_MAX_PATTERN_CANONICAL_LEN).lower()
    if not text:
        return ""
    # Remove highly specific tokens so patterns stay reusable.
    text = re.sub(r"(tc|case|rule|req)[\-_ ]?\d+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[`'\"“”‘’\[\]\(\)\{\}<>]", " ", text)
    text = re.sub(r"\d+", " ", text)
    text = re.sub(r"[:;/|,_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_PATTERN_CANONICAL_LEN]


def _canonicalize_intent_text(raw: Any) -> str:
    text = _sanitize_text(raw, max_len=240).lower()
    if not text:
        return ""
    text = re.sub(r"(tc|case|rule|req)[\-_ ]?\d+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\d+(?:\.\d+)?", " ", text)
    replacements = {
        "ai讲错题": "讲错题",
        "ai 讲错题": "讲错题",
        "学员端": "学生端",
        "督导端": "老师端",
        "教师端": "老师端",
        "管理员": "后台",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    stop_words = (
        "验证",
        "检查",
        "查看",
        "观察",
        "页面",
        "模块",
        "功能",
        "场景",
        "边界值",
        "边界",
        "异常",
        "逻辑",
        "显示",
        "提示",
        "按钮",
        "点击",
        "输入",
        "提交",
    )
    for word in stop_words:
        text = text.replace(word, " ")
    text = re.sub(r"[`'\"“”‘’\[\]\(\)\{\}<>：:；;，,。.!！?？、/|_\-]+", " ", text)
    tokens = [token for token in re.split(r"\s+", text) if token]
    return " ".join(tokens[:16])[:_MAX_PATTERN_CANONICAL_LEN]


_LEGACY_HARDCODED_COMMENT_MARKERS = (
    "linked human-final case; extra business coverage is positive evidence",
    "ai-only case is treated as negative only because it has a clear quality failure",
)

_PATTERN_CATEGORY_LABELS = {
    "permission_or_scope_guard": "权限/范围防护",
    "cross_system_business_flow": "跨端业务流程",
    "transaction_business_risk": "交易业务风险",
    "state_consistency_flow": "状态一致性",
    "manual_final_business_coverage": "人工业务覆盖",
    "core_flow_closure": "核心流程闭环",
    "cross_page_flow": "跨页面流程",
    "multi_step_interaction": "多步骤交互",
    "state_transition_pattern": "状态流转",
    "critical_path_coverage": "关键路径覆盖",
    "complex_business_combination": "复杂业务组合",
    "high_value_assertion": "高价值断言",
    "boundary_effective_coverage": "边界有效覆盖",
    "recall_gap_missing_business_coverage": "业务覆盖遗漏",
    "quality_fix_hint": "质量修正建议",
    "hallucination_or_redundant_case": "幻觉/冗余用例",
    "duplicate_redundant": "重复/冗余",
    "schedule_time": "排课/时间规则",
}

_REASON_CATEGORY_LABELS = {
    "core_flow": "核心流程",
    "exception_path": "异常路径",
    "boundary_condition": "边界条件",
    "state_transition": "状态迁移",
    "redundant_case": "冗余用例",
    "display_issue": "展示问题",
    "other": "其他",
    "non_assertable_expected_result": "预期不可断言",
    "priority_overpromotion_for_low_value_ui_case": "低价值展示误提级",
    "hallucination_or_redundant_case": "幻觉/冗余用例",
    "recall_gap": "召回缺口",
    "quality_fix_hint": "质量修正建议",
    "generated_only_defect_misfiled_as_missing": "生成侧缺陷误归为遗漏",
    "generated_only_defect_misfiled_as_modification": "生成侧缺陷误归为修改建议",
    "duplicate_redundant": "重复/冗余",
    "schedule_time": "排课/时间规则",
}


def _clean_sample_user_comment(raw: Any) -> str:
    comment = _sanitize_text(raw, max_len=240)
    lowered = comment.lower()
    if any(marker in lowered for marker in _LEGACY_HARDCODED_COMMENT_MARKERS):
        return ""
    return comment


def _category_label(sample: dict[str, Any], *, signal_type: str) -> str:
    raw_label = _sanitize_text(
        _sample_value(sample, "category_label", "categoryLabel"),
        max_len=80,
    )
    if raw_label:
        return raw_label
    if signal_type == "positive":
        category = _normalize_pattern_category(
            _sample_value(sample, "pattern_category", "patternCategory")
        )
        return _PATTERN_CATEGORY_LABELS.get(category, category)
    reason = _sanitize_text(
        _sample_value(sample, "reason_category", "reasonCategory"),
        max_len=64,
    ).lower()
    return _REASON_CATEGORY_LABELS.get(reason, reason)


def _pattern_quality_score(summary: str, sample: dict[str, Any] | None = None) -> float:
    text = _sanitize_text(summary, max_len=_MAX_PATTERN_SUMMARY_LEN)
    if not text:
        return 0.0
    score = 0.45
    length = len(text)
    if 12 <= length <= 70:
        score += 0.2
    elif length > 70:
        score += 0.1
    # Encourage abstract action/risk words.
    lowered = text.lower()
    if any(token in lowered for token in ("状态", "异常", "一致", "同步", "回滚", "失败", "state", "retry", "rollback", "consisten")):
        score += 0.2
    # Penalize over-specific UI wording.
    if any(token in lowered for token in ("按钮", "页面", "文案", "样式", "布局", "button", "page", "ui")):
        score -= 0.1
    if isinstance(sample, dict):
        profile = _sample_signal_profile(sample, summary)
        if profile["assertable"]:
            score += 0.1
        if profile["core_rule"]:
            score += 0.08
        if profile["exception_recovery"]:
            score += 0.08
        if profile["boundary"]:
            score += 0.08
        if profile["core_requirement_domain"]:
            score += 0.08
        if profile["weak_related_domain"]:
            score -= 0.12
        if profile["ui_low_value"]:
            score -= 0.08
    return round(max(0.0, min(1.0, score)), 4)


def _pattern_source(sample: dict[str, Any], summary: str) -> str:
    original = _sanitize_text(_sample_value(sample, "pattern_summary", "patternSummary"), max_len=_MAX_PATTERN_SUMMARY_LEN)
    if original:
        return "manual"
    return "auto"


def _pattern_cluster_key(sample: dict[str, Any], canonical: str, summary: str) -> str:
    reason = _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=40).lower()
    signal_type = _normalize_signal_type(
        _sample_value(
            sample,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "feedback_direction",
            "feedbackDirection",
            "sample_type",
            "sampleType",
            "sample_kind",
            "sampleKind",
        )
    )
    pattern_category = _normalize_pattern_category(
        _sample_value(sample, "pattern_category", "patternCategory")
    )
    semantic_bucket = pattern_category if signal_type == "positive" and pattern_category else reason
    base = canonical or _canonicalize_pattern_text(summary)
    if not base:
        return semantic_bucket or "misc"
    tokens = [token for token in base.split(" ") if token]
    # Keep a compact semantic signature to cluster near-duplicate variants.
    cluster = " ".join(tokens[:6])[:_MAX_PATTERN_CLUSTER_LEN]
    if semantic_bucket:
        return f"{semantic_bucket}|{cluster}" if cluster else semantic_bucket
    return cluster or "misc"


def _pattern_weight(sample: dict[str, Any], summary: str, quality: float, source: str) -> float:
    expected = _sanitize_text(_sample_value(sample, "expected_priority", "expectedPriority"), max_len=8).upper()
    reason = _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=40).lower()
    signal_type = _normalize_signal_type(
        _sample_value(
            sample,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "feedback_direction",
            "feedbackDirection",
            "sample_type",
            "sampleType",
            "sample_kind",
            "sampleKind",
        )
    )
    pattern_usage = _normalize_pattern_usage(
        _sample_value(sample, "pattern_usage", "patternUsage"),
        signal_type=signal_type,
    )
    is_positive_signal = bool(signal_type == "positive" or pattern_usage == "prefer")
    is_negative_signal = not is_positive_signal
    profile = _sample_signal_profile(sample, summary)
    ui_low_value = bool(profile["ui_low_value"])
    weight = 0.6 + (quality * 0.6)
    if source == "manual":
        weight += 0.15
    if expected in {"P0", "P1"}:
        weight += 0.2
    if reason in {"core_flow", "exception_path", "state_transition"}:
        weight += 0.1
    if reason == "display_issue":
        weight -= 0.05
    if profile["assertable"]:
        weight += 0.18
    if profile["core_rule"]:
        weight += 0.16
    if profile["exception_recovery"]:
        weight += 0.16
    if profile["boundary"]:
        weight += 0.14
    if profile["core_requirement_domain"]:
        weight += 0.18
    if profile["weak_related_domain"]:
        weight -= 0.25
    if is_negative_signal and ui_low_value:
        # Keep UI-negative patterns retrievable so they can suppress low-value UI-only cases.
        weight += 0.08
    if is_positive_signal and ui_low_value:
        # Avoid over-amplifying UI-positive samples in preferred pattern retrieval.
        weight -= 0.18
    adjustment = _safe_float(
        _sample_value(sample, "pattern_weight_adjustment", "patternWeightAdjustment"),
        default=1.0,
    )
    adjustment = max(_MIN_PATTERN_WEIGHT_ADJUSTMENT, min(_MAX_PATTERN_WEIGHT_ADJUSTMENT, adjustment))
    weight *= adjustment
    return round(max(0.3, min(2.0, weight)), 4)


def _default_pattern_summary(sample: dict[str, Any]) -> str:
    reason = _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=40)
    pattern_category = _normalize_pattern_category(
        _sample_value(sample, "pattern_category", "patternCategory")
    )
    title = _sanitize_text(_sample_value(sample, "title"), max_len=100)
    assertion = _sanitize_text(
        _sample_value(sample, "business_assertion", "businessAssertion", "source_case_expected_result", "sourceCaseExpectedResult", "expected_result", "expectedResult"),
        max_len=120,
    )
    comment = _sanitize_text(_sample_value(sample, "user_comment", "userComment"), max_len=120)
    case_id = _sanitize_text(_sample_value(sample, "case_id", "caseId"), max_len=40)
    parts = [part for part in [pattern_category, reason, title, assertion, comment, case_id] if part]
    if not parts:
        return ""
    return " | ".join(parts)[:_MAX_PATTERN_SUMMARY_LEN]


def normalize_priority_sample(sample: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(sample or {})
    scaffold_snapshot = {
        key: normalized.get(key)
        for key in _EXECUTION_SCAFFOLD_FIELDS
        if key in normalized and normalized.get(key) not in (None, "", [], {})
    }
    if scaffold_snapshot:
        normalized["source_execution_scaffold"] = scaffold_snapshot
        normalized["execution_scaffold_learning_policy"] = "source_only_do_not_reuse"
        for key in _EXECUTION_SCAFFOLD_FIELDS:
            normalized.pop(key, None)
    cleaned_comment = _clean_sample_user_comment(
        _sample_value(normalized, "user_comment", "userComment")
    )
    normalized["user_comment"] = cleaned_comment
    normalized["userComment"] = cleaned_comment
    pattern_category = _normalize_pattern_category(
        _sample_value(normalized, "pattern_category", "patternCategory")
    )
    if pattern_category:
        normalized["pattern_category"] = pattern_category
    summary = _sanitize_text(
        _sample_value(normalized, "pattern_summary", "patternSummary"),
        max_len=_MAX_PATTERN_SUMMARY_LEN,
    )
    if len(summary) < _MIN_PATTERN_SUMMARY_LEN:
        summary = _default_pattern_summary(normalized)
    canonical = _canonicalize_pattern_text(summary)
    source = _pattern_source(normalized, summary)
    quality = _pattern_quality_score(summary, normalized)
    weight = _pattern_weight(normalized, summary, quality, source)
    cluster_key = _pattern_cluster_key(normalized, canonical, summary)
    signal_type = _normalize_signal_type(
        _sample_value(
            normalized,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "feedback_direction",
            "feedbackDirection",
            "sample_type",
            "sampleType",
            "sample_kind",
            "sampleKind",
        )
    )
    normalized["pattern_summary"] = summary
    normalized["pattern_canonical"] = canonical
    normalized["pattern_cluster_key"] = cluster_key
    normalized["pattern_source"] = source
    pattern_scope = _sanitize_text(
        _sample_value(normalized, "pattern_scope", "patternScope"),
        max_len=40,
    ).lower()
    normalized["pattern_scope"] = pattern_scope or "project"
    pattern_grain = _sanitize_text(
        _sample_value(normalized, "pattern_grain", "patternGrain"),
        max_len=40,
    ).lower()
    normalized["pattern_grain"] = pattern_grain or ("pattern" if signal_type == "positive" else "anti_pattern")
    normalized["pattern_quality_score"] = quality
    normalized["pattern_weight"] = weight
    status = _sanitize_text(
        _sample_value(normalized, "governance_status", "pattern_status", "patternStatus"),
        max_len=16,
    ).lower()
    normalized["governance_status"] = "disabled" if status == "disabled" else "active"
    sample_status = _sanitize_text(
        _sample_value(normalized, "status", "sampleStatus"),
        max_len=16,
    ).lower()
    normalized["status"] = "deleted" if sample_status == "deleted" else "active"
    deleted_at = _sample_value(normalized, "deleted_at", "deletedAt")
    if deleted_at is not None:
        try:
            datetime.fromisoformat(str(deleted_at))
            normalized["deleted_at"] = str(deleted_at)
        except (ValueError, TypeError):
            normalized["deleted_at"] = None
    else:
        normalized["deleted_at"] = None
    delete_reason = _sanitize_text(
        _sample_value(normalized, "delete_reason", "deleteReason"),
        max_len=256,
    )
    normalized["delete_reason"] = delete_reason or None
    normalized["source"] = _normalize_sample_source(
        _sample_value(normalized, "source", "sampleSource", "sample_source"),
    )
    # Learning confirmation tracking
    learning_status = _sanitize_text(
        _sample_value(normalized, "learning_status", "learningStatus"),
        max_len=24,
    ).lower()
    normalized["learning_status"] = learning_status if learning_status in {"user_confirmed", "system_candidate", "rejected"} else None
    learning_confirmed_at = _sample_value(normalized, "learning_confirmed_at", "learningConfirmedAt")
    if normalized["learning_status"] == "user_confirmed" and learning_confirmed_at is not None:
        try:
            datetime.fromisoformat(str(learning_confirmed_at))
            normalized["learning_confirmed_at"] = str(learning_confirmed_at)
        except (ValueError, TypeError):
            normalized["learning_confirmed_at"] = None
    else:
        normalized["learning_confirmed_at"] = None
    learning_confirmed_by = _sample_value(normalized, "learning_confirmed_by", "learningConfirmedBy")
    if normalized["learning_status"] == "user_confirmed" and learning_confirmed_by is not None:
        try:
            normalized["learning_confirmed_by"] = int(learning_confirmed_by)
        except (ValueError, TypeError):
            normalized["learning_confirmed_by"] = None
    else:
        normalized["learning_confirmed_by"] = None
    # ── Data-contract canonical fields ──
    # source_type: canonical name for sample origin (was "source")
    normalized["source_type"] = _normalize_sample_source(
        _sample_value(normalized, "source_type", "sourceType", "source", "sampleSource", "sample_source"),
    )
    # source_id: the ID of the originating external entity (evaluation, generation, etc.)
    source_id_raw = _sample_value(normalized, "source_id", "sourceId", "generation_id", "generationId")
    if source_id_raw is not None:
        try:
            normalized["source_id"] = int(source_id_raw)
        except (ValueError, TypeError):
            normalized["source_id"] = None
    else:
        normalized["source_id"] = None
    # source_case_id: the originating case identifier
    source_case_id = _sanitize_text(
        _sample_value(
            normalized,
            "source_case_id", "sourceCaseId",
            "case_id", "caseId",
        ),
        max_len=256,
    )
    normalized["source_case_id"] = source_case_id or None
    normalized["source_case_title"] = _sanitize_text(
        _sample_value(normalized, "source_case_title", "sourceCaseTitle", "title"),
        max_len=160,
    ) or None
    normalized["source_case_module"] = _sanitize_text(
        _sample_value(normalized, "source_case_module", "sourceCaseModule", "test_module", "testModule"),
        max_len=120,
    ) or None
    normalized["source_case_steps"] = _sanitize_text(
        _sample_value(normalized, "source_case_steps", "sourceCaseSteps", "steps"),
        max_len=240,
    ) or None
    business_assertion = _sanitize_text(
        _sample_value(
            normalized,
            "business_assertion", "businessAssertion",
            "source_case_expected_result", "sourceCaseExpectedResult",
            "expected_result", "expectedResult",
        ),
        max_len=240,
    )
    normalized["source_case_expected_result"] = business_assertion or None
    normalized["business_assertion"] = business_assertion or None
    # sample_kind: unified with signal_type ('positive'/'negative')
    normalized["sample_kind"] = signal_type
    # confidence: canonical name (was pattern_confidence)
    confidence_raw = _sample_value(normalized, "confidence", "pattern_confidence", "patternConfidence")
    normalized["confidence"] = round(max(0.0, min(1.0, _safe_float(confidence_raw, default=0.5))), 4)
    category_source = _sanitize_text(
        _sample_value(normalized, "category_source", "categorySource"),
        max_len=40,
    )
    category_label = _category_label(normalized, signal_type=signal_type)
    normalized["category_label"] = category_label or ""
    normalized["category_source"] = category_source or ("backend_inferred" if category_label else "")
    normalized["category_confidence"] = normalized["confidence"] if category_label else None
    # Keep legacy aliases for backward compatibility
    normalized["source"] = normalized["source_type"]
    normalized["pattern_confidence"] = normalized["confidence"]
    # ── End data-contract block ──
    normalized["signal_type"] = signal_type
    normalized["pattern_usage"] = _normalize_pattern_usage(
        _sample_value(normalized, "pattern_usage", "patternUsage"),
        signal_type=signal_type,
    )
    if signal_type == "positive" and not normalized.get("pattern_category"):
        legacy_reason = _sanitize_text(
            _sample_value(normalized, "reason_category", "reasonCategory"),
            max_len=40,
        ).lower()
        if legacy_reason:
            normalized["pattern_category"] = legacy_reason
    adjustment = _safe_float(
        _sample_value(normalized, "pattern_weight_adjustment", "patternWeightAdjustment"),
        default=1.0,
    )
    normalized["pattern_weight_adjustment"] = round(
        max(_MIN_PATTERN_WEIGHT_ADJUSTMENT, min(_MAX_PATTERN_WEIGHT_ADJUSTMENT, adjustment)),
        4,
    )
    return normalized


def _dedupe_priority_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    winners: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for sample in samples:
        canonical = _sanitize_text(sample.get("pattern_canonical"), max_len=_MAX_PATTERN_CANONICAL_LEN)
        if not canonical:
            canonical = _canonicalize_pattern_text(sample.get("pattern_summary"))
        if not canonical:
            # Fallback key for malformed items.
            canonical = f"sample:{len(order)}"
        current = winners.get(canonical)
        if current is None:
            winners[canonical] = sample
            order.append(canonical)
            continue
        left = float(current.get("pattern_weight") or 0.0)
        right = float(sample.get("pattern_weight") or 0.0)
        if right > left:
            winners[canonical] = sample
            continue
        if right == left:
            left_q = float(current.get("pattern_quality_score") or 0.0)
            right_q = float(sample.get("pattern_quality_score") or 0.0)
            if right_q > left_q:
                winners[canonical] = sample
    return [winners[key] for key in order if key in winners]


def _aggregate_by_cluster(
    samples: list[dict[str, Any]],
    max_per_cluster: int = _MAX_SAMPLES_PER_CLUSTER,
) -> list[dict[str, Any]]:
    """Group by pattern_cluster_key, keep top-N per cluster by weight."""
    if not samples:
        return []
    clusters: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        key = _sanitize_text(s.get("pattern_cluster_key"), max_len=_MAX_PATTERN_CLUSTER_LEN) or "misc"
        if key not in clusters:
            clusters[key] = []
        clusters[key].append(s)
    result: list[dict[str, Any]] = []
    for _key, items in clusters.items():
        items.sort(
            key=lambda s: (
                float(s.get("pattern_weight") or 0.0),
                float(s.get("pattern_quality_score") or 0.0),
                int(_sample_signal_profile(s, str(s.get("pattern_summary") or "")).get("core_requirement_domain")),
            ),
            reverse=True,
        )
        result.extend(items[:max(1, int(max_per_cluster))])
    return result


def _apply_source_limits(
    samples: list[dict[str, Any]],
    limits: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Cap samples per source type, keeping highest-weight samples within each source."""
    if not samples:
        return []
    effective_limits = limits if isinstance(limits, dict) else _MAX_SAMPLES_PER_SOURCE
    source_buckets: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        src = _sanitize_text(s.get("source"), max_len=64) or "manual_pool_input"
        if src not in source_buckets:
            source_buckets[src] = []
        source_buckets[src].append(s)
    result: list[dict[str, Any]] = []
    for src, items in source_buckets.items():
        limit = int(effective_limits.get(src, 500))
        items.sort(
            key=lambda s: (
                float(s.get("pattern_weight") or 0.0),
                float(s.get("pattern_quality_score") or 0.0),
            ),
            reverse=True,
        )
        result.extend(items[:max(1, limit)])
    return result


def _apply_signal_type_limits(
    samples: list[dict[str, Any]],
    limits: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Cap samples per signal_type (positive/negative)."""
    if not samples:
        return []
    effective_limits = limits if isinstance(limits, dict) else _MAX_SAMPLES_PER_SIGNAL_TYPE
    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    for s in samples:
        st = _sanitize_text(s.get("signal_type"), max_len=16).lower()
        if st == "positive":
            positive.append(s)
        else:
            negative.append(s)
    positive.sort(
        key=lambda s: (
            float(s.get("pattern_weight") or 0.0),
            float(s.get("pattern_quality_score") or 0.0),
        ),
        reverse=True,
    )
    negative.sort(
        key=lambda s: (
            float(s.get("pattern_weight") or 0.0),
            float(s.get("pattern_quality_score") or 0.0),
        ),
        reverse=True,
    )
    pos_limit = max(1, int(effective_limits.get("positive", 2000)))
    neg_limit = max(1, int(effective_limits.get("negative", 3000)))
    return positive[:pos_limit] + negative[:neg_limit]


def derive_patterns_from_samples(
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build aggregated pattern layer from raw samples.

    Groups samples by pattern_cluster_key and produces one pattern per cluster
    with aggregated statistics, keeping the best representative summary.
    """
    if not samples:
        return []
    clusters: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        key = _sanitize_text(s.get("pattern_cluster_key"), max_len=_MAX_PATTERN_CLUSTER_LEN) or "misc"
        if key not in clusters:
            clusters[key] = []
        clusters[key].append(s)

    patterns: list[dict[str, Any]] = []
    for cluster_key, items in sorted(clusters.items()):
        items.sort(key=lambda s: float(s.get("pattern_weight") or 0.0), reverse=True)
        best = items[0]
        signal_type = _sanitize_text(best.get("signal_type"), max_len=16).lower() or "negative"
        sample_count = len(items)
        weights = [float(s.get("pattern_weight") or 0.0) for s in items]
        confidences = [float(s.get("confidence") or s.get("pattern_confidence") or 0.0) for s in items]
        avg_weight = round(sum(weights) / len(weights), 4) if weights else 0.0
        avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        source_types = list(dict.fromkeys(
            _sanitize_text(s.get("source_type") or s.get("source") or "manual_pool_input", max_len=64)
            for s in items
        ))
        active_sample_ids = [
            _sanitize_text(_sample_value(s, "sample_id", "sampleId"), max_len=512)
            for s in items
        ]
        active_sample_ids = [sid for sid in active_sample_ids if sid]
        patterns.append({
            "pattern_id": f"pat_{cluster_key}",
            "cluster_key": cluster_key,
            "signal_type": signal_type,
            "pattern_usage": _sanitize_text(best.get("pattern_usage"), max_len=24) or (
                "prefer" if signal_type == "positive" else "avoid"
            ),
            "pattern_summary": _sanitize_text(best.get("pattern_summary"), max_len=_MAX_PATTERN_SUMMARY_LEN),
            "pattern_canonical": _sanitize_text(best.get("pattern_canonical"), max_len=_MAX_PATTERN_CANONICAL_LEN),
            "pattern_category": _sanitize_text(best.get("pattern_category"), max_len=64),
            "reason_category": _sanitize_text(best.get("reason_category"), max_len=64),
            "pattern_scope": _sanitize_text(best.get("pattern_scope"), max_len=40) or "project",
            "pattern_grain": _sanitize_text(best.get("pattern_grain"), max_len=40) or (
                "pattern" if signal_type == "positive" else "anti_pattern"
            ),
            "sample_count": sample_count,
            "avg_confidence": avg_confidence,
            "avg_weight": avg_weight,
            "top_weight": round(max(weights), 4) if weights else 0.0,
            "top_source_types": source_types[:5],
            "active_sample_ids": active_sample_ids[:20],
            "governance_status": _sanitize_text(best.get("governance_status"), max_len=16) or "active",
        })
    patterns.sort(key=lambda p: (p.get("avg_weight") or 0.0), reverse=True)
    return patterns


def derive_signals_from_patterns(
    patterns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert aggregated patterns into activation signals for vector indexing.

    Each signal is a lightweight entry suitable for embedding and retrieval.
    Signals are filtered: only active patterns with sufficient weight are emitted.
    """
    if not patterns:
        return []
    signals: list[dict[str, Any]] = []
    for pat in patterns:
        weight = float(pat.get("avg_weight") or 0.0)
        sample_count = int(pat.get("sample_count") or 0)
        governance = _sanitize_text(pat.get("governance_status"), max_len=16)
        if governance == "disabled":
            continue
        if sample_count < 1 and weight < 0.5:
            continue
        signals.append({
            "signal_id": f"sig_{pat.get('pattern_id', 'unknown')}",
            "pattern_id": pat.get("pattern_id"),
            "cluster_key": pat.get("cluster_key"),
            "signal_type": pat.get("signal_type"),
            "pattern_usage": pat.get("pattern_usage"),
            "activation_weight": round(
                weight * (1.0 + min(0.3, sample_count * 0.02)),
                4,
            ),
            "pattern_summary": pat.get("pattern_summary"),
            "pattern_canonical": pat.get("pattern_canonical"),
            "pattern_category": pat.get("pattern_category"),
            "reason_category": pat.get("reason_category"),
            "pattern_scope": pat.get("pattern_scope"),
            "pattern_grain": pat.get("pattern_grain"),
            "sample_count": sample_count,
            "top_source_types": pat.get("top_source_types"),
            "governance_status": governance,
        })
    signals.sort(key=lambda s: float(s.get("activation_weight") or 0.0), reverse=True)
    return signals


def normalize_raw_priority_samples(samples: list[dict[str, Any]] | None, *, max_items: int = _MAX_POOL_SAMPLES) -> list[dict[str, Any]]:
    """Normalize persisted sample-pool rows and collapse semantic duplicates."""
    normalized: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    seen_semantic: dict[str, int] = {}
    seen_family: dict[str, int] = {}
    for input_index, item in enumerate((samples if isinstance(samples, list) else []), start=1):
        if not isinstance(item, dict):
            continue
        sample = normalize_priority_sample(item)
        sample_id = _sanitize_text(
            _sample_value(sample, "sample_id", "sampleId"),
            max_len=512,
        )
        if not sample_id:
            sample_id = f"sample:auto:{input_index}"
            suffix = 1
            while sample_id in seen_ids:
                suffix += 1
                sample_id = f"sample:auto:{input_index}:{suffix}"
            sample["sample_id"] = sample_id
            sample["sampleId"] = sample_id
        if sample_id in seen_ids:
            target_idx = seen_ids[sample_id]
            normalized[target_idx] = _choose_raw_sample_winner(normalized[target_idx], sample)
            continue
        semantic_key = _raw_sample_semantic_key(sample)
        if semantic_key and semantic_key in seen_semantic:
            target_idx = seen_semantic[semantic_key]
            normalized[target_idx] = _choose_raw_sample_winner(normalized[target_idx], sample)
            kept_id = _sanitize_text(
                _sample_value(normalized[target_idx], "sample_id", "sampleId"),
                max_len=512,
            )
            if kept_id:
                seen_ids[kept_id] = target_idx
            continue
        family_key = _raw_sample_family_key(sample)
        if family_key and family_key in seen_family:
            target_idx = seen_family[family_key]
            existing_has_detail = _sample_has_business_detail(normalized[target_idx])
            incoming_has_detail = _sample_has_business_detail(sample)
            if not (existing_has_detail and incoming_has_detail):
                normalized[target_idx] = _choose_raw_sample_winner(normalized[target_idx], sample)
                kept_id = _sanitize_text(
                    _sample_value(normalized[target_idx], "sample_id", "sampleId"),
                    max_len=512,
                )
                if kept_id:
                    seen_ids[kept_id] = target_idx
                if semantic_key:
                    seen_semantic[semantic_key] = target_idx
                continue
        seen_ids[sample_id] = len(normalized)
        if semantic_key:
            seen_semantic[semantic_key] = len(normalized)
        if family_key:
            current_family_idx = seen_family.get(family_key)
            if current_family_idx is None or not _sample_has_business_detail(normalized[current_family_idx]):
                seen_family[family_key] = len(normalized)
        normalized.append(sample)
    return normalized[: max(1, int(max_items))]


def _sample_has_business_detail(sample: dict[str, Any]) -> bool:
    if _sanitize_text(_sample_value(sample, "source_case_steps", "sourceCaseSteps", "steps"), max_len=240):
        return True
    if _sanitize_text(
        _sample_value(
            sample,
            "source_case_expected_result",
            "sourceCaseExpectedResult",
            "business_assertion",
            "businessAssertion",
            "expected_result",
            "expectedResult",
        ),
        max_len=240,
    ):
        return True
    workflow_blueprint = _sample_value(sample, "workflow_blueprint", "workflowBlueprint")
    return isinstance(workflow_blueprint, dict) and bool(workflow_blueprint.get("steps"))


def _raw_sample_family_key(sample: dict[str, Any]) -> str:
    signal_type = _normalize_signal_type(
        _sample_value(sample, "signal_type", "signalType", "sample_kind", "sampleKind")
    )
    category = (
        _normalize_pattern_category(_sample_value(sample, "pattern_category", "patternCategory"))
        if signal_type == "positive"
        else _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=64).lower()
    )
    pattern_grain = _sanitize_text(
        _sample_value(sample, "pattern_grain", "patternGrain"),
        max_len=40,
    ).lower()
    if pattern_grain == "workflow_blueprint":
        return _raw_sample_semantic_key(sample)
    title_key = _canonicalize_pattern_text(
        _sample_value(sample, "source_case_title", "sourceCaseTitle", "title")
    )
    module_key = _canonicalize_pattern_text(
        _sample_value(sample, "source_case_module", "sourceCaseModule", "test_module", "testModule")
    )
    intent_key = _canonicalize_intent_text(
        " ".join(
            str(part or "")
            for part in [
                _sample_value(sample, "source_case_module", "sourceCaseModule", "test_module", "testModule"),
                _sample_value(sample, "source_case_title", "sourceCaseTitle", "title"),
            ]
            if str(part or "").strip()
        )
    )
    intent_bucket = _sample_intent_bucket(sample)
    strong_intent_bucket = intent_bucket if intent_bucket not in {"generic_display", "schedule_plan", "analytics_tracking"} else ""
    family_intent = strong_intent_bucket or intent_key or title_key
    if not family_intent:
        return ""
    return "|".join(
        part
        for part in [
            signal_type,
            category,
            module_key,
            family_intent,
        ]
        if part
    )


def _raw_sample_semantic_key(sample: dict[str, Any]) -> str:
    signal_type = _normalize_signal_type(
        _sample_value(sample, "signal_type", "signalType", "sample_kind", "sampleKind")
    )
    source_type = _normalize_sample_source(
        _sample_value(sample, "source_type", "sourceType", "source")
    )
    category = (
        _normalize_pattern_category(_sample_value(sample, "pattern_category", "patternCategory"))
        if signal_type == "positive"
        else _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=64).lower()
    )
    pattern_grain = _sanitize_text(
        _sample_value(sample, "pattern_grain", "patternGrain"),
        max_len=40,
    ).lower()
    if pattern_grain == "workflow_blueprint":
        workflow_blueprint = _sample_value(sample, "workflow_blueprint", "workflowBlueprint")
        workflow_id = ""
        workflow_name = ""
        if isinstance(workflow_blueprint, dict):
            workflow_id = _sanitize_text(workflow_blueprint.get("id"), max_len=120).lower()
            workflow_name = _canonicalize_pattern_text(workflow_blueprint.get("name"))
        workflow_key = (
            workflow_id
            or workflow_name
            or _canonicalize_pattern_text(_sample_value(sample, "pattern_summary", "patternSummary"))
        )
        return "|".join(
            part
            for part in [
                signal_type,
                pattern_grain,
                category,
                workflow_key or "workflow_blueprint",
            ]
            if part
        )
    expected = _sanitize_text(
        _sample_value(sample, "expected_priority", "expectedPriority"),
        max_len=8,
    ).upper()
    title_key = _canonicalize_pattern_text(
        _sample_value(sample, "source_case_title", "sourceCaseTitle", "title")
    )
    module_key = _canonicalize_pattern_text(
        _sample_value(sample, "source_case_module", "sourceCaseModule", "test_module", "testModule")
    )
    assertion_key = _canonicalize_pattern_text(
        _sample_value(
            sample,
            "business_assertion", "businessAssertion",
            "source_case_expected_result", "sourceCaseExpectedResult",
            "expected_result", "expectedResult",
        )
    )
    steps_key = _canonicalize_pattern_text(
        _sample_value(sample, "source_case_steps", "sourceCaseSteps", "steps")
    )[:80]
    intent_key = _canonicalize_intent_text(
        " ".join(
            str(part or "")
            for part in [
                _sample_value(sample, "source_case_module", "sourceCaseModule", "test_module", "testModule"),
                _sample_value(sample, "source_case_title", "sourceCaseTitle", "title"),
                _sample_value(sample, "business_assertion", "businessAssertion", "source_case_expected_result", "sourceCaseExpectedResult", "expected_result", "expectedResult"),
            ]
            if str(part or "").strip()
        )
    )
    intent_bucket = _sample_intent_bucket(sample)
    strong_intent_bucket = intent_bucket if intent_bucket not in {"generic_display", "schedule_plan", "analytics_tracking"} else ""
    if not title_key and not assertion_key:
        fallback = _sanitize_text(sample.get("pattern_canonical"), max_len=_MAX_PATTERN_CANONICAL_LEN)
        if not fallback:
            fallback = _canonicalize_pattern_text(sample.get("pattern_summary"))
        title_key = fallback
    if not title_key and not assertion_key and not intent_key:
        return ""
    return "|".join(
        part
        for part in [
            signal_type,
            category,
            module_key,
            strong_intent_bucket or intent_key or title_key,
            "" if strong_intent_bucket else assertion_key[:96],
        ]
        if part
    )


def _choose_raw_sample_winner(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    def rank(sample: dict[str, Any]) -> tuple[float, int, int, int, float, float, int]:
        edited_score = 0
        if _clean_sample_user_comment(_sample_value(sample, "user_comment", "userComment")):
            edited_score += 4
        if _sanitize_text(_sample_value(sample, "expected_priority", "expectedPriority"), max_len=8):
            edited_score += 2
        if _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=64):
            edited_score += 1
        if _sanitize_text(_sample_value(sample, "pattern_category", "patternCategory"), max_len=64):
            edited_score += 1
        if _sanitize_text(_sample_value(sample, "learning_status", "learningStatus"), max_len=24):
            edited_score += 2
        fidelity_score = 0
        if _sanitize_text(_sample_value(sample, "source_case_steps", "sourceCaseSteps", "steps"), max_len=240):
            fidelity_score += 2
        if _sanitize_text(
            _sample_value(
                sample,
                "source_case_expected_result",
                "sourceCaseExpectedResult",
                "business_assertion",
                "businessAssertion",
                "expected_result",
                "expectedResult",
            ),
            max_len=240,
        ):
            fidelity_score += 2
        if _sanitize_text(
            _sample_value(sample, "source_case_module", "sourceCaseModule", "test_module", "testModule"),
            max_len=120,
        ):
            fidelity_score += 1
        if _sanitize_text(_sample_value(sample, "source_case_title", "sourceCaseTitle", "title"), max_len=160):
            fidelity_score += 1
        workflow_blueprint = _sample_value(sample, "workflow_blueprint", "workflowBlueprint")
        if isinstance(workflow_blueprint, dict) and isinstance(workflow_blueprint.get("steps"), list):
            fidelity_score += min(4, len(workflow_blueprint.get("steps") or []))
        active_score = 1.0 if sample.get("status") != "deleted" else 0.0
        confidence = _safe_float(_sample_value(sample, "confidence", "pattern_confidence", "patternConfidence"), default=0.0)
        weight = _safe_float(_sample_value(sample, "pattern_weight", "patternWeight"), default=0.0)
        profile = _sample_signal_profile(sample, str(sample.get("pattern_summary") or ""))
        signal_score = (
            int(profile["assertable"]) * 3
            + int(profile["core_rule"]) * 3
            + int(profile["exception_recovery"]) * 2
            + int(profile["boundary"]) * 2
            + int(profile["core_requirement_domain"]) * 2
            - int(profile["weak_related_domain"]) * 2
            - int(profile["ui_low_value"])
        )
        priority = _sanitize_text(_sample_value(sample, "expected_priority", "expectedPriority"), max_len=8).upper()
        priority_score = {"P0": 3, "P1": 2, "P2": 1}.get(priority, 0)
        return (active_score, signal_score, edited_score, fidelity_score, weight, confidence, priority_score)

    return right if rank(right) > rank(left) else left


def select_priority_pattern_samples(samples: list[dict[str, Any]] | None, *, max_items: int = _MAX_INDEXED_PATTERN_SAMPLES) -> list[dict[str, Any]]:
    """Select bounded representatives for prompt/index pattern activation."""
    normalized: list[dict[str, Any]] = []
    for item in (samples if isinstance(samples, list) else []):
        if isinstance(item, dict):
            normalized.append(normalize_priority_sample(item))
    normalized = [s for s in normalized if s.get("status") != "deleted"]
    deduped = _dedupe_priority_samples(normalized)
    clustered = _aggregate_by_cluster(deduped)
    source_capped = _apply_source_limits(clustered)
    signal_capped = _apply_signal_type_limits(source_capped)
    return signal_capped[: max(1, int(max_items))]


def normalize_priority_samples(samples: list[dict[str, Any]] | None, *, max_items: int = _MAX_INDEXED_PATTERN_SAMPLES) -> list[dict[str, Any]]:
    """Backward-compatible pattern-layer normalization."""
    return select_priority_pattern_samples(samples, max_items=max_items)


def _build_priority_pattern_doc_id(project_id: int, user_id: int) -> str:
    return f"{_PRIORITY_PATTERN_VECTOR_DOC_PREFIX}_p{int(project_id)}_u{int(user_id)}"


def _build_priority_pattern_chunk(sample: dict[str, Any], sample_index: int) -> dict[str, Any] | None:
    summary = _sanitize_text(sample.get("pattern_summary"), max_len=_MAX_PATTERN_SUMMARY_LEN)
    if not summary:
        return None
    sample_id = _sanitize_text(_sample_value(sample, "sample_id", "sampleId"), max_len=512)
    title = _sanitize_text(_sample_value(sample, "title"), max_len=120)
    source_case_id = _sanitize_text(
        _sample_value(sample, "source_case_id", "sourceCaseId", "case_id", "caseId"),
        max_len=256,
    )
    source_case_title = _sanitize_text(
        _sample_value(sample, "source_case_title", "sourceCaseTitle"),
        max_len=160,
    )
    source_case_module = _sanitize_text(
        _sample_value(sample, "source_case_module", "sourceCaseModule", "test_module", "testModule"),
        max_len=120,
    )
    source_case_steps = _sanitize_text(
        _sample_value(sample, "source_case_steps", "sourceCaseSteps", "steps"),
        max_len=240,
    )
    source_case_expected = _sanitize_text(
        _sample_value(
            sample,
            "source_case_expected_result",
            "sourceCaseExpectedResult",
            "business_assertion",
            "businessAssertion",
            "expected_result",
            "expectedResult",
        ),
        max_len=240,
    )
    comment = _sanitize_text(_sample_value(sample, "user_comment", "userComment"), max_len=160)
    reason = _sanitize_text(_sample_value(sample, "reason_category", "reasonCategory"), max_len=40)
    pattern_category = _normalize_pattern_category(
        _sample_value(sample, "pattern_category", "patternCategory")
    )
    expected_priority = _sanitize_text(
        _sample_value(sample, "expected_priority", "expectedPriority"),
        max_len=8,
    )
    case_id = _sanitize_text(_sample_value(sample, "case_id", "caseId"), max_len=40)
    pattern_canonical = _sanitize_text(sample.get("pattern_canonical"), max_len=_MAX_PATTERN_CANONICAL_LEN)
    pattern_cluster_key = _sanitize_text(sample.get("pattern_cluster_key"), max_len=_MAX_PATTERN_CLUSTER_LEN)
    pattern_source = _sanitize_text(sample.get("pattern_source"), max_len=20)
    pattern_scope = _sanitize_text(_sample_value(sample, "pattern_scope", "patternScope"), max_len=40).lower()
    pattern_grain = _sanitize_text(_sample_value(sample, "pattern_grain", "patternGrain"), max_len=40).lower()
    workflow_blueprint_text = ""
    workflow_blueprint = _sample_value(sample, "workflow_blueprint", "workflowBlueprint")
    if isinstance(workflow_blueprint, dict):
        step_texts: list[str] = []
        for step in workflow_blueprint.get("steps") or []:
            if not isinstance(step, dict):
                continue
            label = _sanitize_text(
                step.get("label") or step.get("action") or step.get("description"),
                max_len=120,
            )
            state_in = _sanitize_text(step.get("state_in"), max_len=80)
            state_out = _sanitize_text(step.get("state_out"), max_len=80)
            actor = _sanitize_text(step.get("actor"), max_len=40)
            step_text = " ".join(part for part in [actor, label, state_in, state_out] if part)
            if step_text:
                step_texts.append(step_text)
        workflow_blueprint_text = " -> ".join(step_texts[:12])
    governance_status = _sanitize_text(sample.get("governance_status"), max_len=16).lower() or "active"
    signal_type = _normalize_signal_type(
        _sample_value(
            sample,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "feedback_direction",
            "feedbackDirection",
            "sample_type",
            "sampleType",
            "sample_kind",
            "sampleKind",
        )
    )
    pattern_usage = _normalize_pattern_usage(
        _sample_value(sample, "pattern_usage", "patternUsage"),
        signal_type=signal_type,
    )
    try:
        pattern_quality_score = float(sample.get("pattern_quality_score"))
    except Exception:
        pattern_quality_score = 0.0
    try:
        pattern_weight = float(sample.get("pattern_weight"))
    except Exception:
        pattern_weight = 0.6
    chunk_text = " ".join(
        part
        for part in [
            summary,
            f"canonical:{pattern_canonical}" if pattern_canonical else "",
            f"cluster:{pattern_cluster_key}" if pattern_cluster_key else "",
            f"title:{title}" if title else "",
            f"reason:{reason}" if reason else "",
            f"pattern_category:{pattern_category}" if pattern_category else "",
            f"signal:{signal_type}",
            f"usage:{pattern_usage}",
            f"scope:{pattern_scope}" if pattern_scope else "",
            f"grain:{pattern_grain}" if pattern_grain else "",
            f"workflow_blueprint:{workflow_blueprint_text}" if workflow_blueprint_text else "",
            f"source_title:{source_case_title}" if source_case_title else "",
            f"source_module:{source_case_module}" if source_case_module else "",
            f"source_steps:{source_case_steps}" if source_case_steps else "",
            f"source_assertion:{source_case_expected}" if source_case_expected else "",
            f"expected:{expected_priority}" if expected_priority else "",
            f"comment:{comment}" if comment else "",
            f"case:{case_id or source_case_id}" if (case_id or source_case_id) else "",
            f"sample:{sample_id}" if sample_id else "",
        ]
        if part
    )
    if not chunk_text:
        return None
    return {
        "chunk_text": chunk_text,
        "metadata": {
            "sample_index": int(sample_index),
            "pattern_summary": summary,
            "pattern_canonical": pattern_canonical,
            "pattern_cluster_key": pattern_cluster_key,
            "pattern_source": pattern_source,
            "pattern_scope": pattern_scope or "project",
            "pattern_grain": pattern_grain or ("pattern" if signal_type == "positive" else "anti_pattern"),
            "governance_status": "disabled" if governance_status == "disabled" else "active",
            "signal_type": signal_type,
            "pattern_usage": pattern_usage,
            "pattern_quality_score": round(max(0.0, min(1.0, pattern_quality_score)), 4),
            "pattern_weight": round(max(0.3, min(1.8, pattern_weight)), 4),
            "reason_category": reason,
            "pattern_category": pattern_category,
            "expected_priority": expected_priority,
            "case_id": case_id,
            "sample_id": sample_id,
            "source_case_id": source_case_id,
            "source_case_title": source_case_title,
            "source_case_module": source_case_module,
        },
    }


def _sync_priority_pool_pattern_index(
    *,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    pattern_index_token: str | None,
    samples: list[dict[str, Any]],
    signals: list[dict[str, Any]] | None = None,
) -> None:
    safe_token = _sanitize_text(pattern_index_token, max_len=48) or datetime.utcnow().strftime("%Y%m%d%H%M%S")
    doc_id = f"{_build_priority_pattern_doc_id(project_id=project_id, user_id=user_id)}_{safe_token}"
    chunks: list[dict[str, Any]] = []
    # Keep vector metadata pointers aligned with the persisted raw sample list.
    # Aggregated signals are sorted independently, so using their ordinal as
    # sample_index can route retrieval hits back to the wrong raw sample.
    source_entries = samples
    for idx, entry in enumerate(source_entries[:_MAX_INDEXED_PATTERN_SAMPLES]):
        chunk = _build_priority_pattern_chunk(entry, idx)
        if chunk:
            chunks.append(chunk)

    if not chunks:
        return

    vector_store = get_vector_store()
    if not vector_store.is_ready():
        return
    content = "\n".join(str(item.get("chunk_text") or "") for item in chunks if item.get("chunk_text"))
    try:
        vector_store.add_document(
            doc_id=doc_id,
            content=content,
            metadata={
                "project_id": int(project_id),
                "user_id": int(user_id),
                "doc_type": PRIORITY_SAMPLE_POOL_PATTERN_DOC_TYPE,
                "filename": build_priority_sample_pool_filename(project_id),
                "doc_id": doc_id,
                "generation_id": int(generation_id) if generation_id is not None else "",
                "pattern_index_token": safe_token,
                "is_summary": False,
            },
            chunks=chunks,
        )
    except Exception as err:
        logger.warning("priority_pool_index_upsert_failed doc_id=%s err=%s", doc_id, err)


def ensure_priority_pool_pattern_index(
    *,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    pattern_index_token: str | None = None,
    samples: list[dict[str, Any]],
) -> None:
    normalized_samples = normalize_priority_samples(samples)
    _sync_priority_pool_pattern_index(
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id,
        pattern_index_token=pattern_index_token,
        samples=normalized_samples,
    )


def retrieve_priority_sample_patterns(
    *,
    project_id: int,
    user_id: int,
    query_text: str,
    generation_id: int | None = None,
    pattern_index_token: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    query = _sanitize_text(query_text, max_len=_MAX_EMBED_QUERY_CHARS)
    if not query:
        return []

    capped_top_k = max(1, min(int(top_k or 0), _MAX_RETRIEVAL_RESULTS))
    retrieval_candidates = min(_MAX_RETRIEVAL_CANDIDATES, max(capped_top_k, capped_top_k * 3))
    vector_store = get_vector_store()
    if not vector_store.is_ready():
        return []

    clauses: list[dict[str, Any]] = [
        {"project_id": int(project_id)},
        {"user_id": int(user_id)},
        {"doc_type": PRIORITY_SAMPLE_POOL_PATTERN_DOC_TYPE},
        {"is_summary": False},
    ]
    safe_token = _sanitize_text(pattern_index_token, max_len=48)
    if safe_token:
        clauses.append({"pattern_index_token": safe_token})
    elif generation_id is not None:
        clauses.append({"generation_id": int(generation_id)})
    where = {"$and": clauses}
    try:
        result = vector_store.search(
            query=query,
            n_results=retrieval_candidates,
            where=where,
            raise_on_error=True,
        )
    except Exception as err:
        logger.warning("priority_pool_pattern_search_failed project_id=%s user_id=%s err=%s", project_id, user_id, err)
        return []

    docs = (result.get("documents") or [[]])[0] if isinstance(result.get("documents"), list) else []
    metas = (result.get("metadatas") or [[]])[0] if isinstance(result.get("metadatas"), list) else []
    distances = (result.get("distances") or [[]])[0] if isinstance(result.get("distances"), list) else []

    matched: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    for idx, metadata in enumerate(metas):
        if not isinstance(metadata, dict):
            continue
        try:
            sample_index = int(metadata.get("sample_index"))
        except Exception:
            continue
        if sample_index in seen_indices:
            continue
        seen_indices.add(sample_index)
        try:
            pattern_weight = float(metadata.get("pattern_weight"))
        except Exception:
            pattern_weight = 0.6
        try:
            pattern_quality = float(metadata.get("pattern_quality_score"))
        except Exception:
            pattern_quality = 0.0
        rerank_score = (1.0 / (1.0 + idx)) * 0.8 + (max(0.3, min(1.8, pattern_weight)) / 1.8) * 0.2
        matched.append(
            {
                "sample_index": sample_index,
                "pattern_summary": _sanitize_text(metadata.get("pattern_summary"), max_len=_MAX_PATTERN_SUMMARY_LEN),
                "pattern_canonical": _sanitize_text(metadata.get("pattern_canonical"), max_len=_MAX_PATTERN_CANONICAL_LEN),
                "pattern_cluster_key": _sanitize_text(metadata.get("pattern_cluster_key"), max_len=_MAX_PATTERN_CLUSTER_LEN),
                "pattern_source": _sanitize_text(metadata.get("pattern_source"), max_len=20),
                "pattern_scope": _sanitize_text(metadata.get("pattern_scope"), max_len=40).lower() or "project",
                "pattern_grain": _sanitize_text(metadata.get("pattern_grain"), max_len=40).lower() or "case",
                "governance_status": _sanitize_text(metadata.get("governance_status"), max_len=16).lower() or "active",
                "signal_type": _normalize_signal_type(metadata.get("signal_type")),
                "pattern_usage": _normalize_pattern_usage(
                    metadata.get("pattern_usage"),
                    signal_type=_normalize_signal_type(metadata.get("signal_type")),
                ),
                "pattern_quality_score": round(max(0.0, min(1.0, pattern_quality)), 4),
                "pattern_weight": round(max(0.3, min(1.8, pattern_weight)), 4),
                "reason_category": _sanitize_text(metadata.get("reason_category"), max_len=40),
                "pattern_category": _normalize_pattern_category(metadata.get("pattern_category")),
                "expected_priority": _sanitize_text(metadata.get("expected_priority"), max_len=8),
                "case_id": _sanitize_text(metadata.get("case_id"), max_len=40),
                "sample_id": _sanitize_text(metadata.get("sample_id"), max_len=512),
                "source_case_id": _sanitize_text(metadata.get("source_case_id"), max_len=256),
                "source_case_title": _sanitize_text(metadata.get("source_case_title"), max_len=160),
                "source_case_module": _sanitize_text(metadata.get("source_case_module"), max_len=120),
                "document": _sanitize_text(docs[idx] if idx < len(docs) else "", max_len=220),
                "distance": float(distances[idx]) if idx < len(distances) else None,
                "retrieval_rank": int(idx + 1),
                "rerank_score": round(rerank_score, 6),
            }
        )
    matched.sort(
        key=lambda item: (
            float(item.get("rerank_score") or 0.0),
            float(item.get("pattern_weight") or 0.0),
            float(item.get("pattern_quality_score") or 0.0),
        ),
        reverse=True,
    )
    return matched[:capped_top_k]


def upsert_priority_sample_pool(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    samples: list[dict[str, Any]],
) -> KnowledgeDocument:
    repo = EvaluationArtifactRepository(db)
    filename = build_priority_sample_pool_filename(project_id)
    raw_samples = normalize_raw_priority_samples(samples, max_items=_MAX_POOL_SAMPLES)

    incoming_ids: set[str] = set()
    for s in raw_samples:
        sid = _sanitize_text(
            _sample_value(s, "sample_id", "sampleId"),
            max_len=512,
        )
        if sid:
            incoming_ids.add(sid)

    existing = load_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
        include_deleted=True,
    )
    if existing:
        existing_samples = existing.get("samples")
        if isinstance(existing_samples, list):
            for s in existing_samples:
                if not isinstance(s, dict):
                    continue
                if s.get("status") == "deleted":
                    existing_id = _sanitize_text(
                        _sample_value(s, "sample_id", "sampleId"),
                        max_len=512,
                    )
                    if existing_id and existing_id not in incoming_ids:
                        raw_samples.append(s)

    # Preserve existing learning events
    learning_events = existing.get("learning_events") if existing else None
    if not isinstance(learning_events, list):
        learning_events = []

    # Derive pattern layers from samples
    pattern_samples = select_priority_pattern_samples(raw_samples, max_items=_MAX_INDEXED_PATTERN_SAMPLES)
    patterns = derive_patterns_from_samples(pattern_samples)
    signals = derive_signals_from_patterns(patterns)
    manual_quality_profile = build_manual_quality_profile(
        raw_samples,
        project_id=project_id,
        user_id=user_id,
        existing_profile=existing.get("manual_quality_profile") if existing else None,
    )

    payload = {
        "project_id": int(project_id),
        "generation_id": int(generation_id) if generation_id is not None else None,
        "samples": raw_samples,
        "patterns": patterns,
        "signals": signals,
        "manual_quality_profile": manual_quality_profile,
        "learning_events": learning_events,
        "pattern_index_token": datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
        "updated_at": datetime.utcnow().isoformat(),
    }
    content = json.dumps(payload, ensure_ascii=False)

    doc = repo.get_latest_artifact_doc(
        project_id=project_id,
        user_id=user_id,
        doc_type=PRIORITY_SAMPLE_POOL_DOC_TYPE,
        filename=filename,
    )
    if doc:
        doc.content = content
        doc.parse_status = "success"
        doc.parse_error = None
        repo.commit()
        repo.refresh(doc)
        _sync_priority_pool_pattern_index(
            project_id=project_id,
            user_id=user_id,
            generation_id=generation_id,
            pattern_index_token=_sanitize_text(payload.get("pattern_index_token"), max_len=48),
            samples=pattern_samples,
            signals=signals,
        )
        # Shadow write to real tables
        _shadow_samples(db, project_id, user_id, raw_samples)
        _shadow_patterns(db, project_id, patterns)
        return doc

    doc = KnowledgeDocument(
        project_id=project_id,
        user_id=user_id,
        filename=filename,
        content=content,
        doc_type=PRIORITY_SAMPLE_POOL_DOC_TYPE,
        parse_status="success",
    )
    repo.add(doc)
    repo.commit()
    repo.refresh(doc)
    _sync_priority_pool_pattern_index(
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id,
        pattern_index_token=_sanitize_text(payload.get("pattern_index_token"), max_len=48),
        samples=pattern_samples,
        signals=signals,
    )
    # Shadow write to real tables
    _shadow_samples(db, project_id, user_id, raw_samples)
    _shadow_patterns(db, project_id, patterns)
    return doc


def remove_priority_sample_from_pool(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    sample_id: str,
    delete_reason: str = "",
) -> KnowledgeDocument | None:
    payload = load_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
    )
    if not payload:
        return None

    target_id = _sanitize_text(sample_id, max_len=512)
    if not target_id:
        return None

    current_samples = payload.get("samples")
    if not isinstance(current_samples, list):
        current_samples = []

    updated = False
    now_iso = datetime.utcnow().isoformat()
    safe_reason = _sanitize_text(delete_reason, max_len=256)
    for sample in current_samples:
        if not isinstance(sample, dict):
            continue
        current_id = _sanitize_text(
            _sample_value(sample, "sample_id", "sampleId"),
            max_len=512,
        )
        if current_id == target_id:
            sample["status"] = "deleted"
            sample["deleted_at"] = now_iso
            if safe_reason:
                sample["delete_reason"] = safe_reason
            updated = True
            break

    if not updated:
        return None

    return upsert_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id if generation_id is not None else payload.get("generation_id"),
        samples=current_samples,
    )


def add_samples_to_pool(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    incoming: list[dict[str, Any]],
) -> KnowledgeDocument | None:
    payload = load_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
    )
    current_samples: list[dict[str, Any]] = []
    existing_gen: int | None = None
    if payload:
        raw = payload.get("samples")
        if isinstance(raw, list):
            current_samples = list(raw)
        existing_gen = payload.get("generation_id")

    safe_incoming = incoming if isinstance(incoming, list) else []
    if not safe_incoming:
        return None

    normalized_incoming = normalize_raw_priority_samples(safe_incoming, max_items=_MAX_POOL_SAMPLES)

    existing_by_id: dict[str, dict[str, Any]] = {}
    for s in current_samples:
        sid = _sanitize_text(_sample_value(s, "sample_id", "sampleId"), max_len=512)
        if sid:
            existing_by_id[sid] = s

    for sample in normalized_incoming:
        sid = _sanitize_text(_sample_value(sample, "sample_id", "sampleId"), max_len=512)
        if not sid:
            continue
        existing_by_id[sid] = sample

    merged = list(existing_by_id.values())
    return upsert_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id if generation_id is not None else existing_gen,
        samples=merged,
    )


def update_priority_sample_in_pool(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    sample_id: str,
    patch: dict[str, Any],
) -> KnowledgeDocument | None:
    payload = load_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
    )
    if not payload:
        return None

    target_id = _sanitize_text(sample_id, max_len=512)
    if not target_id:
        return None

    current_samples = payload.get("samples")
    if not isinstance(current_samples, list):
        current_samples = []

    safe_patch = patch if isinstance(patch, dict) else {}
    allowed_keys = {
        "user_comment", "userComment",
        "expected_priority", "expectedPriority",
        "reason_category", "reasonCategory",
        "pattern_category", "patternCategory",
        "title",
        "pattern_summary", "patternSummary",
        "signal_type", "signalType",
        "pattern_usage", "patternUsage",
    }
    clean_patch = {}
    for key, val in safe_patch.items():
        if key in allowed_keys:
            clean_patch[key] = val

    if not clean_patch:
        return None

    updated = False
    for sample in current_samples:
        if not isinstance(sample, dict):
            continue
        current_id = _sanitize_text(
            _sample_value(sample, "sample_id", "sampleId"),
            max_len=512,
        )
        if current_id == target_id:
            sample.update(clean_patch)
            updated = True
            break

    if not updated:
        return None

    return upsert_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id if generation_id is not None else payload.get("generation_id"),
        samples=current_samples,
    )


def confirm_priority_sample_in_pool(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    sample_id: str,
    patch: dict[str, Any] | None = None,
) -> KnowledgeDocument | None:
    payload = load_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
    )
    if not payload:
        return None

    target_id = _sanitize_text(sample_id, max_len=512)
    if not target_id:
        return None

    current_samples = payload.get("samples")
    if not isinstance(current_samples, list):
        current_samples = []

    safe_patch = patch if isinstance(patch, dict) else {}
    allowed_patch_keys = {
        "user_comment", "userComment",
        "expected_priority", "expectedPriority",
        "reason_category", "reasonCategory",
        "pattern_category", "patternCategory",
        "title",
        "pattern_summary", "patternSummary",
        "signal_type", "signalType",
        "pattern_usage", "patternUsage",
    }
    clean_patch = {
        key: val
        for key, val in safe_patch.items()
        if key in allowed_patch_keys
    }

    updated = False
    now_ts = datetime.utcnow()
    for sample in current_samples:
        if not isinstance(sample, dict):
            continue
        current_id = _sanitize_text(
            _sample_value(sample, "sample_id", "sampleId"),
            max_len=512,
        )
        if current_id == target_id:
            if clean_patch:
                sample.update(clean_patch)
            sample["manual_confirmed"] = True
            sample["manualConfirmed"] = True
            sample["manual_confirmed_at"] = now_ts.isoformat()
            sample["manualConfirmedAt"] = int(now_ts.timestamp() * 1000)
            updated = True
            break

    if not updated:
        return None

    return upsert_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id if generation_id is not None else payload.get("generation_id"),
        samples=current_samples,
    )


def bulk_archive_priority_samples(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    generation_id: int | None,
    sample_ids: list[str],
    delete_reason: str = "",
) -> KnowledgeDocument | None:
    payload = load_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
    )
    if not payload:
        return None

    if not isinstance(sample_ids, list) or not sample_ids:
        return None

    current_samples = payload.get("samples")
    if not isinstance(current_samples, list):
        current_samples = []

    target_set: set[str] = set()
    for raw_id in sample_ids:
        cleaned = _sanitize_text(raw_id, max_len=512)
        if cleaned:
            target_set.add(cleaned)

    if not target_set:
        return None

    now_iso = datetime.utcnow().isoformat()
    safe_reason = _sanitize_text(delete_reason, max_len=256)
    archived_count = 0
    for sample in current_samples:
        if not isinstance(sample, dict):
            continue
        current_id = _sanitize_text(
            _sample_value(sample, "sample_id", "sampleId"),
            max_len=512,
        )
        if current_id in target_set:
            sample["status"] = "deleted"
            sample["deleted_at"] = now_iso
            if safe_reason:
                sample["delete_reason"] = safe_reason
            archived_count += 1

    if archived_count == 0:
        return None

    return upsert_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
        generation_id=generation_id if generation_id is not None else payload.get("generation_id"),
        samples=current_samples,
    )


def append_learning_event(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    event_type: str,
    event_payload: dict[str, Any],
) -> KnowledgeDocument | None:
    payload = load_priority_sample_pool(
        db=db,
        project_id=project_id,
        user_id=user_id,
        include_deleted=True,
    ) or {
        "project_id": project_id,
        "generation_id": None,
        "samples": [],
        "learning_events": [],
    }
    events = payload.get("learning_events")
    if not isinstance(events, list):
        events = []
    safe_type = _sanitize_text(event_type, max_len=64)
    event = {
        "event_type": safe_type,
        "created_at": datetime.utcnow().isoformat(),
        "created_by": int(user_id),
        **event_payload,
    }
    events.append(event)
    # Keep at most 200 events
    if len(events) > 200:
        events = events[-200:]

    existing_samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
    samples_for_upsert = list(existing_samples)
    merged_payload = {
        "learning_events": events,
    }
    # Rebuild the artifact via upsert, attaching the learning_events in the JSON payload.
    repo = EvaluationArtifactRepository(db)
    filename = build_priority_sample_pool_filename(project_id)
    normalized = normalize_raw_priority_samples(samples_for_upsert, max_items=_MAX_POOL_SAMPLES)
    pattern_samples = select_priority_pattern_samples(normalized, max_items=_MAX_INDEXED_PATTERN_SAMPLES)
    patterns = derive_patterns_from_samples(pattern_samples)
    signals = derive_signals_from_patterns(patterns)
    content = json.dumps(
        {
            "project_id": int(project_id),
            "generation_id": payload.get("generation_id"),
            "samples": normalized,
            "patterns": patterns,
            "signals": signals,
            "learning_events": events,
            "pattern_index_token": datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
            "updated_at": datetime.utcnow().isoformat(),
        },
        ensure_ascii=False,
    )
    doc = repo.get_latest_artifact_doc(
        project_id=project_id,
        user_id=user_id,
        doc_type=PRIORITY_SAMPLE_POOL_DOC_TYPE,
        filename=filename,
    )
    if doc:
        doc.content = content
        doc.parse_status = "success"
        doc.parse_error = None
        repo.commit()
        repo.refresh(doc)
        _shadow_event(db, project_id, user_id, safe_type, event_payload)
        return doc
    doc = KnowledgeDocument(
        project_id=project_id,
        user_id=user_id,
        filename=filename,
        content=content,
        doc_type=PRIORITY_SAMPLE_POOL_DOC_TYPE,
        parse_status="success",
    )
    repo.add(doc)
    repo.commit()
    repo.refresh(doc)
    _shadow_event(db, project_id, user_id, safe_type, event_payload)
    return doc


def load_priority_sample_pool(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    include_deleted: bool = False,
) -> Optional[dict[str, Any]]:
    if db is None or not hasattr(db, "query"):
        return None
    repo = EvaluationArtifactRepository(db)
    filename = build_priority_sample_pool_filename(project_id)
    doc = repo.get_latest_artifact_doc(
        project_id=project_id,
        user_id=user_id,
        doc_type=PRIORITY_SAMPLE_POOL_DOC_TYPE,
        filename=filename,
    )
    if not doc:
        return None
    try:
        payload = json.loads(doc.content or "{}")
        if not isinstance(payload, dict):
            return None
        samples = payload.get("samples")
        if isinstance(samples, list):
            normalized = normalize_raw_priority_samples(samples, max_items=_MAX_POOL_SAMPLES)
            if not include_deleted:
                normalized = [s for s in normalized if s.get("status") != "deleted"]
            payload["samples"] = normalized
            payload["manual_quality_profile"] = build_manual_quality_profile(
                normalized,
                project_id=project_id,
                user_id=user_id,
                existing_profile=payload.get("manual_quality_profile"),
            )
        payload["artifact_doc_id"] = doc.id
        payload["artifact_filename"] = doc.filename
        events = payload.get("learning_events")
        if isinstance(events, list):
            payload["learning_events"] = events
        else:
            payload["learning_events"] = []
        patterns = payload.get("patterns")
        payload["patterns"] = patterns if isinstance(patterns, list) else []
        signals_data = payload.get("signals")
        payload["signals"] = signals_data if isinstance(signals_data, list) else []
        return payload
    except Exception:
        return None
