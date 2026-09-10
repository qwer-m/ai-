from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.db.model_defs import Project
from modules.knowledge_base_components.repositories.project_repository import ProjectRepository


def is_attachment_ocr_ok(parsed_text: str) -> bool:
    text = (parsed_text or "").strip()
    if not text:
        return False

    lowered = text.lower()
    if lowered.startswith(
        (
            "ocr error",
            "ocr exception",
            "error:",
            "exception",
            "[image ocr failed:",
            "[error processing image:",
        )
    ):
        return False

    failure_markers = (
        "额度耗尽",
        "免费额度已用完",
        "余额不足",
        "insufficient_quota",
        "quota exceeded",
        "rate limit",
        "authentication failed",
        "invalid api key",
        "model not found",
    )
    return not any(marker in text or marker in lowered for marker in failure_markers)


def get_owned_project(project_id: int, db: Session, user_id: int) -> Project:
    project = ProjectRepository(db).get_owned_project(project_id=project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
