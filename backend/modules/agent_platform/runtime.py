from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import time
from typing import Any

from jsonschema import validate
from agents.exceptions import ModelBehaviorError
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentApproval,
    AgentNodeRun,
    AgentRun,
)
from core.settings.config import settings
from .contracts import WorkflowGraph, WorkflowNode
from .registry import ToolExecutionContext, runtime_registry_signature, tool_registry
from .repository import AgentPlatformRepository
from .sdk_adapter import run_agent


RUN_LEASE_SECONDS = 3900


class _RunCancelled(RuntimeError):
    """运行已被外部取消，不应进入失败处理。"""


class _RunQuotaExceeded(RuntimeError):
    """单次 Agent Run 已达到平台或调用方设置的硬额度。"""


def _now() -> datetime:
    return datetime.utcnow()


def _event(
    repo: AgentPlatformRepository,
    run: AgentRun,
    event_type: str,
    payload: dict[str, Any],
    *,
    node_run: AgentNodeRun | None = None,
) -> None:
    repo.append_event(
        run_id=run.id,
        node_run_id=node_run.id if node_run else None,
        event_type=event_type,
        payload=payload,
    )


def _refresh_run_is_cancelled(
    repo: AgentPlatformRepository,
    run: AgentRun,
) -> bool:
    repo.refresh(run)
    return run.status == "cancelled"


def _mark_node_cancelled(
    repo: AgentPlatformRepository,
    run: AgentRun,
    node_run: AgentNodeRun,
    *,
    output_payload: dict[str, Any] | None = None,
    sdk_state: dict[str, Any] | None = None,
) -> None:
    if output_payload is not None:
        node_run.output_payload = output_payload
    if sdk_state is not None:
        node_run.sdk_state = sdk_state
    if node_run.status != "cancelled":
        node_run.status = "cancelled"
        node_run.finished_at = _now()
        _event(
            repo,
            run,
            "node_cancelled",
            {"node_key": node_run.node_key, "attempt": node_run.attempt},
            node_run=node_run,
        )
    run.heartbeat_at = None
    run.lease_expires_at = None
    run.claim_token = None
    repo.db.add(node_run)
    repo.db.add(run)
    repo.commit()


def _claim_run(
    repo: AgentPlatformRepository,
    run_id: int,
    task_id: str | None,
) -> AgentRun | None:
    run = repo.get_run(run_id=run_id)
    if run is None or run.status not in {"pending", "running"}:
        return None
    now = _now()
    if (
        run.status == "running"
        and run.lease_expires_at is not None
        and run.lease_expires_at >= now
        and run.task_id != task_id
    ):
        return None
    run.status = "running"
    run.task_id = task_id
    run.claim_token = task_id or f"agent-run-{run_id}-{int(now.timestamp())}"
    run.started_at = run.started_at or now
    run.heartbeat_at = now
    run.lease_expires_at = now + timedelta(seconds=RUN_LEASE_SECONDS)
    repo.db.add(run)
    _event(repo, run, "run_started", {"task_id": task_id or ""})
    repo.commit()
    repo.refresh(run)
    return run


def _node_input(
    run: AgentRun,
    node: WorkflowNode,
    dependency_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if node.input_mapping:
        sources = {
            "input": dict(run.input_payload or {}),
            "dependencies": {
                key: dependency_outputs[key]
                for key in node.depends_on
                if key in dependency_outputs
            },
            "run": {"id": run.id, "project_id": run.project_id},
        }
        mapped: dict[str, Any] = {}
        for target_key, source_path in node.input_mapping.items():
            current: Any = sources
            for part in str(source_path).split("."):
                if not isinstance(current, dict) or part not in current:
                    raise KeyError(
                        f"节点 {node.node_key} 输入映射不存在: {source_path}"
                    )
                current = current[part]
            mapped[target_key] = current
        return mapped

    payload = dict(run.input_payload or {})
    payload["dependency_outputs"] = {
        key: dependency_outputs[key]
        for key in node.depends_on
        if key in dependency_outputs
    }
    payload["run_id"] = run.id
    payload["project_id"] = run.project_id
    return payload


def _approval_allows_execution(
    repo: AgentPlatformRepository,
    run: AgentRun,
    node: WorkflowNode,
    node_run: AgentNodeRun,
    tool: Any,
    node_input: dict[str, Any],
) -> bool:
    latest = repo.latest_approval(run_id=run.id, node_key=node.node_key)
    if latest is not None and latest.status == "approved":
        return True
    if latest is not None and latest.status == "rejected":
        raise PermissionError("工具执行审批已拒绝")
    if latest is None:
        approval = AgentApproval(
            run_id=run.id,
            node_run_id=node_run.id,
            tool_definition_id=tool.id,
            status="pending",
            request_payload={
                "tool_key": tool.tool_key,
                "risk_level": tool.risk_level,
                "arguments": node_input,
            },
        )
        repo.db.add(approval)
        repo.db.flush()
        node_run.status = "waiting_approval"
        run.status = "waiting_approval"
        run.current_node_key = node.node_key
        _event(
            repo,
            run,
            "approval_requested",
            {"approval_id": approval.id, "tool_key": tool.tool_key},
            node_run=node_run,
        )
        repo.commit()
    return False


def _payload_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sum_usage(total: dict[str, int], current: dict[str, int]) -> dict[str, int]:
    keys = {"requests", "input_tokens", "output_tokens", "total_tokens"}
    return {
        key: int(total.get(key, 0) or 0) + int(current.get(key, 0) or 0)
        for key in keys
    }


def _run_execution_limits(run: AgentRun) -> dict[str, int]:
    context = dict(run.run_context or {})
    stored = dict(context.get("execution_limits") or {})
    defaults = {
        "max_requests": int(settings.AGENT_RUN_MAX_REQUESTS),
        "max_input_tokens": int(settings.AGENT_RUN_MAX_INPUT_TOKENS),
        "max_output_tokens": int(settings.AGENT_RUN_MAX_OUTPUT_TOKENS),
        "max_total_tokens": int(settings.AGENT_RUN_MAX_TOTAL_TOKENS),
    }
    return {
        key: min(default_value, int(stored.get(key) or default_value))
        for key, default_value in defaults.items()
    }


def _run_usage(run: AgentRun) -> dict[str, int]:
    raw = dict(dict(run.run_context or {}).get("usage") or {})
    return {
        "attempted_requests": int(raw.get("attempted_requests") or 0),
        "requests": int(raw.get("requests") or 0),
        "input_tokens": int(raw.get("input_tokens") or 0),
        "output_tokens": int(raw.get("output_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def _run_quota_usage(run: AgentRun) -> dict[str, int]:
    """返回额度账本；旧运行没有独立账本时从真实用量开始计费。"""

    context = dict(run.run_context or {})
    raw = dict(context.get("quota_usage") or {})
    if not raw:
        actual = _run_usage(run)
        raw = {
            "attempted_requests": actual["attempted_requests"],
            "input_tokens": actual["input_tokens"],
            "output_tokens": actual["output_tokens"],
            "total_tokens": actual["total_tokens"],
        }
    return {
        "attempted_requests": int(raw.get("attempted_requests") or 0),
        "input_tokens": int(raw.get("input_tokens") or 0),
        "output_tokens": int(raw.get("output_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def _agent_request_reservation(
    *,
    definition: Any,
    tools: list[Any],
    input_payload: dict[str, Any],
) -> dict[str, int]:
    """按一次 SDK 运行可能产生的全部模型轮次预留额度。"""

    runtime_config = dict(getattr(definition, "runtime_config", {}) or {})
    max_turns = int(runtime_config.get("max_turns") or (8 if tools else 1))
    if max_turns <= 0:
        raise ValueError("Agent max_turns 必须大于 0")
    if not tools and max_turns != 1:
        raise ValueError("无工具 Agent 的 max_turns 必须为 1")
    input_tokens = _agent_input_token_upper_bound(
        definition=definition,
        tools=tools,
        input_payload=input_payload,
    ) * max_turns
    output_tokens = _agent_output_token_reservation(definition) * max_turns
    return {
        "attempted_requests": max_turns,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def _agent_input_token_upper_bound(
    *,
    definition: Any,
    tools: list[Any],
    input_payload: dict[str, Any],
) -> int:
    """用 UTF-8 字节数形成保守上界，避免下一次调用跨过 input/total 配额。"""

    tool_payloads = [
        {
            "tool_key": str(getattr(tool, "tool_key", "") or ""),
            "name": str(getattr(tool, "name", "") or ""),
            "description": str(getattr(tool, "description", "") or ""),
            "input_schema": dict(getattr(tool, "input_schema", {}) or {}),
            "output_schema": dict(getattr(tool, "output_schema", {}) or {}),
        }
        for tool in tools
    ]
    envelope = {
        "instructions": str(getattr(definition, "instructions", "") or ""),
        "output_schema": dict(getattr(definition, "output_schema", {}) or {}),
        "runtime_config": dict(getattr(definition, "runtime_config", {}) or {}),
        "tools": tool_payloads,
        "input": input_payload,
    }
    encoded = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    input_mode = str(envelope["runtime_config"].get("input_mode") or "")
    # 视觉输入还会由 SDK 追加真实页图；使用更高预留覆盖图像 token 与协议包装。
    protocol_reservation = 32768 if "image" in input_mode else 8192
    return len(encoded) + protocol_reservation


def _agent_output_token_reservation(definition: Any) -> int:
    runtime_config = dict(getattr(definition, "runtime_config", {}) or {})
    return int(runtime_config.get("max_output_tokens") or settings.MAX_TOKENS)


def _reserve_agent_request(
    *,
    repo: AgentPlatformRepository,
    run: AgentRun,
    node_run: AgentNodeRun,
    definition: Any,
    tools: list[Any],
    input_payload: dict[str, Any],
) -> dict[str, int]:
    limits = _run_execution_limits(run)
    usage = _run_usage(run)
    quota_usage = _run_quota_usage(run)
    reservation = _agent_request_reservation(
        definition=definition,
        tools=tools,
        input_payload=input_payload,
    )
    projected = {
        key: quota_usage[key] + reservation[key]
        for key in quota_usage
    }
    checks = {
        "attempted_requests": "max_requests",
        "input_tokens": "max_input_tokens",
        "output_tokens": "max_output_tokens",
        "total_tokens": "max_total_tokens",
    }
    exceeded = {
        usage_key: {"projected": projected[usage_key], "limit": limits[limit_key]}
        for usage_key, limit_key in checks.items()
        if projected[usage_key] > limits[limit_key]
    }
    if exceeded:
        _event(
            repo,
            run,
            "run_quota_blocked",
            {
                "node_key": node_run.node_key,
                "usage": usage,
                "quota_usage": quota_usage,
                "limits": limits,
                "reservation": reservation,
                "projected": projected,
                "exceeded": exceeded,
            },
            node_run=node_run,
        )
        repo.commit()
        raise _RunQuotaExceeded(
            "Agent Run 额度不足，已在调用模型前阻断: "
            + ", ".join(
                f"{key}={value['projected']}>{value['limit']}"
                for key, value in exceeded.items()
            )
        )

    usage["attempted_requests"] += 1
    run_context = dict(run.run_context or {})
    run_context["execution_limits"] = limits
    run_context["usage"] = usage
    run_context["quota_usage"] = projected
    run.run_context = run_context
    repo.db.add(run)
    repo.commit()
    return reservation


def _record_agent_usage(
    *,
    repo: AgentPlatformRepository,
    run: AgentRun,
    node_run: AgentNodeRun,
    current: dict[str, int],
    reservation: dict[str, int],
) -> None:
    usage = _run_usage(run)
    reported_requests = int(current.get("requests") or 0)
    if reported_requests > 1:
        usage["attempted_requests"] += reported_requests - 1
    summed = _sum_usage(usage, current)
    usage.update(summed)
    limits = _run_execution_limits(run)
    quota_usage = _run_quota_usage(run)
    reported_requests = max(1, int(current.get("requests") or 0))
    has_reported_tokens = any(
        int(current.get(key) or 0) > 0
        for key in ("input_tokens", "output_tokens", "total_tokens")
    )
    actual_charge = {
        "attempted_requests": reported_requests,
        "input_tokens": (
            int(current.get("input_tokens") or 0)
            if has_reported_tokens
            else reservation["input_tokens"]
        ),
        "output_tokens": (
            int(current.get("output_tokens") or 0)
            if has_reported_tokens
            else reservation["output_tokens"]
        ),
        "total_tokens": (
            int(current.get("total_tokens") or 0)
            if has_reported_tokens
            else reservation["total_tokens"]
        ),
    }
    reconciled_quota_usage = {
        key: max(0, quota_usage[key] - reservation[key]) + actual_charge[key]
        for key in quota_usage
    }
    run_context = dict(run.run_context or {})
    run_context["execution_limits"] = limits
    run_context["usage"] = usage
    run_context["quota_usage"] = reconciled_quota_usage
    run.run_context = run_context
    repo.db.add(run)
    repo.commit()
    checks = {
        "attempted_requests": "max_requests",
        "input_tokens": "max_input_tokens",
        "output_tokens": "max_output_tokens",
        "total_tokens": "max_total_tokens",
    }
    exceeded = {
        usage_key: {
            "actual": usage.get(usage_key, 0),
            "charged": reconciled_quota_usage[usage_key],
            "limit": limits[limit_key],
        }
        for usage_key, limit_key in checks.items()
        if reconciled_quota_usage[usage_key] > limits[limit_key]
    }
    if exceeded:
        _event(
            repo,
            run,
            "run_quota_exceeded",
            {
                "node_key": node_run.node_key,
                "usage": usage,
                "quota_usage": reconciled_quota_usage,
                "limits": limits,
                "exceeded": exceeded,
            },
            node_run=node_run,
        )
        repo.commit()
        raise _RunQuotaExceeded(f"Agent Run 实际用量超过硬额度: {exceeded}")


def _is_retryable_agent_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
            InternalServerError,
            ModelBehaviorError,
        ),
    ):
        return True
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and (status_code == 429 or status_code >= 500)


def _execute_node_with_retry(
    repo: AgentPlatformRepository,
    run: AgentRun,
    node: WorkflowNode,
    dependency_outputs: dict[str, dict[str, Any]],
) -> tuple[AgentNodeRun, dict[str, Any]] | None:
    """仅为普通 Agent 节点调度持久化重试，映射节点维持逐项重试语义。"""

    while True:
        try:
            return _execute_node(repo, run, node, dependency_outputs)
        except _RunCancelled:
            raise
        except Exception as exc:
            if node.node_type != "agent":
                raise

            # Agent 调用失败后先清理当前事务，再从数据库恢复本次节点尝试。
            repo.db.rollback()
            active_run = repo.get_run(run_id=run.id)
            if active_run is None:
                raise
            if _refresh_run_is_cancelled(repo, active_run):
                latest = repo.latest_node_run(
                    run_id=active_run.id,
                    node_key=node.node_key,
                )
                if latest is not None and latest.status == "running":
                    _mark_node_cancelled(repo, active_run, latest)
                raise _RunCancelled(f"Agent Run {active_run.id} 已取消") from exc
            latest = repo.latest_node_run(
                run_id=active_run.id,
                node_key=node.node_key,
            )
            if latest is None or latest.status != "running":
                raise

            retryable = _is_retryable_agent_error(exc)
            can_retry = retryable and latest.attempt < node.max_attempts
            latest.status = "failed"
            latest.error_message = f"{type(exc).__name__}: {exc}"
            latest.finished_at = _now()
            _event(
                repo,
                active_run,
                "node_failed",
                {
                    "node_key": node.node_key,
                    "node_type": node.node_type,
                    "attempt": latest.attempt,
                    "retryable": retryable,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                },
                node_run=latest,
            )
            if can_retry:
                _event(
                    repo,
                    active_run,
                    "node_retry_scheduled",
                    {
                        "node_key": node.node_key,
                        "failed_attempt": latest.attempt,
                        "next_attempt": latest.attempt + 1,
                    },
                    node_run=latest,
                )
            repo.db.add(latest)
            repo.commit()
            if not can_retry:
                raise

            run = active_run
            time.sleep(min(2 ** (latest.attempt - 1), 4))


def _execute_agent_map(
    *,
    repo: AgentPlatformRepository,
    run: AgentRun,
    node: WorkflowNode,
    node_run: AgentNodeRun,
    definition: Any,
    tools: list[Any],
    execution_context: ToolExecutionContext,
    node_input: dict[str, Any],
    previous: AgentNodeRun | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = node.map_config
    if config is None:
        raise ValueError(f"agent_map 节点缺少配置: {node.node_key}")
    raw_items = node_input.get(config.items_key)
    if not isinstance(raw_items, list):
        raise ValueError(f"agent_map 节点输入必须包含数组: {config.items_key}")
    if not raw_items and not config.allow_empty:
        raise ValueError(f"agent_map 节点输入必须包含非空数组: {config.items_key}")
    if len(raw_items) > config.max_items:
        raise ValueError(
            f"agent_map 节点输入超过上限: actual={len(raw_items)}, max={config.max_items}"
        )
    if not all(isinstance(item, dict) for item in raw_items):
        raise ValueError("agent_map 的每个输入项都必须是 JSON 对象")

    previous_output = dict(previous.output_payload or {}) if previous is not None else {}
    completed = list(previous_output.get(config.output_key) or [])
    if len(completed) > len(raw_items):
        raise ValueError("agent_map 已持久化结果数量超过本次输入数量")
    for index, record in enumerate(completed):
        if not isinstance(record, dict) or record.get("input_hash") != _payload_hash(raw_items[index]):
            raise ValueError("agent_map 重试输入与已持久化部分结果不一致")

    previous_state = dict(previous.sdk_state or {}) if previous is not None else {}
    aggregate_usage = dict(previous_state.get("usage") or {})
    item_states = list(previous_state.get("items") or [])
    total_count = len(raw_items)

    def current_output() -> dict[str, Any]:
        return {
            config.output_key: completed,
            "completed_count": len(completed),
            "total_count": total_count,
        }

    def current_sdk_state() -> dict[str, Any]:
        return {
            "last_agent_name": item_states[-1]["last_agent_name"] if item_states else "",
            "usage": aggregate_usage,
            "items": item_states,
        }

    for index in range(len(completed), total_count):
        item_input = dict(raw_items[index])
        result = None
        for item_attempt in range(1, node.max_attempts + 1):
            if _refresh_run_is_cancelled(repo, run):
                _mark_node_cancelled(
                    repo,
                    run,
                    node_run,
                    output_payload=current_output(),
                    sdk_state=current_sdk_state(),
                )
                raise _RunCancelled(f"Agent Run {run.id} 已取消")
            _event(
                repo,
                run,
                "map_item_started",
                {
                    "node_key": node.node_key,
                    "item_index": index,
                    "item_number": index + 1,
                    "item_total": total_count,
                    "item_attempt": item_attempt,
                },
                node_run=node_run,
            )
            run.heartbeat_at = _now()
            run.lease_expires_at = _now() + timedelta(seconds=RUN_LEASE_SECONDS)
            repo.db.add(run)
            repo.db.commit()
            try:
                reservation = _reserve_agent_request(
                    repo=repo,
                    run=run,
                    node_run=node_run,
                    definition=definition,
                    tools=tools,
                    input_payload=item_input,
                )
                result = run_agent(
                    db=repo.db,
                    agent_definition=definition,
                    tool_definitions=tools,
                    execution_context=execution_context,
                    input_payload=item_input,
                )
                _record_agent_usage(
                    repo=repo,
                    run=run,
                    node_run=node_run,
                    current=result.usage,
                    reservation=reservation,
                )
                break
            except Exception as exc:
                retryable = _is_retryable_agent_error(exc)
                _event(
                    repo,
                    run,
                    "map_item_failed",
                    {
                        "node_key": node.node_key,
                        "item_index": index,
                        "item_number": index + 1,
                        "item_total": total_count,
                        "item_attempt": item_attempt,
                        "retryable": retryable,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    },
                    node_run=node_run,
                )
                node_run.output_payload = {
                    config.output_key: completed,
                    "completed_count": len(completed),
                    "total_count": total_count,
                }
                node_run.sdk_state = {
                    "last_agent_name": definition.name,
                    "usage": aggregate_usage,
                    "items": item_states,
                    "failed_item": {
                        "item_index": index,
                        "item_attempt": item_attempt,
                        "error_type": type(exc).__name__,
                    },
                }
                repo.db.add(node_run)
                repo.db.commit()
                if not retryable or item_attempt >= node.max_attempts:
                    raise
                _event(
                    repo,
                    run,
                    "map_item_retry_scheduled",
                    {
                        "node_key": node.node_key,
                        "item_index": index,
                        "next_attempt": item_attempt + 1,
                    },
                    node_run=node_run,
                )
                repo.db.commit()
                time.sleep(min(2 ** (item_attempt - 1), 4))
        if result is None:
            raise RuntimeError(f"agent_map 映射项未产生结果: node={node.node_key}, index={index}")

        aggregate_usage = _sum_usage(aggregate_usage, result.usage)
        item_states.append(
            {
                "item_index": index,
                "last_agent_name": result.last_agent_name,
                "usage": result.usage,
                "tool_calls": result.tool_calls,
            }
        )
        completed.append(
            {
                "item_index": index,
                "input_hash": _payload_hash(item_input),
                "output": dict(result.output),
            }
        )
        node_run.output_payload = {
            config.output_key: completed,
            "completed_count": len(completed),
            "total_count": total_count,
        }
        node_run.sdk_state = {
            "last_agent_name": result.last_agent_name,
            "usage": aggregate_usage,
            "items": item_states,
        }
        _event(
            repo,
            run,
            "map_item_completed",
            {
                "node_key": node.node_key,
                "item_index": index,
                "item_number": index + 1,
                "item_total": total_count,
            },
            node_run=node_run,
        )
        run.heartbeat_at = _now()
        run.lease_expires_at = _now() + timedelta(seconds=RUN_LEASE_SECONDS)
        repo.db.add(node_run)
        repo.db.add(run)
        repo.db.commit()

    if _refresh_run_is_cancelled(repo, run):
        _mark_node_cancelled(
            repo,
            run,
            node_run,
            output_payload=current_output(),
            sdk_state=current_sdk_state(),
        )
        raise _RunCancelled(f"Agent Run {run.id} 已取消")

    return current_output(), current_sdk_state()


def _execute_node(
    repo: AgentPlatformRepository,
    run: AgentRun,
    node: WorkflowNode,
    dependency_outputs: dict[str, dict[str, Any]],
) -> tuple[AgentNodeRun, dict[str, Any]] | None:
    if _refresh_run_is_cancelled(repo, run):
        raise _RunCancelled(f"Agent Run {run.id} 已取消")
    previous = repo.latest_node_run(run_id=run.id, node_key=node.node_key)
    if previous is not None and previous.status == "waiting_approval":
        node_run = previous
        attempt = previous.attempt
        node_input = dict(previous.input_payload or {})
        node_run.status = "running"
        node_run.error_message = ""
    else:
        attempt = repo.next_node_attempt(run_id=run.id, node_key=node.node_key)
        if attempt > node.max_attempts:
            raise RuntimeError(f"节点 {node.node_key} 已耗尽重试次数")
        node_input = _node_input(run, node, dependency_outputs)
        node_run = AgentNodeRun(
            run_id=run.id,
            node_key=node.node_key,
            node_type=node.node_type,
            status="running",
            attempt=attempt,
            input_payload=node_input,
            started_at=_now(),
        )
        if previous is not None and node.node_type == "agent_map":
            node_run.output_payload = deepcopy(previous.output_payload or {})
            node_run.sdk_state = deepcopy(previous.sdk_state or {})
        repo.db.add(node_run)
        repo.db.flush()
    run.current_node_key = node.node_key
    run.heartbeat_at = _now()
    run.lease_expires_at = _now() + timedelta(seconds=RUN_LEASE_SECONDS)
    _event(
        repo,
        run,
        "node_started",
        {"node_key": node.node_key, "node_type": node.node_type, "attempt": attempt},
        node_run=node_run,
    )
    repo.commit()

    run_context = deepcopy(run.run_context or {})
    artifacts = deepcopy(run_context.get("artifacts") or {})
    execution_context = ToolExecutionContext(
        db=repo.db,
        user_id=run.user_id,
        project_id=run.project_id,
        run_id=run.id,
        node_key=node.node_key,
        run_input=dict(run.input_payload or {}),
        artifacts=artifacts,
    )
    if node.node_type in {"agent", "agent_map"}:
        definition = repo.get_agent(
            project_id=run.project_id,
            agent_key=node.reference_key,
        )
        if definition is None:
            raise LookupError(f"找不到智能体定义: {node.reference_key}")
        node_run.agent_definition_id = definition.id
        tools = repo.list_agent_tools(definition.id)
        if node.node_type == "agent_map":
            output, sdk_state = _execute_agent_map(
                repo=repo,
                run=run,
                node=node,
                node_run=node_run,
                definition=definition,
                tools=tools,
                execution_context=execution_context,
                node_input=node_input,
                previous=previous,
            )
            node_run.sdk_state = sdk_state
        else:
            if _refresh_run_is_cancelled(repo, run):
                _mark_node_cancelled(repo, run, node_run)
                raise _RunCancelled(f"Agent Run {run.id} 已取消")
            reservation = _reserve_agent_request(
                repo=repo,
                run=run,
                node_run=node_run,
                definition=definition,
                tools=tools,
                input_payload=node_input,
            )
            result = run_agent(
                db=repo.db,
                agent_definition=definition,
                tool_definitions=tools,
                execution_context=execution_context,
                input_payload=node_input,
            )
            _record_agent_usage(
                repo=repo,
                run=run,
                node_run=node_run,
                current=result.usage,
                reservation=reservation,
            )
            output = dict(result.output)
            node_run.sdk_state = {
                "last_agent_name": result.last_agent_name,
                "usage": result.usage,
                "tool_calls": result.tool_calls,
            }
    else:
        tool = repo.get_tool(
            project_id=run.project_id,
            tool_key=node.reference_key,
        )
        if tool is None:
            raise LookupError(f"找不到工具定义: {node.reference_key}")
        node_run.tool_definition_id = tool.id
        repo.db.flush()
        if tool.requires_approval and not _approval_allows_execution(
            repo,
            run,
            node,
            node_run,
            tool,
            node_input,
        ):
            return None
        tool_schema = dict(tool.input_schema or {})
        tool_input = node_input
        if not node.input_mapping and isinstance(tool_schema.get("properties"), dict):
            allowed = set(tool_schema["properties"])
            tool_input = {key: value for key, value in node_input.items() if key in allowed}
        node_run.input_payload = tool_input
        validate(instance=tool_input, schema=tool_schema)
        output = tool_registry.resolve(tool.handler_key)(execution_context, tool_input)
        validate(instance=output, schema=dict(tool.output_schema or {}))

    if _refresh_run_is_cancelled(repo, run):
        _mark_node_cancelled(
            repo,
            run,
            node_run,
            output_payload=output,
            sdk_state=dict(node_run.sdk_state or {}),
        )
        raise _RunCancelled(f"Agent Run {run.id} 已取消")

    # Agent 调用期间会实时更新 usage；完成节点时从最新上下文合并，避免旧快照覆盖额度账本。
    latest_run_context = deepcopy(run.run_context or {})
    latest_run_context["artifacts"] = execution_context.artifacts
    latest_run_context.setdefault("node_outputs", {})[node.node_key] = output
    run.run_context = latest_run_context
    node_run.output_payload = output
    node_run.status = "success"
    node_run.finished_at = _now()
    _event(
        repo,
        run,
        "node_completed",
        {"node_key": node.node_key, "attempt": attempt},
        node_run=node_run,
    )
    repo.db.add(node_run)
    repo.db.add(run)
    repo.commit()
    return node_run, output


def run_agent_workflow(
    *,
    run_id: int,
    task_id: str | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    owns_session = db is None
    active_db = db or SessionLocal()
    repo = AgentPlatformRepository(active_db)
    try:
        run = _claim_run(repo, run_id, task_id)
        if run is None:
            return {"status": "not_claimed", "run_id": run_id}
        expected_signature = str(
            (run.run_context or {}).get("runtime_registry_signature") or ""
        )
        actual_signature = runtime_registry_signature()
        if not expected_signature or expected_signature != actual_signature:
            raise RuntimeError(
                "Agent Worker 运行时代码与创建 Run 的服务版本不一致，请重启 Worker 后重试"
            )
        from core.db.model_defs import AgentWorkflowDefinition

        workflow = repo.db.get(AgentWorkflowDefinition, run.workflow_definition_id)
        if workflow is None or not workflow.enabled:
            raise LookupError("运行引用的工作流不存在或已停用")
        graph = WorkflowGraph.model_validate(workflow.definition)
        validate(instance=dict(run.input_payload or {}), schema=graph.input_schema)
        dependency_outputs = dict((run.run_context or {}).get("node_outputs") or {})

        for node in graph.execution_order():
            repo.refresh(run)
            if run.status == "cancelled":
                return {"status": "cancelled", "run_id": run.id}
            previous = repo.latest_node_run(run_id=run.id, node_key=node.node_key)
            if previous is not None and previous.status == "success":
                dependency_outputs[node.node_key] = dict(previous.output_payload or {})
                continue
            executed = _execute_node_with_retry(
                repo,
                run,
                node,
                dependency_outputs,
            )
            if executed is None:
                return {"status": "waiting_approval", "run_id": run.id}
            _, output = executed
            dependency_outputs[node.node_key] = output

        if _refresh_run_is_cancelled(repo, run):
            return {"status": "cancelled", "run_id": run.id}
        final_output = dependency_outputs[graph.output_node_key]
        run.output_payload = {
            "result": final_output,
            "artifacts": dict((run.run_context or {}).get("artifacts") or {}),
        }
        run.status = "success"
        run.current_node_key = None
        run.finished_at = _now()
        run.heartbeat_at = None
        run.lease_expires_at = None
        run.claim_token = None
        _event(repo, run, "run_completed", {"output_node_key": graph.output_node_key})
        repo.db.add(run)
        repo.commit()
        return {"status": "success", "run_id": run.id}
    except _RunCancelled:
        repo.db.rollback()
        return {"status": "cancelled", "run_id": run_id}
    except Exception as exc:
        repo.db.rollback()
        run = repo.get_run(run_id=run_id)
        if run is not None:
            if _refresh_run_is_cancelled(repo, run):
                latest = (
                    repo.latest_node_run(run_id=run.id, node_key=run.current_node_key)
                    if run.current_node_key
                    else None
                )
                if latest is not None and latest.status == "running":
                    _mark_node_cancelled(repo, run, latest)
                return {"status": "cancelled", "run_id": run.id}
            latest = (
                repo.latest_node_run(run_id=run.id, node_key=run.current_node_key)
                if run.current_node_key
                else None
            )
            if latest is not None and latest.status == "running":
                latest.status = "failed"
                latest.error_message = f"{type(exc).__name__}: {exc}"
                latest.finished_at = _now()
                repo.db.add(latest)
            run.status = "failed"
            run.error_message = f"{type(exc).__name__}: {exc}"
            run.finished_at = _now()
            run.heartbeat_at = None
            run.lease_expires_at = None
            run.claim_token = None
            _event(
                repo,
                run,
                "run_failed",
                {"error_type": type(exc).__name__, "message": str(exc)[:1000]},
                node_run=latest,
            )
            repo.db.add(run)
            repo.commit()
        raise
    finally:
        if owns_session:
            active_db.close()
