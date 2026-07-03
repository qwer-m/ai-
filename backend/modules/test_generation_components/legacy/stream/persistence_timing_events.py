from __future__ import annotations

from typing import Any


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return int(default)


def sanitize_timing_events(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        event: dict[str, Any] = {}
        for key, value in item.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            if isinstance(value, bool):
                event[key_text] = bool(value)
            elif isinstance(value, int):
                event[key_text] = int(value)
            elif isinstance(value, float):
                event[key_text] = round(float(value), 4)
            elif value is None:
                event[key_text] = None
            else:
                event[key_text] = str(value)[:300]
        if event.get("stage"):
            event["duration_ms"] = max(0, _to_int(event.get("duration_ms")))
            sanitized.append(event)
    return sanitized


def sum_timing_duration(events: list[dict[str, Any]], *stages: str) -> int:
    stage_set = {str(stage or "").strip() for stage in stages if str(stage or "").strip()}
    return int(
        sum(
            _to_int(item.get("duration_ms"))
            for item in events
            if str(item.get("stage") or "").strip() in stage_set
        )
    )


__all__ = [
    "sanitize_timing_events",
    "sum_timing_duration",
]
