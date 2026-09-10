"""Business service for project management routes."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.model_defs import (
    APIExecution,
    KnowledgeDocument,
    LogEntry,
    Project,
    RecallMetric,
    UIErrorOperation,
    UIExecution,
)
from modules.system_components.repositories.project_repository import ProjectRepository


class ProjectService:
    """项目增删改查用例层。"""

    def __init__(self, db: Session):
        self.repo = ProjectRepository(db)

    def create_project(self, *, payload, user_id: int):
        level = 1
        if payload.parent_id:
            parent = self.repo.get_owned_project(project_id=payload.parent_id, user_id=user_id)
            if not parent:
                return {"error": "Parent project not found"}
            level = int(parent.level or 1) + 1
            if level > 3:
                return {"error": "Maximum project nesting level (3) reached."}

        duplicate = self.repo.find_duplicate_name(
            user_id=user_id,
            name=payload.name,
            parent_id=payload.parent_id,
        )
        if duplicate:
            return {"error": "Project name already exists in this level"}

        row = Project(
            name=payload.name,
            description=payload.description,
            parent_id=payload.parent_id,
            level=level,
            user_id=user_id,
        )
        self.repo.add(row)
        self.repo.commit()
        self.repo.refresh(row)
        return row

    def list_projects(self, *, user_id: int) -> list[Project]:
        return self.repo.list_owned_projects(user_id=user_id)

    def get_project(self, *, project_id: int, user_id: int):
        return self.repo.get_owned_project(project_id=project_id, user_id=user_id)

    def update_project(self, *, project_id: int, payload, user_id: int):
        row = self.repo.get_owned_project(project_id=project_id, user_id=user_id)
        if not row:
            return {"error": "Project not found"}

        parent_changed = payload.parent_id != row.parent_id
        new_level = int(row.level or 1)
        if parent_changed:
            if payload.parent_id:
                parent = self.repo.get_owned_project(project_id=payload.parent_id, user_id=user_id)
                if not parent:
                    return {"error": "Parent project not found"}
                new_level = int(parent.level or 1) + 1
                if new_level > 3:
                    return {"error": "Maximum project nesting level (3) reached."}
            else:
                new_level = 1

        duplicate = self.repo.find_duplicate_name(
            user_id=user_id,
            name=payload.name,
            parent_id=payload.parent_id,
            exclude_project_id=project_id,
        )
        if duplicate:
            return {"error": "Project name already exists in this level"}

        row.name = payload.name
        row.description = payload.description
        row.parent_id = payload.parent_id
        row.level = new_level

        if parent_changed:
            def _update_child_levels(project_row: Project, parent_level: int) -> None:
                project_row.level = parent_level + 1
                for child in project_row.children:
                    _update_child_levels(child, int(project_row.level or 1))

            for child in row.children:
                _update_child_levels(child, int(row.level or 1))

        self.repo.commit()
        self.repo.refresh(row)
        return row

    def delete_project(self, *, project_id: int, user_id: int) -> tuple[bool, dict]:
        project = self.repo.get_owned_project(project_id=project_id, user_id=user_id)
        if not project:
            return False, {"status_code": 404, "body": {"error": "Project not found"}}

        if project.children:
            return False, {
                "status_code": 400,
                "body": {"error": "Cannot delete project with child projects. Please delete children first."},
            }

        try:
            self.repo.delete_agent_platform(project_id=project_id)
            for model in (
                KnowledgeDocument,
                LogEntry,
                UIErrorOperation,
                UIExecution,
                APIExecution,
                RecallMetric,
            ):
                self.repo.delete_project_scoped(model, project_id=project_id)
            self.repo.delete(project)
            self.repo.commit()
            return True, {"message": "Project deleted successfully"}
        except Exception as exc:
            self.repo.rollback()
            return False, {"status_code": 500, "body": {"error": f"Failed to delete project: {exc}"}}
