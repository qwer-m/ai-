"""Business service for project log routes."""

from __future__ import annotations

import json
from datetime import datetime

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
        normalized_limit = max(1, int(limit))
        user_logs = self.log_repo.list_project_logs(
            project_id=project_id,
            user_id=user_id,
            limit=normalized_limit,
            log_type="user",
        )
        system_logs = self.log_repo.list_project_logs(
            project_id=project_id,
            user_id=user_id,
            limit=normalized_limit,
            log_type="system",
        )
        agent_event_clear_id = self.log_repo.latest_agent_event_clear_id(
            project_id=project_id,
            user_id=user_id,
        )
        agent_events = self.log_repo.list_agent_run_events(
            project_id=project_id,
            user_id=user_id,
            limit=normalized_limit,
            id_after=agent_event_clear_id,
        )
        feed = [
            {
                "id": row.id,
                "project_id": project_id,
                "log_type": row.log_type,
                "message": row.message,
                "created_at": row.created_at,
            }
            for row in [*user_logs, *system_logs]
        ]
        for event in agent_events:
            payload = dict(event.payload or {})
            payload_text = (
                f" {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
                if payload
                else ""
            )
            feed.append(
                {
                    # Agent 事件使用负数命名空间，避免与 operation_logs 主键冲突。
                    "id": -event.id,
                    "project_id": project_id,
                    "log_type": "system",
                    "message": f"本次 Agent 生成 · {event.event_type}{payload_text}",
                    "created_at": event.created_at,
                }
            )
        user_feed = [item for item in feed if item["log_type"] == "user"]
        system_feed = [item for item in feed if item["log_type"] == "system"]
        user_feed.sort(
            key=lambda item: item["created_at"] or datetime.min,
            reverse=True,
        )
        system_feed.sort(
            key=lambda item: item["created_at"] or datetime.min,
            reverse=True,
        )
        # limit 对两个日志标签分别生效，避免密集 Agent 事件挤掉用户操作记录。
        result = user_feed[:normalized_limit] + system_feed[:normalized_limit]
        result.sort(
            key=lambda item: item["created_at"] or datetime.min,
            reverse=True,
        )
        return result

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

