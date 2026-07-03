from __future__ import annotations

from typing import Any

from .feedback_control_state import FeedbackControlState
from .workflow_blueprint_repository import WorkflowBlueprintRepository


def build_from_workflow_blueprint_repository(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    requirement_text: str = "",
    current_source_doc_ids: list[int] | tuple[int, ...] | None = None,
    current_content_hash: str = "",
    max_workflow_blueprints: int = 5,
    repository_cls: Any = WorkflowBlueprintRepository,
) -> FeedbackControlState:
    if db is None or not project_id or not user_id:
        return FeedbackControlState.empty()
    try:
        workflow_blueprints = repository_cls(db).list_matching_trusted_contracts(
            project_id=int(project_id),
            user_id=int(user_id),
            requirement_text=str(requirement_text or ""),
            current_source_doc_ids=current_source_doc_ids or [],
            current_content_hash=str(current_content_hash or ""),
            limit=int(max_workflow_blueprints),
        )
    except Exception:
        workflow_blueprints = []

    source_doc_ids = [
        int(value)
        for value in (current_source_doc_ids or [])
        if str(value or "").strip().isdigit()
    ]
    if not workflow_blueprints:
        return FeedbackControlState(
            source_meta={
                "sources": ["workflow_blueprint_repository"],
                "workflow_blueprint_repository_count": 0,
                "trusted_workflow_contract_count": 0,
                "workflow_blueprint_current_source_doc_ids": source_doc_ids,
                "workflow_blueprint_same_source_count": 0,
            }
        )
    return FeedbackControlState(
        workflow_blueprints=workflow_blueprints,
        source_meta={
            "sources": ["workflow_blueprint_repository"],
            "workflow_blueprint_repository_count": int(len(workflow_blueprints)),
            "trusted_workflow_contract_count": int(len(workflow_blueprints)),
            "workflow_blueprint_current_source_doc_ids": source_doc_ids,
            "workflow_blueprint_same_source_count": int(
                sum(1 for item in workflow_blueprints if (item.get("match_debug") or {}).get("same_source"))
            ),
        },
    )
