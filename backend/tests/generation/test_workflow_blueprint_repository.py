from __future__ import annotations

import importlib

from modules.test_generation_components.control.feedback_control_state import FeedbackControlState
from modules.test_generation_components.control.workflow_blueprint_repository import (
    is_trusted_workflow_contract,
    normalize_workflow_contract,
)


control_builder = importlib.import_module(
    "modules.test_generation_components.control.build_feedback_control_state"
)


def _contract() -> dict:
    return {
        "workflow_id": "schedule_create_to_student_learning",
        "project_id": 8,
        "source_doc_id": 220,
        "source_type": "human_reviewed",
        "trusted": True,
        "confidence": 1.0,
        "actors": ["supervisor", "student"],
        "match_terms": ["近期课程", "排课"],
        "commit_state": "schedule_plan_saved",
        "downstream_state": "student_home_weekly_task_visible",
        "completion_state": "progress_updated",
        "edges": [
            {
                "state_in": "course_management_ready",
                "action": "click_schedule_button",
                "state_out": "schedule_create_started",
                "actor": "supervisor",
            },
            {
                "state_in": "schedule_create_started",
                "action": "select_courses",
                "state_out": "courses_selected",
                "actor": "supervisor",
            },
        ],
    }


def test_normalize_workflow_contract_materializes_planner_steps() -> None:
    contract = normalize_workflow_contract(_contract())

    assert contract is not None
    assert contract["repository_source"] == "workflow_blueprint_repository"
    assert contract["trusted"] is True
    assert contract["steps"] == contract["edges"]
    assert contract["steps"][0]["state_in"] == "course_management_ready"
    assert contract["steps"][0]["state_out"] == "schedule_create_started"
    assert contract["steps"][0]["can_advance_main_flow"] is True
    assert is_trusted_workflow_contract(contract) is True


def test_candidate_derived_contract_cannot_become_trusted() -> None:
    contract = normalize_workflow_contract(
        {
            **_contract(),
            "source_type": "current_generation_cases",
            "trusted": True,
        }
    )

    assert contract is not None
    assert contract["trusted"] is False
    assert is_trusted_workflow_contract(contract) is False


def test_feedback_control_state_reads_repository_when_sample_pool_disabled(monkeypatch) -> None:
    class _Repository:
        def __init__(self, _db: object) -> None:
            pass

        def list_matching_trusted_contracts(self, **_: object) -> list[dict]:
            contract = normalize_workflow_contract(_contract())
            return [contract] if contract is not None else []

    monkeypatch.setattr(control_builder, "WorkflowBlueprintRepository", _Repository)
    monkeypatch.setattr(control_builder, "_build_from_anomaly_pool", lambda **_: FeedbackControlState.empty())
    monkeypatch.setattr(control_builder, "_build_from_reports", lambda **_: FeedbackControlState.empty())

    state = control_builder.build_feedback_control_state(
        db=object(),
        project_id=8,
        user_id=1,
        requirement_text="近期课程和排课需求",
        enable_priority_sample_pool=False,
        memory_fabric=None,
        memory_ctx=None,
    )

    assert len(state.workflow_blueprints) == 1
    assert state.workflow_blueprints[0]["workflow_id"] == "schedule_create_to_student_learning"
    assert state.source_meta["trusted_workflow_contract_count"] == 1
