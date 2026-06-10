from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from ..coverage.scenario_registry import (
    cross_module_scenario_kinds,
    judge_duplicate_scenario_kinds,
    judge_duplicate_thresholds,
    scenario_pattern_entries,
)

from .judge_types import (
    JudgeBatchResult,
    JudgeResult,
    JudgeSignalSet,
    JudgeStatus,
    RepairAction,
    RepairActionType,
)


_NEGATIVE_MARKERS = (
    "must not",
    "should not",
    "do not",
    "don't",
    "forbid",
    "forbidden",
    "禁止",
    "不得",
    "不能",
    "不可",
    "不允许",
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

_DUPLICATE_SCENARIO_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = scenario_pattern_entries()

_REGISTERED_SCENARIO_KINDS = judge_duplicate_scenario_kinds()
_REGISTERED_SCENARIO_THRESHOLDS = judge_duplicate_thresholds()

_DUPLICATE_SIMPLE_SCENARIOS = {
    "title_format",
    "initial_popup",
    "filter_toggle",
    "empty_state",
    "card_element",
    "workbook_scope",
    "bad_image_review",
    "media_preview",
    "answer_analysis_placeholder",
    "source_consistency",
    "network_error",
    "permission",
    "save_delete",
    "manual_mark_correct",
    "manual_mark_wrong",
    "review_warm_hint",
    "review_detail_content",
    "review_status_color",
    "review_filter_tabs",
    "feedback",
    "plan_step1_scope",
    "plan_second_step_navigation",
    "plan_slice_auto_advance",
    "plan_slice_regeneration",
    "plan_fourth_summary",
    "sorting_limit",
    "print_export",
    "report_trigger",
    "generation_trigger",
    "share_friend",
    "share_moments",
    "report_comment_ai",
    "report_comment_voice",
    "report_comment_edit",
    "report_overview_cards",
    "report_wrong_analysis",
    "report_next_plan",
    "readonly",
    "quota_exhaustion",
    "quota_consumption",
    "quota_limit",
    "silent_refresh",
    "history_makeup",
}
_DUPLICATE_SIMPLE_SCENARIOS.update(_REGISTERED_SCENARIO_KINDS)

_DUPLICATE_SCENARIO_THRESHOLDS: dict[str, tuple[float, int]] = judge_duplicate_thresholds()

_CROSS_MODULE_DUPLICATE_SCENARIOS = set()
_CROSS_MODULE_DUPLICATE_SCENARIOS.update(cross_module_scenario_kinds())

_SEMANTIC_STOP_TOKENS = {
    "case",
    "default",
    "input",
    "module",
    "none",
    "null",
    "ok",
    "output",
    "step",
    "test",
    "测试",
    "验证",
    "用例",
}


def _normalize_text(value: Any) -> str:
    lowered = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)


def _priority_rank(value: Any) -> int:
    priority = str(value or "").strip().upper()
    if priority == "P0":
        return 3
    if priority == "P1":
        return 2
    if priority == "P2":
        return 1
    return 0


def _module_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not re.search(r"[\u4e00-\u9fff]", text):
        return _normalize_text(text)
    root = re.split(r"[-_/(（]", text, maxsplit=1)[0]
    return _normalize_text(root or text)


def _semantic_similarity_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("description", "test_module", "test_input", "expected_result"):
        value = case.get(field)
        if value is not None:
            parts.append(str(value))
    steps = case.get("steps")
    if isinstance(steps, list):
        parts.extend(str(item) for item in steps[:3] if str(item).strip())
    elif steps is not None:
        parts.append(str(steps))
    return " ".join(parts)


def _semantic_tokens(value: Any) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    text = re.sub(r"tc-\d+", " ", text)
    tokens: set[str] = set()
    for segment in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text):
        if not segment:
            continue
        if re.fullmatch(r"[a-z0-9]+", segment):
            if len(segment) > 1 and not segment.isdigit() and segment not in _SEMANTIC_STOP_TOKENS:
                tokens.add(segment)
            continue
        if len(segment) == 1:
            tokens.add(segment)
            continue
        for width in (2, 3):
            if len(segment) < width:
                continue
            for index in range(0, len(segment) - width + 1):
                token = segment[index : index + width]
                if token not in _SEMANTIC_STOP_TOKENS:
                    tokens.add(token)
    return tokens


def _semantic_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_tokens = _semantic_tokens(_semantic_similarity_text(left))
    right_tokens = _semantic_tokens(_semantic_similarity_text(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    jaccard = float(intersection) / float(union) if union else 0.0
    containment = float(intersection) / float(min(len(left_tokens), len(right_tokens)))
    return max(jaccard, containment)


def _semantic_overlap_size(left: dict[str, Any], right: dict[str, Any]) -> int:
    left_tokens = _semantic_tokens(_semantic_similarity_text(left))
    right_tokens = _semantic_tokens(_semantic_similarity_text(right))
    return len(left_tokens & right_tokens)


def _scenario_kind(case: dict[str, Any]) -> str:
    text = _semantic_similarity_text(case).lower()
    compact = _normalize_text(text)
    best_kind = ""
    best_score = 0
    for kind, patterns in _DUPLICATE_SCENARIO_PATTERNS:
        matched: list[str] = []
        for pattern in patterns:
            marker = _normalize_text(pattern)
            if marker and marker in compact:
                matched.append(marker)
        if not matched:
            continue
        long_marker_count = sum(1 for marker in matched if len(marker) >= 4)
        if len(matched) < 2 and long_marker_count < 1:
            continue
        score = len(matched) * 10 + long_marker_count * 3 + max(len(marker) for marker in matched)
        if kind in {"manual_mark_correct", "manual_mark_wrong"}:
            score += 40
        if score > best_score:
            best_kind = kind
            best_score = score
    return best_kind


def _same_module_family(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_module = _module_family(left.get("test_module"))
    right_module = _module_family(right.get("test_module"))
    if not left_module or not right_module:
        return True
    return left_module == right_module or left_module in right_module or right_module in left_module


def _is_semantic_duplicate_case(candidate: dict[str, Any], existed: dict[str, Any]) -> tuple[bool, float]:
    candidate_desc = _normalize_text(candidate.get("description"))
    existed_desc = _normalize_text(existed.get("description"))
    if candidate_desc and existed_desc and candidate_desc == existed_desc:
        return True, 1.0
    score = _semantic_similarity(candidate, existed)
    overlap = _semantic_overlap_size(candidate, existed)
    candidate_kind = _scenario_kind(candidate)
    existed_kind = _scenario_kind(existed)
    if not _same_module_family(candidate, existed):
        if (
            candidate_kind
            and candidate_kind == existed_kind
            and candidate_kind in _CROSS_MODULE_DUPLICATE_SCENARIOS
        ):
            simple_score, simple_overlap = _DUPLICATE_SCENARIO_THRESHOLDS.get(candidate_kind, (0.30, 8))
            if score >= simple_score and overlap >= simple_overlap:
                return True, score
        return False, 0.0
    if candidate_kind and candidate_kind == existed_kind:
        simple_score, simple_overlap = _DUPLICATE_SCENARIO_THRESHOLDS.get(candidate_kind, (0.30, 8))
        if candidate_kind in _DUPLICATE_SIMPLE_SCENARIOS:
            if score >= simple_score and overlap >= simple_overlap:
                return True, score
            if score >= 0.46 and overlap >= 12:
                return True, score
    if score >= 0.90 and overlap >= 30:
        return True, score
    return False, score


def _case_quality_key(case: dict[str, Any], original_index: int) -> tuple[int, int, int, int]:
    steps = case.get("steps")
    step_count = len([item for item in steps if str(item).strip()]) if isinstance(steps, list) else 0
    concrete_text_len = len(_normalize_text(case.get("expected_result"))) + len(_normalize_text(case.get("description")))
    return (
        _priority_rank(case.get("priority") or case.get("priority_final")),
        min(step_count, 6),
        min(concrete_text_len, 240),
        -int(original_index),
    )


def _dedupe_texts(values: list[Any] | None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        key = _normalize_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def normalize_requirement_semantics_context(requirement_semantics_context: dict[str, Any] | str | None) -> dict[str, list[str]]:
    payload: dict[str, Any] = {}
    if isinstance(requirement_semantics_context, dict):
        payload = dict(requirement_semantics_context)
    elif isinstance(requirement_semantics_context, str):
        text = requirement_semantics_context.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                decoded = json.loads(text)
                if isinstance(decoded, dict):
                    payload = decoded
            except Exception:
                payload = {}

    return {
        "confirmed_facts": _dedupe_texts(payload.get("confirmed_facts") if isinstance(payload, dict) else []),
        "scoped_rules": _dedupe_texts(payload.get("scoped_rules") if isinstance(payload, dict) else []),
        "forbidden_facts": _dedupe_texts(payload.get("forbidden_facts") if isinstance(payload, dict) else []),
        "pending_items": _dedupe_texts(payload.get("pending_items") if isinstance(payload, dict) else []),
        "reuse_declarations": _dedupe_texts(payload.get("reuse_declarations") if isinstance(payload, dict) else []),
        "hard_flow_constraints": _dedupe_texts(payload.get("hard_flow_constraints") if isinstance(payload, dict) else []),
        "reuse_risks": _dedupe_texts(payload.get("reuse_risks") if isinstance(payload, dict) else []),
    }


def _merge_fact_profile_semantics(
    semantics: dict[str, list[str]],
    control_state: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    if not isinstance(control_state, dict):
        return semantics
    source_meta = control_state.get("source_meta") if isinstance(control_state.get("source_meta"), dict) else {}
    profile = source_meta.get("fact_profile") if isinstance(source_meta, dict) else None
    if not isinstance(profile, dict):
        return semantics
    merged = {key: list(value or []) for key, value in (semantics or {}).items()}
    mapping = {
        "confirmed_facts": "confirmed_facts",
        "scoped_rules": "scoped_rules",
        "forbidden_facts": "forbidden_facts",
        "pending_items": "pending_items",
        "reuse_declarations": "reuse_declarations",
        "hard_flow_constraints": "hard_flow_constraints",
        "reuse_risks": "reuse_risks",
    }
    for profile_key, target_key in mapping.items():
        values = profile.get(profile_key)
        if isinstance(values, list):
            merged[target_key] = _dedupe_texts([*merged.get(target_key, []), *values])
    protected_fact_keys = {
        _normalize_text(item)
        for item in [
            *merged.get("confirmed_facts", []),
            *merged.get("scoped_rules", []),
            *merged.get("hard_flow_constraints", []),
        ]
        if _normalize_text(item)
    }
    if protected_fact_keys:
        merged["forbidden_facts"] = [
            item
            for item in (merged.get("forbidden_facts") or [])
            if _normalize_text(item) and _normalize_text(item) not in protected_fact_keys
        ]
    return merged


def _collect_case_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("id", "description", "test_module", "test_input", "expected_result"):
        value = case.get(field)
        if value is not None:
            parts.append(str(value))
    for field in ("preconditions", "steps", "tags"):
        value = case.get(field)
        if isinstance(value, list):
            parts.extend([str(item) for item in value if str(item).strip()])
        elif value is not None:
            parts.append(str(value))
    return " ".join(parts)


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
    parts: list[str] = []
    for field in ("description", "test_input", "expected_result"):
        value = case.get(field)
        if value is not None:
            parts.append(str(value))
    targeted_text = " ".join(parts)
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


def _violates_negative_fact(case_text: str, fact: str) -> bool:
    lowered_fact = str(fact or "").strip().lower()
    if not lowered_fact:
        return False
    if not any(marker in lowered_fact for marker in _NEGATIVE_MARKERS):
        return False

    normalized_case = _normalize_text(case_text)
    pieces = re.split(r"(must not|should not|do not|don't|forbid|forbidden|禁止|不得|不能|不可|不允许)", lowered_fact)
    tail = pieces[-1] if pieces else ""
    tail_normalized = _normalize_text(tail)
    if len(tail_normalized) < 2:
        return False
    return tail_normalized in normalized_case


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


def judge_case(
    case: dict[str, Any],
    requirement_semantics_context: dict[str, Any] | str | None,
    control_state: dict[str, Any] | None = None,
) -> JudgeResult:
    semantics = _merge_fact_profile_semantics(
        normalize_requirement_semantics_context(requirement_semantics_context),
        control_state=control_state,
    )
    before = deepcopy(case) if isinstance(case, dict) else {}
    case_id = str(before.get("id") or before.get("case_id") or "").strip() or "UNKNOWN"
    case_text = _collect_case_text(before)

    contains_pending_logic, pending_hits = _contains_pending_logic(case_text, semantics.get("pending_items") or [])
    contains_vague_unconfirmed, vague_or_unconfirmed_hits = _contains_vague_unconfirmed_logic(before)
    contains_pending_logic = bool(contains_pending_logic or contains_vague_unconfirmed)
    pending_hits = _dedupe_texts([*pending_hits, *vague_or_unconfirmed_hits])
    confirmed_fact_hits, confirmed_fact_violations = _find_confirmed_fact_violations(
        case_text,
        semantics.get("confirmed_facts") or [],
        semantics.get("scoped_rules") or [],
        semantics.get("hard_flow_constraints") or [],
    )
    forbidden_fact_violations = _find_forbidden_fact_violations(
        case_text,
        semantics.get("forbidden_facts") or [],
    )
    confirmed_fact_violations = _dedupe_texts(
        [*confirmed_fact_violations, *forbidden_fact_violations]
    )
    reuse_risk_hits = _hits_any_pattern(case_text, semantics.get("reuse_risks") or [])

    signals = JudgeSignalSet(
        violates_confirmed_fact=bool(confirmed_fact_violations),
        contains_pending_logic=bool(contains_pending_logic),
        confirmed_fact_hits=confirmed_fact_hits,
        confirmed_fact_violations=confirmed_fact_violations,
        reuse_risk_hits=reuse_risk_hits,
        pending_hits=pending_hits,
        vague_or_unconfirmed_hits=vague_or_unconfirmed_hits,
    )

    if signals.contains_pending_logic:
        return JudgeResult(
            case_id=case_id,
            status=JudgeStatus.PENDING,
            signals=signals,
            pending_reason="contains_pending_logic",
            suggested_actions=[
                RepairAction(
                    action_type=RepairActionType.ISOLATE_PENDING,
                    reason="Case contains pending/unconfirmed statements.",
                    target_case_id=case_id,
                )
            ],
            before_case=before,
        )

    if signals.violates_confirmed_fact:
        return JudgeResult(
            case_id=case_id,
            status=JudgeStatus.REJECT,
            signals=signals,
            reject_reason="violates_confirmed_fact",
            suggested_actions=[
                RepairAction(
                    action_type=RepairActionType.DROP_CASE,
                    reason="Case violates confirmed facts or hard flow constraints.",
                    target_case_id=case_id,
                )
            ],
            before_case=before,
        )

    return JudgeResult(
        case_id=case_id,
        status=JudgeStatus.PASS,
        signals=signals,
        before_case=before,
        after_case=deepcopy(before),
    )


def _all_patterns_covered(cases: list[dict[str, Any]], patterns: list[str]) -> tuple[bool, list[str]]:
    if not patterns:
        return True, []
    missing: list[str] = []
    for pattern in patterns:
        marker = _normalize_text(pattern)
        if not marker:
            continue
        hit = False
        for case in cases:
            if marker in _normalize_text(_collect_case_text(case)):
                hit = True
                break
        if not hit:
            missing.append(pattern)
    return len(missing) == 0, missing


def judge_cases(
    cases: list[dict[str, Any]],
    requirement_semantics_context: dict[str, Any] | str | None,
    control_state: dict[str, Any] | None = None,
) -> JudgeBatchResult:
    semantics = _merge_fact_profile_semantics(
        normalize_requirement_semantics_context(requirement_semantics_context),
        control_state=control_state,
    )
    judged_cases: list[JudgeResult] = []
    for index, case in enumerate(cases or [], start=1):
        if not isinstance(case, dict):
            continue
        judged = judge_case(case, semantics, control_state=control_state)
        if not judged.case_id or judged.case_id == "UNKNOWN":
            judged.case_id = str(case.get("id") or f"CASE-{index:03d}")
        judged_cases.append(judged)

    kept_passes: list[tuple[int, JudgeResult, dict[str, Any]]] = []
    for index, item in enumerate(judged_cases):
        if item.status != JudgeStatus.PASS:
            continue
        candidate_case = item.after_case if item.after_case else item.before_case
        if not isinstance(candidate_case, dict):
            continue

        duplicate_match: tuple[int, JudgeResult, dict[str, Any], float] | None = None
        for kept_index, kept_item, kept_case in kept_passes:
            is_duplicate, similarity = _is_semantic_duplicate_case(candidate_case, kept_case)
            if not is_duplicate:
                continue
            if duplicate_match is None or similarity > duplicate_match[3]:
                duplicate_match = (kept_index, kept_item, kept_case, similarity)

        if duplicate_match is None:
            kept_passes.append((index, item, candidate_case))
            continue

        kept_index, kept_item, kept_case, similarity = duplicate_match
        candidate_quality = _case_quality_key(candidate_case, index)
        kept_quality = _case_quality_key(kept_case, kept_index)

        if candidate_quality > kept_quality:
            kept_item.status = JudgeStatus.REJECT
            kept_item.reject_reason = f"semantic_duplicate:{item.case_id}"
            kept_item.signals.is_semantic_duplicate = True
            kept_item.signals.duplicate_of_case_id = item.case_id
            kept_item.signals.duplicate_similarity = round(float(similarity), 4)
            kept_item.signals.notes = _dedupe_texts([*kept_item.signals.notes, "batch_semantic_duplicate"])
            kept_item.suggested_actions = [
                RepairAction(
                    action_type=RepairActionType.DROP_CASE,
                    reason="Case is semantically duplicated by a stronger candidate.",
                    target_case_id=kept_item.case_id,
                    payload={"duplicate_of_case_id": item.case_id, "similarity": round(float(similarity), 4)},
                )
            ]
            kept_passes = [
                (
                    index if existing_index == kept_index else existing_index,
                    item if existing_index == kept_index else existing_item,
                    candidate_case if existing_index == kept_index else existing_case,
                )
                for existing_index, existing_item, existing_case in kept_passes
            ]
            continue

        item.status = JudgeStatus.REJECT
        item.reject_reason = f"semantic_duplicate:{kept_item.case_id}"
        item.signals.is_semantic_duplicate = True
        item.signals.duplicate_of_case_id = kept_item.case_id
        item.signals.duplicate_similarity = round(float(similarity), 4)
        item.signals.notes = _dedupe_texts([*item.signals.notes, "batch_semantic_duplicate"])
        item.suggested_actions = [
            RepairAction(
                action_type=RepairActionType.DROP_CASE,
                reason="Case is semantically duplicated by an already accepted candidate.",
                target_case_id=item.case_id,
                payload={"duplicate_of_case_id": kept_item.case_id, "similarity": round(float(similarity), 4)},
            )
        ]

    pass_cases = [
        item.after_case if item.after_case else item.before_case
        for item in judged_cases
        if item.status == JudgeStatus.PASS
    ]

    core_flow_patterns = semantics.get("hard_flow_constraints") or []
    reuse_risk_patterns = semantics.get("reuse_risks") or []
    core_flow_covered, missing_core_flow = _all_patterns_covered(pass_cases, core_flow_patterns)
    reuse_risk_covered, missing_reuse_risk = _all_patterns_covered(pass_cases, reuse_risk_patterns)

    if not core_flow_covered:
        judged_cases.append(
            JudgeResult(
                case_id="AUTO-CORE-FLOW",
                status=JudgeStatus.REPAIRABLE,
                signals=JudgeSignalSet(
                    missing_core_flow=True,
                    notes=["batch_level_gap"],
                ),
                suggested_actions=[
                    RepairAction(
                        action_type=RepairActionType.APPEND_CORE_FLOW_CASE,
                        reason="Batch misses core flow coverage.",
                        payload={"missing_core_flow_items": missing_core_flow},
                    )
                ],
                before_case={},
            )
        )

    if not reuse_risk_covered:
        judged_cases.append(
            JudgeResult(
                case_id="AUTO-REUSE-RISK",
                status=JudgeStatus.REPAIRABLE,
                signals=JudgeSignalSet(
                    missing_reuse_risk=True,
                    missing_reuse_risk_items=missing_reuse_risk,
                    notes=["batch_level_gap"],
                ),
                suggested_actions=[
                    RepairAction(
                        action_type=RepairActionType.APPEND_REUSE_RISK_CASE,
                        reason="Batch misses reuse risk coverage.",
                        payload={"missing_reuse_risk_items": missing_reuse_risk},
                    )
                ],
                before_case={},
            )
        )

    result = JudgeBatchResult(
        cases=judged_cases,
        core_flow_covered=bool(core_flow_covered),
        reuse_risk_covered=bool(reuse_risk_covered),
        pass_count=sum(1 for item in judged_cases if item.status == JudgeStatus.PASS),
        repairable_count=sum(1 for item in judged_cases if item.status == JudgeStatus.REPAIRABLE),
        reject_count=sum(1 for item in judged_cases if item.status == JudgeStatus.REJECT),
        pending_count=sum(1 for item in judged_cases if item.status == JudgeStatus.PENDING),
        notes=[
            f"confirmed_facts={len(semantics.get('confirmed_facts') or [])}",
            f"scoped_rules={len(semantics.get('scoped_rules') or [])}",
            f"pending_items={len(semantics.get('pending_items') or [])}",
            f"hard_flow_constraints={len(core_flow_patterns)}",
            f"reuse_risks={len(reuse_risk_patterns)}",
            f"registry_duplicate_scenario_kinds={len(_CROSS_MODULE_DUPLICATE_SCENARIOS)}",
            f"registry_threshold_entries={len(_DUPLICATE_SCENARIO_THRESHOLDS)}",
        ],
    )
    return result
