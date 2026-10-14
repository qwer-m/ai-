"""运行用量与持久自主预算之间的增量入账边界。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.db.model_defs import AgentNodeRun, AgentRun
from .autonomous_budget import interrupt_pending_budget


_USAGE_KEYS = ("attempted_requests", "requests", "input_tokens", "output_tokens", "total_tokens")


def interrupt_node_pending_requests(run: AgentRun, node_run: AgentNodeRun) -> None:
    """终态事务收尾当前持久预算；历史重试快照和实际用量不改写。"""

    state = deepcopy(node_run.sdk_state or {})
    checkpoint = state.get("action_checkpoint")
    if checkpoint and checkpoint.get("budget"):
        checkpoint["budget"] = interrupt_pending_budget(checkpoint["budget"])
    if state.get("budget_usage"):
        state["budget_usage"] = interrupt_pending_budget(state["budget_usage"])
    for item in state.get("items") or []:
        if item.get("budget_usage"):
            item["budget_usage"] = interrupt_pending_budget(item["budget_usage"])
    if state != (node_run.sdk_state or {}):
        node_run.sdk_state = state
        # 与平时检查点保存共用增量游标，不能在终态再次累计已记账用量。
        sync_action_usage(run, node_run)


def run_usage(run: AgentRun) -> dict[str, int]:
    raw = dict(dict(run.run_context or {}).get("usage") or {})
    return {key: int(raw.get(key) or 0) for key in _USAGE_KEYS}


def action_budget_usage(budget: dict[str, Any]) -> dict[str, int]:
    """响应 Token 使用报告值，请求尝试数使用已准入账本，不能把预留 Token 当实耗。"""
    reported = dict(budget.get("reported_usage") or {})
    values = {"attempted_requests": dict(budget.get("charged") or {}).get("attempted_requests"),
              **{key: reported.get(key) for key in _USAGE_KEYS if key != "attempted_requests"}}
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise ValueError("自主预算缺少有效的累计实际用量")
    return values


def initialize_action_usage(
    run: AgentRun, node_run: AgentNodeRun, *, baseline: dict[str, int],
) -> None:
    """一次运行内共享同一累计游标；复制到新 Run 时历史消费只作为基线。"""
    state = dict(node_run.sdk_state or {})
    recorded = state.get("action_usage")
    if recorded is not None and recorded.get("run_id") == run.id:
        return
    if set(baseline) != set(_USAGE_KEYS) or any(type(value) is not int or value < 0 for value in baseline.values()):
        raise ValueError("自主用量入账基线无效")
    checkpoint = dict(state.get("action_checkpoint") or {})
    state["action_usage"] = {
        "run_id": run.id, "input_hash": checkpoint["input_hash"], "recorded": dict(baseline),
    }
    node_run.sdk_state = state


def sync_action_usage(run: AgentRun, node_run: AgentNodeRun) -> None:
    """随预算检查点原子入账；失败、重复保存及节点恢复均只累计未记账增量。"""
    state = dict(node_run.sdk_state or {})
    cursor = state.get("action_usage")
    if cursor is None:
        # 只有经过工作流额度准入的自主节点才建立 Run 级计量边界。
        return
    checkpoint = dict(state.get("action_checkpoint") or {})
    if cursor["run_id"] != run.id or cursor["input_hash"] != checkpoint.get("input_hash"):
        raise ValueError("自主用量游标与当前运行或节点输入不一致")
    current = action_budget_usage(checkpoint["budget"])
    previous = cursor["recorded"]
    if set(previous) != set(current) or any(type(value) is not int or value < 0 for value in previous.values()):
        raise ValueError("自主用量游标计数无效")
    delta = {key: current[key] - previous[key] for key in current}
    if any(value < 0 for value in delta.values()):
        raise ValueError("自主预算累计用量倒退，禁止对已记录消费退款")
    if not any(delta.values()):
        return
    context = dict(run.run_context or {})
    total = run_usage(run)
    context["usage"] = {key: total[key] + delta[key] for key in total}
    run.run_context = context
    state["action_usage"] = {**cursor, "recorded": current}
    node_run.sdk_state = state
