import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument

COMPARE_ARTIFACT_DOC_TYPE = "evaluation_compare_artifact"


def build_compare_artifact_filename(generation_id: int) -> str:
    return f"evaluation_compare_artifact_gen_{generation_id}.json"


def upsert_compare_artifact(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    generation_id: int,
    payload: dict[str, Any],
) -> KnowledgeDocument:
    filename = build_compare_artifact_filename(generation_id)
    normalized_payload = dict(payload or {})
    normalized_payload["generation_id"] = generation_id
    normalized_payload["updated_at"] = datetime.utcnow().isoformat()
    content = json.dumps(normalized_payload, ensure_ascii=False)

    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.user_id == user_id,
            KnowledgeDocument.doc_type == COMPARE_ARTIFACT_DOC_TYPE,
            KnowledgeDocument.filename == filename,
        )
        .order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id))
        .first()
    )

    if doc:
        doc.content = content
        doc.parse_status = "success"
        doc.parse_error = None
        db.commit()
        db.refresh(doc)
        return doc

    doc = KnowledgeDocument(
        project_id=project_id,
        user_id=user_id,
        filename=filename,
        content=content,
        doc_type=COMPARE_ARTIFACT_DOC_TYPE,
        parse_status="success",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def load_compare_artifact_payload(
    *,
    db: Session,
    project_id: int,
    user_id: int,
    generation_id: int,
) -> Optional[dict[str, Any]]:
    filename = build_compare_artifact_filename(generation_id)
    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.user_id == user_id,
            KnowledgeDocument.doc_type == COMPARE_ARTIFACT_DOC_TYPE,
            KnowledgeDocument.filename == filename,
        )
        .order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id))
        .first()
    )
    if not doc:
        return None
    try:
        payload = json.loads(doc.content or "{}")
        if not isinstance(payload, dict):
            return None
        payload["artifact_doc_id"] = doc.id
        payload["artifact_filename"] = doc.filename
        return payload
    except Exception:
        return None
