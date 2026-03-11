import json
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from core.utils import log_to_db


class WorkflowKind(str, Enum):
    TEST_GENERATION = "test_generation"
    UI_AUTOMATION = "ui_automation"
    API_AUTOMATION = "api_automation"
    EVALUATION = "evaluation"


class WorkflowStage(str, Enum):
    PLAN = "plan"
    CONTEXT = "context"
    GENERATE = "generate"
    EXECUTE = "execute"
    EVALUATE = "evaluate"
    REPAIR = "repair"
    LEARN = "learn"


def build_workflow_trace(kind: str, stage: str, details: dict[str, Any]) -> str:
    payload = {
        "kind": kind,
        "stage": stage,
        "details": details,
    }
    return f"WORKFLOW_TRACE:{json.dumps(payload, ensure_ascii=False)}"


def log_workflow_trace(
    db: Session,
    project_id: int,
    user_id: int | None,
    kind: str,
    stage: str,
    details: dict[str, Any],
) -> None:
    log_to_db(
        db,
        project_id,
        "system",
        build_workflow_trace(kind, stage, details),
        user_id=user_id,
    )
