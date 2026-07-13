"""Repository adapter for project ownership lookup."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from core.db.models import Project


class ProjectRepository:
    """Session-backed repository for Project ownership checks."""

    def __init__(self, db: Session):
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Optional[Project]:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

