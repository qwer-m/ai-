"""Real-table models for priority sample pool — incremental migration from KnowledgeDocument JSON."""

from __future__ import annotations

from ._shared import Base, Column, Integer, String, DateTime, ForeignKey, Float, Text, func


class SamplePoolItem(Base):
    """One row per raw sample in the priority sample pool."""

    __tablename__ = "sample_pool_items"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    sample_id = Column(String(512), nullable=False, index=True)
    source_type = Column(String(64), nullable=False, default="priority_debug_manual_add")
    source_id = Column(Integer, nullable=True)
    source_case_id = Column(String(256), nullable=True)

    sample_kind = Column(String(16), nullable=False, default="negative")
    pattern_usage = Column(String(16), nullable=True)

    case_id = Column(String(256), nullable=True)
    title = Column(String(256), nullable=True)
    user_comment = Column(Text, nullable=True)

    expected_priority = Column(String(8), nullable=True)
    reason_category = Column(String(64), nullable=True)
    pattern_category = Column(String(64), nullable=True)

    pattern_summary = Column(String(256), nullable=True)
    pattern_canonical = Column(String(256), nullable=True)
    pattern_cluster_key = Column(String(128), nullable=True, index=True)

    confidence = Column(Float, nullable=True)
    pattern_weight = Column(Float, nullable=True)
    pattern_quality_score = Column(Float, nullable=True)

    status = Column(String(16), nullable=False, default="active")
    deleted_at = Column(DateTime, nullable=True)
    delete_reason = Column(String(256), nullable=True)

    learning_status = Column(String(24), nullable=True)
    learning_confirmed_at = Column(DateTime, nullable=True)
    learning_confirmed_by = Column(Integer, nullable=True)

    tags_json = Column(Text, nullable=True)
    extra_json = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class LearnedPattern(Base):
    """One row per aggregated pattern cluster."""

    __tablename__ = "learned_patterns"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    pattern_id = Column(String(256), nullable=False, index=True)
    cluster_key = Column(String(128), nullable=False, index=True)

    signal_type = Column(String(16), nullable=False)
    pattern_usage = Column(String(16), nullable=True)

    pattern_summary = Column(String(256), nullable=True)
    pattern_canonical = Column(String(256), nullable=True)
    pattern_category = Column(String(64), nullable=True)
    reason_category = Column(String(64), nullable=True)
    pattern_scope = Column(String(40), nullable=True)
    pattern_grain = Column(String(40), nullable=True)

    sample_count = Column(Integer, nullable=False, default=0)
    avg_confidence = Column(Float, nullable=True)
    avg_weight = Column(Float, nullable=True)
    top_weight = Column(Float, nullable=True)

    top_source_types_json = Column(Text, nullable=True)
    active_sample_ids_json = Column(Text, nullable=True)

    governance_status = Column(String(16), nullable=False, default="active")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class QualityFeedbackEvent(Base):
    """One row per learning / selection event."""

    __tablename__ = "quality_feedback_events"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    event_type = Column(String(64), nullable=False, index=True)
    event_payload_json = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
