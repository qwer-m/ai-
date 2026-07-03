from __future__ import annotations

import re
from typing import Any

from .feedback_control_sample_access import (
    normalize_pattern_usage as _normalize_pattern_usage,
    normalize_signal_type as _normalize_signal_type,
    safe_float as _safe_float,
    sample_value as _sample_value,
)


def _is_manual_verified_sample(
    *,
    reason: str,
    pattern_category: str,
    expected_priority: str,
    comment: str,
) -> bool:
    if reason:
        return True
    if pattern_category:
        return True
    if expected_priority in {"P0", "P1", "P2", "P3"}:
        return True
    return len(str(comment or "").strip()) >= 6


_NEGATIVE_SIGNAL_KEYS = (
    "signal_type",
    "signalType",
    "pattern_signal_type",
    "patternSignalType",
    "feedback_direction",
    "feedbackDirection",
    "sample_type",
    "sampleType",
    "sample_kind",
    "sampleKind",
)
_NEGATIVE_SIGNAL_MARKERS = {
    "negative",
    "neg",
    "bad",
    "error",
    "anomaly",
    "anti_pattern",
    "antipattern",
    "forbidden",
    "avoid",
    "problem",
    "异常",
    "负向",
    "反例",
}


def _has_explicit_negative_signal(sample_like: dict[str, Any]) -> bool:
    for key in _NEGATIVE_SIGNAL_KEYS:
        raw = str(_sample_value(sample_like, key) or "").strip().lower()
        if not raw:
            continue
        compact = re.sub(r"[\s\-_]+", "", raw)
        if raw in _NEGATIVE_SIGNAL_MARKERS or compact in _NEGATIVE_SIGNAL_MARKERS:
            return True
        if ("negative" in raw) or ("异常" in raw) or ("负向" in raw):
            return True
    return False


def _is_manual_verified_negative_sample(
    *,
    sample: dict[str, Any],
    reason: str,
    expected_priority: str,
    comment: str,
) -> bool:
    if reason:
        return True
    if str(comment or "").strip():
        return True
    if expected_priority in {"P0", "P1", "P2", "P3"}:
        return True
    return _has_explicit_negative_signal(sample)


def _is_pattern_active(sample_like: dict[str, Any]) -> bool:
    status = str(
        _sample_value(sample_like, "governance_status", "pattern_status", "patternStatus")
        or ""
    ).strip().lower()
    return status != "disabled"


def _pattern_confidence(sample_like: dict[str, Any]) -> float:
    raw = _sample_value(sample_like, "pattern_confidence", "patternConfidence")
    if raw in (None, ""):
        return 1.0
    return max(0.0, min(1.0, _safe_float(raw, default=1.0)))


def _is_preferred_signal_sample(sample_like: dict[str, Any]) -> bool:
    signal_type = _normalize_signal_type(
        _sample_value(
            sample_like,
            "signal_type",
            "signalType",
            "pattern_signal_type",
            "patternSignalType",
            "feedback_direction",
            "feedbackDirection",
            "sample_type",
            "sampleType",
            "sample_kind",
            "sampleKind",
        )
    )
    pattern_usage = _normalize_pattern_usage(
        _sample_value(sample_like, "pattern_usage", "patternUsage"),
        signal_type=signal_type,
    )
    return bool(signal_type == "positive" or pattern_usage == "prefer")


def _count_signal_split(samples: list[dict[str, Any]]) -> tuple[int, int]:
    positive = sum(1 for item in samples if _is_preferred_signal_sample(item))
    negative = int(len(samples) - positive)
    return int(positive), int(negative)
