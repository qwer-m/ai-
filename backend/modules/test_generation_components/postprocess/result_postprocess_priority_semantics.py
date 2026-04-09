from __future__ import annotations

from typing import Any

from .result_postprocess_priority_rules import (
    _build_priority_coverage_context,
    _contains_any,
    _contains_case_level_release_blocking,
    _extract_case_text,
    _normalize_existing_priority,
    _priority_case_signature,
)

from modules.test_generation_components.postprocess.result_postprocess_priority_semantics_split_helpers import (
    score_case_priority,
)

PRIORITY_SEMANTICS_REVISION = "2026-04-08-r5"


def _normalize_score_result_for_debug_and_resolve(score_result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(score_result or {})

    covered_rule_ids = [str(item) for item in (normalized.get("covered_rule_ids") or []) if str(item).strip()]
    missing_rule_hits = [str(item) for item in (normalized.get("missing_rule_hits") or []) if str(item).strip()]
    core_rule_hits = [str(item) for item in (normalized.get("core_rule_hits") or []) if str(item).strip()]
    unique_coverage_hits = [str(item) for item in (normalized.get("unique_coverage_hits") or []) if str(item).strip()]
    reasons = [str(item) for item in (normalized.get("reasons") or []) if str(item).strip()]

    covered_rule_ids = list(dict.fromkeys(covered_rule_ids))
    covered_set = set(covered_rule_ids)
    missing_rule_hits = [rid for rid in dict.fromkeys(missing_rule_hits) if rid not in covered_set]
    core_rule_hits = [rid for rid in dict.fromkeys(core_rule_hits) if rid in covered_set]
    unique_coverage_hits = [rid for rid in dict.fromkeys(unique_coverage_hits) if rid in covered_set]

    has_coverage_signals = bool(covered_rule_ids or missing_rule_hits or core_rule_hits or unique_coverage_hits)
    if not has_coverage_signals:
        drop_reasons = {
            "no_coverage_information_gain",
            "p2_cap_no_coverage_gain_without_hard_guard",
            "redundant_covered_normal_rules",
            "p2_cap_low_risk_only_covered_rules",
            "low_risk_only_covered_rules_penalty",
            "p2_capped",
        }
        reasons = [reason for reason in reasons if reason not in drop_reasons]
        normalized["coverage_gain_score"] = max(0, int(normalized.get("coverage_gain_score") or 0))
        normalized["p2_cap"] = False
        normalized["p2_cap_exempted"] = False
        normalized["p2_cap_exemption_reasons"] = []
        normalized["low_risk_only_covered"] = False

    normalized["covered_rule_ids"] = covered_rule_ids
    normalized["missing_rule_hits"] = missing_rule_hits
    normalized["core_rule_hits"] = core_rule_hits
    normalized["unique_coverage_hits"] = unique_coverage_hits
    normalized["reasons"] = reasons
    return normalized

def resolve_case_priority(model_priority: str, score_result: dict[str, Any], case: dict[str, Any]) -> str:
    del case  # compatibility hook for future semantic overrides
    normalized_model = _normalize_existing_priority(model_priority)
    score = int(score_result.get("priority_score") or 0)
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
    p2_cap = bool(score_result.get("p2_cap"))
    coverage_value_exempt = bool(score_result.get("coverage_value_exempt"))
    missing_rule_hits = [str(item) for item in (score_result.get("missing_rule_hits") or []) if str(item).strip()]
    core_rule_hits = [str(item) for item in (score_result.get("core_rule_hits") or []) if str(item).strip()]
    unique_coverage_hits = [str(item) for item in (score_result.get("unique_coverage_hits") or []) if str(item).strip()]
    coverage_gain_score = int(score_result.get("coverage_gain_score") or 0)
    low_risk_only_covered = bool(score_result.get("low_risk_only_covered"))
    structural_p2_signals = bool(score_result.get("structural_p2_signals"))
    reasons = [str(item) for item in (score_result.get("reasons") or [])]

    if p2_cap and not case_level_hard_guard:
        return "P2"

    if normalized_model == "P0" and (not case_level_hard_guard or score < 70):
        return "P1"

    # Third calibration: security/data critical hits with mid score can move from P2 to P1.
    if (
        normalized_model == "P2"
        and score >= 20
        and "security_or_data_critical_rule_hit" in reasons
        and coverage_value_exempt
        and coverage_gain_score > 0
        and not low_risk_only_covered
        and not structural_p2_signals
    ):
        return "P1"

    # Fourth calibration: core-workflow-covered mid-score cases can move from P2 to P1.
    if (
        normalized_model == "P2"
        and score >= 8
        and "core_workflow_rule_hit" in reasons
        and bool(core_rule_hits)
        and coverage_gain_score >= 8
        and not p2_cap
        and not low_risk_only_covered
        and not structural_p2_signals
    ):
        return "P1"

    # Second calibration: near-threshold + coverage-value cases can move from P2 to P1.
    if (
        normalized_model == "P2"
        and score >= 30
        and coverage_value_exempt
        and bool(missing_rule_hits or core_rule_hits or unique_coverage_hits)
        and not low_risk_only_covered
        and not (structural_p2_signals and not (missing_rule_hits or core_rule_hits))
    ):
        return "P1"
    if normalized_model == "P2" and score >= 35:
        return "P1"
    if normalized_model == "P1" and case_level_hard_guard and score >= 70:
        return "P0"

    if suggested_priority == "P0" and not (case_level_hard_guard and score >= 70):
        suggested_priority = "P1"

    if normalized_model == suggested_priority:
        return normalized_model

    pair = {normalized_model, suggested_priority}
    if pair == {"P0", "P1"}:
        return "P1"
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
        obvious_impact = score >= 45 and any(
            reason in reasons for reason in ("important_non_blocking_flow", "high_frequency_main_flow", "main_workflow_hit")
        )
        if normalized_model == "P1" and not explicit_low_value:
            return "P1"
        return "P1" if obvious_impact else "P2"
    if pair == {"P0", "P2"}:
        return "P1"
    return suggested_priority

def apply_priority_semantics_to_case(
    case: dict[str, Any],
    *,
    attach_debug: bool = False,
    coverage_context: dict[str, Any] | None = None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    model_priority = str(case.get("priority") or "").strip()
    normalized_model_priority = _normalize_existing_priority(model_priority)
    score_result = score_case_priority(
        case,
        coverage_context=coverage_context,
        rule_diagnostics=rule_diagnostics,
    )
    score_result = _normalize_score_result_for_debug_and_resolve(score_result)
    final_priority = resolve_case_priority(normalized_model_priority, score_result, case)
    case["priority"] = final_priority

    if attach_debug:
        meta = case.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        meta["priority_debug"] = {
            "model_priority": model_priority,
            "normalized_model_priority": normalized_model_priority,
            "priority_score": int(score_result.get("priority_score") or 0),
            "suggested_priority": str(score_result.get("suggested_priority") or "P2"),
            "final_priority": final_priority,
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

