"""Chroma-backed implementation of the vector-store port."""

from __future__ import annotations

from typing import Any

from core.cache_layer.chroma_client import chroma_client
from modules.knowledge_base_components.ports.vector_store_port import VectorStorePort


class ChromaVectorStore(VectorStorePort):
    """Chroma 客户端的向量存储端口实现。"""

    def __init__(self, client=None) -> None:
        self._client = client or chroma_client

    def is_ready(self) -> bool:
        return bool(getattr(self._client, "collection", None))

    def search(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, Any] | None = None,
        raise_on_error: bool = False,
    ) -> dict[str, Any]:
        return self._client.search(
            query=query,
            n_results=n_results,
            where=where,
            raise_on_error=raise_on_error,
        )

    def search_by_metadata(
        self,
        *,
        where: dict[str, Any],
        n_results: int,
        raise_on_error: bool = False,
    ) -> dict[str, Any]:
        return self._client.search_by_metadata(
            where=where,
            n_results=n_results,
            raise_on_error=raise_on_error,
        )

    def add_document(
        self,
        *,
        doc_id: str,
        metadata: dict[str, Any],
        chunks: list[dict[str, Any]],
        raise_on_error: bool = False,
    ) -> None:
        self._client.add_document(
            doc_id=doc_id,
            metadata=metadata,
            chunks=chunks,
            raise_on_error=raise_on_error,
        )

    def delete_document(self, doc_id: str, *, raise_on_error: bool = False) -> None:
        self._client.delete_document(doc_id, raise_on_error=raise_on_error)


def get_vector_store(client=None) -> VectorStorePort:
    """Build a vector-store adapter from a concrete client."""
    return ChromaVectorStore(client=client)

