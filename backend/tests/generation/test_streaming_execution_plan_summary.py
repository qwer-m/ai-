from __future__ import annotations

from typing import Any

from modules.testing.test_generation_components.postprocess.streaming_execution_plan_summary import (
    build_execution_plan_metadata_summary,
    execution_plan_counts,
)


def _case(case_id: str, *, group: str, role: str = "student", **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": case_id,
        "test_module": "essay correction",
        "description": f"{case_id} executes a business workflow step",
        "preconditions": ["real business account is logged in"],
        "steps": ["open page", "perform business action", "verify result"],
        "test_input": "real business input",
        "expected_result": "page shows the latest business result",
        "priority": "P0",
        "execution_group": group,
        "role": role,
    }
    item.update(overrides)
    return item


def test_execution_plan_counts_tracks_role_switches_and_main_chain_size() -> None:
    annotated = [
        _case("TC-001", group="main_smoke", role="teacher", main_chain_step=1),
        _case("TC-002", group="main_smoke", role="student", main_chain_step=2, depends_on="TC-001"),
        _case("TC-003", group="main_smoke", role="teacher", main_chain_step=3, depends_on="TC-002"),
        _case("TC-004", group="display", role="student"),
    ]

    counts = execution_plan_counts(annotated)

    assert counts["role_switch_count"] == 2
    assert counts["main_chain_count"] == 3
    assert counts["independent_count"] == 1


def test_execution_plan_counts_flags_main_smoke_steps_missing_depends_on() -> None:
    annotated = [
        _case("TC-001", group="main_smoke", main_chain_step=1),
        _case("TC-002", group="main_smoke", main_chain_step=2),
        _case("TC-003", group="main_smoke", main_chain_step=3, depends_on=["TC-002"]),
        _case("TC-004", group="boundary", isolation_required=True),
        _case("TC-005", group="exception", isolation_required=True),
    ]

    counts = execution_plan_counts(annotated)

    assert counts["broken_dependency_count"] == 1
    assert counts["isolation_count"] == 2


def test_execution_plan_counts_builds_group_breakdown() -> None:
    annotated = [
        _case("TC-001", group="main_smoke"),
        _case("TC-002", group="display"),
        _case("TC-003", group="permission"),
        _case("TC-004", group="display"),
    ]

    counts = execution_plan_counts(annotated)

    assert counts["execution_group_breakdown"] == {
        "display": 2,
        "main_smoke": 1,
        "permission": 1,
    }


def test_execution_plan_counts_collects_sorted_unique_fixture_keys() -> None:
    annotated = [
        _case("TC-001", group="main_smoke", fixture_key="workflow_blueprint_chain_seed"),
        _case("TC-002", group="boundary", fixture_key="boundary_dataset"),
        _case("TC-003", group="display", fixture_key=""),
        _case("TC-004", group="boundary", fixture_key="boundary_dataset"),
    ]

    counts = execution_plan_counts(annotated)

    assert counts["fixture_keys"] == ["boundary_dataset", "workflow_blueprint_chain_seed"]


def test_execution_plan_counts_tracks_bridge_and_materialized_cases() -> None:
    annotated = [
        _case("TC-001", group="main_smoke", generated_bridge_case=True),
        _case("TC-002", group="main_smoke", workflow_contract_materialized_case=True),
        _case("TC-003", group="display", generated_bridge_case=False),
        _case("TC-004", group="permission", workflow_contract_materialized_case=True),
    ]

    counts = execution_plan_counts(annotated)

    assert counts["generated_bridge_case_count"] == 1
    assert counts["workflow_contract_materialized_case_count"] == 2


def test_build_execution_plan_metadata_summary_preserves_core_fields() -> None:
    annotated = [
        _case(
            "TC-001",
            group="main_smoke",
            role="teacher",
            main_chain_stage="draft",
            main_chain_step=1,
            main_chain_stage_kind="commit",
            generated_bridge_case=True,
            fixture_key="workflow_blueprint_chain_seed",
            isolation_required=False,
        ),
        _case(
            "TC-002",
            group="main_smoke",
            role="student",
            main_chain_stage="review",
            main_chain_step=2,
            main_chain_stage_kind="downstream_visibility",
            depends_on=["TC-001"],
            workflow_contract_materialized_case=True,
            fixture_key="workflow_contract_seed",
            isolation_required=False,
        ),
        _case(
            "TC-003",
            group="display",
            role="student",
            fixture_key="display_seed",
            isolation_required=True,
        ),
    ]

    summary = build_execution_plan_metadata_summary(
        annotated,
        coverage_mode="strict",
        workflow_blueprints=[{"id": "bp-1"}, {"id": "bp-2"}, {"id": "bp-3"}],
        trusted_workflow_contracts=[{"id": "bp-1"}],
        current_requirement_workflow_blueprints=[{"id": "bp-2"}],
        plan_workflow_blueprints=[{"id": "bp-1"}, {"id": "bp-2"}],
        workflow_blueprint_source="trusted_workflow_contract",
        main_chain_stage_kinds=["commit", "downstream_visibility"],
        main_chain_incomplete_reason="",
        derived_workflow_debug={"selected_candidate_count": 2},
        group_setup_map={
            "display": "seed_display_dataset()",
            "main_smoke": "seed_main_chain()",
        },
        group_teardown_map={
            "display": "cleanup_display_dataset()",
            "main_smoke": "cleanup_main_chain()",
        },
    )

    assert summary["applied"] is True
    assert summary["coverage_mode"] == "strict"
    assert summary["workflow_blueprint_count"] == 3
    assert summary["trusted_workflow_contract_count"] == 1
    assert summary["current_requirement_blueprint_count"] == 1
    assert summary["plan_workflow_blueprint_count"] == 2
    assert summary["workflow_blueprint_source"] == "trusted_workflow_contract"
    assert summary["linear_executable"] is True
    assert summary["linear_scope"] == "main_smoke_chain_only"
    assert summary["main_chain_case_count"] == 2
    assert summary["main_chain_stage_order"] == ["draft", "review"]
    assert summary["main_chain_stage_kinds"] == ["commit", "downstream_visibility"]
    assert summary["main_chain_incomplete_reason"] == ""
    assert summary["derived_workflow_debug"] == {"selected_candidate_count": 2}
    assert summary["independent_case_count"] == 1
    assert summary["isolation_case_count"] == 1
    assert summary["role_switch_count"] == 1
    assert summary["broken_dependency_count"] == 0
    assert summary["state_conflict_count"] == 0
    assert summary["semantic_conflict_count"] == 0
    assert summary["execution_group_breakdown"] == {"display": 1, "main_smoke": 2}
    assert summary["execution_group_order"] == ["main_smoke", "display"]
    assert summary["execution_orchestration_plan"]["plan_first"] is True
    assert summary["execution_orchestration_plan"]["main_chain_case_count"] == 2
    assert summary["execution_orchestration_plan"]["side_suites"] == [
        {
            "suite_id": "display_suite",
            "execution_group": "display",
            "rank": 5,
            "case_ids": ["TC-003"],
            "case_count": 1,
        }
    ]
    assert summary["generated_bridge_case_count"] == 1
    assert summary["workflow_contract_materialized_case_count"] == 1
    assert summary["group_setup"] == {
        "display": "seed_display_dataset()",
        "main_smoke": "seed_main_chain()",
    }
    assert summary["group_teardown"] == {
        "display": "cleanup_display_dataset()",
        "main_smoke": "cleanup_main_chain()",
    }
    assert summary["fixture_keys"] == [
        "display_seed",
        "workflow_blueprint_chain_seed",
        "workflow_contract_seed",
    ]


def test_build_execution_plan_metadata_summary_truncates_diagnostic_lists() -> None:
    annotated = [
        _case("TC-001", group="main_smoke", main_chain_stage="draft", main_chain_step=1),
        _case(
            "TC-002",
            group="main_smoke",
            main_chain_stage="review",
            main_chain_step=2,
            depends_on=["TC-001"],
        ),
    ]
    excluded = [
        {
            "case_id": f"TC-{index:03d}",
            "reason": "display_only",
            "stage_key": "review",
            "signature": f"signature-{index}",
        }
        for index in range(55)
    ]
    state_conflicts = [{"reason": "state_bridge_missing", "index": index} for index in range(55)]
    selected_stage_state_conflicts = [
        {"reason": "selected_state_conflict", "index": index}
        for index in range(55)
    ]
    semantic_conflicts = [{"reason": "role_action_conflict", "index": index} for index in range(55)]

    summary = build_execution_plan_metadata_summary(
        annotated,
        main_chain_excluded_candidates=excluded,
        state_conflicts=state_conflicts,
        selected_stage_state_conflicts=selected_stage_state_conflicts,
        semantic_conflicts=semantic_conflicts,
    )

    assert summary["linear_executable"] is False
    assert len(summary["main_chain_excluded_candidates"]) == 50
    assert "signature" not in summary["main_chain_excluded_candidates"][0]
    assert summary["main_chain_excluded_candidates"][49]["case_id"] == "TC-049"
    assert summary["state_conflict_count"] == 55
    assert len(summary["state_conflicts"]) == 50
    assert len(summary["selected_stage_state_conflicts"]) == 50
    assert summary["semantic_conflict_count"] == 55
    assert len(summary["semantic_conflicts"]) == 50


def test_build_execution_plan_metadata_summary_uses_empty_defaults() -> None:
    summary = build_execution_plan_metadata_summary()

    assert summary["applied"] is True
    assert summary["coverage_mode"] == ""
    assert summary["workflow_blueprint_count"] == 0
    assert summary["trusted_workflow_contract_count"] == 0
    assert summary["current_requirement_blueprint_count"] == 0
    assert summary["plan_workflow_blueprint_count"] == 0
    assert summary["workflow_blueprint_source"] == ""
    assert summary["linear_executable"] is False
    assert summary["linear_scope"] == "main_smoke_chain_only"
    assert summary["main_chain_case_count"] == 0
    assert summary["main_chain_stage_order"] == []
    assert summary["main_chain_stage_kinds"] == []
    assert summary["main_chain_incomplete_reason"] == ""
    assert summary["derived_workflow_debug"] == {}
    assert summary["main_chain_excluded_candidates"] == []
    assert summary["independent_case_count"] == 0
    assert summary["isolation_case_count"] == 0
    assert summary["role_switch_count"] == 0
    assert summary["broken_dependency_count"] == 0
    assert summary["state_conflict_count"] == 0
    assert summary["state_conflicts"] == []
    assert summary["selected_stage_state_conflicts"] == []
    assert summary["semantic_conflict_count"] == 0
    assert summary["semantic_conflicts"] == []
    assert summary["execution_group_breakdown"] == {}
    assert summary["execution_group_order"] == []
    assert summary["execution_orchestration_plan"] == {
        "plan_first": True,
        "planned_case_count": 0,
        "main_chain_case_count": 0,
        "side_suite_count": 0,
        "execution_group_order": [],
        "main_chain": [],
        "side_suites": [],
    }
    assert summary["generated_bridge_case_count"] == 0
    assert summary["workflow_contract_materialized_case_count"] == 0
    assert summary["group_setup"] == {}
    assert summary["group_teardown"] == {}
    assert summary["fixture_keys"] == []
