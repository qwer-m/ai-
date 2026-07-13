"""Chroma-backed implementation of the vector-store port."""

from __future__ import annotations

from typing import Any

from core.cache_layer.chroma_client import chroma_client
from modules.knowledge_base_components.ports.vector_store_port import VectorStorePort


class ChromaVectorStore(VectorStorePort):
    """Compatibility wrapper over the existing chroma client."""

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
        try:
            return self._client.search(
                query=query,
                n_results=n_results,
                where=where,
                raise_on_error=raise_on_error,
            )
        except TypeError:
            return self._client.search(
                query=query,
                n_results=n_results,
                where=where,
            )

    def search_by_metadata(
        self,
        *,
        where: dict[str, Any],
        n_results: int,
        raise_on_error: bool = False,
    ) -> dict[str, Any]:
        try:
            return self._client.search_by_metadata(
                where=where,
                n_results=n_results,
                raise_on_error=raise_on_error,
            )
        except TypeError:
            return self._client.search_by_metadata(where=where, n_results=n_results)

    def add_document(
        self,
        *,
        doc_id: str,
        content: str,
        metadata: dict[str, Any],
        chunks: list[dict[str, Any]] | None = None,
        raise_on_error: bool = False,
    ) -> None:
        try:
            self._client.add_document(
                doc_id=doc_id,
                content=content,
                metadata=metadata,
                chunks=chunks,
                raise_on_error=raise_on_error,
            )
        except TypeError:
            self._client.add_document(
                doc_id=doc_id,
                content=content,
                metadata=metadata,
                chunks=chunks,
            )

    def delete_document(self, doc_id: str, *, raise_on_error: bool = False) -> None:
        try:
            self._client.delete_document(doc_id, raise_on_error=raise_on_error)
        except TypeError:
            self._client.delete_document(doc_id)


def get_vector_store(client=None) -> VectorStorePort:
    """Build a vector-store adapter from a concrete client."""
    return ChromaVectorStore(client=client)

