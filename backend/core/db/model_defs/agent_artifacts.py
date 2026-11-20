"""不可变产物与增量行动日志；运行检查点只保存内容引用和读取游标。"""

from sqlalchemy import Index
from core.db import db_utc_now
from ._shared import Base, Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint


class AgentArtifactBlob(Base):
    __tablename__ = "agent_artifact_blobs"
    __table_args__ = (UniqueConstraint("user_id", "project_id", "sha256", name="uq_agent_artifact_content"),)
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    sha256 = Column(String(64), nullable=False)
    encoding = Column(String(32), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    logical_bytes = Column(Integer, nullable=False)
    stored_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=db_utc_now)


class AgentJournalBranch(Base):
    __tablename__ = "agent_journal_branches"
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    logical_task_id = Column(String(160), nullable=False)
    source_hash = Column(String(64), nullable=True)
    input_hash = Column(String(64), nullable=False)
    owner_node_id = Column(Integer, ForeignKey("agent_node_runs.id"), nullable=False, unique=True)
    parent_id = Column(String(36), ForeignKey("agent_journal_branches.id"), nullable=True)
    parent_receipt_cursor = Column(Integer, nullable=False, default=0)
    parent_call_cursor = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=db_utc_now)


class AgentActionReceiptRecord(Base):
    __tablename__ = "agent_action_receipts"
    __table_args__ = (Index("ix_agent_receipt_lookup", "branch_id", "tool_call_id", "id"),)
    id = Column(Integer, primary_key=True)
    branch_id = Column(String(36), ForeignKey("agent_journal_branches.id"), nullable=False)
    tool_call_id = Column(String(200), nullable=False)
    record = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=db_utc_now)


class AgentValidatedCallRecord(Base):
    __tablename__ = "agent_validated_calls"
    __table_args__ = (
        UniqueConstraint("branch_id", "call_key", name="uq_agent_validated_call"),
        Index("ix_agent_call_parent", "branch_id", "parent_tool_call_id", "id"),
    )
    id = Column(Integer, primary_key=True)
    branch_id = Column(String(36), ForeignKey("agent_journal_branches.id"), nullable=False)
    call_key = Column(String(64), nullable=False)
    agent_key = Column(String(120), nullable=False)
    parent_tool_call_id = Column(String(200), nullable=False)
    record = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=db_utc_now)
