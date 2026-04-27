"""Business service for project log routes."""

from __future__ import annotations

from sqlalchemy.orm import Session

from modules.system_components.repositories.log_repository import LogRepository
from modules.system_components.repositories.project_repository import ProjectRepository


class LogService:
    """Use-case layer for project logs with ownership checks."""

    def __init__(self, db: Session):
        self.project_repo = ProjectRepository(db)
        self.log_repo = LogRepository(db)

    def _project_exists_for_user(self, *, project_id: int, user_id: int) -> bool:
        return self.project_repo.get_owned_project(project_id=project_id, user_id=user_id) is not None

    def get_project_logs(self, *, project_id: int, user_id: int, limit: int):
        if not self._project_exists_for_user(project_id=project_id, user_id=user_id):
            return None
        return self.log_repo.list_project_logs(project_id=project_id, user_id=user_id, limit=limit)

    def create_log(self, *, project_id: int, user_id: int, log_type: str, message: str):
        if not self._project_exists_for_user(project_id=project_id, user_id=user_id):
            return None
        return self.log_repo.create_log(
            project_id=project_id,
            user_id=user_id,
            log_type=log_type,
            message=message,
        )

    def delete_project_logs(self, *, project_id: int, user_id: int):
        if not self._project_exists_for_user(project_id=project_id, user_id=user_id):
            return None
        deleted = self.log_repo.delete_project_logs(project_id=project_id, user_id=user_id)
        return {"status": "success", "deleted_logs": int(deleted)}

