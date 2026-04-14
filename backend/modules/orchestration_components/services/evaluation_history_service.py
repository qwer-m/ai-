"""Business service for evaluation history routes."""

from __future__ import annotations

from typing import Any, Optional

from core.db.models import KnowledgeDocument
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.domain.knowledge_base import knowledge_base
from modules.orchestration_components.repositories.evaluation_history_repository import (
    EvaluationHistoryRepository,
)


class EvaluationHistoryService:
    """Use-case layer for evaluation history retrieval and report upsert."""

    def __init__(self, db):
        self.repo = EvaluationHistoryRepository(db)
        self._db = db

    def has_owned_project(self, *, project_id: int, user_id: int) -> bool:
        return bool(self.repo.get_owned_project(project_id=project_id, user_id=user_id))

    def list_history_sources(self, *, project_id: int, user_id: int):
        eval_items = self.repo.list_recent_evaluations(project_id=project_id, user_id=user_id, limit=30)
        compare_items = self.repo.list_recent_comparisons(project_id=project_id, user_id=user_id, limit=30)
        return eval_items, compare_items

    def get_latest_supplement_doc(
        self,
        *,
        project_id: int,
        user_id: int,
        source_key: Optional[str],
    ) -> Optional[KnowledgeDocument]:
        return self.repo.get_latest_report_doc(
            project_id=project_id,
            user_id=user_id,
            source_key=source_key,
        )

    def upsert_evaluation_report(
        self,
        *,
        project_id: int,
        user_id: int,
        doc_id: Optional[int],
        source_key: str,
        filename: str,
        content: str,
    ) -> tuple[str, Optional[KnowledgeDocument], bool, Optional[int], Optional[dict[str, Any]]]:
        replaced_previous = False
        previous_doc_id: Optional[int] = None

        if doc_id:
            doc = self.repo.get_owned_report_doc(
                doc_id=doc_id,
                project_id=project_id,
                user_id=user_id,
            )
            if not doc:
                return "not_found", None, replaced_previous, previous_doc_id, None
            replaced_previous = True
            previous_doc_id = doc.id
            doc.filename = filename
            doc.content = content
            doc.doc_type = "evaluation_report"
            self.repo.commit()
            self.repo.refresh(doc)
            return "updated", doc, replaced_previous, previous_doc_id, None

        existing = self.repo.get_latest_report_doc(
            project_id=project_id,
            user_id=user_id,
            source_key=source_key,
        )
        if existing:
            replaced_previous = True
            previous_doc_id = existing.id
            existing.filename = filename
            existing.content = content
            existing.doc_type = "evaluation_report"
            self.repo.commit()
            self.repo.refresh(existing)
            return "updated", existing, replaced_previous, previous_doc_id, None

        created = knowledge_base.add_document(
            filename,
            content,
            "evaluation_report",
            project_id,
            self._db,
            force=True,
            user_id=user_id,
        )
        if isinstance(created, dict):
            return "conflict", None, replaced_previous, previous_doc_id, created
        return "created", created, replaced_previous, previous_doc_id, None

    def log_save_knowledge(
        self,
        *,
        project_id: int,
        user_id: int,
        doc_id: int,
        attachment_count: int,
        content_length: int,
    ) -> None:
        log_workflow_trace(
            self._db,
            project_id,
            user_id,
            WorkflowKind.EVALUATION,
            WorkflowStage.LEARN,
            {
                "action": "save_evaluation_knowledge",
                "doc_id": doc_id,
                "attachments": attachment_count,
                "content_length": content_length,
            },
        )

