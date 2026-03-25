from typing import Optional

from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument
from routers.system.common_support import _serialize_linked_doc, _to_iso


REQUIREMENT_LIKE_TYPES = {"requirement", "product_requirement", "incomplete"}


def build_knowledge_list_related_maps(
    db: Session,
    project_id: int,
    documents: list[KnowledgeDocument],
) -> tuple[dict[int, list[dict]], dict[int, str]]:
    requirement_ids = [doc.id for doc in documents if doc.doc_type in REQUIREMENT_LIKE_TYPES]
    linked_map: dict[int, list[dict]] = {}
    if requirement_ids:
        linked_docs = (
            db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.doc_type == "test_case",
                KnowledgeDocument.source_doc_id.in_(requirement_ids),
            )
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
            .all()
        )
        for linked in linked_docs:
            linked_map.setdefault(linked.source_doc_id, []).append(_serialize_linked_doc(linked))

    source_ids = {doc.source_doc_id for doc in documents if doc.source_doc_id}
    source_name_map: dict[int, str] = {}
    if source_ids:
        source_docs = (
            db.query(KnowledgeDocument.id, KnowledgeDocument.filename)
            .filter(KnowledgeDocument.project_id == project_id, KnowledgeDocument.id.in_(source_ids))
            .all()
        )
        source_name_map = {doc.id: doc.filename for doc in source_docs}

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
