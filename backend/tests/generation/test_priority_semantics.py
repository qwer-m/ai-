import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.postprocess.result_postprocess import (
    apply_priority_semantics_to_case,
    apply_priority_semantics_to_cases,
    resolve_case_priority,
    score_case_priority,
)


def test_score_case_priority_allows_p0_only_with_guard() -> None:
    case = {
        "description": "Login payment submit flow is blocked and release blocking.",
        "test_module": "payment",
        "preconditions": ["user logged in"],
        "steps": ["submit order", "start payment"],
        "test_input": "valid order",
        "expected_result": "payment succeeds and flow continues",
        "priority": "P1",
    }
    score_result = score_case_priority(case)
    assert score_result["priority_score"] >= 70
    assert score_result["suggested_priority"] == "P0"
    assert any(score_result["guards"].values())


def test_resolve_case_priority_downgrades_model_p0_without_guard() -> None:
    case = {
        "description": "Boundary format check for minimum length only.",
        "test_module": "input-validation",
        "preconditions": [],
        "steps": ["input min-length value"],
        "test_input": "len=1",
        "expected_result": "format warning shown and system is still usable",
        "priority": "P0",
    }
    score_result = score_case_priority(case)
    final_priority = resolve_case_priority("P0", score_result, case)
    assert final_priority == "P1"


def test_resolve_case_priority_promotes_model_p2_when_no_hard_guard_and_high_behavioral_score() -> None:
    case = {
        "description": "Important non-blocking defect in a high frequency submit flow.",
        "test_module": "submit-form",
        "preconditions": [],
        "steps": ["submit main form"],
        "test_input": "valid payload",
        "expected_result": "error shown but user can continue",
        "priority": "P2",
    }
    score_result = score_case_priority(case)
    assert score_result["priority_score"] >= 35
    final_priority = resolve_case_priority("P2", score_result, case)
    assert final_priority == "P1"


def test_apply_priority_semantics_attaches_debug_meta() -> None:
    case = {
        "description": "High-frequency core query shows an error but remains usable.",
        "test_module": "query",
        "preconditions": [],
        "steps": ["run core query"],
        "test_input": "valid arguments",
        "expected_result": "query keeps usable with error warning",
        "priority": "P2",
    }
    output = apply_priority_semantics_to_case(dict(case), attach_debug=True)
    assert output["priority"] in {"P0", "P1", "P2"}
    debug = ((output.get("meta") or {}).get("priority_debug") or {})
    assert debug.get("model_priority") == "P2"
    assert debug.get("normalized_model_priority") in {"P0", "P1", "P2"}
    assert "priority_score" in debug
    assert "priority_reasons" in debug
    assert "priority_guards" in debug
    assert "covered_rule_ids" in debug
    assert "missing_rule_hits" in debug
    assert "core_rule_hits" in debug
    assert "unique_coverage_hits" in debug
    assert "coverage_gain_score" in debug
    assert "rule_risk_reasons" in debug
    assert "case_level_release_blocking" in debug
    assert "case_level_hard_guard" in debug
    assert "p2_cap" in debug
    assert "low_risk_only_covered" in debug
    assert "structural_p2_signals" in debug


def test_rule_only_release_blocking_hit_cannot_pass_p0_guard() -> None:
    case = {
        "id": "TC-003",
        "description": "Result display mapping check without workflow blocking.",
        "test_module": "result-ui",
        "preconditions": [],
        "steps": ["open result page"],
        "test_input": "normal score",
        "expected_result": "badge and color are shown",
        "priority": "P0",
    }
    coverage_context = {
        "missing_rules": ["REQ-300"],
        "rule_diagnostics": [
            {
                "rule_id": "REQ-300",
                "rule_text": "REQ-300 release blocking result display rule",
                "biz_key": "ui",
                "covered": False,
                "missing_types": ["happy"],
            },
        ],
    }

    output = apply_priority_semantics_to_case(
        dict(case),
        attach_debug=True,
        coverage_context=coverage_context,
    )
    debug = ((output.get("meta") or {}).get("priority_debug") or {})
    assert output["priority"] in {"P1", "P2"}
    assert output["priority"] != "P0"
    assert debug.get("case_level_hard_guard") is False


def test_priority_scoring_uses_rule_criticality_and_coverage_gain() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "REQ-101 order submit flow blocked",
            "test_module": "order",
            "preconditions": [],
            "steps": ["submit order"],
            "test_input": "valid payload",
            "expected_result": "order submits successfully",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "REQ-102 boundary format check",
            "test_module": "format",
            "preconditions": [],
            "steps": ["input boundary value"],
            "test_input": "len=1",
            "expected_result": "format warning",
            "priority": "P1",
        },
    ]

    coverage_context = {
        "missing_rules": ["REQ-101"],
        "covered_rules": ["REQ-102"],
        "rule_diagnostics": [
            {
                "rule_id": "REQ-101",
                "rule_text": "REQ-101 core workflow order submit release blocking security",
                "biz_key": "order",
                "covered": False,
                "missing_types": ["happy"],
            },
            {
                "rule_id": "REQ-102",
                "rule_text": "REQ-102 supplemental boundary format validation",
                "biz_key": "order",
                "covered": True,
                "missing_types": [],
            },
        ],
    }

    outputs = apply_priority_semantics_to_cases(
        cases,
        attach_debug=True,
        coverage_context=coverage_context,
    )
    assert len(outputs) == 2
    first_debug = ((outputs[0].get("meta") or {}).get("priority_debug") or {})
    second_debug = ((outputs[1].get("meta") or {}).get("priority_debug") or {})

    assert outputs[0]["priority"] in {"P0", "P1"}
    assert int(first_debug.get("coverage_gain_score") or 0) > 0
    assert "REQ-101" in set(first_debug.get("covered_rule_ids") or [])
    assert "REQ-101" not in set(first_debug.get("missing_rule_hits") or [])
    assert len(first_debug.get("core_rule_hits") or []) >= 1

    assert int(second_debug.get("coverage_gain_score") or 0) <= 0
    assert "REQ-102" in set(second_debug.get("covered_rule_ids") or [])


def test_resolve_case_priority_promotes_near_threshold_p2_when_coverage_signals_exist() -> None:
    case = {
        "description": "core workflow case",
        "test_module": "module-a",
        "preconditions": [],
        "steps": ["submit"],
        "test_input": "payload",
        "expected_result": "ok",
        "priority": "P2",
    }
    score_result = {
        "priority_score": 33,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": False,
        },
        "reasons": ["main_workflow_hit", "core_workflow_rule_hit"],
        "p2_cap": False,
        "coverage_value_exempt": True,
        "missing_rule_hits": [],
        "core_rule_hits": ["RULE-030"],
        "unique_coverage_hits": [],
        "low_risk_only_covered": False,
        "structural_p2_signals": False,
        "case_level_hard_guard": False,
    }
    assert resolve_case_priority("P2", score_result, case) == "P1"


def test_resolve_case_priority_does_not_promote_when_low_risk_only_covered() -> None:
    case = {
        "description": "low risk covered case",
        "test_module": "module-b",
        "preconditions": [],
        "steps": ["check"],
        "test_input": "normal",
        "expected_result": "ok",
        "priority": "P2",
    }
    score_result = {
        "priority_score": 33,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": False,
        },
        "reasons": ["main_workflow_hit"],
        "p2_cap": False,
        "coverage_value_exempt": True,
        "missing_rule_hits": [],
        "core_rule_hits": ["RULE-030"],
        "unique_coverage_hits": [],
        "low_risk_only_covered": True,
        "structural_p2_signals": False,
        "case_level_hard_guard": False,
    }
    assert resolve_case_priority("P2", score_result, case) == "P2"


def test_resolve_case_priority_promotes_security_data_critical_mid_score_case() -> None:
    case = {
        "description": "data integrity critical path",
        "test_module": "module-c",
        "preconditions": [],
        "steps": ["submit"],
        "test_input": "payload",
        "expected_result": "ok",
        "priority": "P2",
    }
    score_result = {
        "priority_score": 22,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": False,
        },
        "reasons": ["security_or_data_critical_rule_hit"],
        "p2_cap": False,
        "coverage_value_exempt": True,
        "coverage_gain_score": 10,
        "missing_rule_hits": [],
        "core_rule_hits": [],
        "unique_coverage_hits": ["RULE-036"],
        "low_risk_only_covered": False,
        "structural_p2_signals": False,
        "case_level_hard_guard": False,
    }
    assert resolve_case_priority("P2", score_result, case) == "P1"


def test_resolve_case_priority_keeps_p2_for_security_data_with_structural_low_value_signal() -> None:
    case = {
        "description": "data critical but display-only case",
        "test_module": "module-d",
        "preconditions": [],
        "steps": ["check display"],
        "test_input": "normal",
        "expected_result": "ok",
        "priority": "P2",
    }
    score_result = {
        "priority_score": 24,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": False,
        },
        "reasons": ["security_or_data_critical_rule_hit", "structural_p2_low_value_signal"],
        "p2_cap": False,
        "coverage_value_exempt": True,
        "coverage_gain_score": 12,
        "missing_rule_hits": [],
        "core_rule_hits": [],
        "unique_coverage_hits": [],
        "low_risk_only_covered": False,
        "structural_p2_signals": True,
        "case_level_hard_guard": False,
    }
    assert resolve_case_priority("P2", score_result, case) == "P2"


def test_resolve_case_priority_promotes_core_workflow_mid_score_case() -> None:
    case = {
        "description": "core workflow covered with mid score",
        "test_module": "module-e",
        "preconditions": [],
        "steps": ["open page"],
        "test_input": "normal",
        "expected_result": "ok",
        "priority": "P2",
    }
    score_result = {
        "priority_score": 8,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": False,
        },
        "reasons": ["core_workflow_rule_hit"],
        "p2_cap": False,
        "coverage_value_exempt": True,
        "coverage_gain_score": 8,
        "missing_rule_hits": [],
        "core_rule_hits": ["RULE-030"],
        "unique_coverage_hits": [],
        "low_risk_only_covered": False,
        "structural_p2_signals": False,
        "case_level_hard_guard": False,
    }
    assert resolve_case_priority("P2", score_result, case) == "P1"


def test_resolve_case_priority_keeps_p2_for_core_workflow_mid_score_structural_case() -> None:
    case = {
        "description": "core workflow covered but structural display-focused case",
        "test_module": "module-f",
        "preconditions": [],
        "steps": ["open page"],
        "test_input": "normal",
        "expected_result": "ok",
        "priority": "P2",
    }
    score_result = {
        "priority_score": 8,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": False,
        },
        "reasons": ["core_workflow_rule_hit", "structural_p2_low_value_signal"],
        "p2_cap": False,
        "coverage_value_exempt": True,
        "coverage_gain_score": 8,
        "missing_rule_hits": [],
        "core_rule_hits": ["RULE-030"],
        "unique_coverage_hits": [],
        "low_risk_only_covered": False,
        "structural_p2_signals": True,
        "case_level_hard_guard": False,
    }
    assert resolve_case_priority("P2", score_result, case) == "P2"


def test_score_case_priority_skips_no_info_penalty_without_case_rule_hits() -> None:
    case = {
        "description": "main submit flow validation",
        "test_module": "module-g",
        "preconditions": [],
        "steps": ["submit"],
        "test_input": "valid payload",
        "expected_result": "ok",
        "priority": "P2",
    }
    coverage_context = {
        "rule_diagnostics": [
            {
                "rule_id": "REQ-UNRELATED-999",
                "rule_text": "inventory reconciliation job only",
                "biz_key": "inventory",
                "covered": False,
                "missing_types": ["happy"],
            }
        ]
    }
    score_result = score_case_priority(case, coverage_context=coverage_context)
    assert "no_coverage_information_gain" not in (score_result.get("reasons") or [])
    assert "p2_cap_no_coverage_gain_without_hard_guard" not in (score_result.get("reasons") or [])
    assert bool(score_result.get("p2_cap")) is False


def test_score_case_priority_removes_overlap_between_covered_and_missing_rules() -> None:
    case = {
        "description": "RULE-030 spelling submit core workflow case",
        "test_module": "module-h",
        "preconditions": [],
        "steps": ["submit"],
        "test_input": "normal",
        "expected_result": "ok",
        "priority": "P2",
    }
    coverage_context = {
        "missing_rules": ["RULE-030"],
        "covered_rules": ["RULE-030"],
        "rule_diagnostics": [
            {
                "rule_id": "RULE-030",
                "rule_text": "RULE-030 core workflow spelling submit",
                "biz_key": "spelling",
                "covered": True,
                "missing_types": [],
            }
        ],
    }
    score_result = score_case_priority(case, coverage_context=coverage_context)
    covered = set(score_result.get("covered_rule_ids") or [])
    missing = set(score_result.get("missing_rule_hits") or [])
    assert "RULE-030" in covered
    assert "RULE-030" not in missing


def test_apply_priority_semantics_normalizes_stale_score_artifacts_without_coverage_hits() -> None:
    case = {
        "description": "验证拼写测试全错=0星",
        "test_module": "拼写测试-星级计算",
        "preconditions": ["用户进入拼写测试单元"],
        "steps": ["完成4题且全部错误", "提交答案"],
        "test_input": "0题正确",
        "expected_result": "单元卡片点亮0星",
        "priority": "P2",
    }
    output = apply_priority_semantics_to_case(dict(case), attach_debug=True, coverage_context={})
    debug = ((output.get("meta") or {}).get("priority_debug") or {})
    reasons = set(debug.get("priority_reasons") or [])
    assert "no_coverage_information_gain" not in reasons
    assert "p2_cap_no_coverage_gain_without_hard_guard" not in reasons
    assert bool(debug.get("p2_cap")) is False
