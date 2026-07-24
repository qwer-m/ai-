from __future__ import annotations

from typing import Any

from .priority_behavior_semantics import (
    case_stage_kind,
    has_structured_blocking_priority_evidence,
)
from .streaming_execution_plan_helpers import is_pure_ui_goal_text, main_chain_goal_text
from .streaming_postprocess_utils import _dict_case_copies

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


def has_explicit_blocking_or_critical(case: dict[str, Any]) -> bool:
    """只认结构化阻断或关键声明，不从普通 UI 文本猜测 P0。"""
    if not isinstance(case, dict):
        return False
    transition = case.get("workflow_transition")
    transition_payload = dict(transition) if isinstance(transition, dict) else {}
    criticality = str(
        case.get("criticality")
        or case.get("business_criticality")
        or transition_payload.get("criticality")
        or ""
    ).strip().lower()
    return bool(
        case.get("blocking") is True
        or case.get("critical") is True
        or case.get("business_critical") is True
        or transition_payload.get("blocking") is True
        or transition_payload.get("critical") is True
        or case.get("destructive") is True
        or transition_payload.get("destructive") is True
        or criticality in {"critical", "blocking", "release_blocking"}
        or has_structured_blocking_priority_evidence(case)
    )


def is_entry_path_availability_case(case: dict[str, Any]) -> bool:
    """只按已编译工作流入口 step 的结构化风险识别关键入口。"""
    if not isinstance(case, dict):
        return False
    if case_stage_kind(case) != "entry":
        return False
    transition = case.get("workflow_transition")
    if not isinstance(transition, dict):
        return False
    # workflow_transition 由执行计划按已核验 blueprint step 编译，正文只用于诊断，
    # 不再参与 P0 hard guard，避免领域措辞或同义表达改变优先级。
    return bool(
        transition.get("blocking") is True
        or transition.get("critical") is True
    )


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
        if has_explicit_blocking_or_critical(item):
            continue
        if not is_pure_ui_goal_text(main_chain_goal_text(item)):
            continue
        apply_priority_override(
            item,
            priority="P2",
            source="pure_ui_non_blocking_p2",
        )
    return output
