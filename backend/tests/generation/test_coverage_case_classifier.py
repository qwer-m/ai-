from __future__ import annotations

from modules.test_generation_components.coverage.coverage_analyzer import (
    classify_case_scenario_key as analyzer_classify_case_scenario_key,
)
from modules.test_generation_components.coverage.coverage_case_classifier import (
    classify_case_flow_stage,
    classify_case_intent_signature,
    classify_case_scenario_key,
)


def test_case_classifier_keeps_network_retry_scenario_signature() -> None:
    case = {
        "caseId": "TC-ALIAS",
        "testModule": "Network recovery",
        "title": "retry after network interruption",
        "expectedResult": "network error message is displayed and retry succeeds",
        "testSteps": ["disconnect network", "click retry"],
    }

    assert classify_case_scenario_key(case, "stage:recovery") == (
        "stage:recovery:network_error:obj:network_recovery_retry"
    )
    assert analyzer_classify_case_scenario_key is classify_case_scenario_key


def test_case_classifier_resolves_flow_stage_and_intent_signature() -> None:
    flow_outline = {
        "flow_order": ["upload", "review"],
        "flow_labels": {"upload": "Upload Center", "review": "Review Queue"},
    }
    case = {
        "test_module": "Review Queue",
        "description": "review record and approve it",
        "steps": ["Open Review Queue", "Approve record"],
        "expected_result": "Record status changes to approved",
    }

    assert classify_case_flow_stage(case, flow_outline) == "review"
    assert classify_case_intent_signature(case, "review").startswith("review:intent:")
