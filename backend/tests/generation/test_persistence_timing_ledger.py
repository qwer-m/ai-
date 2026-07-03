from __future__ import annotations

from modules.test_generation_components.legacy.stream.persistence_timing_ledger import (
    build_stream_timing_ledger,
)


def test_build_stream_timing_ledger_aggregates_stage_durations_and_payload() -> None:
    ledger = build_stream_timing_ledger(
        generation_timing_events=[
            {"stage": "prepare_total", "duration_ms": 101},
            {"stage": "snapshot_gate", "duration_ms": 2},
            {"stage": "requirement_compress", "duration_ms": 23},
            {"stage": "long_requirement_compress", "duration_ms": 29},
            {"stage": "primary_batches", "duration_ms": 37},
            {"stage": "gap_supplement", "duration_ms": 7},
            {"stage": "review_selection", "duration_ms": 11},
            {"stage": "final_shortfall_supplement", "duration_ms": 13},
            {"stage": "postprocess_total", "duration_ms": 31},
        ],
        generation_id=42,
        project_id=7,
        request_id="req-stream-1",
        generation_mode="multi_pass",
        multi_pass=True,
    )

    assert ledger.duration_by_stage_ms["prepare_total"] == 101
    assert ledger.duration_by_stage_ms["requirement_compress"] == 52
    assert ledger.duration_by_stage_ms["primary"] == 37
    assert ledger.duration_by_stage_ms["gap"] == 7
    assert ledger.duration_by_stage_ms["review"] == 11
    assert ledger.duration_by_stage_ms["final_shortfall"] == 13
    assert ledger.duration_by_stage_ms["postprocess_total"] == 31
    assert ledger.duration_by_stage_ms["client_resolution"] == 0

    assert ledger.payload is not None
    assert ledger.payload["kind"] == "generation_timing_ledger"
    assert ledger.payload["generation_id"] == 42
    assert ledger.payload["project_id"] == 7
    assert ledger.payload["request_id"] == "req-stream-1"
    assert ledger.payload["event_count"] == 9
    assert ledger.payload["duration_by_stage_ms"] == ledger.duration_by_stage_ms


def test_build_stream_timing_ledger_returns_no_payload_without_events() -> None:
    ledger = build_stream_timing_ledger(
        generation_timing_events=[],
        generation_id=None,
        project_id=7,
        request_id="req-stream-empty",
        generation_mode="",
        multi_pass=False,
    )

    assert ledger.payload is None
    assert all(value == 0 for value in ledger.duration_by_stage_ms.values())
