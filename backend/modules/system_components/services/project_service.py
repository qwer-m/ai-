"""Business service for project management routes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.db.models import (
    APIExecution,
    Evaluation,
    KnowledgeDocument,
    LogEntry,
    Project,
    ProjectPipelineConfig,
    RecallMetric,
    TestGeneration,
    TestGenerationComparison,
    UIErrorOperation,
    UIExecution,
)
from modules.system_components.repositories.project_repository import ProjectRepository


class ProjectService:
    """Use-case layer for project CRUD and project-level defaults."""

    def __init__(self, db: Session):
        self.repo = ProjectRepository(db)

    def _normalize_agent_defaults(self, raw: Any, fallback_cls) -> dict[str, Any]:
        try:
            return fallback_cls.model_validate(raw or {}).model_dump()
        except Exception:
            return fallback_cls().model_dump()

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

    def get_pipeline_agent_defaults(self, *, project_id: int, user_id: int, defaults_cls):
        project = self.repo.get_owned_project(project_id=project_id, user_id=user_id)
        if not project:
            return None

        row = self.repo.get_pipeline_config(project_id=project_id, user_id=user_id)
        if not row:
            return {
                "project_id": project_id,
                "agent": defaults_cls().model_dump(),
                "source": "default",
            }

        return {
            "project_id": project_id,
            "agent": self._normalize_agent_defaults(row.agent_defaults, defaults_cls),
            "source": "saved",
            "updated_at": row.updated_at,
        }

    def upsert_pipeline_agent_defaults(self, *, project_id: int, user_id: int, agent_defaults: dict, defaults_cls):
        project = self.repo.get_owned_project(project_id=project_id, user_id=user_id)
        if not project:
            return None

        row = self.repo.get_pipeline_config(project_id=project_id, user_id=user_id)
        if not row:
            row = ProjectPipelineConfig(
                project_id=project_id,
                user_id=user_id,
                agent_defaults=agent_defaults,
            )
        else:
            row.agent_defaults = agent_defaults

        self.repo.add(row)
        self.repo.commit()
        self.repo.refresh(row)
        return {
            "project_id": project_id,
            "agent": self._normalize_agent_defaults(row.agent_defaults, defaults_cls),
            "source": "saved",
            "updated_at": row.updated_at,
        }

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
            for model in (
                KnowledgeDocument,
                LogEntry,
                TestGeneration,
                UIErrorOperation,
                UIExecution,
                APIExecution,
                Evaluation,
                ProjectPipelineConfig,
                TestGenerationComparison,
                RecallMetric,
            ):
                self.repo.delete_project_scoped(model, project_id=project_id)
            self.repo.delete(project)
            self.repo.commit()
            return True, {"message": "Project deleted successfully"}
        except Exception as exc:
            self.repo.rollback()
            return False, {"status_code": 500, "body": {"error": f"Failed to delete project: {exc}"}}
