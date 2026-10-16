from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from jsonschema import ValidationError, validate
from sqlalchemy.orm import Session

from core.db.model_defs import (
    AgentApproval,
    AgentDefinition,
    AgentNodeRun,
    AgentRun,
    AgentWorkflowDefinition,
)
from core.settings.config import settings
from modules.knowledge_base_components.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from modules.system_components.repositories.project_repository import ProjectRepository
from .contracts import (
    AgentProgramDefinition,
    AgentDefinitionCreate,
    AgentRunCreate,
    ApprovalDecision,
    WorkflowDefinitionCreate,
    WorkflowGraph,
    parse_execution_definition,
)
from .context_compression import (
    context_compression_enabled,
    context_compression_max_tokens,
)
from .run_repository import AgentRunRepository
from .definition_repository import AgentDefinitionRepository
from .input_snapshots import restore_node_input
from .lifecycle import ACTIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES, transition_run
from .results import persisted_test_generation_result
from .sources import (
    SOURCE_ARTIFACT_KEY, SourceSnapshot, assert_same_source, persisted_source_snapshot,
    latest_successful_run_for_source,
)
from .retention import prune_terminal_run_history
from .seed import seed_builtin_definitions
from .registry import runtime_registry_signature


def _resolved_execution_limits(request: AgentRunCreate) -> dict[str, int]:
    """按平台上限归一化单个 Agent 实例的执行额度。"""

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


def _initial_run_context(
    *,
    execution_limits: dict[str, int],
    run_attempt: int = 1,
    source: SourceSnapshot | None = None,
) -> dict[str, Any]:
    """创建全新的运行上下文，禁止重用旧节点输出和旧尝试状态。"""

    if run_attempt < 1:
        raise ValueError("运行执行次数必须从 1 开始")
    return {
        "run_attempt": run_attempt,
        "artifacts": {SOURCE_ARTIFACT_KEY: source.to_dict()} if source is not None else {},
        "execution_limits": dict(execution_limits),
        "quota_mode": "per_agent_instance",
        "agent_instance_quota_usage": {},
        "usage": {
            "attempted_requests": 0,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
        "runtime_registry_signature": runtime_registry_signature(),
    }


def _normalize_workflow_input(
    workflow_key: str,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """在输入边界归一化旧客户端字段，内部仅使用正式字段。"""

    normalized = dict(input_payload or {})
    if str(workflow_key or "").strip() != "test_generation":
        return normalized
    legacy_compression = normalized.pop("compress", None)
    if normalized.get("enable_context_compression") is None and legacy_compression is not None:
        normalized["enable_context_compression"] = legacy_compression
    normalized["enable_context_compression"] = context_compression_enabled(
        normalized,
        default=True,
    )
    normalized["context_compression_max_tokens"] = context_compression_max_tokens(
        normalized,
    )
    return normalized


def _has_restorable_repair_state(state: Any) -> bool:
    """只迁移已绑定输入且具备明确修复方式的状态，输入一致性由运行时复验。"""

    if not isinstance(state, dict):
        return False
    input_hash = state.get("input_hash")
    if not isinstance(input_hash, str) or not input_hash.strip():
        return False
    repair_context = state.get("repair_context")
    if not isinstance(repair_context, dict):
        return False
    if repair_context.get("mode") == "minimal_patch":
        return isinstance(repair_context.get("candidate_output"), dict)
    validation_feedback = repair_context.get("validation_feedback")
    return (
        repair_context.get("mode") == "full_regeneration"
        and isinstance(validation_feedback, str)
        and bool(validation_feedback.strip())
    )


def _has_restorable_node_repair_state(
    node: Any, node_run: AgentNodeRun, *, load_node: Callable[[int], Any | None] | None = None,
) -> bool:
    state = dict(node_run.sdk_state or {})
    if node.node_type in {"agent", "agent_network"}:
        return _has_restorable_repair_state(state)
    if node.node_type != "agent_map" or node.map_config is None:
        return False
    raw_items = restore_node_input(node_run, load_node).get(node.map_config.items_key)
    if not isinstance(raw_items, list):
        return False
    item_states = state.get("items")
    candidates = list(item_states) if isinstance(item_states, list) else []
    candidates.append(state.get("failed_item"))
    for candidate in candidates:
        if not _has_restorable_repair_state(candidate):
            continue
        index = candidate.get("item_index")
        if (
            type(index) is int
            and 0 <= index < len(raw_items)
            and isinstance(raw_items[index], dict)
        ):
            return True
    return False


@dataclass(frozen=True)
class _RestorableCheckpoint:
    """恢复计划同时保留修复和部分完成标志，两种情况可以发生在同一节点。"""

    node_run: AgentNodeRun
    has_repair_state: bool = False
    has_partial_map_output: bool = False
    has_action_checkpoint: bool = False


def _restorable_checkpoints(
    *,
    execution: WorkflowGraph,
    node_runs: list[AgentNodeRun],
) -> list[_RestorableCheckpoint]:
    """选择可安全迁移到重试 Run 的阶段检查点。"""

    node_types = {node.node_key: node.node_type for node in execution.nodes}
    nodes_by_id = {item.id: item for item in node_runs}
    latest_by_key: dict[str, AgentNodeRun] = {}
    for node_run in node_runs:
        expected_type = node_types.get(str(node_run.node_key))
        if expected_type is None or str(node_run.node_type) != expected_type:
            continue
        current = latest_by_key.get(str(node_run.node_key))
        if current is None or (int(node_run.attempt), int(node_run.id or 0)) > (
            int(current.attempt),
            int(current.id or 0),
        ):
            latest_by_key[str(node_run.node_key)] = node_run

    checkpoints: list[_RestorableCheckpoint] = []
    for node in execution.execution_order():
        node_run = latest_by_key.get(node.node_key)
        if node_run is None:
            continue
        if node_run.status == "success":
            checkpoints.append(_RestorableCheckpoint(node_run))
            continue
        has_repair_state = _has_restorable_node_repair_state(node, node_run, load_node=nodes_by_id.get)
        has_partial_map_output = False
        action_checkpoint = dict(node_run.sdk_state or {}).get("action_checkpoint")
        has_action_checkpoint = node.node_type == "agent_network" and isinstance(action_checkpoint, dict)
        if node.node_type == "agent_map" and node.map_config is not None:
            completed = dict(node_run.output_payload or {}).get(node.map_config.output_key)
            has_partial_map_output = isinstance(completed, list) and bool(completed)
        if has_repair_state or has_partial_map_output or has_action_checkpoint:
            checkpoints.append(_RestorableCheckpoint(
                node_run, has_repair_state, has_partial_map_output, has_action_checkpoint,
            ))
    return checkpoints


def _restored_checkpoint_sdk_state(checkpoint: AgentNodeRun) -> dict[str, Any]:
    """保留父运行检查点的真实执行信息，供当前运行区分“复用”与“重新执行”。"""

    state = deepcopy(checkpoint.sdk_state or {})
    duration_seconds = None
    if checkpoint.started_at is not None and checkpoint.finished_at is not None:
        duration_seconds = max(
            0,
            int((checkpoint.finished_at - checkpoint.started_at).total_seconds()),
        )
    state["checkpoint_restore"] = {
        "source_run_id": int(checkpoint.run_id),
        "source_node_run_id": int(checkpoint.id),
        "source_started_at": (
            checkpoint.started_at.isoformat() if checkpoint.started_at is not None else None
        ),
        "source_finished_at": (
            checkpoint.finished_at.isoformat() if checkpoint.finished_at is not None else None
        ),
        "source_duration_seconds": duration_seconds,
    }
    return state


def _restore_checkpoint_node(
    repo: AgentRunRepository, *, checkpoint: AgentNodeRun, run_id: int,
) -> AgentNodeRun:
    """继承检查点时先展开父输入，再仅引用本轮已复制的成功输出。"""
    status = "success" if checkpoint.status == "success" else "failed"
    restored = AgentNodeRun(
        run_id=run_id,
        node_key=checkpoint.node_key,
        node_type=checkpoint.node_type,
        agent_definition_id=checkpoint.agent_definition_id,
        tool_definition_id=checkpoint.tool_definition_id,
        status=status,
        # 继承检查点尚未执行，不能消耗新运行的节点重试预算。
        attempt=0,
        input_payload={},
        output_payload=deepcopy(checkpoint.output_payload or {}),
        sdk_state=_restored_checkpoint_sdk_state(checkpoint),
        error_message="" if status == "success" else "从父运行恢复执行检查点",
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
    )
    repo.write_node_input(restored, repo.read_node_input(checkpoint))
    repo.db.add(restored)
    # 后续检查点的引用需要本轮节点主键，逐节点落入当前事务。
    repo.db.flush()
    return restored


class AgentPlatformService:
    def __init__(
        self,
        db: Session,
        worker_starter: Callable[[int], Any] | None = None,
    ) -> None:
        self.db: Session = db
        self.projects: ProjectRepository = ProjectRepository(db)
        self.documents = KnowledgeDocumentRepository(db)
        self.runs: AgentRunRepository = AgentRunRepository(db)
        self.definitions: AgentDefinitionRepository = AgentDefinitionRepository(db)
        self._start_worker = worker_starter

    def ensure_builtins(self, *, project_id: int, user_id: int) -> bool:
        if self.projects.get_owned_project(project_id=project_id, user_id=user_id) is None:
            return False
        seed_builtin_definitions(db=self.db, project_id=project_id, user_id=user_id)
        return True

    def list_catalog(self, *, project_id: int, user_id: int) -> dict[str, list[Any]] | None:
        if not self.ensure_builtins(project_id=project_id, user_id=user_id):
            return None
        self.db.commit()
        return {
            "agents": self.definitions.list_agents(project_id=project_id),
            "tools": self.definitions.list_tools(project_id=project_id),
            "workflows": self.definitions.list_workflows(project_id=project_id),
        }

    def create_agent(
        self,
        *,
        request: AgentDefinitionCreate,
        user_id: int,
    ) -> tuple[AgentDefinition | None, str]:
        if self.projects.get_owned_project(project_id=request.project_id, user_id=user_id) is None:
            return None, "project_not_found"
        # 创建项目自定义定义时，只检查该项目已有覆盖，不能把全局模板当成同版本冲突。
        existing = self.definitions.get_project_agent_version(
            project_id=request.project_id,
            agent_key=request.agent_key,
            version=request.version,
        )
        if existing is not None:
            return None, "version_exists"
        value = self.definitions.create_agent(
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
        )
        self.db.commit()
        self.db.refresh(value)
        return value, "created"

    def create_workflow(
        self,
        *,
        request: WorkflowDefinitionCreate,
        user_id: int,
    ) -> tuple[AgentWorkflowDefinition | None, str]:
        if self.projects.get_owned_project(project_id=request.project_id, user_id=user_id) is None:
            return None, "project_not_found"
        if isinstance(request.definition, WorkflowGraph):
            for node in request.definition.nodes:
                if node.node_type in {"agent", "agent_network", "agent_map"}:
                    exists = self.definitions.get_agent(
                        project_id=request.project_id,
                        agent_key=node.reference_key,
                    )
                else:
                    exists = self.definitions.get_tool(
                        project_id=request.project_id,
                        tool_key=node.reference_key,
                    )
                if exists is None:
                    return None, f"unknown_node_reference:{node.reference_key}"
        elif isinstance(request.definition, AgentProgramDefinition):
            entry_agent = self.definitions.get_agent(
                project_id=request.project_id,
                agent_key=request.definition.entry_agent_key,
            )
            if entry_agent is None:
                return None, f"unknown_node_reference:{request.definition.entry_agent_key}"
        existing = self.definitions.get_workflow_version(
            project_id=request.project_id,
            workflow_key=request.workflow_key,
            version=request.version,
        )
        if existing is not None:
            return None, "version_exists"
        value = self.definitions.create_workflow(
            user_id=user_id,
            project_id=request.project_id,
            workflow_key=request.workflow_key,
            name=request.name,
            description=request.description,
            definition=request.definition.model_dump(),
            version=request.version,
        )
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
        if self.projects.get_owned_project(project_id=project_id, user_id=user_id) is None:
            return "project_not_found"
        agent = self.definitions.get_agent(project_id=project_id, agent_key=agent_key)
        tool = self.definitions.get_tool(project_id=project_id, tool_key=tool_key)
        if agent is None or tool is None:
            return "definition_not_found"
        self.definitions.bind_tool_to_agent(
            agent=agent, tool=tool, project_id=project_id, user_id=user_id,
        )
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
        # 同步事务先结束，再获取项目锁，避免与续跑的定义外键锁形成反向等待。
        self.db.commit()
        workflow = self.definitions.get_workflow(
            project_id=request.project_id,
            workflow_key=request.workflow_key,
        )
        if workflow is None:
            return None, "workflow_not_found"
        execution = parse_execution_definition(workflow.definition)
        try:
            validate(instance=request.input_payload, schema=execution.input_schema)
        except ValidationError as exc:
            return None, f"invalid_run_input:{exc.message}"
        self.runs.lock_run_creation(project_id=request.project_id, user_id=user_id)
        input_payload = _normalize_workflow_input(
            request.workflow_key,
            dict(request.input_payload or {}),
        )
        try:
            source = self.runs.resolve_source_snapshot(project_id=request.project_id, input_payload=input_payload)
        except ValueError as exc:
            return None, f"invalid_run_input:{exc}"
        active_run = self.runs.get_active_run_for_source(
            project_id=request.project_id,
            user_id=user_id,
            workflow_key=request.workflow_key,
            source=source,
        )
        if active_run is not None:
            self.db.commit()
            self.db.refresh(active_run)
            return active_run, "already_active"
        run = AgentRun(
            user_id=user_id,
            project_id=request.project_id,
            workflow_definition_id=workflow.id,
            status="pending",
            input_payload=input_payload,
            run_context=_initial_run_context(
                execution_limits=_resolved_execution_limits(request),
                source=source,
            ),
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
        workflow_key: str | None = None,
    ) -> list[AgentRun] | None:
        if self.projects.get_owned_project(project_id=project_id, user_id=user_id) is None:
            return None
        return self.runs.list_runs(
            project_id=project_id,
            user_id=user_id,
            limit=limit,
            workflow_key=workflow_key,
        )

    def get_active_run(self, *, project_id: int, user_id: int) -> AgentRun | None:
        if self.projects.get_owned_project(project_id=project_id, user_id=user_id) is None:
            return None
        return self.runs.get_active_run(project_id=project_id, user_id=user_id)

    def get_run(self, *, run_id: int, user_id: int) -> AgentRun | None:
        return self.runs.get_owned_run(run_id=run_id, user_id=user_id)

    def get_generation_reuse_candidate(
        self,
        *,
        project_id: int,
        user_id: int,
        workflow_key: str,
        requirement_doc_id: int,
    ) -> tuple[dict[str, Any] | None, str]:
        if self.projects.get_owned_project(project_id=project_id, user_id=user_id) is None:
            return None, "project_not_found"
        document = self.documents.get_project_doc(
            project_id=project_id,
            doc_id=requirement_doc_id,
        )
        if document is None:
            return None, "document_not_found"
        workflow = self.definitions.get_workflow(
            project_id=project_id,
            workflow_key=workflow_key,
        )
        if workflow is None:
            return None, "workflow_not_found"
        try:
            current_source = SourceSnapshot.from_document(document)
            run = latest_successful_run_for_source(
                self.runs,
                project_id=project_id, user_id=user_id,
                workflow_key=workflow_key, source=current_source,
            )
        except ValueError as exc:
            return None, f"invalid_run_input:{exc}"
        if run is None:
            return None, "not_found"

        artifact = persisted_test_generation_result(run)
        source = persisted_source_snapshot(run)
        test_cases = artifact.get("test_cases") if isinstance(artifact, dict) else []
        return {
            "run_id": int(run.id),
            "source_filename": source.filename if source else "",
            "case_count": len(test_cases) if isinstance(test_cases, list) else 0,
        }, "found"

    def get_test_case_export_data(
        self,
        *,
        run_id: int,
        user_id: int,
    ) -> tuple[dict[str, Any] | None, str]:
        run = self.runs.get_owned_run(run_id=run_id, user_id=user_id)
        if run is None:
            return None, "run_not_found"
        artifact = persisted_test_generation_result(run)
        raw_cases = artifact.get("test_cases") if artifact is not None else None
        if not isinstance(raw_cases, list) or not raw_cases:
            return None, "run_result_not_found"
        if not all(isinstance(test_case, dict) for test_case in raw_cases):
            return None, "run_result_invalid"

        source = persisted_source_snapshot(run)
        return {
            "test_cases": [dict(test_case) for test_case in raw_cases],
            "source_filename": source.filename if source else "",
        }, "found"

    def retry_run(self, *, run_id: int, user_id: int) -> tuple[AgentRun | None, str]:
        run = self.runs.get_owned_run(run_id=run_id, user_id=user_id)
        if run is None:
            return None, "run_not_found"
        if run.status in ACTIVE_RUN_STATUSES:
            return None, "run_not_retryable"
        expected_signature = str(
            (run.run_context or {}).get("runtime_registry_signature") or ""
        )
        if not expected_signature or expected_signature != runtime_registry_signature():
            return None, "run_version_mismatch"
        try:
            source = persisted_source_snapshot(run)
            current_source = self.runs.resolve_source_snapshot(project_id=run.project_id, input_payload=run.input_payload)
            if current_source is not None:
                if source is None:
                    return None, "run_source_snapshot_missing"
                assert_same_source(source, current_source)
        except ValueError:
            return None, "run_source_changed"
        if self._start_worker is None:
            raise RuntimeError("Agent Run 未配置后台执行器")
        workflow = self.db.get(AgentWorkflowDefinition, run.workflow_definition_id)
        if workflow is None:
            return None, "workflow_not_found"
        self.runs.lock_run_creation(project_id=run.project_id, user_id=user_id)
        active_run = self.runs.get_active_run_for_source(
            project_id=run.project_id, user_id=user_id,
            workflow_key=workflow.workflow_key, source=source,
        )
        if active_run is not None:
            return None, "run_source_already_active"
        execution = parse_execution_definition(workflow.definition)
        execution_limits = dict((run.run_context or {}).get("execution_limits") or {})
        parent_attempt = int(dict(run.run_context or {}).get("run_attempt") or 1)
        retry_context = _initial_run_context(
            execution_limits=execution_limits,
            run_attempt=max(1, parent_attempt) + 1,
            source=source,
        )
        checkpoints = (
            _restorable_checkpoints(
                execution=execution,
                node_runs=self.runs.list_node_runs(run_id=run.id),
            )
            if isinstance(execution, WorkflowGraph)
            else []
        )
        successful_output_count = sum(
            item.node_run.status == "success" for item in checkpoints
        )
        if checkpoints:
            retry_context["artifacts"] = deepcopy(
                dict((run.run_context or {}).get("artifacts") or {})
            )
            if source is not None:
                retry_context["artifacts"][SOURCE_ARTIFACT_KEY] = source.to_dict()
        retry = AgentRun(
            user_id=run.user_id,
            project_id=run.project_id,
            workflow_definition_id=run.workflow_definition_id,
            status="pending",
            input_payload=dict(run.input_payload or {}),
            run_context=retry_context,
            output_payload={},
            parent_run_id=run.id,
        )
        self.db.add(retry)
        self.db.flush()
        partial_map_count = sum(item.has_partial_map_output for item in checkpoints)
        repair_node_count = sum(item.has_repair_state for item in checkpoints)
        for item in checkpoints:
            _restore_checkpoint_node(self.runs, checkpoint=item.node_run, run_id=retry.id)
        restored_payload = {
            "restored_node_count": len(checkpoints),
            "restored_success_count": successful_output_count,
            "restored_partial_map_count": partial_map_count,
            "restored_repair_node_count": repair_node_count,
            "restored_action_node_count": sum(item.has_action_checkpoint for item in checkpoints),
        }
        self.runs.append_event(
            run_id=run.id,
            event_type="run_retry_spawned",
            payload={"user_id": user_id, "retry_run_id": retry.id, **restored_payload},
        )
        self.runs.append_event(
            run_id=retry.id,
            event_type="run_created_from_retry",
            payload={"user_id": user_id, "parent_run_id": run.id, **restored_payload},
        )
        self.db.commit()
        self.db.refresh(retry)
        self._start_worker(retry.id)
        return retry, "retried"

    def cancel_run(self, *, run_id: int, user_id: int) -> tuple[AgentRun | None, str]:
        run = self.runs.get_run_for_update(run_id=run_id, user_id=user_id)
        if run is None:
            return None, "run_not_found"
        if run.status in TERMINAL_RUN_STATUSES:
            return run, "already_finished"
        transition_run(
            self.runs, run, "cancelled",
            event_type="run_cancelled",
            payload={"user_id": user_id},
            actor_user_id=user_id,
        )
        self.db.commit()
        prune_terminal_run_history(self.runs, run)
        return run, "cancelled"

    def reset_run_attempt(
        self,
        *,
        run_id: int,
        user_id: int,
    ) -> tuple[AgentRun | None, str]:
        """只重置当前运行链的展示次数，保留运行、节点和事件审计数据。"""

        run = self.runs.get_owned_run(run_id=run_id, user_id=user_id)
        if run is None:
            return None, "run_not_found"
        if run.status in ACTIVE_RUN_STATUSES:
            return None, "run_attempt_reset_forbidden"
        context = deepcopy(dict(run.run_context or {}))
        previous_attempt = max(1, int(context.get("run_attempt") or 1))
        if previous_attempt == 1:
            return run, "already_reset"
        context["run_attempt"] = 1
        run.run_context = context
        self.db.add(run)
        self.runs.append_event(
            run_id=run.id,
            event_type="run_attempt_reset",
            payload={
                "user_id": user_id,
                "previous_attempt": previous_attempt,
                "current_attempt": 1,
            },
        )
        self.db.commit()
        self.db.refresh(run)
        return run, "reset"

    def decide_approval(
        self,
        *,
        approval_id: int,
        decision: ApprovalDecision,
        user_id: int,
    ) -> tuple[AgentApproval | None, str]:
        approval = self.runs.get_owned_approval(
            approval_id=approval_id,
            user_id=user_id,
        )
        if approval is None:
            return None, "approval_not_found"
        run = self.runs.get_run_for_update(run_id=approval.run_id, user_id=user_id)
        if run is None:
            return None, "run_not_found"
        self.db.refresh(approval)
        if run.status != "waiting_approval":
            return None, "approval_run_not_waiting"
        if approval.status != "pending":
            return approval, "already_decided"
        if decision.approved and self._start_worker is None:
            raise RuntimeError("Agent Run 未配置后台执行器")
        approval.status = "approved" if decision.approved else "rejected"
        approval.decision_payload = {"reason": decision.reason}
        approval.decided_at = datetime.utcnow()
        approval.decided_by_user_id = user_id
        if decision.approved:
            transition_run(
                self.runs, run, "pending", event_type="approval_approved",
                payload={"approval_id": approval.id, "user_id": user_id},
                actor_user_id=user_id, node_run_id=approval.node_run_id,
            )
            self.db.add(approval)
            self.db.add(run)
            self.db.commit()
            self._start_worker(run.id)
            return approval, "approved"
        transition_run(
            self.runs, run, "failed", event_type="approval_rejected",
            payload={"approval_id": approval.id, "user_id": user_id},
            error_message=f"审批拒绝: {decision.reason}".strip(),
            actor_user_id=user_id, node_run_id=approval.node_run_id,
        )
        self.db.add(approval)
        self.db.add(run)
        self.db.commit()
        prune_terminal_run_history(self.runs, run)
        return approval, "rejected"
