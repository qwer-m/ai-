"""以真实 Run/Celery 身份的隔离副本验证认领，不写线上业务记录或调用模型。"""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentRunEvent
from modules.agent_platform import runtime
from modules.agent_platform.run_repository import AgentRunRepository


@pytest.fixture(scope="module")
def real_delivery_metadata():
    """选择同一真实项目的两个已记录投递身份，只在 SQLite 中注入过期状态。"""
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        source_id = db.scalar(select(AgentRun.id).where(
            AgentRun.task_id.is_not(None), AgentRun.status.in_(("failed", "cancelled", "success")),
        ).order_by(AgentRun.id.desc()).limit(1))
        assert source_id is not None, "需要真实历史 Celery 投递记录"
        source = db.get(AgentRun, source_id)
        other_task_id = db.scalar(select(AgentRun.task_id).where(
            AgentRun.project_id == source.project_id, AgentRun.user_id == source.user_id,
            AgentRun.task_id.is_not(None), AgentRun.task_id != source.task_id,
        ).order_by(AgentRun.id.desc()).limit(1))
        assert other_task_id, "需要同项目另一条真实投递身份进行隔离错误注入"
        record = {column.key: deepcopy(getattr(source, column.key)) for column in AgentRun.__table__.columns}
        db.rollback()
    return record, other_task_id


def isolated_run(real_delivery_metadata, *, status, lease_expired):
    record, other_task_id = real_delivery_metadata
    copied = deepcopy(record)
    copied["status"] = status
    copied["lease_expires_at"] = datetime.utcnow() + timedelta(seconds=-60 if lease_expired else 60)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    AgentRunEvent.__table__.create(engine)
    db = Session(engine)
    db.add(AgentRun(**copied))
    db.commit()
    return db, engine, copied, other_task_id


@pytest.mark.parametrize("status,lease_expired", [
    ("pending", True), ("running", True), ("running", False),
])
def test_other_real_delivery_cannot_claim_any_active_generation(real_delivery_metadata, status, lease_expired):
    db, engine, copied, other_task_id = isolated_run(
        real_delivery_metadata, status=status, lease_expired=lease_expired,
    )
    try:
        assert runtime._claim_run(AgentRunRepository(db), copied["id"], other_task_id) is None
        row = db.get(AgentRun, copied["id"])
        assert {column.key: getattr(row, column.key) for column in AgentRun.__table__.columns} == copied
        assert db.scalar(select(AgentRunEvent.id)) is None
        assert not db.new and not db.dirty and not db.deleted
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("status", ["pending", "running"])
def test_expected_real_delivery_can_claim_pending_or_expired_run(real_delivery_metadata, status):
    db, engine, copied, _ = isolated_run(real_delivery_metadata, status=status, lease_expired=True)
    try:
        claimed = runtime._claim_run(AgentRunRepository(db), copied["id"], copied["task_id"])
        assert claimed is not None and claimed.status == "running"
        assert claimed.task_id == copied["task_id"]
        assert claimed.claim_token and claimed.claim_token != copied["claim_token"]
        assert claimed.lease_expires_at > datetime.utcnow()
        assert claimed.input_payload == copied["input_payload"]
        assert claimed.output_payload == copied["output_payload"]
        events = db.scalars(select(AgentRunEvent)).all()
        if status == "pending":
            assert len(events) == 1
            assert events[0].event_type == "run_started" and events[0].payload == {"task_id": copied["task_id"]}
        else:
            # 当前同状态重新认领不新增状态转换事件，不能虚构一次 pending→running。
            assert events == []
    finally:
        db.close()
        engine.dispose()
