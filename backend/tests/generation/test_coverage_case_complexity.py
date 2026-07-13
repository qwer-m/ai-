from __future__ import annotations

from modules.test_generation_components.coverage.coverage_case_complexity import (
    case_complexity_profile,
)
from modules.test_generation_components.coverage.coverage_analyzer import (
    case_complexity_profile as analyzer_case_complexity_profile,
)


def test_case_complexity_profile_scores_long_multi_step_case() -> None:
    case = {
        "description": "Validate course schedule save and downstream status update",
        "steps": [
            "Open course schedule",
            "Fill title",
            "Fill teacher",
            "Fill classroom",
            "Select time",
            "Submit",
            "Refresh status",
        ],
        "expected_result": "Saved schedule is visible in the list. " * 8,
    }

    profile = case_complexity_profile(case)

    assert profile["step_count"] == 7
    assert profile["complexity_score"] >= 3
    assert "too_many_steps" in profile["complexity_reasons"]
    assert "long_expected_result" in profile["complexity_reasons"]
    assert profile["is_complex_multi_assertion"] is True


def test_coverage_analyzer_reexports_case_complexity_profile() -> None:
    assert analyzer_case_complexity_profile is case_complexity_profile
