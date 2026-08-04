from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import User
from modules.agent_platform.repository import AgentPlatformRepository

router = APIRouter()


@router.get("/evaluation/history/{project_id}")
def get_evaluation_history(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    repo = AgentPlatformRepository(db)
    if repo.get_owned_project(project_id=project_id, user_id=current_user.id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    run_items = repo.list_runs(
        project_id=project_id,
        user_id=current_user.id,
        limit=50,
    )

    history: list[dict[str, Any]] = []

    for item in run_items:
        artifact = (item.run_context or {}).get("artifacts", {}).get("test_evaluation")
        if not isinstance(artifact, dict):
            continue
        evaluation = artifact.get("evaluation") if isinstance(artifact.get("evaluation"), dict) else {}
        metrics = evaluation.get("metrics") if isinstance(evaluation.get("metrics"), dict) else {}
        history.append(
            {
                "id": f"run-{item.id}",
                "type": "agent_run_evaluation",
                "created_at": item.created_at,
                "preview": str(evaluation.get("summary") or "")[:200],
                "precision": metrics.get("precision"),
                "recall": metrics.get("recall"),
                "f1_score": metrics.get("f1_score"),
                "semantic_similarity": metrics.get("semantic_similarity"),
            }
        )
    history.sort(key=lambda x: x["created_at"] or datetime.min, reverse=True)
    return {"history": history[:50]}
