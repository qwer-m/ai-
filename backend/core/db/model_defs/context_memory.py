from ._shared import (
    Base,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)


class AgentContextRecord(Base):
    """经确认后可跨任务使用的项目知识、用户偏好和反馈记录。

    已验证反馈可以跨任务复用，但只允许在产生它的项目作用域内检索。
    """

    __tablename__ = "agent_context_records"
    __table_args__ = (
        UniqueConstraint(
            "source_kind",
            "scope",
            "scope_id",
            "revision",
            "content_hash",
            name="uq_agent_context_record_revision",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    source_kind = Column(String(40), nullable=False, index=True)
    scope = Column(String(20), nullable=False, index=True)
    scope_id = Column(String(120), nullable=False, index=True)
    revision = Column(String(120), nullable=False)
    content_hash = Column(String(64), nullable=False)
    content = Column(JSON, nullable=False)
    evidence_refs = Column(JSON, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="pending", index=True)
    confirmed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    valid_from = Column(DateTime, nullable=True)
    valid_to = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revocation_reason = Column(Text, nullable=False, default="")
    replaced_by_id = Column(Integer, ForeignKey("agent_context_records.id"), nullable=True)
    origin_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
