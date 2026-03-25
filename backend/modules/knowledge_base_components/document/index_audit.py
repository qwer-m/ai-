"""Knowledge index consistency audit helpers."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from core.cache_layer.chroma_client import chroma_client
from core.db.models import KnowledgeDocument
from modules.knowledge_base_components.document.document_ops import INDEXABLE_DOC_TYPES
from modules.domain.stage25_switches import STAGE25_SWITCHES


def _has_summary_index(doc: KnowledgeDocument) -> bool:
    summary = str(doc.summary or "").strip()
    content = str(doc.content or "").strip()
    return bool(summary and summary != content)


def _fetch_chroma_metas(doc_id: int) -> list[dict[str, Any]]:
    if not getattr(chroma_client, "collection", None):
        return []

    normalized: list[dict[str, Any]] = []
    # Compatibility: legacy summary index may use doc_id={id}_summary.
    legacy_keys = [str(doc_id), f"{doc_id}_summary"]
    for key in legacy_keys:
        try:
            result = chroma_client.collection.get(
                where={"doc_id": key},
                include=["metadatas"],
            )
        except Exception:
            continue
        metas = result.get("metadatas") or []
        for item in metas:
            if isinstance(item, dict):
                normalized.append(item)

    return normalized


def run_index_consistency_audit(
    *,
    db: Session,
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    limit: int = 5000,
) -> dict[str, Any]:
    """Audit DB/chroma consistency for raw/summary indexes."""
    if not STAGE25_SWITCHES.index_audit_enabled:
        return {"enabled": False, "message": "index_audit_disabled"}

    query = db.query(KnowledgeDocument)
    if project_id is not None:
        query = query.filter(KnowledgeDocument.project_id == project_id)
    if user_id is not None:
        query = query.filter(KnowledgeDocument.user_id == user_id)
    docs = (
        query.order_by(KnowledgeDocument.id.asc())
        .limit(max(100, int(limit)))
        .all()
    )

    missing_raw: list[int] = []
    missing_summary: list[int] = []
    stale_index_docs: list[int] = []
    checked_docs = 0
    checked_indexable_docs = 0

    for doc in docs:
        checked_docs += 1
        if str(doc.doc_type or "") not in INDEXABLE_DOC_TYPES:
            continue
        checked_indexable_docs += 1

        metas = _fetch_chroma_metas(int(doc.id))
        has_any_index = bool(metas)
        has_raw = any(not bool(m.get("is_summary")) for m in metas)
        has_summary = any(bool(m.get("is_summary")) for m in metas)
        expect_summary = _has_summary_index(doc)
        is_success = str(doc.parse_status or "").strip().lower() == "success"

        if is_success:
            if not has_raw:
                missing_raw.append(int(doc.id))
            if expect_summary and not has_summary:
                missing_summary.append(int(doc.id))
        else:
            if has_any_index:
                stale_index_docs.append(int(doc.id))

    return {
        "enabled": True,
        "scope": {"project_id": project_id, "user_id": user_id, "limit": int(limit)},
        "checked_docs": checked_docs,
        "checked_indexable_docs": checked_indexable_docs,
        "issues": {
            "missing_raw_index_count": len(missing_raw),
            "missing_summary_index_count": len(missing_summary),
            "stale_index_count": len(stale_index_docs),
        },
        "samples": {
            "missing_raw_doc_ids": missing_raw[:30],
            "missing_summary_doc_ids": missing_summary[:30],
            "stale_index_doc_ids": stale_index_docs[:30],
        },
        "healthy": len(missing_raw) == 0 and len(missing_summary) == 0 and len(stale_index_docs) == 0,
    }
