from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from .contracts import AgentRunCreate
from .dispatcher import start_agent_run_worker
from .service import AgentPlatformService


class EvaluationAlreadyRunningError(ValueError):
    """同一需求已有使用其他用例版本的评测。"""


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
    baseline_groups: list[str] | None = None,
) -> AgentRun:
    """创建用例评测 Run，由 Agent 平台统一调度器异步执行。"""

    input_payload = {
        "source_run_id": source_run_id,
        "requirement": requirement,
        "generated_cases": generated_cases,
        "reference_content": reference_content,
        "baseline_groups": baseline_groups or [],
        "upload": upload,
    }
    service = AgentPlatformService(db, start_agent_run_worker)
    run, reason = service.create_run(
        request=AgentRunCreate(
            project_id=project_id,
            workflow_key="test_case_evaluation",
            input_payload=input_payload,
        ),
        user_id=user_id,
    )
    if run is None:
        raise ValueError(f"无法创建用例评测 Run: {reason}")
    if reason == "already_active" and run.input_payload != input_payload:
        raise EvaluationAlreadyRunningError(f"同一需求已有其他版本在评测（Run #{run.id}），请等待完成后再提交本版本")
    return run
