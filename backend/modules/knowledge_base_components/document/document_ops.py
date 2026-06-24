"""Knowledge base document operations."""

# Keep facade logic split out of knowledge_base.py.


from typing import Optional
import logging

from sqlalchemy.orm import Session

from core.cache_layer.chroma_client import chroma_client as _default_chroma_client
from core.db.models import KnowledgeDocument
from modules.knowledge_base_components.document.document_index_service import (
    delete_document_indexes,
    upsert_document_indexes,
)
from modules.knowledge_base_components.document.document_summary_service import (
    ensure_document_summary,
)
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)

# Only these doc types are written to vector index.
INDEXABLE_DOC_TYPES = (
    "requirement",
    "product_requirement",
    "incomplete",
    "evaluation_report",
    "agent_learning",
)

logger = logging.getLogger(__name__)
# Compatibility seam for tests and legacy monkeypatching.
chroma_client = _default_chroma_client


def _trigger_snapshot_rebuild_async(
    module,
    *,
    project_id: int,
    user_id: Optional[int],
    reason: str,
    db: Session,
) -> None:
    """
    中文注释：文档变更后触发 snapshot 后台重建。
    该动作为“尽力触发”，不得影响当前主流程。
    """
    try:
        result = module.enqueue_context_snapshot_rebuild(
            project_id=project_id,
            db=db,
            user_id=user_id,
            force_rebuild=False,
            rebuild_reason_hint=reason,
        )
        logger.info(
            "snapshot_rebuild_triggered project_id=%s user_id=%s reason=%s queued=%s queue_reason=%s",
            project_id,
            user_id,
            reason,
            bool((result or {}).get("queued")),
            (result or {}).get("reason"),
        )
    except Exception as e:
        logger.warning(
            "snapshot_rebuild_trigger_failed project_id=%s user_id=%s reason=%s error=%s",
            project_id,
            user_id,
            reason,
            e,
        )


def add_document_impl(
    module,
    filename: str,
    content: str,
    doc_type: str,
    project_id: int,
    db: Session,
    force: bool = False,
    user_id: Optional[int] = None,
):
    """Add a document and sync DB + vector indexes."""
    repo = KnowledgeDocumentRepository(db)
    content_hash = module.calculate_hash(content)

    cover_doc = repo.find_latest_by_identity(
        project_id=project_id,
        user_id=user_id,
        doc_type=doc_type,
        filename=filename,
    )
    if cover_doc:
        return update_document_impl(module, cover_doc.id, filename, content, doc_type, db)

    existing = repo.find_duplicate_by_hash(project_id=project_id, content_hash=content_hash)

    if existing:
        if not force:
            return {
                "status": "duplicate",
                "existing_filename": existing.filename,
                "existing_doc_id": existing.id,
            }
        return existing

    min_order = repo.get_min_display_order(project_id=project_id)
    new_order = (min_order if min_order is not None else 0.0) - 1.0

    doc = KnowledgeDocument(
        filename=filename,
        content=content,
        doc_type=doc_type,
        content_hash=content_hash,
        project_id=project_id,
        project_specific_id=0,
        user_id=user_id,
        display_order=new_order,
    )
    repo.add(doc)
    repo.commit()
    repo.refresh(doc)

    module.reindex_project_specific_ids(doc_type, project_id, db)
    repo.refresh(doc)

    summary = ""
    try:
        summary = ensure_document_summary(module, doc=doc, db=db, user_id=user_id)
    except Exception:
        pass

    if doc_type in INDEXABLE_DOC_TYPES:
        upsert_document_indexes(
            doc_id=doc.id,
            content=content,
            metadata={
                "project_id": project_id,
                "doc_type": doc_type,
                "filename": filename,
                "doc_id": doc.id,
                "user_id": user_id,
                "is_summary": False,
            },
            summary_text=summary,
            summary_metadata={
                "project_id": project_id,
                "doc_type": doc_type,
                "filename": f"{filename} (Summary)",
                "doc_id": doc.id,
                "user_id": user_id,
                "is_summary": True,
            },
            client=chroma_client,
        )

    _trigger_snapshot_rebuild_async(
        module,
        project_id=project_id,
        user_id=user_id,
        reason="document_added",
        db=db,
    )
    return doc


def update_document_impl(
    module,
    doc_id: int,
    filename: str,
    content: str,
    doc_type: str,
    db: Session,
):
    """Update a document and rebuild affected indexes."""
    repo = KnowledgeDocumentRepository(db)
    doc = repo.get_by_id_or_project_specific_id(doc_id)
    if not doc:
        return None

    original_doc_type = doc.doc_type
    project_id = doc.project_id

    content_changed = False
    if content != doc.content:
        content_hash = module.calculate_hash(content)
        doc.content = content
        doc.content_hash = content_hash
        content_changed = True
        doc.summary = None

    if filename:
        doc.filename = filename
    if doc_type:
        doc.doc_type = doc_type

    repo.commit()

    if original_doc_type != doc_type:
        module.reindex_project_specific_ids(original_doc_type, project_id, db)
        module.reindex_project_specific_ids(doc_type, project_id, db)
    else:
        module.reindex_project_specific_ids(doc_type, project_id, db)

    repo.refresh(doc)

    try:
        ensure_document_summary(module, doc=doc, db=db, user_id=getattr(doc, "user_id", None))
    except Exception:
        pass

    if content_changed and doc.doc_type in INDEXABLE_DOC_TYPES:
        summary_text = str(doc.summary or "").strip()
        upsert_document_indexes(
            doc_id=doc.id,
            content=content,
            metadata={
                "project_id": project_id,
                "doc_type": doc.doc_type,
                "filename": filename or doc.filename,
                "doc_id": doc.id,
                "user_id": getattr(doc, "user_id", None),
                "is_summary": False,
            },
            summary_text=summary_text,
            summary_metadata={
                "project_id": project_id,
                "doc_type": doc.doc_type,
                "filename": f"{(filename or doc.filename)} (Summary)",
                "doc_id": doc.id,
                "user_id": getattr(doc, "user_id", None),
                "is_summary": True,
            },
            client=chroma_client,
        )

    _trigger_snapshot_rebuild_async(
        module,
        project_id=project_id,
        user_id=getattr(doc, "user_id", None),
        reason="document_updated",
        db=db,
    )
    return doc


def delete_document_impl(module, doc_id: int, db: Session):
    """Delete a document and clean linked indexes."""
    repo = KnowledgeDocumentRepository(db)
    doc = repo.get_by_id(doc_id)
    if not doc:
        doc = repo.get_by_project_specific_id(doc_id)
    if not doc:
        return False

    doc_type = doc.doc_type
    project_id = doc.project_id
    doc_global_id = doc.id

    linked_docs = repo.list_linked_by_source(doc.id)
    for linked_doc in linked_docs:
        linked_doc.source_doc_id = None

    repo.delete(doc)
    repo.commit()

    module.reindex_project_specific_ids(doc_type, project_id, db)
    delete_document_indexes(doc_global_id, client=chroma_client)
    _trigger_snapshot_rebuild_async(
        module,
        project_id=project_id,
        user_id=getattr(doc, "user_id", None),
        reason="document_deleted",
        db=db,
    )
    return True


def move_document_impl(
    project_id: int,
    doc_id: int,
    anchor_doc_id: int,
    position: str,
    db: Session,
):
    """Move a document around an anchor by display order."""
    repo = KnowledgeDocumentRepository(db)
    target_doc = repo.get_project_doc(project_id=project_id, doc_id=doc_id)
    anchor_doc = repo.get_project_doc(project_id=project_id, doc_id=anchor_doc_id)

    if not target_doc or not anchor_doc:
        return False

    if target_doc.id == anchor_doc.id:
        return True

    new_order = 0.0

    if position == "before":
        upper_neighbor = repo.get_upper_neighbor(
            project_id=project_id,
            anchor_display_order=float(anchor_doc.display_order),
        )

        if upper_neighbor:
            new_order = (anchor_doc.display_order + upper_neighbor.display_order) / 2.0
        else:
            new_order = anchor_doc.display_order + 10.0
    else:
        lower_neighbor = repo.get_lower_neighbor(
            project_id=project_id,
            anchor_display_order=float(anchor_doc.display_order),
        )

        if lower_neighbor:
            new_order = (anchor_doc.display_order + lower_neighbor.display_order) / 2.0
        else:
            new_order = anchor_doc.display_order - 10.0

    target_doc.display_order = new_order
    repo.commit()
    return True


def reorder_documents_impl(project_id: int, ordered_ids: list[int], db: Session):
    """Reorder documents in batch using existing order slots."""
    if not ordered_ids:
        return True

    repo = KnowledgeDocumentRepository(db)
    docs = repo.list_project_docs_by_ids(project_id=project_id, doc_ids=ordered_ids)

    if not docs:
        return True

    doc_map = {doc.id: doc for doc in docs}
    current_orders = sorted([doc.display_order for doc in docs], reverse=True)

    for i in range(1, len(current_orders)):
        if current_orders[i] >= current_orders[i - 1]:
            current_orders[i] = current_orders[i - 1] - 1.0

    for i, doc_id in enumerate(ordered_ids):
        if doc_id in doc_map:
            doc_map[doc_id].display_order = current_orders[i]

    repo.commit()
    return True
