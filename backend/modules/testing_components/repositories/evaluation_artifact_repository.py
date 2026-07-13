"""Repository for evaluation compare artifact document persistence."""

from __future__ import annotations

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument


class EvaluationArtifactRepository:
    """Session-backed repository for compare artifact document lookup/upsert."""

    def __init__(self, db: Session):
        self.db = db

    def get_latest_artifact_doc(
        self,
        *,
        project_id: int,
        user_id: int,
        doc_type: str,
        filename: str,
    ) -> KnowledgeDocument | None:
        return (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.user_id == user_id,
                KnowledgeDocument.doc_type == doc_type,
                KnowledgeDocument.filename == filename,
            )
            .order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id))
            .first()
        )

    def add(self, entity: object) -> None:
        self.db.add(entity)

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, entity: object) -> None:
        self.db.refresh(entity)

