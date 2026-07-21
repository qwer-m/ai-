from __future__ import annotations

from typing import Any, Callable

from .case_access import case_flat_text, case_priority
from .postprocess_priority_config import (
    p0_core_tokens,
    p0_critical_families,
    p0_low_value_tokens,
)
from .priority_anchor_floor_policy import MainPathAnchorPolicy
from .streaming_case_normalization import normalize_priority_value
from .streaming_execution_plan_helpers import is_pure_ui_goal_text, main_chain_goal_text
from .streaming_postprocess_utils import _dict_case_copies

_ENTRY_ACTION_TOKENS = (
    "点击",
    "点按",
    "进入",
    "打开",
    "跳转",
    "返回",
    "切换",
    "click",
    "tap",
    "enter",
    "open",
    "navigate",
    "return",
    "switch",
)

_ENTRY_SURFACE_TOKENS = (
    "入口",
    "按钮",
    "卡片",
    "列表项",
    "页面",
    "链接",
    "菜单",
    "路径",
    "tab",
    "entry",
    "button",
    "card",
    "list item",
    "page",
    "link",
    "menu",
    "route",
)

_ENTRY_OUTCOME_TOKENS = (
    "进入",
    "打开",
    "跳转",
    "返回",
    "定位到",
    "目标页面",
    "详情页",
    "首页",
    "列表页",
    "不可点击",
    "点击无效",
    "无响应",
    "入口不存在",
    "未显示入口",
    "enter",
    "open",
    "navigate",
    "redirect",
    "target page",
    "not clickable",
    "no response",
    "entry missing",
    "blocks access",
    "access blocked",
    "cannot access",
    "无法访问",
    "无法进入",
    "阻止进入",
)


def p0_main_path_target_count(case_count: int, *, coverage_mode: str = "") -> int:
    count = max(0, int(case_count or 0))
    if count <= 0:
        return 0
    mode = str(coverage_mode or "")
    if mode == "full_functional_regression":
        if count >= 80:
            target_count = min(12, max(8, int((count + 9) // 10)))
        elif count >= 40:
            target_count = min(10, max(9, int(round(count * 0.12))))
        else:
            target_count = min(6, max(3, int(round(count * 0.14))))
    elif mode == "expanded_regression":
        target_count = (
            min(4, max(3, int(round(count * 0.06))))
            if count >= 50
            else min(3, max(1, int(round(count * 0.08))))
        )
    else:
        target_count = 0
    return min(max(0, int(target_count)), count)


def apply_priority_override(
    case: dict[str, Any],
    *,
    priority: str,
    source: str,
    state: str = "overridden",
) -> None:
    normalized_priority = str(priority or "").strip().upper()
    if normalized_priority not in {"P0", "P1", "P2"}:
        return
    case["priority"] = normalized_priority
    case["priority_final"] = normalized_priority
    case["priority_decision_state"] = str(state or "overridden")
    case["priority_decision_source"] = str(source or "").strip()


def p0_case_anchor_text(case: dict[str, Any] | Any) -> str:
    if not isinstance(case, dict):
        return str(case or "").lower()
    return case_flat_text(
        case,
        fields=("test_module", "description", "expected_result", "test_input", "steps"),
        separator=" ",
        lower=True,
    )


def p0_has_low_value_signal(case_or_text: dict[str, Any] | str) -> bool:
    text = p0_case_anchor_text(case_or_text)
    return any(token.lower() in text for token in p0_low_value_tokens())


def p0_has_core_signal(case_or_text: dict[str, Any] | str) -> bool:
    text = p0_case_anchor_text(case_or_text)
    return any(token.lower() in text for token in p0_core_tokens())


def p0_configured_anchor_family(
    case_or_text: dict[str, Any] | str,
) -> str:
    text = p0_case_anchor_text(case_or_text)
    critical_families = p0_critical_families()
    for family, tokens in critical_families:
        if all(token.lower() in text for token in tokens):
            return str(family)
    return ""


def p0_main_path_anchor(case: dict[str, Any]) -> bool:
    if p0_configured_anchor_family(case):
        return True
    return p0_has_core_signal(case) and not p0_has_low_value_signal(case)


def is_entry_path_availability_case(case: dict[str, Any]) -> bool:
    """识别会阻断用户进入目标功能的真实入口路径，排除仅检查样式的用例。"""
    if not isinstance(case, dict):
        return False
    if is_pure_ui_goal_text(main_chain_goal_text(case)):
        return False
    action_segments = [
        str(case.get("description") or "").lower(),
        str(case.get("test_input") or "").lower(),
        *[
            str(item or "").lower()
            for item in (case.get("steps") or [])
            if str(item or "").strip()
        ],
    ]
    outcome_text = case_flat_text(
        case,
        fields=("description", "expected_result"),
        separator=" ",
        lower=True,
    )
    has_surface_action = any(
        any(action.lower() in segment for action in _ENTRY_ACTION_TOKENS)
        and any(surface.lower() in segment for surface in _ENTRY_SURFACE_TOKENS)
        for segment in action_segments
    )
    has_outcome = any(token.lower() in outcome_text for token in _ENTRY_OUTCOME_TOKENS)
    return bool(has_surface_action and has_outcome)


def enforce_entry_path_p0(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = _dict_case_copies(cases)
    for item in output:
        if not is_entry_path_availability_case(item):
            continue
        apply_priority_override(
            item,
            priority="P0",
            source="entry_path_availability_p0",
        )
    return output


def enforce_pure_ui_p2(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """纯文案和视觉样式校验不占用业务阻断优先级。"""
    output = _dict_case_copies(cases)
    for item in output:
        if str(item.get("execution_group") or "").strip() == "main_smoke":
            continue
        if not is_pure_ui_goal_text(main_chain_goal_text(item)):
            continue
        apply_priority_override(
            item,
            priority="P2",
            source="pure_ui_non_blocking_p2",
        )
    return output


def _default_case_signature(case: dict[str, Any]) -> str:
    return "\n".join(
        str(case.get(field) or "")
        for field in ("test_module", "description", "expected_result", "test_input")
    )


def enforce_main_path_p0_anchors(
    cases: list[dict[str, Any]],
    *,
    coverage_mode: str = "",
    requirement_text: str = "",
    case_signature_fn: Callable[[dict[str, Any]], str] | None = None,
    case_complexity_profile_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    candidate_cases = _dict_case_copies(cases)
    mode = str(coverage_mode or "")
    if mode not in {"expanded_regression", "full_functional_regression"}:
        return candidate_cases
    case_count = len(candidate_cases)
    if case_count <= 0:
        return candidate_cases
    target_count = p0_main_path_target_count(case_count, coverage_mode=mode)
    signature_fn = case_signature_fn or _default_case_signature
    policy = MainPathAnchorPolicy(
        configured_anchor_family_fn=p0_configured_anchor_family,
        has_core_signal_fn=p0_has_core_signal,
        has_low_value_signal_fn=p0_has_low_value_signal,
        complexity_profile_fn=case_complexity_profile_fn,
    )

    for item in candidate_cases:
        if normalize_priority_value(case_priority(item)) != "P0":
            continue
        text = p0_case_anchor_text(item)
        if policy.should_demote_non_blocking(text, item=item):
            apply_priority_override(
                item,
                priority="P1",
                source="main_path_anchor_demoted_non_blocking",
            )

    existing_p0_signatures = {
        signature_fn(item)
        for item in candidate_cases
        if normalize_priority_value(case_priority(item)) == "P0"
    }
    if len(existing_p0_signatures) >= target_count:
        return candidate_cases

    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    for index, item in enumerate(candidate_cases):
        if signature_fn(item) in existing_p0_signatures:
            continue
        text = p0_case_anchor_text(item)
        normalized_priority = normalize_priority_value(case_priority(item))
        rank = policy.primary_rank(
            item=item,
            index=index,
            text=text,
            normalized_priority=normalized_priority,
        )
        if rank is not None:
            ranked.append(rank)

    if len(ranked) < max(1, target_count - len(existing_p0_signatures)):
        ranked_signatures = {signature_fn(item) for _score, _neg_index, _family, item in ranked}
        for index, item in enumerate(candidate_cases):
            signature = signature_fn(item)
            if signature in existing_p0_signatures or signature in ranked_signatures:
                continue
            text = p0_case_anchor_text(item)
            normalized_priority = normalize_priority_value(case_priority(item))
            rank = policy.fallback_rank(
                item=item,
                index=index,
                text=text,
                normalized_priority=normalized_priority,
                mode=mode,
            )
            if rank is not None:
                ranked.append(rank)
                ranked_signatures.add(signature)

    if not ranked:
        return candidate_cases
    ranked.sort(reverse=True)
    promoted_signatures: set[str] = set(existing_p0_signatures)
    promoted_families: set[str] = set()
    output = _dict_case_copies(candidate_cases)
    for _score, _neg_index, family, item in ranked:
        if len(promoted_signatures) >= target_count:
            break
        if family in promoted_families and family != "general":
            continue
        signature = signature_fn(item)
        if signature in promoted_signatures:
            continue
        promoted_signatures.add(signature)
        promoted_families.add(family)
        for updated in output:
            if signature_fn(updated) == signature:
                apply_priority_override(
                    updated,
                    priority="P0",
                    source="main_path_anchor_floor",
                )
                break
    if len(promoted_signatures) < target_count:
        for _score, _neg_index, _family, item in ranked:
            if len(promoted_signatures) >= target_count:
                break
            signature = signature_fn(item)
            if signature in promoted_signatures:
                continue
            promoted_signatures.add(signature)
            for updated in output:
                if signature_fn(updated) == signature:
                    apply_priority_override(
                        updated,
                        priority="P0",
                        source="main_path_anchor_floor",
                    )
                    break
    return output


def enforce_execution_plan_p0_floor(
    cases: list[dict[str, Any]],
    *,
    min_p0_count: int = 6,
) -> list[dict[str, Any]]:
    """在执行计划成形后，仅用主链用例补齐持久化要求的 P0 下限。"""
    output = _dict_case_copies(cases)
    target = max(0, int(min_p0_count or 0))
    current = sum(1 for item in output if normalize_priority_value(case_priority(item)) == "P0")
    if current >= target:
        return output
    for item in output:
        if current >= target:
            break
        if str(item.get("execution_group") or "").strip().lower() != "main_smoke":
            continue
        if normalize_priority_value(case_priority(item)) == "P0":
            continue
        apply_priority_override(
            item,
            priority="P0",
            source="execution_plan_main_chain_p0_floor",
        )
        current += 1
    return output
