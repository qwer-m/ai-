from __future__ import annotations

from typing import Any

from .result_postprocess_priority_rules import (
    _build_priority_coverage_context,
    _contains_any,
    _extract_case_text,
    _normalize_existing_priority,
)
from .case_access import case_text_field

from .postprocess_priority_config import (
    uncertain_requirement_signals,
)
from .result_postprocess_priority_decisions import (
    _build_priority_decision,
    _contains_strong_p0_signal,
    _has_positive_p1_evidence,
    _normalize_score_result_for_debug_and_resolve,
    _resolve_priority_conflict_to_final,
    _should_uplift_to_p1,
)
from .result_postprocess_priority_semantics_split_helpers import (
    score_case_priority,
)

PRIORITY_SEMANTICS_REVISION = "2026-04-28-r7"

_UNCERTAIN_REQUIREMENT_SIGNALS = uncertain_requirement_signals()


def resolve_case_priority_decision(
    model_priority: str,
    score_result: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    normalized_model = _normalize_existing_priority(model_priority)
    case_text = _extract_case_text(case if isinstance(case, dict) else {})
    strong_p0_signal = _contains_strong_p0_signal(case_text)
    uncertain_requirement_hit = _contains_any(str(case_text or "").lower(), _UNCERTAIN_REQUIREMENT_SIGNALS)
    score = int(score_result.get("priority_score") or 0)
    bonus_score = int(score_result.get("bonus_score") or 0)
    p1_uplifted = bool(score_result.get("p1_uplifted"))
    effective_score = int(score + bonus_score) if p1_uplifted else score
    suggested_priority = _normalize_existing_priority(score_result.get("suggested_priority") or "P1")
    guards = dict(score_result.get("guards") or {})
    case_level_hard_guard = bool(score_result.get("case_level_hard_guard"))
    if not case_level_hard_guard:
        case_level_hard_guard = any(
            bool(guards.get(key))
            for key in (
                "main_workflow_blocking",
                "workflow_blocking",
                "severe_data_risk",
                "severe_security_risk",
                "case_level_release_blocking",
            )
        )
    if strong_p0_signal:
        case_level_hard_guard = True
    p2_cap = bool(score_result.get("p2_cap"))
    coverage_value_exempt = bool(score_result.get("coverage_value_exempt"))
    missing_rule_hits = [str(item) for item in (score_result.get("missing_rule_hits") or []) if str(item).strip()]
    core_rule_hits = [str(item) for item in (score_result.get("core_rule_hits") or []) if str(item).strip()]
    unique_coverage_hits = [str(item) for item in (score_result.get("unique_coverage_hits") or []) if str(item).strip()]
    coverage_gain_score = int(score_result.get("coverage_gain_score") or 0)
    low_risk_only_covered = bool(score_result.get("low_risk_only_covered"))
    structural_p2_signals = bool(score_result.get("structural_p2_signals"))
    reasons = [str(item) for item in (score_result.get("reasons") or [])]

    if uncertain_requirement_hit:
        return _build_priority_decision(
            priority_final="P2",
            decision_state="optional",
            decision_source="uncertain_requirement_guard",
            confidence="low",
        )

    if p2_cap and normalized_model == "P0" and not case_level_hard_guard:
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="model_p0_guard_downgrade",
            confidence="high",
        )

    if p2_cap and not case_level_hard_guard:
        return _build_priority_decision(
            priority_final="P2",
            decision_state="decided",
            decision_source="p2_cap_guard",
            confidence="high",
        )

    if normalized_model == "P0":
        p0_score_floor = 0 if strong_p0_signal else 70
        if (not case_level_hard_guard) or score < p0_score_floor:
            return _build_priority_decision(
                priority_final="P1",
                decision_state="decided",
                decision_source="model_p0_guard_downgrade",
                confidence="high",
            )

    if (
        normalized_model == "P2"
        and strong_p0_signal
        and case_level_hard_guard
        and not p2_cap
    ):
        return _build_priority_decision(
            priority_final="P0",
            decision_state="decided",
            decision_source="strong_p0_signal_guard",
            confidence="medium",
        )

    # Third calibration: security/data critical hits with mid score can move from P2 to P1.
    if (
        normalized_model == "P2"
        and effective_score >= 20
        and "security_or_data_critical_rule_hit" in reasons
        and coverage_value_exempt
        and coverage_gain_score > 0
        and not low_risk_only_covered
        and not structural_p2_signals
    ):
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="security_data_critical_promotion",
            confidence="medium",
        )

    # Fourth calibration: core-workflow-covered mid-score cases can move from P2 to P1.
    if (
        normalized_model == "P2"
        and effective_score >= 8
        and "core_workflow_rule_hit" in reasons
        and bool(core_rule_hits)
        and coverage_gain_score >= 8
        and not p2_cap
        and not low_risk_only_covered
        and not structural_p2_signals
    ):
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="core_workflow_promotion",
            confidence="medium",
        )

    if (
        normalized_model == "P2"
        and bool(score_result.get("p1_uplifted"))
        and not p2_cap
        and not low_risk_only_covered
        and not structural_p2_signals
    ):
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="p1_uplift_signal",
            confidence="medium",
            resolution_reason=str(score_result.get("p1_uplift_reason") or "workflow_or_coverage_uplift"),
        )

    # Second calibration: near-threshold + coverage-value cases can move from P2 to P1.
    if (
        normalized_model == "P2"
        and effective_score >= 30
        and coverage_value_exempt
        and bool(missing_rule_hits or core_rule_hits or unique_coverage_hits)
        and not low_risk_only_covered
        and not (structural_p2_signals and not (missing_rule_hits or core_rule_hits))
    ):
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="coverage_signal_promotion",
            confidence="medium",
        )
    if normalized_model == "P2" and effective_score >= 35:
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="score_threshold_promotion",
            confidence="medium",
        )
    if normalized_model == "P1" and case_level_hard_guard and score >= (0 if strong_p0_signal else 70):
        return _build_priority_decision(
            priority_final="P0",
            decision_state="decided",
            decision_source="hard_guard_promotion",
            confidence="high",
        )

    if suggested_priority == "P0" and not (case_level_hard_guard and score >= 70):
        suggested_priority = "P1"

    if normalized_model == suggested_priority:
        return _build_priority_decision(
            priority_final=normalized_model,
            decision_state="decided",
            decision_source="model_semantic_consistent",
            confidence="high",
        )

    pair = {normalized_model, suggested_priority}
    if pair == {"P0", "P1"}:
        return _build_priority_decision(
            priority_final="P1",
            decision_state="decided",
            decision_source="guarded_downgrade",
            confidence="medium",
        )
    if pair == {"P1", "P2"}:
        explicit_low_value = any(
            reason in reasons
            for reason in (
                "boundary_or_low_risk_validation",
                "long_tail_or_supplemental",
                "non_critical_perf_or_ui",
                "completeness_only",
                "structural_p2_low_value_signal",
                "p2_cap_no_coverage_gain_without_hard_guard",
                "p2_cap_low_risk_only_covered_rules",
                "p2_cap_display_mapping_scenario",
            )
        )
        positive_p1_evidence = _has_positive_p1_evidence(score_result, reasons)
        if normalized_model == "P1" and positive_p1_evidence and not explicit_low_value:
            return _build_priority_decision(
                priority_final="P1",
                decision_state="decided",
                decision_source="model_p1_with_positive_evidence",
                confidence="medium",
            )
        if normalized_model == "P1":
            return _resolve_priority_conflict_to_final(
                normalized_model=normalized_model,
                suggested_priority=suggested_priority,
                score_result=score_result,
                case=case,
            )
        return _build_priority_decision(
            priority_final="P2",
            decision_state="decided",
            decision_source="semantic_low_value",
            confidence="medium",
        )
    if pair == {"P0", "P2"}:
        return _resolve_priority_conflict_to_final(
            normalized_model=normalized_model,
            suggested_priority=suggested_priority,
            score_result=score_result,
            case=case,
        )
    return _build_priority_decision(
        priority_final=suggested_priority,
        decision_state="decided",
        decision_source="semantic_fallback",
        confidence="medium",
    )


def resolve_case_priority(model_priority: str, score_result: dict[str, Any], case: dict[str, Any]) -> str:
    decision = resolve_case_priority_decision(model_priority, score_result, case)
    final_priority = str(decision.get("priority_final") or "").strip().upper()
    if final_priority in {"P0", "P1", "P2"}:
        return final_priority
    return _normalize_existing_priority(model_priority)

def apply_priority_semantics_to_case(
    case: dict[str, Any],
    *,
    attach_debug: bool = False,
    coverage_context: dict[str, Any] | None = None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    input_priority = case_text_field(case, "priority")
    model_priority = str(
        case.get("model_priority_current")
        or case.get("model_priority")
        or input_priority
    ).strip()
    normalized_model_priority = _normalize_existing_priority(model_priority)
    normalized_input_priority = _normalize_existing_priority(input_priority)
    if not str(case.get("model_priority_current") or "").strip():
        case["model_priority_current"] = normalized_model_priority
    if not str(case.get("model_priority") or "").strip():
        case["model_priority"] = normalized_model_priority
    if not str(case.get("legacy_priority") or "").strip():
        case["legacy_priority"] = normalized_input_priority
    score_result = score_case_priority(
        case,
        coverage_context=coverage_context,
        rule_diagnostics=rule_diagnostics,
    )
    score_result = _normalize_score_result_for_debug_and_resolve(score_result)
    p1_uplifted = False
    p1_uplift_reason = ""
    uplift_source = ""
    bonus_score = 0
    if normalized_model_priority == "P2":
        p1_uplifted, p1_uplift_reason, uplift_source, bonus_score = _should_uplift_to_p1(score_result)
        if p1_uplifted and bool(score_result.get("p2_cap")):
            p1_uplifted = False
            p1_uplift_reason = "p2_cap_blocked"
            uplift_source = "blocked_p2_cap"
            bonus_score = 0
    score_result["p1_uplifted"] = p1_uplifted
    score_result["p1_uplift_reason"] = p1_uplift_reason
    score_result["uplift_source"] = uplift_source
    score_result["bonus_score"] = bonus_score
    decision = resolve_case_priority_decision(normalized_model_priority, score_result, case)
    final_priority = str(decision.get("priority_final") or "").strip().upper()
    if final_priority in {"P0", "P1", "P2"}:
        case["priority"] = final_priority
    else:
        case["priority"] = normalized_model_priority
    case["priority_final"] = decision.get("priority_final")
    case["priority_decision_state"] = str(decision.get("priority_decision_state") or "undetermined")
    case["priority_decision_source"] = str(decision.get("priority_decision_source") or "insufficient_evidence")
    case["priority_confidence"] = str(decision.get("priority_confidence") or "low")
    case["priority_conflict_reason"] = str(decision.get("priority_conflict_reason") or "")
    case["priority_resolution_reason"] = str(decision.get("priority_resolution_reason") or "")

    if attach_debug:
        meta = case.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        meta["priority_debug"] = {
            "model_priority": model_priority,
            "normalized_model_priority": normalized_model_priority,
            "priority_score": int(score_result.get("priority_score") or 0),
            "suggested_priority": str(score_result.get("suggested_priority") or "P2"),
            "final_priority": case.get("priority_final"),
            "priority_decision_state": str(decision.get("priority_decision_state") or "undetermined"),
            "priority_decision_source": str(decision.get("priority_decision_source") or "insufficient_evidence"),
            "priority_confidence": str(decision.get("priority_confidence") or "low"),
            "priority_conflict_reason": str(decision.get("priority_conflict_reason") or ""),
            "priority_resolution_reason": str(decision.get("priority_resolution_reason") or ""),
            "focus_score": int(score_result.get("focus_score") or 0),
            "ui_like_case": bool(score_result.get("ui_like_case")),
            "cross_page_flow_hit": bool(score_result.get("cross_page_flow_hit")),
            "state_transition_hit": bool(score_result.get("state_transition_hit")),
            "preferred_pattern_hit": bool(score_result.get("preferred_pattern_hit")),
            "reuse_risk_hit": bool(score_result.get("reuse_risk_hit")),
            "priority_reasons": [str(item) for item in (score_result.get("reasons") or [])],
            "priority_guards": dict(score_result.get("guards") or {}),
            "covered_rule_ids": [str(item) for item in (score_result.get("covered_rule_ids") or [])],
            "missing_rule_hits": [str(item) for item in (score_result.get("missing_rule_hits") or [])],
            "core_rule_hits": [str(item) for item in (score_result.get("core_rule_hits") or [])],
            "unique_coverage_hits": [str(item) for item in (score_result.get("unique_coverage_hits") or [])],
            "coverage_gain_score": int(score_result.get("coverage_gain_score") or 0),
            "rule_risk_reasons": [str(item) for item in (score_result.get("rule_risk_reasons") or [])],
            "case_level_release_blocking": bool(score_result.get("case_level_release_blocking")),
            "case_level_hard_guard": bool(score_result.get("case_level_hard_guard")),
            "p1_uplifted": bool(score_result.get("p1_uplifted")),
            "p1_uplift_reason": str(score_result.get("p1_uplift_reason") or ""),
            "uplift_source": str(score_result.get("uplift_source") or ""),
            "bonus_score": int(score_result.get("bonus_score") or 0),
            "p2_cap": bool(score_result.get("p2_cap")),
            "p2_cap_exempted": bool(score_result.get("p2_cap_exempted")),
            "p2_cap_exemption_reasons": [str(item) for item in (score_result.get("p2_cap_exemption_reasons") or [])],
            "coverage_value_exempt": bool(score_result.get("coverage_value_exempt")),
            "low_risk_only_covered": bool(score_result.get("low_risk_only_covered")),
            "structural_p2_signals": bool(score_result.get("structural_p2_signals")),
            "semantics_revision": PRIORITY_SEMANTICS_REVISION,
        }
        case["meta"] = meta
    return case


def apply_priority_semantics_to_cases(
    cases: list[dict[str, Any]],
    *,
    attach_debug: bool = False,
    coverage_context: dict[str, Any] | None = None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    effective_coverage_context = _build_priority_coverage_context(
        [item for item in cases if isinstance(item, dict)],
        coverage_context=coverage_context,
        rule_diagnostics=rule_diagnostics,
    )
    output: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        normalized = dict(case)
        output.append(
            apply_priority_semantics_to_case(
                normalized,
                attach_debug=attach_debug,
                coverage_context=effective_coverage_context,
                rule_diagnostics=rule_diagnostics,
            )
        )
    return output

