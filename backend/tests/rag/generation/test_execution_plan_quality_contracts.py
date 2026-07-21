from __future__ import annotations

import pytest

from modules.testing.test_generation_components.postprocess.result_postprocess import (
    normalize_final_case_priorities,
)
from modules.testing.test_generation_components.postprocess.streaming_execution_plan_ordering import (
    execution_group_order_rank,
)
from tests.rag.generation.quality_governance_harness import (
    run_quality_governance_cases as _run_cases,
)


@pytest.mark.xfail(
    strict=True,
    reason="多阶段共享 checkout/order 业务词会触发跨阶段候选竞争，显式主链当前无法物化",
)
def test_execution_plan_uses_workflow_blueprint_without_domain_template() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "checkout_flow",
                "workflow_id": "checkout_flow",
                "name": "checkout flow",
                "repository_source": "current_requirement_blueprint",
                "source_type": "current_requirement_extracted",
                "steps": [
                    {
                        "id": "open_checkout",
                        "label": "Open checkout",
                        "action": "Open checkout",
                        "actor": "business_user",
                        "state_in": "initial",
                        "state_out": "checkout_opened",
                        "stage_kind": "entry",
                        "match_keywords": ["open checkout"],
                        "assertion": "checkout is ready",
                    },
                    {
                        "id": "configure_order",
                        "label": "Configure order",
                        "action": "Select delivery and payment method",
                        "actor": "business_user",
                        "state_in": "checkout_opened",
                        "state_out": "order_ready",
                        "stage_kind": "configure",
                        "match_keywords": ["select delivery and payment method"],
                        "assertion": "order is ready to submit",
                    },
                    {
                        "id": "submit_order",
                        "label": "Submit order",
                        "action": "Submit order",
                        "actor": "business_user",
                        "state_in": "order_ready",
                        "state_out": "order_created",
                        "stage_kind": "commit",
                        "match_keywords": ["submit order"],
                        "assertion": "order is created",
                    },
                    {
                        "id": "verify_paid",
                        "label": "Verify paid status",
                        "action": "Display paid order status",
                        "actor": "business_user",
                        "state_in": "order_created",
                        "state_out": "paid_status_visible",
                        "stage_kind": "downstream_visibility",
                        "match_keywords": ["display paid order status"],
                        "assertion": "paid order status is displayed and visible downstream",
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement=(
            "Checkout flow: open checkout, select delivery and payment method, submit the order, "
            "then display the paid order status downstream."
        ),
        cases=[
            {
                "id": "TC-001",
                "description": "Open checkout for the current cart",
                "test_module": "checkout",
                "preconditions": ["Authenticated buyer owns cart CART-1001 and SKU-001 is in stock"],
                "steps": ["Open checkout"],
                "test_input": "cart CART-1001 with SKU-001 quantity 1 and valid payment token",
                "expected_result": "checkout is ready",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Select delivery and payment method",
                "test_module": "checkout configuration",
                "preconditions": ["Checkout for CART-1001 is open and delivery address ADDRESS-01 is valid"],
                "steps": ["Select delivery and payment method"],
                "test_input": "delivery standard and payment token PAY-1001",
                "expected_result": "order is ready to submit",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Submit order creates an order record",
                "test_module": "checkout commit",
                "preconditions": ["CART-1001 has a selected delivery method and valid payment token"],
                "steps": ["Submit order"],
                "test_input": "ready cart CART-1001",
                "expected_result": "order is created",
                "priority": "P1",
            },
            {
                "id": "TC-004",
                "description": "Display paid order status",
                "test_module": "order detail",
                "preconditions": ["Order ORDER-1001 exists and its paid callback has completed"],
                "steps": ["Open order detail", "Display paid order status"],
                "test_input": "paid order ORDER-1001 owned by the current account",
                "expected_result": "paid order status is displayed and visible downstream",
                "priority": "P1",
            },
            {
                "id": "TC-005",
                "description": "Network timeout shows retry action",
                "test_module": "checkout",
                "preconditions": ["Authenticated buyer owns cart CART-1002 and payment timeout injection is enabled"],
                "steps": ["Submit order during timeout"],
                "test_input": "cart CART-1002 while the payment API times out after 30 seconds",
                "expected_result": "retry action is shown",
                "priority": "P0",
            },
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    assert [item.get("main_chain_stage") for item in main_cases] == [
        "open_checkout",
        "configure_order",
        "submit_order",
        "verify_paid",
    ]
    assert main_cases[0].get("depends_on") == []
    assert all(main_cases[index].get("depends_on") == [main_cases[index - 1]["id"]] for index in range(1, 4))
    assert all(item.get("role") == "business_user" for item in main_cases)
    assert [item.get("data_state") for item in main_cases] == [
        "checkout_opened",
        "order_ready",
        "order_created",
        "paid_status_visible",
    ]
    assert [dict(item.get("workflow_transition") or {}).get("stage_kind") for item in main_cases] == [
        "entry",
        "configure",
        "commit",
        "downstream_visibility",
    ]
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
                "preconditions": ["Paid order ORDER-2001 exists and is visible to the current account"],
                "steps": ["Open order detail", "Inspect status tag style"],
                "test_input": "paid order ORDER-2001 with status tag configuration enabled",
                "expected_result": "Paid status tag color and copy are displayed consistently",
                "priority": "P2",
                "execution_group": "display",
            },
            {
                "id": "TC-002",
                "description": "Submit order creates an order record",
                "test_module": "checkout",
                "preconditions": ["Authenticated buyer owns cart CART-2001 and SKU-101 is in stock"],
                "steps": ["Open checkout", "Submit order"],
                "test_input": "cart CART-2001 with SKU-101 quantity 1 and valid payment token",
                "expected_result": "Order record is created and order id is returned",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Unauthorized user cannot submit order",
                "test_module": "checkout permission",
                "preconditions": ["Readonly account VIEWER-01 can view but cannot submit cart CART-2002"],
                "steps": ["Use readonly user", "Submit order"],
                "test_input": "readonly account VIEWER-01 and cart CART-2002",
                "expected_result": "System blocks submission and shows permission denied message",
                "priority": "P1",
                "execution_group": "permission",
            },
            {
                "id": "TC-004",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "preconditions": ["Order ORDER-2001 exists and a paid callback is available"],
                "steps": ["Open created order detail", "Refresh payment status"],
                "test_input": "created order ORDER-2001 with payment callback status paid",
                "expected_result": "Paid status is visible on order detail",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "Network timeout during submit shows retry action",
                "test_module": "checkout exception",
                "preconditions": ["Authenticated buyer owns cart CART-2003 and payment timeout injection is enabled"],
                "steps": ["Submit order while payment service times out"],
                "test_input": "cart CART-2003 with payment service forced to a 30-second timeout",
                "expected_result": "Retry action is shown and original cart remains unchanged",
                "priority": "P1",
                "execution_group": "exception",
            },
            {
                "id": "TC-006",
                "description": "Order quantity upper boundary is enforced",
                "test_module": "checkout boundary",
                "preconditions": ["SKU-102 has a configured per-order maximum quantity of 999"],
                "steps": ["Set quantity above maximum", "Submit order"],
                "test_input": "SKU-102 quantity 1001 when the configured maximum is 999",
                "expected_result": "System rejects quantity above maximum and keeps order unsubmitted",
                "priority": "P1",
                "execution_group": "boundary",
            },
            {
                "id": "TC-007",
                "description": "Coupon recalculation updates order total",
                "test_module": "checkout functional",
                "preconditions": ["Coupon SAVE10 is active and applicable to cart CART-2004"],
                "steps": ["Apply valid coupon", "Recalculate order total"],
                "test_input": "cart CART-2004 total 100.00 and coupon SAVE10",
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
                "id": "forum_publish_flow",
                "workflow_id": "forum_publish_flow",
                "name": "forum publish flow",
                "repository_source": "current_requirement_blueprint",
                "source_type": "current_requirement_extracted",
                "steps": [
                    {
                        "id": "open_forum",
                        "label": "Open forum home",
                        "action": "Open forum home",
                        "actor": "business_user",
                        "state_in": "initial",
                        "state_out": "forum_opened",
                        "stage_kind": "entry",
                        "match_keywords": ["open forum home"],
                        "assertion": "forum home is ready",
                    },
                    {
                        "id": "select_zone",
                        "label": "Select forum zone",
                        "action": "Select forum zone",
                        "actor": "business_user",
                        "state_in": "forum_opened",
                        "state_out": "zone_selected",
                        "stage_kind": "configure",
                        "match_keywords": ["select forum zone"],
                        "assertion": "zone is selected",
                    },
                    {
                        "id": "submit_post",
                        "label": "Submit post",
                        "action": "Submit new post",
                        "actor": "business_user",
                        "state_in": "zone_selected",
                        "state_out": "post_created",
                        "stage_kind": "commit",
                        "match_keywords": ["submit new post"],
                        "assertion": "post is created",
                    },
                    {
                        "id": "view_post",
                        "label": "View published post",
                        "action": "Display published post in selected zone",
                        "actor": "business_user",
                        "state_in": "post_created",
                        "state_out": "post_visible",
                        "stage_kind": "downstream_visibility",
                        "match_keywords": ["display published post in selected zone"],
                        "assertion": "post is displayed and visible downstream in the zone",
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Forum flow: open forum home, select a zone, submit a new post, then view it in that zone.",
        cases=[
            {
                "id": "TC-001",
                "description": "Open forum home",
                "test_module": "forum entry",
                "preconditions": ["Authenticated account can access the forum"],
                "steps": ["Open forum home"],
                "test_input": "forum route /forum",
                "expected_result": "forum home is ready",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Select forum zone",
                "test_module": "forum zone",
                "preconditions": ["Forum home is open and zone ZONE-3001 is active"],
                "steps": ["Select forum zone"],
                "test_input": "zone ZONE-3001",
                "expected_result": "zone is selected",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Submit new post",
                "test_module": "forum post",
                "preconditions": ["Zone ZONE-3001 is selected and the account can publish"],
                "steps": ["Enter title and body", "Submit new post"],
                "test_input": "title Release update and body Version 3 is available",
                "expected_result": "post is created",
                "priority": "P1",
            },
            {
                "id": "TC-004",
                "description": "Display published post in selected zone",
                "test_module": "forum list",
                "preconditions": ["Post POST-3001 has been created in zone ZONE-3001"],
                "steps": ["Refresh zone list", "Display published post in selected zone"],
                "test_input": "post POST-3001 in zone ZONE-3001",
                "expected_result": "post is displayed and visible downstream in the zone",
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
    assert [item.get("source_state") for item in transitions] == [
        "initial",
        "forum_opened",
        "zone_selected",
        "post_created",
    ]
    assert [item.get("target_state") for item in transitions] == [
        "forum_opened",
        "zone_selected",
        "post_created",
        "post_visible",
    ]
    assert all(item.get("path_type") == "positive" for item in transitions)
    assert all(item.get("blocking") is False for item in transitions)
    assert all(item.get("destructive") is False for item in transitions)
    assert all(item.get("can_advance_main_flow") is True for item in transitions)
    assert all(item.get("workflow_id") == "forum_publish_flow" for item in main_cases)
    assert [item.get("source_state") for item in main_cases] == [item.get("source_state") for item in transitions]
    assert [item.get("target_state") for item in main_cases] == [item.get("target_state") for item in transitions]
    assert all(float(item.get("state_transition_confidence") or 0.0) >= 0.9 for item in main_cases)


def test_execution_plan_excludes_candidate_when_action_text_does_not_support_stage() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "order_submission_flow",
                "workflow_id": "order_submission_flow",
                "name": "order submission flow",
                "source_type": "current_requirement_extracted",
                "repository_source": "current_requirement_blueprint",
                "steps": [
                    {
                        "id": "open_checkout",
                        "label": "Open checkout",
                        "action": "Open checkout",
                        "actor": "business_user",
                        "state_in": "initial",
                        "state_out": "checkout_opened",
                        "stage_kind": "entry",
                        "allow_bridge": True,
                        "match_keywords": ["open checkout"],
                    },
                    {
                        "id": "configure_order",
                        "label": "Configure order",
                        "action": "Select delivery address and payment method",
                        "actor": "business_user",
                        "state_in": "checkout_opened",
                        "state_out": "order_configured",
                        "stage_kind": "configure",
                        "allow_bridge": True,
                        "match_keywords": ["select delivery address"],
                    },
                    {
                        "id": "submit_order",
                        "label": "Submit order",
                        "action": "Confirm checkout and submit order",
                        "actor": "business_user",
                        "state_in": "order_configured",
                        "state_out": "order_submitted",
                        "stage_kind": "commit",
                        "allow_bridge": True,
                        "match_keywords": ["order"],
                    },
                    {
                        "id": "view_receipt",
                        "label": "View order receipt",
                        "action": "Open submitted order receipt",
                        "actor": "business_user",
                        "state_in": "order_submitted",
                        "state_out": "receipt_visible",
                        "stage_kind": "downstream_visibility",
                        "allow_bridge": True,
                        "match_keywords": ["open submitted order receipt"],
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement=(
            "Checkout flow should open checkout, select delivery and payment, submit the order, "
            "and then open the submitted order receipt."
        ),
        cases=[
            {
                "id": "TC-001",
                "description": "Open checkout",
                "test_module": "checkout entry",
                "preconditions": ["Authenticated buyer owns cart CART-5001"],
                "steps": ["Open checkout"],
                "test_input": "cart CART-5001",
                "expected_result": "checkout is opened",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Select delivery address and payment method",
                "test_module": "checkout configuration",
                "preconditions": ["Checkout for CART-5001 is open"],
                "steps": ["Select delivery address", "Select payment method"],
                "test_input": "address ADDRESS-5001 and payment token PAY-5001",
                "expected_result": "order is configured and ready to submit",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Order history address filter retains previous orders",
                "test_module": "order history",
                "preconditions": ["Account has orders for ADDRESS-OLD and ADDRESS-5001"],
                "steps": ["Open order history", "Switch delivery address filter"],
                "test_input": "filter address ADDRESS-5001",
                "expected_result": "previous orders remain available under their original address filter",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "Open submitted order receipt",
                "test_module": "order receipt",
                "preconditions": ["Order ORDER-5001 has been submitted"],
                "steps": ["Open submitted order receipt"],
                "test_input": "order ORDER-5001",
                "expected_result": "receipt is visible",
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
    assert [item.get("main_chain_stage") for item in main_cases] == [
        "open_checkout",
        "configure_order",
        "submit_order",
        "view_receipt",
    ]
    commit_case = next(item for item in main_cases if item.get("main_chain_stage") == "submit_order")
    assert "address filter" not in str(commit_case.get("description") or "").lower()
    assert commit_case.get("workflow_contract_materialized_case") is True
    assert commit_case.get("steps") == ["Confirm checkout and submit order"]
    assert all("address filter" not in str(item.get("description") or "").lower() for item in main_cases)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    assert plan.get("workflow_blueprint_source") == "current_requirement_blueprint"
    assert plan.get("linear_executable") is True


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
                "preconditions": ["Profile PROFILE-1001 belongs to the authenticated account and is editable"],
                "steps": ["Update profile preference"],
                "test_input": "profile PROFILE-1001 with locale changed from zh-CN to en-US",
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
                "preconditions": [f"workflow is in {state_in} state before {step_id}"],
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
                "preconditions": ["Account can create plans and COURSE-01 and COURSE-02 are available"],
                "steps": ["Open create plan", "Select courses"],
                "test_input": "plan PLAN-100 with courses COURSE-01 and COURSE-02",
                "expected_result": "plan is configured with selected courses",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Capacity shortage blocks configure plan",
                "test_module": "plan create",
                "preconditions": ["The plan course capacity is configured as 10"],
                "steps": ["Select too many courses"],
                "test_input": "plan PLAN-101 with 11 courses when capacity is 10",
                "expected_result": "system shows capacity limit and cannot continue",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Save plan successfully",
                "test_module": "plan create",
                "preconditions": ["Plan PLAN-100 has valid courses and a non-conflicting schedule"],
                "steps": ["Preview plan", "Save plan"],
                "test_input": "configured plan PLAN-100 scheduled for 2026-07-21 10:00",
                "expected_result": "plan is saved with id PLAN-100",
                "priority": "P1",
            },
            {
                "id": "TC-004",
                "description": "Save plan blocked by time conflict",
                "test_module": "plan create",
                "preconditions": ["PLAN-099 already occupies the target time slot"],
                "steps": ["Save plan with conflicting time"],
                "test_input": "plan PLAN-102 overlapping PLAN-099 at 2026-07-21 10:00",
                "expected_result": "save is blocked and conflict message is shown",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "Student home visible after new plan sync",
                "test_module": "student home",
                "preconditions": ["Plan PLAN-100 is saved and assigned to USER-1001"],
                "steps": ["Open student home"],
                "test_input": "account USER-1001 assigned to saved plan PLAN-100",
                "expected_result": "new plan is visible on student home",
                "priority": "P1",
            },
            {
                "id": "TC-006",
                "description": "Open course from student home",
                "test_module": "student home",
                "preconditions": ["USER-1001 can see the COURSE-01 card from PLAN-100"],
                "steps": ["Click course card"],
                "test_input": "course card for COURSE-01 in plan PLAN-100",
                "expected_result": "course page opens for PLAN-100",
                "priority": "P1",
            },
            {
                "id": "TC-007",
                "description": "Save plan fails during network timeout",
                "test_module": "plan create",
                "preconditions": ["Plan PLAN-103 is valid and save API timeout injection is enabled"],
                "steps": ["Save plan during network timeout"],
                "test_input": "configured plan PLAN-103 while save API times out after 30 seconds",
                "expected_result": "save plan failed and retry action is shown",
                "priority": "P0",
            },
            {
                "id": "TC-008",
                "description": "Delete existing plan",
                "test_module": "plan management",
                "preconditions": ["Plan PLAN-100 exists and the account has delete permission"],
                "steps": ["Delete plan PLAN-100"],
                "test_input": "existing saved plan PLAN-100 with no active consumers",
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
                "preconditions": ["Authenticated buyer owns cart CART-4001 and SKU-301 is in stock"],
                "steps": ["Open checkout", "Submit order"],
                "test_input": "cart CART-4001 with SKU-301 quantity 1 and valid payment token",
                "expected_result": "order is created",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "preconditions": ["Order ORDER-4001 exists and its payment callback has completed"],
                "steps": ["Open order detail"],
                "test_input": "paid order ORDER-4001 owned by the current account",
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
                "preconditions": ["Workflow WORKFLOW-1001 exists in draft state and is editable"],
                "steps": ["Open entry page", "Prepare valid state"],
                "test_input": "workflow WORKFLOW-1001 in draft state with editable fields",
                "expected_result": "workflow entry is ready",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Commit the workflow change successfully",
                "test_module": "workflow commit",
                "preconditions": ["Workflow WORKFLOW-1001 has passed validation and is ready to save"],
                "steps": ["Save change"],
                "test_input": "workflow WORKFLOW-1001 with field name changed to Release A",
                "expected_result": "workflow change is saved successfully",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Downstream view reflects the committed change",
                "test_module": "workflow downstream",
                "preconditions": ["Workflow WORKFLOW-1001 has been committed and downstream sync is enabled"],
                "steps": ["Refresh downstream page"],
                "test_input": "downstream view linked to committed workflow WORKFLOW-1001",
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


def test_execution_plan_does_not_treat_scoring_terms_as_generic_commit() -> None:
    result = _run_cases(
        requirement="Interactive AI tutoring flow: enter page, complete dialog, trigger scoring, then show score result.",
        cases=[
            {
                "id": "TC-001",
                "description": "Enter AI tutoring page",
                "test_module": "entry",
                "preconditions": ["Session SESSION-1001 is active and assigned to the authenticated account"],
                "steps": ["Open tutoring page"],
                "test_input": "active tutoring session SESSION-1001",
                "expected_result": "workflow entry is ready for dialog",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Complete dialog and trigger score calculation",
                "test_module": "AI scoring",
                "preconditions": ["Session SESSION-1001 is at its final required dialog round"],
                "steps": ["Complete the final dialog round", "Trigger score calculation"],
                "test_input": "session SESSION-1001 with three completed dialog rounds",
                "expected_result": "score calculation is generated successfully",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "preconditions": ["Score task SCORE-1001 has completed for session SESSION-1001"],
                "steps": ["Open score result page"],
                "test_input": "completed score task SCORE-1001 for session SESSION-1001",
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

    assert not main_cases
    assert plan.get("workflow_blueprint_count") == 0
    assert plan.get("workflow_blueprint_source") == "none"
    assert list(plan.get("main_chain_stage_kinds") or []) == []
    assert plan.get("linear_executable") is False


def test_order_blueprint_excludes_conditional_visibility_and_resume_checks() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "order_checkout",
                "repository_source": "current_requirement_blueprint",
                "source_type": "current_requirement_extracted",
                "steps": [
                    {
                        "id": "entry",
                        "action": "Open checkout page",
                        "stage_kind": "entry",
                        "state_in": "initial",
                        "state_out": "checkout_opened",
                        "match_keywords": ["open checkout page"],
                    },
                    {
                        "id": "commit",
                        "action": "Submit configured order",
                        "stage_kind": "commit",
                        "state_in": "checkout_opened",
                        "state_out": "order_submitted",
                        "match_keywords": ["submit configured order"],
                    },
                    {
                        "id": "downstream",
                        "action": "Display submitted order in order history",
                        "stage_kind": "downstream_visibility",
                        "state_in": "order_submitted",
                        "state_out": "order_visible",
                        "match_keywords": ["display submitted order in order history"],
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement=(
            "Order flow: open checkout, submit the configured order, then view it in order history. "
            "Conditional coupon visibility and unfinished checkout recovery are side regressions."
        ),
        cases=[
            {
                "id": "TC-001",
                "description": "Open checkout page",
                "test_module": "checkout entry",
                "preconditions": ["Authenticated buyer owns cart CART-6001"],
                "steps": ["Open checkout page"],
                "test_input": "cart CART-6001",
                "expected_result": "checkout page is ready",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Submit configured order",
                "test_module": "checkout commit",
                "preconditions": ["CART-6001 has valid delivery and payment selections"],
                "steps": ["Submit configured order"],
                "test_input": "configured cart CART-6001",
                "expected_result": "order ORDER-6001 is submitted",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Display submitted order in order history",
                "test_module": "order history",
                "preconditions": ["Order ORDER-6001 has been submitted"],
                "steps": ["Open order history", "Display submitted order in order history"],
                "test_input": "order ORDER-6001",
                "expected_result": "submitted order is displayed and visible downstream",
                "priority": "P1",
            },
            {
                "id": "TC-004",
                "description": "Coupon banner is visible only when cart total exceeds 100",
                "test_module": "conditional visibility",
                "preconditions": ["Coupon banner threshold is configured as 100"],
                "steps": ["Open checkout with cart total 101"],
                "test_input": "cart CART-6002 total 101",
                "expected_result": "coupon banner is visible only for the threshold condition",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "Re-enter unfinished checkout and verify retained draft fields",
                "test_module": "resume state",
                "preconditions": ["Cart CART-6003 has an unfinished checkout draft"],
                "steps": ["Leave unfinished checkout", "Re-enter checkout"],
                "test_input": "checkout draft for CART-6003",
                "expected_result": "retained delivery fields are displayed after reentry",
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

    assert "Coupon banner" not in main_descriptions
    assert "retained draft fields" not in main_descriptions
    assert [item.get("main_chain_stage") for item in main_cases] == ["entry", "commit", "downstream"]
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
                        "action": "Open forum home for posting",
                        "assertion": "forum home is ready",
                        "stage_kind": "entry",
                        "state_in": "initial",
                        "state_out": "forum_home",
                        "match_keywords": ["open forum home for posting"],
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
                        "action": "Open post detail for reply",
                        "assertion": "post detail is visible",
                        "stage_kind": "preview",
                        "state_in": "zone_selected",
                        "state_out": "post_detail",
                        "match_keywords": ["open post detail for reply"],
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
                "preconditions": ["Pinned post POST-1001 is published on the forum home"],
                "steps": ["Inspect pinned post icon, title and time"],
                "test_input": "forum home containing pinned post POST-1001",
                "expected_result": "Official icon, title and time are visible",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Select forum zone",
                "test_module": "Forum posting",
                "preconditions": ["Forum zone ZONE-01 is active and visible to the account"],
                "steps": ["Open forum home", "Select forum zone"],
                "test_input": "active forum zone ZONE-01",
                "expected_result": "zone is selected",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Post detail return button navigates back to forum home",
                "test_module": "Post detail navigation",
                "preconditions": ["Post detail for POST-1001 is already open"],
                "steps": ["Click return button"],
                "test_input": "published post POST-1001 in zone ZONE-01",
                "expected_result": "The return button navigates back",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "Submit reply",
                "test_module": "Forum reply",
                "preconditions": ["Post POST-1001 accepts replies from the authenticated account"],
                "steps": ["Input reply", "Submit reply"],
                "test_input": "reply text 'confirmed' for post POST-1001",
                "expected_result": "reply is submitted",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "View reply message",
                "test_module": "Messages",
                "preconditions": ["Reply notification MESSAGE-1001 has been created for POST-1001"],
                "steps": ["Open message tab", "View reply message"],
                "test_input": "reply notification MESSAGE-1001 linked to post POST-1001",
                "expected_result": "reply message is visible",
                "priority": "P0",
            },
            {
                "id": "TC-006",
                "description": "Open forum home for posting",
                "test_module": "Forum entry",
                "preconditions": ["Authenticated account can access the forum"],
                "steps": ["Open forum home for posting"],
                "test_input": "forum route /forum",
                "expected_result": "forum home is ready",
                "priority": "P0",
            },
            {
                "id": "TC-007",
                "description": "Open post detail for reply",
                "test_module": "Post detail",
                "preconditions": ["Post POST-1001 is published in selected zone ZONE-01"],
                "steps": ["Open post detail for reply"],
                "test_input": "post POST-1001",
                "expected_result": "post detail is visible",
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
    assert [item.get("main_chain_stage") for item in main_cases] == [
        "entry",
        "configure",
        "preview",
        "commit",
        "downstream",
    ]
    assert plan.get("workflow_blueprint_source") == "current_requirement_blueprint"
    assert plan.get("linear_executable") is True


def test_current_generation_purchase_chain_uses_real_cases_without_bridge() -> None:
    result = _run_cases(
        requirement="Purchase flow: open the purchase entry, select an address, submit the purchase, then display it downstream in account history.",
        cases=[
            {
                "id": "TC-001",
                "description": "Open purchase workflow entry and select delivery address",
                "test_module": "cart entry",
                "preconditions": ["cart CART-1 exists"],
                "steps": ["Open purchase workflow entry", "Select delivery address"],
                "test_input": "cart CART-1 and address ADDR-1",
                "expected_result": "purchase entry is ready with selected address successfully",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Submit purchase successfully",
                "test_module": "payment submission",
                "preconditions": ["purchase entry is ready"],
                "steps": ["Submit purchase"],
                "test_input": "valid payment token PAY-1",
                "expected_result": "purchase ORDER-1 is created successfully",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Display created purchase in account history downstream",
                "test_module": "account history",
                "preconditions": ["purchase ORDER-1 exists"],
                "steps": ["Open account history", "Display created purchase"],
                "test_input": "purchase ORDER-1",
                "expected_result": "purchase ORDER-1 is displayed and visible downstream",
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

    assert [item.get("main_chain_stage_kind") for item in main_cases] == [
        "entry",
        "commit",
        "downstream_visibility",
    ]
    assert not any(bool(item.get("generated_bridge_case")) for item in main_cases)
    assert plan.get("generated_bridge_case_count") == 0
    assert plan.get("workflow_blueprint_count") == 0
    assert plan.get("plan_workflow_blueprint_count") == 1
    assert plan.get("workflow_blueprint_source") == "current_generation_cases"
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
                "preconditions": ["Question QUESTION-04 is active in session SESSION-4001"],
                "steps": ["Submit answer", "Trigger score calculation"],
                "test_input": "answer '42' for question QUESTION-04 in session SESSION-4001",
                "expected_result": "score calculation is generated successfully",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "preconditions": ["Score task SCORE-4001 has completed for session SESSION-4001"],
                "steps": ["Open score result page"],
                "test_input": "completed score task SCORE-4001 for session SESSION-4001",
                "expected_result": "score result is shown with pass or fail status",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Show score result details on feedback page",
                "test_module": "score feedback",
                "preconditions": ["Score task SCORE-4001 contains a criterion breakdown"],
                "steps": ["Open feedback page", "Display score result details"],
                "test_input": "score task SCORE-4001 with criterion breakdown data",
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
                "preconditions": ["账号 USER-5001 已登录且已关联计划 PLAN-5001"],
                "steps": ["1. 打开学生端首页", "2. 查看本周任务"],
                "test_input": "账号 USER-5001，已关联计划 PLAN-5001",
                "expected_result": "首页展示本周任务卡片",
                "priority": "P2",
            },
            {
                "id": "TC-002",
                "description": "排课新增计划第一步选择课程",
                "test_module": "排课-新增计划",
                "preconditions": ["账号具备新建计划权限且课程 COURSE-5001 可选"],
                "steps": ["1. 督导进入新增计划", "2. 选择课程", "3. 点击下一步"],
                "test_input": "课程 COURSE-5001，计划名称为 7 月第 4 周计划",
                "expected_result": "课程加入已选列表并进入时间设置步骤",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "排课新增计划第二步设置上课时间",
                "test_module": "排课-新增计划",
                "preconditions": ["计划草稿 PLAN-5001 已完成课程选择且目标时间无冲突"],
                "steps": ["1. 设置上课时间", "2. 点击下一步"],
                "test_input": "上课时间 2026-07-21 10:00 至 11:00",
                "expected_result": "上课时间保存到计划草稿并进入预览步骤",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "排课新增计划第三步预览并保存",
                "test_module": "排课-新增计划",
                "preconditions": ["计划草稿 PLAN-5001 已完成课程与时间配置"],
                "steps": ["1. 查看预览", "2. 点击保存"],
                "test_input": "已完成课程与时间配置的计划草稿 PLAN-5001",
                "expected_result": "计划保存成功并回到课程管理页",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "学生点击学习进入正确课程",
                "test_module": "首页本周任务",
                "preconditions": ["账号 USER-5001 已登录且首页展示 COURSE-5001 任务卡片"],
                "steps": ["1. 学生端首页点击学习按钮"],
                "test_input": "账号 USER-5001 首页中的 COURSE-5001 任务卡片",
                "expected_result": "系统进入对应课程学习页",
                "priority": "P0",
            },
            {
                "id": "TC-006",
                "description": "学习计划页 PV/UV 埋点上报",
                "test_module": "埋点",
                "preconditions": ["埋点采集开启且账号 USER-5001 可访问计划 PLAN-5001"],
                "steps": ["1. 打开学习计划页"],
                "test_input": "用户 USER-5001 打开计划 PLAN-5001 的详情页",
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

def test_execution_plan_does_not_infer_actor_from_submission_surface_text() -> None:
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
    assert str(case.get("role") or "") == "business_user"
    assert str(case.get("session_key") or "") == "business_user_session"
    assert str(case.get("role_switch_strategy") or "") == "reuse_role_session"


def test_execution_plan_does_not_infer_actor_or_community_fixture_from_list_text() -> None:
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
    assert str(case.get("role") or "") == "business_user"
    assert str(case.get("session_key") or "") == "business_user_session"
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
