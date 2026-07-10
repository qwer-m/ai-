from __future__ import annotations

from modules.testing.test_generation_components.postprocess.result_postprocess import (
    normalize_final_case_priorities,
)
from modules.testing.test_generation_components.postprocess.streaming_execution_plan_ordering import (
    execution_group_order_rank,
)
from tests.rag.generation.quality_governance_harness import (
    run_quality_governance_cases as _run_cases,
)


def test_execution_plan_uses_workflow_blueprint_without_domain_template() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "checkout_flow",
                "name": "checkout flow",
                "steps": [
                    {
                        "id": "submit_order",
                        "label": "Submit order",
                        "actor": "student",
                        "state_in": "cart_ready",
                        "state_out": "order_created",
                        "match_keywords": ["submit order"],
                        "assertion": "order is created",
                    },
                    {
                        "id": "verify_paid",
                        "label": "Verify paid status",
                        "actor": "supervisor",
                        "state_in": "order_created",
                        "state_out": "paid_status_visible",
                        "match_keywords": ["paid status"],
                        "assertion": "status is paid",
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Checkout regression",
        cases=[
            {
                "id": "TC-001",
                "description": "Submit order creates an order record",
                "test_module": "checkout",
                "steps": ["Open checkout", "Submit order"],
                "expected_result": "order is created",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "steps": ["Open order detail"],
                "expected_result": "status is paid",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Network timeout shows retry action",
                "test_module": "checkout",
                "steps": ["Submit order during timeout"],
                "expected_result": "retry action is shown",
                "priority": "P0",
            },
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    assert [item.get("main_chain_stage") for item in main_cases] == ["submit_order", "verify_paid"]
    assert main_cases[0].get("depends_on") == []
    assert main_cases[1].get("depends_on") == [main_cases[0]["id"]]
    assert [item.get("role") for item in main_cases] == ["student", "supervisor"]
    assert [item.get("data_state") for item in main_cases] == ["order_created", "paid_status_visible"]
    assert all(str(item.get("fixture_key") or "") == "workflow_blueprint_chain_seed" for item in main_cases)
    timeout_case = next(item for item in output_cases if "timeout" in str(item.get("description") or "").lower())
    assert str(timeout_case.get("execution_group") or "") == "exception"
    summary = dict(result.get("review_decision_summary") or {})
    plan = dict(summary.get("execution_plan") or {})
    assert plan.get("workflow_blueprint_count") == 1
    assert plan.get("linear_executable") is True


def test_execution_plan_final_output_orders_mixed_side_suites_by_execution_plan() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "checkout_flow",
                "name": "checkout flow",
                "steps": [
                    {
                        "id": "submit_order",
                        "label": "Submit order",
                        "actor": "student",
                        "state_in": "cart_ready",
                        "state_out": "order_created",
                        "match_keywords": ["submit order"],
                        "assertion": "order is created",
                    },
                    {
                        "id": "verify_paid",
                        "label": "Verify paid status",
                        "actor": "supervisor",
                        "state_in": "order_created",
                        "state_out": "paid_status_visible",
                        "match_keywords": ["paid status"],
                        "assertion": "paid status is visible",
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Checkout execution order regression",
        cases=[
            {
                "id": "TC-001",
                "description": "Order detail color tag is displayed consistently",
                "test_module": "order detail",
                "steps": ["Open order detail", "Inspect status tag style"],
                "expected_result": "Paid status tag color and copy are displayed consistently",
                "priority": "P2",
                "execution_group": "display",
            },
            {
                "id": "TC-002",
                "description": "Submit order creates an order record",
                "test_module": "checkout",
                "steps": ["Open checkout", "Submit order"],
                "expected_result": "Order record is created and order id is returned",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Unauthorized user cannot submit order",
                "test_module": "checkout permission",
                "steps": ["Use readonly user", "Submit order"],
                "expected_result": "System blocks submission and shows permission denied message",
                "priority": "P1",
                "execution_group": "permission",
            },
            {
                "id": "TC-004",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "steps": ["Open created order detail", "Refresh payment status"],
                "expected_result": "Paid status is visible on order detail",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "Network timeout during submit shows retry action",
                "test_module": "checkout exception",
                "steps": ["Submit order while payment service times out"],
                "expected_result": "Retry action is shown and original cart remains unchanged",
                "priority": "P1",
                "execution_group": "exception",
            },
            {
                "id": "TC-006",
                "description": "Order quantity upper boundary is enforced",
                "test_module": "checkout boundary",
                "steps": ["Set quantity above maximum", "Submit order"],
                "expected_result": "System rejects quantity above maximum and keeps order unsubmitted",
                "priority": "P1",
                "execution_group": "boundary",
            },
            {
                "id": "TC-007",
                "description": "Coupon recalculation updates order total",
                "test_module": "checkout functional",
                "steps": ["Apply valid coupon", "Recalculate order total"],
                "expected_result": "Order total is recalculated with coupon discount",
                "priority": "P1",
                "execution_group": "independent_functional",
            },
        ],
        expected_count=12,
        feedback_control_state=state,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    groups = [str(item.get("execution_group") or "") for item in output_cases]
    assert groups[:2] == ["main_smoke", "main_smoke"]
    assert groups == sorted(
        groups,
        key=execution_group_order_rank,
    )
    assert [int(item.get("execution_sequence") or 0) for item in output_cases] == list(
        range(1, len(output_cases) + 1)
    )
    summary = dict(result.get("review_decision_summary") or {})
    assert summary.get("final_execution_group_order") == [
        "main_smoke",
        "permission",
        "exception",
        "boundary",
        "independent_functional",
        "display",
    ]


def test_execution_plan_attaches_transition_contract_to_main_smoke() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "checkout_flow",
                "name": "checkout flow",
                "steps": [
                    {
                        "id": "submit_order",
                        "label": "Submit order",
                        "actor": "student",
                        "state_in": "cart_ready",
                        "state_out": "order_created",
                        "match_keywords": ["submit order"],
                        "assertion": "order is created",
                    },
                    {
                        "id": "verify_paid",
                        "label": "Verify paid status",
                        "actor": "student",
                        "state_in": "order_created",
                        "state_out": "paid_status_visible",
                        "match_keywords": ["paid status"],
                        "assertion": "status is paid",
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Checkout regression",
        cases=[
            {
                "id": "TC-001",
                "description": "Submit order creates an order record",
                "test_module": "checkout",
                "steps": ["Open checkout", "Submit order"],
                "expected_result": "order is created",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "steps": ["Open order detail"],
                "expected_result": "status is paid",
                "priority": "P1",
            },
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    assert main_cases
    transitions = [dict(item.get("workflow_transition") or {}) for item in main_cases]
    assert [item.get("source_state") for item in transitions] == ["cart_ready", "order_created"]
    assert [item.get("target_state") for item in transitions] == ["order_created", "paid_status_visible"]
    assert all(item.get("path_type") == "positive" for item in transitions)
    assert all(item.get("blocking") is False for item in transitions)
    assert all(item.get("destructive") is False for item in transitions)
    assert all(item.get("can_advance_main_flow") is True for item in transitions)
    assert [item.get("workflow_id") for item in main_cases] == ["checkout_flow", "checkout_flow"]
    assert [item.get("source_state") for item in main_cases] == ["cart_ready", "order_created"]
    assert [item.get("target_state") for item in main_cases] == ["order_created", "paid_status_visible"]
    assert all(float(item.get("state_transition_confidence") or 0.0) >= 0.9 for item in main_cases)


def test_execution_plan_excludes_candidate_when_action_text_does_not_support_stage() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "assessment_flow",
                "workflow_id": "assessment_flow",
                "name": "assessment flow",
                "source_type": "current_requirement_extracted",
                "repository_source": "current_requirement_blueprint",
                "steps": [
                    {
                        "id": "entry",
                        "label": "Open activity entry",
                        "action": "Open activity entry",
                        "actor": "student",
                        "state_in": "initial",
                        "state_out": "entry_opened",
                        "stage_kind": "entry",
                        "allow_bridge": True,
                        "match_keywords": ["open activity entry"],
                    },
                    {
                        "id": "configure_assessment",
                        "label": "Configure assessment",
                        "action": "Choose grade and material version",
                        "actor": "student",
                        "state_in": "entry_opened",
                        "state_out": "assessment_configured",
                        "stage_kind": "configure",
                        "allow_bridge": True,
                        "match_keywords": ["choose grade"],
                    },
                    {
                        "id": "commit_assessment",
                        "label": "Submit assessment",
                        "action": "Finish all assessment questions and submit assessment",
                        "actor": "student",
                        "state_in": "assessment_configured",
                        "state_out": "assessment_submitted",
                        "stage_kind": "commit",
                        "allow_bridge": True,
                        "match_keywords": ["assessment"],
                    },
                    {
                        "id": "consume_course",
                        "label": "Open review course",
                        "action": "Open review course and start learning",
                        "actor": "student",
                        "state_in": "assessment_submitted",
                        "state_out": "course_opened",
                        "stage_kind": "consume",
                        "allow_bridge": True,
                        "match_keywords": ["open review course"],
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Assessment flow should submit answers before opening review course.",
        cases=[
            {
                "id": "TC-001",
                "description": "Open activity entry from homepage",
                "test_module": "entry",
                "steps": ["Open activity entry"],
                "expected_result": "activity entry page is opened",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Choose grade and material version before starting assessment",
                "test_module": "assessment setup",
                "steps": ["Choose grade", "Choose material version"],
                "expected_result": "assessment questions are loaded",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Course list confirms grade version switch and retains old assessment records",
                "test_module": "course list",
                "steps": ["Open switch version dialog", "Confirm switching grade version"],
                "expected_result": "old grade version records are retained and navigation follows target grade status",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "Open review course after submitted assessment",
                "test_module": "review course",
                "steps": ["Open review course"],
                "expected_result": "review course learning page is opened",
                "priority": "P0",
            },
        ],
        expected_count=6,
        feedback_control_state=state,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    commit_case = next(item for item in main_cases if item.get("main_chain_stage") == "commit_assessment")
    assert "grade version switch" not in str(commit_case.get("description") or "").lower()
    assert commit_case.get("workflow_contract_materialized_case") is True
    assert commit_case.get("steps") == ["Finish all assessment questions and submit assessment"]
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    excluded = [
        item for item in (plan.get("main_chain_excluded_candidates") or [])
        if item.get("case_id") == "TC-003" and item.get("stage_key") == "commit_assessment"
    ]
    assert excluded and excluded[0].get("reason") == "stage_action_not_supported_by_case_text"


def test_trusted_repository_contract_bridges_full_main_chain_when_candidates_do_not_match() -> None:
    states = [
        ("start", "Ready", "open_workflow", "ready", "started", "entry"),
        ("configure", "Configure", "configure_workflow", "started", "configured", "configure"),
        ("preview", "Preview", "preview_workflow", "configured", "preview_ready", "preview"),
        ("commit", "Commit", "commit_workflow", "preview_ready", "committed", "commit"),
        ("visible", "Visible", "show_downstream", "committed", "visible", "downstream_visibility"),
        ("consume", "Consume", "consume_workflow", "visible", "consumed", "consume"),
    ]
    state = {
        "workflow_blueprints": [
            {
                "id": "trusted_bridge_flow",
                "workflow_id": "trusted_bridge_flow",
                "name": "trusted bridge flow",
                "source_type": "human_reviewed",
                "repository_source": "workflow_blueprint_repository",
                "trusted": True,
                "steps": [
                    {
                        "id": step_id,
                        "label": label,
                        "action": action,
                        "actor": "student",
                        "state_in": state_in,
                        "state_out": state_out,
                        "stage_kind": stage_kind,
                        "allow_bridge": True,
                        "match_keywords": [f"__no_candidate_match_{step_id}__"],
                    }
                    for step_id, label, action, state_in, state_out, stage_kind in states
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Trusted repository contract bridge regression",
        cases=[
            {
                "id": "TC-001",
                "description": "Unrelated profile preference update",
                "test_module": "profile",
                "steps": ["Update profile preference"],
                "expected_result": "profile preference is updated",
                "priority": "P1",
            }
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    assert [item.get("main_chain_stage") for item in main_cases] == [item[0] for item in states]
    assert all(item.get("workflow_contract_materialized_case") is True for item in main_cases)
    assert not any(bool(item.get("generated_bridge_case")) for item in main_cases)
    assert all(str(item.get("priority") or "") == "P0" for item in main_cases)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    assert plan.get("trusted_workflow_contract_count") == 1
    assert plan.get("generated_bridge_case_count") == 0
    assert plan.get("workflow_contract_materialized_case_count") == len(states)
    assert plan.get("main_chain_stage_kinds") == [item[5] for item in states]
    assert plan.get("linear_executable") is True


def test_text_stage_classifier_keeps_save_and_display_as_commit() -> None:
    steps = [
        ("start", "open workflow alpha entry", "ready", "started"),
        ("configure", "configure schedule beta slot", "started", "configured"),
        ("preview", "review preview gamma summary", "configured", "preview_ready"),
        ("commit", "save plan and display delta confirmation", "preview_ready", "committed"),
        ("visible", "display saved plan on student home epsilon card", "committed", "visible"),
        ("consume", "learn course from visible plan zeta lesson", "visible", "consumed"),
    ]
    state = {
        "workflow_blueprints": [
            {
                "id": "save_display_flow",
                "workflow_id": "save_display_flow",
                "name": "save display flow",
                "source_type": "human_reviewed",
                "repository_source": "workflow_blueprint_repository",
                "trusted": True,
                "steps": [
                    {
                        "id": step_id,
                        "label": action,
                        "action": action,
                        "actor": "student",
                        "state_in": state_in,
                        "state_out": state_out,
                        "match_keywords": [action],
                    }
                    for step_id, action, state_in, state_out in steps
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Save plan then display it on student home",
        cases=[
            {
                "id": f"TC-{index:03d}",
                "description": action,
                "test_module": f"workflow {step_id}",
                "steps": [action, f"complete unique {step_id} operation"],
                "test_input": f"{state_in} dataset for {step_id}",
                "expected_result": "saved plan is visible on epsilon card" if step_id == "visible" else f"state reaches {state_out} for {step_id}",
                "priority": "P0",
            }
            for index, (step_id, action, state_in, state_out) in enumerate(steps, start=1)
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    assert plan.get("main_chain_stage_kinds")[3] == "commit"
    assert plan.get("main_chain_stage_kinds")[4] == "downstream_visibility"
    assert [dict(item.get("workflow_transition") or {}).get("stage_kind") for item in main_cases][3:5] == [
        "commit",
        "downstream_visibility",
    ]
    assert plan.get("linear_executable") is True


def test_persist_priority_normalization_preserves_execution_plan_p0() -> None:
    result = normalize_final_case_priorities(
        [
            {
                "id": "TC-001",
                "description": "Homepage course card title display",
                "test_module": "student home display",
                "preconditions": ["plan saved"],
                "steps": ["open student home"],
                "test_input": "saved plan",
                "expected_result": "course card title is visible",
                "priority": "P0",
                "priority_final": "P0",
                "execution_group": "main_smoke",
            }
        ],
        requirement_text="student home shows the saved plan",
    )

    assert str(result[0].get("priority") or "") == "P0"
    assert str(result[0].get("priority_final") or "") == "P0"
    assert str(result[0].get("priority_decision_source") or "") == "preserved_execution_plan_priority"


def test_execution_plan_excludes_negative_and_destructive_cases_from_main_smoke() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "create_plan_flow",
                "name": "create plan flow",
                "steps": [
                    {
                        "id": "configure_plan",
                        "label": "Configure plan",
                        "actor": "supervisor",
                        "state_in": "initial",
                        "state_out": "plan_configured",
                        "match_keywords": ["configure plan"],
                        "assertion": "plan is configured",
                    },
                    {
                        "id": "save_plan",
                        "label": "Save plan",
                        "actor": "supervisor",
                        "state_in": "plan_configured",
                        "state_out": "plan_saved",
                        "match_keywords": ["save plan"],
                        "assertion": "plan is saved",
                    },
                    {
                        "id": "student_visibility",
                        "label": "Student visibility",
                        "actor": "student",
                        "state_in": "plan_saved",
                        "state_out": "student_home_visible",
                        "match_keywords": ["student home visible"],
                        "assertion": "new plan is visible",
                    },
                    {
                        "id": "open_course",
                        "label": "Open course",
                        "actor": "student",
                        "state_in": "student_home_visible",
                        "state_out": "course_opened",
                        "match_keywords": ["open course"],
                        "assertion": "course page opens",
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Supervisor creates a plan, student sees the new plan and opens the course.",
        cases=[
            {
                "id": "TC-001",
                "description": "Configure plan with selected courses",
                "test_module": "plan create",
                "steps": ["Open create plan", "Select courses"],
                "expected_result": "plan is configured with selected courses",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Capacity shortage blocks configure plan",
                "test_module": "plan create",
                "steps": ["Select too many courses"],
                "expected_result": "system shows capacity limit and cannot continue",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Save plan successfully",
                "test_module": "plan create",
                "steps": ["Preview plan", "Save plan"],
                "expected_result": "plan is saved with id PLAN-100",
                "priority": "P1",
            },
            {
                "id": "TC-004",
                "description": "Save plan blocked by time conflict",
                "test_module": "plan create",
                "steps": ["Save plan with conflicting time"],
                "expected_result": "save is blocked and conflict message is shown",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "Student home visible after new plan sync",
                "test_module": "student home",
                "steps": ["Open student home"],
                "expected_result": "new plan is visible on student home",
                "priority": "P1",
            },
            {
                "id": "TC-006",
                "description": "Open course from student home",
                "test_module": "student home",
                "steps": ["Click course card"],
                "expected_result": "course page opens for PLAN-100",
                "priority": "P1",
            },
            {
                "id": "TC-007",
                "description": "Save plan fails during network timeout",
                "test_module": "plan create",
                "steps": ["Save plan during network timeout"],
                "expected_result": "save plan failed and retry action is shown",
                "priority": "P0",
            },
            {
                "id": "TC-008",
                "description": "Delete existing plan",
                "test_module": "plan management",
                "steps": ["Delete plan PLAN-100"],
                "expected_result": "plan is removed from management list",
                "priority": "P0",
            },
        ],
        expected_count=80,
        feedback_control_state=state,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    assert [item.get("main_chain_stage") for item in main_cases] == [
        "configure_plan",
        "save_plan",
        "student_visibility",
        "open_course",
    ]
    assert all(str(item.get("priority") or "") == "P0" for item in main_cases)
    main_descriptions = " ".join(str(item.get("description") or "") for item in main_cases).lower()
    assert "capacity" not in main_descriptions
    assert "conflict" not in main_descriptions
    assert "delete" not in main_descriptions
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    excluded_reasons = {str(item.get("reason") or "") for item in (plan.get("main_chain_excluded_candidates") or [])}
    assert "boundary_capacity" in excluded_reasons
    assert plan.get("linear_executable") is True
    final_breakdown = dict((result.get("review_decision_summary") or {}).get("priority_final_breakdown") or {})
    assert int(final_breakdown.get("P0") or 0) >= len(main_cases)


def test_execution_plan_does_not_infer_main_smoke_without_workflow_blueprint() -> None:
    result = _run_cases(
        requirement="Checkout regression",
        cases=[
            {
                "id": "TC-001",
                "description": "Submit order creates an order record",
                "test_module": "checkout",
                "steps": ["Open checkout", "Submit order"],
                "expected_result": "order is created",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "steps": ["Open order detail"],
                "expected_result": "status is paid",
                "priority": "P0",
            },
        ],
        expected_count=10,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert not [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    assert plan.get("workflow_blueprint_count") == 0


def test_execution_plan_can_bridge_generic_main_flow_without_domain_template() -> None:
    result = _run_cases(
        requirement="Generic workflow regression should preserve a positive entry, commit, and downstream visibility chain.",
        cases=[
            {
                "id": "TC-001",
                "description": "Open workflow entry and prepare state",
                "test_module": "workflow entry",
                "steps": ["Open entry page", "Prepare valid state"],
                "expected_result": "workflow entry is ready",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Commit the workflow change successfully",
                "test_module": "workflow commit",
                "steps": ["Save change"],
                "expected_result": "workflow change is saved successfully",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Downstream view reflects the committed change",
                "test_module": "workflow downstream",
                "steps": ["Refresh downstream page"],
                "expected_result": "new state becomes visible downstream",
                "priority": "P1",
            },
        ],
        expected_count=10,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    assert len(main_cases) >= 2
    transitions = [dict(item.get("workflow_transition") or {}) for item in main_cases]
    assert all(item.get("path_type") == "positive" for item in transitions)
    assert all(item.get("blocking") is False for item in transitions)
    assert all(item.get("destructive") is False for item in transitions)
    assert all(item.get("can_advance_main_flow") is True for item in transitions)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    assert plan.get("linear_executable") is True
    assert plan.get("main_chain_case_count") == len(main_cases)


def test_execution_plan_treats_interaction_scoring_as_current_doc_commit() -> None:
    result = _run_cases(
        requirement="Interactive AI tutoring flow: enter page, complete dialog, trigger scoring, then show score result.",
        cases=[
            {
                "id": "TC-001",
                "description": "Enter AI tutoring page",
                "test_module": "entry",
                "steps": ["Open tutoring page"],
                "expected_result": "workflow entry is ready for dialog",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Complete dialog and trigger score calculation",
                "test_module": "AI scoring",
                "steps": ["Complete the final dialog round", "Trigger score calculation"],
                "expected_result": "score calculation is generated successfully",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "steps": ["Open score result page"],
                "expected_result": "score result is shown with pass or fail status",
                "priority": "P1",
            },
        ],
        expected_count=10,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})

    assert len(main_cases) >= 2
    assert "commit" in list(plan.get("main_chain_stage_kinds") or [])
    assert plan.get("linear_executable") is True
    assert plan.get("workflow_blueprint_source") == "current_generation_cases"


def test_current_generation_main_chain_excludes_conditional_visibility_and_resume_checks() -> None:
    result = _run_cases(
        requirement=(
            "Interactive AI tutoring flow: enter page, complete dialog, trigger scoring, "
            "then show score result. Conditional button visibility and unfinished reentry "
            "checks are regression cases, not main smoke chain steps."
        ),
        cases=[
            {
                "id": "TC-001",
                "description": "Enter AI tutoring page",
                "test_module": "entry",
                "steps": ["Open tutoring page"],
                "expected_result": "workflow entry is ready for dialog",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Complete dialog and trigger score calculation",
                "test_module": "AI scoring",
                "steps": ["Complete the final dialog round", "Trigger score calculation"],
                "expected_result": "score calculation is generated successfully",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "steps": ["Open score result page"],
                "expected_result": "score result is shown with pass or fail status",
                "priority": "P1",
            },
            {
                "id": "TC-004",
                "description": "Only when quiz accuracy is greater than 50%, the review button is visible",
                "test_module": "conditional visibility",
                "steps": ["Open quiz feedback popup"],
                "expected_result": "the review button is visible only for the threshold condition",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "Re-enter unfinished tutoring flow and verify retained dialog history",
                "test_module": "resume state",
                "steps": ["Leave unfinished flow", "Re-enter tutoring page"],
                "expected_result": "retained dialog history is displayed after reentry",
                "priority": "P0",
            },
        ],
        expected_count=10,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    main_descriptions = " ".join(str(item.get("description") or "") for item in main_cases)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})

    assert "review button is visible" not in main_descriptions
    assert "retained dialog history" not in main_descriptions
    assert "commit" in list(plan.get("main_chain_stage_kinds") or [])
    assert plan.get("linear_executable") is True


def test_authoritative_blueprint_rejects_static_display_and_return_button_main_chain_candidates() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "forum_post",
                "repository_source": "current_requirement_blueprint",
                "source_type": "current_requirement_extracted",
                "steps": [
                    {
                        "id": "entry",
                        "label": "Enter forum home",
                        "action": "Open forum home",
                        "assertion": "forum home is ready",
                        "stage_kind": "entry",
                        "state_in": "initial",
                        "state_out": "forum_home",
                        "match_keywords": ["forum home"],
                        "allow_bridge": True,
                    },
                    {
                        "id": "configure",
                        "label": "Select forum zone",
                        "action": "Select forum zone",
                        "assertion": "zone is selected",
                        "stage_kind": "configure",
                        "state_in": "forum_home",
                        "state_out": "zone_selected",
                        "match_keywords": ["select forum zone"],
                        "allow_bridge": True,
                    },
                    {
                        "id": "preview",
                        "label": "Open post detail",
                        "action": "Open post detail",
                        "assertion": "post detail is visible",
                        "stage_kind": "preview",
                        "state_in": "zone_selected",
                        "state_out": "post_detail",
                        "match_keywords": ["post detail"],
                        "allow_bridge": True,
                    },
                    {
                        "id": "commit",
                        "label": "Submit reply",
                        "action": "Submit reply",
                        "assertion": "reply is submitted",
                        "stage_kind": "commit",
                        "state_in": "post_detail",
                        "state_out": "reply_committed",
                        "match_keywords": ["submit reply"],
                        "allow_bridge": True,
                    },
                    {
                        "id": "downstream",
                        "label": "View reply message",
                        "action": "View reply message",
                        "assertion": "reply message is visible",
                        "stage_kind": "downstream_visibility",
                        "state_in": "reply_committed",
                        "state_out": "reply_message_visible",
                        "match_keywords": ["reply message"],
                        "allow_bridge": True,
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Forum flow: enter forum home, select a zone, open post detail, submit reply, then view reply message.",
        cases=[
            {
                "id": "TC-001",
                "description": "Pinned post displays official icon, title and time",
                "test_module": "Forum Home content list",
                "steps": ["Open forum home", "View pinned post"],
                "expected_result": "Official icon, title and time are visible",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Select forum zone",
                "test_module": "Forum posting",
                "steps": ["Open forum home", "Select forum zone"],
                "expected_result": "zone is selected",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Post detail return button navigates back to forum home",
                "test_module": "Post detail navigation",
                "steps": ["Open post detail", "Click return button"],
                "expected_result": "The return button navigates back",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "Submit reply",
                "test_module": "Forum reply",
                "steps": ["Input reply", "Submit reply"],
                "expected_result": "reply is submitted",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "View reply message",
                "test_module": "Messages",
                "steps": ["Open message tab", "View reply message"],
                "expected_result": "reply message is visible",
                "priority": "P0",
            },
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    main_descriptions = " ".join(str(item.get("description") or "") for item in main_cases)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})

    assert "Pinned post displays" not in main_descriptions
    assert "return button" not in main_descriptions
    assert any(bool(item.get("workflow_contract_materialized_case")) for item in main_cases)
    assert plan.get("workflow_blueprint_source") == "current_requirement_blueprint"
    assert plan.get("linear_executable") is True


def test_current_generation_main_chain_uses_real_precommit_consume_without_bridge_case() -> None:
    result = _run_cases(
        requirement=(
            "AI tutoring flow: student enters the tutoring dialog, reads the AI question, "
            "submits an answer, triggers scoring, and then sees the score result."
        ),
        cases=[
            {
                "id": "TC-001",
                "description": "Student clicks AI tutoring task before answering",
                "test_module": "student dialog",
                "steps": ["Click AI tutoring task"],
                "expected_result": "answer area is ready for student response",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Student views AI question prompt before answering",
                "test_module": "AI question prompt",
                "steps": ["View current AI question prompt"],
                "expected_result": "question prompt is ready for student answer",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Submit answer and trigger score calculation",
                "test_module": "AI scoring",
                "steps": ["Submit answer", "Trigger score calculation"],
                "expected_result": "score calculation is generated successfully",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "steps": ["Open score result page"],
                "expected_result": "score result is shown with pass or fail status",
                "priority": "P1",
            },
        ],
        expected_count=10,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})

    assert main_cases
    assert not any(bool(item.get("generated_bridge_case")) for item in main_cases)
    assert plan.get("generated_bridge_case_count") == 0
    assert "commit" in list(plan.get("main_chain_stage_kinds") or [])
    assert plan.get("linear_executable") is True


def test_current_generation_main_chain_does_not_materialize_internal_entry_bridge() -> None:
    result = _run_cases(
        requirement=(
            "AI tutoring flow: after the student submits an answer, the system triggers scoring "
            "and displays the score result. No upstream entry or consume step is present."
        ),
        cases=[
            {
                "id": "TC-001",
                "description": "Submit answer and trigger score calculation",
                "test_module": "AI scoring",
                "steps": ["Submit answer", "Trigger score calculation"],
                "expected_result": "score calculation is generated successfully",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "steps": ["Open score result page"],
                "expected_result": "score result is shown with pass or fail status",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Show score result details on feedback page",
                "test_module": "score feedback",
                "steps": ["Open feedback page", "Display score result details"],
                "expected_result": "score result details are visible to the student",
                "priority": "P1",
            },
        ],
        expected_count=10,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})

    assert not main_cases
    assert not any(bool(item.get("generated_bridge_case")) for item in output_cases)
    assert plan.get("generated_bridge_case_count") == 0
    assert plan.get("workflow_blueprint_source") == "none"
    assert plan.get("main_chain_incomplete_reason") == "missing_configure_or_entry_step"
    assert plan.get("linear_executable") is False


def test_execution_plan_does_not_fake_current_main_smoke_when_commit_is_pruned() -> None:
    result = _run_cases(
        requirement="督导完成新增计划后，学生端首页展示本周任务并可点击学习。",
        cases=[
            {
                "id": "TC-001",
                "description": "首页本周任务卡片展示",
                "test_module": "首页",
                "steps": ["1. 打开学生端首页", "2. 查看本周任务"],
                "expected_result": "首页展示本周任务卡片",
                "priority": "P2",
            },
            {
                "id": "TC-002",
                "description": "排课新增计划第一步选择课程",
                "test_module": "排课-新增计划",
                "steps": ["1. 督导进入新增计划", "2. 选择课程", "3. 点击下一步"],
                "expected_result": "课程加入已选列表并进入时间设置步骤",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "排课新增计划第二步设置上课时间",
                "test_module": "排课-新增计划",
                "steps": ["1. 设置上课时间", "2. 点击下一步"],
                "expected_result": "上课时间保存到计划草稿并进入预览步骤",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "排课新增计划第三步预览并保存",
                "test_module": "排课-新增计划",
                "steps": ["1. 查看预览", "2. 点击保存"],
                "expected_result": "计划保存成功并回到课程管理页",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "学生点击学习进入正确课程",
                "test_module": "首页本周任务",
                "steps": ["1. 学生端首页点击学习按钮"],
                "expected_result": "系统进入对应课程学习页",
                "priority": "P0",
            },
            {
                "id": "TC-006",
                "description": "学习计划页 PV/UV 埋点上报",
                "test_module": "埋点",
                "steps": ["1. 打开学习计划页"],
                "expected_result": "PV 和 UV 埋点上报成功",
                "priority": "P2",
            },
        ],
        expected_count=10,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    assert not main_cases
    analytics_cases = [item for item in output_cases if "埋点" in str(item.get("description") or "")]
    assert all(str(item.get("execution_group") or "") != "main_smoke" for item in analytics_cases)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    assert plan.get("workflow_blueprint_source") == "none"
    assert plan.get("linear_executable") is False

def test_execution_plan_keeps_submission_rule_popup_on_student_session() -> None:
    result = _run_cases(
        requirement="作文投稿：学生从批改结果进入投稿页，首次进入会弹出规则说明弹窗。",
        cases=[
            {
                "id": "TC-001",
                "description": "投稿页首次进入自动弹出规则说明弹窗",
                "test_module": "作文投稿",
                "preconditions": ["学生用户已生成批改结果"],
                "steps": ["1. 点击投稿", "2. 进入投稿页", "3. 查看规则说明弹窗和标题正文"],
                "test_input": "已批改作文",
                "expected_result": "学生端进入投稿页后弹出规则说明弹窗，关闭后标题和正文输入区可编辑",
                "priority": "P1",
            }
        ],
    )

    case = next(
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and "规则说明弹窗" in str(item.get("description") or "")
    )
    assert str(case.get("role") or "") == "student"
    assert str(case.get("session_key") or "") == "student_session"
    assert str(case.get("role_switch_strategy") or "") == "reuse_group_session"


def test_execution_plan_does_not_use_community_fixture_for_generic_student_list_sorting() -> None:
    result = _run_cases(
        requirement="督导端学员列表支持科目筛选和列表展示，不涉及作文圈或社区作品。",
        cases=[
            {
                "id": "TC-001",
                "description": "督导端科目筛选功能：选择数学后，学员列表只显示数学科目学员",
                "test_module": "督导端学员列表",
                "preconditions": ["督导已登录，学员列表包含数学、物理等多个科目的学员"],
                "steps": ["1. 点击科目筛选下拉框", "2. 选择数学", "3. 查看学员列表"],
                "test_input": "筛选条件：数学",
                "expected_result": "列表仅显示科目为数学的学员，其他科目学员不再出现",
                "priority": "P1",
            }
        ],
    )

    case = next(item for item in (result.get("cases") or []) if isinstance(item, dict))
    assert str(case.get("role") or "") == "supervisor"
    assert str(case.get("session_key") or "") == "supervisor_session"
    assert str(case.get("fixture_key") or "") != "community_tab_sorting_dataset"
    assert str(case.get("fixture_builder") or "") != "seed_community_works(status='published', count=30, with_like_reply_time_distribution=true)"


def test_execution_plan_uses_browser_permission_fixture_for_microphone_permission() -> None:
    result = _run_cases(
        requirement="学员端讲错题支持语音录制，首次使用需要处理浏览器麦克风授权。",
        cases=[
            {
                "id": "TC-001",
                "description": "学员端语音录制功能异常场景：首次使用时用户拒绝麦克风权限",
                "test_module": "学员端讲错题页面录音功能",
                "preconditions": ["学员尚未授权麦克风权限"],
                "steps": ["1. 点击语音录制按钮", "2. 在浏览器权限弹窗中选择禁止", "3. 观察页面反馈"],
                "test_input": "用户拒绝麦克风权限",
                "expected_result": "页面提示麦克风权限被拒绝，且输入框仍可正常输入文字提交",
                "priority": "P1",
            }
        ],
    )

    case = next(item for item in (result.get("cases") or []) if isinstance(item, dict))
    assert str(case.get("execution_group") or "") == "permission"
    assert str(case.get("fixture_key") or "") == "browser_permission_state"
    assert str(case.get("fixture_builder") or "") == "set_browser_permission(permission='microphone', state='prompt')"
    assert str(case.get("group_setup") or "") == "set_browser_permission(permission='microphone', state='prompt')"
    assert str(case.get("cleanup_policy") or "") == "reset_browser_permissions"


def test_execution_plan_does_not_use_works_over_20_fixture_for_score_boundary_20() -> None:
    result = _run_cases(
        requirement="学员端讲错题评分规则必须断言：回答字数不足50字时完整性20分扣50%后显示为10/20，不涉及作文作品列表数量。",
        cases=[
            {
                "id": "TC-001",
                "description": "评分规则：回答字数不足50字时，完整性20分扣50%后显示为10/20",
                "test_module": "学员端-讲错题页面-评分规则-边界",
                "preconditions": ["学员已完成一轮讲错题回答，回答字数少于50字"],
                "steps": ["1. 提交少于50字的回答", "2. 完成交互并触发评分", "3. 查看评分明细"],
                "test_input": "少于50字的回答文本",
                "expected_result": "评分明细中完整性显示为10/20，清晰度同步按规则扣减，最终总分按扣分后计算",
                "priority": "P1",
            }
        ],
    )

    case = next(item for item in (result.get("cases") or []) if isinstance(item, dict))
    assert str(case.get("execution_group") or "") == "boundary"
    assert str(case.get("fixture_key") or "") != "works_over_20"
    assert str(case.get("fixture_builder") or "") != "seed_works(count=21)"


def test_execution_plan_uses_generic_boundary_fixture_for_composition_list_limit() -> None:
    result = _run_cases(
        requirement="我的作文列表最多展示20条作品，超过20条时需要验证数量上限。",
        cases=[
            {
                "id": "TC-001",
                "description": "我的作文最多20条：超过20篇作文记录时列表仅展示20条",
                "test_module": "我的作文",
                "preconditions": ["用户已有超过20篇作文记录"],
                "steps": ["1. 进入我的作文列表", "2. 查看列表展示数量"],
                "test_input": "21篇作文记录",
                "expected_result": "我的作文列表最多展示20条作品记录，其余记录通过分页或加载更多方式展示",
                "priority": "P1",
            }
        ],
    )

    case = next(item for item in (result.get("cases") or []) if isinstance(item, dict))
    assert str(case.get("fixture_key") or "") == "boundary_dataset"
    assert str(case.get("fixture_builder") or "") == "seed_boundary_dataset()"
