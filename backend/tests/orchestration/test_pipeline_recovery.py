from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db.models import PipelineRun
from modules.orchestration_components.pipeline_runtime import recovery


def _make_pipeline_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PipelineRun.__table__.create(bind=engine)
    return sessionmaker(bind=engine)


def test_recover_expired_pipeline_runs_requeues_only_expired_running(monkeypatch) -> None:
    TestingSessionLocal = _make_pipeline_session()
    now = datetime.utcnow()
    db = TestingSessionLocal()
    db.add_all(
        [
            PipelineRun(
                id=61,
                user_id=1,
                project_id=2,
                status="running",
                current_stage="evaluation",
                task_id="expired-task",
                claim_token="expired-task",
                heartbeat_at=now - timedelta(minutes=20),
                lease_expires_at=now - timedelta(seconds=1),
                request_payload={},
                stage_states={},
                artifacts={},
            ),
            PipelineRun(
                id=62,
                user_id=1,
                project_id=2,
                status="running",
                current_stage="evaluation",
                task_id="active-task",
                claim_token="active-task",
                heartbeat_at=now,
                lease_expires_at=now + timedelta(minutes=10),
                request_payload={},
                stage_states={},
                artifacts={},
            ),
            PipelineRun(
                id=63,
                user_id=1,
                project_id=2,
                status="pending",
                current_stage="evaluation",
                request_payload={},
                stage_states={},
                artifacts={},
            ),
        ]
    )
    db.commit()
    db.close()
    calls: list[tuple[int, str]] = []

    monkeypatch.setattr(recovery, "ensure_pipeline_table", lambda: None)
    monkeypatch.setattr(recovery, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        recovery,
        "start_pipeline_worker",
        lambda run_id, stage: calls.append((run_id, stage)),
    )

    report = recovery.recover_expired_pipeline_runs(limit=10)

    assert report["checked"] == 1
    assert report["requeued"] == 1
    assert report["skipped"] == 0
    assert report["failed"] == 0
    assert report["run_ids"] == [61]
    assert calls == [(61, "evaluation")]

    verify_db = TestingSessionLocal()
    try:
        expired = verify_db.query(PipelineRun).filter(PipelineRun.id == 61).one()
        active = verify_db.query(PipelineRun).filter(PipelineRun.id == 62).one()
        pending = verify_db.query(PipelineRun).filter(PipelineRun.id == 63).one()

        assert expired.status == "pending"
        assert expired.task_id is None
        assert expired.claim_token is None
        assert expired.heartbeat_at is None
        assert expired.lease_expires_at is None
        assert expired.error_message == "requeued_after_expired_pipeline_lease"
        assert active.status == "running"
        assert active.task_id == "active-task"
        assert pending.status == "pending"
    finally:
        verify_db.close()


def test_recover_expired_pipeline_runs_skips_invalid_stage(monkeypatch) -> None:
    TestingSessionLocal = _make_pipeline_session()
    now = datetime.utcnow()
    db = TestingSessionLocal()
    db.add(
        PipelineRun(
            id=64,
            user_id=1,
            project_id=2,
            status="running",
            current_stage="unknown_stage",
            heartbeat_at=now - timedelta(minutes=20),
            lease_expires_at=now - timedelta(seconds=1),
            request_payload={},
            stage_states={},
            artifacts={},
        )
    )
    db.commit()
    db.close()
    calls: list[tuple[int, str]] = []

    monkeypatch.setattr(recovery, "ensure_pipeline_table", lambda: None)
    monkeypatch.setattr(recovery, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        recovery,
        "start_pipeline_worker",
        lambda run_id, stage: calls.append((run_id, stage)),
    )

    report = recovery.recover_expired_pipeline_runs(limit=10)

    assert report["checked"] == 1
    assert report["requeued"] == 0
    assert report["skipped"] == 1
    assert calls == []
