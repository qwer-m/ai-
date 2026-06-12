"""Repository adapter for knowledge-document persistence operations."""

from __future__ import annotations

from typing import Iterable, Optional

from datetime import datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, aliased

from core.db.models import KnowledgeDocument, Project


USER_MANAGED_DOC_TYPES = (
    "requirement",
    "test_case",
    "prototype",
    "product_requirement",
    "incomplete",
)
OPTIONAL_USER_VISIBLE_DOC_TYPES = ("evaluation_report",)


class KnowledgeDocumentRepository:
    """Session-backed repository for KnowledgeDocument."""

    def __init__(self, db: Session):
        self.db = db

    def add(self, doc: KnowledgeDocument) -> None:
        self.db.add(doc)

    def delete(self, doc: KnowledgeDocument) -> None:
        self.db.delete(doc)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def refresh(self, doc: KnowledgeDocument) -> None:
        self.db.refresh(doc)

    def get_by_id(self, doc_id: int) -> Optional[KnowledgeDocument]:
        return self.db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()

    def get_by_project_specific_id(self, project_specific_id: int) -> Optional[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.project_specific_id == project_specific_id)
            .first()
        )

    def get_by_id_or_project_specific_id(self, doc_id: int) -> Optional[KnowledgeDocument]:
        doc = self.get_by_id(doc_id)
        if doc:
            return doc
        return self.get_by_project_specific_id(doc_id)

    def find_duplicate_by_hash(
        self,
        *,
        project_id: int,
        content_hash: str,
        exclude_doc_id: int | None = None,
    ) -> Optional[KnowledgeDocument]:
        query = self.db.query(KnowledgeDocument).filter(
            KnowledgeDocument.content_hash == content_hash,
            KnowledgeDocument.project_id == project_id,
        )
        if exclude_doc_id is not None:
            query = query.filter(KnowledgeDocument.id != exclude_doc_id)
        return query.first()

    def find_latest_by_identity(
        self,
        *,
        project_id: int,
        user_id: int | None,
        doc_type: str | None,
        filename: str,
        exclude_doc_id: int | None = None,
    ) -> Optional[KnowledgeDocument]:
        query = self.db.query(KnowledgeDocument).filter(
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.filename == filename,
        )
        if user_id is None:
            query = query.filter(KnowledgeDocument.user_id.is_(None))
        else:
            query = query.filter(KnowledgeDocument.user_id == user_id)
        if doc_type is None:
            query = query.filter(KnowledgeDocument.doc_type.is_(None))
        else:
            query = query.filter(KnowledgeDocument.doc_type == doc_type)
        if exclude_doc_id is not None:
            query = query.filter(KnowledgeDocument.id != exclude_doc_id)
        return query.order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc()).first()

    def find_by_hash(self, *, content_hash: str) -> Optional[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.content_hash == content_hash)
            .first()
        )

    def get_min_display_order(self, *, project_id: int) -> float | None:
        return (
            self.db.query(func.min(KnowledgeDocument.display_order))
            .filter(KnowledgeDocument.project_id == project_id)
            .scalar()
        )

    def list_linked_by_source(self, source_doc_id: int) -> list[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.source_doc_id == source_doc_id)
            .all()
        )

    def get_owned_by_id(self, *, doc_id: int, user_id: int) -> Optional[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .join(Project, Project.id == KnowledgeDocument.project_id)
            .filter(KnowledgeDocument.id == doc_id, Project.user_id == user_id)
            .first()
        )

    def get_owned_by_project_specific_id(
        self,
        *,
        project_specific_id: int,
        user_id: int,
    ) -> Optional[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .join(Project, Project.id == KnowledgeDocument.project_id)
            .filter(KnowledgeDocument.project_specific_id == project_specific_id, Project.user_id == user_id)
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
            .first()
        )

    def get_owned_by_id_or_project_specific_id(
        self,
        *,
        doc_id: int,
        user_id: int,
    ) -> Optional[KnowledgeDocument]:
        doc = self.get_owned_by_id(doc_id=doc_id, user_id=user_id)
        if doc:
            return doc
        return self.get_owned_by_project_specific_id(project_specific_id=doc_id, user_id=user_id)

    def list_linked_test_cases_for_sources(
        self,
        *,
        project_id: int,
        source_doc_ids: Iterable[int],
    ) -> list[KnowledgeDocument]:
        source_ids = [int(v) for v in source_doc_ids if v is not None]
        if not source_ids:
            return []
        return (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.doc_type == "test_case",
                KnowledgeDocument.source_doc_id.in_(source_ids),
            )
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
            .all()
        )

    def map_source_names(
        self,
        *,
        project_id: int,
        source_ids: Iterable[int],
    ) -> dict[int, str]:
        cleaned_source_ids = [int(v) for v in source_ids if v is not None]
        if not cleaned_source_ids:
            return {}
        rows = (
            self.db.query(KnowledgeDocument.id, KnowledgeDocument.filename)
            .filter(KnowledgeDocument.project_id == project_id, KnowledgeDocument.id.in_(cleaned_source_ids))
            .all()
        )
        return {int(row.id): str(row.filename) for row in rows}

    def list_project_docs_created_desc(
        self,
        *,
        project_id: int,
        limit: int | None = None,
    ) -> list[KnowledgeDocument]:
        query = self.db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
        query = query.order_by(KnowledgeDocument.created_at.desc())
        if limit is not None:
            query = query.limit(max(1, int(limit)))
        return query.all()

    def list_project_docs_for_snapshot(
        self,
        *,
        project_id: int,
        max_docs: int,
    ) -> list[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.project_id == project_id)
            .order_by(KnowledgeDocument.created_at.asc(), KnowledgeDocument.id.asc())
            .limit(max(1, int(max_docs)))
            .all()
        )

    def list_project_docs_ordered_by_id(self, *, project_id: int) -> list[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.project_id == project_id)
            .order_by(KnowledgeDocument.id.asc())
            .all()
        )

    def list_for_keyword_recall(
        self,
        *,
        project_id: int,
        doc_types: Iterable[str] | None = None,
    ) -> list[KnowledgeDocument]:
        query = self.db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
        cleaned_types = [str(item or "").strip().lower() for item in (doc_types or []) if str(item or "").strip()]
        if cleaned_types:
            query = query.filter(KnowledgeDocument.doc_type.in_(cleaned_types))
        return query.order_by(KnowledgeDocument.created_at.desc()).all()

    def list_for_reindex(self, *, project_id: int, doc_type: str) -> list[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.doc_type == doc_type,
                KnowledgeDocument.project_id == project_id,
            )
            .order_by(KnowledgeDocument.created_at.asc())
            .all()
        )

    def get_project_doc(self, *, project_id: int, doc_id: int) -> Optional[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.id == doc_id, KnowledgeDocument.project_id == project_id)
            .first()
        )

    def get_upper_neighbor(self, *, project_id: int, anchor_display_order: float) -> Optional[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.display_order > anchor_display_order,
            )
            .order_by(KnowledgeDocument.display_order.asc())
            .first()
        )

    def get_lower_neighbor(self, *, project_id: int, anchor_display_order: float) -> Optional[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.display_order < anchor_display_order,
            )
            .order_by(KnowledgeDocument.display_order.desc())
            .first()
        )

    def list_project_docs_by_ids(self, *, project_id: int, doc_ids: Iterable[int]) -> list[KnowledgeDocument]:
        cleaned_doc_ids = [int(v) for v in doc_ids]
        if not cleaned_doc_ids:
            return []
        return (
            self.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.id.in_(cleaned_doc_ids),
                KnowledgeDocument.project_id == project_id,
            )
            .all()
        )

    def list_with_source_doc(self) -> list[KnowledgeDocument]:
        return (
            self.db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.source_doc_id.isnot(None))
            .all()
        )

    def list_project_documents_paginated(
        self,
        *,
        project_id: int,
        page: int,
        page_size: int,
        search: str | None = None,
        doc_type: str | None = None,
        include_linked_test_cases: bool = False,
        include_evaluation_reports: bool = False,
        include_internal_artifacts: bool = False,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[int, list[KnowledgeDocument]]:
        query = self.db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)

        if not include_internal_artifacts:
            visible_types = list(USER_MANAGED_DOC_TYPES)
            if include_evaluation_reports:
                visible_types.extend(OPTIONAL_USER_VISIBLE_DOC_TYPES)
            query = query.filter(KnowledgeDocument.doc_type.in_(visible_types))

        if search:
            query = query.filter(KnowledgeDocument.filename.like(f"%{search}%"))

        if doc_type:
            query = query.filter(KnowledgeDocument.doc_type == doc_type)

        if not include_linked_test_cases:
            query = query.filter(
                ~and_(
                    KnowledgeDocument.doc_type == "test_case",
                    KnowledgeDocument.source_doc_id.isnot(None),
                )
            )

        if not include_evaluation_reports:
            query = query.filter(KnowledgeDocument.doc_type != "evaluation_report")

        if start_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                query = query.filter(KnowledgeDocument.created_at >= start_dt)
            except ValueError:
                query = query.filter(KnowledgeDocument.created_at >= start_date)

        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                query = query.filter(KnowledgeDocument.created_at <= end_dt)
            except ValueError:
                query = query.filter(KnowledgeDocument.created_at <= end_date)

        total = int(query.count())
        documents = (
            query.order_by(
                KnowledgeDocument.display_order.desc(),
                KnowledgeDocument.created_at.asc(),
                KnowledgeDocument.id.asc(),
            )
            .offset(max(0, (int(page) - 1) * int(page_size)))
            .limit(max(1, int(page_size)))
            .all()
        )
        return total, documents

    def list_project_documents_with_relation_search(
        self,
        *,
        project_id: int,
        search_term: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[KnowledgeDocument]:
        query = self.db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)

        if search_term:
            linked_doc = aliased(KnowledgeDocument)
            source_doc = aliased(KnowledgeDocument)
            query = (
                query.outerjoin(linked_doc, linked_doc.source_doc_id == KnowledgeDocument.id)
                .outerjoin(source_doc, source_doc.id == KnowledgeDocument.source_doc_id)
                .filter(
                    or_(
                        KnowledgeDocument.filename.contains(search_term),
                        linked_doc.filename.contains(search_term),
                        source_doc.filename.contains(search_term),
                    )
                )
                .distinct()
            )

        if start_date:
            query = query.filter(KnowledgeDocument.created_at >= start_date)

        if end_date:
            query = query.filter(KnowledgeDocument.created_at <= f"{end_date} 23:59:59")

        return query.order_by(
            KnowledgeDocument.display_order.desc(),
            KnowledgeDocument.created_at.desc(),
        ).all()

    def list_for_index_audit(
        self,
        *,
        project_id: int | None,
        user_id: int | None,
        limit: int,
    ) -> list[KnowledgeDocument]:
        query = self.db.query(KnowledgeDocument)
        if project_id is not None:
            query = query.filter(KnowledgeDocument.project_id == project_id)
        if user_id is not None:
            query = query.filter(KnowledgeDocument.user_id == user_id)
        return (
            query.order_by(KnowledgeDocument.id.asc())
            .limit(max(100, int(limit)))
            .all()
        )
