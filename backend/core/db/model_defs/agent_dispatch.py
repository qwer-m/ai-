"""与运行状态同事务保存的任务投递意图，不授予 Worker 执行所有权。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index
from sqlalchemy.dialects.mysql import DATETIME

from ._shared import Base, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint


# 投递到期时间必须与 Python UTC 时钟保持同一微秒精度，避免落库进位成未来一秒。
_DISPATCH_DATETIME = DateTime().with_variant(DATETIME(fsp=6), "mysql")


class AgentRunDispatch(Base):
    __tablename__ = "agent_run_dispatches"
    __table_args__ = (
        UniqueConstraint("run_id", "generation", name="uq_agent_run_dispatch_generation"),
        UniqueConstraint("task_id", name="uq_agent_run_dispatch_task_id"),
        CheckConstraint("attempt_count >= 0", name="ck_agent_run_dispatch_attempts"),
        CheckConstraint(
            "status IN ('pending','publishing','published','consumed','superseded','exhausted')",
            name="ck_agent_run_dispatch_status",
        ),
        Index("ix_agent_run_dispatch_due", "status", "next_attempt_at", "id"),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=False, index=True)
    generation = Column(Integer, nullable=False)
    task_id = Column(String(120), nullable=False)
    reason = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(_DISPATCH_DATETIME, nullable=False, default=datetime.utcnow)
    publish_token = Column(String(64), nullable=True)
    publish_lease_expires_at = Column(_DISPATCH_DATETIME, nullable=True)
    published_at = Column(_DISPATCH_DATETIME, nullable=True)
    last_error = Column(Text, nullable=False, default="")
    created_at = Column(_DISPATCH_DATETIME, nullable=False, default=datetime.utcnow)
    updated_at = Column(_DISPATCH_DATETIME, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
