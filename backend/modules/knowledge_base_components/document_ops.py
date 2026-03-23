"""Knowledge base document operations."""

# Keep facade logic split out of knowledge_base.py.


from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.chroma_client import chroma_client
from core.models import KnowledgeDocument

# Only these doc types are written to vector index.
INDEXABLE_DOC_TYPES = (
    "requirement",
    "product_requirement",
    "incomplete",
    "evaluation_report",
    "agent_learning",
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
    content_hash = module.calculate_hash(content)

    existing = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.content_hash == content_hash,
        KnowledgeDocument.project_id == project_id,
    ).first()

    if existing:
        if not force:
            return {
                "status": "duplicate",
                "existing_filename": existing.filename,
                "existing_doc_id": existing.id,
            }
        return existing

    min_order = db.query(func.min(KnowledgeDocument.display_order)).filter(
        KnowledgeDocument.project_id == project_id
    ).scalar()
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
    db.add(doc)
    db.commit()
    db.refresh(doc)

    module.reindex_project_specific_ids(doc_type, project_id, db)
    db.refresh(doc)

    summary = ""
    try:
        summary = module._ensure_summary(doc, db, user_id)
    except Exception:
        pass

    if doc_type in INDEXABLE_DOC_TYPES:
        chroma_client.add_document(
            doc_id=str(doc.id),
            content=content,
            metadata={
                "project_id": project_id,
                "doc_type": doc_type,
                "filename": filename,
                "doc_id": doc.id,
                "user_id": user_id,
                "is_summary": False,
            },
        )

        if summary and summary != content:
            chroma_client.add_document(
                doc_id=f"{doc.id}_summary",
                content=summary,
                metadata={
                    "project_id": project_id,
                    "doc_type": doc_type,
                    "filename": f"{filename} (Summary)",
                    "doc_id": doc.id,
                    "user_id": user_id,
                    "is_summary": True,
                },
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
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_specific_id == doc_id).first()
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

    db.commit()

    if original_doc_type != doc_type:
        module.reindex_project_specific_ids(original_doc_type, project_id, db)
        module.reindex_project_specific_ids(doc_type, project_id, db)
    else:
        module.reindex_project_specific_ids(doc_type, project_id, db)

    db.refresh(doc)

    try:
        module._ensure_summary(doc, db, getattr(doc, "user_id", None))
    except Exception:
        pass

    if content_changed and doc.doc_type in INDEXABLE_DOC_TYPES:
        # Compatibility cleanup: legacy summary index might use doc_id={id}_summary.
        chroma_client.delete_document(str(doc.id))
        chroma_client.delete_document(f"{doc.id}_summary")

        summary_text = str(doc.summary or "").strip()
        chroma_client.add_document(
            doc_id=str(doc.id),
            content=content,
            metadata={
                "project_id": project_id,
                "doc_type": doc.doc_type,
                "filename": filename or doc.filename,
                "doc_id": doc.id,
                "user_id": getattr(doc, "user_id", None),
                "is_summary": False,
            },
        )
        if summary_text and summary_text != content:
            chroma_client.add_document(
                doc_id=f"{doc.id}_summary",
                content=summary_text,
                metadata={
                    "project_id": project_id,
                    "doc_type": doc.doc_type,
                    "filename": f"{(filename or doc.filename)} (Summary)",
                    "doc_id": doc.id,
                    "user_id": getattr(doc, "user_id", None),
                    "is_summary": True,
                },
            )

    return doc


def delete_document_impl(module, doc_id: int, db: Session):
    """Delete a document and clean linked indexes."""
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_specific_id == doc_id).first()
    if not doc:
        doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        return False

    doc_type = doc.doc_type
    project_id = doc.project_id
    doc_global_id = doc.id

    linked_docs = db.query(KnowledgeDocument).filter(KnowledgeDocument.source_doc_id == doc.id).all()
    for linked_doc in linked_docs:
        linked_doc.source_doc_id = None

    db.delete(doc)
    db.commit()

    module.reindex_project_specific_ids(doc_type, project_id, db)
    chroma_client.delete_document(str(doc_global_id))
    return True


def move_document_impl(
    project_id: int,
    doc_id: int,
    anchor_doc_id: int,
    position: str,
    db: Session,
):
    """Move a document around an anchor by display order."""
    target_doc = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.id == doc_id,
        KnowledgeDocument.project_id == project_id,
    ).first()

    anchor_doc = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.id == anchor_doc_id,
        KnowledgeDocument.project_id == project_id,
    ).first()

    if not target_doc or not anchor_doc:
        return False

    if target_doc.id == anchor_doc.id:
        return True

    new_order = 0.0

    if position == "before":
        upper_neighbor = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.display_order > anchor_doc.display_order,
        ).order_by(KnowledgeDocument.display_order.asc()).first()

        if upper_neighbor:
            new_order = (anchor_doc.display_order + upper_neighbor.display_order) / 2.0
        else:
            new_order = anchor_doc.display_order + 10.0
    else:
        lower_neighbor = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.display_order < anchor_doc.display_order,
        ).order_by(KnowledgeDocument.display_order.desc()).first()

        if lower_neighbor:
            new_order = (anchor_doc.display_order + lower_neighbor.display_order) / 2.0
        else:
            new_order = anchor_doc.display_order - 10.0

    target_doc.display_order = new_order
    db.commit()
    return True


def reorder_documents_impl(project_id: int, ordered_ids: list[int], db: Session):
    """Reorder documents in batch using existing order slots."""
    if not ordered_ids:
        return True

    docs = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.id.in_(ordered_ids),
        KnowledgeDocument.project_id == project_id,
    ).all()

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

    db.commit()
    return True
