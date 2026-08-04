"""Repository for API automation routes."""

from __future__ import annotations

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.db.model_defs import APIExecution, Project, StandardInterface


class APIAutomationRepository:
    """Session-backed repository for API automation ownership and history queries."""

    def __init__(self, db: Session):
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def list_standard_interfaces(
        self,
        *,
        project_id: int,
        user_id: int,
        limit: int = 12,
    ) -> list[StandardInterface]:
        return (
            self.db.query(StandardInterface)
            .filter(
                StandardInterface.project_id == project_id,
                StandardInterface.user_id == user_id,
                StandardInterface.type == "request",
            )
            .order_by(desc(StandardInterface.updated_at), desc(StandardInterface.id))
            .limit(max(1, int(limit)))
            .all()
        )

    def list_api_history(self, *, project_id: int, user_id: int, limit: int = 50) -> list[APIExecution]:
        return (
            self.db.query(APIExecution)
            .filter(APIExecution.project_id == project_id, APIExecution.user_id == user_id)
            .order_by(desc(APIExecution.created_at), desc(APIExecution.id))
            .limit(max(1, int(limit)))
            .all()
        )

