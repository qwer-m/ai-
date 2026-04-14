from __future__ import annotations

from modules.memory_fabric.contracts import MemoryFabric
from modules.memory_fabric.runtime.default_memory_fabric import DefaultMemoryFabric

_MEMORY_FABRIC_SINGLETON: MemoryFabric | None = None


def get_memory_fabric() -> MemoryFabric:
    global _MEMORY_FABRIC_SINGLETON
    if _MEMORY_FABRIC_SINGLETON is None:
        _MEMORY_FABRIC_SINGLETON = DefaultMemoryFabric()
    return _MEMORY_FABRIC_SINGLETON

