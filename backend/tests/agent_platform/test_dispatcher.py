"""只读取得真实投递记录，在脱离会话的 ORM 副本上验证任务与认领身份边界。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from core.db.database import engine
from core.db.model_defs import AgentApproval, AgentNodeRun, AgentRun, AgentRunDispatch, AgentRunEvent
from modules.agent_platform import dispatcher
from modules.agent_platform.dispatcher import (
    _can_record_dispatched_task,
)
from modules.agent_platform.lifecycle import TERMINAL_RUN_STATUSES, new_claim_token


def _copy_run(run: AgentRun) -> AgentRun:
    """复制真实 ORM 列，测试中的生命周期变化不关联线上数据库会话。"""
    return AgentRun(**{
        column.key: deepcopy(getattr(run, column.key))
        for column in AgentRun.__table__.columns
    })


@pytest.fixture(scope="module")
def dispatched_runs() -> tuple[AgentRun, AgentRun]:
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            # 先读取小字段并排序，避免让数据库排序完整产物 JSON。
            identities = connection.execute(select(AgentRun.id, AgentRun.task_id).where(
                AgentRun.task_id.is_not(None), AgentRun.task_id != "",
            ).order_by(AgentRun.id.desc())).all()
            selected_ids = []
            task_ids = set()
            for run_id, task_id in identities:
                if task_id not in task_ids:
                    selected_ids.append(run_id)
                    task_ids.add(task_id)
                if len(selected_ids) == 2:
                    break
            if len(selected_ids) < 2:
                pytest.skip("真实数据库缺少两条具有不同 task_id 的 Agent Run，不能验证跨任务隔离")
            records = connection.execute(select(AgentRun.__table__).where(
                AgentRun.id.in_(selected_ids),
            )).mappings().all()
    except OperationalError as error:
        pytest.skip(f"真实数据库当前不可读取，未使用替代数据：{type(error).__name__}")
    by_id = {record["id"]: AgentRun(**deepcopy(dict(record))) for record in records}
    if len(by_id) != 2:
        pytest.skip("读取期间真实投递记录已不存在，未构造替代记录")
    return by_id[selected_ids[0]], by_id[selected_ids[1]]


def test_dispatch_result_does_not_replace_another_executor_claim(dispatched_runs) -> None:
    source, other = dispatched_runs
    assert source.task_id != other.task_id
    run = _copy_run(source)
    run.status, run.task_id, run.claim_token = "pending", None, None
    assert not _can_record_dispatched_task(run, source.task_id)

    run.claim_token = new_claim_token()
    assert run.claim_token != source.task_id
    assert not _can_record_dispatched_task(run, source.task_id)
    run.status, run.task_id, run.claim_token = "pending", source.task_id, None
    assert _can_record_dispatched_task(run, source.task_id)
    run.status, run.task_id = "running", source.task_id
    assert _can_record_dispatched_task(run, source.task_id)
    assert not _can_record_dispatched_task(run, other.task_id)
    run.task_id = None
    assert not _can_record_dispatched_task(run, source.task_id)

    run.task_id = source.task_id
    for status in TERMINAL_RUN_STATUSES:
        run.status = status
        assert not _can_record_dispatched_task(run, source.task_id), status


@pytest.fixture
def delivery_database(dispatched_runs, monkeypatch):
    """只在内存库扰动真实 Run 的副本，不创建线上任务或接触真实 broker。"""
    local_engine = create_engine("sqlite:///:memory:")
    for table in (AgentRun.__table__, AgentRunDispatch.__table__, AgentRunEvent.__table__,
                  AgentApproval.__table__, AgentNodeRun.__table__):
        table.create(local_engine)
    factory = sessionmaker(bind=local_engine, expire_on_commit=False)
    source, _ = dispatched_runs
    run = _copy_run(source)
    run.status, run.task_id, run.claim_token = "pending", None, None
    run.started_at = run.finished_at = run.heartbeat_at = run.lease_expires_at = None
    with factory() as db:
        db.add(run)
        db.commit()
    monkeypatch.setattr(dispatcher, "SessionLocal", factory)

    def forbidden_dispatch(*args, **kwargs):
        raise AssertionError("离线状态测试禁止真实队列投递")

    monkeypatch.setattr(dispatcher, "submit_background_task", forbidden_dispatch)
    yield factory, source.id
    local_engine.dispose()


def _request_delivery(factory, run_id):
    with factory() as db:
        run = db.get(AgentRun, run_id)
        entry = dispatcher.prepare_agent_run_dispatch(db, run, reason="offline_state_verification")
        db.commit()
        return entry.task_id


def _make_due(factory, run_id):
    with factory() as db:
        entry = db.query(AgentRunDispatch).filter_by(run_id=run_id).order_by(AgentRunDispatch.generation.desc()).first()
        entry.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        entry.publish_lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()


def test_pending_without_explicit_intent_is_never_auto_dispatched(delivery_database):
    _, run_id = delivery_database
    request, result = dispatcher._claim_delivery(run_id)
    assert request is None
    assert not result.queued and result.reason == "dispatch_not_requested"
    assert dispatcher.recover_pending_agent_dispatches() == []


def test_pending_and_intent_rollback_together_and_intent_is_idempotent(delivery_database):
    factory, run_id = delivery_database
    with factory() as db:
        run = db.get(AgentRun, run_id)
        first = dispatcher.prepare_agent_run_dispatch(db, run, reason="offline_state_verification")
        second = dispatcher.prepare_agent_run_dispatch(db, run, reason="offline_state_verification")
        assert first.id == second.id and first.task_id == second.task_id
        assert run.task_id == first.task_id
        db.rollback()
    with factory() as db:
        assert db.query(AgentRunDispatch).count() == 0
        assert db.get(AgentRun, run_id).task_id is None


def test_response_loss_retries_same_task_identity_and_stops_at_limit(delivery_database, monkeypatch):
    factory, run_id = delivery_database
    monkeypatch.setattr(dispatcher.settings, "AGENT_RUN_DISPATCH_MAX_ATTEMPTS", 2)
    task_id = _request_delivery(factory, run_id)
    first, _ = dispatcher._claim_delivery(run_id)
    assert first["task_id"] == task_id
    result = dispatcher._complete_delivery(first, error=TimeoutError())
    assert not result.queued and result.reason == "dispatch_deferred"
    _make_due(factory, run_id)
    second, _ = dispatcher._claim_delivery(run_id)
    assert second["task_id"] == task_id
    assert second["publish_token"] != first["publish_token"]
    result = dispatcher._complete_delivery(second, error=TimeoutError())
    assert result.reason == "dispatch_exhausted"
    with factory() as db:
        assert db.get(AgentRun, run_id).status == "failed"
        entry = db.query(AgentRunDispatch).one()
        assert entry.status == "exhausted" and entry.attempt_count == 2
    third, result = dispatcher._claim_delivery(run_id)
    assert third is None and not result.queued


def test_broker_ack_does_not_mean_worker_claim_and_redelivery_is_bounded(delivery_database, monkeypatch):
    factory, run_id = delivery_database
    monkeypatch.setattr(dispatcher.settings, "AGENT_RUN_DISPATCH_MAX_ATTEMPTS", 1)
    task_id = _request_delivery(factory, run_id)
    request, _ = dispatcher._claim_delivery(run_id)
    confirmed = dispatcher._complete_delivery(request, error=None)
    assert confirmed.queued and confirmed.task_id == task_id
    pending, waiting = dispatcher._claim_delivery(run_id)
    assert pending is None and waiting.reason == "dispatch_waiting"
    with factory() as db:
        assert db.get(AgentRun, run_id).status == "pending"
    _make_due(factory, run_id)
    request, exhausted = dispatcher._claim_delivery(run_id)
    assert request is None and exhausted.reason == "dispatch_exhausted"


def test_publish_crash_lease_uses_same_identity_and_rejects_late_receipt(delivery_database):
    factory, run_id = delivery_database
    _request_delivery(factory, run_id)
    first, _ = dispatcher._claim_delivery(run_id)
    active, result = dispatcher._claim_delivery(run_id)
    assert active is None and result.reason in {"dispatch_waiting", "dispatch_in_progress"}
    _make_due(factory, run_id)
    second, _ = dispatcher._claim_delivery(run_id)
    assert second["task_id"] == first["task_id"]
    result = dispatcher._complete_delivery(first, error=None)
    assert result.reason == "stale_dispatch_receipt"
    assert dispatcher._complete_delivery(second, error=None).queued


def test_worker_claim_before_lost_ack_stops_outbox_retries(delivery_database):
    factory, run_id = delivery_database
    _request_delivery(factory, run_id)
    request, _ = dispatcher._claim_delivery(run_id)
    with factory() as db:
        run = db.get(AgentRun, run_id)
        run.status, run.claim_token = "running", new_claim_token()
        db.commit()
    result = dispatcher._complete_delivery(request, error=TimeoutError())
    assert result.reason == "run_already_observed"
    with factory() as db:
        assert db.query(AgentRunDispatch).one().status == "consumed"
        assert db.get(AgentRun, run_id).status == "running"
    assert dispatcher.recover_pending_agent_dispatches() == []


def test_new_pending_cycle_supersedes_old_messages_and_receipts(delivery_database):
    factory, run_id = delivery_database
    old_task = _request_delivery(factory, run_id)
    old_request, _ = dispatcher._claim_delivery(run_id)
    with factory() as db:
        run = db.get(AgentRun, run_id)
        new = dispatcher.prepare_agent_run_dispatch(db, run, reason="approval_approved", new_cycle=True)
        new_task = new.task_id
        db.commit()
    assert new_task != old_task
    stale = dispatcher._complete_delivery(old_request, error=None)
    assert stale.reason == "stale_dispatch_receipt"
    with factory() as db:
        run = db.get(AgentRun, run_id)
        assert run.task_id == new_task
        assert not dispatcher._can_record_dispatched_task(run, old_task)
        entries = db.query(AgentRunDispatch).order_by(AgentRunDispatch.generation).all()
        assert [entry.generation for entry in entries] == [1, 2]
        assert [entry.status for entry in entries] == ["superseded", "pending"]


def test_outbox_identity_reaches_celery_transport_without_broker(delivery_database, monkeypatch):
    from modules.orchestration.adapters.celery_task_runtime import CeleryTaskRuntime
    from modules.orchestration.background_task_governance import BackgroundTaskKind, submit_background_task

    factory, run_id = delivery_database
    task_id = _request_delivery(factory, run_id)
    calls = []

    class OfflineTransport:
        def send_task(self, task_name, *, kwargs, task_id):
            calls.append({"task_name": task_name, "kwargs": kwargs, "task_id": task_id})
            return SimpleNamespace(id=task_id)

    monkeypatch.setattr("modules.orchestration.task_dispatcher.get_task_runtime",
                        lambda: CeleryTaskRuntime(OfflineTransport()))
    result = submit_background_task(BackgroundTaskKind.AGENT_RUN, kwargs={"run_id": run_id},
                                    business_id=run_id, task_id=task_id)
    assert result.task_id == task_id
    assert calls == [{"task_name": result.task_name, "kwargs": {"run_id": run_id}, "task_id": task_id}]
