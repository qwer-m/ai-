"""Repository for operation logs."""

from __future__ import annotations

import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun, AgentRunEvent, LogEntry


class LogRepository:
    """Session-backed repository for project logs."""

    def __init__(self, db: Session):
        self.db = db

    def list_project_logs(
        self,
        *,
        project_id: int,
        user_id: int,
        limit: int,
        log_type: str | None = None,
    ) -> list[LogEntry]:
        query = (
            self.db.query(LogEntry)
            .filter(LogEntry.project_id == project_id, LogEntry.user_id == user_id)
        )
        if log_type is not None:
            query = query.filter(LogEntry.log_type == log_type)
        return (
            query.order_by(LogEntry.created_at.desc())
            .limit(max(1, int(limit)))
            .all()
        )

    def list_agent_run_events(
        self,
        *,
        project_id: int,
        user_id: int,
        limit: int,
        id_after: int | None = None,
    ) -> list[AgentRunEvent]:
        """读取项目真实 Agent Run 事件，供统一实时日志流展示。"""

        query = (
            self.db.query(AgentRunEvent)
            .join(AgentRun, AgentRun.id == AgentRunEvent.run_id)
            .filter(AgentRun.project_id == project_id, AgentRun.user_id == user_id)
        )
        if id_after is not None:
            query = query.filter(AgentRunEvent.id > id_after)
        return (
            query.order_by(AgentRunEvent.created_at.desc(), AgentRunEvent.id.desc())
            .limit(max(1, int(limit)))
            .all()
        )

    def latest_agent_event_clear_id(self, *, project_id: int, user_id: int) -> int | None:
        marker = (
            self.db.query(LogEntry)
            .filter(
                LogEntry.project_id == project_id,
                LogEntry.user_id == user_id,
                LogEntry.log_type == "clear_marker",
            )
            .order_by(LogEntry.created_at.desc(), LogEntry.id.desc())
            .first()
        )
        if marker is None:
            return None
        try:
            payload = json.loads(str(marker.message or "{}"))
            return max(0, int(payload["agent_event_id"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def create_log(self, *, project_id: int, user_id: int, log_type: str, message: str) -> LogEntry:
        row = LogEntry(
            project_id=project_id,
            user_id=user_id,
            log_type=log_type,
            message=message,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete_project_logs(self, *, project_id: int, user_id: int) -> int:
        latest_agent_event_id = int(
            self.db.query(func.max(AgentRunEvent.id))
            .join(AgentRun, AgentRun.id == AgentRunEvent.run_id)
            .filter(AgentRun.project_id == project_id, AgentRun.user_id == user_id)
            .scalar()
            or 0
        )
        deleted = (
            self.db.query(LogEntry)
            .filter(LogEntry.project_id == project_id, LogEntry.user_id == user_id)
            .delete(synchronize_session=False)
        )
        self.db.add(
            LogEntry(
                project_id=project_id,
                user_id=user_id,
                log_type="clear_marker",
                message=json.dumps(
                    {"agent_event_id": latest_agent_event_id},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        )
        self.db.commit()
        return int(deleted)

