from __future__ import annotations

from typing import Any

from ..postprocess.case_access import case_flat_text

from .judge_fact_negative import (
    _MIN_NEGATIVE_FACT_TAIL_CHARS as _MIN_NEGATIVE_FACT_TAIL_CHARS,
    _NEGATED_TAIL_CONTEXT_MARKERS as _NEGATED_TAIL_CONTEXT_MARKERS,
    _NEGATIVE_MARKERS as _NEGATIVE_MARKERS,
    _TEMPORAL_SHUTDOWN_BLOCK_MARKERS as _TEMPORAL_SHUTDOWN_BLOCK_MARKERS,
    _TEMPORAL_SHUTDOWN_POSITIVE_MARKERS as _TEMPORAL_SHUTDOWN_POSITIVE_MARKERS,
    _TEMPORAL_SHUTDOWN_SCOPE_MARKERS as _TEMPORAL_SHUTDOWN_SCOPE_MARKERS,
    _case_negates_tail as _case_negates_tail,
    _contains_raw_marker as _contains_raw_marker,
    _is_temporal_shutdown_fact as _is_temporal_shutdown_fact,
    _negative_fact_marker_pattern as _negative_fact_marker_pattern,
    _split_negative_fact_tail as _split_negative_fact_tail,
    _violates_negative_fact as _violates_negative_fact,
    _violates_temporal_shutdown_fact as _violates_temporal_shutdown_fact,
)
from .judge_fact_semantics import (
    _merge_fact_profile_semantics as _merge_fact_profile_semantics,
    normalize_requirement_semantics_context as normalize_requirement_semantics_context,
)
from .judge_text_utils import (
    _dedupe_texts as _dedupe_texts,
    _normalize_text as _normalize_text,
)


_PENDING_HINTS = (
    "pending",
    "tbd",
    "todo",
    "to be confirmed",
    "待确认",
    "待澄清",
    "待定",
    "未确认",
    "暂未确定",
)

_VAGUE_UNCONFIRMED_HINTS = (
    "或需求定义",
    "需求定义的文案",
    "根据实际",
    "根据产品",
    "实际产品设计",
    "实际设计",
    "需确认",
    "待确认",
    "暂不确定",
    "或类似文案",
    "或类似格式",
    "符合描述格式",
    "对应空状态文案",
    "或其他已明确",
    "已明确的设计色值",
    "持平或",
    "以实际",
    "如果设计如此",
    "若无",
    "可设计为",
    "可能",
    "需求未细说",
    "未细说",
    "无补充说明",
    "或当日",
    "不变或增加",
    "应跳转到目标页面",
    "页面路径与标题",
    "响应状态码正确",
    "授权范围内页面或模块",
    "应完整显示",
    "关键字段",
    "字段值与输入",
    "后端数据一致",
    "to be confirmed",
    "as designed",
    "depends on actual",
    "depending on actual",
    "if designed",
    "requirement-defined",
    "per actual design",
)

def _contains_pending_logic(case_text: str, pending_items: list[str]) -> tuple[bool, list[str]]:
    normalized_case = _normalize_text(case_text)
    hits: list[str] = []
    for hint in _PENDING_HINTS:
        if _normalize_text(hint) and _normalize_text(hint) in normalized_case:
            hits.append(hint)
    for item in pending_items:
        marker = _normalize_text(item)
        if marker and marker in normalized_case:
            hits.append(item)
    deduped = _dedupe_texts(hits)
    return bool(deduped), deduped


def _contains_vague_unconfirmed_logic(case: dict[str, Any]) -> tuple[bool, list[str]]:
    targeted_text = case_flat_text(case, fields=("description", "test_input", "expected_result"), separator=" ")
    normalized_case = _normalize_text(targeted_text)
    hits: list[str] = []
    for hint in _VAGUE_UNCONFIRMED_HINTS:
        marker = _normalize_text(hint)
        if marker and marker in normalized_case:
            hits.append(hint)
    return bool(hits), _dedupe_texts(hits)


def _extract_sequence_candidates(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if "->" in text:
        candidates = [segment.strip() for segment in text.split("->")]
    elif "→" in text:
        candidates = [segment.strip() for segment in text.split("→")]
    elif "=>" in text:
        candidates = [segment.strip() for segment in text.split("=>")]
    else:
        candidates = []
    tokens = [token for token in candidates if len(_normalize_text(token)) >= 2]
    return tokens


def _violates_flow_order(case_text: str, flow_constraint: str) -> bool:
    ordered_tokens = _extract_sequence_candidates(flow_constraint)
    if len(ordered_tokens) < 2:
        return False
    lowered_case = str(case_text or "").lower()
    positions: list[int] = []
    for token in ordered_tokens:
        idx = lowered_case.find(str(token).lower())
        if idx < 0:
            return False
        positions.append(idx)
    return positions != sorted(positions)


def _is_time_window_scope_rule(rule_text: str) -> bool:
    lowered = str(rule_text or "").strip().lower()
    if not lowered:
        return False
    time_markers = (
        "周日24:00",
        "周日 24:00",
        "24:00后",
        "24:00 后",
        "24点后",
        "24 点后",
        "sunday 24:00",
        "after sunday 24:00",
    )
    view_only_markers = (
        "仅可查看",
        "只可查看",
        "仅查看",
        "不可操作",
        "不能操作",
        "禁止操作",
        "view only",
        "read only",
        "read-only",
        "readonly",
    )
    return bool(
        any(marker in lowered for marker in time_markers)
        and any(marker in lowered for marker in view_only_markers)
    )


def _matches_time_window_scope(case_text: str) -> bool:
    lowered = str(case_text or "").strip().lower()
    if not lowered:
        return False
    scope_markers = (
        "历史周",
        "补做",
        "补学",
        "历史任务",
        "历史周任务",
        "历史周补学",
        "周末任务",
        "补做期",
    )
    time_markers = (
        "周日24:00",
        "周日 24:00",
        "周日24点",
        "周日 24点",
        "24:00后",
        "24:00 后",
        "24点后",
        "24 点后",
        "截止后",
        "过期后",
        "sunday 24:00",
        "after sunday",
        "after deadline",
    )
    return bool(
        any(marker in lowered for marker in scope_markers)
        and any(marker in lowered for marker in time_markers)
    )


def _is_before_deadline_context(case_text: str) -> bool:
    lowered = str(case_text or "").strip().lower()
    if not lowered:
        return False
    before_markers = (
        "周日24:00前",
        "周日 24:00前",
        "周日24点前",
        "周日 24点前",
        "24:00前",
        "24:00 前",
        "24点前",
        "24 点前",
        "截止前",
        "过期前",
        "未过期",
        "before sunday 24:00",
        "before deadline",
    )
    after_markers = (
        "周日24:00后",
        "周日 24:00后",
        "周日24点后",
        "周日 24点后",
        "24:00后",
        "24:00 后",
        "24点后",
        "24 点后",
        "截止后",
        "过期后",
        "after sunday 24:00",
        "after deadline",
    )
    has_before = any(marker in lowered for marker in before_markers)
    has_after = any(marker in lowered for marker in after_markers)
    return bool(has_before and (not has_after))


def _violates_time_window_scope_rule(case_text: str) -> bool:
    lowered = str(case_text or "").strip().lower()
    if not lowered:
        return False
    if _is_before_deadline_context(lowered):
        return False
    positive_operation_markers = (
        "可以操作",
        "允许操作",
        "可以提交",
        "允许提交",
        "提交成功",
        "操作成功",
        "可以编辑",
        "允许编辑",
        "可以修改",
        "允许修改",
        "can operate",
        "can submit",
        "allowed to operate",
        "allowed to submit",
        "allowed to edit",
    )
    nonnegated_tokens = (
        "可操作",
        "可提交",
        "可编辑",
        "可修改",
    )
    read_only_markers = (
        "仅可查看",
        "只可查看",
        "仅查看",
        "不可操作",
        "不能操作",
        "禁止操作",
        "只读",
        "read only",
        "read-only",
        "readonly",
    )
    has_positive_operation = any(marker in lowered for marker in positive_operation_markers)
    if not has_positive_operation:
        for token in nonnegated_tokens:
            start = 0
            while True:
                idx = lowered.find(token, start)
                if idx < 0:
                    break
                prefix = lowered[max(0, idx - 2):idx]
                if all(neg not in prefix for neg in ("不", "禁")):
                    has_positive_operation = True
                    break
                start = idx + len(token)
            if has_positive_operation:
                break
    has_read_only_guard = any(marker in lowered for marker in read_only_markers)
    if has_positive_operation:
        return True
    if has_read_only_guard:
        return False
    return False


def _rule_applies_to_case(case_text: str, rule_text: str) -> bool:
    if _is_time_window_scope_rule(rule_text):
        return _matches_time_window_scope(case_text)
    rule_marker = _normalize_text(rule_text)
    case_marker = _normalize_text(case_text)
    return bool(rule_marker and case_marker and rule_marker in case_marker)


def _find_confirmed_fact_violations(
    case_text: str,
    confirmed_facts: list[str],
    scoped_rules: list[str],
    hard_flow_constraints: list[str],
) -> tuple[list[str], list[str]]:
    hits: list[str] = []
    violations: list[str] = []
    normalized_case = _normalize_text(case_text)

    for fact in confirmed_facts:
        marker = _normalize_text(fact)
        if marker and marker in normalized_case:
            hits.append(fact)
        if _violates_negative_fact(case_text, fact):
            violations.append(fact)
            continue
        if _violates_flow_order(case_text, fact):
            violations.append(fact)

    for flow in hard_flow_constraints:
        if _violates_flow_order(case_text, flow):
            violations.append(flow)

    for scoped_rule in scoped_rules:
        if not _rule_applies_to_case(case_text, scoped_rule):
            continue
        hits.append(scoped_rule)
        if _is_time_window_scope_rule(scoped_rule):
            if _violates_time_window_scope_rule(case_text):
                violations.append(scoped_rule)
            continue
        if _violates_negative_fact(case_text, scoped_rule):
            violations.append(scoped_rule)
            continue
        if _violates_flow_order(case_text, scoped_rule):
            violations.append(scoped_rule)

    return _dedupe_texts(hits), _dedupe_texts(violations)


def _find_forbidden_fact_violations(case_text: str, forbidden_facts: list[str]) -> list[str]:
    normalized_case = _normalize_text(case_text)
    violations: list[str] = []
    for fact in forbidden_facts:
        marker = _normalize_text(fact)
        if marker and len(marker) >= 4 and marker in normalized_case:
            violations.append(fact)
    return _dedupe_texts(violations)


def _hits_any_pattern(case_text: str, patterns: list[str]) -> list[str]:
    normalized_case = _normalize_text(case_text)
    hits: list[str] = []
    for pattern in patterns:
        marker = _normalize_text(pattern)
        if marker and marker in normalized_case:
            hits.append(pattern)
    return _dedupe_texts(hits)


__all__ = [
    '_NEGATIVE_MARKERS',
    '_PENDING_HINTS',
    '_VAGUE_UNCONFIRMED_HINTS',
    'normalize_requirement_semantics_context',
    '_merge_fact_profile_semantics',
    '_contains_pending_logic',
    '_contains_vague_unconfirmed_logic',
    '_extract_sequence_candidates',
    '_MIN_NEGATIVE_FACT_TAIL_CHARS',
    '_TEMPORAL_SHUTDOWN_SCOPE_MARKERS',
    '_TEMPORAL_SHUTDOWN_BLOCK_MARKERS',
    '_TEMPORAL_SHUTDOWN_POSITIVE_MARKERS',
    '_NEGATED_TAIL_CONTEXT_MARKERS',
    '_contains_raw_marker',
    '_negative_fact_marker_pattern',
    '_split_negative_fact_tail',
    '_is_temporal_shutdown_fact',
    '_case_negates_tail',
    '_violates_temporal_shutdown_fact',
    '_violates_negative_fact',
    '_violates_flow_order',
    '_is_time_window_scope_rule',
    '_matches_time_window_scope',
    '_is_before_deadline_context',
    '_violates_time_window_scope_rule',
    '_rule_applies_to_case',
    '_find_confirmed_fact_violations',
    '_find_forbidden_fact_violations',
    '_hits_any_pattern',
]
