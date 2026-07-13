from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_semantic_text import (
    jaccard_similarity,
    semantic_normalize_text,
    semantic_signature,
    semantic_tokenize,
)


def test_semantic_normalize_text_collapses_known_synonyms_and_punctuation() -> None:
    assert semantic_normalize_text("Toast：按钮不可点击！") == "提示按钮禁用"
    assert semantic_normalize_text("入口/图标 文案") == "功能入口功能入口提示文案"


def test_semantic_tokenize_deduplicates_and_uses_minimum_limit() -> None:
    tokens = semantic_tokenize("课程course课程result状态state结果ok", limit=2)

    assert tokens == {"课程", "course", "result", "状态", "state", "结果"}


def test_semantic_signature_uses_module_rule_keys_and_core_text() -> None:
    case = {
        "test_module": " Course ",
        "description": "按钮不可点击",
        "expected_result": "Toast 提示",
        "steps": ["1. 打开入口"],
    }

    assert semantic_signature(case, ["R2", "R1"]) == "course|R1|R2|按钮禁用|提示提示|1打开功能入口"


def test_jaccard_similarity_handles_empty_and_intersection() -> None:
    assert jaccard_similarity(set(), {"a"}) == 0.0
    assert jaccard_similarity({"a", "b"}, {"b", "c"}) == 1 / 3
