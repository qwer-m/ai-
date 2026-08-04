from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from jsonschema import validate
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentApproval,
    AgentNodeRun,
    AgentRun,
)
from .contracts import WorkflowGraph, WorkflowNode
from .registry import ToolExecutionContext, tool_registry
from .repository import AgentPlatformRepository
from .sdk_adapter import run_agent


RUN_LEASE_SECONDS = 3900


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


def _execute_node(
    repo: AgentPlatformRepository,
    run: AgentRun,
    node: WorkflowNode,
    dependency_outputs: dict[str, dict[str, Any]],
) -> tuple[AgentNodeRun, dict[str, Any]] | None:
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
    if node.node_type == "agent":
        definition = repo.get_agent(
            project_id=run.project_id,
            agent_key=node.reference_key,
        )
        if definition is None:
            raise LookupError(f"找不到智能体定义: {node.reference_key}")
        node_run.agent_definition_id = definition.id
        tools = repo.list_agent_tools(definition.id)
        result = run_agent(
            db=repo.db,
            agent_definition=definition,
            tool_definitions=tools,
            execution_context=execution_context,
            input_payload=node_input,
        )
        output = dict(result.output)
        node_run.sdk_state = {
            "last_agent_name": result.last_agent_name,
            "usage": result.usage,
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

    run_context["artifacts"] = execution_context.artifacts
    run_context.setdefault("node_outputs", {})[node.node_key] = output
    run.run_context = run_context
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
            executed = _execute_node(repo, run, node, dependency_outputs)
            if executed is None:
                return {"status": "waiting_approval", "run_id": run.id}
            _, output = executed
            dependency_outputs[node.node_key] = output

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
    except Exception as exc:
        repo.db.rollback()
        run = repo.get_run(run_id=run_id)
        if run is not None:
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
