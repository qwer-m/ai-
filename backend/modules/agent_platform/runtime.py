from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import inspect
import json
import math
import time
from typing import Any

from jsonschema import validate
from jsonschema.validators import validator_for
from agents.exceptions import ModelBehaviorError
from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentApproval,
    AgentDefinition,
    AgentNodeRun,
    AgentRun,
)
from core.settings.config import settings
from .contracts import (
    AgentProgramDefinition,
    WorkflowGraph,
    WorkflowNode,
    parse_execution_definition,
)
from .registry import ToolExecutionContext, runtime_registry_signature, tool_registry
from .repository import AgentPlatformRepository
from .lifecycle import release_run_lease, renew_run_lease, transition_run
from .retention import prune_terminal_run_history
from .sources import assert_same_source, persisted_source_snapshot
from .output_repair import OutputRepairError, restore_protected_output
from .retry_policy import (
    CONCURRENCY_PRESSURE_FAILURE_KINDS as _CONCURRENCY_PRESSURE_FAILURE_KINDS,
    MODEL_ROUTE_HEALTH_FAILURE_KINDS as _MODEL_ROUTE_HEALTH_FAILURE_KINDS,
    RetryAttemptState,
    RetryDecision,
)
from .sdk_adapter import (
    OutputPostprocessingError,
    StructuredOutputJSONError,
    StructuredOutputValidationError,
    ToolArgumentsValidationError,
    ToolOutputValidationError,
    _postprocess_agent_output,
    resolve_agent_model_metadata,
    run_agent,
    run_agent_async,
)


RUN_LEASE_SECONDS = int(settings.AGENT_RUN_LEASE_SECONDS)
RUN_HEARTBEAT_SECONDS = max(10, RUN_LEASE_SECONDS // 4)
_AGENT_MAP_DIAGNOSTIC_ATTEMPT_LIMIT = 3
_AGENT_MAP_DIAGNOSTIC_TEXT_LIMIT = 120_000
_AGENT_MAP_REPAIR_CANDIDATE_TEXT_LIMIT = 30_000
_AGENT_RETRY_FEEDBACK_TEXT_LIMIT = 4_000
_STRUCTURED_OUTPUT_DEGENERATION_REASONS = frozenset(
    {
        "multiple_schema_violations",
        "container_type_mismatch",
        "structural_array_cardinality",
    }
)
_ORIGINAL_RUN_AGENT = run_agent


class _RunCancelled(RuntimeError):
    """运行已被外部取消，不应进入失败处理。"""


class _RunDeadlineExceeded(TimeoutError):
    """运行或当前阶段已达到确定性截止时间。"""


class _AgentQuotaExceeded(RuntimeError):
    """单个 Agent 实例的请求或 Token 额度已经耗尽。"""


def _now() -> datetime:
    return datetime.utcnow()


def _node_duration_seconds(node_run: AgentNodeRun) -> int | None:
    if node_run.started_at is None or node_run.finished_at is None:
        return None
    return max(0, int((node_run.finished_at - node_run.started_at).total_seconds()))


def _deadline_value(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Agent Run 截止时间格式无效: {text}") from exc


def _remaining_run_seconds(run: AgentRun) -> float:
    context = dict(run.run_context or {})
    deadline = _deadline_value(context.get("deadline_at"))
    stage_deadline = _deadline_value(context.get("stage_deadline_at"))
    active_deadlines = [value for value in (deadline, stage_deadline) if value is not None]
    if not active_deadlines:
        return float(settings.AGENT_RUN_DEADLINE_SECONDS)
    return (min(active_deadlines) - _now()).total_seconds()


def _ensure_run_deadline(run: AgentRun, *, node_key: str = "") -> float:
    context = dict(run.run_context or {})
    global_deadline = _deadline_value(context.get("deadline_at"))
    stage_deadline = _deadline_value(context.get("stage_deadline_at"))
    remaining = _remaining_run_seconds(run)
    if remaining <= 0:
        location = f", node={node_key}" if node_key else ""
        if (
            stage_deadline is not None
            and (global_deadline is None or stage_deadline < global_deadline)
            and stage_deadline <= _now()
        ):
            raise _RunDeadlineExceeded(
                f"Agent 节点已达到阶段预算{location}"
            )
        deadline_minutes = max(
            1,
            math.ceil(int(settings.AGENT_RUN_DEADLINE_SECONDS) / 60),
        )
        raise _RunDeadlineExceeded(
            f"Agent Run 已达到 {deadline_minutes} 分钟硬截止{location}"
        )
    return remaining


def _request_timeout_seconds(run: AgentRun, definition: Any, *, node_key: str) -> float:
    remaining = _ensure_run_deadline(run, node_key=node_key)
    configured = dict(getattr(definition, "runtime_config", {}) or {}).get(
        "request_timeout_seconds"
    )
    configured_seconds = float(configured) if configured not in (None, "") else remaining
    return max(1.0, min(configured_seconds, remaining))


def _agent_map_request_timeout_seconds(
    run: AgentRun,
    definition: Any,
    *,
    node_key: str,
    item_attempt: int,
) -> float:
    """首轮尽快释放长尾请求，重试时恢复 Agent 配置的完整超时。"""

    configured_seconds = _request_timeout_seconds(
        run,
        definition,
        node_key=node_key,
    )
    if item_attempt > 1:
        return configured_seconds
    return max(
        1.0,
        min(
            configured_seconds,
            float(settings.AGENT_MAP_FIRST_ATTEMPT_TIMEOUT_SECONDS),
        ),
    )


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


def _persistent_error_message(exc: Exception, *, max_chars: int = 4000) -> str:
    """限制数据库错误摘要长度，完整大对象不得进入运行错误列。"""

    return f"{type(exc).__name__}: {exc}"[:max_chars]


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
    release_run_lease(run)
    repo.db.add(node_run)
    repo.db.add(run)
    repo.commit()


def _renew_run_lease(run_id: int) -> str | None:
    """使用独立会话续租，避免干扰 Agent 工具正在使用的业务会话。"""

    heartbeat_db = SessionLocal()
    try:
        active_run = AgentPlatformRepository(heartbeat_db).get_run_for_update(run_id=run_id)
        if active_run is None:
            return None
        if active_run.status != "running":
            return str(active_run.status)
        now = _now()
        renew_run_lease(active_run, now=now, lease_seconds=RUN_LEASE_SECONDS)
        heartbeat_db.add(active_run)
        heartbeat_db.commit()
        return "running"
    finally:
        heartbeat_db.close()


def _renew_progress_lease(repo: AgentPlatformRepository, run: AgentRun) -> None:
    """进度提交只刷新租约字段并持有行锁，避免旧会话续租已取消或重新认领的运行。"""
    claim_token = run.claim_token
    repo.db.refresh(
        run,
        attribute_names=["status", "claim_token", "heartbeat_at", "lease_expires_at"],
        with_for_update=True,
    )
    if run.status == "cancelled":
        raise _RunCancelled(f"Agent Run {run.id} 已取消")
    if run.status != "running" or run.claim_token != claim_token:
        raise RuntimeError(f"Agent Run {run.id} 已不属于当前执行器")
    renew_run_lease(run, now=_now(), lease_seconds=RUN_LEASE_SECONDS)


def _current_run_status(run_id: int) -> str | None:
    status_db = SessionLocal()
    try:
        active_run = status_db.get(AgentRun, run_id)
        return str(active_run.status) if active_run is not None else None
    finally:
        status_db.close()


async def _run_standard_agent_async(**arguments: Any) -> Any:
    if run_agent is _ORIGINAL_RUN_AGENT:
        operation = run_agent_async(**arguments)
    else:
        operation = asyncio.to_thread(run_agent, **arguments)
    task = asyncio.get_running_loop().create_task(operation)
    run_id = int(arguments["execution_context"].run_id)
    last_heartbeat_at = time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait(
                {task},
                timeout=1.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done:
                return task.result()
            status = await asyncio.to_thread(_current_run_status, run_id)
            if status == "cancelled":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise _RunCancelled(f"Agent Run {run_id} 已取消")
            if status != "running":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise RuntimeError(f"Agent Run {run_id} 续租失败: status={status}")
            if time.monotonic() - last_heartbeat_at >= RUN_HEARTBEAT_SECONDS:
                status = await asyncio.to_thread(_renew_run_lease, run_id)
                if status == "cancelled":
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise _RunCancelled(f"Agent Run {run_id} 已取消")
                if status != "running":
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise RuntimeError(f"Agent Run {run_id} 续租失败: status={status}")
                last_heartbeat_at = time.monotonic()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _run_standard_agent(**arguments: Any) -> Any:
    return asyncio.run(_run_standard_agent_async(**arguments))


def _claim_run(
    repo: AgentPlatformRepository,
    run_id: int,
    task_id: str | None,
) -> AgentRun | None:
    run = repo.get_run_for_update(run_id=run_id)
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
    transition_run(repo, run, "running", event_type="run_started", payload={"task_id": task_id or ""}, now=now)
    run.task_id = task_id
    run.claim_token = task_id or f"agent-run-{run_id}-{int(now.timestamp())}"
    run.started_at = run.started_at or now
    run_context = dict(run.run_context or {})
    deadline_at = _deadline_value(run_context.get("deadline_at"))
    if deadline_at is None:
        deadline_at = now + timedelta(seconds=int(settings.AGENT_RUN_DEADLINE_SECONDS))
    run_context["deadline_at"] = deadline_at.isoformat()
    run_context["stage_deadline_at"] = deadline_at.isoformat()
    run_context["remaining_seconds"] = max(0, int((deadline_at - now).total_seconds()))
    run.run_context = run_context
    renew_run_lease(run, now=now, lease_seconds=RUN_LEASE_SECONDS)
    repo.db.add(run)
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
        locked_run = repo.get_run_for_update(run_id=run.id)
        if locked_run is None or locked_run.status == "cancelled":
            raise _RunCancelled(f"Agent Run {run.id} 已取消")
        if locked_run.status != "running":
            raise RuntimeError("运行已不在执行状态，不能创建审批")
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
        run.current_node_key = node.node_key
        transition_run(
            repo, run, "waiting_approval", event_type="approval_requested",
            payload={"approval_id": approval.id, "tool_key": tool.tool_key},
            node_run_id=node_run.id,
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


def _agent_result_cache_config(definition: AgentDefinition) -> dict[str, Any] | None:
    """读取 Agent 的显式跨运行复用配置。

    复用版本是分析契约的一部分；指令、后处理或 Schema 语义变更时必须显式升级，
    从源头阻止旧结果跨语义版本命中。
    """

    raw = dict(getattr(definition, "runtime_config", {}) or {}).get(
        "result_cache"
    )
    if not isinstance(raw, dict) or not bool(raw.get("enabled", True)):
        return None
    version = str(raw.get("version") or "").strip()
    if not version:
        raise ValueError(
            f"Agent {definition.agent_key} 开启 result_cache 时必须声明 version"
        )
    return {
        "version": version,
        "accept_legacy": bool(raw.get("accept_legacy", False)),
        "candidate_limit": max(1, min(100, int(raw.get("candidate_limit") or 20))),
    }


def _agent_result_cache_input_hash(
    definition: Any,
    node_input: dict[str, Any],
) -> str:
    """计算包含模型输入投影契约的缓存身份哈希。

    节点落库仍保留完整原始输入；缓存身份另外纳入投影版本和字段白名单，
    这样只修改模型视图时不会误命中旧模型输出。没有投影的旧 Agent 继续
    使用原始输入哈希，保持历史缓存兼容。
    """

    runtime_config = dict(getattr(definition, "runtime_config", {}) or {})
    projection = runtime_config.get("input_projection")
    projection_version = runtime_config.get("input_projection_version")
    if projection is None and projection_version in (None, ""):
        return _payload_hash(node_input)
    return _payload_hash(
        {
            "node_input": node_input,
            "input_projection": deepcopy(projection),
            "input_projection_version": str(projection_version or "1"),
        }
    )


def _reusable_agent_node_output(
    *,
    repo: AgentPlatformRepository,
    run: AgentRun,
    node: WorkflowNode,
    definition: AgentDefinition,
    node_input: dict[str, Any],
) -> tuple[AgentNodeRun, dict[str, Any], str, str] | None:
    """按完整节点输入哈希查找已通过整轮交付的可复用结果。"""

    if bool(dict(run.input_payload or {}).get("disable_result_cache")):
        return None
    config = _agent_result_cache_config(definition)
    if config is None:
        return None
    input_hash = _agent_result_cache_input_hash(definition, node_input)
    candidate_ids = repo.list_reusable_node_run_ids(
        run=run,
        node_key=node.node_key,
        node_type=node.node_type,
        agent_definition_id=int(definition.id),
        limit=int(config["candidate_limit"]),
    )
    for candidate_id in candidate_ids:
        candidate = repo.db.get(AgentNodeRun, candidate_id)
        if candidate is None:
            continue
        candidate_state = dict(candidate.sdk_state or {})
        candidate_cache = dict(candidate_state.get("result_cache") or {})
        candidate_version = str(candidate_cache.get("version") or "").strip()
        if candidate_version:
            if candidate_version != config["version"]:
                continue
        elif not (
            bool(config["accept_legacy"])
            and str(config["version"]).endswith("-v1")
        ):
            # 无版本历史结果只允许作为首个缓存版本的一次性迁移源。
            continue
        stored_input_hash = str(candidate_cache.get("input_hash") or "").strip()
        if stored_input_hash:
            if stored_input_hash != input_hash:
                continue
        elif dict(getattr(definition, "runtime_config", {}) or {}).get(
            "input_projection"
        ) is not None:
            # 启用投影后，缺少新身份哈希的历史节点无法证明使用了同一模型视图。
            # 即使允许 legacy 版本，也不能把它当作新投影结果复用。
            continue
        elif _payload_hash(dict(candidate.input_payload or {})) != input_hash:
            continue
        output = dict(candidate.output_payload or {})
        if not output:
            continue
        return candidate, deepcopy(output), str(config["version"]), input_hash
    return None


def _agent_map_output_diagnostic(
    *,
    result: Any,
    item_attempt: int,
    exc: Exception,
) -> dict[str, Any]:
    """留存后处理拒绝的模型正文，供真实失败复盘。"""

    output_text = str(getattr(result, "final_text", "") or "")
    if not output_text:
        candidate = getattr(result, "output", None)
        if not isinstance(candidate, dict):
            candidate = next((
                current.candidate_output for current in _exception_chain(exc)
                if isinstance(getattr(current, "candidate_output", None), dict)
            ), None)
        if isinstance(candidate, dict):
            output_text = json.dumps(candidate, ensure_ascii=False, sort_keys=True, default=str)
    encoded = output_text.encode("utf-8")
    return {
        "item_attempt": item_attempt,
        "error_type": type(exc).__name__,
        "message": str(exc)[:4000],
        "normalized_model_output_text": output_text[:_AGENT_MAP_DIAGNOSTIC_TEXT_LIMIT],
        "output_text_chars": len(output_text),
        "output_text_sha256": hashlib.sha256(encoded).hexdigest(),
        "output_text_truncated": len(output_text) > _AGENT_MAP_DIAGNOSTIC_TEXT_LIMIT,
    }


def _recent_agent_map_diagnostics(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    diagnostics = [
        dict(item)
        for item in list(dict(state or {}).get("validation_diagnostics") or [])
        if isinstance(item, dict)
    ]
    return diagnostics[-_AGENT_MAP_DIAGNOSTIC_ATTEMPT_LIMIT:]


def _restored_agent_retry_context(
    state: dict[str, Any], input_payload: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """恢复修复上下文前验证输入身份，防止把旧候选用于不同任务。"""

    feedback = str(state.get("retry_feedback") or "").strip() or None
    repair = state.get("repair_context")
    if repair is not None:
        if not isinstance(repair, dict) or state.get("input_hash") != _payload_hash(input_payload):
            raise ValueError("Agent 修复检查点与当前输入不一致")
        if repair.get("mode") not in {"minimal_patch", "full_regeneration"}:
            raise ValueError("Agent 修复检查点模式无效")
        if repair.get("mode") == "minimal_patch" and not isinstance(repair.get("candidate_output"), dict):
            raise ValueError("Agent 修复检查点缺少完整候选")
    return feedback, deepcopy(repair)


def _agent_map_repair_context(
    *,
    result: Any | None,
    exc: Exception | None = None,
    validation_feedback: str | None,
    item_input: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """根据候选结构完整性选择最小修补或从原始输入完整重生成。"""

    if not validation_feedback:
        return None
    candidate = None
    if exc is not None:
        candidate = next(
            (
                getattr(current, "candidate_output", None)
                for current in _exception_chain(exc)
                if isinstance(getattr(current, "candidate_output", None), dict)
            ),
            None,
        )
    if not isinstance(candidate, dict):
        candidate = getattr(result, "output", None)
    if not isinstance(candidate, dict):
        return None
    rejection = _candidate_structure_rejection(candidate=candidate, exc=exc)
    if rejection is not None:
        return _full_regeneration_context(
            validation_feedback=validation_feedback,
            candidate_rejection=rejection,
        )
    candidate_text = json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    if len(candidate_text) > _AGENT_MAP_REPAIR_CANDIDATE_TEXT_LIMIT:
        return _full_regeneration_context(
            validation_feedback=validation_feedback,
            candidate_rejection={
                "reason": "candidate_too_large",
                "candidate_chars": len(candidate_text),
                "candidate_limit": _AGENT_MAP_REPAIR_CANDIDATE_TEXT_LIMIT,
            },
        )
    strategy_error = _output_repair_error(exc)
    target_context: dict[str, Any] = {}
    if strategy_error is not None:
        strategy = tool_registry.resolve_repair_strategy(strategy_error.strategy_key)
        target_context = strategy.build_context(
            dict(item_input or {}), candidate, strategy_error.details,
        )
    targeted_instruction = str(target_context.pop("instruction", "") or "")
    return {
        "mode": "minimal_patch",
        "instruction": (
            "candidate_output 是上一版未通过校验的完整结构化结果。"
            "必须以它为基线，仅修正 validation_feedback 指出的字段；"
            "保留未被要求修改的内容，不得从头重写。"
            f"{targeted_instruction}"
            "最终仍返回原输出契约要求的完整 JSON，不得输出本修复上下文。"
        ),
        "validation_feedback": validation_feedback,
        "candidate_output": deepcopy(candidate),
        **({"strategy_key": strategy_error.strategy_key} if strategy_error else {}),
        **target_context,
    }


def _output_repair_error(exc: Exception | None) -> OutputRepairError | None:
    if exc is None:
        return None
    return next(
        (error for error in _exception_chain(exc) if isinstance(error, OutputRepairError)),
        None,
    )


def _restore_protected_repair_slots(
    *,
    item_output: dict[str, Any],
    repair_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """执行领域策略声明的通用保护规则，运行时不解释具体业务字段。"""

    try:
        return restore_protected_output(item_output, dict(repair_context or {}))
    except ValueError as exc:
        raise OutputPostprocessingError(
            output=item_output,
            postprocessor="platform.repair_protection",
            cause=exc,
        ) from exc


def _candidate_structure_rejection(
    *,
    candidate: dict[str, Any],
    exc: Exception | None,
) -> dict[str, Any] | None:
    """用完整 Schema 判断候选是否已不适合做局部修补。"""

    if exc is None:
        return None
    schema_error = next(
        (
            current
            for current in _exception_chain(exc)
            if isinstance(current, StructuredOutputValidationError)
        ),
        None,
    )
    output_schema = dict(getattr(schema_error, "output_schema", {}) or {})
    if not output_schema:
        return None
    validator_class = validator_for(output_schema)
    violations = sorted(
        validator_class(output_schema).iter_errors(candidate),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    summaries = [
        {
            "keyword": str(error.validator or "unknown"),
            "field_path": [str(part) for part in error.absolute_path],
            "message": error.message[:500],
        }
        for error in violations[:5]
    ]
    if len(violations) > 1:
        return {
            "reason": "multiple_schema_violations",
            "violation_count": len(violations),
            "violations": summaries,
        }
    if not violations:
        return None
    violation = violations[0]
    expected_type = violation.validator_value
    expected_types = (
        {str(value) for value in expected_type}
        if isinstance(expected_type, list)
        else {str(expected_type)}
    )
    if violation.validator == "type" and expected_types.intersection({"object", "array"}):
        return {
            "reason": "container_type_mismatch",
            "violation_count": 1,
            "violations": summaries,
        }
    if violation.validator in {"minItems", "maxItems"}:
        return {
            "reason": "structural_array_cardinality",
            "violation_count": 1,
            "violations": summaries,
        }
    return None


def _structured_output_degeneration_rejection(
    exc: Exception,
) -> dict[str, Any] | None:
    """复用完整 Schema 判定，识别无法局部修补的结构化输出退化。"""

    schema_error = next(
        (
            current
            for current in _exception_chain(exc)
            if isinstance(current, StructuredOutputValidationError)
        ),
        None,
    )
    candidate = getattr(schema_error, "candidate_output", None)
    if not isinstance(candidate, dict):
        return None
    rejection = _candidate_structure_rejection(candidate=candidate, exc=exc)
    if str(dict(rejection or {}).get("reason") or "") not in (
        _STRUCTURED_OUTPUT_DEGENERATION_REASONS
    ):
        return None
    return rejection


def _full_regeneration_context(
    *,
    validation_feedback: str,
    candidate_rejection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mode": "full_regeneration",
        "instruction": (
            "上一版候选的结构已经不适合局部修补，平台未提供 candidate_output。"
            "必须重新读取本次原始输入，完整生成满足原输出契约的 JSON；"
            "同时修正 validation_feedback 指出的全部问题，不得拼接或猜测上一版残片。"
        ),
        "validation_feedback": validation_feedback,
        "candidate_rejection": candidate_rejection,
    }


def _repair_retry_event_fields(
    repair_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = dict(repair_context or {})
    rejection = dict(context.get("candidate_rejection") or {})
    return {
        "repair_mode": str(context.get("mode") or "none"),
        "has_repair_candidate": isinstance(context.get("candidate_output"), dict),
        "candidate_rejection_reason": str(rejection.get("reason") or ""),
    }


def _payload_size_diagnostics(value: Any) -> dict[str, int]:
    """记录请求体大小，区分真实 JSON 大小与保守额度预估。"""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return {
        "json_chars": len(encoded.decode("utf-8")),
        "json_bytes": len(encoded),
        "estimated_input_tokens": max(1, math.ceil(len(encoded) / 4)),
    }


def _project_input_value(value: Any, spec: Any) -> Any:
    """按 Agent 定义中的字段白名单生成模型视图。

    规范使用三种形式：``True`` 保留原值、字符串列表选择对象字段、字典
    递归选择嵌套字段。列表值会对每个元素应用同一子规范，原始输入不会被
    就地修改。
    """

    if spec is True or spec is None:
        return deepcopy(value)
    if isinstance(spec, list):
        field_names = [str(field).strip() for field in spec]
        if any(not field for field in field_names):
            raise ValueError("Agent input_projection 字段名不能为空")
        if isinstance(value, list):
            return [_project_input_value(item, spec) for item in value]
        if isinstance(value, dict):
            return {
                field: deepcopy(value[field])
                for field in field_names
                if field in value
            }
        return deepcopy(value)
    if isinstance(spec, dict):
        if isinstance(value, list):
            return [_project_input_value(item, spec) for item in value]
        if isinstance(value, dict):
            return {
                str(field): _project_input_value(value[field], child_spec)
                for field, child_spec in spec.items()
                if str(field) in value
            }
        return deepcopy(value)
    raise ValueError("Agent input_projection 只能使用字段列表、对象或 true")


def _project_agent_map_input(
    *,
    definition: Any,
    raw_item: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成只供模型/额度预估使用的输入视图，并保留原始大小诊断。"""

    runtime_config = dict(getattr(definition, "runtime_config", {}) or {})
    projection = runtime_config.get("input_projection")
    raw_payload = deepcopy(raw_item)
    raw_diagnostics = _payload_size_diagnostics(raw_payload)
    if projection is None:
        return raw_payload, {
            "applied": False,
            "raw_json_chars": raw_diagnostics["json_chars"],
            "model_json_chars": raw_diagnostics["json_chars"],
            "reduction_ratio": 0.0,
        }
    if not isinstance(projection, dict):
        raise ValueError("Agent runtime_config.input_projection 必须是对象")
    projected = _project_input_value(raw_payload, projection)
    if not isinstance(projected, dict):
        raise ValueError("Agent input_projection 必须生成对象输入")
    model_diagnostics = _payload_size_diagnostics(projected)
    raw_chars = int(raw_diagnostics["json_chars"])
    model_chars = int(model_diagnostics["json_chars"])
    return projected, {
        "applied": True,
        "version": str(runtime_config.get("input_projection_version") or "1"),
        "raw_json_chars": raw_chars,
        "model_json_chars": model_chars,
        "raw_json_bytes": int(raw_diagnostics["json_bytes"]),
        "model_json_bytes": int(model_diagnostics["json_bytes"]),
        "reduction_ratio": round((raw_chars - model_chars) / raw_chars, 6)
        if raw_chars
        else 0.0,
    }


def _sum_usage(total: dict[str, int], current: dict[str, int]) -> dict[str, int]:
    keys = {"requests", "input_tokens", "output_tokens", "total_tokens"}
    return {
        key: int(total.get(key, 0) or 0) + int(current.get(key, 0) or 0)
        for key in keys
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


def _normalized_quota_usage(raw: dict[str, Any] | None = None) -> dict[str, int]:
    values = dict(raw or {})
    return {
        "attempted_requests": int(values.get("attempted_requests") or 0),
        "input_tokens": int(values.get("input_tokens") or 0),
        "output_tokens": int(values.get("output_tokens") or 0),
        "total_tokens": int(values.get("total_tokens") or 0),
    }


def _agent_instance_quota_usage(run: AgentRun, quota_scope_key: str) -> dict[str, int]:
    """返回单个已激活 Agent 实例的额度账本，不从 Run 累计用量继承。"""

    ledgers = dict(dict(run.run_context or {}).get("agent_instance_quota_usage") or {})
    return _normalized_quota_usage(
        dict(ledgers.get(quota_scope_key) or {})
    )


def _store_agent_instance_quota_usage(
    run_context: dict[str, Any],
    *,
    quota_scope_key: str,
    quota_usage: dict[str, int],
) -> None:
    """写入实例分账，同时保留 Run 级汇总供展示与审计。"""

    ledgers = {
        str(key): _normalized_quota_usage(dict(value or {}))
        for key, value in dict(run_context.get("agent_instance_quota_usage") or {}).items()
    }
    ledgers[quota_scope_key] = _normalized_quota_usage(quota_usage)
    aggregate = _normalized_quota_usage()
    for ledger in ledgers.values():
        aggregate = {
            key: aggregate[key] + ledger[key]
            for key in aggregate
        }
    run_context["quota_mode"] = "per_agent_instance"
    run_context["agent_instance_quota_usage"] = ledgers
    run_context["quota_usage"] = aggregate


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


def _validate_agent_instance_quota(
    run: AgentRun,
    *,
    quota_scope_key: str,
    projected: dict[str, int],
) -> None:
    limits = dict(dict(run.run_context or {}).get("execution_limits") or {})
    comparisons = (
        ("attempted_requests", "max_requests", "请求次数"),
        ("input_tokens", "max_input_tokens", "输入 Token"),
        ("output_tokens", "max_output_tokens", "输出 Token"),
        ("total_tokens", "max_total_tokens", "总 Token"),
    )
    exceeded = [
        (label, int(projected.get(usage_key) or 0), int(limits.get(limit_key) or 0))
        for usage_key, limit_key, label in comparisons
        if int(limits.get(limit_key) or 0) > 0
        and int(projected.get(usage_key) or 0) > int(limits.get(limit_key) or 0)
    ]
    if not exceeded:
        return
    detail = "、".join(
        f"{label} {actual}/{limit}"
        for label, actual, limit in exceeded
    )
    raise _AgentQuotaExceeded(
        f"Agent 实例额度不足: instance={quota_scope_key}; {detail}"
    )


def _reserve_agent_request(
    *,
    repo: AgentPlatformRepository,
    run: AgentRun,
    node_run: AgentNodeRun,
    definition: Any,
    tools: list[Any],
    input_payload: dict[str, Any],
    quota_scope_key: str,
) -> dict[str, int]:
    usage = _run_usage(run)
    quota_usage = _agent_instance_quota_usage(run, quota_scope_key)
    reservation = _agent_request_reservation(
        definition=definition,
        tools=tools,
        input_payload=input_payload,
    )
    node_sdk_state = dict(node_run.sdk_state or {})
    node_sdk_state["request"] = {
        **_payload_size_diagnostics(input_payload),
        "reserved_input_upper_bound": reservation["input_tokens"],
        "reserved_output_tokens": reservation["output_tokens"],
        "request_timeout_seconds": dict(getattr(definition, "runtime_config", {}) or {}).get(
            "request_timeout_seconds"
        ),
    }
    node_run.sdk_state = node_sdk_state
    repo.db.add(node_run)
    projected = {
        key: quota_usage[key] + reservation[key]
        for key in quota_usage
    }
    try:
        _validate_agent_instance_quota(
            run,
            quota_scope_key=quota_scope_key,
            projected=projected,
        )
    except _AgentQuotaExceeded as exc:
        _event(
            repo,
            run,
            "agent_instance_quota_blocked",
            {
                "node_key": node_run.node_key,
                "instance_id": quota_scope_key,
                "message": str(exc),
            },
            node_run=node_run,
        )
        repo.commit()
        raise
    usage["attempted_requests"] += 1
    run_context = dict(run.run_context or {})
    run_context["usage"] = usage
    _store_agent_instance_quota_usage(
        run_context,
        quota_scope_key=quota_scope_key,
        quota_usage=projected,
    )
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
    quota_scope_key: str,
) -> None:
    usage = _run_usage(run)
    reported_requests = int(current.get("requests") or 0)
    if reported_requests > 1:
        usage["attempted_requests"] += reported_requests - 1
    summed = _sum_usage(usage, current)
    usage.update(summed)
    quota_usage = _agent_instance_quota_usage(run, quota_scope_key)
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
    run_context["usage"] = usage
    _store_agent_instance_quota_usage(
        run_context,
        quota_scope_key=quota_scope_key,
        quota_usage=reconciled_quota_usage,
    )
    run.run_context = run_context
    repo.db.add(run)
    repo.commit()


def _is_retryable_agent_error(exc: Exception) -> bool:
    if isinstance(exc, _RunDeadlineExceeded):
        return False
    if _is_server_output_schema_unsupported(exc):
        return True
    if _model_behavior_error(exc) is not None:
        return True
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
            InternalServerError,
        ),
    ):
        return True
    if isinstance(exc, BadRequestError):
        message = str(exc).lower()
        return "400001" in message or "try again" in message
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and (status_code == 429 or status_code >= 500)


def _is_server_output_schema_unsupported(exc: Exception) -> bool:
    """识别上游明确拒绝服务端结构化输出能力的错误。"""

    message = " ".join(str(exc).lower().split())
    return any(
        marker in message
        for marker in (
            "response_format type is unavailable",
            "invalid schema for response_format",
            "invalid_json_schema",
        )
    )


def _exception_chain(exc: Exception) -> tuple[BaseException, ...]:
    """返回异常及其 cause/context，识别 SDK 对业务异常的外层包装。"""

    chain: list[BaseException] = []
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _model_behavior_error(exc: Exception) -> ModelBehaviorError | None:
    return next(
        (
            current
            for current in _exception_chain(exc)
            if isinstance(current, ModelBehaviorError)
        ),
        None,
    )


def _agent_error_diagnostic(exc: Exception) -> dict[str, Any] | None:
    """提取异常提供的限长结构化诊断信息。"""

    for current in _exception_chain(exc):
        diagnostic = getattr(current, "diagnostic", None)
        if isinstance(diagnostic, dict):
            return deepcopy(diagnostic)
    return None


def _agent_failure_kind(exc: Exception) -> str:
    """将失败归入稳定类别，供事件统计和模型质量治理使用。"""

    model_error = _model_behavior_error(exc)
    tool_output_error = next(
        (
            current
            for current in _exception_chain(exc)
            if isinstance(current, ToolOutputValidationError)
        ),
        None,
    )
    message = str(model_error or exc)
    if isinstance(exc, _RunDeadlineExceeded):
        return "run_deadline"
    if isinstance(exc, (TimeoutError, APITimeoutError)):
        return "timeout"
    if isinstance(exc, (ConnectionError, APIConnectionError)):
        return "connection"
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    if _is_server_output_schema_unsupported(exc):
        return "server_schema_unsupported"
    if isinstance(exc, BadRequestError):
        normalized_message = message.lower()
        if "400001" in normalized_message or "try again" in normalized_message:
            return "upstream_transient"
        return "upstream_bad_request"
    if tool_output_error is not None:
        return "tool_contract_violation"
    if model_error is not None:
        if isinstance(model_error, StructuredOutputJSONError):
            if bool(dict(model_error.diagnostic).get("is_output_degeneration")):
                return "output_degeneration"
            return "json_syntax"
        if isinstance(model_error, StructuredOutputValidationError):
            if _structured_output_degeneration_rejection(exc) is not None:
                return "output_degeneration"
            return "output_validation"
        if isinstance(model_error, OutputPostprocessingError) or message.startswith(
            "agent_map 单项结果校验失败: postprocessor="
        ):
            return "postprocess_validation"
        if "结构化输出正文为空" in message:
            return "empty_output"
        if "不是合法 JSON" in message or "Invalid JSON when parsing" in message:
            return "json_syntax"
        if isinstance(model_error, ToolArgumentsValidationError):
            return "tool_arguments_validation"
        if "契约校验失败" in message or "结果校验失败" in message:
            return "output_validation"
        return "model_behavior"
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return "rate_limit"
    if isinstance(status_code, int) and status_code >= 500:
        return "upstream_server"
    return "unknown"


def _is_concurrency_pressure_failure(failure_kind: str) -> bool:
    """只把可能由上游负载放大的失败用于并发降载。"""

    return failure_kind in _CONCURRENCY_PRESSURE_FAILURE_KINDS


def _is_model_route_health_failure(failure_kind: str) -> bool:
    """识别应促使单个映射项切换模型路由的可重试失败。"""

    return failure_kind in _MODEL_ROUTE_HEALTH_FAILURE_KINDS


def _agent_map_retry_delay(*, item_attempt: int, instance_id: str) -> float:
    """为映射项重试增加短退避和稳定抖动，避免同一批请求同时回冲上游。"""

    base_delay = min(30.0, float(2 ** max(1, item_attempt)))
    jitter_seed = hashlib.sha256(instance_id.encode("utf-8")).digest()[0]
    return base_delay + (jitter_seed % 4) * 0.5


def _agent_transient_fallback_config(definition: Any) -> tuple[str | None, int]:
    """读取 Agent 显式声明的瞬态失败备用路由，不在运行时猜测模型。"""

    runtime_config = dict(getattr(definition, "runtime_config", {}) or {})
    route = str(
        runtime_config.get("transient_fallback_model_route") or ""
    ).strip().lower()
    threshold = max(
        1,
        int(runtime_config.get("transient_fallback_after_failures") or 2),
    )
    return (route or None), threshold


def _structured_output_validation_feedback(exc: Exception) -> str | None:
    """把同一候选的全部 JSON Schema 违规一次性反馈给下一轮模型。"""

    schema_error = next(
        (
            current
            for current in _exception_chain(exc)
            if isinstance(current, StructuredOutputValidationError)
        ),
        None,
    )
    if schema_error is None:
        return None
    candidate = getattr(schema_error, "candidate_output", None)
    output_schema = dict(getattr(schema_error, "output_schema", {}) or {})
    if not isinstance(candidate, dict) or not output_schema:
        return None
    try:
        validator_class = validator_for(output_schema)
        violations = sorted(
            validator_class(output_schema).iter_errors(candidate),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except Exception:
        # 异常本身仍可提供首个字段信息，不能因为诊断器失败阻断重试。
        violations = []
    if not violations:
        return None

    details: list[str] = []
    seen_required_fields: set[tuple[str, str]] = set()
    for violation in violations[:5]:
        path = ".".join(str(part) for part in violation.absolute_path) or "<root>"
        if violation.validator == "required" and isinstance(violation.instance, dict):
            missing_fields = [
                str(field)
                for field in list(violation.validator_value or [])
                if field not in violation.instance
            ]
            if missing_fields:
                # jsonschema 对同一对象的每个缺失字段各产生一条错误，但
                # 每条错误的 validator_value 都带着完整 required 列表；优先
                # 从 message 定位当前字段，避免把整张列表重复展开。
                message = str(violation.message)
                field = next(
                    (
                        candidate
                        for candidate in missing_fields
                        if f"'{candidate}'" in message
                        or f'"{candidate}"' in message
                    ),
                    None,
                )
                if field is None:
                    field = next(
                        (
                            candidate
                            for candidate in missing_fields
                            if (path, candidate) not in seen_required_fields
                        ),
                        None,
                    )
                if field is not None:
                    detail_key = (path, field)
                    if detail_key not in seen_required_fields:
                        details.append(f"{path}.{field}: 缺少必填字段")
                        seen_required_fields.add(detail_key)
                continue
        details.append(f"{path}: {str(violation.message).strip()[:500]}")
    if len(violations) > 5:
        details.append(f"其余 {len(violations) - 5} 个结构错误未展开")
    feedback = (
        "上次输出未通过平台校验：同一候选发现 "
        f"{len(violations)} 个结构错误，必须一次性全部修正："
        + "；".join(details)
    )
    # 根据实际 Schema 给出最小骨架提示，避免把生成契约的字段规则误套到
    # 修复、评审等相邻 Agent。字段列表来自契约本身，不在运行时硬编码。
    schema_properties = dict(output_schema.get("properties") or {})

    def _feedback_schema_fields(schema: dict[str, Any]) -> list[str]:
        """只展示模型真正需要填写的字段，隐藏可由平台派生的可选字段。"""

        properties = dict(schema.get("properties") or {})
        required = {
            str(field)
            for field in list(schema.get("required") or [])
        }
        return [
            str(field)
            for field, field_schema in properties.items()
            if not (
                isinstance(field_schema, dict)
                and bool(field_schema.get("x-platform-derived"))
                and str(field) not in required
            )
        ]

    if schema_properties and output_schema.get("additionalProperties") is False:
        allowed_top_level = ", ".join(_feedback_schema_fields(output_schema))
        feedback += f"。顶层只允许字段：{allowed_top_level}"
    cases_schema = schema_properties.get("test_cases")
    if isinstance(cases_schema, dict):
        case_schema = cases_schema.get("items")
        if isinstance(case_schema, dict):
            case_properties = dict(case_schema.get("properties") or {})
            if case_properties and case_schema.get("additionalProperties") is False:
                allowed_case_fields = ", ".join(_feedback_schema_fields(case_schema))
                feedback += f"；test_cases 每项只允许字段：{allowed_case_fields}"
            steps_schema = case_properties.get("steps")
            if isinstance(steps_schema, dict):
                step_schema = steps_schema.get("items")
                if isinstance(step_schema, dict):
                    required_step_fields = [
                        str(field)
                        for field in list(step_schema.get("required") or [])
                    ]
                    if required_step_fields:
                        feedback += (
                            "；每个步骤必须包含："
                            + ", ".join(required_step_fields)
                        )
    return feedback[:_AGENT_RETRY_FEEDBACK_TEXT_LIMIT]


def _agent_retry_feedback(exc: Exception) -> str | None:
    """只把模型输出校验错误反馈给下一次尝试，网络类错误不应污染业务输入。"""

    model_error = _model_behavior_error(exc)
    if model_error is None:
        return None
    structured_feedback = _structured_output_validation_feedback(model_error)
    if structured_feedback:
        return structured_feedback
    current: BaseException | None = model_error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, PydanticValidationError):
            details: list[str] = []
            for error in current.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )[:8]:
                location = ".".join(str(part) for part in error.get("loc") or ()) or "<root>"
                details.append(
                    f"字段 {location}: {error.get('msg') or '校验失败'}"
                    f" ({error.get('type') or 'validation_error'})"
                )
            if details:
                return (
                    "上次输出未通过平台校验："
                    + "；".join(details)[:_AGENT_RETRY_FEEDBACK_TEXT_LIMIT]
                )
        current = current.__cause__ or current.__context__
    message = " ".join(str(model_error).split())
    if not message:
        return None
    if len(message) > _AGENT_RETRY_FEEDBACK_TEXT_LIMIT:
        prefix_chars = _AGENT_RETRY_FEEDBACK_TEXT_LIMIT // 3
        suffix_chars = _AGENT_RETRY_FEEDBACK_TEXT_LIMIT - prefix_chars - 3
        message = f"{message[:prefix_chars]} … {message[-suffix_chars:]}"
    return f"上次输出未通过平台校验：{message}"


def _preserved_agent_retry_feedback(
    previous_feedback: str | None,
    exc: Exception,
) -> str | None:
    """内容错误只描述最新候选；网络错误保留尚未执行的修正要求。"""

    current_feedback = _agent_retry_feedback(exc)
    if current_feedback:
        return current_feedback
    previous = str(previous_feedback or "").strip()
    return previous or None


def _merge_agent_retry_feedback(
    previous_feedback: str | None,
    current_feedback: str | None,
) -> str | None:
    """累积不同轮次的修复约束，并优先保留最新反馈。"""

    previous = str(previous_feedback or "").strip()
    current = str(current_feedback or "").strip()
    if not current:
        return previous or None
    if not previous or current == previous:
        return current[:_AGENT_RETRY_FEEDBACK_TEXT_LIMIT]
    if current in previous:
        return previous[-_AGENT_RETRY_FEEDBACK_TEXT_LIMIT:]
    separator = "\n"
    current = current[-_AGENT_RETRY_FEEDBACK_TEXT_LIMIT:]
    remaining = _AGENT_RETRY_FEEDBACK_TEXT_LIMIT - len(current) - len(separator)
    if remaining <= 0:
        return current
    return f"{previous[-remaining:]}{separator}{current}"


def _agent_map_item_retry_feedback(
    *,
    previous_feedback: str | None,
    exc: Exception,
    item_input: dict[str, Any],
) -> str | None:
    """领域补充说明由错误源头声明的策略生成，网络错误保留已有反馈。"""

    feedback = _preserved_agent_retry_feedback(previous_feedback, exc)
    strategy_error = _output_repair_error(exc)
    if strategy_error is None:
        return feedback
    strategy = tool_registry.resolve_repair_strategy(strategy_error.strategy_key)
    return _merge_agent_retry_feedback(
        feedback, strategy.feedback(item_input, strategy_error.details),
    )


def _agent_retry_decision(
    state: RetryAttemptState, *, exc: Exception, configured_max_attempts: int,
) -> RetryDecision:
    return state.record_failure(
        failure_kind=_agent_failure_kind(exc),
        retryable=_is_retryable_agent_error(exc),
        is_content_error=_model_behavior_error(exc) is not None,
        configured_max_attempts=configured_max_attempts,
    )


def _agent_failure_recovery(
    *,
    decision: RetryDecision,
    result: Any,
    exc: Exception,
    item_input: dict[str, Any],
    item_attempt: int,
    previous_feedback: str | None,
    previous_repair_context: dict[str, Any] | None,
    validation_diagnostics: list[dict[str, Any]],
) -> tuple[Exception, bool, str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """各执行器使用同一候选历史决定最小修补、完整重生成和终止。"""

    feedback = _agent_map_item_retry_feedback(
        previous_feedback=previous_feedback, exc=exc, item_input=item_input,
    )
    diagnostics = list(validation_diagnostics)
    if _model_behavior_error(exc) is None:
        return exc, decision.can_retry, feedback, previous_repair_context, diagnostics
    diagnostic = _agent_map_output_diagnostic(
        result=result, item_attempt=item_attempt, exc=exc,
    )
    candidate_validated = result is not None or isinstance(_model_behavior_error(exc), OutputPostprocessingError)
    # 只有结构校验通过的候选才进入最小修补重复检测，结构退化按独立生成预算处理。
    candidate_hash = (
        diagnostic.get("output_text_sha256")
        if candidate_validated and diagnostic.get("output_text_chars") else None
    )
    repeated_output = bool(candidate_hash) and any(
        previous.get("output_text_sha256") == candidate_hash for previous in diagnostics
    )
    diagnostics = [*diagnostics, diagnostic][-_AGENT_MAP_DIAGNOSTIC_ATTEMPT_LIMIT:]
    if repeated_output:
        mode = str(dict(previous_repair_context or {}).get("mode") or "")
        if decision.repeated_output_action(mode) == "full_regeneration":
            repair = _full_regeneration_context(
                validation_feedback=feedback or str(exc),
                candidate_rejection={
                    "reason": "repeated_invalid_after_minimal_patch",
                    "repeated_output_sha256": candidate_hash,
                },
            )
            return exc, True, feedback, repair, diagnostics
        stop_reason = "完整重生成后仍未修正" if mode == "full_regeneration" else "重试预算已耗尽"
        duplicate_error = ModelBehaviorError(
            f"智能体返回完全相同的无效结果，{stop_reason}，已停止重复调用；原校验错误：{exc}"
        )
        duplicate_error.__cause__ = exc
        return duplicate_error, False, feedback, previous_repair_context, diagnostics
    repair = _agent_map_repair_context(
        result=result, exc=exc, validation_feedback=feedback, item_input=item_input,
    )
    return exc, decision.can_retry, feedback, repair or previous_repair_context, diagnostics


def _postprocess_agent_map_output(
    *,
    config: Any,
    definition: Any,
    execution_context: ToolExecutionContext,
    item_input: dict[str, Any],
    item_output: dict[str, Any],
) -> dict[str, Any]:
    """在映射项落盘前执行数据驱动的规范化与校验。"""

    item_output = _postprocess_agent_output(
        agent_definition=definition,
        execution_context=execution_context,
        input_payload=item_input,
        output=item_output,
    )
    handler_key = str(getattr(config, "item_postprocessor", None) or "").strip()
    if not handler_key:
        return dict(item_output)
    handler = tool_registry.resolve(handler_key)
    try:
        normalized = handler(
            execution_context,
            {
                "item_input": deepcopy(item_input),
                "item_output": deepcopy(item_output),
            },
        )
    except Exception as exc:
        # 后处理失败说明本次模型结果不可用，按模型行为错误执行该映射项的独立重试。
        raise OutputPostprocessingError(
            output=item_output, postprocessor=handler_key, cause=exc,
            message_prefix="agent_map 单项结果校验失败",
        ) from exc
    if not isinstance(normalized, dict):
        raise ModelBehaviorError(
            f"agent_map 单项后处理器必须返回对象: postprocessor={handler_key}"
        )
    return normalized


def _agent_map_item_label(item_input: dict[str, Any], *, item_index: int) -> str:
    """从真实映射输入生成稳定的人类可读任务标识。"""

    source_kind = str(item_input.get("source_kind") or "")
    if source_kind == "document":
        return f"文档第 {int(item_input.get('page_number') or 0)} 页"
    if source_kind == "document_batch":
        pages = [
            int(dict(page).get("page_number") or 0)
            for page in list(item_input.get("pages") or [])
        ]
        return "文档页 " + "、".join(str(page) for page in pages if page > 0)
    raw_batch = item_input.get("batch")
    batch = dict(raw_batch) if isinstance(raw_batch, dict) else {}
    if batch.get("batch_id"):
        return f"用例批次 {batch['batch_id']}"
    return f"分配任务 {item_index + 1}"


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
            if node.node_type not in {"agent", "agent_network"}:
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

            latest.status = "failed"
            latest.error_message = _persistent_error_message(exc)
            latest.finished_at = _now()
            latest_sdk_state = dict(latest.sdk_state or {})
            retry_state = RetryAttemptState.restore(latest_sdk_state)
            retry_state.attempt = latest.attempt
            decision = _agent_retry_decision(
                retry_state, exc=exc, configured_max_attempts=node.max_attempts,
            )
            exc, can_retry, retry_feedback, repair_context, diagnostics = _agent_failure_recovery(
                decision=decision,
                result=None,
                exc=exc,
                item_attempt=latest.attempt,
                item_input=dict(latest.input_payload or {}),
                previous_feedback=latest_sdk_state.get("retry_feedback"),
                previous_repair_context=latest_sdk_state.get("repair_context"),
                validation_diagnostics=_recent_agent_map_diagnostics(latest_sdk_state),
            )
            latest_sdk_state.update(retry_state.checkpoint())
            latest_sdk_state["retry_exhausted"] = not can_retry
            if retry_feedback:
                latest_sdk_state["retry_feedback"] = retry_feedback
            if repair_context is not None:
                latest_sdk_state["repair_context"] = repair_context
                latest_sdk_state["input_hash"] = _payload_hash(latest.input_payload or {})
            if diagnostics:
                latest_sdk_state["validation_diagnostics"] = diagnostics
            error_diagnostic = _agent_error_diagnostic(exc)
            if error_diagnostic:
                latest_sdk_state["error_diagnostic"] = error_diagnostic
            latest.sdk_state = latest_sdk_state
            latest.error_message = _persistent_error_message(exc)
            failed_event_payload = {
                "node_key": node.node_key,
                "node_type": node.node_type,
                "attempt": latest.attempt,
                "retryable": decision.retryable,
                "error_type": type(exc).__name__,
                "failure_kind": decision.failure_kind,
                "message": str(exc)[:1000],
            }
            if error_diagnostic:
                failed_event_payload["error_diagnostic"] = error_diagnostic
            _event(
                repo,
                active_run,
                "node_failed",
                failed_event_payload,
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
                        **_repair_retry_event_fields(latest_sdk_state.get("repair_context")),
                    },
                    node_run=latest,
                )
            repo.db.add(latest)
            repo.commit()
            if not can_retry:
                raise exc

            run = active_run
            time.sleep(min(2 ** (latest.attempt - 1), 4))


async def _run_parallel_agent_instance(
    *,
    definition_id: int,
    execution_context: ToolExecutionContext,
    item_input: dict[str, Any],
    instance_id: str,
    request_timeout_seconds: float,
    retry_feedback: str | None = None,
    disable_server_output_schema: bool = False,
    disable_model_thinking: bool = False,
    model_route_override: str | None = None,
) -> Any:
    """使用独立数据库会话和 SDK 上下文执行一个并发 Agent 实例。"""

    worker_db = SessionLocal()
    try:
        worker_definition = worker_db.get(AgentDefinition, definition_id)
        if worker_definition is None or not worker_definition.enabled:
            raise LookupError(f"并发 Agent 定义不可用: definition_id={definition_id}")
        worker_context = ToolExecutionContext(
            db=worker_db,
            user_id=execution_context.user_id,
            project_id=execution_context.project_id,
            run_id=execution_context.run_id,
            node_key=instance_id,
            run_input=deepcopy(execution_context.run_input),
            artifacts=deepcopy(execution_context.artifacts),
        )
        worker_tools = AgentPlatformRepository(worker_db).list_agent_tools(
            worker_definition.id,
            project_id=execution_context.project_id,
        )
        return await run_agent_async(
            db=worker_db,
            agent_definition=worker_definition,
            tool_definitions=worker_tools,
            execution_context=worker_context,
            input_payload=item_input,
            request_timeout_seconds=request_timeout_seconds,
            retry_feedback=retry_feedback,
            disable_server_output_schema=disable_server_output_schema,
            disable_model_thinking=disable_model_thinking,
            model_route_override=model_route_override,
            skip_output_postprocessor=True,
        )
    finally:
        worker_db.close()


async def _invoke_parallel_agent_instance(**arguments: Any) -> Any:
    """生产实现为原生协程；同步替身只用于测试或受控扩展。"""

    if inspect.iscoroutinefunction(_run_parallel_agent_instance):
        return await _run_parallel_agent_instance(**arguments)
    return await asyncio.to_thread(_run_parallel_agent_instance, **arguments)


def _unsafe_parallel_tool_keys(tools: list[Any]) -> list[str]:
    """只放行处理器显式声明为并发安全的工具。"""

    unsafe: list[str] = []
    for tool in tools:
        tool_key = str(getattr(tool, "tool_key", "") or "<unknown>")
        handler_key = str(getattr(tool, "handler_key", "") or "")
        if not handler_key or not tool_registry.is_parallel_safe(handler_key):
            unsafe.append(tool_key)
    return unsafe


async def _execute_agent_map_parallel_async(
    *,
    repo: AgentPlatformRepository,
    run: AgentRun,
    node: WorkflowNode,
    node_run: AgentNodeRun,
    definition: Any,
    model_metadata: dict[str, str],
    tools: list[Any],
    execution_context: ToolExecutionContext,
    raw_items: list[dict[str, Any]],
    previous: AgentNodeRun | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """并发启动多个独立 Agent 实例，并在主会话中有序归并结果。"""

    config = node.map_config
    if config is None:
        raise ValueError(f"agent_map 节点缺少配置: {node.node_key}")
    unsafe_tool_keys = _unsafe_parallel_tool_keys(tools)
    if unsafe_tool_keys:
        raise ValueError(
            "并发 agent_map 不允许使用未声明并发安全的工具: "
            f"node={node.node_key}, tool_keys={unsafe_tool_keys}"
        )
    definition_id = int(getattr(definition, "id", 0) or 0)
    if definition_id < 1:
        raise ValueError(f"并发 agent_map 缺少已持久化 Agent 定义: node={node.node_key}")

    previous_output = dict(previous.output_payload or {}) if previous is not None else {}
    previous_records = list(previous_output.get(config.output_key) or [])
    completed_by_index: dict[int, dict[str, Any]] = {}
    for fallback_index, raw_record in enumerate(previous_records):
        if not isinstance(raw_record, dict):
            raise ValueError("agent_map 已持久化结果必须是对象")
        record = dict(raw_record)
        item_index = int(record.get("item_index", fallback_index))
        if item_index < 0 or item_index >= len(raw_items) or item_index in completed_by_index:
            raise ValueError(f"agent_map 已持久化结果索引无效: item_index={item_index}")
        if record.get("input_hash") != _payload_hash(raw_items[item_index]):
            raise ValueError("agent_map 重试输入与已持久化部分结果不一致")
        completed_by_index[item_index] = record

    previous_state = dict(previous.sdk_state or {}) if previous is not None else {}
    aggregate_usage = dict(previous_state.get("usage") or {})
    attempted_requests = int(previous_state.get("attempted_requests") or 0)
    failure_counts = {
        str(key): int(value)
        for key, value in dict(previous_state.get("failure_counts") or {}).items()
    }
    item_states_by_index = {
        int(item["item_index"]): dict(item)
        for item in list(previous_state.get("items") or [])
        if isinstance(item, dict) and "item_index" in item
    }
    total_count = len(raw_items)
    max_concurrency = min(
        config.max_concurrency,
        int(settings.AGENT_MAP_MAX_CONCURRENCY),
        max(1, total_count),
    )
    min_concurrency = min(
        max_concurrency,
        max(1, int(settings.AGENT_MAP_MIN_CONCURRENCY)),
    )
    previous_parallelism = dict(previous_state.get("parallelism") or {})
    effective_concurrency = min(
        max_concurrency,
        max(
            min_concurrency,
            int(previous_parallelism.get("effective_concurrency") or max_concurrency),
        ),
    )
    concurrency_adjustments = [
        dict(item)
        for item in list(previous_state.get("concurrency_adjustments") or [])[-20:]
        if isinstance(item, dict)
    ]
    successful_since_concurrency_adjustment = 0
    pressure_failures_since_concurrency_adjustment = max(
        0,
        int(previous_parallelism.get("pressure_failures") or 0),
    )
    pending: dict[
        asyncio.Task[Any],
        tuple[int, int, dict[str, int], str, dict[str, Any]],
    ] = {}
    ready = deque(index for index in range(total_count) if index not in completed_by_index)
    retry_ready: deque[int] = deque()
    ready_at = {index: 0.0 for index in ready}
    retry_states_by_index = {
        index: RetryAttemptState.restore(item_states_by_index.get(index, {}))
        for index in range(total_count)
    }
    for index in ready:
        retry_states_by_index[index].require_retry_budget(configured_max_attempts=node.max_attempts)
    retry_history_by_index = {
        index: [
            dict(entry)
            for entry in list(
                item_states_by_index.get(index, {}).get("retry_history") or []
            )[-10:]
            if isinstance(entry, dict)
        ]
        for index in range(total_count)
    }
    fallback_route_by_index = {
        index: str(
            item_states_by_index.get(index, {}).get("model_route_override") or ""
        ).strip()
        for index in range(total_count)
        if str(
            item_states_by_index.get(index, {}).get("model_route_override") or ""
        ).strip()
    }
    configured_model_route = str(model_metadata.get("route") or "main")
    transient_fallback_route, transient_fallback_threshold = (
        _agent_transient_fallback_config(definition)
    )
    if transient_fallback_route:
        for index, retry_state in retry_states_by_index.items():
            if retry_state.route_health_failure_count >= transient_fallback_threshold:
                fallback_route_by_index.setdefault(index, transient_fallback_route)
    restored_retry_contexts = {
        index: _restored_agent_retry_context(item_states_by_index.get(index, {}), raw_items[index])
        for index in ready
    }
    retry_feedback_by_index = {
        index: feedback for index, (feedback, _) in restored_retry_contexts.items() if feedback
    }
    repair_context_by_index = {
        index: repair for index, (_, repair) in restored_retry_contexts.items() if repair is not None
    }
    configured_schema_fallback = bool(
        dict(getattr(definition, "runtime_config", {}) or {}).get(
            "disable_server_output_schema"
        )
    )
    if configured_schema_fallback:
        for retry_state in retry_states_by_index.values():
            retry_state.server_output_schema_disabled = True
    fatal_error: Exception | None = None
    cancellation_requested = False

    def ordered_records() -> list[dict[str, Any]]:
        return [completed_by_index[index] for index in sorted(completed_by_index)]

    def ordered_states() -> list[dict[str, Any]]:
        states = []
        for index in sorted(item_states_by_index):
            state = dict(item_states_by_index[index])
            state.update(retry_states_by_index[index].checkpoint())
            if index not in completed_by_index:
                state["input_hash"] = _payload_hash(raw_items[index])
                state["retry_feedback"] = retry_feedback_by_index.get(index)
                state["repair_context"] = deepcopy(repair_context_by_index.get(index))
            states.append(state)
        return states

    def current_output() -> dict[str, Any]:
        return {
            config.output_key: ordered_records(),
            "completed_count": len(completed_by_index),
            "total_count": total_count,
        }

    def current_sdk_state() -> dict[str, Any]:
        states = ordered_states()
        successful = [item for item in states if item.get("status") == "success"]
        return {
            "last_agent_name": successful[-1].get("last_agent_name", "") if successful else "",
            "model": model_metadata,
            "usage": aggregate_usage,
            "attempted_requests": attempted_requests,
            "failure_counts": failure_counts,
            "concurrency_adjustments": concurrency_adjustments,
            "items": states,
            "parallelism": {
                "max_concurrency": max_concurrency,
                "min_concurrency": min_concurrency,
                "effective_concurrency": effective_concurrency,
                "pressure_failures": pressure_failures_since_concurrency_adjustment,
                "active_instances": len(pending),
                "retry_waiting_instances": sum(
                    item.get("status") == "retrying" for item in states
                ),
                "completed_instances": len(completed_by_index),
                "total_instances": total_count,
            },
        }

    def take_ready_index() -> int | None:
        """优先取已到期重试，再取尚未执行项；退避期间不阻塞新任务。"""

        now = time.monotonic()
        for queue in (retry_ready, ready):
            for _ in range(len(queue)):
                index = queue.popleft()
                if ready_at.get(index, 0.0) <= now:
                    ready_at.pop(index, None)
                    return index
                queue.append(index)
        return None

    def next_ready_delay() -> float | None:
        indexes = [*retry_ready, *ready]
        if not indexes:
            return None
        return max(
            0.0,
            min(ready_at.get(index, 0.0) for index in indexes) - time.monotonic(),
        )

    def persist_progress() -> None:
        try:
            _renew_progress_lease(repo, run)
        except _RunCancelled:
            _mark_node_cancelled(repo, run, node_run, output_payload=current_output(), sdk_state=current_sdk_state())
            raise
        node_run.output_payload = current_output()
        node_run.sdk_state = current_sdk_state()
        repo.db.add(node_run)
        repo.db.add(run)
        repo.commit()

    def adjust_concurrency(*, target: int, reason: str, failure_kind: str = "") -> None:
        """记录实际并发变化，使重试调度与诊断共享同一事实。"""

        nonlocal effective_concurrency, successful_since_concurrency_adjustment
        nonlocal pressure_failures_since_concurrency_adjustment
        normalized_target = min(max_concurrency, max(min_concurrency, int(target)))
        if normalized_target == effective_concurrency:
            return
        previous_concurrency = effective_concurrency
        effective_concurrency = normalized_target
        successful_since_concurrency_adjustment = 0
        pressure_failures_since_concurrency_adjustment = 0
        adjustment = {
            "from": previous_concurrency,
            "to": effective_concurrency,
            "reason": reason,
            "failure_kind": failure_kind,
            "completed_instances": len(completed_by_index),
        }
        concurrency_adjustments.append(adjustment)
        del concurrency_adjustments[:-20]
        _event(
            repo,
            run,
            "map_concurrency_adjusted",
            {
                "node_key": node.node_key,
                **adjustment,
            },
            node_run=node_run,
        )

    async def cancel_pending_instances(*, reason: str) -> None:
        """取消正在执行和等待重试的实例，并同步持久化实例终态。"""

        pending_entries = list(pending.items())
        pending_indexes = {metadata[0] for _, metadata in pending_entries}
        cancellation_requested_by_task: dict[asyncio.Task[Any], bool] = {}
        for task, _ in pending_entries:
            cancellation_requested_by_task[task] = task.cancel()
        if pending_entries:
            await asyncio.gather(
                *(task for task, _ in pending_entries),
                return_exceptions=True,
            )
        for task, (
            index,
            item_attempt,
            _reservation,
            instance_id,
            _request_diagnostics,
        ) in pending_entries:
            previous_state = dict(item_states_by_index.get(index) or {})
            item_states_by_index[index] = {
                **previous_state,
                "item_index": index,
                "instance_id": instance_id,
                "status": "cancelled",
                "item_attempt": item_attempt,
                "cancellation_reason": reason,
                "task_cancelled": task.cancelled(),
            }
            _event(
                repo,
                run,
                "map_item_cancelled",
                {
                    "node_key": node.node_key,
                    "instance_id": instance_id,
                    "item_index": index,
                    "item_number": index + 1,
                    "item_total": total_count,
                    "item_attempt": item_attempt,
                    "reason": reason,
                    "cancellation_requested": cancellation_requested_by_task[task],
                    "task_cancelled": task.cancelled(),
                },
                node_run=node_run,
            )
        for index, previous_state in sorted(item_states_by_index.items()):
            if index in pending_indexes or previous_state.get("status") != "retrying":
                continue
            instance_id = str(
                previous_state.get("instance_id")
                or f"{node.node_key}-instance-{index + 1:03d}"
            )
            item_attempt = int(previous_state.get("item_attempt") or retry_states_by_index[index].attempt or 1)
            item_states_by_index[index] = {
                **previous_state,
                "status": "cancelled",
                "cancellation_reason": reason,
                "task_cancelled": False,
                "queued_retry_cancelled": True,
            }
            _event(
                repo,
                run,
                "map_item_cancelled",
                {
                    "node_key": node.node_key,
                    "instance_id": instance_id,
                    "item_index": index,
                    "item_number": index + 1,
                    "item_total": total_count,
                    "item_attempt": item_attempt,
                    "reason": reason,
                    "cancellation_requested": False,
                    "task_cancelled": False,
                    "queued_retry_cancelled": True,
                },
                node_run=node_run,
            )
        ready.clear()
        retry_ready.clear()
        ready_at.clear()
        pending.clear()
        persist_progress()

    last_heartbeat_at = time.monotonic()
    while ready or retry_ready or pending:
        remaining_seconds = _remaining_run_seconds(run)
        if remaining_seconds <= 0:
            fatal_error = _RunDeadlineExceeded(
                f"Agent 节点已达到阶段预算, node={node.node_key}"
            )
            ready.clear()
            retry_ready.clear()
            await cancel_pending_instances(reason="run_deadline")
            break
        if not cancellation_requested and _refresh_run_is_cancelled(repo, run):
            cancellation_requested = True
            ready.clear()
            retry_ready.clear()
            await cancel_pending_instances(reason="run_cancelled")
            break

        else:
            while (
                (retry_ready or ready)
                and len(pending) < effective_concurrency
                and fatal_error is None
                and not cancellation_requested
            ):
                index = take_ready_index()
                if index is None:
                    break
                retry_states_by_index[index].attempt += 1
                item_attempt = retry_states_by_index[index].attempt
                raw_item_input = dict(raw_items[index])
                item_input, projection_diagnostics = _project_agent_map_input(
                    definition=definition,
                    raw_item=raw_item_input,
                )
                repair_context = repair_context_by_index.get(index)
                if repair_context:
                    item_input["_platform_repair"] = deepcopy(repair_context)
                instance_id = f"{node.node_key}-instance-{index + 1:03d}"
                task_label = _agent_map_item_label(raw_item_input, item_index=index)
                try:
                    request_timeout = _agent_map_request_timeout_seconds(
                        run,
                        definition,
                        node_key=node.node_key,
                        item_attempt=item_attempt,
                    )
                    reservation = _reserve_agent_request(
                        repo=repo,
                        run=run,
                        node_run=node_run,
                        definition=definition,
                        tools=tools,
                        input_payload=item_input,
                        quota_scope_key=instance_id,
                    )
                    attempted_requests += 1
                except Exception as exc:
                    failure_kind = _agent_failure_kind(exc)
                    failure_counts[failure_kind] = failure_counts.get(failure_kind, 0) + 1
                    item_states_by_index[index] = {
                        "item_index": index,
                        "instance_id": instance_id,
                        "status": "failed",
                        "item_attempt": item_attempt,
                        "error_type": type(exc).__name__,
                        "failure_kind": failure_kind,
                    }
                    fatal_error = exc
                    ready.clear()
                    retry_ready.clear()
                    break
                request_diagnostics = {
                    **projection_diagnostics,
                    **_payload_size_diagnostics(item_input),
                    "reserved_input_upper_bound": reservation["input_tokens"],
                    "reserved_output_tokens": reservation["output_tokens"],
                    "request_timeout_seconds": request_timeout,
                    "model_route": fallback_route_by_index.get(
                        index,
                        configured_model_route,
                    ),
                }
                _event(
                    repo,
                    run,
                    "map_item_started",
                    {
                        "node_key": node.node_key,
                        "instance_id": instance_id,
                        "item_index": index,
                        "item_number": index + 1,
                        "item_total": total_count,
                        "item_attempt": item_attempt,
                        "max_concurrency": max_concurrency,
                        "effective_concurrency": effective_concurrency,
                        "request": request_diagnostics,
                        "task_label": task_label,
                    },
                    node_run=node_run,
                )
                validation_diagnostics = _recent_agent_map_diagnostics(
                    item_states_by_index.get(index)
                )
                item_states_by_index[index] = {
                    "item_index": index,
                    "instance_id": instance_id,
                    "status": "running",
                    "item_attempt": item_attempt,
                    "request": request_diagnostics,
                    "task_label": task_label,
                    "model_route": request_diagnostics["model_route"],
                }
                if retry_history_by_index[index]:
                    item_states_by_index[index]["retry_history"] = list(
                        retry_history_by_index[index]
                    )
                item_states_by_index[index].update(retry_states_by_index[index].checkpoint())
                if index in fallback_route_by_index:
                    item_states_by_index[index]["model_route_override"] = (
                        fallback_route_by_index[index]
                    )
                if validation_diagnostics:
                    item_states_by_index[index]["validation_diagnostics"] = (
                        validation_diagnostics
                    )
                task = asyncio.get_running_loop().create_task(
                    _invoke_parallel_agent_instance(
                        definition_id=definition_id,
                        execution_context=execution_context,
                        item_input=item_input,
                        instance_id=instance_id,
                        request_timeout_seconds=request_timeout,
                        retry_feedback=retry_feedback_by_index.get(index),
                        disable_server_output_schema=retry_states_by_index[index].server_output_schema_disabled,
                        disable_model_thinking=retry_states_by_index[index].model_thinking_disabled,
                        model_route_override=fallback_route_by_index.get(index),
                    )
                )
                pending[task] = (
                    index,
                    item_attempt,
                    reservation,
                    instance_id,
                    request_diagnostics,
                )
            persist_progress()
            last_heartbeat_at = time.monotonic()

            if fatal_error is not None:
                await cancel_pending_instances(reason="sibling_failed")
                break
            if not pending:
                delay = next_ready_delay()
                if delay is None:
                    break
                if delay > 0:
                    await asyncio.sleep(
                        min(delay, 1.0, max(0.0, _remaining_run_seconds(run)))
                    )
                continue
            ready_delay = next_ready_delay()
            wait_timeout = min(1.0, remaining_seconds)
            if ready_delay is not None:
                wait_timeout = min(wait_timeout, ready_delay)
            done, _ = await asyncio.wait(
                tuple(pending),
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                if time.monotonic() - last_heartbeat_at >= RUN_HEARTBEAT_SECONDS:
                    persist_progress()
                    last_heartbeat_at = time.monotonic()
                continue
            for task in done:
                (
                    index,
                    item_attempt,
                    reservation,
                    instance_id,
                    request_diagnostics,
                ) = pending.pop(task)
                item_input = dict(raw_items[index])
                repair_context = repair_context_by_index.get(index)
                result = None
                postprocessing_output = False
                try:
                    result = task.result()
                    aggregate_usage = _sum_usage(aggregate_usage, result.usage)
                    _record_agent_usage(
                        repo=repo,
                        run=run,
                        node_run=node_run,
                        current=result.usage,
                        reservation=reservation,
                        quota_scope_key=instance_id,
                    )
                    postprocessing_output = True
                    guarded_output = _restore_protected_repair_slots(
                        item_output=dict(result.output),
                        repair_context=repair_context_by_index.get(index),
                    )
                    normalized_output = _postprocess_agent_map_output(
                        config=config,
                        definition=definition,
                        execution_context=execution_context,
                        item_input=item_input,
                        item_output=guarded_output,
                    )
                    postprocessing_output = False
                    validation_diagnostics = _recent_agent_map_diagnostics(
                        item_states_by_index.get(index)
                    )
                    completed_by_index[index] = {
                        "item_index": index,
                        "input_hash": _payload_hash(raw_items[index]),
                        "output": normalized_output,
                    }
                    item_states_by_index[index] = {
                        "item_index": index,
                        "instance_id": instance_id,
                        "status": "success",
                        "item_attempt": item_attempt,
                        "last_agent_name": result.last_agent_name,
                        "usage": result.usage,
                        "reservation": reservation,
                        "tool_calls": result.tool_calls,
                        "task_label": _agent_map_item_label(item_input, item_index=index),
                        "model_route": request_diagnostics["model_route"],
                    }
                    if retry_history_by_index[index]:
                        item_states_by_index[index]["retry_history"] = list(
                            retry_history_by_index[index]
                        )
                    item_states_by_index[index].update(retry_states_by_index[index].checkpoint())
                    if index in fallback_route_by_index:
                        item_states_by_index[index]["model_route_override"] = (
                            fallback_route_by_index[index]
                        )
                    if validation_diagnostics:
                        item_states_by_index[index]["validation_diagnostics"] = (
                            validation_diagnostics
                        )
                    _event(
                        repo,
                        run,
                        "map_item_completed",
                        {
                            "node_key": node.node_key,
                            "instance_id": instance_id,
                            "item_index": index,
                            "item_number": index + 1,
                            "item_total": total_count,
                        },
                        node_run=node_run,
                    )
                    successful_since_concurrency_adjustment += 1
                    pressure_failures_since_concurrency_adjustment = max(
                        0,
                        pressure_failures_since_concurrency_adjustment - 1,
                    )
                    recovery_successes = int(
                        settings.AGENT_MAP_CONCURRENCY_RECOVERY_SUCCESSES
                    )
                    if (
                        effective_concurrency < max_concurrency
                        and successful_since_concurrency_adjustment >= recovery_successes
                    ):
                        adjust_concurrency(
                            target=effective_concurrency + 1,
                            reason="success_recovery",
                        )
                except Exception as exc:
                    retry_state = retry_states_by_index[index]
                    decision = _agent_retry_decision(
                        retry_state, exc=exc, configured_max_attempts=node.max_attempts,
                    )
                    failure_kind = decision.failure_kind
                    failure_counts[failure_kind] = failure_counts.get(failure_kind, 0) + 1
                    retryable = decision.retryable
                    if retryable and _is_model_route_health_failure(failure_kind):
                        if (
                            transient_fallback_route
                            and retry_state.route_health_failure_count >= transient_fallback_threshold
                        ):
                            fallback_route_by_index[index] = transient_fallback_route
                    if retryable and _is_concurrency_pressure_failure(failure_kind):
                        pressure_failures_since_concurrency_adjustment += 1
                        pressure_threshold = int(
                            settings.AGENT_MAP_CONCURRENCY_PRESSURE_FAILURES
                        )
                        if (
                            pressure_failures_since_concurrency_adjustment
                            >= pressure_threshold
                        ):
                            adjust_concurrency(
                                target=effective_concurrency - 1,
                                reason="upstream_pressure",
                                failure_kind=failure_kind,
                            )
                    error_diagnostic = _agent_error_diagnostic(exc)
                    attempt_limit = decision.attempt_limit
                    exc, can_retry, current_retry_feedback, next_repair_context, validation_diagnostics = _agent_failure_recovery(
                        decision=decision,
                        result=result if postprocessing_output else None,
                        exc=exc,
                        item_input=item_input,
                        item_attempt=item_attempt,
                        previous_feedback=retry_feedback_by_index.get(index),
                        previous_repair_context=repair_context_by_index.get(index),
                        validation_diagnostics=_recent_agent_map_diagnostics(item_states_by_index.get(index)),
                    )
                    if current_retry_feedback:
                        retry_feedback_by_index[index] = current_retry_feedback
                    retry_state.retry_exhausted = not can_retry
                    if next_repair_context is not None:
                        repair_context_by_index[index] = next_repair_context
                    diagnostic_recorded = bool(validation_diagnostics or error_diagnostic)
                    item_states_by_index[index] = {
                        "item_index": index,
                        "instance_id": instance_id,
                        "status": "retrying" if can_retry else "failed",
                        "item_attempt": item_attempt,
                        "error_type": type(exc).__name__,
                        "failure_kind": failure_kind,
                        "task_label": _agent_map_item_label(item_input, item_index=index),
                        "request": request_diagnostics,
                        "message": str(exc)[:1000],
                        "model_route": request_diagnostics["model_route"],
                    }
                    retry_history_by_index[index].append(
                        {
                            "item_attempt": item_attempt,
                            "failure_kind": failure_kind,
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:1000],
                            "model_route": request_diagnostics["model_route"],
                        }
                    )
                    del retry_history_by_index[index][:-10]
                    item_states_by_index[index]["retry_history"] = list(
                        retry_history_by_index[index]
                    )
                    item_states_by_index[index].update(retry_state.checkpoint())
                    if index in fallback_route_by_index:
                        item_states_by_index[index]["model_route_override"] = (
                            fallback_route_by_index[index]
                        )
                    if validation_diagnostics:
                        item_states_by_index[index]["validation_diagnostics"] = (
                            validation_diagnostics
                        )
                    if error_diagnostic:
                        item_states_by_index[index]["error_diagnostic"] = error_diagnostic
                    failed_event_payload = {
                        "node_key": node.node_key,
                        "instance_id": instance_id,
                        "item_index": index,
                        "item_number": index + 1,
                        "item_total": total_count,
                        "item_attempt": item_attempt,
                        "retryable": retryable,
                        "error_type": type(exc).__name__,
                        "failure_kind": failure_kind,
                        "message": str(exc)[:1000],
                        "diagnostic_recorded": diagnostic_recorded,
                    }
                    if error_diagnostic:
                        failed_event_payload["error_diagnostic"] = error_diagnostic
                    _event(
                        repo,
                        run,
                        "map_item_failed",
                        failed_event_payload,
                        node_run=node_run,
                    )
                    if can_retry and fatal_error is None:
                        retry_delay = _agent_map_retry_delay(
                            item_attempt=item_attempt,
                            instance_id=instance_id,
                        )
                        ready_at[index] = time.monotonic() + retry_delay
                        _event(
                            repo,
                            run,
                            "map_item_retry_scheduled",
                            {
                                "node_key": node.node_key,
                                "instance_id": instance_id,
                                "item_index": index,
                                "next_attempt": item_attempt + 1,
                                "max_attempts": attempt_limit,
                                "retry_delay_seconds": retry_delay,
                                "queue_priority": "retry",
                                "has_validation_feedback": bool(
                                    retry_feedback_by_index.get(index)
                                ),
                                **_repair_retry_event_fields(
                                    repair_context_by_index.get(index)
                                ),
                            },
                            node_run=node_run,
                        )
                        retry_ready.append(index)
                    elif fatal_error is None:
                        fatal_error = exc
                        ready.clear()
                        retry_ready.clear()
                persist_progress()
                last_heartbeat_at = time.monotonic()

            if fatal_error is not None:
                await cancel_pending_instances(reason="sibling_failed")
                break

    if cancellation_requested or _refresh_run_is_cancelled(repo, run):
        _mark_node_cancelled(
            repo,
            run,
            node_run,
            output_payload=current_output(),
            sdk_state=current_sdk_state(),
        )
        raise _RunCancelled(f"Agent Run {run.id} 已取消")
    if fatal_error is not None:
        node_run.sdk_state = {
            **current_sdk_state(),
            "failed_item": next(
                (
                    {
                        "item_index": index,
                        "item_attempt": state.get("item_attempt", 1),
                        "error_type": state.get("error_type", type(fatal_error).__name__),
                        "failure_kind": state.get(
                            "failure_kind", _agent_failure_kind(fatal_error)
                        ),
                        "task_label": state.get("task_label", ""),
                        **(
                            {"error_diagnostic": state["error_diagnostic"]}
                            if state.get("error_diagnostic")
                            else {}
                        ),
                    }
                    for index, state in sorted(item_states_by_index.items())
                    if state.get("status") == "failed"
                ),
                {"error_type": type(fatal_error).__name__},
            ),
        }
        repo.db.add(node_run)
        repo.commit()
        raise fatal_error
    return current_output(), current_sdk_state()


def _execute_agent_map_parallel(
    **arguments: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return asyncio.run(_execute_agent_map_parallel_async(**arguments))


def _execute_agent_map(
    *,
    repo: AgentPlatformRepository,
    run: AgentRun,
    node: WorkflowNode,
    node_run: AgentNodeRun,
    definition: Any,
    model_metadata: dict[str, str],
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

    if config.max_concurrency > 1:
        output, sdk_state = _execute_agent_map_parallel(
            repo=repo,
            run=run,
            node=node,
            node_run=node_run,
            definition=definition,
            model_metadata=model_metadata,
            tools=tools,
            execution_context=execution_context,
            raw_items=raw_items,
            previous=previous,
        )
        return output, sdk_state

    previous_output = dict(previous.output_payload or {}) if previous is not None else {}
    completed = list(previous_output.get(config.output_key) or [])
    if len(completed) > len(raw_items):
        raise ValueError("agent_map 已持久化结果数量超过本次输入数量")
    for index, record in enumerate(completed):
        if not isinstance(record, dict) or record.get("input_hash") != _payload_hash(raw_items[index]):
            raise ValueError("agent_map 重试输入与已持久化部分结果不一致")

    previous_state = dict(previous.sdk_state or {}) if previous is not None else {}
    aggregate_usage = dict(previous_state.get("usage") or {})
    attempted_requests = int(previous_state.get("attempted_requests") or 0)
    failure_counts = {
        str(key): int(value)
        for key, value in dict(previous_state.get("failure_counts") or {}).items()
    }
    item_states = list(previous_state.get("items") or [])
    failed_item_state = deepcopy(dict(previous_state.get("failed_item") or {}))
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
            "model": model_metadata,
            "usage": aggregate_usage,
            "attempted_requests": attempted_requests,
            "failure_counts": failure_counts,
            "items": item_states,
            **({"failed_item": failed_item_state} if failed_item_state else {}),
        }

    for index in range(len(completed), total_count):
        base_item_input = dict(raw_items[index])
        item_input = dict(base_item_input)
        instance_id = f"{node.node_key}-instance-{index + 1:03d}"
        task_label = _agent_map_item_label(item_input, item_index=index)
        result = None
        normalized_output: dict[str, Any] | None = None
        previous_failed_item = dict(previous_state.get("failed_item") or {})
        if previous_failed_item.get("item_index") != index:
            previous_failed_item = {}
        retry_feedback, repair_context = _restored_agent_retry_context(
            previous_failed_item,
            base_item_input,
        )
        validation_diagnostics = _recent_agent_map_diagnostics(previous_failed_item)
        retry_state = RetryAttemptState.restore(previous_failed_item)
        retry_state.require_retry_budget(configured_max_attempts=node.max_attempts)
        if bool(
            dict(getattr(definition, "runtime_config", {}) or {}).get(
                "disable_server_output_schema"
            )
        ):
            retry_state.server_output_schema_disabled = True
        transient_fallback_route, transient_fallback_threshold = _agent_transient_fallback_config(definition)
        model_route_override = str(previous_failed_item.get("model_route_override") or "") or None
        if transient_fallback_route and retry_state.route_health_failure_count >= transient_fallback_threshold:
            model_route_override = transient_fallback_route
        retry_history = [dict(entry) for entry in previous_failed_item.get("retry_history") or []][-10:]
        while True:
            retry_state.attempt += 1
            item_attempt = retry_state.attempt
            item_input, projection_diagnostics = _project_agent_map_input(
                definition=definition,
                raw_item=base_item_input,
            )
            if repair_context:
                item_input["_platform_repair"] = deepcopy(repair_context)
            result = None
            request_timeout = _agent_map_request_timeout_seconds(
                run,
                definition,
                node_key=node.node_key,
                item_attempt=item_attempt,
            )
            if _refresh_run_is_cancelled(repo, run):
                _mark_node_cancelled(
                    repo,
                    run,
                    node_run,
                    output_payload=current_output(),
                    sdk_state=current_sdk_state(),
                )
                raise _RunCancelled(f"Agent Run {run.id} 已取消")
            _renew_progress_lease(repo, run)
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
                    "request": {
                        **projection_diagnostics,
                        **_payload_size_diagnostics(item_input),
                        "request_timeout_seconds": request_timeout,
                    },
                },
                node_run=node_run,
            )
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
                    quota_scope_key=instance_id,
                )
                attempted_requests += 1
                result = run_agent(
                    db=repo.db,
                    agent_definition=definition,
                    tool_definitions=tools,
                    execution_context=execution_context,
                    input_payload=item_input,
                    request_timeout_seconds=request_timeout,
                    retry_feedback=retry_feedback,
                    disable_server_output_schema=retry_state.server_output_schema_disabled,
                    disable_model_thinking=retry_state.model_thinking_disabled,
                    model_route_override=model_route_override,
                    skip_output_postprocessor=True,
                )
                aggregate_usage = _sum_usage(aggregate_usage, result.usage)
                _record_agent_usage(
                    repo=repo, run=run, node_run=node_run, current=result.usage,
                    reservation=reservation, quota_scope_key=instance_id,
                )
                guarded_output = _restore_protected_repair_slots(
                    item_output=dict(result.output),
                    repair_context=repair_context,
                )
                normalized_output = _postprocess_agent_map_output(
                    config=config,
                    definition=definition,
                    execution_context=execution_context,
                    # 后处理必须使用完整原始输入，尤其是 source_anchor 和事实坐标。
                    item_input=deepcopy(base_item_input),
                    item_output=guarded_output,
                )
                break
            except Exception as exc:
                decision = _agent_retry_decision(
                    retry_state, exc=exc, configured_max_attempts=node.max_attempts,
                )
                failure_kind = decision.failure_kind
                failure_counts[failure_kind] = failure_counts.get(failure_kind, 0) + 1
                error_diagnostic = _agent_error_diagnostic(exc)
                exc, can_retry, retry_feedback, repair_context, validation_diagnostics = _agent_failure_recovery(
                    decision=decision,
                    result=result,
                    exc=exc,
                    item_input=base_item_input,
                    item_attempt=item_attempt,
                    previous_feedback=retry_feedback,
                    previous_repair_context=repair_context,
                    validation_diagnostics=validation_diagnostics,
                )
                retry_state.retry_exhausted = not can_retry
                if transient_fallback_route and retry_state.route_health_failure_count >= transient_fallback_threshold:
                    model_route_override = transient_fallback_route
                retry_history = [*retry_history, {
                    "item_attempt": item_attempt,
                    "failure_kind": failure_kind,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }][-10:]
                failed_event_payload = {
                    "node_key": node.node_key,
                    "item_index": index,
                    "item_number": index + 1,
                    "item_total": total_count,
                    "item_attempt": item_attempt,
                    "retryable": decision.retryable,
                    "error_type": type(exc).__name__,
                    "failure_kind": failure_kind,
                    "message": str(exc)[:1000],
                }
                if error_diagnostic:
                    failed_event_payload["error_diagnostic"] = error_diagnostic
                _event(
                    repo,
                    run,
                    "map_item_failed",
                    failed_event_payload,
                    node_run=node_run,
                )
                node_run.output_payload = {
                    config.output_key: completed,
                    "completed_count": len(completed),
                    "total_count": total_count,
                }
                node_run.sdk_state = {
                    "last_agent_name": definition.name,
                    "model": model_metadata,
                    "usage": aggregate_usage,
                    "attempted_requests": attempted_requests,
                    "failure_counts": failure_counts,
                    "items": item_states,
                    "failed_item": {
                        "item_index": index,
                        "item_attempt": item_attempt,
                        "error_type": type(exc).__name__,
                        "failure_kind": failure_kind,
                        "task_label": task_label,
                        **retry_state.checkpoint(),
                        "model_route_override": model_route_override,
                        "retry_history": retry_history,
                        "input_hash": _payload_hash(base_item_input),
                        "retry_feedback": retry_feedback,
                        "repair_context": deepcopy(repair_context),
                        "validation_diagnostics": validation_diagnostics,
                        **(
                            {"error_diagnostic": error_diagnostic}
                            if error_diagnostic
                            else {}
                        ),
                    },
                }
                repo.db.add(node_run)
                failed_item_state = deepcopy(node_run.sdk_state["failed_item"])
                repo.db.commit()
                if not can_retry:
                    raise exc
                _event(
                    repo,
                    run,
                    "map_item_retry_scheduled",
                    {
                        "node_key": node.node_key,
                        "item_index": index,
                        "next_attempt": item_attempt + 1,
                        "max_attempts": decision.attempt_limit,
                        "has_validation_feedback": bool(retry_feedback),
                        **_repair_retry_event_fields(repair_context),
                    },
                    node_run=node_run,
                )
                repo.db.commit()
                time.sleep(min(2 ** (item_attempt - 1), 4))
        if result is None or normalized_output is None:
            raise RuntimeError(f"agent_map 映射项未产生结果: node={node.node_key}, index={index}")

        failed_item_state = {}
        item_states.append(
            {
                "item_index": index,
                "instance_id": instance_id,
                "status": "success",
                "item_attempt": item_attempt,
                "last_agent_name": result.last_agent_name,
                "usage": result.usage,
                "reservation": reservation,
                "tool_calls": result.tool_calls,
                "task_label": task_label,
                "validation_diagnostics": validation_diagnostics,
                **retry_state.checkpoint(),
                "model_route_override": model_route_override,
                "retry_history": retry_history,
            }
        )
        completed.append(
            {
                "item_index": index,
                "input_hash": _payload_hash(base_item_input),
                "output": normalized_output,
            }
        )
        try:
            _renew_progress_lease(repo, run)
        except _RunCancelled:
            _mark_node_cancelled(repo, run, node_run, output_payload=current_output(), sdk_state=current_sdk_state())
            raise
        node_run.output_payload = {
            config.output_key: completed,
            "completed_count": len(completed),
            "total_count": total_count,
        }
        node_run.sdk_state = {
            "last_agent_name": result.last_agent_name,
            "model": model_metadata,
            "usage": aggregate_usage,
            "attempted_requests": attempted_requests,
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

    output = current_output()
    sdk_state = current_sdk_state()
    return output, sdk_state


def _persisted_dependency_outputs(
    repo: AgentPlatformRepository,
    *,
    run_id: int,
) -> dict[str, dict[str, Any]]:
    """从节点检查点恢复依赖输出，避免在 Run 上重复持久化大 JSON。"""

    latest_by_node_key: dict[str, AgentNodeRun] = {}
    for node_run in repo.list_node_runs(run_id=run_id):
        latest_by_node_key[str(node_run.node_key)] = node_run
    return {
        node_key: deepcopy(node_run.output_payload or {})
        for node_key, node_run in latest_by_node_key.items()
        if node_run.status == "success"
    }


def _execute_node(
    repo: AgentPlatformRepository,
    run: AgentRun,
    node: WorkflowNode,
    dependency_outputs: dict[str, dict[str, Any]],
) -> tuple[AgentNodeRun, dict[str, Any]] | None:
    _renew_progress_lease(repo, run)
    _ensure_run_deadline(run, node_key=node.node_key)
    run_context = deepcopy(run.run_context or {})
    global_deadline = _deadline_value(run_context.get("deadline_at"))
    if global_deadline is None:
        # 兼容由内部测试或旧任务直接进入节点执行的路径，仍从真实执行时刻开始计时。
        global_deadline = _now() + timedelta(
            seconds=int(settings.AGENT_RUN_DEADLINE_SECONDS)
        )
        run_context["deadline_at"] = global_deadline.isoformat()
    stage_deadline = global_deadline
    if node.time_budget_seconds is not None:
        stage_deadline = min(
            global_deadline,
            _now() + timedelta(seconds=node.time_budget_seconds),
        )
    run_context["stage_deadline_at"] = stage_deadline.isoformat()
    run_context["remaining_seconds"] = max(
        0,
        int((global_deadline - _now()).total_seconds()),
    )
    run.run_context = run_context
    previous = repo.latest_node_run(run_id=run.id, node_key=node.node_key)
    if previous is not None and previous.status == "waiting_approval":
        node_run = previous
        attempt = previous.attempt
        node_input = dict(previous.input_payload or {})
        node_run.status = "running"
        node_run.error_message = ""
    else:
        attempt = repo.next_node_attempt(run_id=run.id, node_key=node.node_key)
        previous_sdk_state = dict(previous.sdk_state or {}) if previous is not None else {}
        if node.node_type in {"agent", "agent_network"}:
            RetryAttemptState.restore(previous_sdk_state).require_retry_budget(
                configured_max_attempts=node.max_attempts,
            )
        elif attempt > node.max_attempts:
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
            node_run.sdk_state.pop("checkpoint_restore", None)
        repo.db.add(node_run)
        repo.db.flush()
    run.current_node_key = node.node_key
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
    if node.node_type in {"agent", "agent_network", "agent_map"}:
        definition = repo.get_agent(
            project_id=run.project_id,
            agent_key=node.reference_key,
        )
        if definition is None:
            raise LookupError(f"找不到智能体定义: {node.reference_key}")
        node_run.agent_definition_id = definition.id
        reusable = _reusable_agent_node_output(
            repo=repo,
            run=run,
            node=node,
            definition=definition,
            node_input=node_input,
        )
        if reusable is not None:
            _renew_progress_lease(repo, run)
            source_node_run, output, cache_version, input_hash = reusable
            node_run.sdk_state = {
                "result_cache": {
                    "hit": True,
                    "version": cache_version,
                    "input_hash": input_hash,
                    "source_run_id": int(source_node_run.run_id),
                    "source_node_run_id": int(source_node_run.id),
                    "source_duration_seconds": _node_duration_seconds(source_node_run),
                }
            }
            node_run.output_payload = output
            node_run.status = "success"
            node_run.finished_at = _now()
            _event(
                repo,
                run,
                "node_cache_hit",
                {
                    "node_key": node.node_key,
                    "cache_version": cache_version,
                    "source_run_id": int(source_node_run.run_id),
                    "source_node_run_id": int(source_node_run.id),
                },
                node_run=node_run,
            )
            _event(
                repo,
                run,
                "node_completed",
                {
                    "node_key": node.node_key,
                    "attempt": attempt,
                    "cache_hit": True,
                },
                node_run=node_run,
            )
            repo.db.add(node_run)
            repo.db.add(run)
            repo.commit()
            return node_run, output
        model_metadata = resolve_agent_model_metadata(
            db=repo.db,
            user_id=run.user_id,
            agent_definition=definition,
        )
        node_run.sdk_state = {
            **dict(node_run.sdk_state or {}),
            "model": model_metadata,
        }
        repo.db.add(node_run)
        repo.commit()
        tools = repo.list_agent_tools(definition.id, project_id=run.project_id)
        if node.node_type == "agent_map":
            output, sdk_state = _execute_agent_map(
                repo=repo,
                run=run,
                node=node,
                node_run=node_run,
                definition=definition,
                model_metadata=model_metadata,
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
            model_input, projection_diagnostics = _project_agent_map_input(
                definition=definition,
                raw_item=node_input,
            )
            retry_state = RetryAttemptState.restore(
                dict(previous.sdk_state or {}) if previous is not None else {},
            )
            if bool(
                dict(getattr(definition, "runtime_config", {}) or {}).get(
                    "disable_server_output_schema"
                )
            ):
                retry_state.server_output_schema_disabled = True
            retry_feedback, repair_context = _restored_agent_retry_context(
                dict(previous.sdk_state or {}) if previous is not None else {}, node_input,
            )
            if repair_context is not None:
                model_input["_platform_repair"] = deepcopy(repair_context)
            node_run.sdk_state = {
                **dict(node_run.sdk_state or {}),
                "retry_feedback": retry_feedback,
                "repair_context": deepcopy(repair_context),
                "input_hash": _payload_hash(node_input),
                "validation_diagnostics": _recent_agent_map_diagnostics(
                    dict(previous.sdk_state or {}) if previous is not None else {},
                ),
                **retry_state.checkpoint(),
            }
            repo.db.add(node_run)
            repo.commit()
            reservation = _reserve_agent_request(
                repo=repo,
                run=run,
                node_run=node_run,
                definition=definition,
                tools=tools,
                input_payload=model_input,
                quota_scope_key=f"{node.node_key}-instance-001",
            )
            result = _run_standard_agent(
                db=repo.db,
                agent_definition=definition,
                tool_definitions=tools,
                execution_context=execution_context,
                input_payload=model_input,
                request_timeout_seconds=_request_timeout_seconds(
                    run,
                    definition,
                    node_key=node.node_key,
                ),
                retry_feedback=retry_feedback,
                disable_server_output_schema=retry_state.server_output_schema_disabled,
                disable_model_thinking=retry_state.model_thinking_disabled,
                # 投影后的输入只供模型使用；后处理统一在本层用完整原始输入执行一次。
                skip_output_postprocessor=True,
            )
            _record_agent_usage(
                repo=repo,
                run=run,
                node_run=node_run,
                current=result.usage,
                reservation=reservation,
                quota_scope_key=f"{node.node_key}-instance-001",
            )
            output = _postprocess_agent_output(
                agent_definition=definition,
                execution_context=execution_context,
                input_payload=node_input,
                output=_restore_protected_repair_slots(
                    item_output=dict(result.output), repair_context=repair_context,
                ),
            )
            node_run.sdk_state = {
                "last_agent_name": result.last_agent_name,
                "model": model_metadata,
                "usage": result.usage,
                "tool_calls": result.tool_calls,
                **retry_state.checkpoint(),
                "input_projection": projection_diagnostics,
                "validation_diagnostics": _recent_agent_map_diagnostics(node_run.sdk_state),
            }
        cache_config = _agent_result_cache_config(definition)
        if cache_config is not None:
            node_run.sdk_state = {
                **dict(node_run.sdk_state or {}),
                "result_cache": {
                    "hit": False,
                    "version": str(cache_config["version"]),
                    "input_hash": _agent_result_cache_input_hash(
                        definition,
                        node_input,
                    ),
                },
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

    _renew_progress_lease(repo, run)
    # Agent 调用期间会实时更新 usage；完成节点时从最新上下文合并，避免旧快照覆盖额度账本。
    latest_run_context = deepcopy(run.run_context or {})
    latest_run_context["artifacts"] = execution_context.artifacts
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
    claimed_token: str | None = None
    try:
        run = _claim_run(repo, run_id, task_id)
        if run is None:
            return {"status": "not_claimed", "run_id": run_id}
        claimed_token = run.claim_token
        expected_signature = str(
            (run.run_context or {}).get("runtime_registry_signature") or ""
        )
        actual_signature = runtime_registry_signature()
        if not expected_signature or expected_signature != actual_signature:
            raise RuntimeError(
                "Agent Worker 运行时代码与创建 Run 的服务版本不一致，请重启 Worker 后重试"
            )
        # 重试可能跳过已成功的来源节点，排队期间的文档变化也必须在恢复前拒绝。
        source = persisted_source_snapshot(run)
        current_source = repo.resolve_source_snapshot(project_id=run.project_id, input_payload=run.input_payload)
        if current_source is not None:
            if source is None:
                raise ValueError("运行缺少需求来源快照，请重新生成")
            assert_same_source(source, current_source)
        from core.db.model_defs import AgentWorkflowDefinition

        workflow = repo.db.get(AgentWorkflowDefinition, run.workflow_definition_id)
        if workflow is None or not workflow.enabled:
            raise LookupError("运行引用的工作流不存在或已停用")
        execution = parse_execution_definition(workflow.definition)
        validate(instance=dict(run.input_payload or {}), schema=execution.input_schema)
        dependency_outputs = _persisted_dependency_outputs(repo, run_id=run.id)

        if isinstance(execution, AgentProgramDefinition):
            output_node_key = "agent_network"
            node = WorkflowNode(
                node_key=output_node_key,
                node_type="agent_network",
                reference_key=execution.entry_agent_key,
                max_attempts=execution.max_attempts,
                time_budget_seconds=execution.time_budget_seconds,
            )
            previous = repo.latest_node_run(run_id=run.id, node_key=node.node_key)
            if previous is not None and previous.status == "success":
                dependency_outputs[node.node_key] = dict(previous.output_payload or {})
            else:
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
            required_artifact_key = str(execution.required_artifact_key or "").strip()
            artifacts = dict((run.run_context or {}).get("artifacts") or {})
            if required_artifact_key and required_artifact_key not in artifacts:
                raise RuntimeError(
                    f"Agent Program 未持久化必需产物: {required_artifact_key}"
                )
        else:
            graph = execution
            output_node_key = graph.output_node_key
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

        run = repo.get_run_for_update(run_id=run.id)
        if run is None:
            return {"status": "not_found", "run_id": run_id}
        if run.status != "running":
            return {"status": run.status, "run_id": run.id}
        if run.claim_token != claimed_token:
            return {"status": "not_claimed", "run_id": run.id}
        final_output = dependency_outputs[output_node_key]
        run.output_payload = {
            "result": final_output,
            "artifacts": dict((run.run_context or {}).get("artifacts") or {}),
        }
        transition_run(
            repo, run, "success", event_type="run_completed",
            payload={"output_node_key": output_node_key}, now=_now(),
        )
        repo.commit()
        prune_terminal_run_history(repo, run)
        return {"status": "success", "run_id": run.id}
    except _RunCancelled:
        repo.db.rollback()
        return {"status": "cancelled", "run_id": run_id}
    except Exception as exc:
        repo.db.rollback()
        run = repo.get_run_for_update(run_id=run_id)
        if run is not None:
            if run.status == "cancelled":
                latest = (
                    repo.latest_node_run(run_id=run.id, node_key=run.current_node_key)
                    if run.current_node_key
                    else None
                )
                if latest is not None and latest.status == "running":
                    _mark_node_cancelled(repo, run, latest)
                return {"status": "cancelled", "run_id": run.id}
            if run.status != "running" or run.claim_token != claimed_token:
                raise
            latest = (
                repo.latest_node_run(run_id=run.id, node_key=run.current_node_key)
                if run.current_node_key
                else None
            )
            if latest is not None and latest.status == "running":
                latest.status = "failed"
                latest.error_message = _persistent_error_message(exc)
                latest.finished_at = _now()
                repo.db.add(latest)
            transition_run(
                repo, run, "failed", event_type="run_failed",
                error_message=_persistent_error_message(exc), now=_now(),
                payload={
                    "error_type": type(exc).__name__,
                    "failure_kind": _agent_failure_kind(exc),
                    "message": str(exc)[:1000],
                },
                node_run_id=latest.id if latest is not None else None,
            )
            repo.db.add(run)
            repo.commit()
            prune_terminal_run_history(repo, run)
        raise
    finally:
        if owns_session:
            active_db.close()
