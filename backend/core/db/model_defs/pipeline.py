from ._shared import Base, Column, Integer, String, Boolean, JSON, DateTime, ForeignKey, Float, Text, func, UniqueConstraint, relationship, backref, LONGTEXT

class PipelineRun(Base):
    """
    Global orchestration run record.
    Stores pipeline request payload, per-stage status, outputs, and retry lineage.
    """
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True)

    status = Column(String(20), nullable=False, default="pending", index=True, comment="pending/running/success/failed")
    current_stage = Column(String(50), nullable=True, comment="current stage key")

    request_payload = Column(JSON, nullable=True, comment="pipeline input payload")
    stage_states = Column(JSON, nullable=True, comment="per-stage status details")
    artifacts = Column(JSON, nullable=True, comment="pipeline generated outputs")
    error_message = Column(Text, nullable=True, comment="top-level failure message")

    retry_of_run_id = Column(Integer, ForeignKey('pipeline_runs.id'), nullable=True, index=True)
    retry_of = relationship("PipelineRun", remote_side=[id], backref=backref("retries", lazy="selectin"))

    created_at = Column(DateTime, server_default=func.now(), index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class ProjectPipelineConfig(Base):
    """
    Project-level defaults for orchestration pipeline settings.
    """
    __tablename__ = "project_pipeline_configs"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_project_pipeline_configs_user_project"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    agent_defaults = Column(JSON, nullable=False, comment="Default agent config for this project")

    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
