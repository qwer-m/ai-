from __future__ import annotations

from typing import Any

from .case_access import case_flat_text
from .execution_plan_validation_tokens import _STATE_FIELD_NAMES


_PRECONDITION_STATE_FAMILIES: dict[str, tuple[str, ...]] = {
    "approval": ("审核通过", "审批通过", "已审核", "approved", "approval passed"),
    "rejection": ("审核失败", "审核不通过", "审批拒绝", "rejected", "approval failed"),
    "message": ("系统消息", "通知消息", "message", "notification"),
    "published": ("已发布", "发布成功", "published", "publication"),
    "submitted": ("已提交", "提交成功", "submitted", "submit success"),
    "saved": ("已保存", "保存成功", "saved", "save success"),
    "completed": ("已完成", "completed", "completion"),
    "generated": ("已生成", "生成成功", "generated"),
    "purchased": ("已购买", "购买成功", "purchased", "payment success"),
    "unlocked": ("已解锁", "开通成功", "unlocked", "activated"),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _transition_payload(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("workflow_transition")
    return dict(nested) if isinstance(nested, dict) else {}


def _state_value(case: dict[str, Any], field: str) -> Any:
    value = case.get(field)
    if value not in (None, ""):
        return value
    return _transition_payload(case).get(field)


def _case_order(case: dict[str, Any], fallback: int) -> tuple[int, int]:
    try:
        main_step = int(case.get("main_chain_step") or 0)
    except (TypeError, ValueError):
        main_step = 0
    try:
        sequence = int(case.get("execution_sequence") or fallback)
    except (TypeError, ValueError):
        sequence = fallback
    return (main_step if main_step > 0 else 100000 + sequence, sequence)


def _main_smoke_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        dict(item)
        for item in cases
        if isinstance(item, dict) and _text(item.get("execution_group")).lower() == "main_smoke"
    ]
    return [
        item
        for _, item in sorted(
            enumerate(selected, start=1),
            key=lambda row: _case_order(row[1], row[0]),
        )
    ]


def _case_semantic_text(case: dict[str, Any]) -> str:
    return case_flat_text(
        case,
        fields=("test_module", "description", "test_input", "expected_result", "preconditions", "steps"),
        separator=" ",
        lower=True,
    )


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return _text(value)


def _state_families(text: str) -> set[str]:
    lowered = str(text or "").lower()
    return {
        family
        for family, tokens in _PRECONDITION_STATE_FAMILIES.items()
        if any(str(token).lower() in lowered for token in tokens)
    }


def main_chain_precondition_conflict_reason(
    previous_case: dict[str, Any] | None,
    current_case: dict[str, Any],
    *,
    previous_step_meta: dict[str, Any] | None = None,
    current_step_meta: dict[str, Any] | None = None,
) -> str:
    """校验当前用例声明的关键前置状态是否由上一阶段或蓝图动作产生。"""
    if not isinstance(previous_case, dict) or not isinstance(current_case, dict):
        return ""
    precondition_text = _flatten_text(current_case.get("preconditions"))
    required_families = _state_families(precondition_text)
    if not required_families:
        return ""
    previous_meta = dict(previous_step_meta or {})
    current_meta = dict(current_step_meta or {})
    evidence_text = " ".join(
        [
            _flatten_text(previous_case.get("description")),
            _flatten_text(previous_case.get("steps")),
            _flatten_text(previous_case.get("expected_result")),
            _flatten_text(previous_meta.get("action")),
            _flatten_text(previous_meta.get("assertion")),
            _flatten_text(previous_meta.get("state_out")),
            _flatten_text(current_meta.get("action")),
            _flatten_text(current_meta.get("assertion")),
            _flatten_text(current_meta.get("state_in")),
        ]
    )
    produced_families = _state_families(evidence_text)
    return (
        "precondition_state_not_produced_by_previous_stage"
        if required_families - produced_families
        else ""
    )


def materialize_final_case_state_fields(cases: Any) -> Any:
    """Promote the workflow transition contract into persisted final-case fields."""
    if not isinstance(cases, list):
        return cases
    normalized: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        case = dict(item)
        transition = _transition_payload(case)
        for field in _STATE_FIELD_NAMES:
            value = case.get(field)
            if value in (None, "") and transition.get(field) not in (None, ""):
                case[field] = transition[field]
        normalized.append(case)
    return normalized
