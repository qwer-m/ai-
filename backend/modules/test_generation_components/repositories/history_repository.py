"""Repository for test generation history routes."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.models import Project, TestGeneration


class TestGenerationHistoryRepository:
    """Session-backed repository for history listing and access checks."""

    def __init__(self, db: Session):
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def list_project_generations(self, *, project_id: int) -> list[TestGeneration]:
        return (
            self.db.query(TestGeneration)
            .filter(TestGeneration.project_id == project_id)
            .order_by(TestGeneration.created_at.desc(), TestGeneration.id.desc())
            .all()
        )

    def get_generation(self, *, generation_id: int) -> TestGeneration | None:
        return self.db.query(TestGeneration).filter(TestGeneration.id == generation_id).first()

    def get_latest_generation_exact(
        self,
        *,
        project_id: int,
        user_id: int,
        requirement_text: str,
    ) -> TestGeneration | None:
        return (
            self.db.query(TestGeneration)
            .filter(
                TestGeneration.project_id == project_id,
                TestGeneration.requirement_text == requirement_text,
                TestGeneration.user_id == user_id,
            )
            .order_by(TestGeneration.created_at.desc())
            .first()
        )

    def get_latest_generation_by_prefix(
        self,
        *,
        project_id: int,
        user_id: int,
        prefix: str,
    ) -> TestGeneration | None:
        return (
            self.db.query(TestGeneration)
            .filter(
                TestGeneration.project_id == project_id,
                TestGeneration.requirement_text.startswith(prefix),
                TestGeneration.user_id == user_id,
            )
            .order_by(TestGeneration.created_at.desc())
            .first()
        )
