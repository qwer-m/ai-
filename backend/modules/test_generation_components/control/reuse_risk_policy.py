from __future__ import annotations

from typing import Any


_REUSE_RISK_PATTERNS: dict[str, tuple[str, ...]] = {
    "wrong_return_target_risk": (
        "返回目标",
        "返回地址",
        "回首页",
        "回列表",
        "回原",
        "回旧",
        "回到原",
        "返回原",
        "跳回",
        "退出后",
        "return target",
        "return path",
        "return to",
        "back to",
        "return home",
        "return list",
        "wrong return",
    ),
    "legacy_behavior_risk": (
        "遗留",
        "旧版",
        "旧逻辑",
        "旧行为",
        "原有行为",
        "废弃",
        "过时",
        "legacy",
        "obsolete",
        "deprecated",
        "legacy redirect",
        "residual behavior",
    ),
    "shared_page_residual_risk": (
        "共享页面",
        "共用页面",
        "复用页面",
        "已有页面",
        "原页面",
        "共享入口",
        "共用入口",
        "shared page",
        "existing page",
        "shared entry",
    ),
    "shared_flow_residual_risk": (
        "共享流程",
        "共用流程",
        "复用流程",
        "原流程",
        "已有流程",
        "原模块",
        "已有模块",
        "跨模块污染",
        "上下文污染",
        "状态污染",
        "路由污染",
        "串流程",
        "串逻辑",
        "shared flow",
        "wrong progression",
        "context leak",
        "state leak",
        "route leak",
        "cross-module leak",
        "cross-entity leak",
    ),
}

_REUSE_RISK_DESCRIPTIONS = {
    "wrong_return_target_risk": (
        "wrong_return_target_risk: verify reused flow returns to the current target "
        "instead of a legacy destination."
    ),
    "legacy_behavior_risk": (
        "legacy_behavior_risk: verify reused capability does not retain obsolete "
        "actions, content, or side effects."
    ),
    "shared_page_residual_risk": (
        "shared_page_residual_risk: verify a shared surface does not leak legacy "
        "entry or exit behavior into the current module."
    ),
    "shared_flow_residual_risk": (
        "shared_flow_residual_risk: verify reused flow does not leak state, routing, "
        "or context across modules or entities."
    ),
}


def extract_reuse_risks(*parts: Any, default_shared_flow: bool = False) -> list[str]:
    """从复用描述中提取跨领域风险，不依赖具体业务对象名称。"""
    merged = " ".join(str(part or "") for part in parts).strip().lower()
    output: list[str] = []
    if merged:
        for risk_key, markers in _REUSE_RISK_PATTERNS.items():
            if any(marker.lower() in merged for marker in markers):
                output.append(_REUSE_RISK_DESCRIPTIONS[risk_key])
    if not output and default_shared_flow:
        output.append(_REUSE_RISK_DESCRIPTIONS["shared_flow_residual_risk"])
    return output


__all__ = ["extract_reuse_risks"]
