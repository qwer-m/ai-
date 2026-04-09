"""Shared document vector index operations."""

from __future__ import annotations

from typing import Any, Optional

from modules.knowledge_base_components.adapters.chroma_vector_store import get_vector_store
from modules.knowledge_base_components.ports.vector_store_port import VectorStorePort


def _resolve_vector_store(
    *,
    vector_store: Optional[VectorStorePort] = None,
    client=None,
) -> VectorStorePort:
    return vector_store or get_vector_store(client=client)


def is_vector_store_ready(*, client=None, vector_store: Optional[VectorStorePort] = None) -> bool:
    return _resolve_vector_store(vector_store=vector_store, client=client).is_ready()


def delete_document_indexes(
    doc_id: int | str,
    *,
    raise_on_error: bool = False,
    client=None,
    vector_store: Optional[VectorStorePort] = None,
) -> None:
    """Delete raw and summary indexes for one document id."""
    active = _resolve_vector_store(vector_store=vector_store, client=client)
    normalized_id = str(doc_id)
    active.delete_document(normalized_id, raise_on_error=raise_on_error)
    active.delete_document(f"{normalized_id}_summary", raise_on_error=raise_on_error)


def upsert_document_indexes(
    *,
    doc_id: int | str,
    content: str,
    metadata: dict[str, Any],
    summary_text: str = "",
    summary_metadata: Optional[dict[str, Any]] = None,
    chunks: Optional[list[dict[str, Any]]] = None,
    summary_chunks: Optional[list[dict[str, Any]]] = None,
    raise_on_error: bool = False,
    client=None,
    vector_store: Optional[VectorStorePort] = None,
) -> tuple[bool, bool]:
    """
    Upsert raw and summary vector indexes.

    Returns (indexed_raw, indexed_summary).
    """
    active = _resolve_vector_store(vector_store=vector_store, client=client)
    normalized_id = str(doc_id)
    delete_document_indexes(normalized_id, raise_on_error=raise_on_error, vector_store=active)

    active.add_document(
        doc_id=normalized_id,
        content=content,
        metadata=metadata,
        chunks=chunks,
        raise_on_error=raise_on_error,
    )
    indexed_raw = True

    summary = str(summary_text or "").strip()
    if summary and summary != str(content or ""):
        active.add_document(
            doc_id=f"{normalized_id}_summary",
            content=summary,
            metadata=summary_metadata or metadata,
            chunks=summary_chunks,
            raise_on_error=raise_on_error,
        )
        return indexed_raw, True
    return indexed_raw, False
