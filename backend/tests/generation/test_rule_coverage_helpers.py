from __future__ import annotations

from modules.test_generation_components.coverage import rule_coverage
from modules.test_generation_components.coverage.rule_coverage_extraction import (
    _classify_requirement_rule,
    _extract_requirement_rules,
)
from modules.test_generation_components.coverage.rule_coverage_text import (
    _extract_rule_id,
    _tokenize,
)


def test_rule_coverage_text_helpers_extract_req_id_and_tokens() -> None:
    assert _extract_rule_id("REQ-102 must hide legacy entry") == "REQ-102"

    tokens = _tokenize("REQ-102 must hide legacy entry", limit=3)

    assert "REQ" in tokens
    assert "hide" in tokens
    assert len(tokens) >= 3


def test_rule_extraction_helper_marks_confirmed_rule_as_blocking() -> None:
    rules = _extract_requirement_rules(
        """
### biz_key: legacy_entry
* REQ-102 must hide legacy entry after migration.
"""
    )

    assert rules == [
        {
            "rule_id": "REQ-102",
            "rule_text": "REQ-102 must hide legacy entry after migration.",
            "biz_key": "legacy_entry",
            **_classify_requirement_rule("REQ-102 must hide legacy entry after migration."),
        }
    ]
    assert rules[0]["blocking"] is True


def test_rule_extraction_ignores_requirement_parse_diagnostics() -> None:
    rules = _extract_requirement_rules(
        """
论坛详情页必须展示评论入口，并支持用户发表回复。

[Requirement Understanding]
{"version":"requirement-understanding-v1","visual_facts":[{"source":"pdf_visual:X46.jpg","text":"版主回复标签仅版主内容展示，信息被隐藏"}]}

[Parsed Requirement Evidence]
- pdf_visual: filename=X46.jpg, strategy=pdf_image_ocr, chars=917, ocr_source=cloud, cloud_fallback=true

[Multimodal Evidence Alignment]
- pdf_visual:X46.jpg -> requirement score=1.00; requirement="论坛"; evidence="版主回复标签仅版主内容展示"
"""
    )

    rule_texts = [rule["rule_text"] for rule in rules]
    assert any("评论入口" in text for text in rule_texts)
    assert all("pdf_visual" not in text for text in rule_texts)
    assert all("信息被隐藏" not in text for text in rule_texts)


def test_rule_coverage_facade_preserves_private_helper_imports() -> None:
    assert rule_coverage._extract_rule_id("REQ-103 should display result") == "REQ-103"
    assert rule_coverage._tokenize("must display result", limit=2)
