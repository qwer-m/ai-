"""Celery 后台任务的统一注册、投递与状态契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from modules.orchestration.task_names import TaskName

if TYPE_CHECKING:
    from modules.orchestration.task_dispatcher import TaskDispatchResult


class BackgroundTaskCategory(str, Enum):
    DURABLE_JOB = "durable_job"
    SCHEDULED_JOB = "scheduled_job"


class BackgroundTaskKind(str, Enum):
    KNOWLEDGE_DOCUMENT_PARSE = "knowledge_document_parse"
    KNOWLEDGE_INDEX_AUDIT = "knowledge_index_audit"
    CLEANUP_LOGS = "cleanup_logs"
    AGENT_RUN = "agent_run"
    AGENT_RUN_RECOVERY = "agent_run_recovery"
    RAG_EVAL_RUN = "rag_eval_run"


class TaskBackend(str, Enum):
    CELERY = "celery"


@dataclass(frozen=True)
class BackgroundTaskSpec:
    kind: BackgroundTaskKind
    backend: TaskBackend
    category: BackgroundTaskCategory
    business_type: str
    profile_key: str
    task_name: TaskName | None = None
    default_reason: str = "queued"
    durable_candidate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "backend": self.backend.value,
            "category": self.category.value,
            "business_type": self.business_type,
            "profile_key": self.profile_key,
            "task_name": self.task_name.value if self.task_name else None,
            "default_reason": self.default_reason,
            "durable_candidate": self.durable_candidate,
        }


@dataclass(frozen=True)
class BackgroundTaskProfile:
    key: str
    category: BackgroundTaskCategory
    owner: str
    user_visible: bool
    durable: bool
    status_source: str
    recommended_runtime: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category.value,
            "owner": self.owner,
            "user_visible": self.user_visible,
            "durable": self.durable,
            "status_source": self.status_source,
            "recommended_runtime": self.recommended_runtime,
            "notes": self.notes,
        }


def _profile(
    *,
    key: str,
    category: BackgroundTaskCategory,
    owner: str,
    user_visible: bool,
    durable: bool,
    status_source: str,
    recommended_runtime: str,
    notes: str = "",
) -> BackgroundTaskProfile:
    return BackgroundTaskProfile(
        key=key,
        category=category,
        owner=owner,
        user_visible=user_visible,
        durable=durable,
        status_source=status_source,
        recommended_runtime=recommended_runtime,
        notes=notes,
    )


def _spec(
    *,
    kind: BackgroundTaskKind,
    backend: TaskBackend,
    category: BackgroundTaskCategory,
    business_type: str,
    profile_key: str,
    task_name: TaskName | None = None,
    default_reason: str = "queued",
    durable_candidate: bool = False,
) -> BackgroundTaskSpec:
    return BackgroundTaskSpec(
        kind=kind,
        backend=backend,
        category=category,
        business_type=business_type,
        profile_key=profile_key,
        task_name=task_name,
        default_reason=default_reason,
        durable_candidate=durable_candidate,
    )


def _profiles_by_key(
    profiles: Iterable[BackgroundTaskProfile],
) -> dict[str, BackgroundTaskProfile]:
    indexed: dict[str, BackgroundTaskProfile] = {}
    for profile in profiles:
        if profile.key in indexed:
            raise ValueError(f"duplicate background task profile key: {profile.key}")
        indexed[profile.key] = profile
    return indexed


def _specs_by_kind(
    specs: Iterable[BackgroundTaskSpec],
) -> dict[BackgroundTaskKind, BackgroundTaskSpec]:
    indexed: dict[BackgroundTaskKind, BackgroundTaskSpec] = {}
    for spec in specs:
        if spec.kind in indexed:
            raise ValueError(f"duplicate background task kind: {spec.kind.value}")
        indexed[spec.kind] = spec
    return indexed


def _validate_background_task_registry(
    *,
    profiles: Mapping[str, BackgroundTaskProfile],
    specs: Mapping[BackgroundTaskKind, BackgroundTaskSpec],
) -> None:
    for spec in specs.values():
        if spec.profile_key not in profiles:
            raise ValueError(
                f"background task {spec.kind.value} references unknown profile {spec.profile_key}"
            )
        if spec.backend == TaskBackend.CELERY and spec.task_name is None:
            raise ValueError(f"celery background task {spec.kind.value} requires task_name")
        if spec.backend != TaskBackend.CELERY and spec.task_name is not None:
            raise ValueError(
                f"non-celery background task {spec.kind.value} must not declare task_name"
            )


BACKGROUND_TASK_PROFILES: dict[str, BackgroundTaskProfile] = _profiles_by_key(
    (
        _profile(
            key="celery_knowledge_parse",
            category=BackgroundTaskCategory.DURABLE_JOB,
            owner="knowledge_base",
            user_visible=True,
            durable=True,
            status_source="/api/knowledge/{doc_id}/parse-status",
            recommended_runtime="celery",
            notes="Document parse status is also persisted on KnowledgeDocument.",
        ),
        _profile(
            key="celery_knowledge_index_audit",
            category=BackgroundTaskCategory.DURABLE_JOB,
            owner="knowledge_base",
            user_visible=True,
            durable=True,
            status_source="/api/tasks/{task_id}",
            recommended_runtime="celery",
            notes="Manual or scheduled audit of DB/vector-store consistency.",
        ),
        _profile(
            key="celery_cleanup_logs",
            category=BackgroundTaskCategory.SCHEDULED_JOB,
            owner="maintenance",
            user_visible=False,
            durable=True,
            status_source="/api/tasks/{task_id}",
            recommended_runtime="celery",
            notes="Scheduled log cleanup task triggered by Celery Beat.",
        ),
        _profile(
            key="agent_run_worker",
            category=BackgroundTaskCategory.DURABLE_JOB,
            owner="agent_platform",
            user_visible=True,
            durable=True,
            status_source="agent_runs",
            recommended_runtime="celery",
            notes="Agent 工作流由 Celery 调度，状态持久化到 agent_runs 与 agent_node_runs。",
        ),
        _profile(
            key="agent_run_recovery",
            category=BackgroundTaskCategory.SCHEDULED_JOB,
            owner="agent_platform",
            user_visible=False,
            durable=True,
            status_source="agent_runs",
            recommended_runtime="celery",
            notes="定时扫描租约过期的 Agent Run，并通过统一调度器重新投递。",
        ),
        _profile(
            key="rag_eval_run_worker",
            category=BackgroundTaskCategory.DURABLE_JOB,
            owner="rag_eval",
            user_visible=True,
            durable=True,
            status_source="rag_eval_runs",
            recommended_runtime="celery",
            notes="Supports stop/resume through persisted cursor and stop_requested fields.",
        ),
    )
)


BACKGROUND_TASK_SPECS: dict[BackgroundTaskKind, BackgroundTaskSpec] = _specs_by_kind(
    (
        _spec(
            kind=BackgroundTaskKind.KNOWLEDGE_DOCUMENT_PARSE,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.DURABLE_JOB,
            business_type="knowledge_document",
            profile_key="celery_knowledge_parse",
            task_name=TaskName.PARSE_KNOWLEDGE_DOCUMENT,
            default_reason="offline_parse",
        ),
        _spec(
            kind=BackgroundTaskKind.KNOWLEDGE_INDEX_AUDIT,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.DURABLE_JOB,
            business_type="knowledge_index_audit",
            profile_key="celery_knowledge_index_audit",
            task_name=TaskName.AUDIT_KNOWLEDGE_INDEX_CONSISTENCY,
            default_reason="manual_index_consistency_audit",
        ),
        _spec(
            kind=BackgroundTaskKind.CLEANUP_LOGS,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.SCHEDULED_JOB,
            business_type="maintenance",
            profile_key="celery_cleanup_logs",
            task_name=TaskName.CLEANUP_LOGS,
            default_reason="scheduled_cleanup",
        ),
        _spec(
            kind=BackgroundTaskKind.AGENT_RUN,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.DURABLE_JOB,
            business_type="agent_run",
            profile_key="agent_run_worker",
            task_name=TaskName.RUN_AGENT_WORKFLOW,
            default_reason="agent_run_worker",
        ),
        _spec(
            kind=BackgroundTaskKind.AGENT_RUN_RECOVERY,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.SCHEDULED_JOB,
            business_type="agent_run",
            profile_key="agent_run_recovery",
            task_name=TaskName.RECOVER_EXPIRED_AGENT_RUNS,
            default_reason="scheduled_agent_run_recovery",
        ),
        _spec(
            kind=BackgroundTaskKind.RAG_EVAL_RUN,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.DURABLE_JOB,
            business_type="rag_eval_run",
            profile_key="rag_eval_run_worker",
            task_name=TaskName.RUN_RAG_EVAL,
            default_reason="rag_eval_run_worker",
        ),
    )
)

_validate_background_task_registry(
    profiles=BACKGROUND_TASK_PROFILES,
    specs=BACKGROUND_TASK_SPECS,
)


def get_background_task_spec(kind: BackgroundTaskKind | str) -> BackgroundTaskSpec:
    normalized = kind if isinstance(kind, BackgroundTaskKind) else BackgroundTaskKind(str(kind))
    return BACKGROUND_TASK_SPECS[normalized]


def list_background_task_specs(
    backend: TaskBackend | str | None = None,
) -> list[BackgroundTaskSpec]:
    if backend is None:
        return list(BACKGROUND_TASK_SPECS.values())
    backend_value = backend.value if isinstance(backend, TaskBackend) else str(backend)
    return [spec for spec in BACKGROUND_TASK_SPECS.values() if spec.backend.value == backend_value]


def submit_background_task(
    kind: BackgroundTaskKind | str,
    *,
    kwargs: dict[str, Any] | None = None,
    business_id: Any | None = None,
    reason: str | None = None,
) -> "TaskDispatchResult":
    from modules.orchestration.task_dispatcher import enqueue_task

    spec = get_background_task_spec(kind)
    if spec.backend != TaskBackend.CELERY or spec.task_name is None:
        raise ValueError(f"background task kind {spec.kind.value} is not celery-backed")
    return enqueue_task(
        spec.task_name,
        kwargs=kwargs or {},
        business_type=spec.business_type,
        business_id=business_id,
        reason=reason or spec.default_reason,
        queue=spec.backend.value,
    )


def build_background_task_status(
    kind: BackgroundTaskKind | str,
    *,
    task_id: str | None = None,
    task_status: dict[str, Any] | None = None,
    business_id: Any | None = None,
    business_status: str | None = None,
    business_error: str | None = None,
    retry_count: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from modules.orchestration.task_status import build_task_status_payload

    spec = get_background_task_spec(kind)
    return build_task_status_payload(
        task_id=task_id,
        task_status=task_status,
        business_type=spec.business_type,
        business_id=business_id,
        business_status=business_status,
        business_error=business_error,
        retry_count=retry_count,
        queue=spec.backend.value,
        extra=extra,
    )


def get_background_task_profile(key: str) -> BackgroundTaskProfile:
    return BACKGROUND_TASK_PROFILES[key]


def list_background_task_profiles(
    category: BackgroundTaskCategory | str | None = None,
) -> list[BackgroundTaskProfile]:
    if category is None:
        return list(BACKGROUND_TASK_PROFILES.values())
    category_value = category.value if isinstance(category, BackgroundTaskCategory) else str(category)
    return [
        profile
        for profile in BACKGROUND_TASK_PROFILES.values()
        if profile.category.value == category_value
    ]
