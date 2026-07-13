from __future__ import annotations

from typing import Any

from modules.testing.sample_case_access import sample_case_text as _sample_case_text

from .feedback_control_sample_access import sample_value as _sample_value
from ..coverage.scenario_registry import infer_domain_tags, infer_primary_domain_tag


def _sample_text_for_retrieval(sample_like: dict[str, Any]) -> str:
    return " ".join(
        str(part or "")
        for part in [
            _sample_value(sample_like, "pattern_summary", "patternSummary"),
            _sample_value(sample_like, "pattern_canonical", "patternCanonical"),
            _sample_value(sample_like, "title"),
            _sample_case_text(sample_like, "description", "source_case_title", "sourceCaseTitle"),
            _sample_case_text(sample_like, "test_module", "source_case_module", "sourceCaseModule"),
            _sample_case_text(sample_like, "steps", "source_case_steps", "sourceCaseSteps"),
            _sample_case_text(
                sample_like,
                "expected_result",
                "source_case_expected_result",
                "sourceCaseExpectedResult",
            ),
            _sample_value(sample_like, "business_assertion", "businessAssertion"),
            _sample_value(sample_like, "user_comment", "userComment"),
            _sample_value(sample_like, "reason_category", "reasonCategory"),
            _sample_value(sample_like, "pattern_category", "patternCategory"),
        ]
        if str(part or "").strip()
    )


def _sample_matches_primary_domain(sample_like: dict[str, Any], primary_domain: str) -> bool:
    if not primary_domain:
        return True
    text = _sample_text_for_retrieval(sample_like)
    sample_primary_domain = infer_primary_domain_tag(text)
    if sample_primary_domain:
        return sample_primary_domain == primary_domain
    return primary_domain in infer_domain_tags(text)


__all__ = [
    "_sample_matches_primary_domain",
    "_sample_text_for_retrieval",
]
