from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from modules.agent_platform import recovery


class _ScalarResult:
    def all(self) -> list[int]:
        return [27]


class _FakeDb:
    def __init__(self) -> None:
        self.statement = None
        self.run = SimpleNamespace(
            id=27,
            status="running",
            task_id="old-task",
            claim_token="old-claim",
            heartbeat_at=object(),
            lease_expires_at=object(),
            error_message="旧错误",
            run_context={},
            finished_at=None,
        )
        self.commit_count = 0
        self.closed = False

    def scalars(self, statement):
        self.statement = statement
        return _ScalarResult()

    def get(self, model, run_id: int):
        assert model is recovery.AgentRun
        assert run_id == 27
        return self.run

    def add(self, value) -> None:
        assert value is self.run

    def commit(self) -> None:
        self.commit_count += 1

    def close(self) -> None:
        self.closed = True


class _FakeRepository:
    def __init__(self, db: _FakeDb) -> None:
        self.db = db
        self.events: list[dict[str, object]] = []

    def append_event(self, **event) -> None:
        self.events.append(event)


def test_recovery_orders_only_run_ids_before_loading_large_json_rows(monkeypatch) -> None:
    db = _FakeDb()
    repository = _FakeRepository(db)
    dispatched: list[int] = []
    monkeypatch.setattr(recovery, "SessionLocal", lambda: db)
    monkeypatch.setattr(recovery, "AgentPlatformRepository", lambda current_db: repository)
    monkeypatch.setattr(recovery, "start_agent_run_worker", dispatched.append)

    result = recovery.recover_expired_agent_runs(limit=20)

    assert list(db.statement.selected_columns.keys()) == ["id"]
    assert result == {
        "status": "completed",
        "recovered_run_ids": [27],
        "expired_run_ids": [],
    }
    assert db.run.status == "pending"
    assert db.run.task_id is None
    assert db.run.claim_token is None
    assert db.run.heartbeat_at is None
    assert db.run.lease_expires_at is None
    assert db.run.error_message == ""
    assert repository.events == [
        {
            "run_id": 27,
            "event_type": "run_recovered",
            "payload": {"reason": "expired_lease"},
        }
    ]
    assert db.commit_count == 1
    assert dispatched == [27]
    assert db.closed is True


def test_recovery_marks_run_failed_when_global_deadline_has_expired(monkeypatch) -> None:
    db = _FakeDb()
    db.run.run_context = {
        "deadline_at": (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    }
    repository = _FakeRepository(db)
    dispatched: list[int] = []
    pruned_run_ids: list[int] = []
    monkeypatch.setattr(recovery, "SessionLocal", lambda: db)
    monkeypatch.setattr(recovery, "AgentPlatformRepository", lambda current_db: repository)
    monkeypatch.setattr(recovery, "start_agent_run_worker", dispatched.append)
    monkeypatch.setattr(
        recovery,
        "prune_terminal_run_history",
        lambda current_repo, run: pruned_run_ids.append(run.id),
    )

    result = recovery.recover_expired_agent_runs(limit=20)

    assert result == {
        "status": "completed",
        "recovered_run_ids": [],
        "expired_run_ids": [27],
    }
    assert db.run.status == "failed"
    assert db.run.finished_at is not None
    assert "超过总执行时限" in db.run.error_message
    assert repository.events == [
        {
            "run_id": 27,
            "event_type": "run_recovery_deadline_expired",
            "payload": {"reason": "deadline_expired"},
        }
    ]
    assert pruned_run_ids == [27]
    assert dispatched == []
