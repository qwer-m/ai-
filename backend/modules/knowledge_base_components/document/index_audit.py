"""Knowledge index consistency audit helpers."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from core.db.model_defs import KnowledgeDocument
from modules.knowledge_base_components.adapters.chroma_vector_store import get_vector_store
from modules.knowledge_base_components.document.document_ops import INDEXABLE_DOC_TYPES
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)


def _has_summary_index(doc: KnowledgeDocument) -> bool:
    summary = str(doc.summary or "").strip()
    content = str(doc.content or "").strip()
    return bool(summary and summary != content)


def _fetch_chroma_metas(doc_id: int, *, vector_store) -> list[dict[str, Any]]:
    if not vector_store.is_ready():
        return []

    normalized: list[dict[str, Any]] = []
    # 原文和摘要共享同一个 metadata.doc_id，依靠 is_summary 区分索引通道。
    # 不能按“<doc_id>_summary”查询 metadata，也不能先无差别截取前 100 个
    # chunk；大文档的原文 chunk 会把摘要 chunk 挤出结果，造成假缺失。
    for is_summary in (False, True):
        try:
            result = vector_store.search_by_metadata(
                where={
                    "$and": [
                        {"doc_id": str(doc_id)},
                        {"is_summary": is_summary},
                    ]
                },
                n_results=1,
                raise_on_error=False,
            )
        except Exception:
            continue
        metas = result.get("metadatas") or []
        if metas and isinstance(metas[0], list):
            metas = metas[0]
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
    repo = KnowledgeDocumentRepository(db)
    vector_store = get_vector_store()
    docs = repo.list_for_index_audit(project_id=project_id, user_id=user_id, limit=limit)

    missing_raw: list[int] = []
    missing_summary: list[int] = []
    content_hash_mismatch: list[int] = []
    stale_index_docs: list[int] = []
    checked_docs = 0
    checked_indexable_docs = 0

    for doc in docs:
        checked_docs += 1
        if str(doc.doc_type or "") not in INDEXABLE_DOC_TYPES:
            continue
        checked_indexable_docs += 1

        metas = _fetch_chroma_metas(int(doc.id), vector_store=vector_store)
        has_any_index = bool(metas)
        has_raw = any(not bool(m.get("is_summary")) for m in metas)
        has_summary = any(bool(m.get("is_summary")) for m in metas)
        stored_hash = str(doc.content_hash or "").strip()
        lane_hashes = {
            str(m.get("content_hash") or "").strip()
            for m in metas
            if isinstance(m, dict)
        }
        expect_summary = _has_summary_index(doc)
        is_success = str(doc.parse_status or "").strip().lower() == "success"

        if is_success:
            if not has_raw:
                missing_raw.append(int(doc.id))
            if expect_summary and not has_summary:
                missing_summary.append(int(doc.id))
            if stored_hash and has_raw and stored_hash not in lane_hashes:
                content_hash_mismatch.append(int(doc.id))
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
            "content_hash_mismatch_count": len(content_hash_mismatch),
            "stale_index_count": len(stale_index_docs),
        },
        "samples": {
            "missing_raw_doc_ids": missing_raw[:30],
            "missing_summary_doc_ids": missing_summary[:30],
            "content_hash_mismatch_doc_ids": content_hash_mismatch[:30],
            "stale_index_doc_ids": stale_index_docs[:30],
        },
        "healthy": (
            len(missing_raw) == 0
            and len(missing_summary) == 0
            and len(content_hash_mismatch) == 0
            and len(stale_index_docs) == 0
        ),
    }
