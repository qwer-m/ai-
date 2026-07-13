from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db.models import PipelineRun, Project, User
from modules.orchestration_components.services import pipeline_run_service
from modules.orchestration_components.services.pipeline_run_service import PipelineRunService


def _make_service_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(bind=engine)
    Project.__table__.create(bind=engine)
    PipelineRun.__table__.create(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    db = TestingSessionLocal()
    db.add(User(id=1, username="u1", email="u1@example.com", hashed_password="pw"))
    db.add(Project(id=2, user_id=1, name="p1"))
    db.commit()
    return TestingSessionLocal


def test_resume_run_requeues_expired_running_lease(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_run_service, "ensure_pipeline_table", lambda: None)
    TestingSessionLocal = _make_service_session()
    db = TestingSessionLocal()
    db.add(
        PipelineRun(
            id=51,
            user_id=1,
            project_id=2,
            status="running",
            current_stage="api_automation",
            task_id="old-task",
            claim_token="old-task",
            heartbeat_at=datetime.utcnow() - timedelta(minutes=30),
            lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
            request_payload={},
            stage_states={
                "test_generation": {"status": "success"},
                "ui_automation": {"status": "success"},
                "api_automation": {"status": "running"},
                "evaluation": {"status": "idle"},
            },
            artifacts={},
        )
    )
    db.commit()
    calls: list[tuple[int, str]] = []

    run, status = PipelineRunService(db, lambda run_id, stage: calls.append((run_id, stage))).resume_run(
        run_id=51,
        user_id=1,
    )

    assert run is not None
    assert status == "resumed:api_automation"
    assert calls == [(51, "api_automation")]
    assert run.status == "pending"
    assert run.current_stage == "api_automation"
    assert run.task_id is None
    assert run.claim_token is None
    assert run.heartbeat_at is None
    assert run.lease_expires_at is None
    db.close()


def test_resume_run_keeps_active_running_lease(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_run_service, "ensure_pipeline_table", lambda: None)
    TestingSessionLocal = _make_service_session()
    db = TestingSessionLocal()
    db.add(
        PipelineRun(
            id=52,
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
    calls: list[tuple[int, str]] = []

    run, status = PipelineRunService(db, lambda run_id, stage: calls.append((run_id, stage))).resume_run(
        run_id=52,
        user_id=1,
    )

    assert run is not None
    assert status == "already_running"
    assert calls == []
    assert run.status == "running"
    assert run.task_id == "active-task"
    assert run.claim_token == "active-task"
    db.close()
