"""Quality gates for evaluation-derived final-case learning samples."""

from __future__ import annotations

import re
from typing import Any

from .final_case_parsing import _text

_EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY = "evaluation_defect_reusable_pattern_v1"


def _evaluation_learning_candidate_quality_gate(
    *,
    text: str,
    source_field: str,
    candidate_type: str,
    signal_type: str,
) -> dict[str, str]:
    normalized = _text(text)
    if not normalized:
        return {"status": "rejected", "reason": "empty_text"}

    compact_len = len(re.sub(r"\s+", "", normalized))
    has_case_id = _has_case_identifier(normalized)
    if _is_case_identifier_only_learning_text(normalized):
        return {"status": "rejected", "reason": "case_identifier_label_only"}
    if _is_direct_case_rewrite_note(normalized):
        return {"status": "rejected", "reason": "case_identifier_rewrite_note"}

    context_score = _learning_candidate_context_score(normalized)
    has_defect_or_compare_signal = _has_evaluation_defect_or_compare_signal(normalized)
    has_final_side_anchor = _has_final_side_learning_anchor(normalized)
    has_generated_side_anchor = _has_generated_side_learning_anchor(normalized)
    is_negative = signal_type == "negative" or candidate_type == "negative_pattern" or source_field == "hallucinations"

    if is_negative:
        if has_case_id and context_score < 8:
            return {"status": "rejected", "reason": "case_identifier_without_negative_context"}
        if compact_len < 12 or (context_score < 6 and not has_defect_or_compare_signal):
            return {"status": "rejected", "reason": "low_context_negative_pattern"}
        return {"status": "review_required", "reason": "negative_patterns_require_confirmation"}

    if _is_process_count_learning_note(normalized):
        return {"status": "rejected", "reason": "process_count_note_not_reusable_pattern"}
    if compact_len < 18 and not has_final_side_anchor:
        return {"status": "rejected", "reason": "low_context_positive_pattern"}
    if context_score < 8 and not has_final_side_anchor:
        return {"status": "rejected", "reason": "not_enough_reusable_context"}
    if not has_defect_or_compare_signal and compact_len < 24:
        return {"status": "rejected", "reason": "missing_defect_or_comparison_signal"}
    if _is_ai_to_human_process_note(normalized):
        return {"status": "review_required", "reason": "ai_human_process_note_requires_review"}
    if has_final_side_anchor and not has_generated_side_anchor and compact_len < 24:
        return {"status": "review_required", "reason": "compact_final_side_pattern_requires_review"}
    if not has_final_side_anchor and compact_len < 24:
        return {"status": "review_required", "reason": "compact_positive_pattern_requires_review"}
    return {"status": "auto_select", "reason": "reusable_evaluation_pattern"}


def _has_case_identifier(text: str) -> bool:
    return bool(re.search(r"(?:TC|CASE)[-\s]?\d+", text, flags=re.IGNORECASE))


def _strip_case_identifiers(text: str) -> str:
    return re.sub(r"(?:TC|CASE)[-\s]?\d+", "", text, flags=re.IGNORECASE)


def _is_case_identifier_only_learning_text(text: str) -> bool:
    without_ids = _strip_case_identifiers(text)
    without_ids = re.sub(r"\bAI\b", "", without_ids, flags=re.IGNORECASE)
    without_ids = re.sub(r"group\d+", "", without_ids, flags=re.IGNORECASE)
    without_ids = re.sub(
        r"(修正|修改|调整|对应|匹配|判断|逻辑|功能|验证|测试|题目|场景|页面|模块|用例|类问题聚合|代表例|合并|和|在|中|；|;|:|：|、|，|,|\s)+",
        "",
        without_ids,
    )
    return _has_case_identifier(text) and len(without_ids) < 8


def _is_direct_case_rewrite_note(text: str) -> bool:
    return bool(
        re.match(
            r"^\s*(?:TC|CASE)[-\s]?\d+\s*(?:修正|修改|调整为|对应|合并到?)\s*(?:TC|CASE)[-\s]?\d+\s*$",
            text,
            flags=re.IGNORECASE,
        )
    )


def _is_process_count_learning_note(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(
        re.search(r"AI生成\d+个用例.*人工(?:修改|合并|拆分)为\d+个", compact, flags=re.IGNORECASE)
        or re.search(r"人工将多个AI用例(?:合并|修改)为", compact, flags=re.IGNORECASE)
    )


def _is_ai_to_human_process_note(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(
        re.search(r"AI(?:的|用例|生成)?.{0,16}人工(?:修改|补充|拆分|修正)", compact, flags=re.IGNORECASE)
        or re.search(r"人工(?:修改|补充|拆分|修正).{0,16}AI", compact, flags=re.IGNORECASE)
    )


def _has_final_side_learning_anchor(text: str) -> bool:
    lowered = text.lower()
    tokens = (
        "修改版",
        "修改用例",
        "人工版",
        "人工用例",
        "人工最终",
        "最终用例",
        "modified",
        "human",
        "final case",
    )
    return any(token in lowered for token in tokens)


def _has_generated_side_learning_anchor(text: str) -> bool:
    lowered = text.lower()
    tokens = (
        "生成版",
        "生成用例",
        "原生成",
        "ai",
        "generated",
    )
    return any(token in lowered for token in tokens)


def _has_evaluation_defect_or_compare_signal(text: str) -> bool:
    lowered = text.lower()
    tokens = (
        "缺失",
        "缺少",
        "遗漏",
        "未包含",
        "未覆盖",
        "未涉及",
        "未提及",
        "无对应",
        "不完全对应",
        "无关",
        "多余",
        "冗余",
        "重复",
        "合并",
        "修正",
        "修改",
        "补充",
        "变更",
        "改为",
        "更具体",
        "过于笼统",
        "缺乏",
        "missing",
        "lacks",
        "lack",
        "should",
        "assert",
        "not generic",
        "unrelated",
        "redundant",
        "duplicate",
        "modified",
        "generated",
    )
    return any(token in lowered for token in tokens)


def _learning_candidate_context_score(text: str) -> int:
    cleaned = _strip_case_identifiers(text)
    cleaned = re.sub(r"\b(?:AI|TC|CASE|generated|modified|human|final|case|expected|result|should|assert)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"(生成版|修改版|人工|生成|用例|测试|验证|缺失|缺少|遗漏|未包含|未覆盖|未涉及|未提及|无对应|修正|修改|补充|变更|改为|更具体|过于笼统|缺乏|功能|场景|逻辑|条件|具体|精确|页面|模块|类问题聚合|代表例|个|为|从)",
        "",
        cleaned,
    )
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", cleaned)
    english_words = re.findall(r"[A-Za-z]{3,}", cleaned)
    return len(chinese_chars) + len(english_words) * 4


def _filter_quality_evaluation_sample_for_apply(sample: dict[str, Any]) -> dict[str, Any] | None:
    source_type = str(sample.get("source_type") or sample.get("source") or "")
    if source_type != "quality_evaluation_defect":
        return sample
    text = _text(sample.get("user_comment") or sample.get("title") or sample.get("pattern_summary"))
    signal_type = str(sample.get("signal_type") or sample.get("sample_kind") or "")
    pattern_category = str(sample.get("pattern_category") or "")
    candidate_type = "negative_pattern" if signal_type == "negative" else "positive_pattern"
    if pattern_category == "quality_fix_hint":
        candidate_type = "quality_fix_hint"
    source_field = str(sample.get("learning_signal_source") or "")
    if "." in source_field:
        source_field = source_field.rsplit(".", 1)[-1]
    if not source_field:
        source_field = "hallucinations" if signal_type == "negative" else "missing_points"
    gate = _evaluation_learning_candidate_quality_gate(
        text=text,
        source_field=source_field,
        candidate_type=candidate_type,
        signal_type=signal_type,
    )
    if gate["status"] == "rejected":
        return None
    result = dict(sample)
    result.setdefault("quality_gate_status", gate["status"])
    result.setdefault("quality_gate_reason", gate["reason"])
    result.setdefault("quality_gate_policy", _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY)
    return result


def _candidate_has_sample_shape(candidate: dict[str, Any]) -> bool:
    if not isinstance(candidate, dict):
        return False
    return bool(candidate.get("signal_type") and candidate.get("pattern_usage") and candidate.get("pattern_summary"))
