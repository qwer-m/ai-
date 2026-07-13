from __future__ import annotations

import hashlib
import re
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.db.models import Project
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


def build_source_key(raw: str) -> str:
    value = re.sub(r"\s+", " ", (raw or "").strip().lower())
    if not value:
        value = "default"
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def normalize_source_title(raw: str) -> str:
    value = (raw or "").strip()
    return value or "未命名文档"


def source_filename(source_key: str) -> str:
    return f"evaluation_report_{source_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
