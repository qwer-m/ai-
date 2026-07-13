from __future__ import annotations

from typing import Any

from core.db.models import LogEntry, TestGeneration
from modules.memory_fabric.contracts.memory_context import MemoryContext


class MySQLEpisodicStore:
    """
    L1 episodic read/write adapter.

    Stage 2 keeps this minimal and read-focused.
    """

    def read(self, query: dict[str, Any], ctx: MemoryContext) -> Any:
        db = query.get("db")
        kind = str(query.get("kind") or "").strip().lower()
        if db is None:
            return None
        if kind == "latest_generation":
            project_id = int(query.get("project_id") or int(ctx.project_id))
            user_id = int(query.get("user_id") or int(ctx.user_id))
            q = db.query(TestGeneration).filter(TestGeneration.project_id == project_id)
            if user_id > 0:
                q = q.filter(TestGeneration.user_id == user_id)
            return q.order_by(TestGeneration.created_at.desc(), TestGeneration.id.desc()).first()
        if kind == "run_logs":
            project_id = int(query.get("project_id") or int(ctx.project_id))
            user_id = int(query.get("user_id") or int(ctx.user_id))
            limit = max(1, int(query.get("limit") or 50))
            q = db.query(LogEntry).filter(LogEntry.project_id == project_id)
            if user_id > 0:
                q = q.filter(LogEntry.user_id == user_id)
            return q.order_by(LogEntry.id.desc()).limit(limit).all()
        return None

    def write(self, record: dict[str, Any], ctx: MemoryContext) -> None:
        db = record.get("db")
        if db is None:
            return
        kind = str(record.get("kind") or "").strip().lower()
        if kind == "log_entry":
            entry = LogEntry(
                project_id=int(record.get("project_id") or int(ctx.project_id)),
                user_id=int(record.get("user_id") or int(ctx.user_id) or 0) or None,
                log_type=str(record.get("log_type") or "system"),
                message=str(record.get("message") or ""),
            )
            db.add(entry)

