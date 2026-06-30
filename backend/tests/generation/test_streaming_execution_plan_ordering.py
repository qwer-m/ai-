from __future__ import annotations

from copy import deepcopy

from modules.testing.test_generation_components.postprocess.streaming_execution_plan_ordering import (
    apply_existing_execution_group_ordering,
    apply_final_independent_case_ordering,
    build_execution_orchestration_plan,
    execution_side_suite_order_labels,
    execution_side_suite_order_text,
    order_execution_plan_cases,
    order_independent_cases_by_execution_plan,
)


def _case(
    case_id: str,
    *,
    signature: str | None = None,
    group: str = "independent_functional",
    priority: str = "P2",
    test_module: str = "module",
    description: str = "description",
) -> dict:
    return {
        "id": case_id,
        "signature": signature or case_id,
        "group": group,
        "priority": priority,
        "test_module": test_module,
        "description": description,
    }


def _signature(item: dict) -> str:
    return str(item.get("signature") or "")


def _infer_group(item: dict, *, in_main_chain: bool = False) -> str:
    assert in_main_chain is False
    return str(item.get("group") or "")


def _case_priority(item: dict) -> str:
    return str(item.get("priority") or "")


def _case_text_field(item: dict, field: str) -> str:
    return str(item.get(field) or "")


def _flow_profile(project_profile: dict | None, **policy_updates: object) -> dict:
    profile = dict(project_profile or {})
    scenario_policy = dict(profile.get("scenario_cluster_policy") or {})
    scenario_policy.update(policy_updates)
    profile["scenario_cluster_policy"] = scenario_policy
    return profile


def _order(
    candidate_cases: list[dict],
    selected_by_stage: list[tuple[str, str, dict]] | None = None,
    selected_signatures: set[str] | None = None,
) -> list[dict]:
    return order_execution_plan_cases(
        candidate_cases,
        selected_by_stage or [],
        selected_signatures or set(),
        _signature,
        _infer_group,
        _case_priority,
        _case_text_field,
    )


def test_execution_side_suite_order_text_matches_sorting_rank() -> None:
    assert execution_side_suite_order_labels() == [
        "permission/security",
        "exception/recovery",
        "boundary/state rollback",
        "independent functional",
        "UI/display",
    ]
    assert (
        execution_side_suite_order_text()
        == "permission/security -> exception/recovery -> boundary/state rollback -> independent functional -> UI/display"
    )


def test_order_execution_plan_cases_keeps_main_chain_cases_first() -> None:
    main_entry = _case("TC-main-entry", group="display", priority="P2")
    main_commit = _case("TC-main-commit", group="boundary", priority="P2")
    selected_by_stage = [
        ("entry", "Entry", main_entry),
        ("commit", "Commit", main_commit),
    ]
    candidate_cases = [
        _case("TC-permission-p0", group="permission", priority="P0"),
        dict(main_entry),
        _case("TC-exception-p0", group="exception", priority="P0"),
    ]

    ordered = _order(candidate_cases, selected_by_stage)

    assert [item["id"] for item in ordered] == [
        "TC-main-entry",
        "TC-main-commit",
        "TC-permission-p0",
        "TC-exception-p0",
    ]


def test_order_execution_plan_cases_excludes_selected_signatures() -> None:
    selected_signatures = {"selected-permission"}
    candidate_cases = [
        _case("TC-selected", signature="selected-permission", group="permission", priority="P0"),
        _case("TC-fresh", signature="fresh-boundary", group="boundary", priority="P0"),
    ]

    ordered = _order(candidate_cases, selected_signatures=selected_signatures)

    assert [item["id"] for item in ordered] == ["TC-fresh"]
    assert selected_signatures == {"selected-permission"}


def test_order_execution_plan_cases_sorts_remaining_cases_stably() -> None:
    candidate_cases = [
        _case("TC-display-p0", group="display", priority="P0"),
        _case("TC-permission-p2", group="permission", priority="P2", test_module="z"),
        _case("TC-permission-p0-z", group="permission", priority="P0", test_module="z"),
        _case("TC-exception-p0", group="exception", priority="P0"),
        _case("TC-permission-p0-a", group="permission", priority="P0", test_module="a"),
        _case("TC-stable-first", group="boundary", priority="P1", test_module="same", description="same"),
        _case("TC-stable-second", group="boundary", priority="P1", test_module="same", description="same"),
        _case("TC-boundary-p0", group="boundary", priority="P0"),
    ]

    ordered = _order(candidate_cases)

    assert [item["id"] for item in ordered] == [
        "TC-permission-p0-a",
        "TC-permission-p0-z",
        "TC-permission-p2",
        "TC-exception-p0",
        "TC-boundary-p0",
        "TC-stable-first",
        "TC-stable-second",
        "TC-display-p0",
    ]


def test_order_execution_plan_cases_does_not_mutate_inputs() -> None:
    main_case = _case("TC-main", group="display", priority="P2")
    candidate_case = _case("TC-candidate", group="permission", priority="P0")
    candidate_cases = [candidate_case]
    selected_by_stage = [("entry", "Entry", main_case)]
    selected_signatures = {"already-selected"}
    original_candidates = deepcopy(candidate_cases)
    original_selected_by_stage = deepcopy(selected_by_stage)
    original_selected_signatures = set(selected_signatures)

    ordered = _order(candidate_cases, selected_by_stage, selected_signatures)

    assert candidate_cases == original_candidates
    assert selected_by_stage == original_selected_by_stage
    assert selected_signatures == original_selected_signatures
    assert ordered[0] is not main_case
    assert ordered[1] is not candidate_case


def test_order_independent_cases_by_execution_plan_groups_suites_stably() -> None:
    cases = [
        {"id": "TC-display-1", "execution_group": "display"},
        {"id": "TC-permission-1", "execution_group": "permission"},
        {"id": "TC-boundary-1", "execution_group": "boundary"},
        {"id": "TC-permission-2", "execution_group": "permission"},
        {"id": "TC-exception-1", "execution_group": "exception"},
        {"id": "TC-independent-1", "execution_group": "independent_functional"},
    ]

    ordered = order_independent_cases_by_execution_plan(cases)

    assert [item["id"] for item in ordered] == [
        "TC-permission-1",
        "TC-permission-2",
        "TC-exception-1",
        "TC-boundary-1",
        "TC-independent-1",
        "TC-display-1",
    ]
    assert [item["id"] for item in cases] == [
        "TC-display-1",
        "TC-permission-1",
        "TC-boundary-1",
        "TC-permission-2",
        "TC-exception-1",
        "TC-independent-1",
    ]


def test_apply_existing_execution_group_ordering_restores_final_physical_order() -> None:
    cases = [
        {"id": "TC-display", "execution_group": "display", "execution_sequence": 6},
        {"id": "TC-main-2", "execution_group": "main_smoke", "execution_sequence": 2},
        {"id": "TC-permission", "execution_group": "permission", "execution_sequence": 3},
        {"id": "TC-main-1", "execution_group": "main_smoke", "execution_sequence": 1},
        {"id": "TC-boundary", "execution_group": "boundary", "execution_sequence": 5},
        {"id": "TC-exception", "execution_group": "exception", "execution_sequence": 4},
    ]

    ordered = apply_existing_execution_group_ordering(cases, start_id=10)

    assert [item["execution_group"] for item in ordered] == [
        "main_smoke",
        "main_smoke",
        "permission",
        "exception",
        "boundary",
        "display",
    ]
    assert [item["id"] for item in ordered] == [
        "TC-010",
        "TC-011",
        "TC-012",
        "TC-013",
        "TC-014",
        "TC-015",
    ]
    assert [item["execution_sequence"] for item in ordered] == [1, 2, 3, 4, 5, 6]
    assert cases[0]["id"] == "TC-display"


def test_apply_existing_execution_group_ordering_leaves_legacy_cases_without_groups_unchanged() -> None:
    cases = [
        {"id": "TC-101", "test_module": "A"},
        {"id": "TC-102", "test_module": "B"},
    ]

    ordered = apply_existing_execution_group_ordering(cases, start_id=10)

    assert ordered == cases
    assert ordered is not cases
    assert "execution_sequence" not in ordered[0]


def test_apply_existing_execution_group_ordering_leaves_custom_groups_unchanged() -> None:
    cases = [
        {"id": "TC-custom", "execution_group": "schedule-main", "execution_sequence": 1},
        {"id": "TC-display", "execution_group": "display", "execution_sequence": 2},
    ]

    ordered = apply_existing_execution_group_ordering(cases, start_id=10)

    assert ordered == cases
    assert ordered[0]["id"] == "TC-custom"


def test_build_execution_orchestration_plan_exposes_main_chain_and_side_suites() -> None:
    plan = build_execution_orchestration_plan(
        [
            {
                "id": "TC-main-1",
                "execution_group": "main_smoke",
                "execution_sequence": 1,
                "main_chain_step": 1,
                "main_chain_stage": "entry",
                "main_chain_stage_kind": "entry",
                "source_state": "initial",
                "target_state": "entered",
                "role": "teacher",
                "depends_on": [],
            },
            {
                "id": "TC-main-2",
                "execution_group": "main_smoke",
                "execution_sequence": 2,
                "main_chain_step": 2,
                "main_chain_stage": "commit",
                "main_chain_stage_kind": "commit",
                "source_state": "entered",
                "target_state": "committed",
                "role": "teacher",
                "depends_on": ["TC-main-1"],
            },
            {"id": "TC-display", "execution_group": "display"},
            {"id": "TC-permission", "execution_group": "permission"},
            {"id": "TC-exception", "execution_group": "exception"},
        ]
    )

    assert plan["plan_first"] is True
    assert plan["planned_case_count"] == 5
    assert plan["main_chain_case_count"] == 2
    assert plan["side_suite_count"] == 3
    assert plan["execution_group_order"] == ["main_smoke", "permission", "exception", "display"]
    assert plan["main_chain"][0] == {
        "case_id": "TC-main-1",
        "execution_sequence": 1,
        "main_chain_step": 1,
        "stage": "entry",
        "stage_kind": "entry",
        "source_state": "initial",
        "target_state": "entered",
        "role": "teacher",
        "depends_on": [],
    }
    assert plan["main_chain"][1]["depends_on"] == ["TC-main-1"]
    assert plan["side_suites"] == [
        {
            "suite_id": "permission_suite",
            "execution_group": "permission",
            "rank": 1,
            "case_ids": ["TC-permission"],
            "case_count": 1,
        },
        {
            "suite_id": "exception_suite",
            "execution_group": "exception",
            "rank": 2,
            "case_ids": ["TC-exception"],
            "case_count": 1,
        },
        {
            "suite_id": "display_suite",
            "execution_group": "display",
            "rank": 5,
            "case_ids": ["TC-display"],
            "case_count": 1,
        },
    ]


def test_apply_final_independent_case_ordering_applies_successful_governance_order() -> None:
    calls: list[dict] = []

    def govern(requirement: str, cases: list[dict], **kwargs: object) -> tuple[list[dict], dict]:
        calls.append({"requirement": requirement, "cases": cases, **kwargs})
        return [dict(cases[1]), dict(cases[0])], {"applied": True, "flow_reordered": True}

    main_case = _case("TC-main")
    main_case["execution_group"] = "main_smoke"
    first_independent = _case("TC-independent-1")
    first_independent["execution_group"] = "independent"
    second_independent = _case("TC-independent-2")
    second_independent["execution_group"] = "independent"

    ordered, summary = apply_final_independent_case_ordering(
        [first_independent, main_case, second_independent],
        requirement="真实需求",
        start_id=10,
        flow_project_profile={"scenario_cluster_policy": {"existing": "kept"}},
        flow_profile_with_scenario_policy_fn=_flow_profile,
        govern_cases_by_flow_structure_fn=govern,
    )

    assert [item["id"] for item in ordered] == [
        "TC-main",
        "TC-independent-2",
        "TC-independent-1",
    ]
    assert summary["applied"] is True
    assert summary["flow_reordered"] is True
    assert summary["execution_group_order"] == ["main_smoke", "independent"]
    assert summary["execution_orchestration_plan"]["planned_case_count"] == 3
    assert [item["id"] for item in calls[0]["cases"]] == ["TC-independent-1", "TC-independent-2"]
    assert calls[0]["start_id"] == 11
    assert calls[0]["renumber_ids"] is False
    assert calls[0]["max_per_scenario"] == 2
    assert calls[0]["project_profile"]["scenario_cluster_policy"] == {
        "existing": "kept",
        "disable_scenario_pruning": True,
        "intent_duplicate_cap": 1_000_000,
        "final_order_only": True,
    }


def test_apply_final_independent_case_ordering_keeps_original_when_ordered_length_differs() -> None:
    def govern(_requirement: str, cases: list[dict], **_kwargs: object) -> tuple[list[dict], dict]:
        return [dict(cases[0])], {"applied": True, "flow_reordered": True, "dropped": 1}

    main_case = _case("TC-main")
    main_case["execution_group"] = "main_smoke"
    independent_cases = [_case("TC-independent-1"), _case("TC-independent-2")]

    ordered, summary = apply_final_independent_case_ordering(
        [main_case, *independent_cases],
        requirement="真实需求",
        start_id=3,
        flow_project_profile={},
        flow_profile_with_scenario_policy_fn=_flow_profile,
        govern_cases_by_flow_structure_fn=govern,
    )

    assert [item["id"] for item in ordered] == [
        "TC-main",
        "TC-independent-1",
        "TC-independent-2",
    ]
    assert summary == {"applied": True, "flow_reordered": True, "dropped": 1}


def test_apply_final_independent_case_ordering_returns_exception_summary() -> None:
    def govern(_requirement: str, _cases: list[dict], **_kwargs: object) -> tuple[list[dict], dict]:
        raise RuntimeError("x" * 240)

    original = [_case("TC-main"), _case("TC-independent")]

    ordered, summary = apply_final_independent_case_ordering(
        original,
        requirement="真实需求",
        start_id=1,
        flow_project_profile={},
        flow_profile_with_scenario_policy_fn=_flow_profile,
        govern_cases_by_flow_structure_fn=govern,
    )

    assert ordered == original
    assert ordered is not original
    assert all(result_item is not source_item for result_item, source_item in zip(ordered, original))
    assert summary == {
        "applied": False,
        "reason": "exception",
        "exception": "x" * 200,
    }


def test_apply_final_independent_case_ordering_keeps_main_smoke_first_and_offsets_start_id() -> None:
    calls: list[dict] = []

    def govern(_requirement: str, cases: list[dict], **kwargs: object) -> tuple[list[dict], dict]:
        calls.append({"cases": cases, **kwargs})
        return [dict(item) for item in reversed(cases)], {"applied": True}

    first_main = _case("TC-main-1")
    first_main["execution_group"] = "main_smoke"
    second_main = _case("TC-main-2")
    second_main["execution_group"] = "main_smoke"
    independent = [_case("TC-independent-1"), _case("TC-independent-2")]

    ordered, _summary = apply_final_independent_case_ordering(
        [independent[0], first_main, independent[1], second_main],
        requirement="真实需求",
        start_id=20,
        flow_project_profile={},
        flow_profile_with_scenario_policy_fn=_flow_profile,
        govern_cases_by_flow_structure_fn=govern,
    )

    assert [item["id"] for item in ordered] == [
        "TC-main-1",
        "TC-main-2",
        "TC-independent-2",
        "TC-independent-1",
    ]
    assert calls[0]["start_id"] == 22
    assert [item["id"] for item in calls[0]["cases"]] == ["TC-independent-1", "TC-independent-2"]


def test_apply_final_independent_case_ordering_applies_suite_order_after_governance() -> None:
    def govern(_requirement: str, cases: list[dict], **_kwargs: object) -> tuple[list[dict], dict]:
        return [dict(item) for item in cases], {"applied": True, "flow_reordered": False}

    main_case = _case("TC-main")
    main_case["execution_group"] = "main_smoke"
    main_case["execution_sequence"] = 9
    display_case = _case("TC-display")
    display_case["execution_group"] = "display"
    display_case["execution_sequence"] = 1
    permission_case = _case("TC-permission")
    permission_case["execution_group"] = "permission"
    permission_case["execution_sequence"] = 3
    exception_case = _case("TC-exception")
    exception_case["execution_group"] = "exception"
    exception_case["execution_sequence"] = 2

    ordered, summary = apply_final_independent_case_ordering(
        [display_case, main_case, permission_case, exception_case],
        requirement="真实需求",
        start_id=1,
        flow_project_profile={},
        flow_profile_with_scenario_policy_fn=_flow_profile,
        govern_cases_by_flow_structure_fn=govern,
    )

    assert [item["id"] for item in ordered] == [
        "TC-main",
        "TC-permission",
        "TC-exception",
        "TC-display",
    ]
    assert [item["execution_sequence"] for item in ordered] == [1, 2, 3, 4]
    assert summary["execution_group_order"] == ["main_smoke", "permission", "exception", "display"]
    assert summary["execution_orchestration_plan"]["side_suites"][0]["case_ids"] == ["TC-permission"]


def test_apply_final_independent_case_ordering_does_not_mutate_inputs_and_filters_non_dicts() -> None:
    def govern(_requirement: str, cases: list[dict], **_kwargs: object) -> tuple[list[dict], dict]:
        cases[0]["description"] = "governance-mutated-copy"
        return [dict(item) for item in cases], {"applied": True}

    main_case = _case("TC-main", description="original-main")
    main_case["execution_group"] = "main_smoke"
    independent_case = _case("TC-independent", description="original-independent")
    parsed_result = [main_case, "not-a-case", independent_case]
    original = deepcopy(parsed_result)

    ordered, summary = apply_final_independent_case_ordering(
        parsed_result,
        requirement="真实需求",
        start_id=1,
        flow_project_profile={},
        flow_profile_with_scenario_policy_fn=_flow_profile,
        govern_cases_by_flow_structure_fn=govern,
    )

    assert parsed_result == original
    assert [item["id"] for item in ordered] == ["TC-main", "TC-independent"]
    assert ordered[1]["description"] == "governance-mutated-copy"
    assert ordered[0] is not main_case
    assert ordered[1] is not independent_case
    assert summary["applied"] is True
    assert summary["execution_group_order"] == ["main_smoke", "independent_functional"]
