from __future__ import annotations

from typing import TYPE_CHECKING

from core.db.model_defs import AgentRun
from core.settings.config import settings

from .sources import RunSourceRecord

if TYPE_CHECKING:
    from .run_repository import AgentRunRepository


def terminal_run_ids_to_delete(
    terminal_run_rows: list[RunSourceRecord],
    *,
    keep_run_id: int,
    limit: int,
) -> list[int]:
    """按来源保留最新终态；失败或取消的新运行不能淘汰成功结果。"""
    keep_row = next((row for row in terminal_run_rows if row.run_id == keep_run_id), None)
    if keep_row is None or keep_row.source_key is None:
        return []
    scoped_rows = [
        row for row in terminal_run_rows
        if row.source_key == keep_row.source_key
        and (keep_row.status == "success" or row.status != "success")
    ]
    keep_ids = {keep_run_id}
    for row in scoped_rows:
        if len(keep_ids) >= max(1, int(limit)):
            break
        keep_ids.add(row.run_id)
    return [row.run_id for row in scoped_rows if row.run_id not in keep_ids]


def prune_terminal_run_history(
    repo: AgentRunRepository,
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
        repo.db.commit()
    except Exception:
        repo.db.rollback()
