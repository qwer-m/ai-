import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.prompting.prompt_orchestration import (
    build_closed_loop_base_prompt,
    build_gap_fill_prompt,
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
