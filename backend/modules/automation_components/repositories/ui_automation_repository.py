"""Repository for UI automation routes."""

from __future__ import annotations

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.db.models import Project, UIExecution


class UIAutomationRepository:
    """Session-backed repository for UI automation ownership and execution queries."""

    def __init__(self, db: Session):
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def list_history(self, *, project_id: int, user_id: int, limit: int = 50) -> list[UIExecution]:
        return (
            self.db.query(UIExecution)
            .filter(
                UIExecution.project_id == project_id,
                UIExecution.user_id == user_id,
            )
            .order_by(desc(UIExecution.created_at))
            .limit(max(1, int(limit)))
            .all()
        )

    def get_execution(self, *, execution_id: int, user_id: int) -> UIExecution | None:
        return (
            self.db.query(UIExecution)
            .filter(UIExecution.id == execution_id, UIExecution.user_id == user_id)
            .first()
        )

