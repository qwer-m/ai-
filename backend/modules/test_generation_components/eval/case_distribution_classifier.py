from __future__ import annotations

from typing import Any

from ..postprocess.case_access import case_flat_text, case_id, case_step_lines

CASE_TYPE_FLOW = "FLOW"
CASE_TYPE_STATE = "STATE"
CASE_TYPE_UI = "UI"
CASE_TYPES = (CASE_TYPE_FLOW, CASE_TYPE_STATE, CASE_TYPE_UI)

_FLOW_TOKENS = (
    "流程",
    "路径",
    "进入",
    "返回",
    "跳转",
    "完成",
    "提交",
    "闭环",
    "继续",
    "flow",
    "journey",
    "submit",
    "complete",
    "continue",
)
_FLOW_PROGRESS_TOKENS = (
    "提交",
    "完成",
    "继续",
    "下一步",
    "下一阶段",
    "进入下一阶段",
    "闭环",
    "submit",
    "complete",
    "continue",
    "finish",
    "next step",
    "next stage",
    "close the loop",
)
_CROSS_PAGE_TOKENS = (
    "跳转",
    "返回",
    "进入",
    "跨页",
    "跨页面",
    "跨模块",
    "导航",
    "切换页面",
    "cross-page",
    "cross page",
    "cross module",
    "page jump",
    "navigation",
    "navigate",
    "open details",
    "re-enter",
    "reenter",
)
_FLOW_SEQUENCE_PATTERNS = (
    ("返回", "再进入"),
    ("上一步", "下一步"),
    ("return", "re-enter"),
    ("return", "reenter"),
    ("previous", "next"),
)
_STATE_TOKENS = (
    "状态",
    "切换",
    "加载",
    "刷新",
    "缓存",
    "接口",
    "请求",
    "数据",
    "恢复",
    "中断",
    "重试",
    "并发",
    "一致",
    "上下文",
    "保持",
    "保留",
    "当前节点",
    "state",
    "status",
    "transition",
    "cache",
    "request",
    "data",
    "context",
    "consistent",
    "restore",
    "retry",
)
_STATE_GUARD_TOKENS = (
    "不丢上下文",
    "不错误推进",
    "不标记完成",
    "保持当前节点",
    "保持当前页",
    "保持当前状态",
    "context preserved",
    "keep current node",
    "keep current page",
    "keep current state",
    "no wrong progression",
    "no context leak",
    "no state leak",
    "no cross-module leak",
)
_UI_TOKENS = (
    "按钮",
    "文案",
    "样式",
    "颜色",
    "展示",
    "提示",
    "弹窗",
    "图标",
    "非空",
    "页面元素",
    "标题",
    "字段",
    "布局",
    "列表",
    "表格",
    "排序",
    "筛选",
    "显隐",
    "占位",
    "ui",
    "button",
    "copy",
    "style",
    "color",
    "display",
    "tooltip",
    "popup",
    "icon",
    "title",
    "field",
    "layout",
    "list",
    "table",
    "sort",
    "filter",
    "placeholder",
)


def _flatten_case_text(case: dict[str, Any]) -> str:
    return case_flat_text(
        case,
        fields=("description", "test_module", "steps", "expected_result", "test_input"),
        separator=" ",
        lower=True,
    )


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in text for token in tokens)


def _count_step_tokens(text: str, patterns: tuple[tuple[str, str], ...]) -> bool:
    return any(all(token.lower() in text for token in pattern) for pattern in patterns)


def _semantic_payload(case: dict[str, Any]) -> dict[str, Any]:
    value = case.get("_semantic")
    return dict(value) if isinstance(value, dict) else {}


def _verified_semantic_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        dict(item)
        for item in value
        if isinstance(item, dict) and item.get("evidence_verified") is True
    ]


def _workflow_transition(case: dict[str, Any]) -> dict[str, Any]:
    value = case.get("workflow_transition")
    return dict(value) if isinstance(value, dict) else {}


def _transition_state_pair(transition: dict[str, Any]) -> tuple[str, str]:
    source = str(transition.get("source_state") or transition.get("state_in") or "").strip()
    target = str(transition.get("target_state") or transition.get("state_out") or "").strip()
    return source, target


def _has_verified_semantic_state(case: dict[str, Any]) -> bool:
    semantic = _semantic_payload(case)
    return any(
        _verified_semantic_items(semantic.get(field))
        for field in ("precondition_states", "required_states", "produced_states")
    )


def _has_verified_workflow_stage(case: dict[str, Any]) -> bool:
    semantic = _semantic_payload(case)
    return bool(_verified_semantic_items(semantic.get("workflow_stage_candidates")))


def _has_structured_cross_module_signal(case: dict[str, Any]) -> bool:
    semantic = _semantic_payload(case)
    interaction_ids = semantic.get("interaction_ids")
    if not (
        isinstance(interaction_ids, list)
        and any(str(item or "").strip() for item in interaction_ids)
    ):
        return False
    modules = _verified_semantic_items(semantic.get("module_candidates"))
    roles = {str(item.get("role") or "").strip().lower() for item in modules}
    return "source" in roles and "target" in roles


def _structured_distribution(case: dict[str, Any]) -> str:
    execution_group = str(case.get("execution_group") or "").strip().lower()
    if execution_group == "main_smoke":
        return CASE_TYPE_FLOW
    if execution_group == "display":
        return CASE_TYPE_UI

    transition = _workflow_transition(case)
    source_state, target_state = _transition_state_pair(transition)
    can_advance = transition.get("can_advance_main_flow") is True
    if can_advance and source_state and target_state and source_state != target_state:
        return CASE_TYPE_FLOW
    if _has_verified_workflow_stage(case) and can_advance:
        return CASE_TYPE_FLOW
    if source_state or target_state or _has_verified_semantic_state(case):
        return CASE_TYPE_STATE
    return ""


def classify_case_distribution(case: dict[str, Any]) -> str:
    structured = _structured_distribution(case)
    if structured:
        return structured

    steps = case_step_lines(case)
    step_count = len(steps)
    step_text = " ".join(steps).lower()
    text = _flatten_case_text(case)

    has_cross_page = _contains_any(text, _CROSS_PAGE_TOKENS)
    has_state_transition = _contains_any(text, _STATE_TOKENS)
    has_state_guard = _contains_any(text, _STATE_GUARD_TOKENS)
    has_flow_token = _contains_any(text, _FLOW_TOKENS)
    has_flow_progress = _contains_any(text, _FLOW_PROGRESS_TOKENS)
    has_flow_sequence = _count_step_tokens(step_text or text, _FLOW_SEQUENCE_PATTERNS)
    has_ui_token = _contains_any(text, _UI_TOKENS)

    strong_flow_hit = bool(
        has_flow_progress
        and (
            has_flow_sequence
            or (has_cross_page and step_count >= 2)
            or step_count >= 3
            or (has_flow_token and step_count >= 2)
        )
    )
    state_hit = bool(has_state_transition or has_state_guard)

    if has_state_guard and not strong_flow_hit:
        return CASE_TYPE_STATE
    if strong_flow_hit:
        return CASE_TYPE_FLOW
    if state_hit:
        return CASE_TYPE_STATE
    if has_ui_token:
        return CASE_TYPE_UI
    return CASE_TYPE_UI


def classify_case_distributions(cases: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        resolved_case_id = case_id(case) or f"__index_{index}"
        mapping[resolved_case_id] = classify_case_distribution(case)
    return mapping


def summarize_case_distribution(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        CASE_TYPE_FLOW: 0,
        CASE_TYPE_STATE: 0,
        CASE_TYPE_UI: 0,
    }
    for case_type in classify_case_distributions(cases).values():
        counts[case_type] = int(counts.get(case_type, 0)) + 1
    return counts


def summarize_case_structure_signals(cases: list[dict[str, Any]]) -> dict[str, int]:
    cross_page = 0
    multi_step = 0
    state_transition = 0
    for case in cases:
        if not isinstance(case, dict):
            continue
        steps = case_step_lines(case)
        text = _flatten_case_text(case)
        if len(steps) >= 3:
            multi_step += 1
        if _has_structured_cross_module_signal(case) or _contains_any(text, _CROSS_PAGE_TOKENS):
            cross_page += 1
        transition = _workflow_transition(case)
        source_state, target_state = _transition_state_pair(transition)
        if (
            source_state
            or target_state
            or _has_verified_semantic_state(case)
            or _contains_any(text, _STATE_TOKENS)
            or _contains_any(text, _STATE_GUARD_TOKENS)
        ):
            state_transition += 1
    return {
        "cross_page_case_count": int(cross_page),
        "multi_step_case_count": int(multi_step),
        "state_transition_case_count": int(state_transition),
    }
