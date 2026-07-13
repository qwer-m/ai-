from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from scripts.qa.diagnostics.analyze_generation_stability import (  # noqa: E402
    _build_deterministic_backfill_attribution,
    _build_diff,
)


def test_build_deterministic_backfill_attribution_prefers_missing_dropped_reasons_signal() -> None:
    summary = {
        "drop_by_review_llm_count": 10,
        "review_llm_drop_reason_source_breakdown": {"llm": 2, "deterministic_backfill": 8},
        "review_llm_runtime_debug": {
            "mapped_count": 0,
            "dropped_reason_count": 0,
            "final_source": "fallback_llm",
            "retry_parse_success": True,
            "retry_mapped_count": 6,
        },
    }
    table = {
        "row_count": 2,
        "row_count_total": 10,
        "rows_scope": "sampled_due_to_size",
        "rows": [
            {
                "dropped_stage": "review_llm",
                "review_llm_drop_reason_source": "deterministic_backfill",
                "review_llm_drop_reason_raw": "",
                "review_llm_drop_reason_evidence": {},
            },
            {
                "dropped_stage": "review_llm",
                "review_llm_drop_reason_source": "deterministic_backfill",
                "review_llm_drop_reason_raw": "",
                "review_llm_drop_reason_evidence": {},
            },
        ],
    }

    metrics = _build_deterministic_backfill_attribution(
        review_summary=summary,
        review_table_payload=table,
    )
    assert metrics["llm_reason_coverage_ratio"] == 0.2
    assert metrics["deterministic_backfill_ratio"] == 0.8
    assert metrics["dominant_deterministic_source"] == "fallback_missing_dropped_reasons"
    assert metrics["mapped_count_effective"] == 6
    assert metrics["dropped_reason_count_effective"] == 0
    assert metrics["sampled_detail_rows"] is True


def test_build_deterministic_backfill_attribution_accepts_compact_reason_source_breakdown() -> None:
    summary = {
        "drop_by_review_llm_count": 12,
        "reason_source_breakdown": {"primary": 3, "fallback": 2, "backfill": 7},
        "review_llm_runtime_debug": {
            "mapped_count": 5,
            "dropped_reason_count": 5,
            "final_source": "primary_llm",
            "retry_parse_success": False,
            "retry_mapped_count": 0,
        },
    }
    table = {"row_count": 0, "row_count_total": 0, "rows": []}
    metrics = _build_deterministic_backfill_attribution(
        review_summary=summary,
        review_table_payload=table,
    )
    assert metrics["llm_reason_count"] == 5
    assert metrics["deterministic_backfill_count"] == 7
    assert metrics["llm_reason_coverage_ratio"] == round(5 / 12, 4)
    assert metrics["deterministic_backfill_ratio"] == round(7 / 12, 4)


def test_build_diff_marks_generation_and_review_variance_sources() -> None:
    left = {
        "candidate_by_stage": {"primary": 20, "gap": 10, "before_review": 30, "retained": 16},
        "drop_ratio": 0.45,
        "deterministic_backfill_ratio": 0.6,
        "llm_reason_coverage_ratio": 0.4,
        "context_fingerprint": "abc",
        "review_final_source": "primary_llm",
        "review_runtime": {"primary_invalid_reason": ""},
        "reason_metrics": {"dominant_deterministic_source": "reason_adjusted_from_llm"},
    }
    right = {
        "candidate_by_stage": {"primary": 20, "gap": 25, "before_review": 45, "retained": 18},
        "drop_ratio": 0.6,
        "deterministic_backfill_ratio": 1.0,
        "llm_reason_coverage_ratio": 0.0,
        "context_fingerprint": "abc",
        "review_final_source": "fallback_llm",
        "review_runtime": {"primary_invalid_reason": "schema_parse_error"},
        "reason_metrics": {"dominant_deterministic_source": "fallback_missing_dropped_reasons"},
    }
    diff = _build_diff(left, right)
    variance = set(diff.get("variance_sources") or [])
    assert "generation_stage_variance" in variance
    assert "review_runtime_variance" in variance
    assert "review_payload_validity_variance" in variance
    assert "reason_coverage_variance" in variance
