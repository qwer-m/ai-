"""Repository for operation logs."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.models import LogEntry


class LogRepository:
    """Session-backed repository for project logs."""

    def __init__(self, db: Session):
        self.db = db

    def list_project_logs(self, *, project_id: int, user_id: int, limit: int) -> list[LogEntry]:
        return (
            self.db.query(LogEntry)
            .filter(LogEntry.project_id == project_id, LogEntry.user_id == user_id)
            .order_by(LogEntry.created_at.desc())
            .limit(max(1, int(limit)))
            .all()
        )

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
        deleted = (
            self.db.query(LogEntry)
            .filter(LogEntry.project_id == project_id, LogEntry.user_id == user_id)
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return int(deleted)

