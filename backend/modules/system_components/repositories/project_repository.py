"""Repository for project-related persistence operations."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from core.db.models import Project, ProjectPipelineConfig


class ProjectRepository:
    """Session-backed repository for project and project pipeline settings."""

    def __init__(self, db: Session):
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Optional[Project]:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def list_owned_projects(self, *, user_id: int) -> list[Project]:
        return (
            self.db.query(Project)
            .filter(Project.user_id == user_id)
            .order_by(Project.created_at.desc())
            .all()
        )

    def find_duplicate_name(
        self,
        *,
        user_id: int,
        name: str,
        parent_id: int | None,
        exclude_project_id: int | None = None,
    ) -> Optional[Project]:
        query = self.db.query(Project).filter(
            Project.user_id == user_id,
            Project.name == name,
            Project.parent_id == parent_id,
        )
        if exclude_project_id is not None:
            query = query.filter(Project.id != exclude_project_id)
        return query.first()

    def get_pipeline_config(self, *, project_id: int, user_id: int) -> Optional[ProjectPipelineConfig]:
        return (
            self.db.query(ProjectPipelineConfig)
            .filter(
                ProjectPipelineConfig.project_id == project_id,
                ProjectPipelineConfig.user_id == user_id,
            )
            .first()
        )

    def add(self, entity: object) -> None:
        self.db.add(entity)

    def delete(self, entity: object) -> None:
        self.db.delete(entity)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def refresh(self, entity: object) -> None:
        self.db.refresh(entity)

    def delete_project_scoped(self, model, *, project_id: int) -> int:
        return int(
            self.db.query(model)
            .filter(model.project_id == project_id)
            .delete(synchronize_session=False)
        )

