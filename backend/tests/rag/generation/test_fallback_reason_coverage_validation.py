from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from scripts.qa.diagnostics.validate_fallback_reason_coverage import (  # noqa: E402
    _evaluate_acceptance,
    _extract_metrics_from_summary,
)


def test_extract_metrics_prefers_summary_fields() -> None:
    summary = {
        "drop_by_review_llm_count": 10,
        "fallback_reason_incomplete": False,
        "fallback_dropped_reason_count": 6,
        "fallback_dropped_reason_mapped_count": 5,
        "fallback_reason_coverage_ratio": 0.5,
        "llm_reason_coverage_ratio": 0.7,
        "deterministic_backfill_ratio": 0.3,
        "review_llm_drop_reason_source_breakdown": {
            "llm": 2,
            "fallback_llm": 5,
            "deterministic_backfill": 3,
        },
        "review_llm_runtime_debug": {
            "final_source": "fallback_llm",
            "retry_parse_success": True,
            "final_dropped_reason_count": 5,
            "final_dropped_reason_payload_count": 6,
        },
    }
    metrics = _extract_metrics_from_summary(summary)
    assert metrics["final_source"] == "fallback_llm"
    assert metrics["retry_parse_success"] is True
    assert metrics["fallback_reason_incomplete"] is False
    assert metrics["fallback_dropped_reason_count"] == 6
    assert metrics["fallback_dropped_reason_mapped_count"] == 5
    assert metrics["fallback_reason_coverage_ratio"] == 0.5
    assert metrics["llm_reason_coverage_ratio"] == 0.7
    assert metrics["deterministic_backfill_ratio"] == 0.3


def test_extract_metrics_falls_back_to_runtime_and_breakdown() -> None:
    summary = {
        "drop_by_review_llm_count": 8,
        "review_llm_drop_reason_source_breakdown": {
            "llm": 1,
            "fallback_llm": 3,
            "deterministic_backfill": 4,
        },
        "review_llm_runtime_debug": {
            "final_source": "fallback_llm",
            "retry_parse_success": True,
            "final_dropped_reason_count": 3,
            "final_dropped_reason_payload_count": 5,
        },
    }
    metrics = _extract_metrics_from_summary(summary)
    assert metrics["fallback_dropped_reason_count"] == 5
    assert metrics["fallback_dropped_reason_mapped_count"] == 3
    assert metrics["llm_reason_coverage_ratio"] == 0.5
    assert metrics["deterministic_backfill_ratio"] == 0.5


def test_extract_metrics_normalizes_fallback_ratio_over_payload_count() -> None:
    summary = {
        "drop_by_review_llm_count": 10,
        "fallback_reason_coverage_ratio": 1.4,
        "review_llm_runtime_debug": {
            "final_source": "fallback_llm",
            "retry_parse_success": True,
            "final_dropped_reason_count": 14,
            "final_dropped_reason_payload_count": 14,
        },
    }
    metrics = _extract_metrics_from_summary(summary)
    assert metrics["fallback_reason_coverage_ratio"] == 1.0


def test_evaluate_acceptance_pass_and_fail() -> None:
    pass_metrics = {
        "final_source": "fallback_llm",
        "fallback_dropped_reason_mapped_count": 2,
        "llm_reason_coverage_ratio": 0.2,
        "deterministic_backfill_ratio": 0.8,
    }
    fail_metrics = {
        "final_source": "review_selector",
        "fallback_dropped_reason_mapped_count": 0,
        "llm_reason_coverage_ratio": 0.0,
        "deterministic_backfill_ratio": 1.0,
    }
    assert _evaluate_acceptance(pass_metrics)["passed"] is True
    assert _evaluate_acceptance(fail_metrics)["passed"] is False
