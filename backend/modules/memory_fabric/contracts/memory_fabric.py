from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from modules.memory_fabric.contracts.memory_context import MemoryContext


class MemoryFabric(ABC):
    """Unified memory contract for L0/L1/L2/L3."""

    @abstractmethod
    def read_working(self, key: str, ctx: MemoryContext) -> Any:
        raise NotImplementedError

    @abstractmethod
    def write_working(self, key: str, value: Any, ctx: MemoryContext, ttl: int | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_episodic(self, query: Any, ctx: MemoryContext) -> Any:
        raise NotImplementedError

    @abstractmethod
    def write_episodic(self, record: Any, ctx: MemoryContext) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_semantic(self, query: Any, ctx: MemoryContext) -> Any:
        raise NotImplementedError

    @abstractmethod
    def write_semantic(self, doc: Any, ctx: MemoryContext) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_rule(self, query: Any, ctx: MemoryContext) -> Any:
        raise NotImplementedError

    @abstractmethod
    def write_rule(self, rule_state: Any, ctx: MemoryContext) -> None:
        raise NotImplementedError

