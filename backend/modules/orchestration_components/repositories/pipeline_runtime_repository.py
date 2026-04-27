"""Repository helpers for pipeline runtime worker."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.models import PipelineRun


class PipelineRuntimeRepository:
    """Session-backed repository for pipeline worker run lookups."""

    def __init__(self, db: Session):
        self.db = db

    def get_run(self, *, run_id: int) -> PipelineRun | None:
        return self.db.query(PipelineRun).filter(PipelineRun.id == run_id).first()

