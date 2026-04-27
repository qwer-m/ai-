"""Repository for UI test case routes."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.models import Project, UITestCase


class UITestCaseRepository:
    """Session-backed repository for UI test case CRUD with ownership checks."""

    def __init__(self, db: Session):
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def list_project_cases(self, *, project_id: int) -> list[UITestCase]:
        return self.db.query(UITestCase).filter(UITestCase.project_id == project_id).all()

    def get_owned_case(self, *, item_id: int, user_id: int) -> UITestCase | None:
        return (
            self.db.query(UITestCase)
            .join(Project, Project.id == UITestCase.project_id)
            .filter(UITestCase.id == item_id, Project.user_id == user_id)
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

