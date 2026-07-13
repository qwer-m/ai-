from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db.models import PipelineRun
from modules.orchestration.background_task_governance import BackgroundTaskKind
from modules.orchestration.task_dispatcher import TaskDispatchResult
from modules.orchestration_components.pipeline_runtime import dispatcher, support
from modules.orchestration_components.pipeline_runtime import runner
from modules.orchestration.task_names import TaskName


def _make_pipeline_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PipelineRun.__table__.create(bind=engine)
    return sessionmaker(bind=engine)


def test_start_pipeline_worker_dispatches_pipeline_run_to_celery(monkeypatch) -> None:
    calls: list[dict] = []
    fake_db = SimpleNamespace(close=lambda: None)

    def fake_submit_background_task(kind, *, kwargs, business_id):
        calls.append({"kind": kind, "kwargs": kwargs, "business_id": business_id})
        return TaskDispatchResult(
            task_id="pipeline-task-33",
            task_name=TaskName.RUN_PIPELINE.value,
        )

    class FakeRepository:
        def __init__(self, db):
            assert db is fake_db

        def get_run(self, *, run_id: int):
            assert run_id == 33
            return None

    monkeypatch.setattr(dispatcher, "ensure_pipeline_table", lambda: None)
    monkeypatch.setattr(dispatcher, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(dispatcher, "PipelineRuntimeRepository", FakeRepository)
    monkeypatch.setattr(dispatcher, "submit_background_task", fake_submit_background_task)

    result = dispatcher.start_pipeline_worker(33, "test_generation")

    assert result.id == "pipeline-task-33"
    assert calls == [
        {
            "kind": BackgroundTaskKind.PIPELINE_RUN,
            "kwargs": {"run_id": 33, "start_stage": "test_generation"},
            "business_id": 33,
        }
    ]


def test_start_pipeline_worker_records_task_id_on_pipeline_run(monkeypatch) -> None:
    TestingSessionLocal = _make_pipeline_session()
    db = TestingSessionLocal()
    db.add(
        PipelineRun(
            id=34,
            user_id=1,
            project_id=2,
            status="pending",
            current_stage="test_generation",
            request_payload={},
            stage_states={},
            artifacts={},
        )
    )
    db.commit()
    db.close()

    def fake_submit_background_task(*args, **kwargs):
        return TaskDispatchResult(
            task_id="pipeline-task-34",
            task_name=TaskName.RUN_PIPELINE.value,
        )

    monkeypatch.setattr(dispatcher, "ensure_pipeline_table", lambda: None)
    monkeypatch.setattr(dispatcher, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(dispatcher, "submit_background_task", fake_submit_background_task)

    dispatcher.start_pipeline_worker(34, "test_generation")

    verify_db = TestingSessionLocal()
    try:
        run = verify_db.query(PipelineRun).filter(PipelineRun.id == 34).one()
        assert run.task_id == "pipeline-task-34"
        assert run.claim_token is None
        assert run.heartbeat_at is None
        assert run.lease_expires_at is None
    finally:
        verify_db.close()


def test_start_pipeline_worker_marks_run_failed_when_dispatch_fails(monkeypatch) -> None:
    TestingSessionLocal = _make_pipeline_session()

    db = TestingSessionLocal()
    db.add(
        PipelineRun(
            id=44,
            user_id=1,
            project_id=2,
            status="pending",
            current_stage="evaluation",
            request_payload={},
            stage_states={},
            artifacts={},
        )
    )
    db.commit()
    db.close()

    def fake_submit_background_task(*args, **kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(dispatcher, "ensure_pipeline_table", lambda: None)
    monkeypatch.setattr(dispatcher, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(dispatcher, "submit_background_task", fake_submit_background_task)

    with pytest.raises(RuntimeError, match="queue unavailable"):
        dispatcher.start_pipeline_worker(44, "evaluation")

    verify_db = TestingSessionLocal()
    try:
        run = verify_db.query(PipelineRun).filter(PipelineRun.id == 44).one()
        assert run.status == "failed"
        assert run.current_stage == "evaluation"
        assert run.error_message == "failed_to_queue_pipeline_run:queue unavailable"
        assert isinstance(run.finished_at, datetime)
        assert run.claim_token is None
        assert run.heartbeat_at is None
        assert run.lease_expires_at is None
    finally:
        verify_db.close()


def test_claim_pending_run_writes_worker_lease() -> None:
    TestingSessionLocal = _make_pipeline_session()
    db = TestingSessionLocal()
    db.add(
        PipelineRun(
            id=45,
            user_id=1,
            project_id=2,
            status="pending",
            current_stage="test_generation",
            request_payload={},
            stage_states={},
            artifacts={},
        )
    )
    db.commit()

    claimed = runner.claim_pending_run(
        db,
        run_id=45,
        start_stage="test_generation",
        task_id="pipeline-task-45",
        lease_seconds=120,
    )

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.task_id == "pipeline-task-45"
    assert claimed.claim_token == "pipeline-task-45"
    assert isinstance(claimed.heartbeat_at, datetime)
    assert isinstance(claimed.lease_expires_at, datetime)
    assert claimed.lease_expires_at > claimed.heartbeat_at
    db.close()


def test_claim_pending_run_recovers_expired_running_lease() -> None:
    TestingSessionLocal = _make_pipeline_session()
    db = TestingSessionLocal()
    db.add(
        PipelineRun(
            id=46,
            user_id=1,
            project_id=2,
            status="running",
            current_stage="evaluation",
            task_id="old-task",
            claim_token="old-task",
            heartbeat_at=datetime.utcnow() - timedelta(minutes=10),
            lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
            request_payload={},
            stage_states={},
            artifacts={},
        )
    )
    db.commit()

    claimed = runner.claim_pending_run(
        db,
        run_id=46,
        start_stage="evaluation",
        task_id="new-task",
        lease_seconds=120,
    )

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.task_id == "new-task"
    assert claimed.claim_token == "new-task"
    assert claimed.lease_expires_at > datetime.utcnow()
    db.close()


def test_claim_pending_run_skips_running_with_active_lease() -> None:
    TestingSessionLocal = _make_pipeline_session()
    db = TestingSessionLocal()
    db.add(
        PipelineRun(
            id=47,
            user_id=1,
            project_id=2,
            status="running",
            current_stage="evaluation",
            task_id="active-task",
            claim_token="active-task",
            heartbeat_at=datetime.utcnow(),
            lease_expires_at=datetime.utcnow() + timedelta(minutes=10),
            request_payload={},
            stage_states={},
            artifacts={},
        )
    )
    db.commit()

    claimed = runner.claim_pending_run(
        db,
        run_id=47,
        start_stage="evaluation",
        task_id="new-task",
        lease_seconds=120,
    )

    assert claimed is None
    run = db.query(PipelineRun).filter(PipelineRun.id == 47).one()
    assert run.task_id == "active-task"
    assert run.claim_token == "active-task"
    db.close()


def test_claim_pending_run_allows_same_task_redelivery_with_active_lease() -> None:
    TestingSessionLocal = _make_pipeline_session()
    db = TestingSessionLocal()
    db.add(
        PipelineRun(
            id=48,
            user_id=1,
            project_id=2,
            status="running",
            current_stage="evaluation",
            task_id="redelivered-task",
            claim_token="redelivered-task",
            heartbeat_at=datetime.utcnow(),
            lease_expires_at=datetime.utcnow() + timedelta(minutes=10),
            request_payload={},
            stage_states={},
            artifacts={},
        )
    )
    db.commit()

    claimed = runner.claim_pending_run(
        db,
        run_id=48,
        start_stage="evaluation",
        task_id="redelivered-task",
        lease_seconds=120,
    )

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.task_id == "redelivered-task"
    assert claimed.claim_token == "redelivered-task"
    assert claimed.lease_expires_at > datetime.utcnow()
    db.close()


def test_persist_run_can_clear_nullable_terminal_fields() -> None:
    calls: list[str] = []
    run = SimpleNamespace(
        status="running",
        current_stage="evaluation",
        finished_at=datetime(2026, 1, 1),
        task_id="task-1",
        claim_token="claim-1",
        heartbeat_at=datetime(2026, 1, 1),
        lease_expires_at=datetime(2026, 1, 1),
    )
    db = SimpleNamespace(
        add=lambda item: calls.append("add"),
        commit=lambda: calls.append("commit"),
        refresh=lambda item: calls.append("refresh"),
    )

    support._persist_run(
        db,
        run,
        status="success",
        current_stage=None,
        finished_at=None,
        claim_token=None,
        heartbeat_at=None,
        lease_expires_at=None,
    )

    assert run.status == "success"
    assert run.current_stage is None
    assert run.finished_at is None
    assert run.claim_token is None
    assert run.heartbeat_at is None
    assert run.lease_expires_at is None
    assert calls == ["add", "commit", "refresh"]
