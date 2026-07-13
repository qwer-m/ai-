from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_rule_keys import extract_rule_keys


def test_extract_rule_keys_normalizes_and_deduplicates_requirement_ids() -> None:
    case = {
        "description": "covers req 12 and REQ_13",
        "test_module": "REQ 12",
        "test_input": "no rule",
        "expected_result": "REQ-14 is visible",
        "steps": ["1. verify req_13"],
    }

    assert extract_rule_keys(case) == ["REQ-12", "REQ-13", "REQ-14"]


def test_extract_rule_keys_ignores_non_matching_text() -> None:
    assert extract_rule_keys({"description": "request 12 and requirement 13"}) == []
