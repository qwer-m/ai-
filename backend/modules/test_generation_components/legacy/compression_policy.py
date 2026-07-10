from __future__ import annotations

import os
from typing import Any


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        value = int(str(raw).strip())
    except ValueError:
        return int(default)
    return max(int(minimum), int(value))


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        value = float(str(raw).strip())
    except ValueError:
        return float(default)
    return max(float(minimum), float(value))


def requirement_compression_decision(
    requirement: Any,
    *,
    compress_requested: bool,
    min_chars: int | None = None,
    near_threshold_ratio: float | None = None,
) -> dict[str, Any]:
    text = str(requirement or "")
    threshold = (
        int(min_chars)
        if min_chars is not None
        else _env_int("GENERATION_REQUIREMENT_COMPRESSION_MIN_CHARS", 12000, minimum=0)
    )
    ratio = (
        max(1.0, float(near_threshold_ratio))
        if near_threshold_ratio is not None
        else _env_float("GENERATION_REQUIREMENT_COMPRESSION_NEAR_THRESHOLD_RATIO", 1.2, minimum=1.0)
    )
    char_count = int(len(text))
    if not compress_requested:
        return {
            "should_compress": False,
            "reason": "not_requested",
            "char_count": char_count,
            "min_chars": threshold,
        }
    if threshold > 0 and char_count < threshold:
        return {
            "should_compress": False,
            "reason": "below_min_chars",
            "char_count": char_count,
            "min_chars": threshold,
        }
    near_threshold_chars = int(round(float(threshold) * float(ratio))) if threshold > 0 else 0
    if threshold > 0 and ratio > 1.0 and char_count < near_threshold_chars:
        return {
            "should_compress": False,
            "reason": "near_threshold",
            "char_count": char_count,
            "min_chars": threshold,
            "near_threshold_chars": near_threshold_chars,
            "near_threshold_ratio": ratio,
        }
    return {
        "should_compress": True,
        "reason": "requested",
        "char_count": char_count,
        "min_chars": threshold,
    }
