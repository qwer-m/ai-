from __future__ import annotations

from typing import Any

from modules.test_generation_components.postprocess.streaming_execution_plan_metadata import (
    apply_execution_plan_metadata,
)


def _essay_review_workflow_blueprints() -> list[dict[str, Any]]:
    return [
        {
            "id": "essay_review_publish",
            "source": "current_requirement_blueprint",
            "steps": [
                {
                    "id": "open_entry",
                    "label": "open correction entry",
                    "action": "open correction entry",
                    "actor": "supervisor",
                    "state_in": "prepared",
                    "state_out": "entry_opened",
                    "stage_kind": "entry",
                    "keywords": ["open correction entry"],
                },
                {
                    "id": "submit_result",
                    "label": "submit correction result",
                    "action": "submit correction result",
                    "actor": "supervisor",
                    "state_in": "entry_opened",
                    "state_out": "correction_published",
                    "stage_kind": "commit",
                    "keywords": ["submit correction result"],
                },
                {
                    "id": "student_visible",
                    "label": "latest correction result visible",
                    "action": "latest correction result visible",
                    "actor": "student",
                    "state_in": "correction_published",
                    "state_out": "student_result_visible",
                    "stage_kind": "downstream_visibility",
                    "keywords": ["latest correction result visible"],
                },
            ],
        }
    ]


def _essay_review_cases() -> list[dict[str, Any]]:
    return [
        {
            "id": "raw-1",
            "test_module": "作文批改",
            "description": "Supervisor opens the correction entry for a submitted essay",
            "preconditions": ["supervisor account has a submitted essay"],
            "steps": ["open correction entry"],
            "test_input": "submitted essay",
            "expected_result": "correction entry is ready",
            "priority": "P1",
            "role": "supervisor",
        },
        {
            "id": "raw-2",
            "test_module": "作文批改",
            "description": "Supervisor submit correction result after review",
            "preconditions": ["correction entry is ready"],
            "steps": ["submit correction result"],
            "test_input": "real rubric feedback",
            "expected_result": "submit success and correction result saved",
            "priority": "P1",
            "role": "supervisor",
        },
        {
            "id": "raw-3",
            "test_module": "作文批改",
            "description": "Student sees latest correction result visible in the essay detail",
            "preconditions": ["correction result has been submitted"],
            "steps": ["open essay detail", "verify latest correction result visible"],
            "test_input": "student essay",
            "expected_result": "latest correction result visible and synced to student",
            "priority": "P1",
            "role": "student",
        },
        {
            "id": "raw-4",
            "test_module": "作文批改",
            "description": "Student checks result list tooltip display text",
            "preconditions": ["student has correction result"],
            "steps": ["open result list", "hover tooltip"],
            "test_input": "student essay",
            "expected_result": "tooltip display text is readable",
            "priority": "P0",
            "role": "student",
        },
    ]


def test_apply_execution_plan_metadata_materializes_chain_annotations() -> None:
    annotated, summary = apply_execution_plan_metadata(
        _essay_review_cases(),
        workflow_blueprints=_essay_review_workflow_blueprints(),
    )

    main_cases = [item for item in annotated if item.get("execution_group") == "main_smoke"]
    display_cases = [item for item in annotated if item.get("execution_group") == "display"]

    assert summary["workflow_blueprint_source"] == "current_requirement_blueprint"
    assert summary["linear_executable"] is True
    assert summary["main_chain_case_count"] == 3
    assert summary["main_chain_stage_order"] == ["open_entry", "submit_result", "student_visible"]
    assert summary["state_conflict_count"] == 0
    assert summary["semantic_conflict_count"] == 0
    assert [item["id"] for item in main_cases] == ["TC-001", "TC-002", "TC-003"]
    assert [item.get("depends_on") for item in main_cases] == [[], ["TC-001"], ["TC-002"]]
    assert [item.get("main_chain_stage_kind") for item in main_cases] == [
        "entry",
        "commit",
        "downstream_visibility",
    ]
    assert [(item.get("source_state"), item.get("target_state")) for item in main_cases] == [
        ("prepared", "entry_opened"),
        ("entry_opened", "correction_published"),
        ("correction_published", "student_result_visible"),
    ]
    assert [item.get("role") for item in main_cases] == ["supervisor", "supervisor", "student"]
    assert [item.get("priority") for item in main_cases] == ["P0", "P0", "P0"]

    assert len(display_cases) == 1
    assert display_cases[0]["id"] == "TC-004"
    assert display_cases[0]["priority"] == "P1"
    assert display_cases[0]["priority_decision_source"] == "execution_plan_non_main_p0_demoted"
