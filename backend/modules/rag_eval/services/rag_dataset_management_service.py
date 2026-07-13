"""Business service for RAG dataset CRUD/import/export routes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.db.models import RagDataset, RagDatasetSample
from modules.rag_eval.repositories.rag_dataset_management_repository import (
    RagDatasetManagementRepository,
)


class RagDatasetManagementService:
    """Use-case layer for dataset/sample management."""

    def __init__(self, db):
        self.repo = RagDatasetManagementRepository(db)

    def list_datasets(self, *, user_id: int) -> list[dict[str, Any]]:
        datasets = self.repo.list_user_datasets(user_id=user_id)
        counts = self.repo.count_samples_by_dataset_ids([ds.id for ds in datasets])
        result: list[dict[str, Any]] = []
        for ds in datasets:
            result.append({**ds.__dict__, "sample_count": int(counts.get(int(ds.id), 0))})
        return result

    def create_dataset(self, *, user_id: int, payload: dict[str, Any]) -> tuple[str, RagDataset | None]:
        if self.repo.find_dataset_by_name(user_id=user_id, name=str(payload.get("name") or "")):
            return "exists", None
        row = RagDataset(
            user_id=user_id,
            name=str(payload.get("name") or ""),
            type=str(payload.get("type") or "validation"),
            description=payload.get("description"),
        )
        self.repo.add(row)
        self.repo.commit()
        self.repo.refresh(row)
        return "created", row

    def update_dataset(
        self,
        *,
        dataset_id: int,
        user_id: int,
        payload: dict[str, Any],
    ) -> tuple[str, RagDataset | None]:
        ds = self.repo.get_owned_dataset(dataset_id=dataset_id, user_id=user_id)
        if not ds:
            return "not_found", None
        for key, value in payload.items():
            setattr(ds, key, value)
        self.repo.commit()
        self.repo.refresh(ds)
        return "updated", ds

    def count_samples(self, *, dataset_id: int) -> int:
        return self.repo.count_dataset_samples(dataset_id=dataset_id)

    def delete_dataset(self, *, dataset_id: int, user_id: int) -> bool:
        ds = self.repo.get_owned_dataset(dataset_id=dataset_id, user_id=user_id)
        if not ds:
            return False
        self.repo.delete_dataset_and_samples(ds)
        self.repo.commit()
        return True

    def list_samples(
        self,
        *,
        dataset_id: int,
        user_id: int,
        tags: list[str] | None,
        difficulty: str,
        enabled_only: bool,
        page: int,
        page_size: int,
    ) -> tuple[str, list[RagDatasetSample]]:
        ds = self.repo.get_owned_dataset(dataset_id=dataset_id, user_id=user_id)
        if not ds:
            return "dataset_not_found", []
        rows = self.repo.list_dataset_samples(
            dataset_id=dataset_id,
            tags=tags,
            difficulty=difficulty,
            enabled_only=enabled_only,
            page=page,
            page_size=page_size,
        )
        return "ok", rows

    def create_sample(
        self,
        *,
        dataset_id: int,
        user_id: int,
        payload: dict[str, Any],
    ) -> tuple[str, RagDatasetSample | None]:
        ds = self.repo.get_owned_dataset(dataset_id=dataset_id, user_id=user_id)
        if not ds:
            return "dataset_not_found", None
        row = RagDatasetSample(dataset_id=dataset_id, **payload)
        self.repo.add(row)
        self.repo.commit()
        self.repo.refresh(row)
        return "created", row

    def update_sample(
        self,
        *,
        sample_id: int,
        user_id: int,
        payload: dict[str, Any],
    ) -> tuple[str, RagDatasetSample | None]:
        row = self.repo.get_owned_sample(sample_id=sample_id, user_id=user_id)
        if not row:
            return "not_found", None
        for key, value in payload.items():
            setattr(row, key, value)
        self.repo.commit()
        self.repo.refresh(row)
        return "updated", row

    def delete_sample(self, *, sample_id: int, user_id: int) -> bool:
        row = self.repo.get_owned_sample(sample_id=sample_id, user_id=user_id)
        if not row:
            return False
        self.repo.delete(row)
        self.repo.commit()
        return True

    def import_samples(
        self,
        *,
        user_id: int,
        raw_content: str,
        dataset_id: int | None,
        name: str | None,
        dataset_type: str,
    ) -> tuple[int, int, int, list[str]]:
        if dataset_id:
            ds = self.repo.get_owned_dataset(dataset_id=dataset_id, user_id=user_id)
            if not ds:
                raise ValueError("Dataset not found")
        else:
            _, ds = self.create_dataset(
                user_id=user_id,
                payload={
                    "name": name or f"import-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "type": dataset_type,
                    "description": "imported",
                },
            )
            if not ds:
                # rare fallback for name collision during same-second import
                ds = RagDataset(
                    user_id=user_id,
                    name=f"import-{datetime.now().strftime('%Y%m%d%H%M%S')}-{user_id}",
                    type=dataset_type,
                    description="imported",
                )
                self.repo.add(ds)
                self.repo.commit()
                self.repo.refresh(ds)

        imported = 0
        skipped = 0
        errors: list[str] = []
        for idx, line in enumerate((raw_content or "").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                query = str(item.get("query") or "").strip()
                if not query:
                    skipped += 1
                    continue
                if self.repo.find_sample_by_query(dataset_id=ds.id, query=query):
                    skipped += 1
                    continue
                row = RagDatasetSample(
                    dataset_id=ds.id,
                    query=query,
                    gold_docs=item.get("gold_docs") or [],
                    gold_chunks=item.get("gold_chunks") or [],
                    gold_answer=item.get("gold_answer") or "",
                    answer_points=item.get("answer_points") or [],
                    tags=item.get("tags") or [],
                    difficulty=item.get("difficulty") or "medium",
                    metadata_filters=item.get("metadata_filters") or {},
                    expected_doc_version=item.get("expected_doc_version"),
                    enabled=bool(item.get("enabled", True)),
                )
                self.repo.add(row)
                imported += 1
            except Exception as exc:
                errors.append(f"line {idx}: {exc}")
        self.repo.commit()
        return int(ds.id), imported, skipped, errors[:30]

    def export_dataset_lines(self, *, dataset_id: int, user_id: int) -> tuple[str, str]:
        ds = self.repo.get_owned_dataset(dataset_id=dataset_id, user_id=user_id)
        if not ds:
            return "not_found", ""
        rows = self.repo.list_samples_for_export(dataset_id=dataset_id)
        lines: list[str] = []
        for row in rows:
            lines.append(
                json.dumps(
                    {
                        "id": row.id,
                        "query": row.query,
                        "gold_docs": row.gold_docs or [],
                        "gold_chunks": row.gold_chunks or [],
                        "gold_answer": row.gold_answer or "",
                        "answer_points": row.answer_points or [],
                        "tags": row.tags or [],
                        "difficulty": row.difficulty,
                        "metadata_filters": row.metadata_filters or {},
                        "expected_doc_version": row.expected_doc_version,
                        "enabled": bool(row.enabled),
                    },
                    ensure_ascii=False,
                )
            )
        return "ok", "\n".join(lines)
