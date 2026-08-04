"""Shared document vector index operations."""

from __future__ import annotations

from typing import Any, Optional

from core.processing.biz_key_extractor import extract_biz_key
from core.processing.business_chunking import BusinessChunkerDispatcher
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


def build_document_index_chunks(
    *,
    content: str,
    doc_type: str,
    default_module: str | None = None,
    default_biz_key: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, str]:
    """按文档类型生成向量索引分块及业务元数据。"""
    chunk_objects = BusinessChunkerDispatcher().chunk(doc_type, content)
    if not chunk_objects:
        raise ValueError(f"文档未生成可索引分块：doc_type={doc_type}")

    module_hint = default_module or next(
        (
            str(item.module).strip()
            for item in chunk_objects
            if getattr(item, "module", None)
        ),
        None,
    )
    biz_key = str(default_biz_key or "").strip() or extract_biz_key(content, module_hint or "")

    payloads: list[dict[str, Any]] = []
    for item in chunk_objects:
        chunk_text = str(getattr(item, "text", "") or "").strip()
        if not chunk_text:
            continue
        module_value = str(getattr(item, "module", "") or "").strip() or module_hint
        biz_key_value = str(getattr(item, "biz_key", "") or "").strip() or biz_key
        requirement_id = str(getattr(item, "requirement_id", "") or "").strip() or None
        test_case_id = str(getattr(item, "test_case_id", "") or "").strip() or None
        payloads.append(
            {
                "chunk_text": chunk_text,
                "metadata": {
                    "module": module_value,
                    "biz_key": biz_key_value,
                    "requirement_id": requirement_id,
                    "test_case_id": test_case_id,
                    "related_ids": [
                        item_id
                        for item_id in (requirement_id, test_case_id)
                        if item_id
                    ],
                },
            }
        )
    return payloads, module_hint, biz_key


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
    chunks: list[dict[str, Any]],
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
    if not chunks:
        raise ValueError(f"原文索引分块不能为空：doc_id={normalized_id}")
    delete_document_indexes(normalized_id, raise_on_error=raise_on_error, vector_store=active)

    active.add_document(
        doc_id=normalized_id,
        metadata=metadata,
        chunks=chunks,
        raise_on_error=raise_on_error,
    )
    indexed_raw = True

    summary = str(summary_text or "").strip()
    if summary and summary != str(content or ""):
        if not summary_chunks:
            raise ValueError(f"摘要索引分块不能为空：doc_id={normalized_id}")
        active.add_document(
            doc_id=f"{normalized_id}_summary",
            metadata=summary_metadata or metadata,
            chunks=summary_chunks,
            raise_on_error=raise_on_error,
        )
        return indexed_raw, True
    return indexed_raw, False
