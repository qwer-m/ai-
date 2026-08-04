"""Vector-store abstraction for knowledge-base retrieval and indexing."""

from __future__ import annotations

from typing import Any, Protocol


class VectorStorePort(Protocol):
    """Protocol for vector index operations used by RAG pipelines."""

    def is_ready(self) -> bool:
        ...

    def search(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, Any] | None = None,
        raise_on_error: bool = False,
    ) -> dict[str, Any]:
        ...

    def search_by_metadata(
        self,
        *,
        where: dict[str, Any],
        n_results: int,
        raise_on_error: bool = False,
    ) -> dict[str, Any]:
        ...

    def add_document(
        self,
        *,
        doc_id: str,
        metadata: dict[str, Any],
        chunks: list[dict[str, Any]],
        raise_on_error: bool = False,
    ) -> None:
        ...

    def delete_document(self, doc_id: str, *, raise_on_error: bool = False) -> None:
        ...

