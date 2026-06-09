from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_text_match import (
    build_quality_hint_keywords,
    normalize_match_patterns,
    normalize_match_text,
)


def test_normalize_match_text_keeps_ascii_digits_underscore_and_cjk() -> None:
    assert normalize_match_text("  P0-main path: 保存/提交 #1  ") == "p0mainpath保存提交1"


def test_normalize_match_patterns_deduplicates_after_normalization() -> None:
    assert normalize_match_patterns(["保存-提交", "保存 提交", "", None, "权限"]) == [
        "保存提交",
        "权限",
    ]


def test_build_quality_hint_keywords_extracts_unique_tokens_in_order() -> None:
    assert build_quality_hint_keywords(["保存提交后验证下游展示", "P0 main-flow state sync"]) == [
        "保存提交后验证下游展示",
        "p0mainflowstatesync",
    ]
