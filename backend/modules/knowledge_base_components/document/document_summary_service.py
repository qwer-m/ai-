"""Shared summary helpers for document flows."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session


def ensure_document_summary(
    module,
    *,
    doc,
    db: Session,
    user_id: Optional[int],
) -> str:
    """
    Ensure one document has a summary and return the summary content.

    This keeps summary generation behind a single seam for future replacement.
    """
    return module._ensure_summary(doc, db, user_id)

