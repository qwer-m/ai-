import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.eval.case_distribution_classifier import (
    classify_case_distribution,
    classify_case_distributions,
    summarize_case_distribution,
    summarize_case_structure_signals,
)


def test_classify_case_distribution_returns_flow_for_multi_step_path() -> None:
    case = {
        "id": "TC-001",
        "description": "open course and finish the practice journey",
        "steps": ["enter course", "submit answer", "return to course list"],
        "expected_result": "user completes the learning flow",
    }
    assert classify_case_distribution(case) == "FLOW"


def test_classify_case_distribution_returns_state_for_context_guard_case() -> None:
    case = {
        "id": "TC-002",
        "description": "reload after interruption",
        "steps": ["interrupt app", "resume current lesson"],
        "expected_result": "context preserved and keep current state",
    }
    assert classify_case_distribution(case) == "STATE"


def test_classify_case_distribution_keeps_return_reenter_state_guard_as_state() -> None:
    case = {
        "id": "TC-002A",
        "description": "return to course list and re-enter current lesson",
        "steps": ["return to course list", "re-enter current lesson"],
        "expected_result": "context preserved and keep current state",
    }
    assert classify_case_distribution(case) == "STATE"


def test_classify_case_distribution_returns_ui_for_display_only_case() -> None:
    case = {
        "id": "TC-003",
        "description": "button copy and layout display",
        "steps": ["open page"],
        "expected_result": "button title and icon display correctly",
    }
    assert classify_case_distribution(case) == "UI"


def test_classify_case_distributions_and_summaries_use_shared_rules() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "enter course and submit exercise",
            "steps": ["enter course", "do exercise", "submit answer"],
            "expected_result": "flow closes correctly",
        },
        {
            "id": "TC-002",
            "description": "switch version after loading",
            "steps": ["switch version"],
            "expected_result": "state remains consistent after refresh",
        },
        {
            "id": "TC-003",
            "description": "verify button text",
            "steps": ["open page"],
            "expected_result": "button copy displays correctly",
        },
    ]
    mapping = classify_case_distributions(cases)
    assert mapping == {
        "TC-001": "FLOW",
        "TC-002": "STATE",
        "TC-003": "UI",
    }
    assert summarize_case_distribution(cases) == {
        "FLOW": 1,
        "STATE": 1,
        "UI": 1,
    }
    assert summarize_case_structure_signals(cases) == {
        "cross_page_case_count": 0,
        "multi_step_case_count": 1,
        "state_transition_case_count": 1,
    }


def test_classify_case_distribution_requires_progress_signal_for_flow() -> None:
    case = {
        "id": "TC-004",
        "description": "enter details page and return to list",
        "steps": ["enter details page", "return to list"],
        "expected_result": "navigation remains available",
    }
    assert classify_case_distribution(case) == "UI"
