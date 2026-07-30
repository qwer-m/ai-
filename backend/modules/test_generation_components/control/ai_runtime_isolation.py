from __future__ import annotations

import threading
from typing import Any


AI_CLIENT_RUNTIME_IDENTITY_ATTR = "isolated_runtime_identity_client"


def resolve_ai_client_runtime_identity(client: Any) -> Any:
    """解析观察包装器显式声明的底层 AIClient 身份。"""

    current = client
    seen: list[Any] = []
    while current is not None and not any(item is current for item in seen):
        seen.append(current)
        nested = getattr(current, AI_CLIENT_RUNTIME_IDENTITY_ATTR, None)
        if nested is None or nested is current:
            break
        current = nested
    return current


class AIRuntimeIsolationGuard:
    """线程安全地保证每个分片独占 AIClient、Provider 与 DB Session。"""

    def __init__(
        self,
        *,
        parent_client: Any,
        parent_db: Any,
        error_message: str,
    ) -> None:
        self._parent_client = resolve_ai_client_runtime_identity(parent_client)
        self._parent_db = parent_db
        self._parent_provider = getattr(self._parent_client, "provider", None)
        self._error_message = str(error_message)
        self._claimed_clients: list[Any] = []
        self._claimed_dbs: list[Any] = []
        self._claimed_providers: list[Any] = []
        self._lock = threading.Lock()

    def claim(self, *, client: Any, db: Any) -> None:
        client_identity = resolve_ai_client_runtime_identity(client)
        provider = getattr(client_identity, "provider", None)
        with self._lock:
            shares_runtime = (
                client_identity is self._parent_client
                or db is self._parent_db
                or any(
                    item is client_identity for item in self._claimed_clients
                )
                or any(item is db for item in self._claimed_dbs)
                or (
                    provider is not None
                    and (
                        provider is self._parent_provider
                        or any(
                            item is provider
                            for item in self._claimed_providers
                        )
                    )
                )
            )
            if shares_runtime:
                raise RuntimeError(self._error_message)
            self._claimed_clients.append(client_identity)
            self._claimed_dbs.append(db)
            if provider is not None:
                self._claimed_providers.append(provider)


__all__ = [
    "AI_CLIENT_RUNTIME_IDENTITY_ATTR",
    "AIRuntimeIsolationGuard",
    "resolve_ai_client_runtime_identity",
]
