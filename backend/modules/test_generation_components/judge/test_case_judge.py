from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

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


def _normalize_text(value: Any) -> str:
    lowered = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)


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
        "pending_items": _dedupe_texts(payload.get("pending_items") if isinstance(payload, dict) else []),
        "reuse_declarations": _dedupe_texts(payload.get("reuse_declarations") if isinstance(payload, dict) else []),
        "hard_flow_constraints": _dedupe_texts(payload.get("hard_flow_constraints") if isinstance(payload, dict) else []),
        "reuse_risks": _dedupe_texts(payload.get("reuse_risks") if isinstance(payload, dict) else []),
    }


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
    _ = control_state
    semantics = normalize_requirement_semantics_context(requirement_semantics_context)
    before = deepcopy(case) if isinstance(case, dict) else {}
    case_id = str(before.get("id") or before.get("case_id") or "").strip() or "UNKNOWN"
    case_text = _collect_case_text(before)

    contains_pending_logic, pending_hits = _contains_pending_logic(case_text, semantics.get("pending_items") or [])
    confirmed_fact_hits, confirmed_fact_violations = _find_confirmed_fact_violations(
        case_text,
        semantics.get("confirmed_facts") or [],
        semantics.get("scoped_rules") or [],
        semantics.get("hard_flow_constraints") or [],
    )
    reuse_risk_hits = _hits_any_pattern(case_text, semantics.get("reuse_risks") or [])

    signals = JudgeSignalSet(
        violates_confirmed_fact=bool(confirmed_fact_violations),
        contains_pending_logic=bool(contains_pending_logic),
        confirmed_fact_hits=confirmed_fact_hits,
        confirmed_fact_violations=confirmed_fact_violations,
        reuse_risk_hits=reuse_risk_hits,
        pending_hits=pending_hits,
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
    semantics = normalize_requirement_semantics_context(requirement_semantics_context)
    judged_cases: list[JudgeResult] = []
    for index, case in enumerate(cases or [], start=1):
        if not isinstance(case, dict):
            continue
        judged = judge_case(case, semantics, control_state=control_state)
        if not judged.case_id or judged.case_id == "UNKNOWN":
            judged.case_id = str(case.get("id") or f"CASE-{index:03d}")
        judged_cases.append(judged)

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
        ],
    )
    return result
