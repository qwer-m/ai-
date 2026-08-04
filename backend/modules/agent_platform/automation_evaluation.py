from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from .contracts import AgentRunCreate
from .runtime import run_agent_workflow
from .service import AgentPlatformService


EvaluationType = Literal["ui", "api"]


def execute_automation_evaluation(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    evaluation_type: EvaluationType,
    script: str,
    execution_result: str,
    project_context: str = "",
    user_journey: dict[str, Any] | None = None,
    openapi_spec: str = "",
    source_execution_id: int | None = None,
) -> tuple[AgentRun, dict[str, Any]]:
    """创建并执行正式自动化评测工作流，返回同一 Run 上的结构化产物。"""

    workflow_key = f"{evaluation_type}_automation_evaluation"
    input_payload: dict[str, Any] = {
        "evaluation_type": evaluation_type,
        "script": script,
        "execution_result": execution_result,
        "project_context": project_context,
        "source_execution_id": source_execution_id,
    }
    if evaluation_type == "ui":
        input_payload["user_journey"] = user_journey
    else:
        input_payload["openapi_spec"] = openapi_spec

    service = AgentPlatformService(db)
    run, reason = service.create_run(
        request=AgentRunCreate(
            project_id=project_id,
            workflow_key=workflow_key,
            input_payload=input_payload,
        ),
        user_id=user_id,
        dispatch=False,
    )
    if run is None:
        raise ValueError(f"无法创建自动化评测 Run: {reason}")

    execution = run_agent_workflow(run_id=run.id, db=db)
    db.refresh(run)
    if execution.get("status") != "success" or run.status != "success":
        raise RuntimeError(f"自动化评测 Run 执行失败: run_id={run.id}, status={run.status}")

    artifact = (run.run_context or {}).get("artifacts", {}).get("automation_evaluation")
    if not isinstance(artifact, dict):
        raise RuntimeError(f"自动化评测 Run 未生成结构化产物: run_id={run.id}")
    return run, dict(artifact)
