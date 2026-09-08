from __future__ import annotations

from typing import Any

from core.db.database import SessionLocal
from modules.orchestration.background_task_governance import (
    BackgroundTaskKind,
    submit_background_task,
)
from modules.orchestration.task_dispatcher import TaskDispatchResult
from .repository import AgentPlatformRepository
from .lifecycle import transition_run


def _can_fail_unclaimed_run(run: Any) -> bool:
    """只有尚未被任何执行器认领的待运行任务可由投递失败终止。"""

    return (
        str(getattr(run, "status", "") or "") == "pending"
        and not getattr(run, "task_id", None)
        and not getattr(run, "claim_token", None)
    )


def _can_record_dispatched_task(run: Any, task_id: str) -> bool:
    """避免迟到的投递结果覆盖另一个执行器已经持有的租约。"""

    status = str(getattr(run, "status", "") or "")
    claim_token = str(getattr(run, "claim_token", "") or "")
    return (status == "pending" and not claim_token) or (
        status == "running" and claim_token == task_id
    )


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
            run = repo.get_run_for_update(run_id=run_id)
            if run is not None:
                if _can_record_dispatched_task(run, result.task_id):
                    run.task_id = result.task_id
                else:
                    repo.append_event(
                        run_id=run.id,
                        event_type="run_dispatch_task_id_ignored",
                        payload={
                            "status": str(run.status or ""),
                            "task_id": result.task_id,
                        },
                    )
                db.add(run)
                db.commit()
        finally:
            db.close()
        return result
    except Exception as exc:
        db = SessionLocal()
        try:
            repo = AgentPlatformRepository(db)
            run = repo.get_run_for_update(run_id=run_id)
            if run is not None:
                error_message = f"任务投递失败: {type(exc).__name__}: {exc}"
                if _can_fail_unclaimed_run(run):
                    transition_run(
                        repo, run, "failed", event_type="run_dispatch_failed",
                        error_message=error_message,
                        payload={"status": "failed", "error_type": type(exc).__name__, "message": str(exc)[:1000]},
                    )
                else:
                    repo.append_event(
                        run_id=run.id, event_type="run_dispatch_failure_ignored",
                        payload={"status": str(run.status or ""), "error_type": type(exc).__name__, "message": str(exc)[:1000]},
                    )
                db.add(run)
                db.commit()
        finally:
            db.close()
        raise
