"""Repository for project context snapshot persistence."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from core.db.models import ProjectContextSnapshot


class ContextSnapshotRepository:
    """Session-backed repository for project context snapshots."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_project_id(self, *, project_id: int) -> Optional[ProjectContextSnapshot]:
        return (
            self.db.query(ProjectContextSnapshot)
            .filter(ProjectContextSnapshot.project_id == project_id)
            .first()
        )

    def add(self, row: ProjectContextSnapshot) -> None:
        self.db.add(row)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def refresh(self, row: ProjectContextSnapshot) -> None:
        self.db.refresh(row)

