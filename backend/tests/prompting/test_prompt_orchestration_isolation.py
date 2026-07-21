import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.prompting.prompt_orchestration import (
    build_append_closed_loop_coverage_instruction,
    build_closed_loop_base_prompt,
    build_gap_fill_prompt,
    build_review_select_prompt,
)


def test_business_isolation_rule_contains_current_biz_key() -> None:
    prompt = build_closed_loop_base_prompt(
        strategy_plan={"system_type": "Web", "impact_scope": "module", "suggested_ratios": {}},
        requirement_context="REQ context",
        requirement_semantics_context="[Requirement Semantics]\n* Confirmed fact",
        testcase_context="CASE context",
        supplement_context="SUPPLEMENT context",
        current_biz_key="org_close_rule",
    )
    assert "BUSINESS ISOLATION RULE" in prompt
    assert "org_close_rule" in prompt
    assert "S0 - Workflow / Closed-loop" in prompt
    assert "S1 - Quality Rules" in prompt
    assert "S2 - Global Guidance" in prompt
    assert "TEST CASE PRIORITY CLASSIFICATION (MANDATORY)" in prompt
    assert "Coverage != P0" in prompt
    assert "Requirement Semantics - CONFIRMED vs PENDING" in prompt
    assert "Pending / Open Questions are NOT confirmed behavior" in prompt
    assert "EXPECTED_RESULT ASSERTABILITY (MANDATORY)" in prompt
    assert "正常展示" in prompt
    assert "do NOT generate that case" in prompt


def test_base_prompt_declares_execution_order_contract() -> None:
    prompt = build_closed_loop_base_prompt(
        strategy_plan={"system_type": "Web", "impact_scope": "module", "suggested_ratios": {}},
        requirement_context="REQ context",
        control_context="### WORKFLOW BLUEPRINTS\n* entry / open / initial->opened\n* commit / save / opened->saved",
        current_biz_key="workflow_order",
    )

    assert "EXECUTION ORDER CONTRACT (MANDATORY)" in prompt
    assert "JSON 数组顺序就是执行计划顺序" in prompt
    assert "exact blueprint step order" in prompt
    assert "permission/security -> exception/recovery -> boundary/state rollback" in prompt
    assert "Do not interleave UI/display" in prompt


def test_base_prompt_uses_structured_targets_without_fixed_coverage_templates() -> None:
    prompt = build_closed_loop_base_prompt(
        strategy_plan={
            "system_type": "Web",
            "impact_scope": "module",
            "suggested_ratios": {
                "functional": 0.6,
                "regression": 0.2,
                "non_functional": 0.2,
            },
            "coverage_targets": {
                "target_case_range": {"min": 12, "max": 18},
                "focus": ["REQ-1", "REQ-2"],
            },
        },
        requirement_context="REQ context",
        control_context="### FUNCTIONAL MODULE CONTRACT\n* A\n* A -> B",
        current_biz_key="generic_flow",
    )

    assert '"coverage_targets"' in prompt
    assert '"min": 12' in prompt
    assert "FUNCTIONAL MODULE CONTRACT" in prompt
    assert "At least 30%" not in prompt
    assert "At least 20%" not in prompt
    assert "must be <= 40%" not in prompt
    assert "same-type cases <= 2" not in prompt
    assert "每个模块必须包含" not in prompt
    assert "Recommended range: 30-50" not in prompt
    assert "Functional: 60%" not in prompt


def test_gap_fill_prompt_consumes_coverage_result() -> None:
    prompt = build_gap_fill_prompt(
        requirement_context="REQ-023 close org only when balance is zero",
        existing_cases=[
            {
                "id": "TC-001",
                "description": "verify close org happy path",
                "test_module": "org-close",
                "preconditions": [],
                "steps": ["submit close"],
                "test_input": "org=A",
                "expected_result": "success",
                "priority": "P0",
            }
        ],
        coverage_result={
            "rule_diagnostics": [
                {
                    "rule_id": "REQ-023",
                    "rule_text": "close org requires balance=0",
                    "biz_key": "org_close_rule",
                    "covered": True,
                    "missing_types": ["boundary", "exception"],
                }
            ]
        },
        current_biz_key="org_close_rule",
    )
    assert "REQ-023" in prompt
    assert "missing_types=boundary,exception" in prompt
    assert "coverage" in prompt.lower()


def test_review_select_prompt_uses_compact_candidate_payload() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "verify close org happy path",
            "test_module": "org-close",
            "preconditions": ["logged in"],
            "steps": ["1. submit close request"],
            "test_input": "org=A",
            "expected_result": "org A status changes to closed and disappears from active list",
            "priority": "P1",
            "model_priority_current": "P0",
            "legacy_priority": "P0",
            "priority_final": "P1",
            "priority_decision_source": "debug-only",
            "priority_reasons": ["debug-only"],
            "review_llm_drop_reason_evidence": {"large": "debug"},
        }
        for _ in range(20)
    ]

    prompt = build_review_select_prompt(
        requirement_context="REQ close org",
        candidate_cases=cases,
        target_count=20,
        target_min_count=10,
        target_max_count=20,
    )

    assert '"priority": "P1"' in prompt
    assert "model_priority_current" not in prompt
    assert "priority_decision_source" not in prompt
    assert "review_llm_drop_reason_evidence" not in prompt
    assert len(prompt) < 20000


def test_review_select_prompt_accepts_alias_case_fields() -> None:
    prompt = build_review_select_prompt(
        requirement_context="REQ close org",
        candidate_cases=[
            {
                "caseId": "TC-ALIAS",
                "title": "verify alias close path",
                "module": "org-close",
                "testSteps": ["submit close request"],
                "testData": "org=A",
                "expectedResult": "org is closed",
                "finalPriority": "P0",
            }
        ],
        target_count=1,
    )

    assert '"id": "TC-ALIAS"' in prompt
    assert '"description": "verify alias close path"' in prompt
    assert '"test_module": "org-close"' in prompt
    assert '"priority": "P0"' in prompt
    assert '"test_input": "org=A"' in prompt
    assert '"expected_result": "org is closed"' in prompt
    assert '"TC-ALIAS"' in prompt


def test_closed_loop_snapshot_accepts_alias_module_field() -> None:
    prompt = build_append_closed_loop_coverage_instruction(
        existing_cases=[{"caseId": "TC-001", "module": "org-close"}],
        requirement="plain requirement",
        expected_count=1,
        infer_case_kind_fn=lambda _case: "happy_path",
    )

    assert "org-close: total=1" in prompt
