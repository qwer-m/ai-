from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from jsonschema import ValidationError, validate

from core.db.model_defs import (
    AgentApproval,
    AgentDefinition,
    AgentNodeRun,
    AgentRun,
    AgentToolBinding,
    AgentWorkflowDefinition,
)
from core.settings.config import settings
from .contracts import (
    AgentDefinitionCreate,
    AgentRunCreate,
    ApprovalDecision,
    WorkflowDefinitionCreate,
    WorkflowGraph,
)
from .repository import AgentPlatformRepository
from .seed import seed_builtin_definitions
from .registry import runtime_registry_signature


def _resolved_execution_limits(request: AgentRunCreate) -> dict[str, int]:
    """调用方只能降低额度，平台环境变量始终是不可绕过的上限。"""

    requested = request.execution_limits
    platform_limits = {
        "max_requests": int(settings.AGENT_RUN_MAX_REQUESTS),
        "max_input_tokens": int(settings.AGENT_RUN_MAX_INPUT_TOKENS),
        "max_output_tokens": int(settings.AGENT_RUN_MAX_OUTPUT_TOKENS),
        "max_total_tokens": int(settings.AGENT_RUN_MAX_TOTAL_TOKENS),
    }
    if requested is None:
        return platform_limits
    values = requested.model_dump()
    return {
        key: min(platform_value, int(values[key]))
        if values.get(key) is not None
        else platform_value
        for key, platform_value in platform_limits.items()
    }


class AgentPlatformService:
    def __init__(
        self,
        db: Any,
        worker_starter: Callable[[int], Any] | None = None,
    ) -> None:
        self.db = db
        self.repo = AgentPlatformRepository(db)
        self._start_worker = worker_starter

    def _owned_project(self, *, project_id: int, user_id: int) -> bool:
        return self.repo.get_owned_project(project_id=project_id, user_id=user_id) is not None

    def ensure_builtins(self, *, project_id: int, user_id: int) -> bool:
        if not self._owned_project(project_id=project_id, user_id=user_id):
            return False
        seed_builtin_definitions(db=self.db, project_id=project_id, user_id=user_id)
        return True

    def list_catalog(self, *, project_id: int, user_id: int) -> dict[str, list[Any]] | None:
        if not self.ensure_builtins(project_id=project_id, user_id=user_id):
            return None
        return {
            "agents": self.repo.list_agents(project_id=project_id),
            "tools": self.repo.list_tools(project_id=project_id),
            "workflows": self.repo.list_workflows(project_id=project_id),
        }

    def create_agent(
        self,
        *,
        request: AgentDefinitionCreate,
        user_id: int,
    ) -> tuple[AgentDefinition | None, str]:
        if not self._owned_project(project_id=request.project_id, user_id=user_id):
            return None, "project_not_found"
        existing = self.repo.get_agent(
            project_id=request.project_id,
            agent_key=request.agent_key,
            enabled_only=False,
        )
        if existing is not None and existing.version == request.version:
            return None, "version_exists"
        value = AgentDefinition(
            user_id=user_id,
            project_id=request.project_id,
            agent_key=request.agent_key,
            name=request.name,
            description=request.description,
            instructions=request.instructions,
            model=request.model,
            output_schema=request.output_schema,
            runtime_config=request.runtime_config,
            version=request.version,
            enabled=True,
            builtin=False,
        )
        self.db.add(value)
        self.db.commit()
        self.db.refresh(value)
        return value, "created"

    def create_workflow(
        self,
        *,
        request: WorkflowDefinitionCreate,
        user_id: int,
    ) -> tuple[AgentWorkflowDefinition | None, str]:
        if not self._owned_project(project_id=request.project_id, user_id=user_id):
            return None, "project_not_found"
        for node in request.definition.nodes:
            if node.node_type in {"agent", "agent_map"}:
                exists = self.repo.get_agent(
                    project_id=request.project_id,
                    agent_key=node.reference_key,
                )
            else:
                exists = self.repo.get_tool(
                    project_id=request.project_id,
                    tool_key=node.reference_key,
                )
            if exists is None:
                return None, f"unknown_node_reference:{node.reference_key}"
        existing = self.repo.get_workflow(
            project_id=request.project_id,
            workflow_key=request.workflow_key,
            enabled_only=False,
        )
        if existing is not None and existing.version == request.version:
            return None, "version_exists"
        value = AgentWorkflowDefinition(
            user_id=user_id,
            project_id=request.project_id,
            workflow_key=request.workflow_key,
            name=request.name,
            description=request.description,
            definition=request.definition.model_dump(),
            version=request.version,
            enabled=True,
            builtin=False,
        )
        self.db.add(value)
        self.db.commit()
        self.db.refresh(value)
        return value, "created"

    def bind_tool(
        self,
        *,
        project_id: int,
        agent_key: str,
        tool_key: str,
        user_id: int,
    ) -> str:
        if not self._owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found"
        agent = self.repo.get_agent(project_id=project_id, agent_key=agent_key)
        tool = self.repo.get_tool(project_id=project_id, tool_key=tool_key)
        if agent is None or tool is None:
            return "definition_not_found"
        binding = (
            self.db.query(AgentToolBinding)
            .filter(
                AgentToolBinding.agent_definition_id == agent.id,
                AgentToolBinding.tool_definition_id == tool.id,
            )
            .first()
        )
        if binding is None:
            binding = AgentToolBinding(
                agent_definition_id=agent.id,
                tool_definition_id=tool.id,
            )
        binding.enabled = True
        self.db.add(binding)
        self.db.commit()
        return "bound"

    def create_run(
        self,
        *,
        request: AgentRunCreate,
        user_id: int,
        dispatch: bool = True,
    ) -> tuple[AgentRun | None, str]:
        if not self.ensure_builtins(project_id=request.project_id, user_id=user_id):
            return None, "project_not_found"
        workflow = self.repo.get_workflow(
            project_id=request.project_id,
            workflow_key=request.workflow_key,
        )
        if workflow is None:
            return None, "workflow_not_found"
        graph = WorkflowGraph.model_validate(workflow.definition)
        try:
            validate(instance=request.input_payload, schema=graph.input_schema)
        except ValidationError as exc:
            return None, f"invalid_run_input:{exc.message}"
        run = AgentRun(
            user_id=user_id,
            project_id=request.project_id,
            workflow_definition_id=workflow.id,
            status="pending",
            input_payload=request.input_payload,
            run_context={
                "node_outputs": {},
                "artifacts": {},
                "execution_limits": _resolved_execution_limits(request),
                "usage": {
                    "attempted_requests": 0,
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "runtime_registry_signature": runtime_registry_signature(),
            },
            output_payload={},
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        if dispatch:
            if self._start_worker is None:
                raise RuntimeError("Agent Run 未配置后台执行器")
            self._start_worker(run.id)
        self.db.refresh(run)
        return run, "created"

    def list_runs(
        self,
        *,
        project_id: int,
        user_id: int,
        limit: int,
    ) -> list[AgentRun] | None:
        if not self._owned_project(project_id=project_id, user_id=user_id):
            return None
        return self.repo.list_runs(
            project_id=project_id,
            user_id=user_id,
            limit=limit,
        )

    def get_run(self, *, run_id: int, user_id: int) -> AgentRun | None:
        return self.repo.get_owned_run(run_id=run_id, user_id=user_id)

    def retry_run(self, *, run_id: int, user_id: int) -> tuple[AgentRun | None, str]:
        run = self.repo.get_owned_run(run_id=run_id, user_id=user_id)
        if run is None:
            return None, "run_not_found"
        if run.status in {"pending", "running", "waiting_approval"}:
            return None, "run_not_retryable"
        expected_signature = str(
            (run.run_context or {}).get("runtime_registry_signature") or ""
        )
        if not expected_signature or expected_signature != runtime_registry_signature():
            return None, "run_version_mismatch"
        run.status = "pending"
        run.error_message = ""
        run.output_payload = {}
        run.task_id = None
        run.claim_token = None
        run.heartbeat_at = None
        run.lease_expires_at = None
        run.finished_at = None
        self.repo.append_event(
            run_id=run.id,
            event_type="run_retry_requested",
            payload={"user_id": user_id},
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        self._start_worker(run.id)
        return run, "retried"

    def cancel_run(self, *, run_id: int, user_id: int) -> tuple[AgentRun | None, str]:
        run = self.repo.get_owned_run(run_id=run_id, user_id=user_id)
        if run is None:
            return None, "run_not_found"
        if run.status in {"success", "failed", "cancelled"}:
            return run, "already_finished"
        run.status = "cancelled"
        run.finished_at = datetime.utcnow()
        run.claim_token = None
        run.heartbeat_at = None
        run.lease_expires_at = None
        self.repo.append_event(
            run_id=run.id,
            event_type="run_cancelled",
            payload={"user_id": user_id},
        )
        self.db.add(run)
        self.db.commit()
        return run, "cancelled"

    def decide_approval(
        self,
        *,
        approval_id: int,
        decision: ApprovalDecision,
        user_id: int,
    ) -> tuple[AgentApproval | None, str]:
        approval = self.repo.get_owned_approval(
            approval_id=approval_id,
            user_id=user_id,
        )
        if approval is None:
            return None, "approval_not_found"
        if approval.status != "pending":
            return approval, "already_decided"
        approval.status = "approved" if decision.approved else "rejected"
        approval.decision_payload = {"reason": decision.reason}
        approval.decided_at = datetime.utcnow()
        approval.decided_by_user_id = user_id
        run = self.repo.get_run(run_id=approval.run_id)
        if run is None:
            return None, "run_not_found"
        if decision.approved:
            run.status = "pending"
            run.task_id = None
            self.db.add(approval)
            self.db.add(run)
            self.db.commit()
            self._start_worker(run.id)
            return approval, "approved"
        run.status = "failed"
        run.error_message = f"审批拒绝: {decision.reason}".strip()
        run.finished_at = datetime.utcnow()
        node_run = self.db.get(AgentNodeRun, approval.node_run_id)
        if node_run is not None:
            node_run.status = "failed"
            node_run.error_message = run.error_message
            node_run.finished_at = datetime.utcnow()
            self.db.add(node_run)
        self.db.add(approval)
        self.db.add(run)
        self.db.commit()
        return approval, "rejected"
