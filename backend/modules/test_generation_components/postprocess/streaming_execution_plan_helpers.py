from __future__ import annotations

from typing import Any, Callable, Collection

from ..control.actor_roles import normalize_actor_role as normalize_actor_role_value
from .streaming_execution_plan_blueprints import (
    main_chain_stages_from_blueprints,
    pattern_match_score,
    stage_match_patterns,
    workflow_blueprint_source_label,
)
from .streaming_execution_plan_contracts import (
    CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX_BY_STAGE,
    DEFAULT_CONTRACT_MATERIALIZED_EXPECTED_RESULT_SUFFIX,
    contract_materialized_expected_result,
    is_internal_state_text,
    materialize_workflow_contract_case,
    public_contract_module_label,
    workflow_bridge_case,
)
from .streaming_execution_plan_stage_inference import (
    contains_any_token,
    infer_workflow_phase,
    infer_workflow_stage_kind,
    stage_kind_compatible,
    token_hit,
)
from .streaming_execution_plan_derived_workflow import (
    DERIVED_WORKFLOW_ACTION_TOKENS,
    DERIVED_WORKFLOW_BOUNDARY_TOKENS,
    DERIVED_WORKFLOW_DISPLAY_ONLY_PENALTY_TOKENS,
    DERIVED_WORKFLOW_STATE_TOKENS,
    derived_workflow_candidate_buckets,
    derived_workflow_selected_for_closure,
    derived_workflow_steps_from_selected,
    derive_workflow_blueprint_from_current_cases,
    select_derived_workflow_candidates,
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
    main_chain_state_overrides_for_current_generation,
    priority_rank,
    session_key_for_role,
    setup_hint,
)

def main_chain_closure_status(
    selected: list[tuple[str, str, dict[str, Any]]],
    *,
    stage_meta_by_key: dict[str, dict[str, Any]],
    source: str,
) -> tuple[bool, str, list[str]]:
    stage_kinds: list[str] = []
    for stage_key, stage_label, item in selected:
        meta = stage_meta_by_key.get(stage_key) or {}
        text = " ".join(
            [
                execution_case_text(item),
                str(stage_label or ""),
                str(meta.get("label") or ""),
                str(meta.get("action") or ""),
                str(meta.get("assertion") or ""),
                str(meta.get("state_out") or ""),
                str(meta.get("state_out") or "").replace("_", " "),
            ]
        )
        explicit_stage_kind = str(meta.get("stage_kind") or "").strip().lower()
        stage_kinds.append(explicit_stage_kind or infer_workflow_stage_kind(text))
    if len(stage_kinds) < 2:
        return False, "main_chain_too_short", stage_kinds
    has_commit = "commit" in stage_kinds
    first_commit_index = stage_kinds.index("commit") if has_commit else -1
    has_post_commit_downstream = bool(
        has_commit
        and any(
            kind in {"downstream_visibility", "consume", "completion_sync"}
            for kind in stage_kinds[first_commit_index + 1:]
        )
    )
    has_configure = any(kind in {"entry", "configure", "preview"} for kind in stage_kinds)
    has_pre_commit_consume = bool(
        first_commit_index > 0
        and any(kind == "consume" for kind in stage_kinds[:first_commit_index])
    )
    if not has_commit:
        return False, "missing_commit_success_step", stage_kinds
    if not has_post_commit_downstream:
        return False, "missing_downstream_visibility_or_consume_step", stage_kinds
    if source == "current_generation_cases" and not (has_configure or has_pre_commit_consume):
        return False, "missing_configure_or_entry_step", stage_kinds
    return True, "", stage_kinds


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


_DEFAULT_MAIN_CHAIN_EXCLUSION_TOKEN_TUPLES: dict[str, tuple[str, ...]] = {
    "analytics_tokens": (
        "埋点",
        "上报",
        "曝光",
        "停留时间",
        "pv",
        "uv",
        "tracking",
        "analytics",
        "event",
    ),
    "destructive_action_tokens": (
        "删除",
        "下架",
        "撤销",
        "作废",
        "取消发布",
        "delete",
        "remove",
        "unpublish",
        "archive",
        "deactivate",
    ),
    "blocking_negative_tokens": (
        "失败",
        "异常",
        "超时",
        "错误",
        "拒绝",
        "不通过",
        "不可点击",
        "不可操作",
        "置灰",
        "阻止",
        "无法",
        "不能",
        "不允许",
        "不进入",
        "不生成",
        "不保存",
        "failure",
        "failed",
        "timeout",
        "error",
        "invalid",
        "blocked",
        "cannot",
        "not allowed",
        "not saved",
        "返回按钮",
        "返回上一级",
        "放弃编辑",
        "放弃",
        "back button",
        "return button",
        "discard",
        "abort",
    ),
    "boundary_capacity_tokens": (
        "边界",
        "上限",
        "下限",
        "最多",
        "最少",
        "容量不足",
        "学不完",
        "课程设置过少",
        "时间冲突",
        "冲突",
        "boundary",
        "limit",
        "capacity",
        "conflict",
        "too few",
        "too many",
    ),
    "display_only_tokens": (
        "文案",
        "样式",
        "布局",
        "标题",
        "排序",
        "筛选",
        "列表",
        "卡片",
        "弹窗",
        "copy",
        "style",
        "layout",
        "title",
        "sorting",
        "filter",
        "list",
        "card",
        "popup",
        "图标",
        "时间",
        "头像",
        "昵称",
        "标识",
        "标签",
        "按钮",
        "字段",
        "icon",
        "time",
        "avatar",
        "nickname",
        "badge",
        "label",
        "tag",
        "button",
        "field",
    ),
    "downstream_visibility_tokens": (
        "新增",
        "新计划",
        "同步",
        "生效",
        "最新",
        "进度更新",
        "状态同步",
        "new",
        "created",
        "sync",
        "synced",
        "visible",
        "effective",
        "latest",
        "updated",
    ),
}


def default_main_chain_exclusion_token_sets() -> dict[str, set[str]]:
    return {
        name: set(tokens)
        for name, tokens in _DEFAULT_MAIN_CHAIN_EXCLUSION_TOKEN_TUPLES.items()
    }


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
    text = " ".join(
        [
            execution_case_text(item),
            str(stage_label or ""),
            str(meta.get("label") or ""),
            str(meta.get("action") or ""),
            str(meta.get("assertion") or ""),
            str(meta.get("state_out") or ""),
            str(meta.get("state_out") or "").replace("_", " "),
        ]
    )
    destructive = bool(contains_any_token(text, destructive_action_tokens))
    blocking = bool(
        contains_any_token(text, blocking_negative_tokens)
        or contains_any_token(text, boundary_capacity_tokens)
        or contains_any_token(text, analytics_tokens)
    )
    stage_kind = str(meta.get("stage_kind") or "").strip().lower() or infer_workflow_stage_kind(text)
    source_state = str(meta.get("state_in") or "").strip()
    target_state = str(meta.get("state_out") or "").strip()
    if not source_state:
        phase = infer_workflow_phase(text)
        source_state = {
            10: "entry_ready",
            20: "workflow_started",
            30: "workflow_edit_ready",
            50: "workflow_configured",
            60: "workflow_ready_to_commit",
            70: "committed",
            80: "downstream_visible",
        }.get(phase, "prepared")
    if not target_state:
        target_state = {
            "entry": "workflow_entered",
            "configure": "workflow_configured",
            "preview": "workflow_preview_ready",
            "commit": "workflow_committed",
            "downstream_visibility": "downstream_visible",
            "consume": "workflow_consumed",
            "completion_sync": "completion_synced",
        }.get(stage_kind, "prepared")
    path_type = "positive" if not (blocking or destructive) else "negative"
    can_advance = bool(path_type == "positive" and stage_kind != "unknown")
    workflow_id = str(meta.get("workflow_id") or meta.get("blueprint_id") or "").strip()
    transition_confidence = 0.9 if workflow_blueprints_present else 0.35
    return {
        "workflow_id": workflow_id,
        "source_state": source_state,
        "action": _clip_text(
            meta.get("action") or stage_label or item.get("description"),
            160,
            strip=True,
        ),
        "target_state": target_state,
        "path_type": path_type,
        "blocking": bool(blocking),
        "destructive": bool(destructive),
        "can_advance_main_flow": bool(can_advance),
        "state_transition_confidence": float(transition_confidence),
        "stage_kind": stage_kind,
    }


def main_chain_exclusion_reason(
    item: dict[str, Any],
    *,
    step_meta: dict[str, Any] | None = None,
    stage_label: str = "",
    workflow_blueprints_present: bool = False,
    analytics_tokens: Collection[str] = (),
    destructive_action_tokens: Collection[str] = (),
    boundary_capacity_tokens: Collection[str] = (),
    blocking_negative_tokens: Collection[str] = (),
    display_only_tokens: Collection[str] = (),
    downstream_visibility_tokens: Collection[str] = (),
    reasoning_leakage_fn: Callable[[dict[str, Any]], bool] | None = None,
    semantic_alignment_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    action_support_conflict_fn: Callable[[dict[str, Any]], str] | None = None,
) -> str:
    text = execution_case_text(item)
    if not text:
        return "empty_text"
    if reasoning_leakage_fn is not None and reasoning_leakage_fn(item):
        return "reasoning_leakage"
    if contains_any_token(text, analytics_tokens):
        return "analytics"
    if contains_any_token(text, destructive_action_tokens):
        return "destructive_action"
    if contains_any_token(text, boundary_capacity_tokens):
        return "boundary_capacity"
    if contains_any_token(text, blocking_negative_tokens):
        return "blocking_negative"
    meta = dict(step_meta or {})

    def _display_only_overridden_by_supported_stage_action() -> bool:
        expected_stage_kind = str(meta.get("stage_kind") or "").strip().lower()
        if not expected_stage_kind or action_support_conflict_fn is None:
            return False
        semantic_probe = dict(item)
        semantic_probe["execution_group"] = "main_smoke"
        semantic_probe["main_chain_stage_kind"] = expected_stage_kind
        semantic_probe["main_chain_stage_label"] = str(meta.get("label") or stage_label or "").strip()
        semantic_probe["action"] = str(meta.get("action") or stage_label or "").strip()
        semantic_probe["main_chain_stage_module"] = str(meta.get("module") or "").strip()
        semantic_probe["main_chain_stage_assertion"] = str(meta.get("assertion") or "").strip()
        semantic_probe["main_chain_stage_description"] = str(meta.get("description") or "").strip()
        semantic_probe["main_chain_stage_state_in"] = str(meta.get("state_in") or "").strip()
        semantic_probe["main_chain_stage_state_out"] = str(meta.get("state_out") or "").strip()
        semantic_probe["main_chain_stage_keywords"] = [
            str(keyword).strip()
            for keyword in (meta.get("match_keywords") or meta.get("keywords") or meta.get("aliases") or [])
            if str(keyword).strip()
        ]
        semantic_probe["main_chain_stage_evidence"] = [
            str(evidence).strip()
            for evidence in (meta.get("evidence") or [])
            if str(evidence).strip()
        ]
        return not bool(action_support_conflict_fn(semantic_probe))

    goal_text = main_chain_goal_text(item)
    if goal_text and is_pure_ui_goal_text(goal_text):
        return "display_only"
    goal_downstream_tokens = {
        token
        for token in downstream_visibility_tokens
        if str(token).strip().lower()
        not in {"visible", "visibility", "display", "displayed", "show", "shown"}
    }
    if goal_text and is_display_only_workflow_text(
        goal_text,
        display_only_tokens=display_only_tokens,
        downstream_visibility_tokens=goal_downstream_tokens,
    ):
        if not _display_only_overridden_by_supported_stage_action():
            return "display_only"
    if is_display_only_workflow_text(
        text,
        display_only_tokens=display_only_tokens,
        downstream_visibility_tokens=downstream_visibility_tokens,
    ):
        if not _display_only_overridden_by_supported_stage_action():
            return "display_only"
    transition = workflow_transition_for_case(
        item,
        step_meta=meta,
        stage_label=stage_label,
        workflow_blueprints_present=workflow_blueprints_present,
        destructive_action_tokens=destructive_action_tokens,
        blocking_negative_tokens=blocking_negative_tokens,
        boundary_capacity_tokens=boundary_capacity_tokens,
        analytics_tokens=analytics_tokens,
    )
    if not bool(transition.get("can_advance_main_flow")):
        return "non_advancing_transition"
    expected_stage_kind = str(transition.get("stage_kind") or "").strip().lower()
    goal_action_text = main_chain_goal_action_text(item)
    goal_stage_kind = infer_workflow_stage_kind(goal_action_text or goal_text) if goal_text else "unknown"
    goal_stage_kind_mismatch = bool(
        workflow_blueprints_present and not stage_kind_compatible(expected_stage_kind, goal_stage_kind)
    )
    semantic_probe = dict(item)
    semantic_probe["execution_group"] = "main_smoke"
    semantic_probe["main_chain_stage_kind"] = expected_stage_kind
    semantic_probe["main_chain_stage_label"] = str(meta.get("label") or stage_label or "").strip()
    semantic_probe["action"] = str(transition.get("action") or "").strip()
    semantic_probe["main_chain_stage_module"] = str(meta.get("module") or "").strip()
    semantic_probe["main_chain_stage_assertion"] = str(meta.get("assertion") or "").strip()
    semantic_probe["main_chain_stage_description"] = str(meta.get("description") or "").strip()
    semantic_probe["main_chain_stage_state_in"] = str(meta.get("state_in") or "").strip()
    semantic_probe["main_chain_stage_state_out"] = str(meta.get("state_out") or "").strip()
    semantic_probe["main_chain_stage_keywords"] = [
        str(keyword).strip()
        for keyword in (meta.get("match_keywords") or meta.get("keywords") or meta.get("aliases") or [])
        if str(keyword).strip()
    ]
    semantic_probe["main_chain_stage_evidence"] = [
        str(evidence).strip()
        for evidence in (meta.get("evidence") or [])
        if str(evidence).strip()
    ]
    if semantic_alignment_fn is not None:
        semantic_conflicts = semantic_alignment_fn([semantic_probe])
        if semantic_conflicts:
            return str(semantic_conflicts[0].get("reason") or "main_chain_semantic_conflict")
    stage_action_supported = False
    if action_support_conflict_fn is not None:
        action_support_reason = action_support_conflict_fn(semantic_probe)
        if action_support_reason:
            return action_support_reason
        stage_action_supported = True
    if goal_stage_kind_mismatch and not stage_action_supported:
        return "goal_stage_kind_not_compatible_with_blueprint"
    return ""


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
