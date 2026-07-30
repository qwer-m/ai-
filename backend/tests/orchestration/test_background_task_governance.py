from __future__ import annotations

import logging
import threading

import pytest

from modules.orchestration.background_task_governance import (
    BackgroundTaskCategory,
    BackgroundTaskKind,
    TaskBackend,
    add_fastapi_background_task,
    build_background_task_status,
    create_process_local_worker_thread,
    get_background_task_spec,
    get_background_task_profile,
    list_background_task_specs,
    list_background_task_profiles,
    iter_governed_threadpool_map,
    run_governed_threadpool_call,
    run_governed_threadpool_map,
    submit_background_task,
    wrap_background_callable,
)
from modules.orchestration.task_dispatcher import TaskDispatchResult
from modules.orchestration.task_names import TaskName


class FakeBackgroundTasks:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add_task(self, target, *args, **kwargs) -> None:
        self.calls.append((target, args, kwargs))


def test_background_task_profiles_are_classified() -> None:
    profile = get_background_task_profile("rag_eval_run_worker")

    assert profile.category == BackgroundTaskCategory.DURABLE_JOB
    assert profile.user_visible is True
    assert profile.durable is True
    assert profile.status_source == "rag_eval_runs"

    pipeline_profile = get_background_task_profile("pipeline_run_worker")
    assert pipeline_profile.category == BackgroundTaskCategory.DURABLE_JOB
    assert pipeline_profile.durable is True
    assert pipeline_profile.user_visible is True
    assert pipeline_profile.status_source == "pipeline_runs"
    assert pipeline_profile.recommended_runtime == "celery"

    compare_profile = get_background_task_profile("evaluation_compare_background")
    assert compare_profile.category == BackgroundTaskCategory.DURABLE_JOB
    assert compare_profile.durable is True
    assert compare_profile.recommended_runtime == "celery"

    pipeline_agent_profile = get_background_task_profile("pipeline_agent_executor_threadpool")
    assert pipeline_agent_profile.category == BackgroundTaskCategory.IN_REQUEST_PARALLEL
    assert pipeline_agent_profile.durable is False
    assert pipeline_agent_profile.recommended_runtime == "threadpool"

    snapshot_retry_profile = get_background_task_profile("snapshot_sync_retry_threadpool")
    assert snapshot_retry_profile.category == BackgroundTaskCategory.IN_REQUEST_PARALLEL
    assert snapshot_retry_profile.durable is False
    assert snapshot_retry_profile.recommended_runtime == "threadpool"

    semantic_compile_profile = get_background_task_profile(
        "test_generation_semantic_compile_threadpool"
    )
    assert semantic_compile_profile.category == BackgroundTaskCategory.IN_REQUEST_PARALLEL
    assert semantic_compile_profile.durable is False
    assert semantic_compile_profile.user_visible is True
    assert semantic_compile_profile.status_source == "request_stream"

    graph_shard_profile = get_background_task_profile(
        "test_generation_graph_fact_shard_threadpool"
    )
    assert graph_shard_profile.category == BackgroundTaskCategory.IN_REQUEST_PARALLEL
    assert graph_shard_profile.durable is False
    assert graph_shard_profile.status_source == "request_stream"

    fact_ledger_shard_profile = get_background_task_profile(
        "test_generation_fact_ledger_shard_threadpool"
    )
    assert fact_ledger_shard_profile.category == BackgroundTaskCategory.IN_REQUEST_PARALLEL
    assert fact_ledger_shard_profile.durable is False
    assert fact_ledger_shard_profile.status_source == "request_stream"

    durable = list_background_task_profiles(BackgroundTaskCategory.DURABLE_JOB)
    assert {item.key for item in durable} >= {
        "celery_test_generation",
        "celery_knowledge_parse",
        "celery_context_snapshot",
        "rag_eval_run_worker",
        "evaluation_compare_background",
    }


def test_background_task_specs_cover_celery_and_migration_candidates() -> None:
    celery_specs = list_background_task_specs(TaskBackend.CELERY)

    assert {spec.task_name for spec in celery_specs} == {
        TaskName.GENERATE_TEST_CASES,
        TaskName.PARSE_KNOWLEDGE_DOCUMENT,
        TaskName.BUILD_CONTEXT_SNAPSHOT,
        TaskName.AUDIT_KNOWLEDGE_INDEX_CONSISTENCY,
        TaskName.COMPARE_TEST_CASES,
        TaskName.RUN_RAG_EVAL,
        TaskName.RUN_PIPELINE,
        TaskName.RECOVER_EXPIRED_PIPELINE_RUNS,
        TaskName.CLEANUP_LOGS,
        TaskName.ARCHIVE_OLD_DATA,
    }
    assert all(spec.business_type for spec in celery_specs)

    candidates = [spec.kind for spec in list_background_task_specs() if spec.durable_candidate]
    assert candidates == []


def test_background_task_specs_reference_existing_profiles() -> None:
    profiles = {profile.key for profile in list_background_task_profiles()}

    assert all(spec.profile_key in profiles for spec in list_background_task_specs())
    assert all(
        spec.task_name is not None
        for spec in list_background_task_specs(TaskBackend.CELERY)
    )


def test_pipeline_run_spec_is_durable_celery_job() -> None:
    spec = get_background_task_spec(BackgroundTaskKind.PIPELINE_RUN)

    assert spec.backend == TaskBackend.CELERY
    assert spec.category == BackgroundTaskCategory.DURABLE_JOB
    assert spec.business_type == "pipeline_run"
    assert spec.profile_key == "pipeline_run_worker"
    assert spec.task_name == TaskName.RUN_PIPELINE
    assert spec.default_reason == "pipeline_run_worker"


def test_pipeline_run_recovery_spec_is_scheduled_celery_job() -> None:
    profile = get_background_task_profile("pipeline_run_recovery")
    spec = get_background_task_spec(BackgroundTaskKind.PIPELINE_RUN_RECOVERY)

    assert profile.category == BackgroundTaskCategory.SCHEDULED_JOB
    assert profile.status_source == "pipeline_runs"
    assert spec.backend == TaskBackend.CELERY
    assert spec.category == BackgroundTaskCategory.SCHEDULED_JOB
    assert spec.business_type == "pipeline_run"
    assert spec.profile_key == "pipeline_run_recovery"
    assert spec.task_name == TaskName.RECOVER_EXPIRED_PIPELINE_RUNS
    assert spec.default_reason == "scheduled_pipeline_run_recovery"


def test_wrap_background_callable_preserves_result_and_logs(caplog) -> None:
    def target(value: int) -> int:
        return value + 1

    wrapped = wrap_background_callable(
        profile_key="evaluation_compare_background",
        category=BackgroundTaskCategory.BEST_EFFORT_HOOK,
        target=target,
        business_id=9,
    )

    with caplog.at_level(logging.INFO):
        assert wrapped(3) == 4

    assert "background_task_started key=evaluation_compare_background" in caplog.text
    assert "background_task_finished key=evaluation_compare_background" in caplog.text


def test_wrap_background_callable_reraises_failure(caplog) -> None:
    def target() -> None:
        raise ValueError("bad task")

    wrapped = wrap_background_callable(
        profile_key="evaluation_compare_background",
        category=BackgroundTaskCategory.BEST_EFFORT_HOOK,
        target=target,
        business_id=9,
    )

    with caplog.at_level(logging.INFO), pytest.raises(ValueError, match="bad task"):
        wrapped()

    assert "background_task_failed key=evaluation_compare_background" in caplog.text


def test_create_process_local_worker_thread_wraps_target() -> None:
    seen: list[int] = []

    def target(value: int) -> None:
        seen.append(value)

    thread = create_process_local_worker_thread(
        profile_key="pipeline_run_worker",
        target=target,
        args=(12,),
        name="pipeline-run-test",
        business_id=12,
    )

    assert thread.name == "pipeline-run-test"
    assert thread.daemon is True
    thread.start()
    thread.join(timeout=5)
    assert seen == [12]


def test_run_governed_threadpool_call_returns_target_result() -> None:
    result = run_governed_threadpool_call(
        profile_key="snapshot_sync_retry_threadpool",
        target=lambda value: value + 1,
        args=(4,),
        max_workers=1,
        thread_name_prefix="governed-call-test",
    )

    assert result == 5


def test_run_governed_threadpool_map_preserves_order_and_captures_failures() -> None:
    def worker(value: int) -> int:
        if value == 2:
            raise ValueError("bad item")
        return value * 10

    results = run_governed_threadpool_map(
        profile_key="pipeline_agent_executor_threadpool",
        items=[3, 2, 1],
        worker=worker,
        max_workers=2,
        thread_name_prefix="governed-map-test",
    )

    assert [item.index for item in results] == [0, 1, 2]
    assert [item.item for item in results] == [3, 2, 1]
    assert results[0].result == 30
    assert isinstance(results[1].exception, ValueError)
    assert results[2].result == 10


def test_iter_governed_threadpool_map_emits_heartbeat_while_worker_is_pending() -> None:
    release = threading.Event()
    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        updates = list(
            iter_governed_threadpool_map(
                profile_key="test_generation_coverage_shard_threadpool",
                items=[1],
                worker=lambda value: (release.wait(1), value)[1],
                max_workers=1,
                heartbeat_interval_seconds=0.005,
            )
        )
    finally:
        release.set()
        timer.cancel()

    assert updates[0].kind == "heartbeat"
    assert updates[0].completed_count == 0
    assert updates[-1].kind == "item"
    assert updates[-1].completed_count == 1
    assert updates[-1].item_result is not None
    assert updates[-1].item_result.result == 1


def test_add_fastapi_background_task_wraps_arguments() -> None:
    calls: list[dict] = []

    def target(*, comparison_id: int, status: str) -> None:
        calls.append({"comparison_id": comparison_id, "status": status})

    background_tasks = FakeBackgroundTasks()

    add_fastapi_background_task(
        background_tasks,
        profile_key="evaluation_compare_background",
        target=target,
        business_id=55,
        kwargs={"comparison_id": 55, "status": "running"},
    )

    assert len(background_tasks.calls) == 1
    wrapped, args, kwargs = background_tasks.calls[0]
    assert args == ()
    assert kwargs == {"comparison_id": 55, "status": "running"}

    wrapped(*args, **kwargs)
    assert calls == [{"comparison_id": 55, "status": "running"}]


def test_submit_background_task_uses_kind_spec(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_enqueue_task(task_name, *, kwargs, business_type, business_id, reason, queue):
        calls.append(
            {
                "task_name": task_name,
                "kwargs": kwargs,
                "business_type": business_type,
                "business_id": business_id,
                "reason": reason,
                "queue": queue,
            }
        )
        return TaskDispatchResult(task_id="task-7", task_name=task_name.value)

    monkeypatch.setattr(
        "modules.orchestration.task_dispatcher.enqueue_task",
        fake_enqueue_task,
    )

    result = submit_background_task(
        BackgroundTaskKind.KNOWLEDGE_DOCUMENT_PARSE,
        kwargs={"document_id": 7},
        business_id=7,
    )

    assert result.id == "task-7"
    assert calls == [
        {
            "task_name": TaskName.PARSE_KNOWLEDGE_DOCUMENT,
            "kwargs": {"document_id": 7},
            "business_type": "knowledge_document",
            "business_id": 7,
            "reason": "offline_parse",
            "queue": "celery",
        }
    ]


def test_submit_background_task_supports_test_case_compare(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_enqueue_task(task_name, *, kwargs, business_type, business_id, reason, queue):
        calls.append(
            {
                "task_name": task_name,
                "kwargs": kwargs,
                "business_type": business_type,
                "business_id": business_id,
                "reason": reason,
                "queue": queue,
            }
        )
        return TaskDispatchResult(task_id="compare-task-9", task_name=task_name.value)

    monkeypatch.setattr(
        "modules.orchestration.task_dispatcher.enqueue_task",
        fake_enqueue_task,
    )

    result = submit_background_task(
        BackgroundTaskKind.TEST_CASE_COMPARE,
        kwargs={"comparison_id": 9},
        business_id=9,
    )

    assert result.id == "compare-task-9"
    assert calls == [
        {
            "task_name": TaskName.COMPARE_TEST_CASES,
            "kwargs": {"comparison_id": 9},
            "business_type": "test_case_compare",
            "business_id": 9,
            "reason": "evaluation_compare_background",
            "queue": "celery",
        }
    ]


def test_submit_background_task_supports_rag_eval_run(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_enqueue_task(task_name, *, kwargs, business_type, business_id, reason, queue):
        calls.append(
            {
                "task_name": task_name,
                "kwargs": kwargs,
                "business_type": business_type,
                "business_id": business_id,
                "reason": reason,
                "queue": queue,
            }
        )
        return TaskDispatchResult(task_id="rag-eval-task-11", task_name=task_name.value)

    monkeypatch.setattr(
        "modules.orchestration.task_dispatcher.enqueue_task",
        fake_enqueue_task,
    )

    result = submit_background_task(
        BackgroundTaskKind.RAG_EVAL_RUN,
        kwargs={"run_id": 11, "user_id": 5},
        business_id=11,
    )

    assert result.id == "rag-eval-task-11"
    assert calls == [
        {
            "task_name": TaskName.RUN_RAG_EVAL,
            "kwargs": {"run_id": 11, "user_id": 5},
            "business_type": "rag_eval_run",
            "business_id": 11,
            "reason": "rag_eval_run_worker",
            "queue": "celery",
        }
    ]


def test_submit_background_task_supports_pipeline_run(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_enqueue_task(task_name, *, kwargs, business_type, business_id, reason, queue):
        calls.append(
            {
                "task_name": task_name,
                "kwargs": kwargs,
                "business_type": business_type,
                "business_id": business_id,
                "reason": reason,
                "queue": queue,
            }
        )
        return TaskDispatchResult(task_id="pipeline-task-13", task_name=task_name.value)

    monkeypatch.setattr(
        "modules.orchestration.task_dispatcher.enqueue_task",
        fake_enqueue_task,
    )

    result = submit_background_task(
        BackgroundTaskKind.PIPELINE_RUN,
        kwargs={"run_id": 13, "start_stage": "test_generation"},
        business_id=13,
    )

    assert result.id == "pipeline-task-13"
    assert calls == [
        {
            "task_name": TaskName.RUN_PIPELINE,
            "kwargs": {"run_id": 13, "start_stage": "test_generation"},
            "business_type": "pipeline_run",
            "business_id": 13,
            "reason": "pipeline_run_worker",
            "queue": "celery",
        }
    ]


def test_build_background_task_status_uses_kind_business_type() -> None:
    status = build_background_task_status(
        BackgroundTaskKind.TEST_CASE_COMPARE,
        task_id=None,
        business_id=42,
        business_status="running",
    )

    assert status["status"] == "NOT_QUEUED"
    assert status["queue"] == "celery"
    assert status["business_type"] == "test_case_compare"
    assert status["business_id"] == 42
    assert status["business_status"] == "running"
