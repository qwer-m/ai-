from __future__ import annotations

import time
from typing import Any


def record_timing_event(
    timing_events: list[dict[str, Any]],
    stage: str,
    started_at: float,
    **fields: Any,
) -> dict[str, Any]:
    event = {
        "stage": str(stage or "unknown"),
        "duration_ms": max(0, int(round((time.perf_counter() - started_at) * 1000))),
    }
    for key, value in fields.items():
        if value is not None:
            event[key] = value
    timing_events.append(event)
    return event
