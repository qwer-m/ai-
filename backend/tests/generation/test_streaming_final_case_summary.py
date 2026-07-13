from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_final_case_summary import (
    build_execution_plan_flow_summary_fields,
    build_final_flow_structure_fields,
    build_flow_profile_governance_fields,
    build_review_flow_structure_fields,
    final_dedup_priority_summary_fields,
    final_case_breakdown,
    resolve_final_duplicate_project_profile,
    review_flow_structure_summary_fields,
    summarize_final_description_dedup_drops,
    summarize_final_description_dedup_and_priority_breakdown,
    summarize_priority_decision_breakdown,
)


def test_final_case_breakdown_counts_priority_group_module_and_ratios() -> None:
    cases = [
        {
            "priority_final": "P0",
            "execution_group": "main_smoke",
            "test_module": "Course",
            "description": "submit",
        },
        {
            "priority": "P1",
            "execution_group": "permission",
            "test_module": "Course",
            "description": "permission",
            "display_only": True,
        },
        {
            "model_priority": "custom",
            "execution_group": "",
            "test_module": "Report",
            "description": "report",
        },
        "ignored",
    ]

    summary = final_case_breakdown(
        cases,  # type: ignore[arg-type]
        final_count=4,
        display_predicate=lambda case: bool(case.get("display_only")),
    )

    assert summary == {
        "final_priority_breakdown": {"P0": 1, "P1": 1, "UNKNOWN": 1},
        "final_execution_group_breakdown": {
            "main_smoke": 1,
            "permission": 1,
            "unknown": 1,
        },
        "final_module_breakdown_top": {"Course": 2, "Report": 1},
        "final_display_case_count": 1,
        "final_display_ratio": 0.25,
        "final_high_priority_ratio": 0.5,
    }


def test_final_case_breakdown_uses_safe_denominator_for_empty_result() -> None:
    assert final_case_breakdown([], final_count=0, display_predicate=lambda _case: False) == {
        "final_priority_breakdown": {},
        "final_execution_group_breakdown": {},
        "final_module_breakdown_top": {},
        "final_display_case_count": 0,
        "final_display_ratio": 0.0,
        "final_high_priority_ratio": 0.0,
    }


def test_summarize_final_description_dedup_counts_and_samples_stably() -> None:
    rows = [
        {
            "id": "drop-1",
            "signature": "sig-drop-1",
            "description": "Submit   Order",
            "test_module": "Order",
            "retained_final": False,
        },
        {
            "id": "keep-1",
            "signature": "sig-keep",
            "description": "Submit Order",
            "test_module": "Order",
            "retained_final": True,
        },
        {
            "id": "drop-other",
            "signature": "sig-drop-other",
            "description": "Cancel Order",
            "test_module": "Order",
            "retained_final": False,
        },
        {
            "id": "drop-2",
            "signature": "sig-drop-2",
            "description": "submit order",
            "test_module": "Payment",
            "retained_final": False,
        },
    ]

    summary = summarize_final_description_dedup_and_priority_breakdown(rows)

    assert summary["final_description_dedup_drop_signatures"] == {"sig-drop-1", "sig-drop-2"}
    assert summary["final_description_dedup_drop_count"] == 2
    assert summary["final_description_dedup_drop_samples"] == [
        {
            "signature": "sig-drop-1",
            "description_key": "submit order",
            "case_id": "drop-1",
            "test_module": "Order",
        },
        {
            "signature": "sig-drop-2",
            "description_key": "submit order",
            "case_id": "drop-2",
            "test_module": "Payment",
        },
    ]


def test_summarize_final_description_dedup_drops_respects_sample_limit() -> None:
    rows = [
        {
            "id": "keep",
            "signature": "sig-keep",
            "description": "Submit Order",
            "test_module": "Order",
            "retained_final": True,
        },
        {
            "id": "drop-1",
            "signature": "sig-drop-1",
            "description": "submit order",
            "test_module": "Order",
            "retained_final": False,
        },
        {
            "id": "drop-2",
            "signature": "sig-drop-2",
            "description": "Submit   Order",
            "test_module": "Payment",
            "retained_final": False,
        },
    ]

    summary = summarize_final_description_dedup_drops(rows, sample_limit=1)

    assert summary["final_description_dedup_drop_signatures"] == {"sig-drop-1", "sig-drop-2"}
    assert summary["final_description_dedup_drop_count"] == 2
    assert summary["final_description_dedup_drop_samples"] == [
        {
            "signature": "sig-drop-1",
            "description_key": "submit order",
            "case_id": "drop-1",
            "test_module": "Order",
        }
    ]


def test_summarize_final_description_dedup_priority_breakdown_groups_by_priority() -> None:
    rows = [
        {"priority_decision_state": "decided", "priority_final": "P0", "legacy_priority": "P2"},
        {"priority_decision_state": "conflict", "priority_final": "p1", "legacy_priority": "p1"},
        {"priority_decision_state": "optional", "priority_final": "P2", "legacy_priority": "P0"},
        {"priority_decision_state": "needs_input", "priority_final": "P9", "legacy_priority": ""},
        {"priority_decision_state": "invalid", "priority_final": "", "priority": "P2"},
    ]

    summary = summarize_final_description_dedup_and_priority_breakdown(rows)

    assert summary["priority_decision_state_breakdown"] == {
        "decided": 1,
        "conflict": 1,
        "undetermined": 1,
        "optional": 1,
        "invalid": 1,
    }
    assert summary["priority_final_breakdown"] == {"P0": 1, "P1": 1, "P2": 1, "null": 2}
    assert summary["legacy_priority_breakdown"] == {"P0": 1, "P1": 1, "P2": 1, "UNKNOWN": 2}
    assert summary["priority_conflict_count"] == 1
    assert summary["priority_undetermined_count"] == 1
    assert summary["priority_optional_count"] == 1
    assert summary["priority_invalid_count"] == 1
    assert summary["priority_quality_gate_failed"] is True
    assert summary["needs_priority_review"] is True


def test_summarize_priority_decision_breakdown_handles_empty_and_invalid_values() -> None:
    summary = summarize_priority_decision_breakdown(
        [
            {"priority_decision_state": "", "priority_final": "", "legacy_priority": ""},
            {"priority_decision_state": "decided", "priority_final": "p0", "legacy_priority": "p1"},
            "ignored",
        ]
    )

    assert summary["priority_decision_state_breakdown"] == {
        "decided": 1,
        "conflict": 0,
        "undetermined": 1,
        "optional": 0,
        "invalid": 0,
    }
    assert summary["priority_final_breakdown"] == {"P0": 1, "P1": 0, "P2": 0, "null": 1}
    assert summary["legacy_priority_breakdown"] == {"P0": 0, "P1": 1, "P2": 0, "UNKNOWN": 1}
    assert summary["priority_conflict_count"] == 0
    assert summary["priority_undetermined_count"] == 1
    assert summary["priority_optional_count"] == 0
    assert summary["priority_invalid_count"] == 0
    assert summary["priority_quality_gate_failed"] is False
    assert summary["needs_priority_review"] is True


def test_summarize_final_description_dedup_ignores_non_dict_cases() -> None:
    rows = [
        {
            "signature": "sig-keep",
            "description": "Login succeeds",
            "retained_final": True,
            "priority_decision_state": "decided",
            "priority_final": "P0",
            "legacy_priority": "P1",
        },
        "ignored",
        None,
        ["also ignored"],
        {
            "signature": "sig-drop",
            "description": "Login   succeeds",
            "retained_final": False,
            "priority_decision_state": "decided",
            "priority_final": "P1",
            "legacy_priority": "P2",
        },
    ]

    summary = summarize_final_description_dedup_and_priority_breakdown(rows)

    assert summary["final_description_dedup_drop_signatures"] == {"sig-drop"}
    assert summary["final_description_dedup_drop_count"] == 1
    assert summary["priority_decision_state_breakdown"] == {
        "decided": 2,
        "conflict": 0,
        "undetermined": 0,
        "optional": 0,
        "invalid": 0,
    }
    assert summary["priority_final_breakdown"] == {"P0": 1, "P1": 1, "P2": 0, "null": 0}
    assert summary["legacy_priority_breakdown"] == {"P0": 0, "P1": 1, "P2": 1, "UNKNOWN": 0}


def test_final_dedup_priority_summary_fields_normalizes_payload_without_mutating_input() -> None:
    source = {
        "priority_decision_state_breakdown": {"conflict": 2},
        "priority_final_breakdown": {"P0": 1},
        "legacy_priority_breakdown": {"P2": 3},
        "priority_conflict_count": "2",
        "priority_undetermined_count": 1,
        "priority_optional_count": None,
        "priority_invalid_count": "4",
        "priority_quality_gate_failed": False,
        "needs_priority_review": True,
    }

    fields = final_dedup_priority_summary_fields(source)
    fields["priority_decision_state_breakdown"]["conflict"] = 99

    assert source["priority_decision_state_breakdown"] == {"conflict": 2}
    assert fields == {
        "priority_decision_state_breakdown": {"conflict": 99},
        "priority_final_breakdown": {"P0": 1},
        "legacy_priority_breakdown": {"P2": 3},
        "priority_conflict_count": 2,
        "priority_undetermined_count": 1,
        "priority_optional_count": 0,
        "priority_invalid_count": 4,
        "priority_quality_gate_failed": True,
        "needs_priority_review": True,
    }


def test_final_dedup_priority_summary_fields_defaults_empty_payload() -> None:
    assert final_dedup_priority_summary_fields(None) == {
        "priority_decision_state_breakdown": {},
        "priority_final_breakdown": {},
        "legacy_priority_breakdown": {},
        "priority_conflict_count": 0,
        "priority_undetermined_count": 0,
        "priority_optional_count": 0,
        "priority_invalid_count": 0,
        "priority_quality_gate_failed": False,
        "needs_priority_review": False,
    }


def test_resolve_final_duplicate_project_profile_returns_original_by_default() -> None:
    calls: list[dict[str, object]] = []
    profile = {"profile_source": "flow", "scenario_policy": {"enabled": True}}

    resolved = resolve_final_duplicate_project_profile(
        flow_project_profile=profile,
        flow_governance_summary={"applied": True},
        final_shortfall_supplement_applied=False,
        effective_generation_coverage_mode="standard",
        flow_profile_with_scenario_policy_fn=lambda source, **kwargs: calls.append(
            {"source": source, "kwargs": kwargs}
        )
        or {"unexpected": True},
    )

    assert resolved is profile
    assert calls == []


def test_resolve_final_duplicate_project_profile_relaxed_backfill_uses_policy_profile() -> None:
    calls: list[dict[str, object]] = []
    profile = {"profile_source": "flow", "scenario_policy": {"enabled": True}}

    def record_policy_profile(source: dict[str, object], **kwargs: object) -> dict[str, object]:
        calls.append({"source": source, "kwargs": kwargs})
        return {"profile_source": "flow", "scenario_policy": {"enabled": False}}

    resolved = resolve_final_duplicate_project_profile(
        flow_project_profile=profile,
        flow_governance_summary={"relaxed_for_floor_backfill": True},
        final_shortfall_supplement_applied=False,
        effective_generation_coverage_mode="full_regression",
        flow_profile_with_scenario_policy_fn=record_policy_profile,
    )

    assert resolved == {"profile_source": "flow", "scenario_policy": {"enabled": False}}
    assert calls == [
        {
            "source": profile,
            "kwargs": {
                "coverage_mode": "full_regression",
                "disable_scenario_pruning": True,
                "intent_duplicate_cap": 1_000_000,
                "relaxed_for_floor_backfill": True,
            },
        }
    ]


def test_resolve_final_duplicate_project_profile_shortfall_supplement_uses_policy_profile() -> None:
    calls: list[dict[str, object]] = []
    profile = {"profile_source": "flow", "scenario_policy": {"enabled": True}}

    def record_policy_profile(source: dict[str, object], **kwargs: object) -> dict[str, object]:
        calls.append({"source": source, "kwargs": kwargs})
        return {"profile_source": "flow", "coverage_mode": kwargs["coverage_mode"]}

    resolved = resolve_final_duplicate_project_profile(
        flow_project_profile=profile,
        flow_governance_summary=None,
        final_shortfall_supplement_applied=True,
        effective_generation_coverage_mode=None,
        flow_profile_with_scenario_policy_fn=record_policy_profile,
    )

    assert resolved == {"profile_source": "flow", "coverage_mode": ""}
    assert calls[0]["source"] is profile
    assert calls[0]["kwargs"] == {
        "coverage_mode": "",
        "disable_scenario_pruning": True,
        "intent_duplicate_cap": 1_000_000,
        "relaxed_for_floor_backfill": True,
    }


def test_resolve_final_duplicate_project_profile_policy_result_does_not_share_input() -> None:
    profile = {"profile_source": "flow", "scenario_policy": {"enabled": True}}

    def record_policy_profile(source: dict[str, object], **_kwargs: object) -> dict[str, object]:
        return {
            "profile_source": str(source["profile_source"]),
            "scenario_policy": dict(source["scenario_policy"]),  # type: ignore[arg-type]
        }

    resolved = resolve_final_duplicate_project_profile(
        flow_project_profile=profile,
        flow_governance_summary={"relaxed_for_floor_backfill": True},
        final_shortfall_supplement_applied=False,
        effective_generation_coverage_mode="standard",
        flow_profile_with_scenario_policy_fn=record_policy_profile,
    )
    resolved["scenario_policy"]["enabled"] = False  # type: ignore[index]

    assert resolved is not profile
    assert resolved["scenario_policy"] is not profile["scenario_policy"]
    assert profile == {"profile_source": "flow", "scenario_policy": {"enabled": True}}


def test_build_review_flow_structure_fields_normalizes_outline_and_duplicate_samples() -> None:
    fields = build_review_flow_structure_fields(
        {
            "flow_outline": {"flow_order": ["login", "", "submit"], "flow_labels": {"login": "Login"}},
            "stage_breakdown": {"login": 2},
            "missing_flow_stages": ["pay", ""],
            "missing_flow_stage_count": "1",
            "misordered_count": 2,
            "duplicate_cluster_count": 3,
            "duplicate_case_count": 4,
            "duplicate_clusters": [{"scenario": "same"}, "ignored"],
        }
    )

    assert fields == {
        "flow_order": ["login", "submit"],
        "flow_labels": {"login": "Login"},
        "flow_stage_breakdown": {"login": 2},
        "flow_missing_stages": ["pay"],
        "flow_missing_stage_count": 1,
        "flow_misordered_count": 2,
        "scenario_duplicate_cluster_count": 3,
        "scenario_duplicate_case_count": 4,
        "scenario_duplicate_clusters": [{"scenario": "same"}],
    }


def test_build_final_flow_structure_fields_keeps_final_governance_snapshot() -> None:
    fields = build_final_flow_structure_fields(
        final_independent_case_structure={
            "stage_breakdown": {"report": 1},
            "missing_flow_stages": ["archive"],
            "missing_flow_stage_count": "1",
            "misordered_count": 2,
        },
        final_duplicate_excess={
            "duplicate_excess_cluster_count": 3,
            "duplicate_excess_case_count": 4,
            "duplicate_excess_clusters": [{"scenario": "final"}, ["ignored"]],
        },
        final_case_structure={"duplicate_cluster_count": 5, "duplicate_case_count": 6},
        final_order_flow_governance_summary={
            "applied": True,
            "execution_group_order": ["main_smoke", "permission"],
            "execution_orchestration_plan": {
                "planned_case_count": 2,
                "execution_group_order": ["main_smoke", "permission"],
            },
        },
    )

    assert fields == {
        "final_flow_stage_breakdown": {"report": 1},
        "final_flow_missing_stages": ["archive"],
        "final_flow_missing_stage_count": 1,
        "final_flow_misordered_count": 2,
        "final_scenario_duplicate_cluster_count": 3,
        "final_scenario_duplicate_case_count": 4,
        "final_scenario_duplicate_clusters": [{"scenario": "final"}],
        "final_scenario_duplicate_raw_cluster_count": 5,
        "final_scenario_duplicate_raw_case_count": 6,
        "final_order_flow_governance": {
            "applied": True,
            "execution_group_order": ["main_smoke", "permission"],
            "execution_orchestration_plan": {
                "planned_case_count": 2,
                "execution_group_order": ["main_smoke", "permission"],
            },
        },
        "final_execution_group_order": ["main_smoke", "permission"],
        "final_execution_orchestration_plan": {
            "planned_case_count": 2,
            "execution_group_order": ["main_smoke", "permission"],
        },
    }


def test_build_flow_profile_governance_fields_counts_profiles_and_caps_indices() -> None:
    fields = build_flow_profile_governance_fields(
        fact_profile={
            "profile_source": "requirement",
            "confidence": 0.8,
            "confirmed_facts": ["A"],
            "forbidden_facts": ["B"],
            "pending_items": ["C"],
        },
        project_profile={"profile_source": "project", "confidence": 0.6},
        flow_governance_summary={
            "applied": True,
            "flow_reordered": True,
            "reason": "ordered",
            "scenario_duplicate_pruned_count": 3,
            "scenario_duplicate_pruned_indices": list(range(120)),
        },
    )

    assert fields["fact_profile_source"] == "requirement"
    assert fields["fact_profile_confidence"] == 0.8
    assert fields["fact_profile_confirmed_count"] == 1
    assert fields["fact_profile_forbidden_count"] == 1
    assert fields["fact_profile_pending_count"] == 1
    assert fields["project_profile_source"] == "project"
    assert fields["project_profile_confidence"] == 0.6
    assert fields["flow_governance_applied"] is True
    assert fields["flow_reordered"] is True
    assert fields["flow_governance_reason"] == "ordered"
    assert fields["scenario_duplicate_pruned_count"] == 3
    assert fields["scenario_duplicate_pruned_indices"] == list(range(100))


def test_build_execution_plan_flow_summary_fields_projects_counts_and_snapshot() -> None:
    plan = {
        "linear_executable": True,
        "linear_scope": "main",
        "main_chain_case_count": "2",
        "independent_case_count": 3,
        "isolation_case_count": 4,
        "broken_dependency_count": 5,
        "state_conflict_count": 6,
        "role_switch_count": 7,
        "extra": {"kept": True},
    }

    fields = build_execution_plan_flow_summary_fields(plan)

    assert fields == {
        "execution_plan": plan,
        "linear_executable": True,
        "linear_scope": "main",
        "main_chain_case_count": 2,
        "independent_case_count": 3,
        "isolation_case_count": 4,
        "broken_dependency_count": 5,
        "state_conflict_count": 6,
        "role_switch_count": 7,
    }


def test_review_flow_structure_summary_fields_normalizes_flow_duplicate_and_plan_fields() -> None:
    fields = review_flow_structure_summary_fields(
        review_case_structure={
            "flow_outline": {
                "flow_order": ["login", "", "submit"],
                "flow_labels": {"login": "Login"},
            },
            "stage_breakdown": {"login": 2},
            "missing_flow_stages": ["pay", ""],
            "missing_flow_stage_count": "1",
            "misordered_count": 2,
            "duplicate_cluster_count": 3,
            "duplicate_case_count": 4,
            "duplicate_clusters": [{"scenario": "same"}, "ignored"],
        },
        final_independent_case_structure={
            "stage_breakdown": {"report": 1},
            "missing_flow_stages": ["archive"],
            "missing_flow_stage_count": 1,
            "misordered_count": 0,
        },
        final_duplicate_excess={
            "duplicate_excess_cluster_count": 5,
            "duplicate_excess_case_count": 6,
            "duplicate_excess_clusters": [{"scenario": "final"}, ["ignored"]],
        },
        final_case_structure={"duplicate_cluster_count": 7, "duplicate_case_count": 8},
        final_order_flow_governance_summary={"applied": True},
        fact_profile={
            "profile_source": "requirement",
            "confidence": 0.8,
            "confirmed_facts": ["A"],
            "forbidden_facts": ["B"],
            "pending_items": ["C"],
        },
        project_profile={"profile_source": "project", "confidence": 0.6},
        flow_governance_summary={
            "applied": True,
            "flow_reordered": True,
            "reason": "ordered",
            "scenario_duplicate_pruned_count": 9,
            "scenario_duplicate_pruned_indices": list(range(120)),
        },
        execution_plan_summary={
            "linear_executable": True,
            "linear_scope": "main",
            "main_chain_case_count": 2,
            "independent_case_count": 3,
            "isolation_case_count": 4,
            "broken_dependency_count": 5,
            "state_conflict_count": 6,
            "role_switch_count": 7,
        },
    )

    assert set(fields) == {
        "flow_order",
        "flow_labels",
        "flow_stage_breakdown",
        "flow_missing_stages",
        "flow_missing_stage_count",
        "flow_misordered_count",
        "scenario_duplicate_cluster_count",
        "scenario_duplicate_case_count",
        "scenario_duplicate_clusters",
        "final_flow_stage_breakdown",
        "final_flow_missing_stages",
        "final_flow_missing_stage_count",
        "final_flow_misordered_count",
        "final_scenario_duplicate_cluster_count",
        "final_scenario_duplicate_case_count",
        "final_scenario_duplicate_clusters",
        "final_scenario_duplicate_raw_cluster_count",
        "final_scenario_duplicate_raw_case_count",
        "final_order_flow_governance",
        "final_execution_group_order",
        "final_execution_orchestration_plan",
        "fact_profile_source",
        "fact_profile_confidence",
        "fact_profile_confirmed_count",
        "fact_profile_forbidden_count",
        "fact_profile_pending_count",
        "project_profile_source",
        "project_profile_confidence",
        "flow_governance_applied",
        "flow_reordered",
        "flow_governance_reason",
        "scenario_duplicate_pruned_count",
        "scenario_duplicate_pruned_indices",
        "execution_plan",
        "linear_executable",
        "linear_scope",
        "main_chain_case_count",
        "independent_case_count",
        "isolation_case_count",
        "broken_dependency_count",
        "state_conflict_count",
        "role_switch_count",
    }
    assert fields["flow_order"] == ["login", "submit"]
    assert fields["flow_labels"] == {"login": "Login"}
    assert fields["flow_missing_stages"] == ["pay"]
    assert fields["scenario_duplicate_clusters"] == [{"scenario": "same"}]
    assert fields["final_scenario_duplicate_clusters"] == [{"scenario": "final"}]
    assert fields["final_scenario_duplicate_raw_cluster_count"] == 7
    assert fields["final_execution_group_order"] == []
    assert fields["final_execution_orchestration_plan"] == {}
    assert fields["fact_profile_confirmed_count"] == 1
    assert fields["project_profile_source"] == "project"
    assert fields["scenario_duplicate_pruned_indices"] == list(range(100))
    assert fields["execution_plan"] == {
        "linear_executable": True,
        "linear_scope": "main",
        "main_chain_case_count": 2,
        "independent_case_count": 3,
        "isolation_case_count": 4,
        "broken_dependency_count": 5,
        "state_conflict_count": 6,
        "role_switch_count": 7,
    }
    assert fields["linear_executable"] is True
    assert fields["role_switch_count"] == 7


def test_review_flow_structure_summary_fields_tolerates_non_list_duplicate_clusters() -> None:
    fields = review_flow_structure_summary_fields(
        review_case_structure={"duplicate_clusters": {"unexpected": "mapping"}},
        final_independent_case_structure=None,
        final_duplicate_excess={
            "duplicate_excess_clusters": [
                {"index": index}
                for index in range(25)
            ],
        },
        final_case_structure=None,
        final_order_flow_governance_summary=None,
        fact_profile=None,
        project_profile=None,
        flow_governance_summary=None,
        execution_plan_summary=None,
    )

    assert fields["scenario_duplicate_clusters"] == []
    assert fields["final_scenario_duplicate_clusters"] == [
        {"index": index}
        for index in range(20)
    ]


def test_review_flow_structure_summary_fields_defaults_empty_payloads() -> None:
    fields = review_flow_structure_summary_fields(
        review_case_structure=None,
        final_independent_case_structure=None,
        final_duplicate_excess=None,
        final_case_structure=None,
        final_order_flow_governance_summary=None,
        fact_profile=None,
        project_profile=None,
        flow_governance_summary=None,
        execution_plan_summary=None,
    )

    assert fields["flow_order"] == []
    assert fields["flow_stage_breakdown"] == {}
    assert fields["scenario_duplicate_clusters"] == []
    assert fields["final_flow_missing_stages"] == []
    assert fields["final_scenario_duplicate_case_count"] == 0
    assert fields["final_execution_group_order"] == []
    assert fields["final_execution_orchestration_plan"] == {}
    assert fields["fact_profile_source"] == ""
    assert fields["project_profile_confidence"] == 0.0
    assert fields["scenario_duplicate_pruned_indices"] == []
    assert fields["execution_plan"] == {}
    assert fields["linear_executable"] is False
