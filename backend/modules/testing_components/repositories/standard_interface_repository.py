"""Repository for standard API interface persistence operations."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from core.db.model_defs import Project, StandardInterface


class StandardInterfaceRepository:
    """Session-backed repository for standard interface CRUD."""

    def __init__(self, db: Session):
        self.db = db

    def list_interfaces(self, *, user_id: int, project_id: Optional[int] = None) -> list[StandardInterface]:
        query = self.db.query(StandardInterface).filter(StandardInterface.user_id == user_id)
        if project_id:
            query = query.filter(StandardInterface.project_id == project_id)
        return query.all()

    def get_owned_project(self, *, project_id: int, user_id: int) -> Optional[Project]:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def get_owned_interface(self, *, interface_id: int, user_id: int) -> Optional[StandardInterface]:
        return (
            self.db.query(StandardInterface)
            .filter(StandardInterface.id == interface_id, StandardInterface.user_id == user_id)
            .first()
        )

    def add(self, entity: object) -> None:
        self.db.add(entity)

    def delete(self, entity: object) -> None:
        self.db.delete(entity)

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, entity: object) -> None:
        self.db.refresh(entity)

