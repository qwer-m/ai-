from __future__ import annotations

from core.db.model_defs import AgentRun
from core.settings.config import settings

from .repository import AgentPlatformRepository


def prune_terminal_run_history(
    repo: AgentPlatformRepository,
    run: AgentRun,
) -> None:
    """按来源清理终态运行；失败运行不得删除可复用的成功结果。"""

    try:
        deleted_ids = repo.prune_terminal_run_history(
            project_id=int(run.project_id),
            user_id=int(run.user_id),
            workflow_definition_id=int(run.workflow_definition_id),
            keep_run_id=int(run.id),
            limit=int(settings.AGENT_RUN_HISTORY_LIMIT),
        )
        if deleted_ids:
            repo.append_event(
                run_id=run.id,
                event_type="run_history_pruned",
                payload={"deleted_run_count": len(deleted_ids)},
            )
        repo.commit()
    except Exception:
        repo.db.rollback()
