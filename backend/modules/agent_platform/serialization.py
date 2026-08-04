from __future__ import annotations

from typing import Any


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


def serialize_node_run(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "node_key": value.node_key,
        "node_type": value.node_type,
        "status": value.status,
        "attempt": value.attempt,
        "input_payload": value.input_payload or {},
        "output_payload": value.output_payload or {},
        "sdk_state": value.sdk_state or {},
        "error_message": value.error_message or "",
        "started_at": value.started_at,
        "finished_at": value.finished_at,
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


def serialize_run(
    value: Any,
    *,
    node_runs: list[Any] | None = None,
    approvals: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": value.id,
        "project_id": value.project_id,
        "workflow_definition_id": value.workflow_definition_id,
        "status": value.status,
        "current_node_key": value.current_node_key,
        "input_payload": value.input_payload or {},
        "run_context": value.run_context or {},
        "output_payload": value.output_payload or {},
        "error_message": value.error_message or "",
        "parent_run_id": value.parent_run_id,
        "task_id": value.task_id,
        "created_at": value.created_at,
        "started_at": value.started_at,
        "finished_at": value.finished_at,
        "nodes": [serialize_node_run(item) for item in (node_runs or [])],
        "approvals": [serialize_approval(item) for item in (approvals or [])],
    }
