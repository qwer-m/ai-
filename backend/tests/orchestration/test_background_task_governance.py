from __future__ import annotations

from modules.orchestration.background_task_governance import (
    BackgroundTaskCategory,
    BackgroundTaskKind,
    TaskBackend,
    build_background_task_status,
    get_background_task_spec,
    get_background_task_profile,
    list_background_task_specs,
    list_background_task_profiles,
    submit_background_task,
)
from modules.orchestration.task_dispatcher import TaskDispatchResult
from modules.orchestration.task_names import TaskName


def test_background_task_profiles_are_classified() -> None:
    profile = get_background_task_profile("rag_eval_run_worker")

    assert profile.category == BackgroundTaskCategory.DURABLE_JOB
    assert profile.user_visible is True
    assert profile.durable is True
    assert profile.status_source == "rag_eval_runs"

    agent_profile = get_background_task_profile("agent_run_worker")
    assert agent_profile.category == BackgroundTaskCategory.DURABLE_JOB
    assert agent_profile.durable is True
    assert agent_profile.user_visible is True
    assert agent_profile.status_source == "agent_runs"
    assert agent_profile.recommended_runtime == "celery"

    durable = list_background_task_profiles(BackgroundTaskCategory.DURABLE_JOB)
    assert {item.key for item in durable} >= {
        "celery_knowledge_parse",
        "rag_eval_run_worker",
    }


def test_background_task_specs_cover_celery_and_migration_candidates() -> None:
    celery_specs = list_background_task_specs(TaskBackend.CELERY)

    assert {spec.task_name for spec in celery_specs} == {
        TaskName.PARSE_KNOWLEDGE_DOCUMENT,
        TaskName.AUDIT_KNOWLEDGE_INDEX_CONSISTENCY,
        TaskName.RUN_RAG_EVAL,
        TaskName.RUN_AGENT_WORKFLOW,
        TaskName.RECOVER_EXPIRED_AGENT_RUNS,
        TaskName.CLEANUP_LOGS,
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


def test_agent_run_spec_is_durable_celery_job() -> None:
    spec = get_background_task_spec(BackgroundTaskKind.AGENT_RUN)

    assert spec.backend == TaskBackend.CELERY
    assert spec.category == BackgroundTaskCategory.DURABLE_JOB
    assert spec.business_type == "agent_run"
    assert spec.profile_key == "agent_run_worker"
    assert spec.task_name == TaskName.RUN_AGENT_WORKFLOW
    assert spec.default_reason == "agent_run_worker"


def test_agent_run_recovery_spec_is_scheduled_celery_job() -> None:
    profile = get_background_task_profile("agent_run_recovery")
    spec = get_background_task_spec(BackgroundTaskKind.AGENT_RUN_RECOVERY)

    assert profile.category == BackgroundTaskCategory.SCHEDULED_JOB
    assert profile.status_source == "agent_runs"
    assert spec.backend == TaskBackend.CELERY
    assert spec.category == BackgroundTaskCategory.SCHEDULED_JOB
    assert spec.business_type == "agent_run"
    assert spec.profile_key == "agent_run_recovery"
    assert spec.task_name == TaskName.RECOVER_EXPIRED_AGENT_RUNS
    assert spec.default_reason == "scheduled_agent_run_recovery"


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

    assert result.task_id == "task-7"
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

    assert result.task_id == "rag-eval-task-11"
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


def test_submit_background_task_supports_agent_run(monkeypatch) -> None:
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
        return TaskDispatchResult(task_id="agent-task-13", task_name=task_name.value)

    monkeypatch.setattr(
        "modules.orchestration.task_dispatcher.enqueue_task",
        fake_enqueue_task,
    )

    result = submit_background_task(
        BackgroundTaskKind.AGENT_RUN,
        kwargs={"run_id": 13},
        business_id=13,
    )

    assert result.task_id == "agent-task-13"
    assert calls == [
        {
            "task_name": TaskName.RUN_AGENT_WORKFLOW,
            "kwargs": {"run_id": 13},
            "business_type": "agent_run",
            "business_id": 13,
            "reason": "agent_run_worker",
            "queue": "celery",
        }
    ]


def test_build_background_task_status_uses_kind_business_type() -> None:
    status = build_background_task_status(
        BackgroundTaskKind.AGENT_RUN,
        task_id=None,
        business_id=42,
        business_status="running",
    )

    assert status["status"] == "NOT_QUEUED"
    assert status["queue"] == "celery"
    assert status["business_type"] == "agent_run"
    assert status["business_id"] == 42
    assert status["business_status"] == "running"
