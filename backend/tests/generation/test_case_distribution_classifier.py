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
        "description": "open request and finish the approval journey",
        "steps": ["enter request", "submit approval", "return to request list"],
        "expected_result": "user completes the approval flow",
    }
    assert classify_case_distribution(case) == "FLOW"


def test_classify_case_distribution_accepts_alias_fields() -> None:
    case = {
        "caseId": "TC-ALIAS",
        "title": "open request and finish the approval journey",
        "testSteps": ["enter request", "submit approval", "return to request list"],
        "expectedResult": "user completes the approval flow",
    }

    assert classify_case_distribution(case) == "FLOW"
    assert classify_case_distributions([case]) == {"TC-ALIAS": "FLOW"}


def test_classify_case_distribution_splits_text_steps_with_shared_accessor() -> None:
    case = {
        "caseId": "TC-TEXT-STEPS",
        "title": "finish the approval journey",
        "testSteps": "enter request;review details；submit approval",
        "expectedResult": "user completes the approval flow",
    }

    assert classify_case_distribution(case) == "FLOW"
    assert summarize_case_structure_signals([case])["multi_step_case_count"] == 1


def test_classify_case_distribution_returns_state_for_context_guard_case() -> None:
    case = {
        "id": "TC-002",
        "description": "reload after interruption",
        "steps": ["interrupt app", "resume current request"],
        "expected_result": "context preserved and keep current state",
    }
    assert classify_case_distribution(case) == "STATE"


def test_classify_case_distribution_keeps_return_reenter_state_guard_as_state() -> None:
    case = {
        "id": "TC-002A",
        "description": "return to request list and re-enter current request",
        "steps": ["return to request list", "re-enter current request"],
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
            "description": "enter item and submit approval",
            "steps": ["enter item", "review details", "submit approval"],
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


def test_classify_case_distribution_prefers_structured_execution_contract() -> None:
    main_flow = {
        "id": "TC-STRUCT-001",
        "description": "button title display",
        "steps": ["open page"],
        "expected_result": "button title is visible",
        "execution_group": "main_smoke",
        "workflow_transition": {
            "source_state": "draft_ready",
            "target_state": "request_submitted",
            "can_advance_main_flow": True,
        },
    }
    state_case = {
        "id": "TC-STRUCT-002",
        "description": "refresh view",
        "steps": ["refresh"],
        "expected_result": "view remains current",
        "_semantic": {
            "produced_states": [
                {
                    "entity": "request",
                    "state": "current",
                    "evidence_verified": True,
                }
            ]
        },
    }

    assert classify_case_distribution(main_flow) == "FLOW"
    assert classify_case_distribution(state_case) == "STATE"


def test_classify_case_distribution_does_not_trust_unverified_semantic_state() -> None:
    case = {
        "id": "TC-STRUCT-003",
        "description": "button title display",
        "steps": ["open page"],
        "expected_result": "button title is visible",
        "_semantic": {
            "produced_states": [
                {
                    "entity": "request",
                    "state": "current",
                    "evidence_verified": False,
                }
            ]
        },
    }

    assert classify_case_distribution(case) == "UI"


def test_structure_signals_do_not_infer_cross_module_from_two_module_candidates() -> None:
    case = {
        "id": "TC-STRUCT-004",
        "description": "verify a shared panel",
        "steps": ["inspect panel"],
        "expected_result": "panel remains visible",
        "_semantic": {
            "interaction_ids": [],
            "module_candidates": [
                {"module_key": "source", "role": "source", "evidence_verified": True},
                {"module_key": "target", "role": "target", "evidence_verified": True},
            ],
        },
    }

    assert summarize_case_structure_signals([case])["cross_page_case_count"] == 0


def test_structure_signals_require_interaction_and_source_target_roles() -> None:
    case = {
        "id": "TC-STRUCT-005",
        "description": "verify a shared panel",
        "steps": ["inspect panel"],
        "expected_result": "panel remains visible",
        "_semantic": {
            "interaction_ids": ["source_target_sync"],
            "module_candidates": [
                {"module_key": "source", "role": "source", "evidence_verified": True},
                {"module_key": "target", "role": "target", "evidence_verified": True},
            ],
        },
    }

    assert summarize_case_structure_signals([case])["cross_page_case_count"] == 1
