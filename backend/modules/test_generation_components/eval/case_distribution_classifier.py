from __future__ import annotations

import re
from typing import Any

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
    "学习",
    "练习",
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
    "答题",
    "练习",
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
    "不串课文",
    "不串单元",
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
    "no cross-unit leak",
    "no cross-lesson leak",
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


def _normalize_steps(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n;；。]", value) if item.strip()]
    return []


def _flatten_case_text(case: dict[str, Any]) -> str:
    steps = _normalize_steps(case.get("steps"))
    text_parts = [
        str(case.get("description") or ""),
        str(case.get("test_module") or case.get("module") or ""),
        " ".join(steps),
        str(case.get("expected_result") or ""),
        str(case.get("test_input") or ""),
    ]
    return " ".join(part for part in text_parts if part).lower()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in text for token in tokens)


def _count_step_tokens(text: str, patterns: tuple[tuple[str, str], ...]) -> bool:
    return any(all(token.lower() in text for token in pattern) for pattern in patterns)


def classify_case_distribution(case: dict[str, Any]) -> str:
    steps = _normalize_steps(case.get("steps"))
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
        case_id = str(case.get("id") or case.get("case_id") or case.get("caseId") or "").strip()
        if not case_id:
            case_id = f"__index_{index}"
        mapping[case_id] = classify_case_distribution(case)
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
        steps = _normalize_steps(case.get("steps"))
        text = _flatten_case_text(case)
        if len(steps) >= 3:
            multi_step += 1
        if _contains_any(text, _CROSS_PAGE_TOKENS):
            cross_page += 1
        if _contains_any(text, _STATE_TOKENS) or _contains_any(text, _STATE_GUARD_TOKENS):
            state_transition += 1
    return {
        "cross_page_case_count": int(cross_page),
        "multi_step_case_count": int(multi_step),
        "state_transition_case_count": int(state_transition),
    }
