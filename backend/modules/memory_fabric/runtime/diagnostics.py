from __future__ import annotations

from typing import Any


def init_memory_diag() -> dict[str, Any]:
    return {
        "memory_fabric_used": False,
        "memory_reads": {
            "working": 0,
            "episodic": 0,
            "semantic": 0,
            "rule": 0,
        },
    }


def mark_memory_fabric_used(diag: dict[str, Any] | None) -> None:
    if not isinstance(diag, dict):
        return
    diag["memory_fabric_used"] = True


def record_memory_read(diag: dict[str, Any] | None, layer: str, via_memory_fabric: bool = False) -> None:
    if not isinstance(diag, dict):
        return
    reads = diag.get("memory_reads")
    if not isinstance(reads, dict):
        reads = {"working": 0, "episodic": 0, "semantic": 0, "rule": 0}
        diag["memory_reads"] = reads
    key = str(layer or "").strip().lower()
    if key not in reads:
        reads[key] = 0
    reads[key] = int(reads.get(key) or 0) + 1
    if via_memory_fabric:
        diag["memory_fabric_used"] = True

