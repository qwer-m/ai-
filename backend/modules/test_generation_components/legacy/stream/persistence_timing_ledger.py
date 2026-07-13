from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .persistence_timing_events import sum_timing_duration as _sum_timing_duration


@dataclass(frozen=True)
class StreamTimingLedger:
    duration_by_stage_ms: dict[str, int]
    payload: dict[str, Any] | None


def build_stream_timing_ledger(
    *,
    generation_timing_events: list[dict[str, Any]],
    generation_id: int | None,
    project_id: int,
    request_id: str,
    generation_mode: str,
    multi_pass: bool,
) -> StreamTimingLedger:
    events = list(generation_timing_events or [])
    duration_by_stage_ms = {
        "prepare_total": _sum_timing_duration(events, "prepare_total"),
        "client_resolution": _sum_timing_duration(events, "client_resolution"),
        "linked_final_case_signal": _sum_timing_duration(events, "linked_final_case_signal"),
        "append_existing_lookup": _sum_timing_duration(events, "append_existing_lookup"),
        "snapshot_gate": _sum_timing_duration(events, "snapshot_gate"),
        "hybrid_context": _sum_timing_duration(events, "hybrid_context"),
        "feedback_control_state": _sum_timing_duration(events, "feedback_control_state"),
        "current_requirement_blueprint": _sum_timing_duration(events, "current_requirement_blueprint"),
        "requirement_compress": _sum_timing_duration(
            events,
            "requirement_compress",
            "long_requirement_compress",
        ),
        "kb_context_compress": _sum_timing_duration(events, "kb_context_compress"),
        "meta_analysis": _sum_timing_duration(events, "meta_analysis"),
        "primary": _sum_timing_duration(events, "primary_batches"),
        "stream_generation_phase": _sum_timing_duration(events, "stream_generation_phase"),
        "gap": _sum_timing_duration(events, "gap_supplement"),
        "review": _sum_timing_duration(events, "review_selection"),
        "final_shortfall": _sum_timing_duration(events, "final_shortfall_supplement"),
        "postprocess_total": _sum_timing_duration(events, "postprocess_total"),
    }
    if not events:
        return StreamTimingLedger(
            duration_by_stage_ms=duration_by_stage_ms,
            payload=None,
        )

    return StreamTimingLedger(
        duration_by_stage_ms=duration_by_stage_ms,
        payload={
            "kind": "generation_timing_ledger",
            "schema_version": "1.0",
            "generation_id": int(generation_id or 0),
            "project_id": int(project_id),
            "request_id": request_id,
            "mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
            "multi_pass": bool(multi_pass),
            "duration_by_stage_ms": duration_by_stage_ms,
            "event_count": int(len(events)),
            "events": events,
        },
    )
