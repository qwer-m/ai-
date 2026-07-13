"""Build quality-evaluation learning candidates for final-case learning."""

from __future__ import annotations

import json
import re
from typing import Any

from ..postprocess.case_access import case_text_list_value
from .final_case_parsing import _text
from .final_case_evaluation_quality import (
    _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY,
    _candidate_has_sample_shape,
    _evaluation_learning_candidate_quality_gate,
    _filter_quality_evaluation_sample_for_apply,
)

_MAX_EVALUATION_LEARNING_CANDIDATES = 80
_MAX_EVALUATION_POSITIVE_CANDIDATES_PER_FIELD = 8
_MAX_EVALUATION_FIX_CANDIDATES_PER_FIELD = 6
_MAX_EVALUATION_NEGATIVE_CANDIDATES_PER_FIELD = 3

def parse_evaluation_result_payload(raw: Any) -> dict[str, Any]:
    """Parse the quality-evaluation report into a dict.

    The evaluation endpoint may return plain JSON, markdown fenced JSON, or an
    already-parsed object. Keep this parser local so both API and tests share
    the same tolerance as the frontend report renderer.
    """
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if block:
        text = block.group(1).strip()
    first_open = text.find("{")
    last_close = text.rfind("}")
    if first_open >= 0 and last_close > first_open:
        text = text[first_open : last_close + 1]
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_learning_candidates_from_evaluation_result(evaluation_result: Any) -> dict[str, Any]:
    """Convert quality-evaluation defects into user-confirmable learning candidates.

    This does not write anything. The candidate contains the exact sample that
    will later be inserted into the existing priority sample pool if the user
    confirms it.
    """
    payload = parse_evaluation_result_payload(evaluation_result)
    defect = payload.get("defect_analysis") if isinstance(payload.get("defect_analysis"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}

    candidates: list[dict[str, Any]] = []
    raw_candidate_count = 0
    rejected_candidates: list[dict[str, Any]] = []

    def add_candidate(
        *,
        source_field: str,
        item: Any,
        index: int,
        signal_type: str,
        pattern_usage: str,
        pattern_category: str,
        reason_category: str,
        candidate_type: str,
        selected_by_default: bool,
        confidence: float,
    ) -> None:
        nonlocal raw_candidate_count
        raw_candidate_count += 1
        text = _text(item)
        if not text:
            return
        candidate_id = f"{source_field}-{index}"
        gate = _evaluation_learning_candidate_quality_gate(
            text=text,
            source_field=source_field,
            candidate_type=candidate_type,
            signal_type=signal_type,
        )
        if gate["status"] == "rejected":
            rejected_candidates.append(
                {
                    "id": candidate_id,
                    "source_field": source_field,
                    "reason": gate["reason"],
                    "text": text[:160],
                }
            )
            return
        effective_selected = bool(selected_by_default and gate["status"] == "auto_select")
        sample = {
            "signal_type": signal_type,
            "pattern_usage": pattern_usage,
            "pattern_category": pattern_category,
            "reason_category": reason_category,
            "expected_priority": "P1" if signal_type == "positive" else "P2",
            "case_id": candidate_id,
            "title": text[:120],
            "user_comment": text[:240],
            "pattern_summary": _summarize_evaluation_defect_pattern(
                text=text,
                signal_type=signal_type,
                pattern_category=pattern_category,
            ),
            "pattern_grain": "pattern" if signal_type == "positive" else "anti_pattern",
            "source": "quality_evaluation_defect",
            "source_type": "quality_evaluation_defect",
            "source_id": None,
            "source_case_id": str(candidate_id),
            "learning_signal_source": f"defect_analysis.{source_field}",
            "pattern_scope": "project",
            "pattern_confidence": round(max(0.35, min(0.9, confidence)), 4),
            "evaluation_metrics": _compact_evaluation_metrics(metrics),
            "quality_gate_status": gate["status"],
            "quality_gate_reason": gate["reason"],
            "quality_gate_policy": _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY,
        }
        candidates.append(
            {
                "id": candidate_id,
                "candidate_type": candidate_type,
                "target": "priority_sample_pool",
                "source_field": source_field,
                "text": text,
                "selected_by_default": effective_selected,
                "confidence": sample["pattern_confidence"],
                "quality_gate_status": gate["status"],
                "quality_gate_reason": gate["reason"],
                "sample": sample,
            }
        )

    for idx, item in enumerate(_as_text_list(defect.get("missing_points")), start=1):
        generated_only = _is_generated_only_evaluation_defect(item)
        redundant_or_overgenerated = _is_redundant_or_overgenerated_evaluation_defect(item)
        if generated_only or redundant_or_overgenerated:
            add_candidate(
                source_field="missing_points",
                item=item,
                index=idx,
                signal_type="negative",
                pattern_usage="avoid",
                pattern_category="hallucination_or_redundant_case",
                reason_category=(
                    "generated_only_defect_misfiled_as_missing"
                    if generated_only
                    else "redundant_defect_misfiled_as_missing"
                ),
                candidate_type="negative_pattern",
                selected_by_default=False,
                confidence=_confidence_from_metrics(metrics, base=0.62, metric_name="precision", inverse=True),
            )
            continue
        add_candidate(
            source_field="missing_points",
            item=item,
            index=idx,
            signal_type="positive",
            pattern_usage="prefer",
            pattern_category="recall_gap_missing_business_coverage",
            reason_category="recall_gap",
            candidate_type="positive_pattern",
            selected_by_default=True,
            confidence=_confidence_from_metrics(metrics, base=0.72, metric_name="recall", inverse=True),
        )
    for idx, item in enumerate(_as_text_list(defect.get("modifications")), start=1):
        generated_only = _is_generated_only_evaluation_defect(item)
        redundant_or_overgenerated = _is_redundant_or_overgenerated_evaluation_defect(item)
        if generated_only or redundant_or_overgenerated:
            add_candidate(
                source_field="modifications",
                item=item,
                index=idx,
                signal_type="negative",
                pattern_usage="avoid",
                pattern_category="hallucination_or_redundant_case",
                reason_category=(
                    "generated_only_defect_misfiled_as_modification"
                    if generated_only
                    else "redundant_defect_misfiled_as_modification"
                ),
                candidate_type="negative_pattern",
                selected_by_default=False,
                confidence=_confidence_from_metrics(metrics, base=0.62, metric_name="precision", inverse=True),
            )
            continue
        add_candidate(
            source_field="modifications",
            item=item,
            index=idx,
            signal_type="positive",
            pattern_usage="prefer",
            pattern_category="quality_fix_hint",
            reason_category="quality_fix_hint",
            candidate_type="quality_fix_hint",
            selected_by_default=True,
            confidence=_confidence_from_metrics(metrics, base=0.68, metric_name="semantic_similarity", inverse=False),
        )
    for idx, item in enumerate(_as_text_list(defect.get("hallucinations")), start=1):
        add_candidate(
            source_field="hallucinations",
            item=item,
            index=idx,
            signal_type="negative",
            pattern_usage="avoid",
            pattern_category="hallucination_or_redundant_case",
            reason_category="hallucination_or_redundant_case",
            candidate_type="negative_pattern",
            selected_by_default=False,
            confidence=_confidence_from_metrics(metrics, base=0.6, metric_name="precision", inverse=True),
        )

    candidates = _aggregate_evaluation_learning_candidates(candidates)
    candidates = candidates[:_MAX_EVALUATION_LEARNING_CANDIDATES]
    return {
        "candidates": candidates,
        "diagnostics": {
            "raw_candidate_count": raw_candidate_count,
            "quality_gate_rejected_count": len(rejected_candidates),
            "quality_gate_review_required_count": sum(
                1 for item in candidates if item.get("quality_gate_status") == "review_required"
            ),
            "quality_gate_rejected_examples": rejected_candidates[:8],
            "candidate_count": len(candidates),
            "selected_by_default_count": sum(1 for item in candidates if item.get("selected_by_default") is True),
            "missing_points_count": len(_as_text_list(defect.get("missing_points"))),
            "modifications_count": len(_as_text_list(defect.get("modifications"))),
            "hallucinations_count": len(_as_text_list(defect.get("hallucinations"))),
            "candidate_aggregation_policy": (
                "defect_field_semantic_bucket_positive8_fix6_negative3_generated_redundant_quality_gate"
            ),
            "candidate_quality_policy": _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY,
            "target": "priority_sample_pool",
            "write_policy": "user_confirmed_only",
        },
    }

def _as_text_list(raw: Any) -> list[str]:
    return [text for text in (_text(item) for item in case_text_list_value(raw)) if text]


def _compact_evaluation_metrics(metrics: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(metrics, dict):
        return {}
    result: dict[str, float] = {}
    for key in ("precision", "recall", "f1_score", "semantic_similarity"):
        try:
            result[key] = round(float(metrics.get(key)), 4)
        except Exception:
            continue
    return result


def _confidence_from_metrics(
    metrics: dict[str, Any] | None,
    *,
    base: float,
    metric_name: str,
    inverse: bool,
) -> float:
    value = None
    if isinstance(metrics, dict):
        try:
            value = float(metrics.get(metric_name))
        except Exception:
            value = None
    if value is None:
        return round(base, 4)
    value = max(0.0, min(1.0, value))
    if inverse:
        return round(base + ((1.0 - value) * 0.12), 4)
    return round(base + (value * 0.08), 4)


def _summarize_evaluation_defect_pattern(
    *,
    text: str,
    signal_type: str,
    pattern_category: str,
) -> str:
    prefix = "prefer" if signal_type == "positive" else "avoid"
    return f"{prefix} | {pattern_category} | {_text(text)[:140]}"[:180]


def _is_generated_only_evaluation_defect(raw: Any) -> bool:
    text = _text(raw)
    if not text:
        return False
    generated_side_tokens = (
        "生成用例",
        "原生成",
        "AI 生成",
        "AI生成",
        "generated",
    )
    final_absent_tokens = (
        "修改用例未涉及",
        "修改用例未覆盖",
        "修改用例不存在",
        "修改用例完全不存在",
        "修改版本未涉及",
        "修改版本未覆盖",
        "修改版本中未体现",
        "修改后未涉及",
        "在修改用例中不存在",
        "在修改用例中未体现",
        "modified version does not",
        "absent from modified",
    )
    generated_excess_tokens = (
        "生成用例包含大量",
        "生成用例中新增了大量",
        "生成用例新增了大量",
        "生成用例额外包含",
        "generated contains many",
        "generated adds many",
    )
    lowered = text.lower()
    has_generated_side = any(token.lower() in lowered for token in generated_side_tokens)
    if not has_generated_side:
        return False
    return any(token.lower() in lowered for token in final_absent_tokens + generated_excess_tokens)


def _is_redundant_or_overgenerated_evaluation_defect(raw: Any) -> bool:
    text = _text(raw)
    if not text:
        return False
    lowered = text.lower()
    generated_tokens = ("ai", "生成", "generated")
    redundant_tokens = (
        "duplicate_redundant",
        "重复",
        "冗余",
        "合并",
        "过多",
        "大量",
        "多个",
        "未被人工采用",
        "not adopted",
        "redundant",
        "duplicate",
        "merged",
    )
    return any(token in lowered for token in generated_tokens) and any(
        token in lowered for token in redundant_tokens
    )


def _aggregate_evaluation_learning_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = _evaluation_candidate_key(candidate)
        buckets.setdefault(key, []).append(candidate)

    selected: list[dict[str, Any]] = []
    field_counts: dict[str, int] = {}
    for _key, bucket in sorted(buckets.items(), key=lambda item: _evaluation_candidate_bucket_rank(item[1])):
        candidate = _merge_evaluation_candidate_bucket(bucket)
        source_field = str(candidate.get("source_field") or "")
        limit = _evaluation_candidate_field_limit(source_field, candidate)
        if field_counts.get(source_field, 0) >= limit:
            continue
        field_counts[source_field] = field_counts.get(source_field, 0) + 1
        selected.append(candidate)
    return selected


def _evaluation_candidate_key(candidate: dict[str, Any]) -> str:
    source_field = str(candidate.get("source_field") or "")
    candidate_type = str(candidate.get("candidate_type") or "")
    text = _text(candidate.get("text"))
    return "|".join(
        [
            source_field,
            candidate_type,
            _semantic_bucket_for_learning_text(text),
        ]
    )


def _semantic_bucket_for_learning_text(text: str) -> str:
    normalized = text.lower()
    token_groups = [
        ("schedule_time", ("排课", "课程时间", "时间区间", "顺延", "课程延期", "节假日", "时间冲突", "schedule")),
        ("learning_plan", ("学习计划", "计划页", "卡片", "周列表", "学习中", "复习", "计划")),
        ("course_status", ("课程状态", "已完成", "未完成", "进度", "归档", "下架", "状态")),
        ("navigation_flow", ("跳转", "进入", "返回", "下一步", "页面流转", "入口")),
        ("teacher_admin", ("督导", "老师", "书房", "中房端", "后台", "管理端", "ta", "ops")),
        ("ui_copy", ("文案", "提示", "按钮", "标题", "标签", "弹窗", "显示")),
        ("duplicate_redundant", ("重复", "相似", "合并", "大量", "冗余")),
        ("buried_point", ("埋点", "pv", "uv", "上报")),
    ]
    for name, tokens in token_groups:
        if any(token in normalized for token in tokens):
            return name
    compact = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text.lower())
    return compact[:24] or "general"


def _evaluation_candidate_field_limit(source_field: str, candidate: dict[str, Any]) -> int:
    if source_field == "hallucinations" or str(candidate.get("candidate_type") or "") == "negative_pattern":
        return _MAX_EVALUATION_NEGATIVE_CANDIDATES_PER_FIELD
    if source_field == "modifications":
        return _MAX_EVALUATION_FIX_CANDIDATES_PER_FIELD
    return _MAX_EVALUATION_POSITIVE_CANDIDATES_PER_FIELD


def _evaluation_candidate_bucket_rank(bucket: list[dict[str, Any]]) -> tuple[int, float, str]:
    first = bucket[0] if bucket else {}
    candidate_type = str(first.get("candidate_type") or "")
    type_rank = {
        "positive_pattern": 0,
        "quality_fix_hint": 1,
        "negative_pattern": 2,
    }.get(candidate_type, 3)
    confidence = float(first.get("confidence") or 0.0)
    return (type_rank, -confidence, _evaluation_candidate_key(first))


def _merge_evaluation_candidate_bucket(bucket: list[dict[str, Any]]) -> dict[str, Any]:
    if not bucket:
        return {}
    base = dict(bucket[0])
    if len(bucket) <= 1:
        return base
    texts = [_text(item.get("text")) for item in bucket if _text(item.get("text"))]
    summary = _summarize_candidate_texts(texts)
    selected_by_default = any(item.get("selected_by_default") is True for item in bucket)
    gate_statuses = {str(item.get("quality_gate_status") or "") for item in bucket}
    gate_status = "auto_select" if selected_by_default else "review_required"
    if "auto_select" not in gate_statuses and "review_required" in gate_statuses:
        gate_status = "review_required"
    base["text"] = summary
    base["id"] = f"{base.get('source_field')}-{_semantic_bucket_for_learning_text(summary)}"
    base["confidence"] = round(max(float(item.get("confidence") or 0.0) for item in bucket), 4)
    base["selected_by_default"] = selected_by_default
    base["quality_gate_status"] = gate_status
    base["quality_gate_reason"] = "aggregated_reusable_evidence" if selected_by_default else "aggregated_review_required"
    sample = dict(base.get("sample") or {})
    sample["case_id"] = str(base["id"])
    sample["title"] = summary[:120]
    sample["user_comment"] = summary[:240]
    sample["pattern_summary"] = _summarize_evaluation_defect_pattern(
        text=summary,
        signal_type=str(sample.get("signal_type") or "positive"),
        pattern_category=str(sample.get("pattern_category") or base.get("candidate_type") or "evaluation_defect"),
    )
    sample["pattern_confidence"] = base["confidence"]
    sample["aggregated_evidence_count"] = len(bucket)
    sample["aggregated_evidence_examples"] = texts[:5]
    sample["quality_gate_status"] = gate_status
    sample["quality_gate_reason"] = str(base["quality_gate_reason"])
    sample["quality_gate_policy"] = _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY
    base["sample"] = sample
    base["aggregated_count"] = len(bucket)
    return base


def _summarize_candidate_texts(texts: list[str]) -> str:
    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0]
    first = texts[0]
    bucket = _semantic_bucket_for_learning_text(first)
    examples = "；".join(text[:60] for text in texts[:3])
    return f"{bucket} 类问题聚合：{len(texts)} 条相似缺陷，代表例：{examples}"[:240]



