from __future__ import annotations

from typing import Any, Callable, Collection

from ..control.actor_roles import normalize_actor_role as normalize_actor_role_value
from .streaming_execution_plan_blueprints import (
    main_chain_stages_from_blueprints,
    pattern_match_score,
    resolve_primary_workflow_blueprint,
    stage_match_patterns,
    workflow_blueprint_source_label,
)
from .streaming_execution_plan_stage_inference import (
    contains_any_token,
    infer_workflow_phase,
    infer_workflow_stage_kind,
    token_hit,
)
from .streaming_postprocess_utils import _clip_text

from .streaming_execution_plan_grouping import (
    default_group_setup_map,
    default_group_teardown_map,
    empty_execution_plan_summary,
    execution_case_text,
    fixture_for_case,
    infer_data_state,
    infer_group,
    infer_role,
    is_core_result_output_anchor,
    is_low_value_main_chain_p0,
    priority_rank,
    session_key_for_role,
    setup_hint,
)

def _declared_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _declared_text_list(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    output: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _main_chain_cases_in_order(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        dict(item)
        for item in cases
        if isinstance(item, dict)
        and str(item.get("execution_group") or "").strip().lower() == "main_smoke"
    ]

    def order_key(row: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        index, item = row
        try:
            main_step = int(item.get("main_chain_step") or 0)
        except (TypeError, ValueError):
            main_step = 0
        try:
            sequence = int(item.get("execution_sequence") or index)
        except (TypeError, ValueError):
            sequence = index
        return (main_step if main_step > 0 else 100_000 + sequence, sequence)

    return [item for _index, item in sorted(enumerate(selected, start=1), key=order_key)]


def _case_transition_value(case: dict[str, Any], field: str) -> Any:
    value = case.get(field)
    if value not in (None, ""):
        return value
    transition = case.get("workflow_transition")
    return transition.get(field) if isinstance(transition, dict) else None


def _select_workflow_blueprint(
    workflow_blueprints: list[dict[str, Any]],
    main_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    blueprints = [dict(item) for item in workflow_blueprints if isinstance(item, dict)]
    if not blueprints:
        return {}
    workflow_ids = {
        str(_case_transition_value(item, "workflow_id") or "").strip()
        for item in main_cases
        if str(_case_transition_value(item, "workflow_id") or "").strip()
    }
    if workflow_ids:
        for blueprint in blueprints:
            blueprint_id = str(blueprint.get("workflow_id") or blueprint.get("id") or "").strip()
            if blueprint_id in workflow_ids:
                return blueprint
    return blueprints[0]


def _declared_workflow_contract_snapshot(
    blueprint: dict[str, Any],
    *,
    steps: list[dict[str, Any]],
    required_stage_ids: list[str],
    initial_state: str,
    terminal_states: list[str],
) -> dict[str, Any]:
    if not blueprint:
        return {}
    contract_fields = (
        "id",
        "workflow_id",
        "name",
        "source_type",
        "repository_source",
        "source",
        "trusted",
        "confidence",
    )
    step_fields = (
        "id",
        "label",
        "action",
        "stage_kind",
        "actor",
        "state_in",
        "state_out",
        "required",
        "terminal",
        "critical",
        "blocking",
        "module_candidates",
    )
    snapshot = {
        key: blueprint.get(key)
        for key in contract_fields
        if blueprint.get(key) not in (None, "")
    }
    snapshot.update(
        {
            "initial_state": initial_state,
            "terminal_states": list(terminal_states),
            "required_stage_ids": list(required_stage_ids),
            "steps": [
                {
                    key: step.get(key)
                    for key in step_fields
                    if step.get(key) not in (None, "")
                }
                for step in steps
            ],
        }
    )
    return snapshot


def evaluate_declared_workflow_closure(
    cases: list[dict[str, Any]],
    *,
    workflow_blueprints: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """按蓝图声明的必选阶段和状态图评估闭环，不猜测固定业务阶段。"""
    main_cases = _main_chain_cases_in_order(cases)
    blueprint = _select_workflow_blueprint(list(workflow_blueprints or []), main_cases)
    steps = [dict(item) for item in (blueprint.get("steps") or []) if isinstance(item, dict)]
    step_by_id = {
        str(step.get("id") or "").strip(): step
        for step in steps
        if str(step.get("id") or "").strip()
    }

    declared_required_ids = _declared_text_list(blueprint.get("required_stage_ids"))
    required_stage_ids = declared_required_ids or [
        step_id
        for step_id, step in step_by_id.items()
        if _declared_bool(step.get("required"), default=False)
    ]
    initial_state = str(blueprint.get("initial_state") or "").strip()
    if not initial_state:
        for stage_id in required_stage_ids:
            state_in = str((step_by_id.get(stage_id) or {}).get("state_in") or "").strip()
            if state_in:
                initial_state = state_in
                break

    terminal_states = _declared_text_list(
        blueprint.get("terminal_states") or blueprint.get("terminal_state")
    )
    if not terminal_states:
        for step in steps:
            if not _declared_bool(step.get("terminal"), default=False):
                continue
            state_out = str(step.get("state_out") or "").strip()
            if state_out and state_out not in terminal_states:
                terminal_states.append(state_out)

    covered_stage_ids = [
        str(item.get("main_chain_stage") or "").strip()
        for item in main_cases
        if str(item.get("main_chain_stage") or "").strip()
    ]
    covered_stage_set = set(covered_stage_ids)
    missing_required_stage_ids = [
        stage_id for stage_id in required_stage_ids if stage_id not in covered_stage_set
    ]

    transitions: list[dict[str, str]] = []
    state_conflicts: list[dict[str, str]] = []
    previous_target = ""
    for item in main_cases:
        source_state = str(_case_transition_value(item, "source_state") or "").strip()
        target_state = str(_case_transition_value(item, "target_state") or "").strip()
        stage_id = str(item.get("main_chain_stage") or "").strip()
        case_id = str(item.get("id") or "").strip()
        transitions.append(
            {
                "case_id": case_id,
                "stage_id": stage_id,
                "source_state": source_state,
                "target_state": target_state,
            }
        )
        if not source_state or not target_state:
            state_conflicts.append(
                {
                    "case_id": case_id,
                    "stage_id": stage_id,
                    "reason": "state_transition_missing",
                }
            )
        elif previous_target and previous_target != source_state:
            state_conflicts.append(
                {
                    "case_id": case_id,
                    "stage_id": stage_id,
                    "previous_target_state": previous_target,
                    "source_state": source_state,
                    "reason": "state_not_connected",
                }
            )
        if target_state:
            previous_target = target_state

    first_source_state = str(transitions[0].get("source_state") or "") if transitions else ""
    initial_state_matched = bool(initial_state and first_source_state == initial_state)
    reachable_states: set[str] = {initial_state} if initial_state else set()
    pending_edges = [
        (str(item.get("source_state") or ""), str(item.get("target_state") or ""))
        for item in transitions
        if item.get("source_state") and item.get("target_state")
    ]
    changed = True
    while changed:
        changed = False
        for source_state, target_state in pending_edges:
            if source_state in reachable_states and target_state not in reachable_states:
                reachable_states.add(target_state)
                changed = True
    terminal_state_reachable = bool(set(terminal_states) & reachable_states)

    failure_reasons: list[str] = []
    if not blueprint:
        failure_reasons.append("workflow_contract_missing")
    if blueprint and not required_stage_ids:
        failure_reasons.append("workflow_required_stages_missing")
    if blueprint and not initial_state:
        failure_reasons.append("workflow_initial_state_missing")
    if blueprint and not terminal_states:
        failure_reasons.append("workflow_terminal_states_missing")
    if missing_required_stage_ids:
        failure_reasons.append("required_stage_coverage_missing")
    if state_conflicts:
        failure_reasons.append("workflow_state_not_connected")
    if initial_state and not initial_state_matched:
        failure_reasons.append("workflow_initial_state_not_matched")
    if terminal_states and not terminal_state_reachable:
        failure_reasons.append("workflow_terminal_state_not_reachable")

    return {
        "contract_present": bool(blueprint),
        "blueprint_id": str(blueprint.get("workflow_id") or blueprint.get("id") or "").strip(),
        "declared_workflow_contract": _declared_workflow_contract_snapshot(
            blueprint,
            steps=steps,
            required_stage_ids=required_stage_ids,
            initial_state=initial_state,
            terminal_states=terminal_states,
        ),
        "required_stage_ids": required_stage_ids,
        "covered_stage_ids": covered_stage_ids,
        "missing_required_stage_ids": missing_required_stage_ids,
        "initial_state": initial_state,
        "first_source_state": first_source_state,
        "initial_state_matched": bool(initial_state_matched),
        "terminal_states": terminal_states,
        "reachable_states": sorted(reachable_states),
        "terminal_state_reachable": bool(terminal_state_reachable),
        "state_connected": not bool(state_conflicts),
        "state_conflicts": state_conflicts,
        "closure_satisfied": not bool(failure_reasons),
        "failure_reasons": failure_reasons,
    }


def selected_stage_state_conflicts(
    selected: list[tuple[str, str, dict[str, Any]]],
    *,
    stage_meta_by_key: dict[str, dict[str, Any]],
    case_id_fn: Callable[[dict[str, Any]], str] | None = None,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    previous_stage_key = ""
    previous_case_id = ""
    previous_target_state = ""
    resolve_case_id = case_id_fn or (lambda item: str(item.get("id") or ""))
    for stage_key, _stage_label, item in selected:
        step_meta = stage_meta_by_key.get(stage_key) or {}
        source_state = str(step_meta.get("state_in") or "").strip()
        target_state = str(step_meta.get("state_out") or "").strip()
        case_id = resolve_case_id(item)
        if previous_target_state and source_state and previous_target_state != source_state:
            conflicts.append(
                {
                    "prev_stage_key": previous_stage_key,
                    "curr_stage_key": str(stage_key),
                    "prev_case_id": previous_case_id,
                    "curr_case_id": case_id,
                    "prev_target_state": previous_target_state,
                    "curr_source_state": source_state,
                    "reason": "state_not_connected",
                }
            )
        previous_stage_key = str(stage_key)
        previous_case_id = case_id
        previous_target_state = target_state
    return conflicts


class MainChainExclusionRecorder:
    def __init__(
        self,
        records: list[dict[str, str]],
        *,
        signature_fn: Callable[[dict[str, Any]], str],
        case_id_fn: Callable[[dict[str, Any]], str],
        description_fn: Callable[[dict[str, Any]], str],
    ) -> None:
        self._records = records
        self._signature_fn = signature_fn
        self._case_id_fn = case_id_fn
        self._description_fn = description_fn

    def __call__(self, item: dict[str, Any], reason: str, *, stage_key: str = "") -> None:
        if not reason:
            return
        signature = self._signature_fn(item)
        if any(
            entry.get("signature") == signature and entry.get("reason") == reason
            for entry in self._records
        ):
            return
        self._records.append(
            {
                "case_id": _clip_text(self._case_id_fn(item), 40),
                "description": _clip_text(self._description_fn(item), 160),
                "stage_key": _clip_text(stage_key, 80),
                "reason": str(reason),
                "signature": signature,
            }
        )


def main_chain_goal_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(field) or "")
        for field in (
            "test_module",
            "description",
            "test_input",
            "expected_result",
        )
        if str(item.get(field) or "").strip()
    )


def main_chain_goal_action_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(field) or "")
        for field in (
            "test_module",
            "description",
            "test_input",
        )
        if str(item.get(field) or "").strip()
    )


def is_pure_ui_goal_text(text: str) -> bool:
    """识别仅验证文案或视觉样式、没有业务状态变化的用例目标。"""
    pure_ui_tokens = (
        "文案", "样式", "颜色", "字号", "字体", "间距", "边距", "布局", "对齐", "美术风格",
        "copy", "visual style", "color", "font", "spacing", "margin", "layout", "alignment",
    )
    business_action_tokens = (
        "新增", "创建", "选择", "设置", "编辑", "填写", "上传", "保存", "提交", "发布", "确认",
        "跳转", "进入", "点击", "删除", "回复", "点赞", "审核", "同步", "通知",
        "create", "select", "set", "edit", "fill", "upload", "save", "submit", "publish", "confirm",
        "navigate", "enter", "click", "delete", "reply", "like", "audit", "sync", "notify",
    )
    return bool(
        contains_any_token(text, pure_ui_tokens)
        and not contains_any_token(text, business_action_tokens)
    )


def workflow_transition_for_case(
    item: dict[str, Any],
    *,
    step_meta: dict[str, Any] | None = None,
    stage_label: str = "",
    workflow_blueprints_present: bool = False,
    destructive_action_tokens: Collection[str] = (),
    blocking_negative_tokens: Collection[str] = (),
    boundary_capacity_tokens: Collection[str] = (),
    analytics_tokens: Collection[str] = (),
) -> dict[str, Any]:
    meta = dict(step_meta or {})
    destructive = meta.get("destructive") is True
    blocking = meta.get("blocking") is True
    critical = meta.get("critical") is True
    stage_kind = str(meta.get("stage_kind") or "").strip().lower()
    source_state = str(meta.get("state_in") or "").strip()
    target_state = str(meta.get("state_out") or "").strip()
    path_type = str(meta.get("path_type") or "").strip().lower()
    can_advance = bool(
        meta.get("can_advance_main_flow") is True
        and path_type == "positive"
    )
    workflow_id = str(meta.get("workflow_id") or meta.get("blueprint_id") or "").strip()
    try:
        transition_confidence = max(0.0, min(1.0, float(meta.get("confidence") or 1.0)))
    except (TypeError, ValueError):
        transition_confidence = 0.0
    return {
        "workflow_id": workflow_id,
        "source_state": source_state,
        "action": _clip_text(
            meta.get("action") or stage_label,
            160,
            strip=True,
        ),
        "target_state": target_state,
        "path_type": path_type,
        "blocking": bool(blocking),
        "critical": bool(critical),
        "destructive": bool(destructive),
        "can_advance_main_flow": bool(can_advance),
        "state_transition_confidence": float(transition_confidence),
        "stage_kind": stage_kind,
    }


def is_display_only_workflow_text(
    text: str,
    *,
    display_only_tokens: Collection[str],
    downstream_visibility_tokens: Collection[str],
) -> bool:
    if not contains_any_token(text, display_only_tokens):
        return False
    if contains_any_token(text, downstream_visibility_tokens):
        return False
    workflow_action_tokens = (
        "新增",
        "创建",
        "添加",
        "选择",
        "设置",
        "预览",
        "保存",
        "提交",
        "确认",
        "跳转",
        "进入",
        "create",
        "add",
        "select",
        "set",
        "preview",
        "save",
        "submit",
        "confirm",
        "click",
        "open",
        "view",
        "navigate",
        "enter",
    )
    return not contains_any_token(text, workflow_action_tokens)
