from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from scripts.qa.diagnostics.run_reason_coverage_acceptance import (  # noqa: E402
    AcceptanceConfig,
    _persist_outputs,
    run_acceptance_loop,
)


class _FakeRunner:
    def __init__(self, generation_ids: list[int]) -> None:
        self._ids = list(generation_ids)
        self._cursor = 0

    def trigger_once(self) -> int:
        if self._cursor >= len(self._ids):
            raise RuntimeError("no more fake generation ids")
        value = int(self._ids[self._cursor])
        self._cursor += 1
        return value


class _FakeCollector:
    def __init__(self, records_by_generation: dict[int, dict]) -> None:
        self._records = {int(k): dict(v) for k, v in records_by_generation.items()}

    def collect(self, *, generation_id: int, previous_generation_id: int | None, out_dir: Path) -> dict:
        _ = (previous_generation_id, out_dir)
        payload = dict(self._records[int(generation_id)])
        payload.setdefault("generation_id", int(generation_id))
        payload.setdefault("request_id", f"req-{generation_id}")
        payload.setdefault("reason_source_breakdown", {"primary": 0, "fallback": 0, "backfill": 0})
        payload.setdefault("candidate_total", 0)
        payload.setdefault("candidate_primary", 0)
        payload.setdefault("candidate_gap", 0)
        payload.setdefault("review_input_size", 0)
        payload.setdefault("review_output_size", 0)
        payload.setdefault("snapshot_id", "")
        payload.setdefault("corpus_hash", "")
        payload.setdefault("retrieval_hash", "")
        payload.setdefault("validate_checks", {})
        payload.setdefault("diff", {})
        payload.setdefault("export_paths", {})
        return payload


def _base_record(**kwargs) -> dict:
    record = {
        "final_source": "primary_llm",
        "review_primary_valid": True,
        "review_retry_valid": False,
        "primary_reason_coverage_ratio": 0.2,
        "fallback_reason_coverage_ratio": 0.0,
        "llm_reason_coverage_ratio": 0.2,
        "deterministic_backfill_ratio": 0.8,
        "fallback_dropped_reason_mapped_count": 0,
        "fallback_dropped_reason_count": 0,
        "retry_parse_success": False,
        "fallback_reason_incomplete": False,
        "validate_passed": False,
    }
    record.update(kwargs)
    return record


def test_acceptance_stops_early_when_fallback_passed(tmp_path: Path) -> None:
    runner = _FakeRunner([1001, 1002, 1003])
    collector = _FakeCollector(
        {
            1001: _base_record(),
            1002: _base_record(
                final_source="fallback_llm",
                review_retry_valid=True,
                fallback_reason_coverage_ratio=1.0,
                llm_reason_coverage_ratio=0.6,
                deterministic_backfill_ratio=0.4,
                fallback_dropped_reason_count=8,
                fallback_dropped_reason_mapped_count=8,
                validate_passed=True,
            ),
            1003: _base_record(),
        }
    )
    summary = run_acceptance_loop(
        runner=runner,
        collector=collector,
        config=AcceptanceConfig(max_runs=5, max_no_fallback_streak=4, low_coverage_threshold=0.05, max_low_coverage_streak=4),
        out_dir=tmp_path,
    )
    assert summary["stop_reason"] == "fallback_acceptance_passed"
    assert summary["total_runs"] == 2
    assert summary["fallback_hit_count"] == 1


def test_acceptance_stops_on_no_fallback_streak(tmp_path: Path) -> None:
    runner = _FakeRunner([2001, 2002, 2003, 2004])
    collector = _FakeCollector(
        {
            2001: _base_record(),
            2002: _base_record(),
            2003: _base_record(),
            2004: _base_record(),
        }
    )
    summary = run_acceptance_loop(
        runner=runner,
        collector=collector,
        config=AcceptanceConfig(max_runs=10, max_no_fallback_streak=3, low_coverage_threshold=0.01, max_low_coverage_streak=6),
        out_dir=tmp_path,
    )
    assert summary["stop_reason"] == "no_fallback_streak_exceeded"
    assert summary["total_runs"] == 3
    assert summary["fallback_hit_count"] == 0


def test_acceptance_marks_reason_coverage_regression(tmp_path: Path) -> None:
    runner = _FakeRunner([3001, 3002, 3003])
    collector = _FakeCollector(
        {
            3001: _base_record(llm_reason_coverage_ratio=0.0),
            3002: _base_record(llm_reason_coverage_ratio=0.0),
            3003: _base_record(llm_reason_coverage_ratio=0.5),
        }
    )
    summary = run_acceptance_loop(
        runner=runner,
        collector=collector,
        config=AcceptanceConfig(max_runs=6, max_no_fallback_streak=10, low_coverage_threshold=0.1, max_low_coverage_streak=2),
        out_dir=tmp_path,
    )
    assert summary["stop_reason"] == "reason_coverage_regression"
    assert summary["total_runs"] == 2


def test_acceptance_outputs_have_required_fields(tmp_path: Path) -> None:
    runner = _FakeRunner([4001])
    collector = _FakeCollector(
        {
            4001: _base_record(
                final_source="fallback_llm",
                review_primary_valid=False,
                review_retry_valid=True,
                fallback_reason_coverage_ratio=0.8,
                llm_reason_coverage_ratio=0.8,
                deterministic_backfill_ratio=0.2,
                fallback_dropped_reason_count=5,
                fallback_dropped_reason_mapped_count=4,
                validate_passed=True,
                reason_source_breakdown={"primary": 1, "fallback": 4, "backfill": 1},
                candidate_total=60,
                candidate_primary=40,
                candidate_gap=20,
                review_input_size=60,
                review_output_size=28,
                snapshot_id="snap-1",
                corpus_hash="hash-a",
                retrieval_hash="hash-b",
            )
        }
    )
    summary = run_acceptance_loop(
        runner=runner,
        collector=collector,
        config=AcceptanceConfig(max_runs=3, max_no_fallback_streak=3, low_coverage_threshold=0.05, max_low_coverage_streak=3),
        out_dir=tmp_path,
    )
    output_paths = _persist_outputs(tmp_path, summary)

    summary_path = Path(output_paths["json"])
    csv_path = Path(output_paths["csv"])
    md_path = Path(output_paths["markdown"])
    assert summary_path.exists()
    assert csv_path.exists()
    assert md_path.exists()

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["records"]
    row = dict(payload["records"][0])
    required_keys = {
        "generation_id",
        "final_source",
        "review_primary_valid",
        "review_retry_valid",
        "primary_reason_coverage_ratio",
        "fallback_reason_coverage_ratio",
        "llm_reason_coverage_ratio",
        "deterministic_backfill_ratio",
        "reason_source_breakdown",
        "candidate_total",
        "candidate_primary",
        "candidate_gap",
        "review_input_size",
        "review_output_size",
        "snapshot_id",
        "corpus_hash",
        "retrieval_hash",
    }
    assert required_keys.issubset(set(row.keys()))
