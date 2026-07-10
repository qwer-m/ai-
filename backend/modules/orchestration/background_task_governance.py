"""Lightweight governance helpers for non-Celery background work.

This module does not move work between runtimes. It standardizes metadata and
logging around existing background execution styles so later migrations can be
made from evidence instead of ad hoc call sites.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

from modules.orchestration.task_names import TaskName

if TYPE_CHECKING:
    from modules.orchestration.task_dispatcher import TaskDispatchResult


class BackgroundTaskCategory(str, Enum):
    DURABLE_JOB = "durable_job"
    BEST_EFFORT_HOOK = "best_effort_hook"
    IN_REQUEST_PARALLEL = "in_request_parallel"
    SCHEDULED_JOB = "scheduled_job"
    PROCESS_LOCAL_WORKER = "process_local_worker"


class BackgroundTaskKind(str, Enum):
    TEST_GENERATION = "test_generation"
    KNOWLEDGE_DOCUMENT_PARSE = "knowledge_document_parse"
    CONTEXT_SNAPSHOT_REBUILD = "context_snapshot_rebuild"
    KNOWLEDGE_INDEX_AUDIT = "knowledge_index_audit"
    CLEANUP_LOGS = "cleanup_logs"
    ARCHIVE_OLD_DATA = "archive_old_data"
    PIPELINE_RUN = "pipeline_run"
    PIPELINE_RUN_RECOVERY = "pipeline_run_recovery"
    RAG_EVAL_RUN = "rag_eval_run"
    TEST_CASE_COMPARE = "test_case_compare"


class TaskBackend(str, Enum):
    CELERY = "celery"
    FASTAPI_BACKGROUND_TASKS = "fastapi_background_tasks"
    PROCESS_LOCAL_WORKER = "process_local_worker"
    THREADPOOL = "threadpool"


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


@dataclass(frozen=True)
class ThreadPoolItemResult:
    index: int
    item: Any
    result: Any = None
    exception: Exception | None = None


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
            key="celery_test_generation",
            category=BackgroundTaskCategory.DURABLE_JOB,
            owner="orchestration",
            user_visible=True,
            durable=True,
            status_source="/api/tasks/{task_id}",
            recommended_runtime="celery",
            notes="Long-running test generation with queue state and retry boundary.",
        ),
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
            key="celery_context_snapshot",
            category=BackgroundTaskCategory.DURABLE_JOB,
            owner="knowledge_base",
            user_visible=False,
            durable=True,
            status_source="project_context_snapshots",
            recommended_runtime="celery",
            notes="Async prewarm task; generation falls back to RAG when not ready.",
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
            key="celery_archive_old_data",
            category=BackgroundTaskCategory.SCHEDULED_JOB,
            owner="maintenance",
            user_visible=False,
            durable=True,
            status_source="/api/tasks/{task_id}",
            recommended_runtime="celery",
            notes="Scheduled archival task triggered by Celery Beat.",
        ),
        _profile(
            key="pipeline_run_worker",
            category=BackgroundTaskCategory.DURABLE_JOB,
            owner="orchestration_pipeline",
            user_visible=True,
            durable=True,
            status_source="pipeline_runs",
            recommended_runtime="celery",
            notes="Pipeline execution is queued through Celery and state is persisted on pipeline_runs.",
        ),
        _profile(
            key="pipeline_run_recovery",
            category=BackgroundTaskCategory.SCHEDULED_JOB,
            owner="orchestration_pipeline",
            user_visible=False,
            durable=True,
            status_source="pipeline_runs",
            recommended_runtime="celery",
            notes="Scheduled scanner that requeues expired running pipeline runs through the governed dispatcher.",
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
        _profile(
            key="evaluation_compare_background",
            category=BackgroundTaskCategory.DURABLE_JOB,
            owner="evaluation",
            user_visible=True,
            durable=True,
            status_source="test_generation_comparisons",
            recommended_runtime="celery",
            notes=(
                "Request returns a running artifact; execution is queued through "
                "Celery and result is persisted on TestGenerationComparison."
            ),
        ),
        _profile(
            key="test_generation_estimate_threadpool",
            category=BackgroundTaskCategory.IN_REQUEST_PARALLEL,
            owner="test_generation",
            user_visible=False,
            durable=False,
            status_source="request",
            recommended_runtime="threadpool",
            notes="Used to keep a request responsive; no standalone lifecycle.",
        ),
        _profile(
            key="test_generation_coverage_shard_threadpool",
            category=BackgroundTaskCategory.IN_REQUEST_PARALLEL,
            owner="test_generation",
            user_visible=False,
            durable=False,
            status_source="request",
            recommended_runtime="threadpool",
            notes="Bounded coverage-shard model calls inside a streaming generation request.",
        ),
        _profile(
            key="pipeline_agent_executor_threadpool",
            category=BackgroundTaskCategory.IN_REQUEST_PARALLEL,
            owner="orchestration_pipeline",
            user_visible=False,
            durable=False,
            status_source="pipeline_runs",
            recommended_runtime="threadpool",
            notes="Bounded parallel rule checks inside a durable pipeline run worker.",
        ),
        _profile(
            key="snapshot_sync_retry_threadpool",
            category=BackgroundTaskCategory.IN_REQUEST_PARALLEL,
            owner="test_generation",
            user_visible=False,
            durable=False,
            status_source="request",
            recommended_runtime="threadpool",
            notes="Bounded synchronous retry with timeout inside generation gate.",
        ),
    )
)


BACKGROUND_TASK_SPECS: dict[BackgroundTaskKind, BackgroundTaskSpec] = _specs_by_kind(
    (
        _spec(
            kind=BackgroundTaskKind.TEST_GENERATION,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.DURABLE_JOB,
            business_type="test_generation",
            profile_key="celery_test_generation",
            task_name=TaskName.GENERATE_TEST_CASES,
            default_reason="generate_tests_async",
        ),
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
            kind=BackgroundTaskKind.CONTEXT_SNAPSHOT_REBUILD,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.DURABLE_JOB,
            business_type="context_snapshot",
            profile_key="celery_context_snapshot",
            task_name=TaskName.BUILD_CONTEXT_SNAPSHOT,
            default_reason="snapshot_rebuild",
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
            kind=BackgroundTaskKind.ARCHIVE_OLD_DATA,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.SCHEDULED_JOB,
            business_type="maintenance",
            profile_key="celery_archive_old_data",
            task_name=TaskName.ARCHIVE_OLD_DATA,
            default_reason="scheduled_archive",
        ),
        _spec(
            kind=BackgroundTaskKind.PIPELINE_RUN,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.DURABLE_JOB,
            business_type="pipeline_run",
            profile_key="pipeline_run_worker",
            task_name=TaskName.RUN_PIPELINE,
            default_reason="pipeline_run_worker",
        ),
        _spec(
            kind=BackgroundTaskKind.PIPELINE_RUN_RECOVERY,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.SCHEDULED_JOB,
            business_type="pipeline_run",
            profile_key="pipeline_run_recovery",
            task_name=TaskName.RECOVER_EXPIRED_PIPELINE_RUNS,
            default_reason="scheduled_pipeline_run_recovery",
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
        _spec(
            kind=BackgroundTaskKind.TEST_CASE_COMPARE,
            backend=TaskBackend.CELERY,
            category=BackgroundTaskCategory.DURABLE_JOB,
            business_type="test_case_compare",
            profile_key="evaluation_compare_background",
            task_name=TaskName.COMPARE_TEST_CASES,
            default_reason="evaluation_compare_background",
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


def _resolve_profile(
    *,
    profile_key: str,
    fallback_category: BackgroundTaskCategory,
) -> BackgroundTaskProfile:
    return BACKGROUND_TASK_PROFILES.get(
        profile_key,
        BackgroundTaskProfile(
            key=profile_key,
            category=fallback_category,
            owner="unknown",
            user_visible=False,
            durable=False,
            status_source="unknown",
            recommended_runtime=fallback_category.value,
        ),
    )


def wrap_background_callable(
    *,
    profile_key: str,
    category: BackgroundTaskCategory,
    target: Callable[..., Any],
    business_id: Any | None = None,
    task_logger: logging.Logger | None = None,
) -> Callable[..., Any]:
    profile = _resolve_profile(profile_key=profile_key, fallback_category=category)
    logger = task_logger or logging.getLogger(__name__)

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        logger.info(
            "background_task_started key=%s category=%s business_id=%s runtime=%s",
            profile.key,
            profile.category.value,
            business_id,
            profile.recommended_runtime,
        )
        try:
            result = target(*args, **kwargs)
        except Exception:
            logger.exception(
                "background_task_failed key=%s category=%s business_id=%s runtime=%s",
                profile.key,
                profile.category.value,
                business_id,
                profile.recommended_runtime,
            )
            raise
        logger.info(
            "background_task_finished key=%s category=%s business_id=%s runtime=%s",
            profile.key,
            profile.category.value,
            business_id,
            profile.recommended_runtime,
        )
        return result

    return _wrapped


def run_governed_threadpool_call(
    *,
    profile_key: str,
    target: Callable[..., Any],
    args: Iterable[Any] = (),
    kwargs: Mapping[str, Any] | None = None,
    timeout: float | None = None,
    max_workers: int = 1,
    thread_name_prefix: str | None = None,
    business_id: Any | None = None,
    task_logger: logging.Logger | None = None,
) -> Any:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

    profile = _resolve_profile(
        profile_key=profile_key,
        fallback_category=BackgroundTaskCategory.IN_REQUEST_PARALLEL,
    )
    logger = task_logger or logging.getLogger(__name__)
    worker_count = max(1, int(max_workers or 1))
    logger.info(
        "threadpool_task_started key=%s category=%s business_id=%s workers=%s timeout=%s",
        profile.key,
        profile.category.value,
        business_id,
        worker_count,
        timeout,
    )
    wrapped = wrap_background_callable(
        profile_key=profile.key,
        category=BackgroundTaskCategory.IN_REQUEST_PARALLEL,
        target=target,
        business_id=business_id,
        task_logger=logger,
    )
    executor = ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix=thread_name_prefix or profile.key,
    )
    future = executor.submit(wrapped, *tuple(args), **dict(kwargs or {}))
    try:
        result = future.result(timeout=timeout)
    except FutureTimeoutError:
        future.cancel()
        logger.warning(
            "threadpool_task_timeout key=%s category=%s business_id=%s timeout=%s",
            profile.key,
            profile.category.value,
            business_id,
            timeout,
        )
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    logger.info(
        "threadpool_task_finished key=%s category=%s business_id=%s workers=%s",
        profile.key,
        profile.category.value,
        business_id,
        worker_count,
    )
    return result


def run_governed_threadpool_map(
    *,
    profile_key: str,
    items: Iterable[Any],
    worker: Callable[[Any], Any],
    max_workers: int,
    thread_name_prefix: str | None = None,
    business_id: Any | None = None,
    task_logger: logging.Logger | None = None,
) -> list[ThreadPoolItemResult]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    profile = _resolve_profile(
        profile_key=profile_key,
        fallback_category=BackgroundTaskCategory.IN_REQUEST_PARALLEL,
    )
    logger = task_logger or logging.getLogger(__name__)
    item_list = list(items)
    worker_count = max(1, int(max_workers or 1))
    logger.info(
        "threadpool_map_started key=%s category=%s business_id=%s workers=%s items=%s",
        profile.key,
        profile.category.value,
        business_id,
        worker_count,
        len(item_list),
    )
    results: list[ThreadPoolItemResult] = []
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix=thread_name_prefix or profile.key,
    ) as pool:
        future_to_item = {
            pool.submit(worker, item): (index, item)
            for index, item in enumerate(item_list)
        }
        for future in as_completed(future_to_item):
            index, item = future_to_item[future]
            try:
                results.append(
                    ThreadPoolItemResult(
                        index=index,
                        item=item,
                        result=future.result(),
                    )
                )
            except Exception as exc:
                logger.exception(
                    "threadpool_map_item_failed key=%s category=%s business_id=%s index=%s",
                    profile.key,
                    profile.category.value,
                    business_id,
                    index,
                )
                results.append(
                    ThreadPoolItemResult(
                        index=index,
                        item=item,
                        exception=exc,
                    )
                )
    results.sort(key=lambda item: item.index)
    logger.info(
        "threadpool_map_finished key=%s category=%s business_id=%s workers=%s items=%s",
        profile.key,
        profile.category.value,
        business_id,
        worker_count,
        len(item_list),
    )
    return results


def create_process_local_worker_thread(
    *,
    profile_key: str,
    target: Callable[..., Any],
    args: Iterable[Any] = (),
    kwargs: Mapping[str, Any] | None = None,
    name: str,
    business_id: Any | None = None,
    daemon: bool = True,
    task_logger: logging.Logger | None = None,
) -> threading.Thread:
    wrapped = wrap_background_callable(
        profile_key=profile_key,
        category=BackgroundTaskCategory.PROCESS_LOCAL_WORKER,
        target=target,
        business_id=business_id,
        task_logger=task_logger,
    )
    return threading.Thread(
        target=wrapped,
        args=tuple(args),
        kwargs=dict(kwargs or {}),
        daemon=daemon,
        name=name,
    )


def add_fastapi_background_task(
    background_tasks: Any,
    *,
    profile_key: str,
    target: Callable[..., Any],
    args: Iterable[Any] = (),
    kwargs: Mapping[str, Any] | None = None,
    business_id: Any | None = None,
    task_logger: logging.Logger | None = None,
) -> None:
    wrapped = wrap_background_callable(
        profile_key=profile_key,
        category=BackgroundTaskCategory.BEST_EFFORT_HOOK,
        target=target,
        business_id=business_id,
        task_logger=task_logger,
    )
    background_tasks.add_task(wrapped, *tuple(args), **dict(kwargs or {}))


def add_best_effort_background_task(
    background_tasks: Any,
    *,
    profile_key: str,
    target: Callable[..., Any],
    args: Iterable[Any] = (),
    kwargs: Mapping[str, Any] | None = None,
    business_id: Any | None = None,
    task_logger: logging.Logger | None = None,
) -> None:
    add_fastapi_background_task(
        background_tasks,
        profile_key=profile_key,
        target=target,
        args=args,
        kwargs=kwargs,
        business_id=business_id,
        task_logger=task_logger,
    )
