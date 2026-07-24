"""优先级锚点与执行分组共享的通用行为语义。"""

from __future__ import annotations

from typing import Any


VALID_EXECUTION_GROUPS = {
    "main_smoke",
    "permission",
    "exception",
    "boundary",
    "independent_functional",
    "display",
}

GENERIC_NON_BLOCKING_BEHAVIOR_TOKENS = (
    "copy",
    "toast",
    "tooltip",
    "popup",
    "modal",
    "dialog",
    "badge",
    "layout",
    "style",
    "format",
    "sort",
    "sorting",
    "filter",
    "preview",
    "display",
    "placeholder",
    "editable",
    "复制",
    "提示文案",
    "弹窗",
    "弹层",
    "状态标识",
    "文案",
    "样式",
    "格式",
    "布局",
    "排序",
    "筛选",
    "预览",
    "占位",
    "可编辑",
)

GENERIC_STATE_OBSERVATION_TOKENS = (
    "remains pending",
    "status remains",
    "stays unchanged",
    "state remains",
    "still pending",
    "状态保持",
    "状态不变",
    "仍处于等待",
    "持续处于等待",
    "保持待处理",
    "保持审核中",
)

GENERIC_LOW_VALUE_TOKENS = tuple(
    dict.fromkeys(GENERIC_NON_BLOCKING_BEHAVIOR_TOKENS + GENERIC_STATE_OBSERVATION_TOKENS)
)

_GENERIC_BLOCKING_OUTCOME_TOKENS = (
    "cannot continue",
    "blocks access",
    "access blocked",
    "permission denied",
    "submit success",
    "submitted successfully",
    "publish success",
    "approval passed",
    "review approved",
    "generation completes",
    "result is generated",
    "无法继续",
    "阻止访问",
    "权限不足",
    "提交成功",
    "发布成功",
    "审核通过",
    "生成完成",
    "结果已生成",
)

_GENERIC_OUTPUT_OUTCOME_TOKENS = (
    "output becomes available",
    "complete output",
    "complete result",
    "result details",
    "result is generated",
    "generated result",
    "输出可用",
    "完整输出",
    "完整结果",
    "结果详情",
    "结果已生成",
    "生成结果",
)

_GENERIC_GROUP_TOKENS = {
    "permission": (
        "permission",
        "authorization",
        "unauthorized",
        "forbidden",
        "access denied",
        "auth failed",
        "权限",
        "无权限",
        "越权",
        "授权失败",
        "鉴权",
        "未登录",
    ),
    "exception": (
        "failure",
        "failed",
        "error",
        "timeout",
        "retry",
        "network unavailable",
        "失败",
        "异常",
        "错误",
        "超时",
        "重试",
        "网络不可用",
        "拒绝",
    ),
    "boundary": (
        "empty state",
        "no data",
        "maximum",
        "minimum",
        "at most",
        "at least",
        "more than",
        "less than",
        "upper limit",
        "lower limit",
        "format",
        "size",
        "boundary",
        "空状态",
        "无数据",
        "最大",
        "最小",
        "最多",
        "至少",
        "超过",
        "少于",
        "上限",
        "下限",
        "格式",
        "大小",
        "边界",
    ),
    "display": GENERIC_NON_BLOCKING_BEHAVIOR_TOKENS
    + (
        "list",
        "detail",
        "visible",
        "visibility",
        "列表",
        "详情",
        "展示",
        "可见",
    ),
}

_LOW_VALUE_PRIORITY_REASONS = {
    "boundary_or_low_risk_validation",
    "long_tail_or_supplemental",
    "non_critical_perf_or_ui",
    "completeness_only",
    "structural_p2_low_value_signal",
    "p2_cap_no_coverage_gain_without_hard_guard",
    "p2_cap_low_risk_only_covered_rules",
    "p2_cap_display_mapping_scenario",
}

_DISPLAY_PRIORITY_SOURCES = {
    "pure_ui_non_blocking_p2",
    "execution_plan_non_main_p0_demoted",
    "execution_plan_main_support_step_demoted",
}

_MAIN_CHAIN_STAGE_FAMILIES = {
    "entry": "workflow_entry",
    "commit": "workflow_commit",
    "downstream_visibility": "workflow_output",
    "completion_sync": "workflow_output",
}


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    normalized = _normalized(text)
    return any(token and token.lower() in normalized for token in tokens)


def _transition(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("workflow_transition")
    return dict(value) if isinstance(value, dict) else {}


def _normalized_values(value: Any) -> set[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return {_normalized(item) for item in values if _normalized(item)}


def case_execution_group(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    group = _normalized(item.get("execution_group"))
    return group if group in VALID_EXECUTION_GROUPS else ""


def case_stage_kind(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    return _normalized(item.get("main_chain_stage_kind") or _transition(item).get("stage_kind"))


def _priority_evidence(item: dict[str, Any] | None) -> tuple[set[str], set[str], dict[str, bool]]:
    if not isinstance(item, dict):
        return set(), set(), {}

    reasons = _normalized_values(item.get("priority_reasons"))
    sources = {_normalized(item.get("priority_decision_source"))}
    guards: dict[str, bool] = {}

    direct_guards = item.get("priority_guards")
    if isinstance(direct_guards, dict):
        guards.update({_normalized(key): bool(value) for key, value in direct_guards.items()})

    meta = item.get("meta")
    debug = meta.get("priority_debug") if isinstance(meta, dict) else None
    if isinstance(debug, dict):
        reasons.update(_normalized_values(debug.get("priority_reasons")))
        debug_guards = debug.get("priority_guards")
        if isinstance(debug_guards, dict):
            guards.update({_normalized(key): bool(value) for key, value in debug_guards.items()})

    sources.discard("")
    return reasons, sources, guards


def has_structured_blocking_priority_evidence(item: dict[str, Any] | None) -> bool:
    """只读模型/工作流原始结构化风险，不回读正文打分产生的 reason/guard。"""
    if not isinstance(item, dict):
        return False
    transition = _transition(item)
    direct = any(
        payload.get(field) is True
        for payload in (item, transition)
        for field in ("critical", "blocking", "destructive", "business_critical")
    )
    semantic = item.get("_semantic")
    semantic = dict(semantic) if isinstance(semantic, dict) else {}
    verified_risks = []
    for risk in semantic.get("risk_declarations") or []:
        if not isinstance(risk, dict) or risk.get("evidence_verified") is not True:
            continue
        try:
            confidence = float(risk.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence > 0.0:
            verified_risks.append(risk)
    return bool(
        direct
        or any(
            risk.get(field) is True
            for risk in verified_risks
            for field in ("critical", "blocking", "destructive")
        )
    )


def has_structured_low_value_priority_evidence(item: dict[str, Any] | None) -> bool:
    reasons, sources, _guards = _priority_evidence(item)
    return bool(
        reasons.intersection(_LOW_VALUE_PRIORITY_REASONS)
        or sources.intersection(_DISPLAY_PRIORITY_SOURCES)
    )


def has_cross_module_structure(item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    phase = _normalized(item.get("functional_phase"))
    modules = item.get("functional_interaction_modules")
    module_count = (
        len({str(value).strip() for value in modules if str(value).strip()})
        if isinstance(modules, (list, tuple, set))
        else 0
    )
    return bool(phase == "cross_module" and module_count >= 2)


def structured_anchor_family(item: dict[str, Any] | None) -> str:
    group = case_execution_group(item)
    stage_kind = case_stage_kind(item)
    if group == "permission":
        return "permission"
    if group == "main_smoke":
        return _MAIN_CHAIN_STAGE_FAMILIES.get(stage_kind, "main_workflow")
    if has_structured_blocking_priority_evidence(item):
        return "release_blocking"
    if has_cross_module_structure(item):
        return "cross_module"
    return ""


def has_structured_core_signal(item: dict[str, Any] | None) -> bool:
    group = case_execution_group(item)
    stage_kind = case_stage_kind(item)
    return bool(
        group in {"main_smoke", "permission"}
        or stage_kind in _MAIN_CHAIN_STAGE_FAMILIES
        or has_structured_blocking_priority_evidence(item)
        or has_cross_module_structure(item)
    )


def is_structured_non_blocking_detail(item: dict[str, Any] | None) -> bool:
    if has_structured_blocking_priority_evidence(item):
        return False
    group = case_execution_group(item)
    stage_kind = case_stage_kind(item)
    return bool(
        group == "display"
        or stage_kind == "preview"
        or has_structured_low_value_priority_evidence(item)
    )


def is_structured_output_anchor(item: dict[str, Any] | None) -> bool:
    return bool(
        case_execution_group(item) == "main_smoke"
        and case_stage_kind(item) in {"downstream_visibility", "completion_sync"}
    )


def has_generic_non_blocking_behavior(text: str) -> bool:
    return _contains_any(text, GENERIC_LOW_VALUE_TOKENS)


def has_generic_blocking_outcome(text: str) -> bool:
    return _contains_any(text, _GENERIC_BLOCKING_OUTCOME_TOKENS)


def has_generic_output_outcome(text: str) -> bool:
    return _contains_any(text, _GENERIC_OUTPUT_OUTCOME_TOKENS)


def infer_non_main_execution_group(item: dict[str, Any], text: str) -> str:
    explicit_group = case_execution_group(item)
    if explicit_group and explicit_group != "main_smoke":
        return explicit_group

    stage_kind = case_stage_kind(item)
    if stage_kind in {"preview", "downstream_visibility"}:
        return "display"
    if has_structured_low_value_priority_evidence(item):
        return "display"

    for group in ("permission", "exception", "boundary", "display"):
        if _contains_any(text, _GENERIC_GROUP_TOKENS[group]):
            return group
    return "independent_functional"


__all__ = [
    "GENERIC_LOW_VALUE_TOKENS",
    "case_execution_group",
    "case_stage_kind",
    "has_cross_module_structure",
    "has_generic_blocking_outcome",
    "has_generic_non_blocking_behavior",
    "has_generic_output_outcome",
    "has_structured_blocking_priority_evidence",
    "has_structured_core_signal",
    "has_structured_low_value_priority_evidence",
    "infer_non_main_execution_group",
    "is_structured_non_blocking_detail",
    "is_structured_output_anchor",
    "structured_anchor_family",
]
