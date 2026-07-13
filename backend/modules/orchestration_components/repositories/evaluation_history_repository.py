"""Repository for evaluation history persistence operations."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.db.models import Evaluation, KnowledgeDocument, Project, TestGenerationComparison


class EvaluationHistoryRepository:
    """Session-backed repository for evaluation history and report documents."""

    def __init__(self, db: Session):
        self.db = db

    def get_owned_project(self, *, project_id: int, user_id: int) -> Optional[Project]:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )

    def list_recent_evaluations(self, *, project_id: int, user_id: int, limit: int = 30) -> list[Evaluation]:
        return (
            self.db.query(Evaluation)
            .filter(Evaluation.project_id == project_id, Evaluation.user_id == user_id)
            .order_by(desc(Evaluation.created_at), desc(Evaluation.id))
            .limit(max(1, int(limit)))
            .all()
        )

    def list_recent_comparisons(
        self,
        *,
        project_id: int,
        user_id: int,
        limit: int = 30,
    ) -> list[TestGenerationComparison]:
        return (
            self.db.query(TestGenerationComparison)
            .filter(
                TestGenerationComparison.project_id == project_id,
                TestGenerationComparison.user_id == user_id,
            )
            .order_by(desc(TestGenerationComparison.created_at), desc(TestGenerationComparison.id))
            .limit(max(1, int(limit)))
            .all()
        )

    def get_owned_report_doc(
        self,
        *,
        doc_id: int,
        project_id: int,
        user_id: int,
    ) -> Optional[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.id == doc_id,
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.user_id == user_id,
            )
            .first()
        )

    def get_latest_report_doc(
        self,
        *,
        project_id: int,
        user_id: int,
        source_key: str | None = None,
    ) -> Optional[KnowledgeDocument]:
        query = self.db.query(KnowledgeDocument).filter(
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.user_id == user_id,
            KnowledgeDocument.doc_type == "evaluation_report",
        )
        if source_key:
            query = query.filter(KnowledgeDocument.filename.like(f"evaluation_report_{source_key}_%"))
        return query.order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id)).first()

    def add(self, entity: object) -> None:
        self.db.add(entity)

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, entity: object) -> None:
        self.db.refresh(entity)

