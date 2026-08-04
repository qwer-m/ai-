from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun


def stage_run_artifact(
    db: Session,
    *,
    run_id: int,
    project_id: int,
    user_id: int,
    artifact_key: str,
    payload: dict[str, Any],
) -> AgentRun:
    """将结构化产物加入当前事务，由 Agent Runtime 统一提交。"""

    run = (
        db.query(AgentRun)
        .filter(
            AgentRun.id == run_id,
            AgentRun.project_id == project_id,
            AgentRun.user_id == user_id,
        )
        .first()
    )
    if run is None:
        raise LookupError("Agent Run 不存在或不属于当前项目")
    run_context = dict(run.run_context or {})
    artifacts = dict(run_context.get("artifacts") or {})
    artifacts[artifact_key] = dict(payload)
    run_context["artifacts"] = artifacts
    run.run_context = run_context
    db.add(run)
    return run
