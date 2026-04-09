from typing import Optional

from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from routers.system.common_support import _serialize_linked_doc, _to_iso


REQUIREMENT_LIKE_TYPES = {"requirement", "product_requirement", "incomplete"}


def build_knowledge_list_related_maps(
    db: Session,
    project_id: int,
    documents: list[KnowledgeDocument],
) -> tuple[dict[int, list[dict]], dict[int, str]]:
    repo = KnowledgeDocumentRepository(db)
    requirement_ids = [doc.id for doc in documents if doc.doc_type in REQUIREMENT_LIKE_TYPES]
    linked_map: dict[int, list[dict]] = {}
    if requirement_ids:
        linked_docs = repo.list_linked_test_cases_for_sources(
            project_id=project_id,
            source_doc_ids=requirement_ids,
        )
        for linked in linked_docs:
            linked_map.setdefault(linked.source_doc_id, []).append(_serialize_linked_doc(linked))

    source_ids = {doc.source_doc_id for doc in documents if doc.source_doc_id}
    source_name_map = repo.map_source_names(project_id=project_id, source_ids=source_ids)

    return linked_map, source_name_map


def build_knowledge_list_response(
    serialized_docs: list[dict],
    page: int,
    page_size: int,
    total: int,
    total_pages: int,
) -> dict:
    return {
        "documents": serialized_docs,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


def build_knowledge_detail_response(doc: KnowledgeDocument, linked_docs: list[KnowledgeDocument]) -> dict:
    return {
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "created_at": _to_iso(doc.created_at),
        "content": doc.content,
        "source_doc_id": doc.source_doc_id,
        "linked_docs": [_serialize_linked_doc(linked) for linked in linked_docs],
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "parsed_at": _to_iso(doc.parsed_at),
        "task_id": doc.task_id,
        "retry_count": doc.retry_count,
    }


def build_parse_status_response(doc: KnowledgeDocument, task_state: Optional[str]) -> dict:
    return {
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "parsed_at": _to_iso(doc.parsed_at),
        "task_id": doc.task_id,
        "retry_count": doc.retry_count,
        "task_state": task_state,
    }


def build_upload_knowledge_response(doc: KnowledgeDocument, enqueue_result: dict) -> dict:
    return {
        "success": True,
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "created_at": _to_iso(doc.created_at),
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "parsed_at": _to_iso(doc.parsed_at),
        "task_id": enqueue_result.get("task_id"),
        "retry_count": doc.retry_count,
    }
