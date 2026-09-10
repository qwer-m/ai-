from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from .contracts import AgentRunCreate
from .dispatcher import start_agent_run_worker
from .service import AgentPlatformService


def create_test_case_evaluation_run(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    source_run_id: int,
    requirement: str,
    generated_cases: list[dict[str, Any]],
    reference_content: str,
    upload: dict[str, Any],
    project_context: str = "",
) -> AgentRun:
    """创建用例评测 Run，由 Agent 平台统一调度器异步执行。"""

    service = AgentPlatformService(db, start_agent_run_worker)
    run, reason = service.create_run(
        request=AgentRunCreate(
            project_id=project_id,
            workflow_key="test_case_evaluation",
            input_payload={
                "source_run_id": source_run_id,
                "requirement": requirement,
                "generated_cases": generated_cases,
                "reference_content": reference_content,
                "project_context": project_context,
                "upload": upload,
            },
        ),
        user_id=user_id,
    )
    if run is None:
        raise ValueError(f"无法创建用例评测 Run: {reason}")
    return run
