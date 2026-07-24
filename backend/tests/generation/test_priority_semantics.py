import sys
import importlib
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.postprocess.result_postprocess import (
    apply_priority_semantics_to_case,
    apply_priority_semantics_to_cases,
    resolve_case_priority_decision,
    resolve_case_priority,
    score_case_priority,
)

priority_semantics_module = importlib.import_module(
    "modules.test_generation_components.postprocess.result_postprocess_priority_semantics"
)


def test_score_case_priority_requires_structured_guard_for_p0() -> None:
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
    assert score_result["suggested_priority"] == "P1"
    assert score_result["guards"]["case_level_hard_guard"] is False
    assert score_result["guards"]["text_workflow_blocking_diagnostic"] is True

    structured_result = score_case_priority({**case, "critical": True})
    assert structured_result["suggested_priority"] == "P0"
    assert structured_result["guards"]["case_level_hard_guard"] is True


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


def test_business_noun_alone_does_not_promote_model_p2_to_p0() -> None:
    case = {
        "description": "Verify wrong question collection is generated after wrong answers.",
        "test_module": "learning-error-book",
        "preconditions": ["student has submitted answers"],
        "steps": ["submit wrong answers", "open wrong question collection"],
        "test_input": "wrong answer set",
        "expected_result": "wrong question collection contains the generated item",
        "priority": "P2",
    }

    output = apply_priority_semantics_to_case(dict(case), attach_debug=True)
    debug = ((output.get("meta") or {}).get("priority_debug") or {})

    assert output["priority"] in {"P1", "P2"}
    assert output["priority_final"] in {"P1", "P2"}
    assert output["priority_decision_source"] != "strong_p0_signal_guard"
    assert debug.get("priority_decision_source") != "strong_p0_signal_guard"


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


def test_apply_priority_semantics_preserves_original_priority_fields_on_reapply() -> None:
    case = {
        "description": "Important submit flow remains usable after a non-blocking warning.",
        "test_module": "submit-form",
        "preconditions": [],
        "steps": ["submit main form"],
        "test_input": "valid payload",
        "expected_result": "warning is shown and user can continue",
        "priority": "P2",
    }

    first = apply_priority_semantics_to_case(dict(case), attach_debug=True)
    second = apply_priority_semantics_to_case(dict(first), attach_debug=True)

    assert first["model_priority_current"] == "P2"
    assert first["model_priority"] == "P2"
    assert first["legacy_priority"] == "P2"
    assert second["model_priority_current"] == "P2"
    assert second["model_priority"] == "P2"
    assert second["legacy_priority"] == "P2"


def test_reuse_risk_cases_are_treated_as_high_value_for_priority() -> None:
    case = {
        "description": "复用页面后完成返回首页，不应残留原列表页跳转。",
        "test_module": "lesson-flow",
        "preconditions": [],
        "steps": ["进入复用页面", "完成学习流程", "校验返回目标"],
        "test_input": "lesson=A",
        "expected_result": "返回首页且不出现旧按钮/旧跳转",
        "priority": "P2",
    }

    score_result = score_case_priority(case)

    assert score_result["reuse_risk_hit"] is True
    assert "reuse_risk_hit" in score_result["reasons"]

    output = apply_priority_semantics_to_case(dict(case), attach_debug=True)
    debug = ((output.get("meta") or {}).get("priority_debug") or {})
    assert debug.get("reuse_risk_hit") is True
    assert output["priority"] in {"P1", "P2"}


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


def test_resolve_case_priority_decision_marks_conflict_for_model_p0_vs_semantic_p2() -> None:
    case = {
        "description": "release blocking wording exists but semantic suggestion is low",
        "test_module": "module-conflict",
        "steps": ["submit"],
        "expected_result": "state stays usable",
        "priority": "P0",
    }
    score_result = {
        "priority_score": 80,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": True,
            "workflow_blocking": True,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": True,
        },
        "reasons": ["main_workflow_hit"],
        "p2_cap": False,
        "coverage_value_exempt": False,
        "missing_rule_hits": [],
        "core_rule_hits": [],
        "unique_coverage_hits": [],
        "coverage_gain_score": 0,
        "low_risk_only_covered": False,
        "structural_p2_signals": False,
        "case_level_hard_guard": True,
    }
    decision = resolve_case_priority_decision("P0", score_result, case)
    assert decision.get("priority_final") == "P0"
    assert decision.get("priority_decision_state") == "conflict_resolved"
    assert decision.get("priority_decision_source") == "conflict_resolved_by_high_risk_business_rule"


def test_resolve_case_priority_decision_uses_alias_fields_for_p1_conflict_keywords() -> None:
    case = {
        "title": "workflow state wording exists but semantic suggestion is low",
        "testModule": "module-conflict",
        "testSteps": ["submit"],
        "expectedResult": "state stays usable",
        "Priority": "P1",
    }
    score_result = {
        "priority_score": 20,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": False,
        },
        "reasons": [],
        "p2_cap": False,
        "coverage_value_exempt": False,
        "missing_rule_hits": [],
        "core_rule_hits": [],
        "unique_coverage_hits": [],
        "coverage_gain_score": 0,
        "low_risk_only_covered": False,
        "structural_p2_signals": False,
        "case_level_hard_guard": False,
    }

    decision = resolve_case_priority_decision("P1", score_result, case)

    assert decision.get("priority_final") == "P1"
    assert decision.get("priority_decision_source") == "conflict_resolved_by_core_business_rule"


def test_resolve_case_priority_decision_marks_undetermined_for_model_p1_without_positive_evidence() -> None:
    case = {
        "description": "普通展示校验",
        "test_module": "ui-page",
        "steps": ["open page"],
        "expected_result": "display ok",
        "priority": "P1",
    }
    score_result = {
        "priority_score": 20,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": False,
        },
        "reasons": ["no_release_blocking_guard"],
        "p2_cap": False,
        "coverage_value_exempt": False,
        "missing_rule_hits": [],
        "core_rule_hits": [],
        "unique_coverage_hits": [],
        "coverage_gain_score": 0,
        "low_risk_only_covered": False,
        "structural_p2_signals": False,
        "case_level_hard_guard": False,
    }
    decision = resolve_case_priority_decision("P1", score_result, case)
    assert decision.get("priority_final") == "P2"
    assert decision.get("priority_decision_state") == "conflict_resolved"
    assert decision.get("priority_decision_source") == "conflict_resolved_by_non_blocking_experience_rule"


def test_resolve_case_priority_decision_marks_optional_when_requirement_is_uncertain() -> None:
    case = {
        "description": "能力模型评分需教研确认，本期可以不做",
        "test_module": "学习报告",
        "steps": ["open report"],
        "expected_result": "score shown",
        "priority": "P1",
    }
    score_result = {
        "priority_score": 70,
        "suggested_priority": "P0",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": False,
            "severe_security_risk": False,
            "case_level_release_blocking": False,
        },
        "reasons": ["main_workflow_hit"],
        "p2_cap": False,
        "coverage_value_exempt": False,
        "missing_rule_hits": [],
        "core_rule_hits": [],
        "unique_coverage_hits": [],
        "coverage_gain_score": 0,
        "low_risk_only_covered": False,
        "structural_p2_signals": False,
        "case_level_hard_guard": False,
    }
    decision = resolve_case_priority_decision("P1", score_result, case)
    assert decision.get("priority_final") == "P2"
    assert decision.get("priority_decision_state") == "optional"
    assert decision.get("priority_decision_source") == "uncertain_requirement_guard"


def test_resolve_case_priority_decision_keeps_permission_isolation_case_out_of_p2() -> None:
    case = {
        "description": "验证未授权用户越权访问他人数据会被权限拦截",
        "test_module": "权限与数据隔离",
        "steps": ["open direct url", "observe forbidden response"],
        "expected_result": "permission denied and data isolation holds",
        "priority": "P0",
    }
    score_result = {
        "priority_score": 25,
        "suggested_priority": "P2",
        "guards": {
            "main_workflow_blocking": False,
            "workflow_blocking": False,
            "severe_data_risk": True,
            "severe_security_risk": True,
            "case_level_release_blocking": True,
        },
        "reasons": ["security_or_data_critical_rule_hit"],
        "p2_cap": False,
        "coverage_value_exempt": True,
        "missing_rule_hits": [],
        "core_rule_hits": [],
        "unique_coverage_hits": [],
        "coverage_gain_score": 2,
        "low_risk_only_covered": False,
        "structural_p2_signals": False,
        "case_level_hard_guard": True,
    }
    decision = resolve_case_priority_decision("P0", score_result, case)
    assert decision.get("priority_final") in {"P0", "P1"}
    assert decision.get("priority_final") != "P2"


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


def test_apply_priority_semantics_relaxes_workflow_uplift_threshold(monkeypatch) -> None:
    def fake_score_case_priority(*_: object, **__: object) -> dict[str, object]:
        return {
            "priority_score": 20,
            "suggested_priority": "P2",
            "guards": {
                "main_workflow_blocking": False,
                "workflow_blocking": False,
                "severe_data_risk": False,
                "severe_security_risk": False,
                "case_level_release_blocking": False,
            },
            "reasons": ["main_workflow_hit"],
            "focus_score": 1,
            "ui_like_case": False,
            "cross_page_flow_hit": False,
            "state_transition_hit": False,
            "preferred_pattern_hit": False,
            "covered_rule_ids": [],
            "missing_rule_hits": [],
            "core_rule_hits": [],
            "unique_coverage_hits": [],
            "coverage_gain_score": 0,
            "rule_risk_reasons": [],
            "p2_cap": False,
            "p2_cap_exempted": False,
            "p2_cap_exemption_reasons": [],
            "coverage_value_exempt": False,
            "low_risk_only_covered": False,
            "structural_p2_signals": False,
            "case_level_hard_guard": False,
            "case_level_release_blocking": False,
        }

    monkeypatch.setattr(priority_semantics_module, "score_case_priority", fake_score_case_priority)

    case = {
        "description": "main workflow path",
        "test_module": "module-z",
        "steps": ["submit"],
        "expected_result": "success",
        "priority": "P2",
    }
    output = priority_semantics_module.apply_priority_semantics_to_case(dict(case), attach_debug=True)
    debug = ((output.get("meta") or {}).get("priority_debug") or {})
    assert output["priority"] == "P1"
    assert output["priority_final"] == "P1"
    assert output["priority_decision_source"] == "p1_uplift_signal"
    assert bool(debug.get("p1_uplifted")) is True
    assert str(debug.get("p1_uplift_reason") or "") == "workflow_focus_relaxed"


def test_score_case_priority_marks_flow_signal_hits() -> None:
    case = {
        "description": "cross-page navigation chain validates state transition",
        "test_module": "learning-flow",
        "steps": ["page jump to details", "resume after interruption"],
        "expected_result": "state transition remains consistent",
        "pattern_category": "cross_page_flow",
        "priority": "P2",
    }
    score_result = score_case_priority(case)
    reasons = set(score_result.get("reasons") or [])
    assert bool(score_result.get("cross_page_flow_hit")) is True
    assert bool(score_result.get("state_transition_hit")) is True
    assert bool(score_result.get("preferred_pattern_hit")) is True
    assert "cross_page_flow_hit" in reasons
    assert "state_transition_hit" in reasons
    assert "preferred_pattern_hit" in reasons


def test_score_case_priority_ui_like_excludes_flow_or_state_depth_case() -> None:
    case = {
        "description": "button display check after cross-page navigation keeps context consistent",
        "test_module": "learning-flow",
        "steps": ["click card and jump to details", "resume and verify state transition consistency"],
        "expected_result": "context is preserved and no wrong page jump",
        "pattern_category": "cross_page_flow",
        "priority": "P2",
    }
    score_result = score_case_priority(case)
    assert bool(score_result.get("cross_page_flow_hit")) is True
    assert bool(score_result.get("state_transition_hit")) is True
    assert bool(score_result.get("ui_like_case")) is False


def test_score_case_priority_ui_like_excludes_state_guard_expected_result_case() -> None:
    case = {
        "description": "button display check after resume",
        "test_module": "learning-flow",
        "steps": ["click card and open details"],
        "expected_result": "不丢上下文，不串课文",
        "pattern_category": "ui_display",
        "priority": "P2",
    }
    score_result = score_case_priority(case)
    assert bool(score_result.get("ui_like_case")) is False


def test_score_case_priority_ui_like_excludes_alias_step_guard_sequence_case() -> None:
    case = {
        "title": "button visibility check for recover flow",
        "testModule": "learning-flow",
        "testSteps": ["return to course list", "re-enter current course and verify state"],
        "expectedResult": "display is correct",
        "patternCategory": "ui_display",
        "Priority": "P2",
    }
    score_result = score_case_priority(case)
    assert bool(score_result.get("ui_like_case")) is False


def test_score_case_priority_ui_like_excludes_step_guard_sequence_case() -> None:
    case = {
        "description": "button visibility check for recover flow",
        "test_module": "learning-flow",
        "steps": ["返回课程列表", "再进入当前课程并校验状态"],
        "expected_result": "display is correct",
        "pattern_category": "ui_display",
        "priority": "P2",
    }
    score_result = score_case_priority(case)
    assert bool(score_result.get("ui_like_case")) is False
