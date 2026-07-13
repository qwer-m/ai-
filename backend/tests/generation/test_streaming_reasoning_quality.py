from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_reasoning_quality import (
    reasoning_leakage_hits,
)


def test_reasoning_leakage_hits_detects_streaming_checked_fields() -> None:
    hits = reasoning_leakage_hits(
        {
            "preconditions": ["需求未明确时先按默认配置"],
            "steps": ["1. 打开页面"],
            "expected_result": "maybe show success",
        }
    )

    assert "需求未明确" in hits
    assert "先按" in hits
    assert "maybe" in hits


def test_reasoning_leakage_hits_keeps_streaming_field_scope() -> None:
    case = {
        "description": "需求未明确",
        "test_input": "maybe",
        "steps": ["打开页面"],
        "expected_result": "展示课程列表",
    }

    assert reasoning_leakage_hits(case) == []


def test_reasoning_leakage_hits_accepts_custom_signal_set() -> None:
    assert reasoning_leakage_hits({"expected_result": "custom signal"}, signals=("custom",)) == ["custom"]


def test_reasoning_leakage_hits_uses_alias_fields() -> None:
    hits = reasoning_leakage_hits(
        {
            "testSteps": ["1. maybe retry with assumed condition"],
            "expectedResult": "need product confirm before final assertion",
        }
    )

    assert "maybe" in hits
    assert "need product confirm" in hits
