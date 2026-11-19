from __future__ import annotations

from datetime import datetime, timezone
import json
from collections import Counter
from typing import Any, Callable
from .input_snapshots import restore_node_input
from .results import persisted_run_output, persisted_test_generation_result


class DuplicateJSONKeyError(ValueError):
    """重复对象字段会丢失原候选语义，不能按一般 JSON 语法损坏修补。"""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"JSON 对象包含重复字段: {json.dumps(key, ensure_ascii=False)}")


def strict_json_loads(value: str) -> Any:
    """在字典形成之前拒绝任意层重复字段，避免默认的后值覆盖前值。"""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, item in pairs:
            if key in result:
                raise DuplicateJSONKeyError(key)
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=unique_object)


def _serialize_utc_datetime(value: Any) -> Any:
    """执行时间在数据库中以无时区 UTC 保存，对外补齐明确时区。"""

    if value is None or not isinstance(value, datetime):
        return value
    aware_value = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return aware_value.isoformat().replace("+00:00", "Z")


def serialize_context_record(value: Any) -> dict[str, Any]:
    """序列化长期上下文记录，正文只由当前授权用户读取。"""

    return {
        "id": int(value.id),
        "user_id": int(value.user_id) if value.user_id is not None else None,
        "project_id": int(value.project_id) if value.project_id is not None else None,
        "source_kind": str(value.source_kind),
        "scope": str(value.scope),
        "scope_id": str(value.scope_id),
        "revision": str(value.revision),
        "content_hash": str(value.content_hash),
        "content": dict(value.content or {}),
        "evidence_refs": list(value.evidence_refs or []),
        "status": str(value.status),
        "confirmed_by_user_id": (
            int(value.confirmed_by_user_id)
            if value.confirmed_by_user_id is not None else None
        ),
        "confirmed_at": _serialize_utc_datetime(value.confirmed_at),
        "valid_from": _serialize_utc_datetime(value.valid_from),
        "valid_to": _serialize_utc_datetime(value.valid_to),
        "revoked_at": _serialize_utc_datetime(value.revoked_at),
        "revocation_reason": str(value.revocation_reason or ""),
        "replaced_by_id": int(value.replaced_by_id) if value.replaced_by_id is not None else None,
        "origin_run_id": int(value.origin_run_id) if value.origin_run_id is not None else None,
        "created_at": _serialize_utc_datetime(value.created_at),
        "updated_at": _serialize_utc_datetime(value.updated_at),
    }


def serialize_agent(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "project_id": value.project_id,
        "agent_key": value.agent_key,
        "name": value.name,
        "description": value.description,
        "instructions": value.instructions,
        "model": value.model,
        "output_schema": value.output_schema or {},
        "runtime_config": value.runtime_config or {},
        "version": value.version,
        "enabled": bool(value.enabled),
        "builtin": bool(value.builtin),
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def serialize_tool(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "project_id": value.project_id,
        "tool_key": value.tool_key,
        "name": value.name,
        "description": value.description,
        "input_schema": value.input_schema or {},
        "output_schema": value.output_schema or {},
        "risk_level": value.risk_level,
        "requires_approval": bool(value.requires_approval),
        "enabled": bool(value.enabled),
        "builtin": bool(value.builtin),
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def serialize_workflow(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "project_id": value.project_id,
        "workflow_key": value.workflow_key,
        "name": value.name,
        "description": value.description,
        "definition": value.definition or {},
        "version": value.version,
        "enabled": bool(value.enabled),
        "builtin": bool(value.builtin),
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def serialize_node_run(
    value: Any, *, load_node: Callable[[int], Any | None] | None = None,
) -> dict[str, Any]:
    from .checkpoint_storage import INPUT_MARKER

    packed_input = value.input_payload or {}
    input_is_reference = packed_input.get(INPUT_MARKER) == 1
    sdk_state = dict(value.sdk_state or {})
    checkpoint = sdk_state.get("action_checkpoint")
    if isinstance(checkpoint, dict):
        # 展示只消费小投影与预算，不因旧内嵌记录或新引用而自动展开状态/回执/专业结果。
        header_keys = ("version", "storage", "input_hash", "input_ref", "state_hash", "state_ref",
                       "journal_ref", "budget", "execution_semantic_hash", "quota_base_usage")
        header = {key: checkpoint[key] for key in header_keys if key in checkpoint}
        if checkpoint.get("version") == 3 and checkpoint.get("storage") == "artifact-journal-v1":
            from .checkpoint_storage import checkpoint_projection

            header["projection"] = checkpoint_projection(checkpoint)
        else:
            header["projection"] = {"unsupported_checkpoint": True}
        sdk_state["action_checkpoint"] = header
    return {
        "id": value.id,
        "node_key": value.node_key,
        "node_type": value.node_type,
        "agent_definition_id": value.agent_definition_id,
        "status": value.status,
        "attempt": value.attempt,
        # 详情轮询只展示已持久化的引用头；完整输入由运行时仓库的显式读取边界还原。
        "input_payload": dict(packed_input) if input_is_reference else restore_node_input(value, load_node),
        "input_payload_is_reference": input_is_reference,
        "output_payload": value.output_payload or {},
        "sdk_state": sdk_state,
        "error_message": value.error_message or "",
        "started_at": _serialize_utc_datetime(value.started_at),
        "finished_at": _serialize_utc_datetime(value.finished_at),
        "created_at": value.created_at,
    }


def serialize_event(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "run_id": value.run_id,
        "node_run_id": value.node_run_id,
        "sequence": value.sequence,
        "event_type": value.event_type,
        "payload": value.payload or {},
        "created_at": value.created_at,
    }


def serialize_approval(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "run_id": value.run_id,
        "node_run_id": value.node_run_id,
        "status": value.status,
        "request_payload": value.request_payload or {},
        "decision_payload": value.decision_payload or {},
        "requested_at": value.requested_at,
        "decided_at": value.decided_at,
    }


def _serialize_run_header(value: Any, *, run_context: dict[str, Any]) -> dict[str, Any]:
    """共享运行响应头；上下文由调用方提供，避免摘要触发大字段延迟加载。"""
    run_attempt = int(run_context.get("run_attempt") or 1)
    display_attempt = max(1, run_attempt - int(run_context.get("attempt_display_offset") or 0))
    execution_contract = run_context.get("execution_contract")
    contract_hash = (
        str(execution_contract.get("hash"))
        if isinstance(execution_contract, dict) and execution_contract.get("hash")
        else None
    )
    return {
        "id": value.id,
        "run_attempt": display_attempt,
        "execution_attempt": max(1, run_attempt),
        "input_hash": run_context.get("input_hash"),
        "logical_task_id": run_context.get("logical_task_id"),
        "attempt_id": run_context.get("attempt_id"),
        "checkpoint_schema_version": run_context.get("checkpoint_schema_version"),
        "execution_contract_hash": contract_hash,
        "project_id": value.project_id,
        "workflow_definition_id": value.workflow_definition_id,
        "status": value.status,
        "current_node_key": value.current_node_key,
        "input_payload": value.input_payload or {},
        "error_message": value.error_message or "",
        "parent_run_id": value.parent_run_id,
        "task_id": value.task_id,
        "created_at": value.created_at,
        "started_at": _serialize_utc_datetime(value.started_at),
        "finished_at": _serialize_utc_datetime(value.finished_at),
    }


def serialize_run(
    value: Any,
    *,
    node_runs: list[Any] | None = None,
    approvals: list[Any] | None = None,
) -> dict[str, Any]:
    run_context = value.run_context or {}
    nodes_by_id = {item.id: item for item in (node_runs or [])}
    return {
        **_serialize_run_header(value, run_context=dict(run_context)),
        "run_context": run_context,
        "output_payload": persisted_run_output(value),
        "test_generation_result": persisted_test_generation_result(value),
        "nodes": [serialize_node_run(item, load_node=nodes_by_id.get) for item in (node_runs or [])],
        "approvals": [serialize_approval(item) for item in (approvals or [])],
    }


def serialize_run_summary(value: Any) -> dict[str, Any]:
    """工作台初始化只需要活动状态，不传输节点和运行产物大字段。"""

    # 运行对象可能已经在同一 Session 中加载过完整 run_context。这里只读取
    # 已投影的身份字段，不能把整个上下文复制到摘要，否则“摘要”会重新变成大响应。
    projected_context = getattr(value, "__dict__", {}).get("_summary_context")
    loaded_context = getattr(value, "__dict__", {}).get("run_context")
    run_context = (
        projected_context
        if isinstance(projected_context, dict)
        else loaded_context if isinstance(loaded_context, dict) else {}
    )
    summary_context = {
        key: run_context.get(key)
        for key in (
            "run_attempt",
            "attempt_display_offset",
            "input_hash",
            "logical_task_id",
            "attempt_id",
            "checkpoint_schema_version",
            "execution_contract",
        )
        if key in run_context
    }
    return {
        **_serialize_run_header(value, run_context=summary_context),
        "run_context": {},
        "output_payload": {},
        "test_generation_result": None,
        "nodes": [],
        "approvals": [],
    }


def summarize_run_evaluation(
    value: Any,
    *,
    node_runs: list[Any] | None = None,
    events: list[Any] | None = None,
    approvals: list[Any] | None = None,
    event_total: int | None = None,
    context_feedback: list[Any] | None = None,
) -> dict[str, Any]:
    """从已持久化运行轨迹生成可审计的执行指标。

    执行成功只代表 Run 完成；业务质量必须来自独立评估产物，缺少产物时明确
    标记为 ``unverified``，不能把模型调用成功率冒充业务正确率。
    """

    nodes = list(node_runs or [])
    event_rows = list(events or [])
    approval_rows = list(approvals or [])
    status = str(getattr(value, "status", "") or "")
    node_statuses = Counter(str(item.status or "") for item in nodes)
    retry_count = sum(max(0, int(item.attempt or 1) - 1) for item in nodes)
    event_counts = Counter(str(item.event_type or "") for item in event_rows)
    total_events = len(event_rows) if event_total is None else max(0, int(event_total))
    approval_counts = Counter(str(item.status or "") for item in approval_rows)
    approval_kinds = Counter(
        str((getattr(item, "request_payload", {}) or {}).get("kind") or "unspecified")
        for item in approval_rows
    )

    started_at = getattr(value, "started_at", None)
    finished_at = getattr(value, "finished_at", None)
    duration_seconds = None
    if isinstance(started_at, datetime) and isinstance(finished_at, datetime):
        duration_seconds = max(0, int((finished_at - started_at).total_seconds()))

    artifacts = dict((getattr(value, "run_context", {}) or {}).get("artifacts") or {})
    from .checkpoint_storage import run_context_retrieval_metrics

    retrieval_metrics = run_context_retrieval_metrics(value, nodes)
    feedback_rows = list(context_feedback or [])
    feedback_statuses = Counter(str(getattr(item, "status", "") or "unknown") for item in feedback_rows)
    feedback_outcomes = Counter(
        str((getattr(item, "content", {}) or {}).get("outcome"))
        for item in feedback_rows
        if (getattr(item, "content", {}) or {}).get("outcome")
    )
    feedback_categories = Counter(
        str((getattr(item, "content", {}) or {}).get("category"))
        for item in feedback_rows
        if (getattr(item, "content", {}) or {}).get("category")
    )
    business_metrics: dict[str, float] = {}
    artifact_metrics: dict[str, dict[str, float]] = {}
    artifact_metric_scales: dict[str, str] = {}
    evaluation_keys: list[str] = []
    for artifact_key in ("test_evaluation", "automation_evaluation"):
        artifact = artifacts.get(artifact_key)
        if not isinstance(artifact, dict):
            continue
        evaluation_keys.append(artifact_key)
        evaluation = artifact.get("evaluation") or artifact.get("result")
        metrics = evaluation.get("metrics") if isinstance(evaluation, dict) else None
        if metrics is None and isinstance(evaluation, dict) and "overall_score" in evaluation:
            metrics = {"overall_score": evaluation.get("overall_score")}
        artifact_values: dict[str, float] = {}
        if isinstance(metrics, dict):
            for key, raw in metrics.items():
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    normalized_key = str(key)
                    value = float(raw)
                    business_metrics[f"{artifact_key}.{normalized_key}"] = value
                    artifact_values[normalized_key] = value
        artifact_metrics[artifact_key] = artifact_values
        artifact_metric_scales[artifact_key] = "0-10" if artifact_key == "automation_evaluation" else "0-1"

    if not approval_rows:
        intervention_status = "none"
    elif approval_counts.get("pending", 0):
        intervention_status = "pending"
    else:
        intervention_status = "resolved"

    return {
        "run_status": status,
        "execution_success": status == "success",
        "execution_success_rate": 1.0 if status == "success" else 0.0,
        "success_rate_basis": "run_status_only",
        "execution": {
            "node_count": len(nodes),
            "successful_node_count": node_statuses.get("success", 0),
            "failed_node_count": node_statuses.get("failed", 0),
            "waiting_approval_node_count": node_statuses.get("waiting_approval", 0),
            "retry_count": retry_count,
            "duration_seconds": duration_seconds,
        },
        "trajectory": {
            "event_count": total_events,
            "sampled_event_count": len(event_rows),
            "event_counts_complete": total_events == len(event_rows),
            "last_sequence": max(
                (int(item.sequence or 0) for item in event_rows),
                default=0,
            ),
            "event_type_counts": dict(sorted(event_counts.items())),
        },
        "human_intervention": {
            "status": intervention_status,
            "requested_count": len(approval_rows),
            "pending_count": approval_counts.get("pending", 0),
            "approved_count": approval_counts.get("approved", 0),
            "rejected_count": approval_counts.get("rejected", 0),
            "kind_counts": dict(sorted(approval_kinds.items())),
        },
        "business_evaluation": {
            "status": "recorded" if evaluation_keys else "unverified",
            "artifact_keys": evaluation_keys,
            "metrics": business_metrics,
            "artifact_metrics": artifact_metrics,
            "metric_scales": artifact_metric_scales,
        },
        "context_evaluation": {
            **retrieval_metrics,
            "feedback_status": "recorded" if feedback_rows else "unverified",
            "feedback_count": len(feedback_rows),
            "feedback_status_counts": dict(sorted(feedback_statuses.items())),
            "feedback_outcome_counts": dict(sorted(feedback_outcomes.items())),
            "feedback_category_counts": dict(sorted(feedback_categories.items())),
        },
    }
