"""Persistence adapter for pipeline orchestration routes."""

from __future__ import annotations

from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from core.db.models import LogEntry, PipelineRun, Project


class PipelineRepository:
    """Session-backed repository for pipeline runs and workflow traces."""

    def __init__(self, db: Session):
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def get_owned_run(self, *, run_id: int, user_id: int) -> PipelineRun | None:
        return (
            self.db.query(PipelineRun)
            .filter(PipelineRun.id == run_id, PipelineRun.user_id == user_id)
            .first()
        )

    def list_runs(self, *, project_id: int, user_id: int, limit: int) -> list[PipelineRun]:
        return (
            self.db.query(PipelineRun)
            .filter(PipelineRun.project_id == project_id, PipelineRun.user_id == user_id)
            .order_by(desc(PipelineRun.created_at), desc(PipelineRun.id))
            .limit(max(1, int(limit)))
            .all()
        )

    def add(self, row: object) -> None:
        self.db.add(row)

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, row: object) -> None:
        self.db.refresh(row)

    def list_workflow_trace_rows(self, *, project_id: int, user_id: int, limit: int) -> list[LogEntry]:
        return (
            self.db.query(LogEntry)
            .filter(
                LogEntry.project_id == project_id,
                or_(LogEntry.user_id == user_id, LogEntry.user_id.is_(None)),
                LogEntry.message.like("WORKFLOW_TRACE:%"),
            )
            .order_by(desc(LogEntry.created_at), desc(LogEntry.id))
            .limit(max(1, int(limit)))
            .all()
        )

