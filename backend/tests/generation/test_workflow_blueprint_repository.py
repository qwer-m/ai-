from __future__ import annotations

import importlib

from modules.test_generation_components.control.feedback_control_state import FeedbackControlState
from modules.test_generation_components.control.workflow_blueprint_repository import (
    _contract_requirement_match,
    _contract_requirement_match_is_sufficient,
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


def test_contract_requirement_match_rejects_unrelated_unlinked_contract() -> None:
    contract = normalize_workflow_contract(
        {
            **_contract(),
            "source_doc_id": None,
            "match_terms": [],
            "edges": [
                {
                    "state_in": "course_management_ready",
                    "action": "click_schedule_button",
                    "state_out": "schedule_create_started",
                    "actor": "supervisor",
                    "label": "进入排课创建",
                    "match_keywords": ["排课", "创建排课", "课程管理"],
                },
                {
                    "state_in": "schedule_create_started",
                    "action": "select_courses",
                    "state_out": "courses_selected",
                    "actor": "supervisor",
                    "label": "选择近期课程",
                    "match_keywords": ["近期课程", "选择课程", "课程选择"],
                },
            ],
        }
    )

    assert contract is not None
    score, hit_count, has_explicit_terms, core_hit_count, hit_terms = _contract_requirement_match(
        contract,
        "讲错题接入AI后，学生在随堂测结果页进入讲错题流程，AI按错因追问并评分。",
    )

    assert hit_terms == []
    assert _contract_requirement_match_is_sufficient(
        score=score,
        hit_count=hit_count,
        has_explicit_terms=has_explicit_terms,
        core_hit_count=core_hit_count,
    ) is False


def test_contract_requirement_match_keeps_related_unlinked_contract() -> None:
    contract = normalize_workflow_contract(
        {
            **_contract(),
            "source_doc_id": None,
            "match_terms": [],
            "edges": [
                {
                    "state_in": "course_management_ready",
                    "action": "click_schedule_button",
                    "state_out": "schedule_create_started",
                    "actor": "supervisor",
                    "stage_kind": "entry",
                    "label": "进入排课创建",
                    "match_keywords": ["排课", "创建排课", "课程管理"],
                },
                {
                    "state_in": "schedule_create_started",
                    "action": "select_courses",
                    "state_out": "courses_selected",
                    "actor": "supervisor",
                    "stage_kind": "configure",
                    "label": "选择近期课程",
                    "match_keywords": ["近期课程", "选择课程", "课程选择"],
                },
            ],
        }
    )

    assert contract is not None
    score, hit_count, has_explicit_terms, core_hit_count, hit_terms = _contract_requirement_match(
        contract,
        "近期课程排课需求：从课程管理进入创建排课，选择近期课程并保存排课计划。",
    )

    assert {"排课", "近期课程"}.issubset(set(hit_terms))
    assert core_hit_count >= 1
    assert _contract_requirement_match_is_sufficient(
        score=score,
        hit_count=hit_count,
        has_explicit_terms=has_explicit_terms,
        core_hit_count=core_hit_count,
        require_core_hit=True,
    ) is True


def test_contract_requirement_match_does_not_use_declared_actor_as_business_evidence() -> None:
    contract = normalize_workflow_contract(
        {
            **_contract(),
            "actors": ["content_reviewer"],
            "match_terms": ["content_reviewer"],
            "edges": [
                {
                    "state_in": "draft_ready",
                    "action": "review_content",
                    "state_out": "review_completed",
                    "actor": "content_reviewer",
                },
                {
                    "state_in": "review_completed",
                    "action": "publish_content",
                    "state_out": "content_published",
                    "actor": "content_reviewer",
                },
            ],
        }
    )

    assert contract is not None
    score, hit_count, has_explicit_terms, core_hit_count, hit_terms = _contract_requirement_match(
        contract,
        "content_reviewer",
    )

    assert (score, hit_count, core_hit_count, hit_terms) == (0, 0, 0, [])
    assert has_explicit_terms is False


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
