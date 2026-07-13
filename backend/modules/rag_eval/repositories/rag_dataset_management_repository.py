"""Repository for RAG dataset management routes."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.db.models import RagDataset, RagDatasetSample


class RagDatasetManagementRepository:
    """Session-backed repository for dataset/sample CRUD and ownership checks."""

    def __init__(self, db: Session):
        self.db = db

    def list_user_datasets(self, *, user_id: int) -> list[RagDataset]:
        return (
            self.db.query(RagDataset)
            .filter(RagDataset.user_id == user_id)
            .order_by(RagDataset.updated_at.desc(), RagDataset.id.desc())
            .all()
        )

    def find_dataset_by_name(self, *, user_id: int, name: str) -> RagDataset | None:
        return (
            self.db.query(RagDataset)
            .filter(RagDataset.user_id == user_id, RagDataset.name == name)
            .first()
        )

    def get_owned_dataset(self, *, dataset_id: int, user_id: int) -> RagDataset | None:
        return (
            self.db.query(RagDataset)
            .filter(RagDataset.id == dataset_id, RagDataset.user_id == user_id)
            .first()
        )

    def count_samples_by_dataset_ids(self, dataset_ids: Iterable[int]) -> dict[int, int]:
        ids = [int(x) for x in dataset_ids]
        if not ids:
            return {}
        rows = (
            self.db.query(
                RagDatasetSample.dataset_id,
                func.count(RagDatasetSample.id).label("sample_count"),
            )
            .filter(RagDatasetSample.dataset_id.in_(ids))
            .group_by(RagDatasetSample.dataset_id)
            .all()
        )
        return {int(row.dataset_id): int(row.sample_count or 0) for row in rows}

    def delete_dataset_and_samples(self, dataset: RagDataset) -> None:
        self.db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == dataset.id).delete()
        self.db.delete(dataset)

    def list_dataset_samples(
        self,
        *,
        dataset_id: int,
        tags: list[str] | None,
        difficulty: str,
        enabled_only: bool,
        page: int,
        page_size: int,
    ) -> list[RagDatasetSample]:
        q = self.db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == dataset_id)
        if enabled_only:
            q = q.filter(RagDatasetSample.enabled.is_(True))
        if difficulty != "all":
            q = q.filter(RagDatasetSample.difficulty == difficulty)
        for tag in tags or []:
            q = q.filter(RagDatasetSample.tags.contains([tag]))
        return (
            q.order_by(RagDatasetSample.id.asc())
            .offset(max(0, page - 1) * page_size)
            .limit(page_size)
            .all()
        )

    def get_owned_sample(self, *, sample_id: int, user_id: int) -> RagDatasetSample | None:
        return (
            self.db.query(RagDatasetSample)
            .join(RagDataset, RagDataset.id == RagDatasetSample.dataset_id)
            .filter(RagDatasetSample.id == sample_id, RagDataset.user_id == user_id)
            .first()
        )

    def find_sample_by_query(self, *, dataset_id: int, query: str) -> RagDatasetSample | None:
        return (
            self.db.query(RagDatasetSample)
            .filter(RagDatasetSample.dataset_id == dataset_id, RagDatasetSample.query == query)
            .first()
        )

    def list_samples_for_export(self, *, dataset_id: int) -> list[RagDatasetSample]:
        return (
            self.db.query(RagDatasetSample)
            .filter(RagDatasetSample.dataset_id == dataset_id)
            .order_by(RagDatasetSample.id.asc())
            .all()
        )

    def count_dataset_samples(self, *, dataset_id: int) -> int:
        return int(
            self.db.query(func.count(RagDatasetSample.id))
            .filter(RagDatasetSample.dataset_id == dataset_id)
            .scalar()
            or 0
        )

    def add(self, entity: object) -> None:
        self.db.add(entity)

    def delete(self, entity: object) -> None:
        self.db.delete(entity)

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, entity: object) -> None:
        self.db.refresh(entity)
