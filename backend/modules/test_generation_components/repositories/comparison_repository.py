"""Repository for test generation comparison lookup helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.models import TestGenerationComparison


class TestGenerationComparisonRepository:
    """Session-backed repository for comparison history lookup."""

    def __init__(self, db: Session):
        self.db = db

    def list_recent_project_comparisons(
        self,
        *,
        project_id: int,
        user_id: int,
        limit: int = 200,
    ) -> list[TestGenerationComparison]:
        return (
            self.db.query(TestGenerationComparison)
            .filter(
                TestGenerationComparison.project_id == project_id,
                TestGenerationComparison.user_id == user_id,
            )
            .order_by(TestGenerationComparison.created_at.desc(), TestGenerationComparison.id.desc())
            .limit(max(1, int(limit)))
            .all()
        )

