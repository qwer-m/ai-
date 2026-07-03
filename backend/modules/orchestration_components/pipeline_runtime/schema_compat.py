"""Runtime schema compatibility for persisted pipeline runs."""

from __future__ import annotations

from sqlalchemy import inspect, text

from core.db.database import engine
from core.db.models import PipelineRun

PIPELINE_RUN_COMPAT_COLUMNS: dict[str, str] = {
    "task_id": "VARCHAR(100) NULL",
    "claim_token": "VARCHAR(100) NULL",
    "heartbeat_at": "DATETIME NULL",
    "lease_expires_at": "DATETIME NULL",
}


def ensure_pipeline_table() -> None:
    """Create pipeline table and add compatibility columns for existing installs."""

    try:
        PipelineRun.__table__.create(bind=engine, checkfirst=True)
        inspector = inspect(engine)
        existing_columns = {column["name"] for column in inspector.get_columns(PipelineRun.__tablename__)}
        missing_columns = [
            (name, ddl)
            for name, ddl in PIPELINE_RUN_COMPAT_COLUMNS.items()
            if name not in existing_columns
        ]
        if missing_columns:
            with engine.begin() as conn:
                for name, ddl in missing_columns:
                    conn.execute(text(f"ALTER TABLE {PipelineRun.__tablename__} ADD COLUMN {name} {ddl}"))
    except Exception:
        pass
