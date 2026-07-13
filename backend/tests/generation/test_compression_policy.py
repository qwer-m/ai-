from __future__ import annotations

from modules.test_generation_components.legacy.compression_policy import (
    requirement_compression_decision,
)


def test_requirement_compression_skips_short_requested_text() -> None:
    decision = requirement_compression_decision(
        "x" * 5996,
        compress_requested=True,
        min_chars=12000,
    )

    assert decision == {
        "should_compress": False,
        "reason": "below_min_chars",
        "char_count": 5996,
        "min_chars": 12000,
    }


def test_requirement_compression_skips_near_threshold_requested_text() -> None:
    decision = requirement_compression_decision(
        "x" * 12709,
        compress_requested=True,
        min_chars=12000,
        near_threshold_ratio=1.2,
    )

    assert decision["should_compress"] is False
    assert decision["reason"] == "near_threshold"
    assert decision["char_count"] == 12709
    assert decision["near_threshold_chars"] == 14400


def test_requirement_compression_allows_long_requested_text() -> None:
    decision = requirement_compression_decision(
        "x" * 16000,
        compress_requested=True,
        min_chars=12000,
        near_threshold_ratio=1.2,
    )

    assert decision["should_compress"] is True
    assert decision["reason"] == "requested"
    assert decision["char_count"] == 16000


def test_requirement_compression_respects_not_requested() -> None:
    decision = requirement_compression_decision(
        "x" * 50000,
        compress_requested=False,
        min_chars=12000,
    )

    assert decision["should_compress"] is False
    assert decision["reason"] == "not_requested"
