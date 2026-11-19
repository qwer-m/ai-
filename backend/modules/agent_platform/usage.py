"""运行用量与持久自主预算之间的增量入账边界。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, inspect, literal, select, update

from core.db.model_defs import AgentNodeRun, AgentRun
from .autonomous_budget import interrupt_pending_budget


_USAGE_KEYS = ("attempted_requests", "requests", "input_tokens", "output_tokens", "total_tokens")


def interrupt_node_pending_requests(run: AgentRun, node_run: AgentNodeRun) -> None:
    """终态事务收尾当前持久预算；历史重试快照和实际用量不改写。"""

    # 预算游标之外的状态只保留原引用，取消不能复制或展开不可变领域产物。
    state = dict(node_run.sdk_state or {})
    checkpoint = state.get("action_checkpoint")
    if checkpoint and checkpoint.get("budget"):
        state["action_checkpoint"] = {**checkpoint, "budget": interrupt_pending_budget(checkpoint["budget"])}
    if state.get("budget_usage"):
        state["budget_usage"] = interrupt_pending_budget(state["budget_usage"])
    if state.get("items"):
        state["items"] = [{**item, "budget_usage": interrupt_pending_budget(item["budget_usage"])}
                          if item.get("budget_usage") else item for item in state["items"]]
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


def _action_usage_delta(run_id: int, node_run: AgentNodeRun):
    """两种持久化入口共用同一增量校验，不读取领域产物或历史调用。"""
    state = dict(node_run.sdk_state or {})
    cursor = state.get("action_usage")
    if cursor is None:
        # 只有经过工作流额度准入的自主节点才建立 Run 级计量边界。
        return
    checkpoint = dict(state.get("action_checkpoint") or {})
    if cursor["run_id"] != run_id or cursor["input_hash"] != checkpoint.get("input_hash"):
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
    return state, cursor, current, delta


def sync_action_usage(run: AgentRun, node_run: AgentNodeRun) -> None:
    """普通生命周期在已加载的业务上下文中入账，仍只累计未记录的增量。"""

    change = _action_usage_delta(run.id, node_run)
    if change is None:
        return
    state, cursor, current, delta = change
    context = dict(run.run_context or {})
    total = run_usage(run)
    context["usage"] = {key: total[key] + delta[key] for key in total}
    run.run_context = context
    state["action_usage"] = {**cursor, "recorded": current}
    node_run.sdk_state = state


def sync_action_usage_projection(db, run: AgentRun, node_run: AgentNodeRun) -> None:
    """热路径只更新同一权威 usage JSON 路径；调用方先持有 Run 锁且负责事务提交。"""

    if node_run.run_id != run.id or inspect(run).session is not db or inspect(node_run).session is not db:
        raise ValueError("投影入账必须绑定同一运行和同一事务的节点")
    change = _action_usage_delta(run.id, node_run)
    if change is None:
        return
    if inspect(run).attrs.run_context.history.has_changes():
        raise RuntimeError("投影入账不能覆盖尚未提交的业务上下文修改")
    state, cursor, current, delta = change
    with db.no_autoflush:
        # 只投影五个计数所在的对象，不将 artifacts、执行契约与领域正文读进控制事务。
        raw = db.scalar(select(AgentRun.run_context["usage"]).where(AgentRun.id == run.id).with_for_update()) or {}
        if not isinstance(raw, dict):
            raise ValueError("运行累计用量不是有效对象")
        total = {key: raw.get(key, 0) for key in _USAGE_KEYS}
        if any(type(value) is not int or value < 0 for value in total.values()):
            raise ValueError("运行累计用量缺少有效的非负计数")
        next_usage = {key: total[key] + delta[key] for key in total}
        # JSON_EXTRACT 保留对象类型；MySQL 与 SQLite 均不能把 JSON 字符串当作 usage 对象。
        encoded = json.dumps(next_usage, ensure_ascii=False, separators=(",", ":"))
        db.execute(update(AgentRun).where(AgentRun.id == run.id).values(
            run_context=func.json_set(AgentRun.run_context, "$.usage", func.json_extract(literal(encoded), "$")),
        ).execution_options(synchronize_session=False))
        state["action_usage"] = {**cursor, "recorded": current}
        node_run.sdk_state = state
        db.add(node_run)
        # SQL 路径更新后废弃已有 ORM 副本；后续业务修改必须回读真实累计值再提交。
        db.expire(run, ["run_context"])
