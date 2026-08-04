from __future__ import annotations

from datetime import datetime

from core.db.database import SessionLocal
from modules.orchestration.background_task_governance import (
    BackgroundTaskKind,
    submit_background_task,
)
from modules.orchestration.task_dispatcher import TaskDispatchResult
from .repository import AgentPlatformRepository


def start_agent_run_worker(run_id: int) -> TaskDispatchResult:
    try:
        result = submit_background_task(
            BackgroundTaskKind.AGENT_RUN,
            kwargs={"run_id": run_id},
            business_id=run_id,
        )
        db = SessionLocal()
        try:
            repo = AgentPlatformRepository(db)
            run = repo.get_run(run_id=run_id)
            if run is not None:
                run.task_id = result.task_id
                db.add(run)
                db.commit()
        finally:
            db.close()
        return result
    except Exception as exc:
        db = SessionLocal()
        try:
            repo = AgentPlatformRepository(db)
            run = repo.get_run(run_id=run_id)
            if run is not None:
                run.status = "failed"
                run.error_message = f"任务投递失败: {type(exc).__name__}: {exc}"
                run.finished_at = datetime.utcnow()
                db.add(run)
                db.commit()
        finally:
            db.close()
        raise
