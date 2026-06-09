from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterator

from ..control.workflow_blueprint_repository import is_trusted_workflow_contract
from ..coverage.coverage_analyzer import case_complexity_profile
from .execution_plan_validator import (
    materialize_final_case_state_fields,
    validate_main_smoke_semantic_alignment,
    validate_main_smoke_state_chain,
)
from .priority_anchor_rules import (
    p0_case_anchor_text,
    p0_configured_anchor_family,
    p0_cross_domain_essay_case,
    p0_has_core_signal,
    p0_has_low_value_signal,
)
from .result_postprocess_priority_semantics import apply_priority_semantics_to_cases, score_case_priority
from .streaming_p0_groups import (
    covered_p0_groups as _covered_p0_groups,
    required_p0_groups_from_requirement as _required_p0_groups_from_requirement,
)
from .streaming_priority_rebuild import rebuild_priority_by_semantics as _rebuild_priority_by_semantics
from .streaming_case_normalization import (
    is_placeholder_expected_result as _is_placeholder_expected_result,
    normalize_priority_value as _normalize_priority_value,
    normalize_steps as _normalize_steps,
    strip_step_prefix as _strip_step_prefix,
    strip_validation_prefix as _strip_validation_prefix,
)
from .streaming_case_quality import (
    final_quality_drop_reason as _final_quality_drop_reason,
    low_quality_reason as _low_quality_reason,
    quality_drop_detail as _quality_drop_detail,
    record_low_quality_drop as _record_low_quality_drop,
    strip_case_meta_list as _strip_case_meta_list,
)
from .streaming_case_keys import (
    case_coverage_bucket as _coverage_bucket,
    case_focus_score as _focus_score,
    case_priority_score as _priority_score,
    case_signature as _signature,
    dedupe_by_final_description as _dedupe_by_final_description,
    final_description_dedup_key as _final_description_dedup_key,
    review_case_id as _review_case_id,
)
from .streaming_expected_result_builder import build_expected_result_from_case as _build_expected_result_from_case
from .streaming_expected_result_quality import (
    has_concrete_expected_assertion as _has_concrete_expected_assertion,
    has_weak_ambiguous_expected_result as _has_weak_ambiguous_expected_result,
    is_ambiguous_expected_result as _is_ambiguous_expected_result,
    is_non_assertable_expected_result as _is_non_assertable_expected_result,
    looks_template_polluted_expected_result as _looks_template_polluted_expected_result,
    looks_truncated_text as _looks_truncated_text,
)
from .streaming_flow_conflicts import (
    filter_cases_conflicting_with_confirmed_flow_facts as _filter_cases_conflicting_with_confirmed_flow_facts,
)
from .streaming_review_keys import (
    review_domain as _review_domain,
    review_scenario as _review_scenario,
)
from .streaming_review_mapping import (
    map_review_selection_with_reasons as _map_review_selection_with_reasons,
    map_review_to_candidates as _map_review_to_candidates,
    normalize_review_llm_reason as _normalize_review_llm_reason,
)
from .streaming_reasoning_quality import reasoning_leakage_hits as _reasoning_leakage_hits
from .streaming_rule_keys import extract_rule_keys as _extract_rule_keys
from .streaming_semantic_dedup import (
    semantic_deduplicate_cases as _semantic_deduplicate_cases,
)
from .streaming_semantic_text import (
    jaccard_similarity as _jaccard_similarity,
    semantic_signature as _semantic_signature,
    semantic_tokenize as _semantic_tokenize,
)
from .streaming_text_match import (
    build_quality_hint_keywords,
    normalize_match_patterns,
    normalize_match_text as _normalize_match_text,
)
from .streaming_uncertain_requirement import (
    UNCERTAIN_SIGNALS as _UNCERTAIN_SIGNALS,
    apply_uncertain_requirement_downgrade as _apply_uncertain_requirement_downgrade,
    enforce_uncertain_priority_floor as _enforce_uncertain_priority_floor,
    extract_uncertain_requirement_tokens as _extract_uncertain_requirement_tokens,
)
from .streaming_ui_like import is_ui_like_case as _is_ui_like_case

def stream_postprocess_cases(
    *,
    client: Any,
    requirement: str,
    base_prompt: str,
    kb_context: str,
    full_content: str,
    expected_count: int,
    append: bool,
    existing_cases: list[dict[str, Any]],
    existing_unique_count: int,
    start_id: int,
    db: Any,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    count_unique_test_cases_fn: Callable[[list[dict[str, Any]]], int],
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
    build_supplement_closed_loop_instruction_fn: Callable[..., str],
    current_biz_key: str = "",
    multi_pass: bool = True,
    generation_mode: str = "",
    feedback_control_state: dict[str, Any] | None = None,
    requirement_semantics_context: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream postprocess: dedup + quality filtering + rerank + convergence diagnostics."""

    from ..coverage.coverage_analyzer import (
        analyze_case_structure,
        analyze_coverage,
        govern_cases_by_flow_structure,
        summarize_duplicate_excess_by_policy,
    )
    from ..prompting.prompt_orchestration import (
        build_gap_fill_prompt,
        build_review_select_prompt,
    )
    from ..control.feedback_control_state import (
        FeedbackControlState,
    )

    control_state = FeedbackControlState.from_any(feedback_control_state)
    generation_coverage_profile = dict(
        (dict(control_state.source_meta or {}).get("generation_coverage_profile") or {})
    )
    fact_profile = dict((dict(control_state.source_meta or {}).get("fact_profile") or {}))
    project_profile = dict((dict(control_state.source_meta or {}).get("project_profile") or {}))
    manual_quality_profile = dict((dict(control_state.source_meta or {}).get("manual_quality_profile") or {}))
    generation_coverage_mode = str(generation_coverage_profile.get("coverage_mode") or "core_smoke")
    generation_target_case_range = dict(generation_coverage_profile.get("target_case_range") or {})
    must_cover_rule_set = {str(rule).strip().upper() for rule in (control_state.must_cover_rules or []) if str(rule).strip()}
    forbidden_patterns = [str(item).strip() for item in (control_state.forbidden_patterns or []) if str(item).strip()]
    reuse_risks = [str(item).strip() for item in (control_state.reuse_risks or []) if str(item).strip()]
    soft_constraints = [str(item).strip() for item in (control_state.soft_constraints or []) if str(item).strip()]
    quality_fix_hints = [str(item).strip() for item in (control_state.quality_fix_hints or []) if str(item).strip()]
    workflow_blueprints = [
        dict(item)
        for item in (control_state.workflow_blueprints or [])
        if isinstance(item, dict) and isinstance(item.get("steps"), list)
    ]
    trusted_workflow_contracts = [
        item for item in workflow_blueprints if is_trusted_workflow_contract(item)
    ]

    normalized_forbidden_patterns = normalize_match_patterns(forbidden_patterns)
    normalized_reuse_risks = normalize_match_patterns(reuse_risks)
    normalized_soft_constraints = normalize_match_patterns(soft_constraints)
    quality_hint_keywords = build_quality_hint_keywords(quality_fix_hints)

    def _merged_unique_total(new_cases: Any) -> int:
        merged: list[dict[str, Any]] = []
        if append and isinstance(existing_cases, list):
            merged.extend(existing_cases)
        if isinstance(new_cases, list):
            merged.extend(new_cases)
        return count_unique_test_cases_fn(merged)

    def _rank_review_case_for_fill(
        case: dict[str, Any],
        *,
        coverage_context: dict[str, Any] | None,
        rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
    ) -> tuple[int, int, int, int, int, int, int]:
        profile = score_case_priority(
            case,
            coverage_context=coverage_context,
            rule_diagnostics=rule_diagnostics,
        )
        has_coverage_value = bool(
            (profile.get("missing_rule_hits") or [])
            or (profile.get("core_rule_hits") or [])
            or (profile.get("unique_coverage_hits") or [])
        )
        reuse_risk_hit = bool(profile.get("reuse_risk_hit"))
        high_signal = bool(_is_high_signal(case, profile))
        focus_score = int(_focus_score(case))
        coverage_gain_score = int(profile.get("coverage_gain_score") or 0)
        priority_score = int(_priority_score(case))
        complexity_score = int(case_complexity_profile(case).get("complexity_score") or 0)
        return (
            int(has_coverage_value),
            int(reuse_risk_hit),
            int(high_signal),
            int(focus_score),
            int(max(0, coverage_gain_score)),
            -min(complexity_score, 8),
            int(priority_score),
        )

    def _build_review_selection_constraints(
        cases: list[dict[str, Any]],
        *,
        reference_count: int,
        generation_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate_cases = [item for item in cases if isinstance(item, dict)]
        total = int(len(candidate_cases))
        if total <= 0:
            return {
                "target_min_count": 1,
                "target_max_count": 1,
                "priority_min": {},
                "scenario_min": {},
                "domain_min": {},
            }

        reference = max(1, int(reference_count or total))
        profile = dict(generation_profile or {})
        coverage_mode = str(profile.get("coverage_mode") or "").strip()

        priority_counts: dict[str, int] = {}
        scenario_counts: dict[str, int] = {}
        domain_counts: dict[str, int] = {}
        for case in candidate_cases:
            priority = str(case.get("priority") or "").strip().upper()
            if priority in {"P0", "P1", "P2"}:
                priority_counts[priority] = int(priority_counts.get(priority, 0)) + 1
            scenario = _review_scenario(case)
            scenario_counts[scenario] = int(scenario_counts.get(scenario, 0)) + 1
            domain = _review_domain(case)
            domain_counts[domain] = int(domain_counts.get(domain, 0)) + 1

        priority_min: dict[str, int] = {}
        if int(priority_counts.get("P0") or 0) > 0:
            priority_min["P0"] = 1
        if int(priority_counts.get("P1") or 0) > 0:
            priority_min["P1"] = min(int(priority_counts.get("P1") or 0), 2)
        if int(priority_counts.get("P2") or 0) > 0:
            priority_min["P2"] = 1

        scenario_min: dict[str, int] = {}
        for scenario in ("happy", "state", "exception"):
            if int(scenario_counts.get(scenario) or 0) > 0:
                scenario_min[scenario] = 1

        domain_min: dict[str, int] = {}
        for domain in ("permission", "report"):
            if int(domain_counts.get(domain) or 0) > 0:
                domain_min[domain] = 1

        active_priority_count = int(sum(1 for value in priority_counts.values() if int(value) > 0))
        active_scenario_count = int(sum(1 for value in scenario_counts.values() if int(value) > 0))
        active_domain_count = int(sum(1 for value in domain_counts.values() if int(value) > 0))
        diversity_floor = int((active_priority_count * 2) + active_scenario_count + min(2, active_domain_count))

        target_min = min(
            total,
            max(
                8,
                int(round(total * 0.24)),
                int(diversity_floor),
            ),
        )
        target_max = min(
            total,
            max(
                int(target_min + 6),
                int(round(total * 0.42)),
                int(round(target_min * 1.6)),
            ),
        )

        if coverage_mode == "full_functional_regression":
            target_min = min(
                total,
                max(
                    target_min,
                    int(round(total * 0.65)),
                    int(round(reference * 0.35)),
                ),
            )
            target_max = min(
                total,
                max(
                    target_min,
                    int(round(total * 0.90)),
                    int(round(reference * 0.75)),
                ),
            )
        elif coverage_mode == "expanded_regression":
            target_min = min(
                total,
                max(
                    target_min,
                    int(round(total * 0.80)),
                    int(round(reference * 0.80)),
                ),
            )
            target_max = min(
                total,
                max(
                    target_min,
                    int(round(total * 0.96)),
                    int(round(reference * 1.10)),
                ),
            )
        elif coverage_mode == "standard_regression":
            target_min = min(
                total,
                max(
                    target_min,
                    int(round(total * 0.45)),
                    int(round(reference * 0.35)),
                ),
            )
            target_max = min(
                total,
                max(
                    target_min,
                    int(round(total * 0.70)),
                    int(round(reference * 0.65)),
                ),
            )
        else:
            reference_cap = max(12, int(round(reference * 0.65)))
            target_max = min(target_max, total, reference_cap)
        target_min = min(target_min, target_max)

        return {
            "target_min_count": int(target_min),
            "target_max_count": int(target_max),
            "priority_min": priority_min,
            "scenario_min": scenario_min,
            "domain_min": domain_min,
        }

    def _enforce_review_selection_constraints(
        *,
        selected_cases: list[dict[str, Any]],
        pool_cases: list[dict[str, Any]],
        constraints: dict[str, Any],
        coverage_context: dict[str, Any] | None,
        rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        selected: list[dict[str, Any]] = []
        selected_signature_set: set[str] = set()
        selected_case_id_set: set[str] = set()
        constraint_reasons: dict[str, str] = {}

        def _append(case: dict[str, Any], reason: str = "") -> None:
            signature = _signature(case)
            if not signature or signature in selected_signature_set:
                return
            selected.append(case)
            selected_signature_set.add(signature)
            case_id = _review_case_id(case)
            if case_id:
                selected_case_id_set.add(case_id)
            if reason and signature:
                constraint_reasons[signature] = reason

        for case in selected_cases:
            if isinstance(case, dict):
                _append(case)

        all_pool_cases = [item for item in pool_cases if isinstance(item, dict)]
        remaining_pool = [item for item in all_pool_cases if _signature(item) not in selected_signature_set]

        def _select_best(predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
            candidates = [item for item in remaining_pool if predicate(item)]
            if not candidates:
                return None
            candidates.sort(
                key=lambda item: tuple(
                    -value
                    for value in _rank_review_case_for_fill(
                        item,
                        coverage_context=coverage_context,
                        rule_diagnostics=rule_diagnostics,
                    )
                )
                + (_review_case_id(item),)
            )
            return candidates[0]

        def _selected_priority_count(priority: str) -> int:
            return int(
                sum(1 for item in selected if str(item.get("priority") or "").strip().upper() == str(priority).upper())
            )

        def _selected_scenario_count(scenario: str) -> int:
            return int(sum(1 for item in selected if _review_scenario(item) == str(scenario).strip().lower()))

        def _selected_domain_count(domain: str) -> int:
            return int(sum(1 for item in selected if _review_domain(item) == str(domain).strip().lower()))

        priority_min = dict(constraints.get("priority_min") or {})
        for priority, min_count in priority_min.items():
            required = max(0, int(min_count or 0))
            while _selected_priority_count(priority) < required:
                best = _select_best(
                    lambda item, p=priority: str(item.get("priority") or "").strip().upper() == str(p).upper()
                )
                if best is None:
                    break
                _append(best, reason=f"retained_by_constraint_priority_{priority}")
                remaining_pool = [item for item in remaining_pool if _signature(item) != _signature(best)]

        scenario_min = dict(constraints.get("scenario_min") or {})
        for scenario, min_count in scenario_min.items():
            required = max(0, int(min_count or 0))
            while _selected_scenario_count(scenario) < required:
                best = _select_best(lambda item, s=scenario: _review_scenario(item) == str(s).strip().lower())
                if best is None:
                    break
                _append(best, reason=f"retained_by_constraint_scenario_{scenario}")
                remaining_pool = [item for item in remaining_pool if _signature(item) != _signature(best)]

        domain_min = dict(constraints.get("domain_min") or {})
        for domain, min_count in domain_min.items():
            required = max(0, int(min_count or 0))
            while _selected_domain_count(domain) < required:
                best = _select_best(lambda item, d=domain: _review_domain(item) == str(d).strip().lower())
                if best is None:
                    break
                _append(best, reason=f"retained_by_constraint_domain_{domain}")
                remaining_pool = [item for item in remaining_pool if _signature(item) != _signature(best)]

        target_min_count = max(1, int(constraints.get("target_min_count") or 1))
        while len(selected) < target_min_count:
            best = _select_best(lambda item: True)
            if best is None:
                break
            _append(best, reason="retained_by_constraint_target_min")
            remaining_pool = [item for item in remaining_pool if _signature(item) != _signature(best)]

        target_max_count = max(target_min_count, int(constraints.get("target_max_count") or target_min_count))
        if len(selected) > target_max_count:
            priority_min = {str(key).strip().upper(): max(0, int(value or 0)) for key, value in dict(constraints.get("priority_min") or {}).items()}
            scenario_min = {str(key).strip().lower(): max(0, int(value or 0)) for key, value in dict(constraints.get("scenario_min") or {}).items()}
            domain_min = {str(key).strip().lower(): max(0, int(value or 0)) for key, value in dict(constraints.get("domain_min") or {}).items()}

            def _can_remove(case: dict[str, Any], current: list[dict[str, Any]]) -> bool:
                priority = str(case.get("priority") or "").strip().upper()
                scenario = _review_scenario(case)
                domain = _review_domain(case)
                if priority in priority_min:
                    count = sum(
                        1 for item in current if str(item.get("priority") or "").strip().upper() == priority
                    )
                    if count <= int(priority_min.get(priority) or 0):
                        return False
                if scenario in scenario_min:
                    count = sum(1 for item in current if _review_scenario(item) == scenario)
                    if count <= int(scenario_min.get(scenario) or 0):
                        return False
                if domain in domain_min:
                    count = sum(1 for item in current if _review_domain(item) == domain)
                    if count <= int(domain_min.get(domain) or 0):
                        return False
                return True

            removal_candidates = list(selected)
            removal_candidates.sort(
                key=lambda item: tuple(
                    _rank_review_case_for_fill(
                        item,
                        coverage_context=coverage_context,
                        rule_diagnostics=rule_diagnostics,
                    )
                )
                + (_review_case_id(item),)
            )

            for case in removal_candidates:
                if len(selected) <= target_max_count:
                    break
                if not _can_remove(case, selected):
                    continue
                signature = _signature(case)
                selected = [item for item in selected if _signature(item) != signature]
                if signature in selected_signature_set:
                    selected_signature_set.remove(signature)
                case_id = _review_case_id(case)
                if case_id and case_id in selected_case_id_set:
                    selected_case_id_set.remove(case_id)
                # Do not override stronger constraint reasons; mark auto cap only for plain-selected cases.
                if signature not in constraint_reasons:
                    constraint_reasons[signature] = "dropped_by_target_max"

        return selected, constraint_reasons

    def _is_high_signal(case: dict[str, Any], score_profile: dict[str, Any] | None = None) -> bool:
        """
        中文注释：高信号判定以 coverage/focus 为主，不再由 priority 主导。
        priority 仅作为极弱 tie-breaker，不参与 high_signal 结论。
        """
        profile = score_profile if isinstance(score_profile, dict) else {}
        focus_score = int(_focus_score(case))
        missing_rule_hits = [str(x) for x in (profile.get("missing_rule_hits") or []) if str(x).strip()]
        core_rule_hits = [str(x) for x in (profile.get("core_rule_hits") or []) if str(x).strip()]
        unique_coverage_hits = [str(x) for x in (profile.get("unique_coverage_hits") or []) if str(x).strip()]
        rule_risk_reasons = [str(x).strip().lower() for x in (profile.get("rule_risk_reasons") or []) if str(x).strip()]
        has_coverage_value = bool(missing_rule_hits or core_rule_hits or unique_coverage_hits)
        has_high_risk_signal = "high" in rule_risk_reasons
        coverage_gain_score = int(profile.get("coverage_gain_score") or 0)
        reuse_risk_hit = bool(profile.get("reuse_risk_hit"))
        return bool(
            has_coverage_value or has_high_risk_signal or reuse_risk_hit or focus_score >= 2 or coverage_gain_score >= 8
        )

    def _hit_must_cover_rule(rule_keys: list[str], score_profile: dict[str, Any] | None = None) -> bool:
        if not must_cover_rule_set:
            return False
        profile = dict(score_profile or {})
        case_rules = set()
        for key in (rule_keys or []):
            normalized = str(key or "").strip().upper()
            if normalized:
                case_rules.add(normalized)
        for field in ("covered_rule_ids", "missing_rule_hits", "core_rule_hits", "unique_coverage_hits"):
            for item in (profile.get(field) or []):
                normalized = str(item or "").strip().upper()
                if normalized:
                    case_rules.add(normalized)
        return bool(case_rules.intersection(must_cover_rule_set))

    def _violates_forbidden_pattern(case: dict[str, Any]) -> bool:
        if not normalized_forbidden_patterns:
            return False
        text = _normalize_match_text(
            " ".join(
                [
                    str(case.get("description") or ""),
                    str(case.get("test_module") or ""),
                    str(case.get("expected_result") or ""),
                    str(case.get("test_input") or ""),
                    " ".join([str(x) for x in (case.get("steps") or []) if str(x).strip()]) if isinstance(case.get("steps"), list) else "",
                ]
            )
        )
        return any(pattern and pattern in text for pattern in normalized_forbidden_patterns)

    def _hits_soft_constraint(case: dict[str, Any]) -> bool:
        if not normalized_soft_constraints:
            return False
        text = _normalize_match_text(
            " ".join(
                [
                    str(case.get("description") or ""),
                    str(case.get("test_module") or ""),
                    str(case.get("expected_result") or ""),
                    str(case.get("test_input") or ""),
                    " ".join([str(x) for x in (case.get("steps") or []) if str(x).strip()]) if isinstance(case.get("steps"), list) else "",
                ]
            )
        )
        return any(pattern and pattern in text for pattern in normalized_soft_constraints)

    def _hits_reuse_risk(case: dict[str, Any], score_profile: dict[str, Any] | None = None) -> bool:
        if bool((score_profile or {}).get("reuse_risk_hit")):
            return True
        if not normalized_reuse_risks:
            return False
        text = _normalize_match_text(
            " ".join(
                [
                    str(case.get("description") or ""),
                    str(case.get("test_module") or ""),
                    str(case.get("expected_result") or ""),
                    str(case.get("test_input") or ""),
                    " ".join([str(x) for x in (case.get("steps") or []) if str(x).strip()]) if isinstance(case.get("steps"), list) else "",
                ]
            )
        )
        return any(pattern and pattern in text for pattern in normalized_reuse_risks)

    def _satisfies_quality_hint(case: dict[str, Any]) -> bool:
        if not quality_hint_keywords:
            return False
        text = _normalize_match_text(
            " ".join(
                [
                    str(case.get("description") or ""),
                    str(case.get("expected_result") or ""),
                    str(case.get("test_input") or ""),
                    " ".join([str(x) for x in (case.get("steps") or []) if str(x).strip()]) if isinstance(case.get("steps"), list) else "",
                ]
            )
        )
        return any(keyword in text for keyword in quality_hint_keywords)

    def _review_must_keep_reasons(case: dict[str, Any], score_profile: dict[str, Any] | None = None) -> list[str]:
        profile = dict(score_profile or {})
        reasons: list[str] = []

        priority = str(case.get("priority") or "").strip().upper()
        if priority == "P0":
            reasons.append("priority_p0")

        has_coverage_value = bool(
            (profile.get("missing_rule_hits") or [])
            or (profile.get("core_rule_hits") or [])
            or (profile.get("unique_coverage_hits") or [])
        )

        if bool(profile.get("reuse_risk_hit")):
            reasons.append("reuse_risk_hit")

        profile_reasons = {
            str(item).strip().lower() for item in (profile.get("reasons") or []) if str(item).strip()
        }

        if _hit_must_cover_rule(_extract_rule_keys(case), profile):
            reasons.append("must_cover_rule_hit")

        text = _normalize_match_text(
            " ".join(
                [
                    str(case.get("description") or ""),
                    str(case.get("test_module") or ""),
                    str(case.get("expected_result") or ""),
                    str(case.get("test_input") or ""),
                    " ".join([str(x) for x in (case.get("steps") or []) if str(x).strip()])
                    if isinstance(case.get("steps"), list)
                    else "",
                ]
            )
        )
        permission_tokens = (
            "permission", "auth", "authorize", "authorization", "role", "accesscontrol",
            "权限", "鉴权", "授权", "角色", "访问控制", "全局控制",
        )
        report_tokens = (
            "report", "dashboard", "metric", "analytics", "supervisor",
            "报表", "看板", "统计", "指标", "督导端", "督导",
        )
        state_tokens = (
            "state", "status", "transition", "resume", "rollback", "idempotent",
            "状态", "流转", "恢复", "回滚", "幂等", "上下文",
        )
        _ = (text, permission_tokens, report_tokens, state_tokens, has_coverage_value, profile_reasons)

        confirmed_fact_hits: list[str] = []
        raw_hits = case.get("confirmed_fact_hits")
        if isinstance(raw_hits, list):
            confirmed_fact_hits.extend([str(item) for item in raw_hits if str(item).strip()])
        meta = case.get("meta")
        if isinstance(meta, dict):
            meta_hits = meta.get("confirmed_fact_hits")
            if isinstance(meta_hits, list):
                confirmed_fact_hits.extend([str(item) for item in meta_hits if str(item).strip()])
        if confirmed_fact_hits:
            reasons.append("confirmed_fact_hit")

        unique_reasons: list[str] = []
        seen_reasons: set[str] = set()
        for reason in reasons:
            normalized = str(reason or "").strip()
            if not normalized or normalized in seen_reasons:
                continue
            seen_reasons.add(normalized)
            unique_reasons.append(normalized)
        return unique_reasons

    def _normalize_case_structure(case: dict[str, Any]) -> dict[str, Any] | None:
        normalized = dict(case or {})
        module = str(normalized.get("test_module") or "").strip()
        description = str(normalized.get("description") or "").strip()
        expected_result_raw = str(normalized.get("expected_result") or "").strip()
        expected_result = expected_result_raw
        expected_result_alignment_warning = False
        expected_result_quality = "assertable"
        expected_result_quality_reason = ""
        truncated_text_detected = False
        invalid_case_reason = ""
        invalid_case_signals: list[str] = []
        if not module or len(description) < 4:
            return None

        normalized_steps = _normalize_steps(normalized.get("steps"))
        if not normalized_steps:
            return None

        preconditions = normalized.get("preconditions")
        if not isinstance(preconditions, list):
            preconditions = []
        preconditions = [str(item).strip() for item in preconditions if str(item).strip()]
        if not preconditions:
            preconditions = [f"User has logged in and can access module {module}"]

        if not expected_result or _is_placeholder_expected_result(expected_result):
            rebuilt_expected_result = _build_expected_result_from_case(
                module=module,
                description=description,
                normalized_steps=normalized_steps,
            )
            if rebuilt_expected_result:
                expected_result = rebuilt_expected_result
            else:
                expected_result = expected_result_raw
                expected_result_quality = "non_assertable"
                expected_result_quality_reason = "no_concrete_assertion"
        else:
            step_tokens = _semantic_tokenize(" ".join(normalized_steps), limit=18)
            expected_tokens = _semantic_tokenize(expected_result, limit=12)
            if expected_tokens and step_tokens and not step_tokens.intersection(expected_tokens):
                expected_result_alignment_warning = True

        if _looks_truncated_text(expected_result):
            expected_result_quality = "truncated"
            expected_result_quality_reason = "truncated_suffix_detected"
            truncated_text_detected = True
        elif _is_non_assertable_expected_result(expected_result):
            expected_result_quality = "non_assertable"
            if not expected_result_quality_reason:
                expected_result_quality_reason = "template_or_weak_assertion"
        else:
            expected_result_quality = "assertable"
            if not expected_result_quality_reason:
                expected_result_quality_reason = "contains_concrete_assertion"

        test_input = str(normalized.get("test_input") or "").strip()
        if not test_input:
            test_input = _strip_step_prefix(normalized_steps[0]) if normalized_steps else ""
        if not test_input:
            test_input = description or module or "默认输入"

        normalized["steps"] = normalized_steps
        normalized["preconditions"] = preconditions
        leakage_probe = dict(normalized)
        leakage_probe["steps"] = normalized_steps
        leakage_probe["preconditions"] = preconditions
        leakage_probe["test_input"] = test_input
        leakage_probe["expected_result"] = expected_result
        invalid_case_signals = _reasoning_leakage_hits(leakage_probe)
        if invalid_case_signals:
            invalid_case_reason = "reasoning_leakage"
            expected_result_quality = "invalid_case"
            expected_result_quality_reason = "reasoning_leakage"
            truncated_text_detected = False

        normalized["steps"] = normalized_steps
        normalized["preconditions"] = preconditions
        normalized["test_input"] = test_input
        normalized["expected_result"] = expected_result
        normalized["expected_result_quality"] = expected_result_quality
        normalized["expected_result_quality_reason"] = expected_result_quality_reason
        normalized["expected_result_alignment_warning"] = bool(expected_result_alignment_warning)
        normalized["truncated_text_detected"] = bool(truncated_text_detected)
        normalized["case_quality"] = "invalid_case" if invalid_case_reason else "valid_case"
        normalized["invalid_case_reason"] = invalid_case_reason
        normalized["invalid_case_signals"] = invalid_case_signals
        normalized["priority"] = _normalize_priority_value(str(normalized.get("priority") or ""))
        return normalized

    def _enforce_main_path_p0_anchors_legacy_unused(
        cases: list[dict[str, Any]],
        *,
        coverage_mode: str = "",
    ) -> list[dict[str, Any]]:
        candidate_cases = [dict(item) for item in cases if isinstance(item, dict)]
        if str(coverage_mode or "") not in {"expanded_regression", "full_functional_regression"}:
            return candidate_cases
        if any(_normalize_priority_value(str(item.get("priority") or "")) == "P0" for item in candidate_cases):
            return candidate_cases

        strong_tokens = (
            "submit",
            "publish",
            "upload",
            "generate",
            "approve",
            "review pass",
            "permission",
            "member",
            "locked",
            "paywall",
            "result",
            "提交",
            "投稿",
            "发布",
            "上传",
            "生成",
            "批改",
            "审核通过",
            "审核中",
            "权限",
            "会员",
            "锁",
            "结果",
        )
        low_value_tokens = (
            "copy",
            "toast",
            "tooltip",
            "format",
            "layout",
            "复制",
            "提示",
            "文案",
            "样式",
            "格式",
            "入口",
            "空状态",
        )

        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for index, item in enumerate(candidate_cases):
            text = " ".join(
                [
                    str(item.get("test_module") or ""),
                    str(item.get("description") or ""),
                    str(item.get("expected_result") or ""),
                    str(item.get("test_input") or ""),
                    " ".join(str(step) for step in (item.get("steps") or []) if str(step).strip())
                    if isinstance(item.get("steps"), list)
                    else "",
                ]
            ).lower()
            score = 0
            score += 10 * sum(1 for token in strong_tokens if token and token.lower() in text)
            score -= 8 * sum(1 for token in low_value_tokens if token and token.lower() in text)
            if _normalize_priority_value(str(item.get("priority") or "")) == "P1":
                score += 6
            if str(item.get("priority_decision_state") or "").strip().lower() in {"optional", "invalid"}:
                score -= 20
            if score >= 10:
                ranked.append((score, -index, item))

        if not ranked:
            return candidate_cases
        ranked.sort(reverse=True)
        target_count = 2 if str(coverage_mode or "") == "expanded_regression" else 4
        target_count = min(target_count, max(1, int(round(len(candidate_cases) * 0.08))))
        promoted_signatures: set[str] = set()
        output: list[dict[str, Any]] = []
        for item in candidate_cases:
            updated = dict(item)
            output.append(updated)
        for _score, _neg_index, item in ranked:
            if len(promoted_signatures) >= target_count:
                break
            signature = _signature(item)
            if signature in promoted_signatures:
                continue
            promoted_signatures.add(signature)
            for updated in output:
                if _signature(updated) == signature:
                    updated["priority"] = "P0"
                    updated["priority_final"] = "P0"
                    updated["priority_decision_state"] = "overridden"
                    updated["priority_decision_source"] = "main_path_anchor_floor"
                    break
        return output

    def _enforce_main_path_p0_anchors(
        cases: list[dict[str, Any]],
        *,
        coverage_mode: str = "",
        requirement_text: str = "",
    ) -> list[dict[str, Any]]:
        candidate_cases = [dict(item) for item in cases if isinstance(item, dict)]
        mode = str(coverage_mode or "")
        if mode not in {"expanded_regression", "full_functional_regression"}:
            return candidate_cases
        case_count = len(candidate_cases)
        if case_count <= 0:
            return candidate_cases
        if mode == "full_functional_regression":
            if case_count >= 80:
                target_count = min(12, max(8, int((case_count + 9) // 10)))
            elif case_count >= 40:
                target_count = min(10, max(9, int(round(case_count * 0.12))))
            else:
                target_count = min(6, max(3, int(round(case_count * 0.14))))
        else:
            target_count = (
                min(4, max(3, int(round(case_count * 0.06))))
                if case_count >= 50
                else min(3, max(1, int(round(case_count * 0.08))))
            )
        target_count = min(target_count, case_count)
        strong_tokens = (
            "submit",
            "publish",
            "upload",
            "generate",
            "approve",
            "review pass",
            "review approved",
            "approval passed",
            "permission",
            "member",
            "vip",
            "locked",
            "paywall",
            "result",
            "first lesson",
            "all courses",
            "successfully generated",
            "generated result",
            "correction result",
            "review result",
            "four modules",
            "feedback modules",
            "result details",
            "submit success",
            "approval state",
            "detail page",
            "提交",
            "投稿",
            "发布",
            "上传",
            "生成",
            "批改",
            "审核通过",
            "审核中",
            "权限",
            "会员",
            "锁定",
            "结果",
            "第一课",
            "试学",
            "普通用户",
            "非会员",
            "全部课程",
            "成功生成",
            "生成批改结果",
            "批改结果展示",
            "四大模块",
            "提交成功",
            "进入审核中",
            "审核通过后",
            "作品详情",
        )
        low_value_tokens = (
            "copy",
            "toast",
            "tooltip",
            "popup",
            "modal",
            "dialog",
            "badge",
            "status badge",
            "record limit",
            "max records",
            "maximum records",
            "format",
            "layout",
            "sort",
            "sorting",
            "rank",
            "ranking",
            "share",
            "h5",
            "category",
            "tab",
            "pdf",
            "download",
            "image preview",
            "large image",
            "photo preview",
            "drag",
            "drag sort",
            "reorder",
            "delete image",
            "remove image",
            "force close",
            "kill app",
            "48h",
            "48 hours",
            "zero images",
            "0 images",
            "no images",
            "disabled button",
            "button disabled",
            "remaining count",
            "quota decrement",
            "star rating",
            "countdown",
            "title body",
            "editable title",
            "my list",
            "复制",
            "提示",
            "弹窗",
            "弹层",
            "规则弹窗",
            "状态标识",
            "标识",
            "最多20条",
            "上限",
            "文案",
            "样式",
            "格式",
            "入口",
            "空状态",
            "排序",
            "置顶",
            "分类",
            "分享",
            "下载",
            "拖动",
            "拖拽",
            "排序",
            "删除图片",
            "删除缩略图",
            "强杀",
            "强制退出",
            "48小时",
            "大图",
            "照片大图",
            "预览",
            "序号",
            "榜单",
            "0张",
            "无图片",
            "按钮不可点",
            "按钮不可用",
            "剩余次数",
            "次数递减",
            "星星评分",
            "倒计时",
            "标题正文",
            "可编辑",
            "我的列表",
            "分句点评",
            "划线句子",
            "点评跳转",
            "sentence comment",
            "underlined sentence",
            "comment jump",
        )
        anchor_families = (
            ("submission", ("submit", "publish", "提交", "投稿", "发布")),
            ("result_display", ("four modules", "feedback modules", "result details", "四大模块", "四部分", "完整展示")),
            ("generation_result", ("generate", "result", "upload", "生成", "结果", "批改", "上传")),
            ("approval", ("approve", "review approved", "approval passed", "审核通过", "审核中")),
            ("permission", ("permission", "member", "vip", "locked", "paywall", "first lesson", "权限", "会员", "锁定", "第一课", "试学")),
            ("community_detail", ("detail page", "review approved", "作品详情", "审核通过后")),
        )
        critical_anchor_families = (
            ("generation_result", ("上传", "去批改", "生成", "批改结果")),
            ("result_display", ("批改反馈", "四部分", "完整展示", "综合点评", "全文润色", "优化建议")),
            ("submission", ("投稿", "提交成功", "审核中")),
            ("submission", ("投稿成功", "审核中")),
            ("cross_module_state", ("批改", "投稿", "已发布", "作文圈")),
            ("approval", ("审核通过", "已发布", "作文圈", "可见")),
            ("approval", ("审核通过", "作文圈")),
            ("approval", ("已发布", "作文圈")),
            ("free_first_lesson", ("普通用户", "第一课", "试学")),
            ("free_first_lesson", ("普通用户", "第一课", "免费")),
            ("locked_member_courses", ("普通用户", "非第一课", "会员中心")),
            ("locked_member_courses", ("其余课程", "会员中心")),
            ("member_all_courses", ("会员", "全部课程", "可学")),
            ("member_all_courses", ("会员", "全部课程")),
            ("delete_restore", ("删除", "已发布", "恢复未投稿")),
            ("delete_restore", ("删除作品", "未投稿")),
        )

        def _anchor_family(text: str) -> str:
            for family, tokens in anchor_families:
                if any(token and token.lower() in text for token in tokens):
                    return family
            return "general"

        def _case_anchor_text(item: dict[str, Any]) -> str:
            return p0_case_anchor_text(item)

        def _has_strong_anchor(text: str) -> bool:
            return any(token and token.lower() in text for token in strong_tokens) or p0_has_core_signal(text)

        def _critical_anchor_family(text: str) -> str:
            for family, tokens in critical_anchor_families:
                if all(token and token.lower() in text for token in tokens):
                    return family
            return p0_configured_anchor_family(
                text,
                requirement_text=str(requirement_text or ""),
                course_only_when_non_essay=False,
            )

        def _has_critical_anchor(text: str) -> bool:
            return bool(_critical_anchor_family(text))

        def _has_low_value_anchor(text: str) -> bool:
            return any(token and token.lower() in text for token in low_value_tokens) or p0_has_low_value_signal(text)

        def _has_non_blocking_detail_anchor(text: str) -> bool:
            detail_tokens = (
                "分句点评",
                "划线句子",
                "点评跳转",
                "最多20条",
                "0张",
                "无图片",
                "按钮不可点",
                "按钮不可用",
                "剩余次数",
                "次数递减",
                "星星评分",
                "倒计时",
                "标题正文",
                "可编辑",
                "我的列表",
                "sentence comment",
                "underlined sentence",
                "comment jump",
                "max 20",
            )
            return any(token and token.lower() in text for token in detail_tokens)

        def _has_blocking_anchor(text: str) -> bool:
            generation_terms = (
                "generate",
                "generated",
                "correction result",
                "review result",
                "four modules",
                "feedback modules",
                "result details",
                "successfully generated",
                "生成",
                "生成批改结果",
                "批改结果",
                "批改结果展示",
                "四大模块",
                "四部分",
                "完整展示",
            )
            submit_terms = (
                "submit success",
                "submitted successfully",
                "enters pending review",
                "提交成功",
                "投稿成功",
                "进入审核中",
                "状态变为审核中",
            )
            approval_terms = (
                "approval passed",
                "review approved",
                "approved work",
                "visible in community",
                "community detail",
                "审核通过",
                "审核通过后",
                "作文圈可见",
                "他人可见",
                "作品详情",
            )
            permission_terms = (
                "permission",
                "member all courses",
                "all courses",
                "first lesson",
                "locked",
                "paywall",
                "vip",
                "权限",
                "普通用户",
                "第一课",
                "试学",
                "非第一课",
                "锁课",
                "锁定",
                "跳会员",
                "会员用户",
                "全部课程",
            )
            return any(token and token.lower() in text for token in generation_terms + submit_terms + approval_terms + permission_terms)

        for item in candidate_cases:
            if _normalize_priority_value(str(item.get("priority") or "")) != "P0":
                continue
            if p0_cross_domain_essay_case(item, requirement_text=str(requirement_text or "")):
                item["priority"] = "P1"
                item["priority_final"] = "P1"
                item["priority_decision_state"] = "overridden"
                item["priority_decision_source"] = "main_path_anchor_demoted_domain_mismatch"
                continue
            text = _case_anchor_text(item)
            if (not _has_critical_anchor(text)) and (_has_non_blocking_detail_anchor(text) or (
                _has_low_value_anchor(text) and not _has_blocking_anchor(text)
            )):
                item["priority"] = "P1"
                item["priority_final"] = "P1"
                item["priority_decision_state"] = "overridden"
                item["priority_decision_source"] = "main_path_anchor_demoted_non_blocking"

        existing_p0_signatures = {
            _signature(item)
            for item in candidate_cases
            if _normalize_priority_value(str(item.get("priority") or "")) == "P0"
        }
        if len(existing_p0_signatures) >= target_count:
            return candidate_cases

        ranked: list[tuple[int, int, str, dict[str, Any]]] = []
        for index, item in enumerate(candidate_cases):
            if _signature(item) in existing_p0_signatures:
                continue
            if p0_cross_domain_essay_case(item, requirement_text=str(requirement_text or "")):
                continue
            text = _case_anchor_text(item)
            score = 0
            score += 10 * sum(1 for token in strong_tokens if token and token.lower() in text)
            score -= 12 * sum(1 for token in low_value_tokens if token and token.lower() in text)
            if _normalize_priority_value(str(item.get("priority") or "")) == "P1":
                score += 6
            critical_family = _critical_anchor_family(text)
            if critical_family:
                score += 70
            if (not critical_family) and (_has_non_blocking_detail_anchor(text) or (
                _has_low_value_anchor(text) and not _has_blocking_anchor(text)
            )):
                score -= 40
            if str(item.get("priority_decision_state") or "").strip().lower() in {"optional", "invalid"}:
                score -= 20
            try:
                score -= 4 * int((case_complexity_profile(item) or {}).get("complexity_score") or 0)
            except Exception:
                pass
            if score >= 10:
                ranked.append((score, -index, critical_family or _anchor_family(text), item))

        if len(ranked) < max(1, target_count - len(existing_p0_signatures)):
            ranked_signatures = {_signature(item) for _score, _neg_index, _family, item in ranked}
            for index, item in enumerate(candidate_cases):
                signature = _signature(item)
                if signature in existing_p0_signatures or signature in ranked_signatures:
                    continue
                if p0_cross_domain_essay_case(item, requirement_text=str(requirement_text or "")):
                    continue
                text = _case_anchor_text(item)
                critical_family = _critical_anchor_family(text)
                if (not critical_family) and (_has_non_blocking_detail_anchor(text) or (
                    _has_low_value_anchor(text) and not _has_blocking_anchor(text)
                )):
                    continue
                if mode == "full_functional_regression" and not (_has_strong_anchor(text) or critical_family):
                    continue
                normalized_priority = _normalize_priority_value(str(item.get("priority") or ""))
                priority_bonus = 8 if normalized_priority == "P1" else 3 if normalized_priority == "P2" else 0
                try:
                    complexity_penalty = 4 * int((case_complexity_profile(item) or {}).get("complexity_score") or 0)
                except Exception:
                    complexity_penalty = 0
                fallback_score = priority_bonus + (60 if critical_family else 0) - complexity_penalty
                if fallback_score >= 3:
                    ranked.append((fallback_score, -index, critical_family or _anchor_family(text), item))
                    ranked_signatures.add(signature)

        if not ranked:
            return candidate_cases
        ranked.sort(reverse=True)
        promoted_signatures: set[str] = set(existing_p0_signatures)
        promoted_families: set[str] = set()
        output = [dict(item) for item in candidate_cases]
        for _score, _neg_index, family, item in ranked:
            if len(promoted_signatures) >= target_count:
                break
            if family in promoted_families and family != "general":
                continue
            signature = _signature(item)
            if signature in promoted_signatures:
                continue
            promoted_signatures.add(signature)
            promoted_families.add(family)
            for updated in output:
                if _signature(updated) == signature:
                    updated["priority"] = "P0"
                    updated["priority_final"] = "P0"
                    updated["priority_decision_state"] = "overridden"
                    updated["priority_decision_source"] = "main_path_anchor_floor"
                    break
        if len(promoted_signatures) < target_count:
            for _score, _neg_index, _family, item in ranked:
                if len(promoted_signatures) >= target_count:
                    break
                signature = _signature(item)
                if signature in promoted_signatures:
                    continue
                promoted_signatures.add(signature)
                for updated in output:
                    if _signature(updated) == signature:
                        updated["priority"] = "P0"
                        updated["priority_final"] = "P0"
                        updated["priority_decision_state"] = "overridden"
                        updated["priority_decision_source"] = "main_path_anchor_floor"
                        break
        return output

    def _apply_execution_plan_metadata(
        cases: list[dict[str, Any]],
        *,
        start_id: int = 1,
        coverage_mode: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        candidate_cases = [dict(item) for item in cases if isinstance(item, dict)]
        if not candidate_cases:
            return [], {
                "applied": False,
                "linear_executable": False,
                "main_chain_case_count": 0,
                "independent_case_count": 0,
                "isolation_case_count": 0,
                "role_switch_count": 0,
                "broken_dependency_count": 0,
                "state_conflict_count": 0,
            }

        def _case_text(item: dict[str, Any]) -> str:
            return " ".join(
                [
                    str(item.get("test_module") or ""),
                    str(item.get("description") or ""),
                    str(item.get("expected_result") or ""),
                    str(item.get("test_input") or ""),
                    " ".join(str(step) for step in (item.get("steps") or []) if str(step).strip())
                    if isinstance(item.get("steps"), list)
                    else "",
                ]
            ).lower()

        def _all(text: str, tokens: tuple[str, ...]) -> bool:
            return all(token and token.lower() in text for token in tokens)

        def _any(text: str, tokens: tuple[str, ...]) -> bool:
            return any(token and token.lower() in text for token in tokens)

        def _token_hit(text: str, tokens: tuple[str, ...]) -> bool:
            haystack = str(text or "").strip().lower()
            if not haystack:
                return False
            for token in tokens:
                needle = str(token or "").strip().lower()
                if not needle:
                    continue
                if needle.isascii() and re.search(r"[a-z0-9]", needle):
                    if re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", haystack):
                        return True
                    continue
                if needle in haystack:
                    return True
            return False

        def _priority_rank(item: dict[str, Any]) -> int:
            priority = str(item.get("priority") or "").strip().upper()
            return {"P0": 30, "P1": 15, "P2": 0}.get(priority, 0)

        workflow_stage_meta_by_key: dict[str, dict[str, Any]] = {}
        workflow_stage_output_state: dict[str, str] = {}
        plan_workflow_blueprints = list(workflow_blueprints)

        def _stage_match_patterns(step: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
            raw_keywords: list[str] = []
            for key in ("match_keywords", "keywords", "aliases"):
                value = step.get(key)
                if isinstance(value, list):
                    raw_keywords.extend(str(item).strip() for item in value if str(item).strip())
            if bool(step.get("allow_bridge")) and raw_keywords:
                return tuple((keyword.lower(),) for keyword in raw_keywords if str(keyword or "").strip())
            for key in ("label", "action", "module", "assertion", "state_in", "state_out"):
                value = str(step.get(key) or "").strip()
                if value:
                    raw_keywords.append(value)
            patterns: list[tuple[str, ...]] = []
            for keyword in raw_keywords:
                compact = str(keyword or "").strip().lower()
                if compact:
                    patterns.append((compact,))
            return tuple(patterns)

        def _main_chain_stages_from_blueprints() -> list[tuple[str, str, tuple[tuple[str, ...], ...]]]:
            stages: list[tuple[str, str, tuple[tuple[str, ...], ...]]] = []
            for blueprint_index, blueprint in enumerate(plan_workflow_blueprints[:3], start=1):
                steps = [step for step in (blueprint.get("steps") or []) if isinstance(step, dict)]
                if len(steps) < 2:
                    continue
                for step_index, step in enumerate(steps[:12], start=1):
                    stage_key = str(step.get("id") or f"bp{blueprint_index}_step_{step_index:03d}").strip()
                    stage_label = str(
                        step.get("label")
                        or step.get("action")
                        or step.get("description")
                        or stage_key
                    ).strip()
                    patterns = _stage_match_patterns(step)
                    if not stage_key or not stage_label or not patterns:
                        continue
                    workflow_stage_meta_by_key[stage_key] = {
                        **step,
                        "blueprint_id": str(blueprint.get("id") or f"blueprint_{blueprint_index}"),
                        "blueprint_name": str(blueprint.get("name") or blueprint.get("title") or "workflow_blueprint"),
                        "step_index": int(step_index),
                    }
                    state_out = str(step.get("state_out") or "").strip()
                    if state_out:
                        workflow_stage_output_state[stage_key] = state_out
                    stages.append((stage_key, stage_label, patterns))
                if stages:
                    break
            return stages

        low_chain_tokens = (
            "失败",
            "异常",
            "超时",
            "错误",
            "拒绝",
            "不通过",
            "不可点击",
            "置灰",
            "空状态",
            "无数据",
            "上限",
            "下限",
            "格式",
            "大小",
            "边界",
            "failure",
            "failed",
            "timeout",
            "error",
            "invalid",
            "empty",
            "limit",
            "boundary",
        )

        analytics_tokens = (
            "埋点",
            "上报",
            "曝光",
            "停留时间",
            "pv",
            "uv",
            "tracking",
            "analytics",
            "event",
        )

        destructive_action_tokens = (
            "删除",
            "下架",
            "撤销",
            "作废",
            "取消发布",
            "delete",
            "remove",
            "unpublish",
            "archive",
            "deactivate",
        )
        blocking_negative_tokens = (
            "失败",
            "异常",
            "超时",
            "错误",
            "拒绝",
            "不通过",
            "不可点击",
            "不可操作",
            "置灰",
            "阻止",
            "无法",
            "不能",
            "不允许",
            "不进入",
            "不生成",
            "不保存",
            "failure",
            "failed",
            "timeout",
            "error",
            "invalid",
            "blocked",
            "cannot",
            "not allowed",
            "not saved",
        )
        boundary_capacity_tokens = (
            "边界",
            "上限",
            "下限",
            "最多",
            "最少",
            "容量不足",
            "学不完",
            "课程设置过少",
            "时间冲突",
            "冲突",
            "boundary",
            "limit",
            "capacity",
            "conflict",
            "too few",
            "too many",
        )
        display_only_tokens = (
            "文案",
            "样式",
            "布局",
            "标题",
            "排序",
            "筛选",
            "列表",
            "卡片",
            "弹窗",
            "copy",
            "style",
            "layout",
            "title",
            "sorting",
            "filter",
            "list",
            "card",
            "popup",
        )
        downstream_visibility_tokens = (
            "新增",
            "新计划",
            "同步",
            "生效",
            "最新",
            "进度更新",
            "状态同步",
            "new",
            "created",
            "sync",
            "synced",
            "visible",
            "effective",
            "latest",
            "updated",
        )
        main_chain_excluded_candidates: list[dict[str, str]] = []
        main_chain_incomplete_reason = ""
        main_chain_incomplete_reason_holder = {"reason": ""}
        derived_workflow_debug: dict[str, Any] = {
            "candidate_total": int(len(candidate_cases)),
            "action_state_candidate_count": 0,
            "primary_candidate_count": 0,
            "fallback_candidate_count": 0,
            "selected_candidate_count": 0,
            "closure_reason": "",
        }

        def _record_main_chain_exclusion(item: dict[str, Any], reason: str, *, stage_key: str = "") -> None:
            if not reason:
                return
            signature = _signature(item)
            if any(
                entry.get("signature") == signature and entry.get("reason") == reason
                for entry in main_chain_excluded_candidates
            ):
                return
            main_chain_excluded_candidates.append(
                {
                    "case_id": str(item.get("id") or "")[:40],
                    "description": str(item.get("description") or "")[:160],
                    "stage_key": str(stage_key or "")[:80],
                    "reason": str(reason),
                    "signature": signature,
                }
            )

        def _is_display_only_text(text: str) -> bool:
            if not _any(text, display_only_tokens):
                return False
            if _any(text, downstream_visibility_tokens):
                return False
            workflow_action_tokens = (
                "新增",
                "创建",
                "添加",
                "选择",
                "设置",
                "预览",
                "保存",
                "提交",
                "确认",
                "跳转",
                "进入",
                "create",
                "add",
                "select",
                "set",
                "preview",
                "save",
                "submit",
                "confirm",
                "click",
                "open",
                "view",
                "learn",
                "navigate",
                "enter",
            )
            return not _any(text, workflow_action_tokens)

        def _workflow_transition_for_case(
            item: dict[str, Any],
            *,
            stage_key: str = "",
            stage_label: str = "",
        ) -> dict[str, Any]:
            step_meta = workflow_stage_meta_by_key.get(stage_key) or {}
            text = " ".join(
                [
                    _case_text(item),
                    str(stage_label or ""),
                    str(step_meta.get("label") or ""),
                    str(step_meta.get("action") or ""),
                    str(step_meta.get("assertion") or ""),
                    str(step_meta.get("state_in") or ""),
                    str(step_meta.get("state_out") or ""),
                ]
            )
            destructive = bool(_any(text, destructive_action_tokens))
            blocking = bool(
                _any(text, blocking_negative_tokens)
                or _any(text, boundary_capacity_tokens)
                or _any(text, analytics_tokens)
            )
            stage_kind = str(step_meta.get("stage_kind") or "").strip().lower() or _workflow_stage_kind_from_text(text)
            source_state = str(step_meta.get("state_in") or "").strip()
            target_state = str(step_meta.get("state_out") or "").strip()
            if not source_state:
                phase = _workflow_phase(text)
                source_state = {
                    10: "entry_ready",
                    20: "workflow_started",
                    30: "workflow_edit_ready",
                    50: "workflow_configured",
                    60: "workflow_ready_to_commit",
                    70: "committed",
                    80: "downstream_visible",
                }.get(phase, "prepared")
            if not target_state:
                target_state = {
                    "entry": "workflow_entered",
                    "configure": "workflow_configured",
                    "preview": "workflow_preview_ready",
                    "commit": "workflow_committed",
                    "downstream_visibility": "downstream_visible",
                    "consume": "workflow_consumed",
                    "completion_sync": "completion_synced",
                }.get(stage_kind, "prepared")
            path_type = "positive" if not (blocking or destructive) else "negative"
            can_advance = bool(path_type == "positive" and stage_kind != "unknown")
            workflow_id = str(step_meta.get("workflow_id") or step_meta.get("blueprint_id") or "").strip()
            transition_confidence = 0.9 if workflow_blueprints else 0.35
            return {
                "workflow_id": workflow_id,
                "source_state": source_state,
                "action": str(step_meta.get("action") or stage_label or item.get("description") or "").strip()[:160],
                "target_state": target_state,
                "path_type": path_type,
                "blocking": bool(blocking),
                "destructive": bool(destructive),
                "can_advance_main_flow": bool(can_advance),
                "state_transition_confidence": float(transition_confidence),
                "stage_kind": stage_kind,
            }

        def _main_chain_exclusion_reason(item: dict[str, Any], *, stage_key: str = "", stage_label: str = "") -> str:
            text = _case_text(item)
            if not text:
                return "empty_text"
            if _reasoning_leakage_hits(item):
                return "reasoning_leakage"
            if _any(text, analytics_tokens):
                return "analytics"
            if _any(text, destructive_action_tokens):
                return "destructive_action"
            if _any(text, boundary_capacity_tokens):
                return "boundary_capacity"
            if _any(text, blocking_negative_tokens):
                return "blocking_negative"
            if _is_display_only_text(text):
                return "display_only"
            transition = _workflow_transition_for_case(item, stage_key=stage_key, stage_label=stage_label)
            if not bool(transition.get("can_advance_main_flow")):
                return "non_advancing_transition"
            return ""

        def _workflow_stage_kind_from_text(text: str) -> str:
            lowered = str(text or "").lower()
            if _token_hit(lowered, ("保存", "提交", "确认", "发布", "save", "submit", "commit", "confirm", "publish")):
                return "commit"
            if _token_hit(lowered, ("同步", "生效", "展示", "显示", "刷新", "最新", "sync", "display", "show", "visible", "effective", "latest", "reflect", "reflects", "reflected", "downstream")):
                return "downstream_visibility"
            if _token_hit(lowered, ("入口", "工作流入口", "进入入口", "entry", "workflow entry")):
                return "entry"
            if _token_hit(lowered, ("点击", "跳转", "学习", "查看", "打开", "click", "navigate", "learn", "view", "open")):
                return "consume"
            if _token_hit(lowered, ("预览", "检查", "确认前", "preview", "review")):
                return "preview"
            if _token_hit(lowered, ("新增", "创建", "添加", "选择", "设置", "配置", "编辑", "修改", "create", "add", "select", "set", "configure", "edit", "modify")):
                return "configure"
            if _token_hit(lowered, ("进入", "访问", "打开", "enter", "access", "open")):
                return "entry"
            if _token_hit(lowered, ("完成", "进度", "状态", "complete", "completion", "progress", "status")):
                return "completion_sync"
            return "unknown"

        def _main_chain_closure_status(
            selected: list[tuple[str, str, dict[str, Any]]],
            *,
            source: str,
        ) -> tuple[bool, str, list[str]]:
            stage_kinds: list[str] = []
            for stage_key, stage_label, item in selected:
                meta = workflow_stage_meta_by_key.get(stage_key) or {}
                text = " ".join(
                    [
                        _case_text(item),
                        str(stage_label or ""),
                        str(meta.get("label") or ""),
                        str(meta.get("action") or ""),
                        str(meta.get("assertion") or ""),
                        str(meta.get("state_out") or ""),
                    ]
                )
                explicit_stage_kind = str(meta.get("stage_kind") or "").strip().lower()
                stage_kinds.append(explicit_stage_kind or _workflow_stage_kind_from_text(text))
            if len(stage_kinds) < 2:
                return False, "main_chain_too_short", stage_kinds
            has_commit = "commit" in stage_kinds
            has_downstream = any(kind in {"downstream_visibility", "consume", "completion_sync"} for kind in stage_kinds)
            has_configure = any(kind in {"entry", "configure", "preview"} for kind in stage_kinds)
            if not has_commit:
                return False, "missing_commit_success_step", stage_kinds
            if not has_downstream:
                return False, "missing_downstream_visibility_or_consume_step", stage_kinds
            if source == "current_generation_cases" and not has_configure:
                return False, "missing_configure_or_entry_step", stage_kinds
            return True, "", stage_kinds

        def _selected_stage_state_conflicts(
            selected: list[tuple[str, str, dict[str, Any]]],
        ) -> list[dict[str, Any]]:
            conflicts: list[dict[str, Any]] = []
            previous_stage_key = ""
            previous_case_id = ""
            previous_target_state = ""
            for stage_key, _stage_label, item in selected:
                step_meta = workflow_stage_meta_by_key.get(stage_key) or {}
                source_state = str(step_meta.get("state_in") or "").strip()
                target_state = str(step_meta.get("state_out") or "").strip()
                case_id = str(item.get("id") or "").strip()
                if previous_target_state and source_state and previous_target_state != source_state:
                    conflicts.append(
                        {
                            "prev_stage_key": previous_stage_key,
                            "curr_stage_key": str(stage_key),
                            "prev_case_id": previous_case_id,
                            "curr_case_id": case_id,
                            "prev_target_state": previous_target_state,
                            "curr_source_state": source_state,
                            "reason": "state_not_connected",
                        }
                    )
                previous_stage_key = str(stage_key)
                previous_case_id = case_id
                previous_target_state = target_state
            return conflicts

        def _workflow_phase(text: str) -> int:
            lowered = str(text or "").lower()
            if _any(lowered, ("打开", "进入", "访问", "入口", "open", "enter", "entry")):
                return 10
            if _any(lowered, ("新增", "创建", "添加", "选择", "选课", "设置", "配置", "准备", "create", "add", "select", "set", "prepare", "prepared", "ready")):
                return 20
            if _any(lowered, ("编辑", "修改", "调整", "update", "edit", "modify")):
                return 30
            if _any(lowered, ("预览", "检查", "确认前", "preview", "review")):
                return 50
            if _token_hit(lowered, ("保存", "提交", "确认", "发布", "下架", "删除", "save", "submit", "commit", "confirm", "publish", "delete")):
                return 60
            if _token_hit(lowered, ("同步", "展示", "显示", "刷新", "生效", "sync", "display", "show", "effective", "visible", "reflect", "reflects", "reflected", "downstream")):
                return 70
            if _any(lowered, ("点击", "跳转", "学习", "查看", "click", "navigate", "learn", "view")):
                return 80
            return 90

        def _infer_actor_from_text(text: str) -> str:
            lowered = str(text or "").lower()
            if _any(lowered, ("admin", "administrator", "后台", "管理员", "审核员", "运营")):
                return "admin"
            student_surface = _any(
                lowered,
                (
                    "学生端",
                    "学员端",
                    "书房",
                    "首页",
                    "学习计划页",
                    "学习按钮",
                    "本周任务",
                    "本周进度",
                    "student",
                ),
            )
            management_action = _any(
                lowered,
                (
                    "supervisor",
                    "teacher",
                    "mentor",
                    "督导",
                    "老师",
                    "教师",
                    "教练",
                    "辅导员",
                    "管理",
                    "配置",
                    "新增",
                    "编辑",
                    "下架",
                    "删除",
                    "保存",
                    "课堂管理",
                    "课程管理",
                    "学员信息",
                    "排课",
                ),
            )
            if management_action and not student_surface:
                return "supervisor"
            if _any(lowered, ("会员用户", "会员", "vip", "member")) and not _any(lowered, ("非会员", "普通用户")):
                return "member"
            if _any(lowered, ("普通用户", "非会员", "未订阅", "未付费")):
                return "student_free"
            return "student"

        def _derive_workflow_blueprint_from_current_cases(cases_for_plan: list[dict[str, Any]]) -> dict[str, Any] | None:
            action_tokens = (
                "新增",
                "创建",
                "添加",
                "选择",
                "设置",
                "编辑",
                "修改",
                "准备",
                "准备好",
                "预览",
                "保存",
                "提交",
                "提交成功",
                "提交后",
                "确认",
                "发布",
                "下架",
                "删除",
                "同步",
                "生效",
                "进入",
                "进入页面",
                "入口",
                "跳转",
                "点击",
                "学习",
                "查看",
                "打开",
                "create",
                "add",
                "select",
                "set",
                "edit",
                "preview",
                "save",
                "submit",
                "commit",
                "committed",
                "confirm",
                "sync",
                "navigate",
                "click",
                "open",
                "entry",
                "prepare",
                "prepared",
                "reflect",
                "reflects",
                "downstream",
            )
            state_tokens = (
                "成功",
                "完成",
                "正确",
                "一致",
                "保存",
                "已保存",
                "保存成功",
                "加入",
                "回到",
                "跳转",
                "更新",
                "展示",
                "显示",
                "进入",
                "准备好",
                "准备完成",
                "生效",
                "已生效",
                "success",
                "completed",
                "successfully",
                "updated",
                "saved",
                "visible",
                "ready",
                "prepared",
                "reflected",
                "shown",
            )
            primary_candidates: list[tuple[int, int, int, dict[str, Any]]] = []
            fallback_candidates: list[tuple[int, int, int, dict[str, Any]]] = []
            for index, item in enumerate(cases_for_plan):
                text = _case_text(item)
                exclusion_reason = _main_chain_exclusion_reason(item)
                if exclusion_reason:
                    _record_main_chain_exclusion(item, exclusion_reason)
                    continue
                action_score = sum(1 for token in action_tokens if token.lower() in text)
                state_score = sum(1 for token in state_tokens if token.lower() in text)
                if action_score <= 0 or state_score <= 0:
                    continue
                score = _priority_rank(item) + action_score * 10 + state_score * 5
                if _any(text, ("边界", "上限", "下限", "空状态", "无数据", "boundary", "limit", "empty")):
                    score -= 20
                if _any(text, ("按钮展示", "文案", "样式", "排序", "筛选", "列表", "display only")):
                    score -= 10
                if score < 15:
                    continue
                bucket = primary_candidates if _priority_rank(item) > 0 else fallback_candidates
                bucket.append((score, _workflow_phase(text), index, item))
            derived_workflow_debug["action_state_candidate_count"] = int(
                len(primary_candidates) + len(fallback_candidates)
            )
            derived_workflow_debug["primary_candidate_count"] = int(len(primary_candidates))
            derived_workflow_debug["fallback_candidate_count"] = int(len(fallback_candidates))
            scored = primary_candidates if len(primary_candidates) >= 2 else fallback_candidates
            if len(scored) < 2:
                derived_workflow_debug["closure_reason"] = "insufficient_action_state_candidates"
                return None
            scored.sort(key=lambda row: (-row[0], row[1], row[2]))
            selected = sorted(scored[:10], key=lambda row: (row[1], row[2]))
            derived_workflow_debug["selected_candidate_count"] = int(len(selected))
            steps: list[dict[str, Any]] = []
            previous_state = "initial"
            for step_index, (_score, _phase, _index, item) in enumerate(selected, start=1):
                description = str(item.get("description") or item.get("test_module") or f"workflow step {step_index}").strip()
                module = str(item.get("test_module") or "").strip()
                expected = str(item.get("expected_result") or "").strip()
                step_texts = item.get("steps") if isinstance(item.get("steps"), list) else []
                first_step = next((str(step).strip() for step in step_texts if str(step).strip()), "")
                state_out = f"derived_state_{step_index:03d}"
                match_keywords = [
                    value[:120]
                    for value in (description, module, expected, first_step)
                    if str(value or "").strip()
                ]
                steps.append(
                    {
                        "id": f"derived_step_{step_index:03d}",
                        "label": description[:160],
                        "module": module[:80],
                        "actor": _infer_actor_from_text(_case_text(item)),
                        "action": description[:160],
                        "state_in": previous_state,
                        "state_out": state_out,
                        "assertion": expected[:240],
                        "test_steps": step_texts,
                        "match_keywords": list(dict.fromkeys(match_keywords))[:6],
                        "source_case_id": str(item.get("id") or "").strip(),
                        "main_path_step": True,
                        "allow_bridge": False,
                    }
                )
                previous_state = state_out
            if len(steps) < 2:
                return None
            selected_for_closure = [
                (
                    str(step.get("id") or ""),
                    str(step.get("label") or ""),
                    next(
                        (
                            item
                            for _score, _phase, _index, item in selected
                            if str(item.get("id") or "") == str(step.get("source_case_id") or "")
                        ),
                        {},
                    ),
                )
                for step in steps
            ]
            ok, reason, _stage_kinds = _main_chain_closure_status(
                selected_for_closure,
                source="current_generation_cases",
            )
            if not ok:
                derived_workflow_debug["closure_reason"] = str(reason or "")
                has_commit = "commit" in _stage_kinds
                has_downstream = any(kind in {"downstream_visibility", "consume", "completion_sync"} for kind in _stage_kinds)
                if reason != "missing_configure_or_entry_step" or not (has_commit and has_downstream):
                    main_chain_incomplete_reason_holder["reason"] = reason
                    return None
                first_item = selected[0][3] if selected else {}
                bridge_out = "workflow_entry_ready"
                bridge_step = {
                    "id": "derived_entry_bridge",
                    "label": "Enter workflow entry and prepare valid starting state",
                    "module": str(first_item.get("test_module") or "workflow_setup")[:80],
                    "actor": _infer_actor_from_text(_case_text(first_item)),
                    "action": "Enter workflow entry and prepare valid starting state",
                    "state_in": "initial",
                    "state_out": bridge_out,
                    "assertion": "workflow entry is ready for the next positive state transition",
                    "test_steps": ["Enter the workflow entry with a valid account and prepare the starting data state"],
                    "match_keywords": ["__generic_workflow_entry_bridge__"],
                    "source_case_id": "",
                    "main_path_step": True,
                    "allow_bridge": True,
                }
                bridged_steps = [bridge_step]
                previous_state = bridge_out
                for step in steps:
                    updated_step = dict(step)
                    updated_step["state_in"] = previous_state
                    previous_state = str(updated_step.get("state_out") or previous_state)
                    bridged_steps.append(updated_step)
                bridged_selected_for_closure = [
                    (
                        str(step.get("id") or ""),
                        str(step.get("label") or ""),
                        next(
                            (
                                item
                                for _score, _phase, _index, item in selected
                                if str(item.get("id") or "") == str(step.get("source_case_id") or "")
                            ),
                            {},
                        ),
                    )
                    for step in bridged_steps
                ]
                bridge_ok, bridge_reason, _bridge_stage_kinds = _main_chain_closure_status(
                    bridged_selected_for_closure,
                    source="current_generation_cases",
                )
                if not bridge_ok:
                    derived_workflow_debug["closure_reason"] = str(bridge_reason or reason or "")
                    main_chain_incomplete_reason_holder["reason"] = bridge_reason or reason
                    return None
                steps = bridged_steps
                main_chain_incomplete_reason_holder["reason"] = ""
                derived_workflow_debug["closure_reason"] = "entry_bridge_added"
            return {
                "id": "derived_current_generation_workflow",
                "name": "current generation derived workflow",
                "source": "current_generation_cases",
                "steps": steps,
                "terminal_state": previous_state,
            }

        if not plan_workflow_blueprints:
            derived_blueprint = _derive_workflow_blueprint_from_current_cases(candidate_cases)
            if main_chain_incomplete_reason_holder.get("reason"):
                main_chain_incomplete_reason = str(main_chain_incomplete_reason_holder.get("reason") or "")
            if derived_blueprint is not None:
                plan_workflow_blueprints = [derived_blueprint]

        main_chain_stages = _main_chain_stages_from_blueprints()

        def _pattern_match_score(text: str, patterns: tuple[tuple[str, ...], ...]) -> int:
            best = 0
            for pattern in patterns:
                tokens = [str(token or "").strip().lower() for token in pattern if str(token or "").strip()]
                if not tokens:
                    continue
                if all(token in text for token in tokens):
                    best = max(best, sum(len(token) for token in tokens))
                    continue
                if len(tokens) == 1:
                    parts = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{3,}", tokens[0])
                    if parts and all(part in text for part in parts[:6]):
                        best = max(best, sum(len(part) for part in parts[:6]))
            return best

        selected_by_stage: list[tuple[str, str, dict[str, Any]]] = []
        selected_signatures: set[str] = set()
        strict_blueprint_semantic_filter = bool(workflow_blueprints)
        for stage_key, stage_label, patterns in main_chain_stages:
            ranked: list[tuple[int, int, dict[str, Any]]] = []
            for index, item in enumerate(candidate_cases):
                signature = _signature(item)
                if not signature or signature in selected_signatures:
                    continue
                text = _case_text(item)
                match_score = _pattern_match_score(text, patterns)
                if match_score <= 0:
                    continue
                exclusion_reason = _main_chain_exclusion_reason(item, stage_key=stage_key, stage_label=stage_label)
                if exclusion_reason:
                    _record_main_chain_exclusion(item, exclusion_reason, stage_key=stage_key)
                    continue
                step_meta = workflow_stage_meta_by_key.get(stage_key) or {}
                if bool(step_meta.get("exclude_failure_steps")) and _any(text, low_chain_tokens):
                    _record_main_chain_exclusion(item, "failure_step_excluded_by_blueprint", stage_key=stage_key)
                    continue
                expected_stage_kind = str(step_meta.get("stage_kind") or "").strip().lower()
                if not expected_stage_kind:
                    expected_stage_kind = _workflow_stage_kind_from_text(
                        " ".join(
                            [
                                str(stage_label or ""),
                                str(step_meta.get("label") or ""),
                                str(step_meta.get("action") or ""),
                                str(step_meta.get("assertion") or ""),
                            ]
                        )
                    )
                candidate_stage_kind = _workflow_stage_kind_from_text(text)
                if strict_blueprint_semantic_filter:
                    semantic_probe = dict(item)
                    semantic_probe["execution_group"] = "main_smoke"
                    semantic_probe["main_chain_stage_kind"] = expected_stage_kind
                    semantic_probe["role"] = str(
                        step_meta.get("actor") or item.get("role") or _infer_actor_from_text(text)
                    ).strip()
                    semantic_conflicts = validate_main_smoke_semantic_alignment([semantic_probe])
                    if semantic_conflicts:
                        first_reason = str(semantic_conflicts[0].get("reason") or "main_chain_semantic_conflict")
                        _record_main_chain_exclusion(item, first_reason, stage_key=stage_key)
                        continue
                score = _priority_rank(item) + min(80, match_score)
                if expected_stage_kind in {"commit", "downstream_visibility"}:
                    if candidate_stage_kind == expected_stage_kind:
                        score += 30
                    elif candidate_stage_kind in {"commit", "downstream_visibility", "consume", "completion_sync"}:
                        score -= 45
                if _any(text, low_chain_tokens):
                    score -= 35
                state_out = str(step_meta.get("state_out") or "").lower()
                assertion = str(step_meta.get("assertion") or "").lower()
                if state_out and state_out in text:
                    score += 8
                if assertion and assertion[:40] in text:
                    score += 8
                ranked.append((score, -index, item))
            if not ranked:
                continue
            ranked.sort(reverse=True)
            best = dict(ranked[0][2])
            selected_signature = _signature(best)
            selected_signatures.add(selected_signature)
            selected_by_stage.append((stage_key, stage_label, best))

        selected_stage_keys = {stage_key for stage_key, _stage_label, _item in selected_by_stage}
        stage_label_by_key = {stage_key: stage_label for stage_key, stage_label, _patterns in main_chain_stages}

        def _bridge_case(stage_key: str, *, available_stage_keys: set[str] | None = None) -> dict[str, Any] | None:
            step_meta = workflow_stage_meta_by_key.get(stage_key) or {}
            if not step_meta or not bool(step_meta.get("allow_bridge")):
                return None
            stage_order = [key for key, _label, _patterns in main_chain_stages]
            try:
                stage_index = stage_order.index(stage_key)
            except ValueError:
                return None
            available = available_stage_keys if available_stage_keys is not None else selected_stage_keys
            if stage_index > 0 and stage_order[stage_index - 1] not in available:
                return None
            label = str(step_meta.get("label") or step_meta.get("action") or stage_key).strip()
            assertion = str(step_meta.get("assertion") or step_meta.get("expected_result") or step_meta.get("state_out") or "").strip()
            test_steps = step_meta.get("test_steps") if isinstance(step_meta.get("test_steps"), list) else []
            return {
                "id": f"TC-BRIDGE-{stage_key.upper().replace(':', '-').replace(' ', '-')[:40]}",
                "description": label or stage_key,
                "test_module": str(step_meta.get("module") or step_meta.get("blueprint_name") or "workflow_blueprint"),
                "preconditions": [str(step_meta.get("state_in") or "previous workflow state")],
                "steps": test_steps or [str(step_meta.get("action") or label or stage_key)],
                "test_input": str(step_meta.get("input") or step_meta.get("state_in") or "workflow state"),
                "expected_result": assertion or f"workflow state reaches {stage_key}",
                "priority": "P0" if bool(step_meta.get("main_path_step", True)) else "P1",
                "role": str(step_meta.get("actor") or "student"),
                "generated_bridge_case": True,
                "workflow_blueprint_bridge": True,
            }

        def _is_internal_state_text(value: str) -> bool:
            return bool(re.search(r"\b[a-z][a-z0-9]*_[a-z0-9_]*\b", str(value or "").strip().lower()))

        def _public_contract_module_label(step_meta: dict[str, Any], label: str) -> str:
            for raw in (
                step_meta.get("module"),
                step_meta.get("domain"),
                step_meta.get("feature"),
                step_meta.get("blueprint_name"),
            ):
                value = str(raw or "").strip()
                if value and not _is_internal_state_text(value):
                    return value[:80]
            if _any(str(label or "").lower(), ("学生", "学员", "student")):
                return "学生端主链路"
            return "业务主链路"

        def _contract_materialized_expected_result(label: str, stage_kind: str) -> str:
            stage = str(stage_kind or "").strip().lower()
            if stage == "entry":
                return f"{label}完成，目标入口页面可继续操作"
            if stage == "configure":
                return f"{label}完成，已选配置在页面中保留并可进入下一步"
            if stage == "preview":
                return f"{label}完成，预览内容展示当前配置结果"
            if stage == "commit":
                return f"{label}完成，保存结果展示成功状态"
            if stage == "downstream_visibility":
                return f"{label}完成，下游页面展示最新业务结果"
            if stage == "consume":
                return f"{label}完成，目标页面打开并展示可操作内容"
            if stage == "completion_sync":
                return f"{label}完成，进度状态更新"
            return f"{label}完成，业务状态已更新并可继续执行下一步"

        def _contract_materialized_case(
            stage_key: str,
            *,
            available_stage_keys: set[str] | None = None,
        ) -> dict[str, Any] | None:
            bridge = _bridge_case(stage_key, available_stage_keys=available_stage_keys)
            if bridge is None:
                return None
            step_meta = workflow_stage_meta_by_key.get(stage_key) or {}
            label = str(step_meta.get("label") or stage_key).strip()
            if not label or _is_internal_state_text(label):
                return None
            stage_kind = str(step_meta.get("stage_kind") or "").strip().lower()
            assertion = str(step_meta.get("assertion") or step_meta.get("expected_result") or "").strip()
            expected_result = (
                assertion
                if assertion and not _is_internal_state_text(assertion)
                else _contract_materialized_expected_result(label, stage_kind)
            )
            return {
                "id": f"TC-CONTRACT-{stage_key.upper().replace(':', '-').replace(' ', '-')[:40]}",
                "description": label,
                "test_module": _public_contract_module_label(step_meta, label),
                "preconditions": [f"已具备执行“{label}”的前置业务状态"],
                "steps": [label],
                "test_input": label,
                "expected_result": expected_result,
                "priority": "P0" if bool(step_meta.get("main_path_step", True)) else "P1",
                "role": str(step_meta.get("actor") or "student"),
                "workflow_contract_materialized_case": True,
            }

        if selected_by_stage or trusted_workflow_contracts:
            bridged_by_stage: list[tuple[str, str, dict[str, Any]]] = []
            current_selected = {stage_key for stage_key, _label, _item in selected_by_stage}
            selected_by_stage_map = {stage_key: (stage_label, item) for stage_key, stage_label, item in selected_by_stage}
            allow_contract_materialization = bool(strict_blueprint_semantic_filter and selected_by_stage)
            for stage_key, stage_label, _patterns in main_chain_stages:
                existing = selected_by_stage_map.get(stage_key)
                if existing:
                    current_selected.add(stage_key)
                    selected_stage_keys.add(stage_key)
                    bridged_by_stage.append((stage_key, existing[0], existing[1]))
                    continue
                bridge = (
                    _contract_materialized_case(stage_key, available_stage_keys=current_selected)
                    if allow_contract_materialization
                    else _bridge_case(stage_key, available_stage_keys=current_selected)
                )
                if bridge is not None:
                    if strict_blueprint_semantic_filter:
                        if not bool(bridge.get("workflow_contract_materialized_case")):
                            _record_main_chain_exclusion(bridge, "bridge_case_not_public_final_case", stage_key=stage_key)
                            continue
                    current_selected.add(stage_key)
                    selected_stage_keys.add(stage_key)
                    bridged_by_stage.append((stage_key, stage_label_by_key.get(stage_key, stage_label), bridge))
            selected_by_stage = bridged_by_stage

        selected_by_stage_source = (
            "feedback_control_state"
            if workflow_blueprints
            else "current_generation_cases"
            if plan_workflow_blueprints
            else "none"
        )
        main_chain_stage_kinds: list[str] = []
        selected_stage_state_conflicts: list[dict[str, Any]] = []
        if selected_by_stage and strict_blueprint_semantic_filter:
            selected_stage_state_conflicts = _selected_stage_state_conflicts(selected_by_stage)
            if selected_stage_state_conflicts:
                main_chain_incomplete_reason = "state_chain_conflict"
                conflicted_stage_keys = {
                    str(conflict.get("prev_stage_key") or "")
                    for conflict in selected_stage_state_conflicts
                    if str(conflict.get("prev_stage_key") or "")
                } | {
                    str(conflict.get("curr_stage_key") or "")
                    for conflict in selected_stage_state_conflicts
                    if str(conflict.get("curr_stage_key") or "")
                }
                for excluded_stage_key, _excluded_stage_label, excluded_item in selected_by_stage:
                    if str(excluded_stage_key) in conflicted_stage_keys:
                        _record_main_chain_exclusion(
                            excluded_item,
                            "state_bridge_missing",
                            stage_key=excluded_stage_key,
                        )
                selected_by_stage = []
                selected_signatures.clear()
        if selected_by_stage:
            closure_ok, closure_reason, main_chain_stage_kinds = _main_chain_closure_status(
                selected_by_stage,
                source=selected_by_stage_source,
            )
            if not closure_ok:
                main_chain_incomplete_reason = closure_reason
                for excluded_stage_key, _excluded_stage_label, excluded_item in selected_by_stage:
                    _record_main_chain_exclusion(
                        excluded_item,
                        "state_bridge_missing",
                        stage_key=excluded_stage_key,
                    )
                selected_by_stage = []
                selected_signatures.clear()

        def _infer_role(item: dict[str, Any]) -> str:
            explicit_role = str(item.get("role") or "").strip().lower()
            if explicit_role in {"admin", "supervisor", "teacher", "student", "member", "student_free"}:
                return "supervisor" if explicit_role == "teacher" else explicit_role
            return _infer_actor_from_text(_case_text(item))

        stage_output_state = dict(workflow_stage_output_state)

        def _infer_data_state(item: dict[str, Any], *, stage_key: str = "") -> str:
            if stage_key in stage_output_state:
                return str(stage_output_state[stage_key])
            text = _case_text(item)
            if _any(text, ("失败", "异常", "错误", "超时", "failure", "failed", "error", "timeout")):
                return "failed"
            if _any(text, ("待处理", "处理中", "待审核", "审核中", "pending", "processing")):
                return "pending"
            if _any(text, ("完成", "成功", "已生成", "已保存", "生效", "completed", "success", "saved")):
                return "completed"
            if _any(text, ("变更", "更新", "同步", "流转", "changed", "updated", "synced", "transition")):
                return "changed"
            if _any(text, ("空状态", "无数据", "暂无")):
                return "empty"
            return "prepared"

        def _fixture_for_case(item: dict[str, Any], group: str, data_state: str) -> dict[str, str]:
            text = _case_text(item)
            fixture_key = "default_logged_in_student"
            fixture_builder = "login_student()"
            cleanup_policy = "reset_session"
            if group == "main_smoke":
                fixture_key = "workflow_blueprint_chain_seed"
                fixture_builder = "seed_workflow_blueprint_dataset()"
                cleanup_policy = "cleanup_workflow_blueprint_dataset"
            elif group == "permission":
                if _any(text, ("麦克风", "语音", "录音", "浏览器权限", "授权")):
                    fixture_key = "browser_permission_state"
                    fixture_builder = "set_browser_permission(permission='microphone', state='prompt')"
                    cleanup_policy = "reset_browser_permissions"
                else:
                    fixture_key = "permission_state_dataset"
                    fixture_builder = "seed_permission_state_dataset()"
                    cleanup_policy = "reset_permission_state_dataset"
            elif group == "exception":
                fixture_key = "fault_injection_case"
                fixture_builder = "enable_fault_injection_for_case()"
                cleanup_policy = "disable_fault_injection"
            elif group == "boundary":
                if data_state == "empty":
                    fixture_key = "empty_state_dataset"
                    fixture_builder = "seed_empty_state()"
                    cleanup_policy = "restore_default_dataset"
                else:
                    fixture_key = "boundary_dataset"
                    fixture_builder = "seed_boundary_dataset()"
                    cleanup_policy = "delete_boundary_dataset"
            elif group == "display":
                fixture_key = "display_ready_dataset"
                fixture_builder = "seed_display_ready_dataset()"
                cleanup_policy = "delete_display_ready_dataset"
            else:
                fixture_key = f"{group}_dataset"
                fixture_builder = f"seed_{group}_dataset()"
                cleanup_policy = f"cleanup_{group}_dataset"
            return {
                "fixture_key": fixture_key,
                "fixture_builder": fixture_builder,
                "cleanup_policy": cleanup_policy,
            }

        def _session_key_for_role(role: str) -> str:
            if role == "admin":
                return "admin_review_session"
            if role == "supervisor":
                return "supervisor_session"
            if role == "member":
                return "member_student_session"
            if role == "student_free":
                return "free_student_session"
            return "student_session"

        def _is_student_observation_projection(item: dict[str, Any]) -> bool:
            text = _case_text(item)
            approval_or_publish = _any(
                text,
                (
                    "review approved",
                    "approval passed",
                    "published",
                    "visible in community",
                    "审核通过",
                    "已发布",
                    "发布",
                ),
            )
            student_surface = _any(
                text,
                (
                    "community",
                    "visible",
                    "student",
                    "作文圈",
                    "我的作文",
                    "列表可见",
                    "可见",
                    "同步",
                ),
            )
            return bool(approval_or_publish and student_surface)

        group_setup_map = {
            "main_smoke": "seed_workflow_blueprint_dataset()",
            "permission": "seed_permission_state_dataset()",
            "exception": "enable_fault_injection_for_case()",
            "boundary": "seed_boundary_dataset()",
            "independent_functional": "seed_functional_dataset()",
            "display": "seed_display_ready_dataset()",
        }
        group_teardown_map = {
            "main_smoke": "cleanup_workflow_blueprint_dataset()",
            "permission": "reset_permission_state_dataset()",
            "exception": "disable_fault_injection()",
            "boundary": "delete_boundary_dataset()",
            "independent_functional": "cleanup_functional_dataset()",
            "display": "delete_display_ready_dataset()",
        }

        def _infer_group(item: dict[str, Any], *, in_main_chain: bool) -> str:
            if in_main_chain:
                return "main_smoke"
            text = _case_text(item)
            if _any(
                text,
                (
                    "权限",
                    "无权限",
                    "越权",
                    "授权失败",
                    "鉴权",
                    "未登录",
                    "permission",
                    "unauthorized",
                    "forbidden",
                    "access denied",
                    "auth failed",
                ),
            ):
                return "permission"
            if _any(text, ("失败", "异常", "超时", "网络", "拒绝", "不通过", "接口", "重试", "failure", "error", "timeout", "retry")):
                return "exception"
            if _any(text, ("空状态", "最多", "最少", "上限", "下限", "格式", "大小", "边界", "无数据", "max", "min", "limit", "boundary")):
                return "boundary"
            if _any(text, ("下载", "入口", "弹窗", "展示", "排序", "筛选", "列表", "详情", "display", "list", "detail", "filter")):
                return "display"
            return "independent_functional"

        def _setup_hint(item: dict[str, Any], *, in_main_chain: bool, previous_id: str = "", previous_result: str = "") -> str:
            if in_main_chain and previous_id:
                return f"依赖 {previous_id} 的执行结果：{previous_result[:120]}"
            preconditions = item.get("preconditions")
            if isinstance(preconditions, list):
                joined = "；".join(str(x).strip() for x in preconditions if str(x).strip())
                if joined:
                    return f"独立准备：{joined[:160]}"
            return "独立准备：按本用例前置条件准备账号、数据和页面状态"

        def _is_low_value_main_chain_p0(item: dict[str, Any]) -> bool:
            text = _case_text(item)
            low_value_status = _any(
                text,
                (
                    "remains pending",
                    "pending status remains",
                    "pending status",
                    "48 hours",
                    "48h",
                    "record limit",
                    "records limit",
                    "maximum records",
                    "drag sort",
                    "drag sorted",
                    "delete thumbnail",
                    "force close",
                    "kill app",
                    "\u4fdd\u6301\u5ba1\u6838\u4e2d",
                    "\u5ba1\u6838\u4e2d\u4fdd\u6301",
                    "\u72b6\u6001\u4fdd\u6301",
                    "48\u5c0f\u65f6",
                ),
            )
            blocking_closure = _any(
                text,
                (
                    "submit succeeds",
                    "submit success",
                    "submitted successfully",
                    "generate correction result",
                    "correction result is generated",
                    "feedback modules",
                    "four modules",
                    "result details",
                    "approval passed",
                    "review approved",
                    "approved work",
                    "first lesson",
                    "all courses",
                    "locked",
                    "paywall",
                    "\u63d0\u4ea4\u6210\u529f",
                    "\u751f\u6210\u6279\u6539\u7ed3\u679c",
                    "\u6279\u6539\u7ed3\u679c",
                    "\u56db\u4e2a\u6a21\u5757",
                    "\u5ba1\u6838\u901a\u8fc7",
                    "\u7b2c\u4e00\u8bfe",
                    "\u5168\u90e8\u8bfe\u7a0b",
                ),
            )
            return bool(low_value_status and not blocking_closure)

        def _is_core_result_output_anchor(item: dict[str, Any]) -> bool:
            text = _case_text(item)
            complete_result_output = _any(
                text,
                (
                    "feedback modules",
                    "four modules",
                    "result details",
                    "correction result is generated",
                    "generate correction result",
                    "\u56db\u4e2a\u6a21\u5757",
                    "\u56db\u90e8\u5206",
                    "\u7ed3\u679c\u8be6\u60c5",
                    "\u6279\u6539\u7ed3\u679c\u9875\u5c55\u793a",
                ),
            )
            detail_only_output = _any(
                text,
                (
                    "star rating",
                    "stars",
                    "button disabled",
                    "disabled button",
                    "0 images",
                    "editable title",
                    "title body",
                    "\u661f\u661f\u8bc4\u5206",
                    "\u8bc4\u5206\u5c55\u793a",
                    "\u6309\u94ae\u4e0d\u53ef\u70b9",
                    "\u7f6e\u7070",
                    "0\u5f20",
                    "\u6807\u9898\u6b63\u6587",
                    "\u53ef\u7f16\u8f91",
                ),
            )
            if detail_only_output and not complete_result_output:
                return False
            return _any(
                text,
                (
                    "feedback modules",
                    "four modules",
                    "result details",
                    "correction result is generated",
                    "generate correction result",
                ),
            ) or complete_result_output

        main_chain_cases = [item for _stage_key, _stage_label, item in selected_by_stage]
        main_chain_signatures = {_signature(item) for item in main_chain_cases}
        remaining_cases = [
            dict(item)
            for item in candidate_cases
            if _signature(item) not in main_chain_signatures and _signature(item) not in selected_signatures
        ]
        group_rank = {
            "permission": 1,
            "exception": 2,
            "boundary": 3,
            "independent_functional": 4,
            "display": 5,
        }
        remaining_cases.sort(
            key=lambda item: (
                group_rank.get(_infer_group(item, in_main_chain=False), 9),
                {"P0": 0, "P1": 1, "P2": 2}.get(str(item.get("priority") or "").upper(), 2),
                str(item.get("test_module") or ""),
                str(item.get("description") or ""),
            )
        )
        ordered_cases = [*main_chain_cases, *remaining_cases]

        safe_start = max(1, int(start_id or 1))
        annotated: list[dict[str, Any]] = []
        previous_main_id = ""
        previous_main_result = ""
        main_chain_stage_by_signature = {
            _signature(item): (stage_key, stage_label, index + 1)
            for index, (stage_key, stage_label, item) in enumerate(selected_by_stage)
        }
        role_sequence: list[str] = []
        for offset, item in enumerate(ordered_cases):
            updated = dict(item)
            signature = _signature(updated)
            new_id = f"TC-{safe_start + offset:03d}"
            updated["id"] = new_id
            stage_info = main_chain_stage_by_signature.get(signature)
            in_main_chain = bool(stage_info)
            stage_key = str(stage_info[0]) if stage_info else ""
            group = _infer_group(updated, in_main_chain=in_main_chain)
            step_meta_for_role = workflow_stage_meta_by_key.get(stage_key) or {}
            role = (
                str(step_meta_for_role.get("actor") or "").strip().lower()
                if in_main_chain and str(step_meta_for_role.get("actor") or "").strip()
                else _infer_role(updated)
            )
            if role == "teacher":
                role = "supervisor"
            student_observation_projection = _is_student_observation_projection(updated)
            if student_observation_projection:
                updated["source_actor_role"] = role
                role = "student"
                updated["student_observation_projection"] = True
            role_sequence.append(role)
            depends_on = [previous_main_id] if in_main_chain and previous_main_id else []
            data_state = _infer_data_state(updated, stage_key=stage_key)
            fixture = _fixture_for_case(updated, group, data_state)
            updated["execution_group"] = group
            updated["execution_sequence"] = int(offset + 1)
            updated["chain_id"] = "main_smoke_chain" if in_main_chain else f"{group}_independent"
            updated["depends_on"] = depends_on
            updated["role"] = role
            updated["session_key"] = _session_key_for_role(role)
            updated["role_switch_strategy"] = (
                "switch_to_admin_session_then_return_student_session"
                if in_main_chain and role == "admin"
                else "reuse_group_session"
            )
            updated["data_state"] = data_state
            updated["isolation_required"] = bool(not in_main_chain)
            updated["fixture_key"] = fixture["fixture_key"]
            updated["fixture_builder"] = fixture["fixture_builder"]
            updated["cleanup_policy"] = fixture["cleanup_policy"]
            updated["group_setup"] = group_setup_map.get(group, "seed_case_dataset()")
            updated["group_teardown"] = group_teardown_map.get(group, "cleanup_case_dataset()")
            if group == "permission" and fixture["fixture_key"] in {"browser_permission_state", "permission_state_dataset"}:
                updated["group_setup"] = fixture["fixture_builder"]
                updated["group_teardown"] = (
                    "reset_browser_permissions()"
                    if fixture["fixture_key"] == "browser_permission_state"
                    else "reset_permission_state_dataset()"
                )
            updated["setup_hint"] = _setup_hint(
                updated,
                in_main_chain=in_main_chain,
                previous_id=previous_main_id,
                previous_result=previous_main_result,
            )
            if in_main_chain and not depends_on:
                updated["setup_hint"] = f"组级准备：{updated['group_setup']}；然后执行本主链路首个用例"
            updated["teardown_hint"] = (
                "主链路末尾执行清理；中间步骤不清理，供下一条用例复用状态"
                if in_main_chain
                else "执行后恢复本用例改动的数据，避免影响其他独立用例"
            )
            if stage_info:
                updated["main_chain_stage"] = str(stage_info[0])
                updated["main_chain_stage_label"] = str(stage_info[1])
                updated["main_chain_step"] = int(stage_info[2])
                step_meta = workflow_stage_meta_by_key.get(str(stage_info[0]) or "") or {}
                transition = _workflow_transition_for_case(
                    updated,
                    stage_key=str(stage_info[0]),
                    stage_label=str(stage_info[1]),
                )
                updated["workflow_transition"] = transition
                updated["main_chain_stage_kind"] = str(transition.get("stage_kind") or "").strip()
                if bool(step_meta.get("main_path_step", True)) and not _is_low_value_main_chain_p0(updated):
                    updated["priority"] = "P0"
                    updated["priority_final"] = "P0"
                else:
                    updated["priority"] = "P1"
                    updated["priority_final"] = "P1"
                    updated["priority_decision_state"] = "overridden"
                    updated["priority_decision_source"] = "execution_plan_main_support_step_demoted"
            elif str(updated.get("priority") or "").strip().upper() == "P0":
                decision_source = str(updated.get("priority_decision_source") or "").strip()
                non_blocking_detail = _any(
                    _case_text(updated),
                    (
                        "弹窗",
                        "提示文案",
                        "展示",
                        "排序",
                        "筛选",
                        "列表",
                        "详情",
                        "display",
                        "tooltip",
                        "badge",
                    ),
                )
                blocking_business_anchor = _any(
                    _case_text(updated),
                    (
                        "generate result",
                        "generated result",
                        "correction result",
                        "review result",
                        "four modules",
                        "feedback modules",
                        "upload",
                        "submit success",
                        "approval passed",
                        "review approved",
                        "上传",
                        "去批改",
                        "生成批改结果",
                        "批改结果",
                        "四部分",
                        "综合点评",
                        "全文润色",
                        "优化建议",
                        "提交成功",
                        "审核通过",
                        "已发布",
                        "作文圈",
                    ),
                )
                preserve_semantic_anchor = decision_source in {
                    "main_path_anchor_floor",
                    "hard_guard_promotion",
                    "preserved_priority_override",
                    "conflict_resolved_by_high_risk_business_rule",
                } and (blocking_business_anchor or not non_blocking_detail)
                demote_non_main = False
                if not preserve_semantic_anchor:
                    demote_non_main = group in {"boundary", "display", "exception"}
                if demote_non_main:
                    updated["priority"] = "P1"
                    updated["priority_final"] = "P1"
                    updated["priority_decision_state"] = "overridden"
                    updated["priority_decision_source"] = "execution_plan_non_main_p0_demoted"
            elif group == "display" and _is_core_result_output_anchor(updated):
                updated["priority"] = "P0"
                updated["priority_final"] = "P0"
                updated["priority_decision_state"] = "overridden"
                updated["priority_decision_source"] = "execution_plan_core_result_output_promoted"
            annotated.append(updated)
            if in_main_chain:
                previous_main_id = new_id
                previous_main_result = str(updated.get("expected_result") or "")

        annotated = materialize_final_case_state_fields(annotated)
        role_switch_count = 0
        previous_role = ""
        for role in [str(item.get("role") or "") for item in annotated if str(item.get("execution_group") or "") == "main_smoke"]:
            if previous_role and role != previous_role:
                role_switch_count += 1
            previous_role = role

        main_chain_count = sum(1 for item in annotated if str(item.get("execution_group") or "") == "main_smoke")
        independent_count = max(0, len(annotated) - main_chain_count)
        isolation_count = sum(1 for item in annotated if bool(item.get("isolation_required")))
        broken_dependency_count = sum(
            1
            for item in annotated
            if str(item.get("execution_group") or "") == "main_smoke"
            and int(item.get("main_chain_step") or 0) > 1
            and not item.get("depends_on")
        )
        state_conflicts = validate_main_smoke_state_chain(annotated)
        semantic_conflicts = validate_main_smoke_semantic_alignment(annotated)
        summary = {
            "applied": True,
            "coverage_mode": str(coverage_mode or ""),
            "workflow_blueprint_count": int(len(workflow_blueprints)),
            "trusted_workflow_contract_count": int(len(trusted_workflow_contracts)),
            "plan_workflow_blueprint_count": int(len(plan_workflow_blueprints)),
            "workflow_blueprint_source": (
                "feedback_control_state"
                if workflow_blueprints
                else "current_generation_cases"
                if plan_workflow_blueprints
                else "none"
            ),
            "linear_executable": bool(
                main_chain_count >= 2
                and broken_dependency_count == 0
                and not state_conflicts
                and not semantic_conflicts
            ),
            "linear_scope": "main_smoke_chain_only",
            "main_chain_case_count": int(main_chain_count),
            "main_chain_stage_order": [
                str(item.get("main_chain_stage") or "")
                for item in annotated
                if str(item.get("execution_group") or "") == "main_smoke"
            ],
            "main_chain_stage_kinds": list(main_chain_stage_kinds),
            "main_chain_incomplete_reason": str(main_chain_incomplete_reason or ""),
            "derived_workflow_debug": dict(derived_workflow_debug),
            "main_chain_excluded_candidates": [
                {key: value for key, value in item.items() if key != "signature"}
                for item in main_chain_excluded_candidates[:50]
            ],
            "independent_case_count": int(independent_count),
            "isolation_case_count": int(isolation_count),
            "role_switch_count": int(role_switch_count),
            "broken_dependency_count": int(broken_dependency_count),
            "state_conflict_count": int(len(state_conflicts)),
            "state_conflicts": state_conflicts[:50],
            "selected_stage_state_conflicts": selected_stage_state_conflicts[:50],
            "semantic_conflict_count": int(len(semantic_conflicts)),
            "semantic_conflicts": semantic_conflicts[:50],
            "execution_group_breakdown": {
                group: sum(1 for item in annotated if str(item.get("execution_group") or "") == group)
                for group in sorted({str(item.get("execution_group") or "") for item in annotated})
            },
            "generated_bridge_case_count": int(sum(1 for item in annotated if bool(item.get("generated_bridge_case")))),
            "workflow_contract_materialized_case_count": int(
                sum(1 for item in annotated if bool(item.get("workflow_contract_materialized_case")))
            ),
            "group_setup": {
                group: group_setup_map.get(group, "seed_case_dataset()")
                for group in sorted({str(item.get("execution_group") or "") for item in annotated})
            },
            "group_teardown": {
                group: group_teardown_map.get(group, "cleanup_case_dataset()")
                for group in sorted({str(item.get("execution_group") or "") for item in annotated})
            },
            "fixture_keys": sorted(
                {str(item.get("fixture_key") or "") for item in annotated if str(item.get("fixture_key") or "")}
            ),
        }
        return annotated, summary

    def _filter_low_quality_cases_with_stats(
        cases: list[dict[str, Any]],
        *,
        requirement_text: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        normalized_cases: list[dict[str, Any]] = []
        stats = {
            "invalid_structure_dropped": 0,
            "weak_case_dropped": 0,
            "semantic_dedup_dropped": 0,
            "governance_hard_drop": 0,
            "total_dropped": 0,
            "dropped_details": [],
        }
        for item in cases:
            if not isinstance(item, dict):
                stats["invalid_structure_dropped"] += 1
                stats["dropped_details"].append(
                    {"stage": "initial_structure_filter", "reason": "non_dict_case"}
                )
                continue
            normalized = _normalize_case_structure(item)
            if not isinstance(normalized, dict):
                stats["invalid_structure_dropped"] += 1
                stats["dropped_details"].append(
                    _quality_drop_detail(item, reason="normalize_failed", stage="initial_structure_filter")
                )
                continue
            low_quality_reason = _low_quality_reason(normalized)
            if low_quality_reason:
                stats["weak_case_dropped"] += 1
                stats["dropped_details"].append(
                    _quality_drop_detail(
                        normalized,
                        reason=low_quality_reason,
                        stage="initial_quality_filter",
                    )
                )
                continue
            normalized_cases.append(normalized)

        deduplicated_cases, dedup_dropped = _semantic_deduplicate_cases(normalized_cases)
        stats["semantic_dedup_dropped"] += int(dedup_dropped)

        reprioritized_cases = _rebuild_priority_by_semantics(deduplicated_cases)
        downgraded_cases = _apply_uncertain_requirement_downgrade(
            reprioritized_cases,
            requirement_text=str(requirement_text or ""),
        )
        downgraded_cases = _enforce_uncertain_priority_floor(downgraded_cases)
        if downgraded_cases:
            priority_coverage = analyze_coverage(str(requirement_text or ""), downgraded_cases)
            downgraded_cases = apply_priority_semantics_to_cases(
                [item for item in downgraded_cases if isinstance(item, dict)],
                attach_debug=False,
                coverage_context=priority_coverage,
                rule_diagnostics={"rule_diagnostics": priority_coverage.get("rule_diagnostics") or []},
            )
            downgraded_cases = _enforce_uncertain_priority_floor(downgraded_cases)

        required_groups = _required_p0_groups_from_requirement(str(requirement_text or ""))
        requirement_has_uncertain_signal = any(
            signal in str(requirement_text or "") for signal in _UNCERTAIN_SIGNALS
        )
        if required_groups and not requirement_has_uncertain_signal:
            covered_groups = _covered_p0_groups(downgraded_cases)
            if not covered_groups:
                needs_priority_review = any(
                    str(item.get("priority_decision_state") or "").strip().lower() in {"conflict", "undetermined", "invalid"}
                    for item in downgraded_cases
                    if isinstance(item, dict)
                )
                if not needs_priority_review:
                    # Missing P0 coverage is a review/diagnostic signal, not a valid reason
                    # to erase every generated candidate. The previous hard-drop path caused
                    # stream preview candidates to persist as [] when broad requirement tokens
                    # accidentally activated a P0 group.
                    pass

        stats["total_dropped"] = int(
            stats.get("invalid_structure_dropped", 0)
            + stats.get("weak_case_dropped", 0)
            + stats.get("semantic_dedup_dropped", 0)
            + stats.get("governance_hard_drop", 0)
        )
        return downgraded_cases, stats

    def _filter_low_quality_cases(
        cases: list[dict[str, Any]],
        *,
        requirement_text: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        filtered_cases, stats = _filter_low_quality_cases_with_stats(
            cases,
            requirement_text=requirement_text,
        )
        return filtered_cases, int((stats or {}).get("total_dropped") or 0)

    def _rerank_and_cap_by_rule(
        cases: list[dict[str, Any]],
        *,
        max_per_rule: int = 3,
        include_trace: bool = False,
        coverage_context: dict[str, Any] | None = None,
        rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
        generation_profile: dict[str, Any] | None = None,
    ) -> Any:
        # 中文注释：第三刀——rule 级收敛 + 语义去重 + UI-like 收敛，防止“覆盖正确但结构爆炸”。
        profile = dict(generation_profile or {})
        coverage_mode = str(profile.get("coverage_mode") or "").strip()
        if coverage_mode == "full_functional_regression":
            max_cases_per_rule = max(1, min(int(max_per_rule or 4), 4))
            max_ui_like_cases_per_bucket = 4
            semantic_duplicate_threshold = 0.90
        elif coverage_mode == "expanded_regression":
            max_cases_per_rule = max(1, min(int(max_per_rule or 3), 3))
            max_ui_like_cases_per_bucket = 3
            semantic_duplicate_threshold = 0.88
        elif coverage_mode == "standard_regression":
            max_cases_per_rule = max(1, min(int(max_per_rule or 3), 3))
            max_ui_like_cases_per_bucket = 3
            semantic_duplicate_threshold = 0.86
        else:
            max_cases_per_rule = max(1, min(int(max_per_rule or 1), 2))
            max_ui_like_cases_per_bucket = 2
            semantic_duplicate_threshold = 0.82

        candidate_items = [item for item in cases if isinstance(item, dict)]
        original_signatures = [_signature(item) for item in candidate_items]
        candidate_items = deduplicate_test_cases_fn(candidate_items)
        dedup_signatures = [_signature(item) for item in candidate_items]

        # 中文注释：review gate 排序改为 coverage/focus 主导，priority 只做弱 tie-breaker。
        rank_entries: list[dict[str, Any]] = []
        for item in candidate_items:
            signature = _signature(item)
            score_profile = score_case_priority(
                item,
                coverage_context=coverage_context,
                rule_diagnostics=rule_diagnostics,
            )
            missing_rule_hits = [str(x) for x in (score_profile.get("missing_rule_hits") or []) if str(x).strip()]
            core_rule_hits = [str(x) for x in (score_profile.get("core_rule_hits") or []) if str(x).strip()]
            unique_coverage_hits = [str(x) for x in (score_profile.get("unique_coverage_hits") or []) if str(x).strip()]
            has_coverage_value = bool(missing_rule_hits or core_rule_hits or unique_coverage_hits)
            focus_score = int(_focus_score(item))
            coverage_gain_score = int(score_profile.get("coverage_gain_score") or 0)
            high_signal_seed = _is_high_signal(item, score_profile)
            ui_like_case = _is_ui_like_case(item, score_profile)
            reuse_risk_hit = _hits_reuse_risk(item, score_profile)
            complexity_score = int(case_complexity_profile(item).get("complexity_score") or 0)
            semantic_sig = _semantic_signature(item, list(_extract_rule_keys(item)))
            semantic_tokens = _semantic_tokenize(
                " ".join(
                    [
                        str(item.get("description") or ""),
                        str(item.get("expected_result") or ""),
                        str(item.get("test_input") or ""),
                        " ".join([str(x) for x in item.get("steps", [])]) if isinstance(item.get("steps"), list) else "",
                    ]
                )
            )
            entry = {
                "item": item,
                "signature": signature,
                "rule_keys": _extract_rule_keys(item),
                "bucket": _coverage_bucket(item),
                "score_profile": score_profile,
                "missing_rule_hits": missing_rule_hits,
                "core_rule_hits": core_rule_hits,
                "unique_coverage_hits": unique_coverage_hits,
                "has_coverage_value": bool(has_coverage_value),
                "focus_score": int(focus_score),
                "coverage_gain_score": int(coverage_gain_score),
                "high_signal_seed": bool(high_signal_seed),
                "ui_like_case": bool(ui_like_case),
                "reuse_risk_hit": bool(reuse_risk_hit),
                "complexity_score": int(complexity_score),
                "soft_constraint_hit": bool(_hits_soft_constraint(item)),
                "semantic_signature": semantic_sig,
                "semantic_tokens": semantic_tokens,
                # priority 保留为最后层 tie-breaker，弱影响
                "priority_tiebreaker": int(_priority_score(item)),
            }
            rank_entries.append(entry)

        signal_bearing_candidate_total = int(
            sum(
                1
                for entry in rank_entries
                if bool(entry.get("rule_keys"))
                or bool(entry.get("has_coverage_value"))
                or bool(entry.get("reuse_risk_hit"))
            )
        )
        preserve_dense_quality_set = bool(
            len(rank_entries) >= 30 and signal_bearing_candidate_total == 0
        ) or bool(
            coverage_mode == "full_functional_regression" and len(rank_entries) >= 30
        )

        def _seed_sort_key(entry: dict[str, Any]) -> tuple:
            return (
                int(entry.get("has_coverage_value") or False),
                int(entry.get("high_signal_seed") or False),
                int(bool(entry.get("core_rule_hits") or [])),
                int(bool(entry.get("missing_rule_hits") or [])),
                int(bool(entry.get("unique_coverage_hits") or [])),
                int(bool(entry.get("reuse_risk_hit"))),
                int(entry.get("focus_score") or 0),
                max(0, int(entry.get("coverage_gain_score") or 0)),
                -min(8, int(entry.get("complexity_score") or 0)),
                int(not bool(entry.get("ui_like_case"))),
                int(entry.get("priority_tiebreaker") or 0),
            )

        seed_ordered_entries = sorted(
            rank_entries,
            key=lambda entry: tuple([-value for value in _seed_sort_key(entry)])
            + (
                # soft constraint is tie-break only: never participates in primary ranking.
                int(bool(entry.get("soft_constraint_hit"))),
                str(entry.get("signature") or ""),
            ),
        )
        fallback_first_entry = seed_ordered_entries[0] if seed_ordered_entries else None
        ui_like_candidates_total = int(
            sum(
                1
                for entry in seed_ordered_entries
                if bool(entry.get("ui_like_case")) and not bool(entry.get("has_coverage_value"))
            )
        )
        expected_total = max(0, int(expected_count or 0))
        ui_min_keep_baseline = 2 if expected_total >= 12 else 1
        ui_min_keep_ratio_count = int(round(float(expected_total) * 0.15))
        ui_min_keep_target = min(
            ui_like_candidates_total,
            max(ui_min_keep_baseline, min(4, ui_min_keep_ratio_count)),
        )
        remaining_entries = list(seed_ordered_entries)

        selected: list[dict[str, Any]] = []
        seen_rules: set[str] = set()
        seen_buckets: set[str] = set()
        rule_counts: dict[str, int] = {}
        primary_rule_rank_counts: dict[str, int] = {}
        ui_like_bucket_counts: dict[str, int] = {}
        selected_semantic_by_group: dict[str, list[dict[str, Any]]] = {}
        trace_decisions: dict[str, dict[str, Any]] = {}
        selected_signatures: list[str] = []
        ordered_signatures: list[str] = []
        selected_ui_like_count = 0

        rank = 0
        while remaining_entries:
            for entry in remaining_entries:
                rule_keys = list(entry.get("rule_keys") or [])
                bucket = str(entry.get("bucket") or "")
                adds_rule = any(key not in seen_rules for key in rule_keys)
                adds_bucket = bucket not in seen_buckets
                has_coverage_value = bool(entry.get("has_coverage_value"))
                high_signal = _is_high_signal(entry.get("item") or {}, entry.get("score_profile") or {})
                if bool(entry.get("reuse_risk_hit")):
                    high_signal = True
                prefers_bucket_representative = bool(
                    adds_bucket and not rule_keys and not high_signal and not has_coverage_value
                )
                dynamic_sort_key = (
                    int(adds_rule),
                    int(adds_bucket),
                    int(prefers_bucket_representative),
                    int(has_coverage_value),
                    int(high_signal),
                    int(bool(entry.get("core_rule_hits") or [])),
                    int(bool(entry.get("missing_rule_hits") or [])),
                    int(bool(entry.get("unique_coverage_hits") or [])),
                    int(bool(entry.get("reuse_risk_hit"))),
                    int(entry.get("focus_score") or 0),
                    max(0, int(entry.get("coverage_gain_score") or 0)),
                    -min(8, int(entry.get("complexity_score") or 0)),
                    int(not bool(entry.get("ui_like_case"))),
                    int(entry.get("priority_tiebreaker") or 0),
                )
                entry["adds_rule"] = bool(adds_rule)
                entry["adds_bucket"] = bool(adds_bucket)
                entry["high_signal_dynamic"] = bool(high_signal)
                entry["dynamic_sort_key"] = dynamic_sort_key

            remaining_entries.sort(
                key=lambda entry: tuple([-value for value in tuple(entry.get("dynamic_sort_key") or ())])
                + (
                    # soft constraint is tie-break only: applies after primary/risk/coverage ordering.
                    int(bool(entry.get("soft_constraint_hit"))),
                    str(entry.get("signature") or ""),
                )
            )
            current = remaining_entries.pop(0)
            rank += 1
            item = dict(current.get("item") or {})
            signature = str(current.get("signature") or "")
            rule_keys = list(current.get("rule_keys") or [])
            bucket = str(current.get("bucket") or "")
            adds_rule = bool(current.get("adds_rule"))
            adds_bucket = bool(current.get("adds_bucket"))
            has_coverage_value = bool(current.get("has_coverage_value"))
            high_signal = bool(current.get("high_signal_dynamic"))
            score_profile = dict(current.get("score_profile") or {})
            missing_rule_hits = [str(x) for x in (current.get("missing_rule_hits") or []) if str(x).strip()]
            core_rule_hits = [str(x) for x in (current.get("core_rule_hits") or []) if str(x).strip()]
            unique_coverage_hits = [str(x) for x in (current.get("unique_coverage_hits") or []) if str(x).strip()]
            ui_like_case = bool(current.get("ui_like_case"))
            reuse_risk_hit = bool(current.get("reuse_risk_hit"))
            soft_constraint_hit = bool(current.get("soft_constraint_hit"))
            semantic_signature = str(current.get("semantic_signature") or "")
            semantic_tokens = set(current.get("semantic_tokens") or set())
            primary_rule_key = rule_keys[0] if rule_keys else f"BUCKET::{bucket}"
            semantic_group_key = "|".join(sorted(rule_keys)) if rule_keys else f"BUCKET::{bucket}"
            gate_sort_key = list(current.get("dynamic_sort_key") or ())
            ordered_signatures.append(signature)
            drop_reason = ""
            drop_reason_detail = ""
            rule_cap_applied = False
            is_semantic_duplicate = False
            duplicate_of_case_id = ""
            retained_rank_within_rule = int(primary_rule_rank_counts.get(primary_rule_key, 0)) + 1

            if rule_keys and all(rule_counts.get(key, 0) >= max_cases_per_rule for key in rule_keys):
                rule_cap_applied = True
                drop_reason = "drop_rule_cap"
                drop_reason_detail = "drop_rule_level_cap"
            elif (
                not preserve_dense_quality_set
                and not adds_rule
                and not adds_bucket
                and not high_signal
                and not has_coverage_value
                and not reuse_risk_hit
            ):
                if not (ui_like_case and selected_ui_like_count < ui_min_keep_target):
                    drop_reason = "drop_no_new_rule_no_new_bucket_no_high_signal"

            if not drop_reason and not preserve_dense_quality_set:
                existed_candidates = list(selected_semantic_by_group.get(semantic_group_key) or [])
                for existed in existed_candidates:
                    existed_signature = str(existed.get("semantic_signature") or "")
                    existed_tokens = set(existed.get("semantic_tokens") or set())
                    if semantic_signature and semantic_signature == existed_signature:
                        is_semantic_duplicate = True
                    else:
                        similarity = _jaccard_similarity(semantic_tokens, existed_tokens)
                        if similarity >= semantic_duplicate_threshold:
                            is_semantic_duplicate = True
                    if is_semantic_duplicate:
                        duplicate_of_case_id = str(existed.get("case_id") or "")
                        drop_reason = "drop_semantic_duplicate"
                        drop_reason_detail = "drop_semantic_duplicate"
                        break

            if not drop_reason and ui_like_case and not has_coverage_value and not reuse_risk_hit:
                ui_like_count = int(ui_like_bucket_counts.get(bucket, 0))
                if ui_like_count >= max_ui_like_cases_per_bucket and selected_ui_like_count >= ui_min_keep_target:
                    drop_reason = "drop_ui_like_redundant_case"
                    drop_reason_detail = "drop_ui_like_redundant_case"

            selected_flag = not bool(drop_reason)
            if selected_flag:
                selected.append(item)
                selected_signatures.append(signature)
                seen_buckets.add(bucket)
                primary_rule_rank_counts[primary_rule_key] = int(primary_rule_rank_counts.get(primary_rule_key, 0)) + 1
                retained_rank_within_rule = int(primary_rule_rank_counts.get(primary_rule_key, 0))
                if ui_like_case and not has_coverage_value and not reuse_risk_hit:
                    ui_like_bucket_counts[bucket] = int(ui_like_bucket_counts.get(bucket, 0)) + 1
                    selected_ui_like_count += 1
                for key in rule_keys:
                    rule_counts[key] = rule_counts.get(key, 0) + 1
                    seen_rules.add(key)
                selected_semantic_by_group.setdefault(semantic_group_key, []).append(
                    {
                        "signature": signature,
                        "case_id": str(item.get("id") or ""),
                        "semantic_signature": semantic_signature,
                        "semantic_tokens": semantic_tokens,
                    }
                )
            else:
                retained_rank_within_rule = int(primary_rule_rank_counts.get(primary_rule_key, 0))

            if include_trace:
                trace_decisions[signature] = {
                    "signature": signature,
                    "rank": int(rank),
                    "selected": bool(selected_flag),
                    "drop_reason": drop_reason or "retained",
                    "drop_reason_detail": drop_reason_detail or (drop_reason or "retained"),
                    "rule_keys": rule_keys,
                    "bucket": bucket,
                    "adds_rule": bool(adds_rule),
                    "adds_bucket": bool(adds_bucket),
                    "high_signal": bool(high_signal),
                    "has_coverage_value": bool(has_coverage_value),
                    "missing_rule_hits": missing_rule_hits,
                    "core_rule_hits": core_rule_hits,
                    "unique_coverage_hits": unique_coverage_hits,
                    "gate_sort_key": gate_sort_key,
                    "retained_reason": (
                        "retained_due_to_coverage_value"
                        if selected_flag and (not adds_rule and not adds_bucket and has_coverage_value and not reuse_risk_hit)
                        else "retained_default"
                    ) if selected_flag else "",
                    "priority_before_gate": str(item.get("priority") or ""),
                    "priority_tiebreaker": int(current.get("priority_tiebreaker") or 0),
                    "focus_score": int(_focus_score(item)),
                    "semantic_signature": semantic_signature,
                    "is_semantic_duplicate": bool(is_semantic_duplicate),
                    "duplicate_of_case_id": duplicate_of_case_id,
                    "rule_cap_applied": bool(rule_cap_applied),
                    "ui_like_case": bool(ui_like_case),
                    "reuse_risk_hit": bool(reuse_risk_hit),
                    "soft_constraint_hit": bool(soft_constraint_hit),
                    "retained_rank_within_rule": int(retained_rank_within_rule),
                }

        if not selected and fallback_first_entry:
            fallback_min_keep = 1
            if len(seed_ordered_entries) > 1 and expected_total >= 12:
                fallback_min_keep = min(3, len(seed_ordered_entries), 2)
            fallback_entries = seed_ordered_entries[: int(max(1, fallback_min_keep))]
            selected = [dict(entry.get("item") or {}) for entry in fallback_entries if dict(entry.get("item") or {})]
            selected_signatures = [str(entry.get("signature") or "") for entry in fallback_entries if str(entry.get("signature") or "")]
            selected_ui_like_count = int(
                sum(
                    1
                    for entry in fallback_entries
                    if bool(entry.get("ui_like_case"))
                )
            )
            if include_trace:
                for signature in selected_signatures:
                    if signature in trace_decisions:
                        trace_decisions[signature]["selected"] = True
                        trace_decisions[signature]["drop_reason"] = "retained_fallback_first"

        if selected_ui_like_count < ui_min_keep_target:
            selected_signature_set = set(selected_signatures)
            for entry in seed_ordered_entries:
                if selected_ui_like_count >= ui_min_keep_target:
                    break
                signature = str(entry.get("signature") or "")
                if not signature or signature in selected_signature_set:
                    continue
                if (
                    not bool(entry.get("ui_like_case"))
                    or bool(entry.get("has_coverage_value"))
                    or bool(entry.get("reuse_risk_hit"))
                ):
                    continue
                item = dict(entry.get("item") or {})
                if not item:
                    continue
                selected.append(item)
                selected_signatures.append(signature)
                selected_signature_set.add(signature)
                selected_ui_like_count += 1
                if include_trace:
                    trace_decisions[signature] = {
                        **dict(trace_decisions.get(signature) or {}),
                        "signature": signature,
                        "selected": True,
                        "drop_reason": "retained_min_ui_keep",
                        "drop_reason_detail": "retained_min_ui_keep",
                    }

        if not include_trace:
            return selected

        from collections import Counter

        original_counter = Counter(original_signatures)
        dedup_counter = Counter(dedup_signatures)
        dedup_dropped_signatures = sorted(
            {
                signature
                for signature, count in original_counter.items()
                if count > dedup_counter.get(signature, 0)
            }
        )
        trace_payload = {
            "decisions": trace_decisions,
            "selected_signatures": selected_signatures,
            "ordered_signatures": ordered_signatures,
            "dedup_dropped_signatures": dedup_dropped_signatures,
            "summary": {
                "input_count": int(len(original_signatures)),
                "dedup_input_count": int(len(dedup_signatures)),
                "selected_count": int(len(selected)),
                "dropped_count": int(max(0, len(dedup_signatures) - len(selected))),
                "drop_rule_cap_count": int(
                    sum(1 for item in trace_decisions.values() if item.get("drop_reason") == "drop_rule_cap")
                ),
                "rule_cap_drop_count": int(
                    sum(1 for item in trace_decisions.values() if item.get("drop_reason") == "drop_rule_cap")
                ),
                "drop_no_new_signal_count": int(
                    sum(
                        1
                        for item in trace_decisions.values()
                        if item.get("drop_reason") == "drop_no_new_rule_no_new_bucket_no_high_signal"
                    )
                ),
                "ui_min_keep_target": int(ui_min_keep_target),
                "ui_selected_after_keep": int(selected_ui_like_count),
                "semantic_duplicate_drop_count": int(
                    sum(1 for item in trace_decisions.values() if item.get("drop_reason") == "drop_semantic_duplicate")
                ),
                "ui_like_drop_count": int(
                    sum(
                        1
                        for item in trace_decisions.values()
                        if item.get("drop_reason") == "drop_ui_like_redundant_case"
                    )
                ),
                "dedup_drop_count": int(max(0, len(original_signatures) - len(dedup_signatures))),
            },
        }
        return selected, trace_payload

    def _resolve_review_llm_drop_reason_maps(
        *,
        pool_cases: list[dict[str, Any]],
        selected_cases: list[dict[str, Any]],
        raw_drop_reason_map: dict[str, str] | None,
        raw_drop_reason_origin_map: dict[str, str] | None = None,
        coverage_context: dict[str, Any] | None,
        rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
    ) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
        raw_map = {
            str(signature or "").strip(): str(reason or "").strip()
            for signature, reason in dict(raw_drop_reason_map or {}).items()
            if str(signature or "").strip()
        }
        origin_map = {
            str(signature or "").strip(): str(origin or "").strip().lower()
            for signature, origin in dict(raw_drop_reason_origin_map or {}).items()
            if str(signature or "").strip()
        }
        resolved_map: dict[str, str] = {}
        source_map: dict[str, str] = {}
        evidence_map: dict[str, Any] = {}
        candidate_items = [item for item in pool_cases if isinstance(item, dict)]
        selected_items = [item for item in selected_cases if isinstance(item, dict)]
        selected_signatures = {_signature(item) for item in selected_items if _signature(item)}
        semantic_duplicate_threshold = 0.82

        selected_entries: list[dict[str, Any]] = []
        selected_by_bucket: dict[str, list[dict[str, Any]]] = {}
        for item in selected_items:
            signature = _signature(item)
            if not signature:
                continue
            rule_keys = list(_extract_rule_keys(item))
            selected_rank = _rank_review_case_for_fill(
                item,
                coverage_context=coverage_context,
                rule_diagnostics=rule_diagnostics,
            )
            entry = {
                "signature": signature,
                "case_id": _review_case_id(item),
                "bucket": _coverage_bucket(item),
                "semantic_signature": _semantic_signature(item, rule_keys),
                "semantic_tokens": _semantic_tokenize(
                    " ".join(
                        [
                            str(item.get("description") or ""),
                            str(item.get("expected_result") or ""),
                            str(item.get("test_input") or ""),
                            " ".join([str(x) for x in item.get("steps", [])])
                            if isinstance(item.get("steps"), list)
                            else "",
                        ]
                    )
                ),
                "rank_tuple": tuple(int(x) for x in selected_rank),
            }
            selected_entries.append(entry)
            selected_by_bucket.setdefault(str(entry.get("bucket") or ""), []).append(entry)

        for item in candidate_items:
            signature = _signature(item)
            if not signature or signature in selected_signatures:
                continue

            raw_reason = str(raw_map.get(signature) or "").strip()

            score_profile = score_case_priority(
                item,
                coverage_context=coverage_context,
                rule_diagnostics=rule_diagnostics,
            )
            missing_rule_hits = [str(x) for x in (score_profile.get("missing_rule_hits") or []) if str(x).strip()]
            core_rule_hits = [str(x) for x in (score_profile.get("core_rule_hits") or []) if str(x).strip()]
            unique_coverage_hits = [str(x) for x in (score_profile.get("unique_coverage_hits") or []) if str(x).strip()]
            coverage_gain_score = int(score_profile.get("coverage_gain_score") or 0)
            has_coverage_signal = bool(
                missing_rule_hits or core_rule_hits or unique_coverage_hits or coverage_gain_score > 0
            )
            reuse_risk_hit = bool(score_profile.get("reuse_risk_hit"))
            high_signal_seed = bool(_is_high_signal(item, score_profile))
            has_high_signal = bool(high_signal_seed or reuse_risk_hit)
            focus_score = int(_focus_score(item))
            bucket = _coverage_bucket(item)
            priority = str(item.get("priority") or "").strip().upper()
            moderate_signal = bool(priority in {"P0", "P1"} or focus_score >= 1 or coverage_gain_score > 0)
            bucket_selected = [entry for entry in (selected_by_bucket.get(bucket) or []) if isinstance(entry, dict)]

            candidate_rank_tuple = tuple(
                int(x)
                for x in _rank_review_case_for_fill(
                    item,
                    coverage_context=coverage_context,
                    rule_diagnostics=rule_diagnostics,
                )
            )
            best_bucket_rank = max(
                (tuple(int(x) for x in (entry.get("rank_tuple") or ())) for entry in bucket_selected),
                default=(),
            )
            has_competition_signal = bool(bucket_selected and best_bucket_rank and best_bucket_rank > candidate_rank_tuple)
            competition_only_signal = bool(
                has_competition_signal and not has_coverage_signal and not has_high_signal and not reuse_risk_hit
            )
            has_positive_evidence = bool(has_coverage_signal or has_high_signal or has_competition_signal)

            base_evidence = {
                "bucket": bucket,
                "priority": priority,
                "focus_score": int(focus_score),
                "coverage_gain_score": int(coverage_gain_score),
                "selected_case_ids": [
                    str(entry.get("case_id") or "") for entry in bucket_selected if str(entry.get("case_id") or "")
                ][:3],
                "selected_count_in_bucket": int(len(bucket_selected)),
                "has_positive_evidence": bool(has_positive_evidence),
                "has_coverage_signal": bool(has_coverage_signal),
                "has_high_signal": bool(has_high_signal),
                "has_competition_signal": bool(has_competition_signal),
                "missing_rule_hits": missing_rule_hits,
                "core_rule_hits": core_rule_hits,
                "unique_coverage_hits": unique_coverage_hits,
                "reuse_risk_hit": bool(reuse_risk_hit),
            }

            if raw_reason and raw_reason.lower() not in {"unspecified", "unknown", "other"}:
                llm_reason = _normalize_review_llm_reason(raw_reason)
                llm_reason_lower = llm_reason.lower()
                adjusted_reason = llm_reason
                adjustment_rule = ""
                reason_origin = str(origin_map.get(signature) or "llm").strip().lower()
                if reason_origin not in {"llm", "fallback_llm"}:
                    reason_origin = "llm"
                if llm_reason_lower == "coverage_redundant":
                    if has_coverage_signal:
                        adjusted_reason = "coverage_protected_omitted"
                        adjustment_rule = "llm_coverage_redundant_with_coverage_signal"
                    elif competition_only_signal and moderate_signal:
                        adjusted_reason = "selection_tradeoff_omitted"
                        adjustment_rule = "llm_coverage_redundant_with_competition_only_signal"
                    elif has_high_signal:
                        adjusted_reason = "high_signal_omitted"
                        adjustment_rule = "llm_coverage_redundant_with_high_signal"
                elif llm_reason_lower == "low_value":
                    if has_coverage_signal:
                        adjusted_reason = "coverage_protected_omitted"
                        adjustment_rule = "llm_low_value_with_coverage_signal"
                    elif has_competition_signal and moderate_signal:
                        adjusted_reason = "selection_tradeoff_omitted"
                        adjustment_rule = "llm_low_value_with_competition_signal"
                    elif has_high_signal:
                        adjusted_reason = "high_signal_omitted"
                        adjustment_rule = "llm_low_value_with_high_signal"

                if adjusted_reason != llm_reason:
                    resolved_map[signature] = str(adjusted_reason)
                    source_map[signature] = reason_origin if reason_origin == "fallback_llm" else "deterministic_backfill"
                    evidence_map[signature] = {
                        **base_evidence,
                        "reason_from": "llm",
                        "reason_adjusted_from": llm_reason,
                        "reason_adjusted_to": str(adjusted_reason),
                        "reason_adjustment_rule": str(adjustment_rule),
                        "reason_origin": str(reason_origin),
                    }
                else:
                    resolved_map[signature] = llm_reason
                    source_map[signature] = str(reason_origin)
                    evidence_map[signature] = {
                        **base_evidence,
                        "reason_from": "llm",
                        "reason_origin": str(reason_origin),
                    }
                continue

            # Priority order:
            # coverage_redundant -> duplicate -> low_value -> coverage_protected_omitted
            # -> high_signal_omitted -> selection_tradeoff_omitted -> fallback_unspecified
            if (
                bucket_selected
                and not has_coverage_signal
                and not has_high_signal
                and not reuse_risk_hit
                and not competition_only_signal
            ):
                resolved_map[signature] = "coverage_redundant"
                source_map[signature] = "deterministic_backfill"
                evidence_map[signature] = dict(base_evidence)
                continue

            rule_keys = list(_extract_rule_keys(item))
            semantic_signature = _semantic_signature(item, rule_keys)
            semantic_tokens = _semantic_tokenize(
                " ".join(
                    [
                        str(item.get("description") or ""),
                        str(item.get("expected_result") or ""),
                        str(item.get("test_input") or ""),
                        " ".join([str(x) for x in item.get("steps", [])]) if isinstance(item.get("steps"), list) else "",
                    ]
                )
            )
            duplicate_match: dict[str, Any] | None = None
            duplicate_similarity = 0.0
            for selected_entry in selected_entries:
                selected_signature = str(selected_entry.get("semantic_signature") or "")
                selected_tokens = set(selected_entry.get("semantic_tokens") or set())
                similarity = _jaccard_similarity(semantic_tokens, selected_tokens)
                if semantic_signature and semantic_signature == selected_signature:
                    duplicate_match = selected_entry
                    duplicate_similarity = 1.0
                    break
                if similarity >= semantic_duplicate_threshold and similarity >= duplicate_similarity:
                    duplicate_match = selected_entry
                    duplicate_similarity = float(similarity)
            if duplicate_match:
                resolved_map[signature] = "duplicate"
                source_map[signature] = "deterministic_backfill"
                evidence_map[signature] = {
                    **base_evidence,
                    "duplicate_of_case_id": str(duplicate_match.get("case_id") or ""),
                    "duplicate_bucket": str(duplicate_match.get("bucket") or ""),
                    "similarity": round(float(duplicate_similarity), 4),
                }
                continue

            if (
                not has_coverage_signal
                and not has_high_signal
                and not reuse_risk_hit
                and not moderate_signal
                and not has_competition_signal
            ):
                resolved_map[signature] = "low_value"
                source_map[signature] = "deterministic_backfill"
                evidence_map[signature] = dict(base_evidence)
                continue

            if has_coverage_signal:
                resolved_map[signature] = "coverage_protected_omitted"
                source_map[signature] = "deterministic_backfill"
                evidence_map[signature] = dict(base_evidence)
                continue

            if has_high_signal:
                resolved_map[signature] = "high_signal_omitted"
                source_map[signature] = "deterministic_backfill"
                evidence_map[signature] = dict(base_evidence)
                continue

            if competition_only_signal and moderate_signal:
                resolved_map[signature] = "selection_tradeoff_omitted"
                source_map[signature] = "deterministic_backfill"
                evidence_map[signature] = dict(base_evidence)
                continue

            resolved_map[signature] = "fallback_unspecified"
            source_map[signature] = "deterministic_backfill"
            evidence_map[signature] = dict(base_evidence)

        return resolved_map, source_map, evidence_map

    parsed_result = clean_and_parse_json_fn(full_content)
    parsed_result = normalize_json_structure_fn(parsed_result)
    if not isinstance(parsed_result, list):
        parsed_result = []
    parsed_result = deduplicate_test_cases_fn(parsed_result)
    parsed_result = apply_priority_semantics_to_cases([x for x in parsed_result if isinstance(x, dict)], attach_debug=False)
    parsed_result, initial_filter_stats = _filter_low_quality_cases_with_stats(
        parsed_result,
        requirement_text=requirement,
    )
    low_quality_structural_dropped_total = int(initial_filter_stats.get("invalid_structure_dropped") or 0) + int(
        initial_filter_stats.get("weak_case_dropped") or 0
    )
    semantic_dedup_dropped_total = int(initial_filter_stats.get("semantic_dedup_dropped") or 0)
    governance_hard_drop_total = int(initial_filter_stats.get("governance_hard_drop") or 0)
    low_quality_dropped_total = int(low_quality_structural_dropped_total)
    postprocess_filter_drop_total = int(initial_filter_stats.get("total_dropped") or 0)
    low_quality_drop_details: list[dict[str, Any]] = [
        dict(item)
        for item in (initial_filter_stats.get("dropped_details") or [])
        if isinstance(item, dict)
    ]

    stage_counts = {
        "primary": len(parsed_result),
        "gap": 0,
        "review": 0,
    }
    gap_attempts = 0
    gap_remaining_after_attempts = 0
    gap_stopped_by_provider_error = False
    candidate_count_before_review = len([x for x in parsed_result if isinstance(x, dict)])
    review_selected_count = len([x for x in parsed_result if isinstance(x, dict)])
    append_target_count = 0
    if append:
        append_target_count = max(0, int(expected_count or 0) - int(existing_unique_count or 0))
    reference_count_effective = max(
        1,
        int(append_target_count or 0) if append and int(append_target_count or 0) > 0 else int(expected_count or 1),
    )
    append_final_cap_count = int(append_target_count or 0) if append and int(append_target_count or 0) > 0 else 0
    append_cap_drop_total = 0
    append_cap_drop_signatures: set[str] = set()
    final_description_dedup_drop_signatures: set[str] = set()
    flow_governance_summary: dict[str, Any] = {}
    execution_plan_summary: dict[str, Any] = {}
    review_candidate_cases: list[dict[str, Any]] = []
    review_selection_input: list[dict[str, Any]] = []
    review_gate_trace: dict[str, Any] = {}
    review_llm_applied = False
    review_llm_pool_count = 0
    review_llm_selected_signatures: set[str] = set()
    review_constraint_retained_signatures: set[str] = set()
    review_llm_drop_reason_raw_map: dict[str, str] = {}
    review_llm_drop_reason_raw_origin_map: dict[str, str] = {}
    review_llm_drop_reason_map: dict[str, str] = {}
    review_llm_drop_reason_source_map: dict[str, str] = {}
    review_llm_drop_reason_evidence_map: dict[str, Any] = {}
    review_constraint_reason_map: dict[str, str] = {}
    review_target_min_count = 1
    review_target_max_count = 1
    review_candidate_coverage_context: dict[str, Any] = {}
    review_candidate_rule_diagnostics: dict[str, Any] = {"rule_diagnostics": []}
    mode_rank = {
        "core_smoke": 0,
        "standard_regression": 1,
        "expanded_regression": 2,
        "full_functional_regression": 3,
    }
    effective_generation_coverage_mode = str(generation_coverage_mode or "")
    expected_count_mode = ""
    if int(expected_count or 0) >= 80:
        expected_count_mode = "full_functional_regression"
    elif int(expected_count or 0) >= 60:
        expected_count_mode = "expanded_regression"
    elif int(expected_count or 0) > 0:
        expected_count_mode = "standard_regression"
    if mode_rank.get(expected_count_mode, -1) > mode_rank.get(effective_generation_coverage_mode, -1):
        effective_generation_coverage_mode = expected_count_mode
    if effective_generation_coverage_mode not in mode_rank:
        effective_generation_coverage_mode = expected_count_mode or "standard_regression"
    generation_coverage_mode = effective_generation_coverage_mode
    review_shortfall_detected = False
    review_shortfall_before_count = 0
    review_shortfall_recovered_count = 0
    review_post_rerank_floor_count = 1
    review_post_rerank_recovered_count = 0
    final_target_floor_count = 0
    final_floor_recovered_count = 0
    final_floor_recovery_applied = False
    final_floor_recovery_attempted = False
    final_floor_recovery_reason = ""
    final_confirmed_conflict_drop_count = 0
    final_shortfall_supplement_attempted = False
    final_shortfall_supplement_applied = False
    final_shortfall_supplement_count = 0
    final_shortfall_supplement_reason = ""
    final_order_flow_governance_summary: dict[str, Any] = {}
    final_case_structure: dict[str, Any] = {}
    final_independent_case_structure: dict[str, Any] = {}
    review_fill_source = "none"
    review_must_keep_signatures: set[str] = set()
    review_must_keep_reason_map: dict[str, list[str]] = {}
    review_decision_table: list[dict[str, Any]] = []
    review_decision_summary: dict[str, Any] = {}
    review_llm_runtime_debug: dict[str, Any] = {
        "invoked": False,
        "pool_non_empty": False,
        "pool_size": 0,
        "primary_model": "",
        "primary_invalid_reason": "",
        "primary_reason_incomplete": False,
        "primary_dropped_reason_count": 0,
        "primary_dropped_reason_payload_count": 0,
        "primary_reason_coverage_ratio": 0.0,
        "response_len": 0,
        "response_preview": "",
        "primary_response_metadata": {},
        "primary_compact_retry_invoked": False,
        "primary_compact_retry_model": "",
        "primary_compact_retry_invalid_reason": "",
        "primary_compact_retry_response_len": 0,
        "primary_compact_retry_response_metadata": {},
        "retry_invoked": False,
        "retry_reason": "",
        "retry_model": "",
        "retry_attempts": [],
        "retry_response_len": 0,
        "retry_parse_success": False,
        "retry_mapped_count": 0,
        "retry_payload_has_selection_signal": False,
        "parsed_type": "",
        "parsed_len": 0,
        "mapped_count": 0,
        "mapped_signature_count": 0,
        "dropped_reason_count": 0,
        "dropped_reason_payload_count": 0,
        "dropped_reason_unmapped_count": 0,
        "payload_has_selection_signal": False,
        "final_selected_and_dropped_overlap_count": 0,
        "final_selected_and_dropped_overlap_case_ids": [],
        "final_payload_consistent": True,
        "reason_repair_invoked": False,
        "reason_repair_model": "",
        "reason_repair_candidate_count": 0,
        "reason_repair_response_len": 0,
        "reason_repair_mapped_count": 0,
        "reason_repair_invalid_reason": "",
        "reason_repair_response_metadata": {},
        "final_source": "review_selector",
        "applied": False,
        "applied_reason": "",
        "exception": "",
        "forced_reset_by_fallback": False,
        "fallback_reason_incomplete": False,
        "final_reason_incomplete": False,
        "final_reason_coverage_ratio": 0.0,
    }
    judge_summary_payload: dict[str, Any] = {}
    judge_decision_table_payload: list[dict[str, Any]] = []

    current_total = _merged_unique_total(parsed_result)
    if current_total == 0 and int(expected_count or 0) > 0:
        yield "@@STATUS@@:Initial streaming result is empty, trying one non-stream rescue pass...\n"
        rescue_prompt = f"""
{base_prompt}

RESCUE INSTRUCTION:
- Quantity is reference-only; prioritize quality and coverage gain.
- Stop when additional cases add no new information.
- Return ONLY strict JSON array.
"""
        try:
            rescue_raw = client.generate_response(requirement, rescue_prompt, db=db, task_type="generation")
            rescue_parsed = clean_and_parse_json_fn(str(rescue_raw or ""))
            rescue_parsed = normalize_json_structure_fn(rescue_parsed)
            if isinstance(rescue_parsed, list) and rescue_parsed:
                rescue_parsed = deduplicate_test_cases_fn(rescue_parsed)
                rescue_parsed = apply_priority_semantics_to_cases(
                    [x for x in rescue_parsed if isinstance(x, dict)],
                    attach_debug=False,
                )
                rescue_parsed, rescue_filter_stats = _filter_low_quality_cases_with_stats(
                    rescue_parsed,
                    requirement_text=requirement,
                )
                low_quality_structural_dropped_total += int(rescue_filter_stats.get("invalid_structure_dropped") or 0) + int(
                    rescue_filter_stats.get("weak_case_dropped") or 0
                )
                semantic_dedup_dropped_total += int(rescue_filter_stats.get("semantic_dedup_dropped") or 0)
                governance_hard_drop_total += int(rescue_filter_stats.get("governance_hard_drop") or 0)
                low_quality_dropped_total = int(low_quality_structural_dropped_total)
                postprocess_filter_drop_total += int(rescue_filter_stats.get("total_dropped") or 0)
                low_quality_drop_details.extend(
                    dict(item)
                    for item in (rescue_filter_stats.get("dropped_details") or [])
                    if isinstance(item, dict)
                )
                parsed_result = rescue_parsed
                stage_counts["primary"] = len(parsed_result)
                current_total = _merged_unique_total(parsed_result)
                yield f"@@STATUS@@:Rescue succeeded, recovered {len(parsed_result)} cases.\n"
        except Exception as rescue_err:
            yield f"@@STATUS@@:Rescue failed ({str(rescue_err)}), continue pipeline.\n"

    normalized_mode = str(generation_mode or "").strip().lower()
    if normalized_mode not in {"single_pass", "multi_pass", "biz_key_multi_pass"}:
        normalized_mode = "multi_pass" if bool(multi_pass) else "single_pass"

    if normalized_mode in {"multi_pass", "biz_key_multi_pass"} and isinstance(parsed_result, list):
        coverage_primary = analyze_coverage(requirement, [x for x in parsed_result if isinstance(x, dict)])
        missing_rules = list(coverage_primary.get("missing_rules") or [])
        diagnostics = [item for item in (coverage_primary.get("rule_diagnostics") or []) if isinstance(item, dict)]
        has_missing_types = any(bool(item.get("missing_types")) for item in diagnostics)

        need_gap = bool(missing_rules) or has_missing_types
        if need_gap:
            yield "@@STATUS@@:[multi-pass] Stage 2/3 gap supplement started...\n"
            before_gap = len(parsed_result)
            supplement_attempt = 0

            while supplement_attempt < 3 and (missing_rules or has_missing_types):
                supplement_attempt += 1
                yield f"@@STATUS@@:Gap supplement attempt #{supplement_attempt}...\n"

                supplement_source: list[dict[str, Any]] = []
                if append and isinstance(existing_cases, list):
                    supplement_source.extend([x for x in existing_cases if isinstance(x, dict)])
                supplement_source.extend([x for x in parsed_result if isinstance(x, dict)])

                closed_loop_instruction = build_supplement_closed_loop_instruction_fn(
                    all_cases=supplement_source,
                    requirement=requirement,
                    infer_case_kind_fn=infer_case_kind_fn,
                )
                gap_prompt = build_gap_fill_prompt(
                    requirement_context=requirement,
                    existing_cases=supplement_source,
                    coverage_result=coverage_primary,
                    missing_rules=missing_rules,
                    current_biz_key=current_biz_key,
                    pretty_json=False,
                )
                system_prompt = f"""
{gap_prompt}

CLOSED_LOOP_HINT:
{closed_loop_instruction}

APPEND_POLICY: only append if new cases add coverage gain; otherwise return [].
"""

                extra_content = ""
                extra_stream = client.generate_response_stream(requirement, system_prompt, task_type="generation")
                provider_error = None
                for chunk in extra_stream:
                    extra_content += chunk
                    yield chunk
                    if chunk.startswith("Error:") or chunk.startswith("[棰濆害鑰楀敖]") or chunk.startswith("Exception occurred:"):
                        provider_error = chunk
                        break
                if provider_error:
                    yield "\n@@STATUS@@:Generation failed\n"
                    yield f"{provider_error}\n"
                    gap_stopped_by_provider_error = True
                    break

                try:
                    extra_parsed = clean_and_parse_json_fn(extra_content)
                    extra_parsed = normalize_json_structure_fn(extra_parsed)
                    if isinstance(extra_parsed, list) and extra_parsed:
                        extra_parsed = deduplicate_test_cases_fn(extra_parsed)
                        extra_parsed = apply_priority_semantics_to_cases(
                            [x for x in extra_parsed if isinstance(x, dict)],
                            attach_debug=False,
                        )
                        extra_parsed, extra_filter_stats = _filter_low_quality_cases_with_stats(
                            extra_parsed,
                            requirement_text=requirement,
                        )
                        low_quality_structural_dropped_total += int(extra_filter_stats.get("invalid_structure_dropped") or 0) + int(
                            extra_filter_stats.get("weak_case_dropped") or 0
                        )
                        semantic_dedup_dropped_total += int(extra_filter_stats.get("semantic_dedup_dropped") or 0)
                        governance_hard_drop_total += int(extra_filter_stats.get("governance_hard_drop") or 0)
                        low_quality_dropped_total = int(low_quality_structural_dropped_total)
                        postprocess_filter_drop_total += int(extra_filter_stats.get("total_dropped") or 0)
                        low_quality_drop_details.extend(
                            dict(item)
                            for item in (extra_filter_stats.get("dropped_details") or [])
                            if isinstance(item, dict)
                        )
                        parsed_result.extend([x for x in extra_parsed if isinstance(x, dict)])
                        parsed_result = normalize_json_structure_fn(parsed_result)
                        parsed_result = deduplicate_test_cases_fn(parsed_result)
                except Exception:
                    pass

                coverage_primary = analyze_coverage(requirement, parsed_result)
                missing_rules = list(coverage_primary.get("missing_rules") or [])
                diagnostics = [item for item in (coverage_primary.get("rule_diagnostics") or []) if isinstance(item, dict)]
                has_missing_types = any(bool(item.get("missing_types")) for item in diagnostics)
                if not missing_rules and not has_missing_types:
                    break

            gap_attempts = supplement_attempt
            gap_remaining_after_attempts = int(len(missing_rules) + (1 if has_missing_types else 0))
            stage_counts["gap"] = max(0, len(parsed_result) - before_gap)

        yield "@@STATUS@@:[multi-pass] Stage 3/3 review selection started...\n"

        candidate_cases = [x for x in parsed_result if isinstance(x, dict)]
        candidate_cases, review_filter_stats = _filter_low_quality_cases_with_stats(
            candidate_cases,
            requirement_text=requirement,
        )
        low_quality_structural_dropped_total += int(review_filter_stats.get("invalid_structure_dropped") or 0) + int(
            review_filter_stats.get("weak_case_dropped") or 0
        )
        semantic_dedup_dropped_total += int(review_filter_stats.get("semantic_dedup_dropped") or 0)
        governance_hard_drop_total += int(review_filter_stats.get("governance_hard_drop") or 0)
        low_quality_dropped_total = int(low_quality_structural_dropped_total)
        postprocess_filter_drop_total += int(review_filter_stats.get("total_dropped") or 0)
        low_quality_drop_details.extend(
            dict(item)
            for item in (review_filter_stats.get("dropped_details") or [])
            if isinstance(item, dict)
        )
        candidate_count_before_review = len(candidate_cases)
        review_candidate_cases = list(candidate_cases)

        review_candidate_coverage_context = analyze_coverage(requirement, candidate_cases)
        review_candidate_rule_diagnostics = {
            "rule_diagnostics": review_candidate_coverage_context.get("rule_diagnostics") or []
        }
        must_keep_cases: list[dict[str, Any]] = []
        llm_pool_cases: list[dict[str, Any]] = []
        must_keep_seen_signatures: set[str] = set()
        for case in candidate_cases:
            if not isinstance(case, dict):
                continue
            score_profile = score_case_priority(
                case,
                coverage_context=review_candidate_coverage_context,
                rule_diagnostics=review_candidate_rule_diagnostics,
            )
            must_keep_reasons = _review_must_keep_reasons(case, score_profile)
            signature = _signature(case)
            if must_keep_reasons:
                if signature not in must_keep_seen_signatures:
                    must_keep_cases.append(case)
                    must_keep_seen_signatures.add(signature)
                review_must_keep_signatures.add(signature)
                review_must_keep_reason_map[signature] = list(must_keep_reasons)
            else:
                llm_pool_cases.append(case)

        review_llm_pool_count = int(len(llm_pool_cases))
        review_constraints = _build_review_selection_constraints(
            llm_pool_cases,
            reference_count=int(reference_count_effective or len(llm_pool_cases) or 1),
            generation_profile=generation_coverage_profile,
        )
        review_target_min_count = int(review_constraints.get("target_min_count") or 1)
        review_target_max_count = int(review_constraints.get("target_max_count") or review_target_min_count)
        review_prompt = build_review_select_prompt(
            requirement_context=requirement,
            candidate_cases=llm_pool_cases,
            target_count=max(1, int(reference_count_effective or len(llm_pool_cases) or 1)),
            target_min_count=review_target_min_count,
            target_max_count=review_target_max_count,
            coverage_constraints=review_constraints,
            current_biz_key=current_biz_key,
            pretty_json=False,
        )
        selected_from_llm_pool: list[dict[str, Any]] = list(llm_pool_cases)
        review_llm_runtime_debug["pool_size"] = int(len(llm_pool_cases))
        review_llm_runtime_debug["pool_non_empty"] = bool(llm_pool_cases)
        review_llm_runtime_debug["prompt_chars"] = int(len(review_prompt or ""))
        review_llm_runtime_debug["prompt_est_tokens"] = int(round(len(review_prompt or "") / 4))
        review_llm_runtime_debug["candidate_count"] = int(len(llm_pool_cases))
        review_llm_runtime_debug["append_target_count"] = int(append_target_count or 0)
        review_llm_runtime_debug["append_final_cap_count"] = int(append_final_cap_count or 0)
        try:
            if llm_pool_cases:
                review_llm_runtime_debug["invoked"] = True

                def _analyze_review_payload(response_text: str, *, reason_origin: str = "llm") -> dict[str, Any]:
                    payload_text = str(response_text or "")
                    payload_trimmed = payload_text.strip()
                    reviewed_payload_local = clean_and_parse_json_fn(payload_text)
                    parsed_type_local = type(reviewed_payload_local).__name__
                    parsed_len_local = int(len(reviewed_payload_local)) if isinstance(reviewed_payload_local, (list, dict)) else 0
                    parse_success_local = not (
                        isinstance(reviewed_payload_local, dict)
                        and bool(str(reviewed_payload_local.get("error") or "").strip())
                    )
                    if isinstance(reviewed_payload_local, list):
                        reviewed_payload_local = normalize_json_structure_fn(reviewed_payload_local)
                        parsed_type_local = type(reviewed_payload_local).__name__
                        parsed_len_local = int(len(reviewed_payload_local)) if isinstance(reviewed_payload_local, list) else 0
                    (
                        mapped_local,
                        mapped_signatures_local,
                        dropped_reason_map_local,
                        dropped_reason_origin_map_local,
                    ) = _map_review_selection_with_reasons(
                        llm_pool_cases,
                        reviewed_payload_local,
                        reason_origin=reason_origin,
                    )
                    dropped_reason_payload_count_local = 0
                    dropped_payload_local = reviewed_payload_local.get("dropped") if isinstance(reviewed_payload_local, dict) else None
                    if isinstance(dropped_payload_local, list):
                        for dropped_item in dropped_payload_local:
                            if not isinstance(dropped_item, dict):
                                continue
                            case_id_local = str(dropped_item.get("case_id") or dropped_item.get("id") or "").strip()
                            reason_local = str(dropped_item.get("reason") or "").strip()
                            if case_id_local and reason_local:
                                dropped_reason_payload_count_local += 1
                    dropped_reason_unmapped_count_local = max(
                        0,
                        int(dropped_reason_payload_count_local) - int(len(dropped_reason_map_local or {})),
                    )
                    payload_has_selection_signal_local = bool(
                        isinstance(reviewed_payload_local, dict)
                        and any(
                            key in reviewed_payload_local
                            for key in ("kept_case_ids", "selected_case_ids", "kept", "selected", "dropped")
                        )
                    )
                    invalid_reason_local = ""
                    if not payload_trimmed:
                        invalid_reason_local = "empty_response"
                    elif payload_trimmed.startswith("Error:") or payload_trimmed.startswith("Exception"):
                        invalid_reason_local = "error_response"
                    elif not parse_success_local:
                        invalid_reason_local = "schema_parse_error"
                    elif parsed_type_local not in {"dict", "list"}:
                        invalid_reason_local = "schema_not_dict_or_list"
                    elif not mapped_local and not payload_has_selection_signal_local:
                        invalid_reason_local = "no_mapped_and_no_selection_signal"
                    elif not mapped_local:
                        invalid_reason_local = "no_mapped_ids"
                    return {
                        "payload": reviewed_payload_local,
                        "parsed_type": str(parsed_type_local),
                        "parsed_len": int(parsed_len_local),
                        "parse_success": bool(parse_success_local),
                        "mapped": [item for item in mapped_local if isinstance(item, dict)],
                        "mapped_signatures": set(mapped_signatures_local or set()),
                        "dropped_reason_map": dict(dropped_reason_map_local or {}),
                        "dropped_reason_origin_map": dict(dropped_reason_origin_map_local or {}),
                        "dropped_reason_payload_count": int(dropped_reason_payload_count_local),
                        "dropped_reason_unmapped_count": int(dropped_reason_unmapped_count_local),
                        "payload_has_selection_signal": bool(payload_has_selection_signal_local),
                        "invalid_reason": str(invalid_reason_local),
                    }

                def _compact_review_retry_prompt() -> str:
                    def _clip(value: Any, limit: int = 180) -> str:
                        text = str(value or "").strip()
                        return text[:limit]

                    compact_cases: list[dict[str, Any]] = []
                    for item in llm_pool_cases:
                        if not isinstance(item, dict):
                            continue
                        case_id = _review_case_id(item)
                        if not case_id:
                            continue
                        compact_cases.append(
                            {
                                "id": case_id,
                                "module": _clip(item.get("test_module"), 80),
                                "description": _clip(item.get("description"), 180),
                                "expected_result": _clip(item.get("expected_result"), 180),
                                "priority": _clip(item.get("priority_final") or item.get("priority"), 12),
                            }
                        )
                    candidate_ids = [str(item.get("id") or "") for item in compact_cases if item.get("id")]
                    return (
                        "REVIEW COMPACT RETRY.\n"
                        "The previous review response had no usable final answer. Do not reason aloud.\n"
                        "Return STRICT compact JSON only, no prose, no markdown, no code fences.\n"
                        f"Keep between {review_target_min_count} and {review_target_max_count} cases when possible.\n"
                        "Schema:\n"
                        '{"kept_case_ids":["TC-001"],"dropped":[{"case_id":"TC-002","reason":"coverage_redundant"}]}\n'
                        "Allowed reasons: coverage_redundant, duplicate, low_value, coverage_protected_omitted, "
                        "high_signal_omitted, selection_tradeoff_omitted, fallback_unspecified.\n"
                        "Case ids must come from this list only:\n"
                        f"{json.dumps(candidate_ids[:200], ensure_ascii=False)}\n"
                        "Compact candidate facts:\n"
                        f"{json.dumps(compact_cases[:200], ensure_ascii=False, separators=(',', ':'))}"
                    )

                try:
                    review_llm_runtime_debug["primary_model"] = str(
                        client.select_model(review_prompt, "review")
                    )
                except Exception:
                    review_llm_runtime_debug["primary_model"] = ""
                review_response = client.generate_response(
                    review_prompt,
                    "You are a QA Auditor.",
                    db=db,
                    task_type="review",
                )
                review_response_text = str(review_response or "")
                review_llm_runtime_debug["primary_response_metadata"] = dict(
                    getattr(client, "last_response_metadata", {}) or {}
                )
                primary_result = _analyze_review_payload(review_response_text, reason_origin="llm")
                review_llm_runtime_debug["response_len"] = int(len(review_response_text))
                review_llm_runtime_debug["response_preview"] = review_response_text[:500]
                review_llm_runtime_debug["parsed_type"] = str(primary_result.get("parsed_type") or "")
                review_llm_runtime_debug["parsed_len"] = int(primary_result.get("parsed_len") or 0)
                review_llm_runtime_debug["mapped_count"] = int(len(primary_result.get("mapped") or []))
                review_llm_runtime_debug["mapped_signature_count"] = int(len(primary_result.get("mapped_signatures") or set()))
                review_llm_runtime_debug["dropped_reason_count"] = int(len(primary_result.get("dropped_reason_map") or {}))
                review_llm_runtime_debug["dropped_reason_payload_count"] = int(primary_result.get("dropped_reason_payload_count") or 0)
                review_llm_runtime_debug["dropped_reason_unmapped_count"] = int(
                    primary_result.get("dropped_reason_unmapped_count") or 0
                )
                primary_mapped_count = int(len(primary_result.get("mapped") or []))
                primary_dropped_reason_count = int(len(primary_result.get("dropped_reason_map") or {}))
                primary_dropped_reason_payload_count = int(primary_result.get("dropped_reason_payload_count") or 0)
                review_llm_runtime_debug["primary_dropped_reason_count"] = int(primary_dropped_reason_count)
                review_llm_runtime_debug["primary_dropped_reason_payload_count"] = int(primary_dropped_reason_payload_count)
                review_llm_runtime_debug["primary_reason_incomplete"] = bool(
                    primary_mapped_count > 0 and primary_dropped_reason_count <= 0
                )
                review_llm_runtime_debug["primary_reason_coverage_ratio"] = (
                    round(float(primary_dropped_reason_count) / float(primary_dropped_reason_payload_count), 4)
                    if primary_dropped_reason_payload_count > 0
                    else 0.0
                )
                review_llm_runtime_debug["payload_has_selection_signal"] = bool(
                    primary_result.get("payload_has_selection_signal")
                )
                review_llm_runtime_debug["primary_invalid_reason"] = str(primary_result.get("invalid_reason") or "")

                final_result = dict(primary_result)
                final_source = "primary_llm"
                retry_reason = str(primary_result.get("invalid_reason") or "")
                if retry_reason:
                    review_llm_runtime_debug["retry_invoked"] = True
                    review_llm_runtime_debug["retry_reason"] = retry_reason
                    primary_model_for_retry = str(review_llm_runtime_debug.get("primary_model") or "").strip()
                    if (
                        retry_reason in {"empty_response", "error_response"}
                        and primary_model_for_retry
                        and str(review_response_text or "").startswith("Error: Empty response")
                    ):
                        review_llm_runtime_debug["primary_compact_retry_invoked"] = True
                        review_llm_runtime_debug["primary_compact_retry_model"] = primary_model_for_retry
                        compact_retry_text = str(
                            client.generate_response(
                                _compact_review_retry_prompt(),
                                "You are a QA Auditor. Return strict JSON only.",
                                db=db,
                                task_type="review",
                                model=primary_model_for_retry,
                                max_tokens=4096,
                            )
                            or ""
                        )
                        review_llm_runtime_debug["primary_compact_retry_response_len"] = int(len(compact_retry_text))
                        review_llm_runtime_debug["primary_compact_retry_response_metadata"] = dict(
                            getattr(client, "last_response_metadata", {}) or {}
                        )
                        compact_retry_result = _analyze_review_payload(
                            compact_retry_text,
                            reason_origin="primary_compact_retry",
                        )
                        compact_retry_invalid_reason = str(compact_retry_result.get("invalid_reason") or "")
                        review_llm_runtime_debug["primary_compact_retry_invalid_reason"] = compact_retry_invalid_reason
                        if not compact_retry_invalid_reason:
                            review_response_text = compact_retry_text
                            final_result = compact_retry_result
                            final_source = "primary_compact_retry"
                            retry_reason = ""

                if retry_reason:
                    fallback_models: list[str] = []
                    primary_model_name = str(review_llm_runtime_debug.get("primary_model") or "").strip().lower()
                    if "deepseek" in primary_model_name and primary_model_name != "deepseek-chat":
                        fallback_models.append("deepseek-chat")
                    for candidate in (
                        str(getattr(client, "model", "") or "").strip(),
                        str(getattr(client, "turbo_model", "") or "").strip(),
                    ):
                        if candidate:
                            fallback_models.append(candidate)

                    candidate_ids = [
                        _review_case_id(item)
                        for item in llm_pool_cases
                        if isinstance(item, dict) and _review_case_id(item)
                    ]
                    candidate_ids = candidate_ids[:200]
                    repair_prompt = (
                        f"{review_prompt}\n\n"
                        "PROTOCOL FIX (MANDATORY):\n"
                        "- Previous output was invalid for downstream selection mapping.\n"
                        "- Return STRICT JSON only; no prose, no markdown, no code fences.\n"
                        "- Schema MUST be:\n"
                        "{\n"
                        '  "kept_case_ids": ["<case_id>"],\n'
                        '  "dropped": [{"case_id": "<case_id>", "reason": "<reason>"}]\n'
                        "}\n"
                        "- `kept_case_ids` and `dropped[*].case_id` must come from this candidate id list only:\n"
                        f"{json.dumps(candidate_ids, ensure_ascii=False)}\n"
                        "- Do not invent or rewrite case ids.\n"
                        "- `dropped[*].reason` must be ONE canonical key from:\n"
                        '  ["coverage_redundant","duplicate","low_value","coverage_protected_omitted","high_signal_omitted","selection_tradeoff_omitted","fallback_unspecified"]\n'
                    )

                    seen_fallback_models: set[str] = set()
                    for fallback_model in fallback_models:
                        model_key = str(fallback_model or "").strip()
                        if not model_key:
                            continue
                        if model_key in seen_fallback_models:
                            continue
                        seen_fallback_models.add(model_key)
                        review_response_retry = client.generate_response(
                            repair_prompt,
                            "You are a QA Auditor.",
                            db=db,
                            task_type="review",
                            model=model_key,
                        )
                        retry_text = str(review_response_retry or "")
                        retry_result = _analyze_review_payload(retry_text, reason_origin="fallback_llm")
                        retry_invalid_reason = str(retry_result.get("invalid_reason") or "")
                        review_llm_runtime_debug["retry_attempts"].append(
                            {
                                "model": model_key,
                                "response_len": int(len(retry_text)),
                                "is_error": bool(bool(retry_invalid_reason) and retry_invalid_reason in {"empty_response", "error_response"}),
                                "invalid_reason": retry_invalid_reason,
                                "parsed_type": str(retry_result.get("parsed_type") or ""),
                                "mapped_count": int(len(retry_result.get("mapped") or [])),
                                "dropped_reason_count": int(len(retry_result.get("dropped_reason_map") or {})),
                                "dropped_reason_payload_count": int(retry_result.get("dropped_reason_payload_count") or 0),
                                "dropped_reason_unmapped_count": int(
                                    retry_result.get("dropped_reason_unmapped_count") or 0
                                ),
                                "payload_has_selection_signal": bool(retry_result.get("payload_has_selection_signal")),
                            }
                        )
                        if retry_invalid_reason:
                            continue
                        review_response_text = retry_text
                        final_result = retry_result
                        final_source = "fallback_llm"
                        review_llm_runtime_debug["retry_model"] = model_key
                        break

                    review_llm_runtime_debug["retry_response_len"] = int(len(review_response_text))

                review_llm_runtime_debug["retry_parse_success"] = bool(
                    review_llm_runtime_debug.get("retry_invoked")
                    and bool(final_result.get("parse_success"))
                    and not bool(final_result.get("invalid_reason"))
                )
                review_llm_runtime_debug["retry_mapped_count"] = int(
                    len(final_result.get("mapped") or []) if bool(review_llm_runtime_debug.get("retry_invoked")) else 0
                )
                review_llm_runtime_debug["retry_payload_has_selection_signal"] = bool(
                    final_result.get("payload_has_selection_signal") if bool(review_llm_runtime_debug.get("retry_invoked")) else False
                )
                if bool(review_llm_runtime_debug.get("retry_invoked")):
                    review_llm_runtime_debug["retry_dropped_reason_count"] = int(
                        len(final_result.get("dropped_reason_map") or {})
                    )
                    review_llm_runtime_debug["retry_dropped_reason_payload_count"] = int(
                        final_result.get("dropped_reason_payload_count") or 0
                    )
                    review_llm_runtime_debug["retry_dropped_reason_unmapped_count"] = int(
                        final_result.get("dropped_reason_unmapped_count") or 0
                    )

                final_invalid_reason = str(final_result.get("invalid_reason") or "")
                review_llm_runtime_debug["final_source"] = (
                    str(final_source) if not final_invalid_reason else "review_selector"
                )
                if not final_invalid_reason:
                    selected_from_llm_pool = [item for item in (final_result.get("mapped") or []) if isinstance(item, dict)]
                    review_llm_selected_signatures = set(final_result.get("mapped_signatures") or set())
                    review_llm_drop_reason_raw_map = dict(final_result.get("dropped_reason_map") or {})
                    review_llm_drop_reason_raw_origin_map = dict(final_result.get("dropped_reason_origin_map") or {})
                    final_dropped_reason_count = int(len(review_llm_drop_reason_raw_map))
                    final_mapped_signatures = {
                        str(signature or "").strip()
                        for signature in set(final_result.get("mapped_signatures") or set())
                        if str(signature or "").strip()
                    }
                    final_dropped_signatures = {
                        str(signature or "").strip()
                        for signature in review_llm_drop_reason_raw_map.keys()
                        if str(signature or "").strip()
                    }
                    selected_and_dropped_overlap = final_mapped_signatures & final_dropped_signatures
                    signature_to_case_id = {
                        _signature(item): _review_case_id(item)
                        for item in llm_pool_cases
                        if isinstance(item, dict) and _signature(item)
                    }
                    overlap_case_ids = [
                        str(signature_to_case_id.get(signature) or "")
                        for signature in selected_and_dropped_overlap
                    ]
                    overlap_case_ids = [case_id for case_id in overlap_case_ids if case_id]
                    review_llm_runtime_debug["final_selected_and_dropped_overlap_count"] = int(
                        len(selected_and_dropped_overlap)
                    )
                    review_llm_runtime_debug["final_selected_and_dropped_overlap_case_ids"] = overlap_case_ids[:20]
                    review_llm_runtime_debug["final_payload_consistent"] = bool(
                        len(selected_and_dropped_overlap) == 0
                    )
                    review_llm_runtime_debug["final_dropped_reason_count"] = int(final_dropped_reason_count)
                    review_llm_runtime_debug["final_dropped_reason_payload_count"] = int(
                        final_result.get("dropped_reason_payload_count") or 0
                    )
                    review_llm_runtime_debug["final_dropped_reason_unmapped_count"] = int(
                        final_result.get("dropped_reason_unmapped_count") or 0
                    )
                    review_llm_applied = True
                    review_llm_runtime_debug["applied"] = True
                    review_llm_runtime_debug["applied_reason"] = (
                        "mapped_valid_payload" if final_source == "primary_llm" else "retry_payload_valid"
                    )
                    review_llm_runtime_debug["fallback_reason_incomplete"] = bool(
                        final_source == "fallback_llm" and int(final_dropped_reason_count or 0) <= 0
                    )
                else:
                    review_llm_runtime_debug["applied"] = False
                    review_llm_runtime_debug["applied_reason"] = final_invalid_reason
            else:
                review_llm_runtime_debug["applied_reason"] = "empty_llm_pool"
        except Exception:
            review_llm_runtime_debug["exception"] = str(__import__("traceback").format_exc()[-1500:])

        selected_from_llm_pool, constraint_reason_map = _enforce_review_selection_constraints(
            selected_cases=[item for item in selected_from_llm_pool if isinstance(item, dict)],
            pool_cases=[item for item in llm_pool_cases if isinstance(item, dict)],
            constraints=review_constraints,
            coverage_context=review_candidate_coverage_context,
            rule_diagnostics=review_candidate_rule_diagnostics,
        )
        review_constraint_reason_map = dict(constraint_reason_map or {})
        selected_signature_after_constraints = {
            _signature(item) for item in selected_from_llm_pool if isinstance(item, dict)
        }
        if review_llm_applied and llm_pool_cases:
            pool_by_signature = {
                _signature(item): item
                for item in llm_pool_cases
                if isinstance(item, dict) and _signature(item)
            }
            dropped_after_constraints = [
                item
                for signature, item in pool_by_signature.items()
                if signature and signature not in selected_signature_after_constraints
            ]
            missing_reason_cases = [
                item
                for item in dropped_after_constraints
                if _signature(item) and _signature(item) not in review_llm_drop_reason_raw_map
            ]
            if missing_reason_cases:
                def _reason_repair_clip(value: Any, limit: int = 160) -> str:
                    return str(value or "").strip()[:limit]

                repair_candidates: list[dict[str, Any]] = []
                for item in missing_reason_cases[:80]:
                    if not isinstance(item, dict):
                        continue
                    case_id = _review_case_id(item)
                    if not case_id:
                        continue
                    repair_candidates.append(
                        {
                            "id": case_id,
                            "module": _reason_repair_clip(item.get("test_module"), 80),
                            "description": _reason_repair_clip(item.get("description"), 180),
                            "expected_result": _reason_repair_clip(item.get("expected_result"), 180),
                            "priority": _reason_repair_clip(item.get("priority_final") or item.get("priority"), 12),
                        }
                    )
                if repair_candidates:
                    repair_ids = [str(item.get("id") or "") for item in repair_candidates if item.get("id")]
                    repair_prompt = (
                        "REVIEW REASON REPAIR ONLY.\n"
                        "The final selected case set is already fixed. Do NOT select or rewrite cases.\n"
                        "For each dropped candidate below, assign exactly one canonical drop reason.\n"
                        "Return STRICT JSON only, no prose, no markdown.\n"
                        "Schema:\n"
                        '{"dropped":[{"case_id":"TC-001","reason":"coverage_redundant"}]}\n'
                        "Allowed reasons: coverage_redundant, duplicate, low_value, selection_tradeoff_omitted.\n"
                        "Use coverage_redundant when another retained case covers the same rule or workflow value.\n"
                        "Use duplicate only for near-identical validation targets.\n"
                        "Use low_value for weak, generic, or low business-signal cases.\n"
                        "Use selection_tradeoff_omitted when the case has some value but was omitted to keep the final set concise.\n"
                        f"case_id must come from: {json.dumps(repair_ids, ensure_ascii=False)}\n"
                        f"Dropped candidates: {json.dumps(repair_candidates, ensure_ascii=False, separators=(',', ':'))}"
                    )
                    review_llm_runtime_debug["reason_repair_invoked"] = True
                    review_llm_runtime_debug["reason_repair_candidate_count"] = int(len(repair_candidates))
                    try:
                        review_llm_runtime_debug["reason_repair_model"] = str(
                            client.select_model(repair_prompt, "review")
                        )
                    except Exception:
                        review_llm_runtime_debug["reason_repair_model"] = ""
                    repair_response_text = str(
                        client.generate_response(
                            repair_prompt,
                            "You are a QA Auditor. Return strict JSON only.",
                            db=db,
                            task_type="review",
                            max_tokens=2048,
                        )
                        or ""
                    )
                    review_llm_runtime_debug["reason_repair_response_len"] = int(len(repair_response_text))
                    review_llm_runtime_debug["reason_repair_response_metadata"] = dict(
                        getattr(client, "last_response_metadata", {}) or {}
                    )
                    repair_payload = clean_and_parse_json_fn(repair_response_text)
                    repair_invalid_reason = ""
                    if not str(repair_response_text or "").strip():
                        repair_invalid_reason = "empty_response"
                    elif str(repair_response_text).startswith(("Error:", "Exception")):
                        repair_invalid_reason = "error_response"
                    elif (
                        isinstance(repair_payload, dict)
                        and bool(str(repair_payload.get("error") or "").strip())
                    ):
                        repair_invalid_reason = "schema_parse_error"
                    elif not isinstance(repair_payload, dict):
                        repair_invalid_reason = "schema_not_dict"
                    dropped_payload = repair_payload.get("dropped") if isinstance(repair_payload, dict) else None
                    if not isinstance(dropped_payload, list):
                        dropped_payload = []
                    case_by_id = {
                        _review_case_id(item): item
                        for item in missing_reason_cases
                        if isinstance(item, dict) and _review_case_id(item)
                    }
                    mapped_reason_count = 0
                    allowed_repair_reasons = {
                        "coverage_redundant",
                        "duplicate",
                        "low_value",
                        "selection_tradeoff_omitted",
                    }
                    for dropped_item in dropped_payload:
                        if not isinstance(dropped_item, dict):
                            continue
                        case_id = str(dropped_item.get("case_id") or dropped_item.get("id") or "").strip()
                        reason = str(dropped_item.get("reason") or "").strip().lower()
                        if reason not in allowed_repair_reasons:
                            continue
                        original = case_by_id.get(case_id)
                        if not isinstance(original, dict):
                            continue
                        signature = _signature(original)
                        if not signature or signature in review_llm_drop_reason_raw_map:
                            continue
                        review_llm_drop_reason_raw_map[signature] = reason
                        review_llm_drop_reason_raw_origin_map[signature] = "llm"
                        mapped_reason_count += 1
                    if mapped_reason_count <= 0 and not repair_invalid_reason:
                        repair_invalid_reason = "no_mapped_reasons"
                    review_llm_runtime_debug["reason_repair_mapped_count"] = int(mapped_reason_count)
                    review_llm_runtime_debug["reason_repair_invalid_reason"] = str(repair_invalid_reason)
                    if mapped_reason_count > 0:
                        review_llm_runtime_debug["final_dropped_reason_count"] = int(
                            len(review_llm_drop_reason_raw_map)
                        )
                        review_llm_runtime_debug["final_dropped_reason_payload_count"] = int(
                            len(review_llm_drop_reason_raw_map)
                        )
                        review_llm_runtime_debug["final_dropped_reason_unmapped_count"] = 0
        review_llm_drop_reason_map, review_llm_drop_reason_source_map, review_llm_drop_reason_evidence_map = (
            _resolve_review_llm_drop_reason_maps(
                pool_cases=[item for item in llm_pool_cases if isinstance(item, dict)],
                selected_cases=[item for item in selected_from_llm_pool if isinstance(item, dict)],
                raw_drop_reason_map=review_llm_drop_reason_raw_map,
                raw_drop_reason_origin_map=review_llm_drop_reason_raw_origin_map,
                coverage_context=review_candidate_coverage_context,
                rule_diagnostics=review_candidate_rule_diagnostics,
            )
        )
        review_constraint_retained_signatures = {
            signature
            for signature, reason in review_constraint_reason_map.items()
            if signature in selected_signature_after_constraints and str(reason or "").startswith("retained_by_constraint_")
        }
        if review_llm_applied:
            review_llm_selected_signatures = {
                signature
                for signature in review_llm_selected_signatures
                if signature and signature in {_signature(item) for item in selected_from_llm_pool if isinstance(item, dict)}
            }

        selection_input: list[dict[str, Any]] = []
        selection_seen_signatures: set[str] = set()
        for case in [*must_keep_cases, *selected_from_llm_pool]:
            if not isinstance(case, dict):
                continue
            signature = _signature(case)
            if signature in selection_seen_signatures:
                continue
            selection_seen_signatures.add(signature)
            selection_input.append(case)

        # If review output collapses below target_min_count, deterministically recover from
        # already-filtered candidate pool instead of accepting a 50->1 shortfall.
        review_shortfall_before_count = int(len(selection_input))
        if int(review_target_min_count or 1) > 0 and int(len(selection_input)) < int(review_target_min_count or 1):
            review_shortfall_detected = True
            review_fill_source = "constraint_fill"
            selection_signatures_local = set(selection_seen_signatures)
            fill_pool = [
                item
                for item in candidate_cases
                if isinstance(item, dict) and _signature(item) not in selection_signatures_local
            ]
            if bool(review_constraints.get("domain_guard_active")):
                guarded_fill_pool = [item for item in fill_pool if not _is_cross_domain_noise(item)]
                if guarded_fill_pool:
                    fill_pool = guarded_fill_pool
            fill_pool.sort(
                key=lambda item: tuple(
                    [
                        -value
                        for value in _rank_review_case_for_fill(
                            item,
                            coverage_context=review_candidate_coverage_context,
                            rule_diagnostics=review_candidate_rule_diagnostics,
                        )
                    ]
                )
                + (_review_case_id(item),)
            )
            for fill_case in fill_pool:
                if int(len(selection_input)) >= int(review_target_min_count or 1):
                    break
                signature = _signature(fill_case)
                if not signature or signature in selection_signatures_local:
                    continue
                selection_input.append(fill_case)
                selection_signatures_local.add(signature)
                review_constraint_reason_map.setdefault(signature, "retained_by_shortfall_recovery")

            review_shortfall_recovered_count = max(
                0,
                int(len(selection_input)) - int(review_shortfall_before_count),
            )
        else:
            review_shortfall_before_count = int(len(selection_input))

        review_selection_input = [x for x in selection_input if isinstance(x, dict)]
        review_selection_coverage = analyze_coverage(requirement, review_selection_input)
        rerank_result = _rerank_and_cap_by_rule(
            review_selection_input,
            max_per_rule=3,
            include_trace=True,
            coverage_context=review_selection_coverage,
            rule_diagnostics={"rule_diagnostics": review_selection_coverage.get("rule_diagnostics") or []},
            generation_profile=generation_coverage_profile,
        )
        if isinstance(rerank_result, tuple) and len(rerank_result) == 2:
            parsed_result = [x for x in (rerank_result[0] or []) if isinstance(x, dict)]
            review_gate_trace = dict(rerank_result[1] or {})
        else:
            parsed_result = [x for x in (rerank_result or []) if isinstance(x, dict)]
            review_gate_trace = {}
        if not parsed_result and candidate_cases:
            fallback_coverage = analyze_coverage(requirement, candidate_cases)
            fallback_result = _rerank_and_cap_by_rule(
                candidate_cases,
                max_per_rule=3,
                include_trace=True,
                coverage_context=fallback_coverage,
                rule_diagnostics={"rule_diagnostics": fallback_coverage.get("rule_diagnostics") or []},
                generation_profile=generation_coverage_profile,
            )
            if isinstance(fallback_result, tuple) and len(fallback_result) == 2:
                parsed_result = [x for x in (fallback_result[0] or []) if isinstance(x, dict)]
                review_gate_trace = dict(fallback_result[1] or {})
            else:
                parsed_result = [x for x in (fallback_result or []) if isinstance(x, dict)]
                review_gate_trace = {}
            review_selection_input = list(candidate_cases)
            review_llm_applied = False
            review_llm_selected_signatures = set()
            review_constraint_retained_signatures = set()
            review_llm_drop_reason_raw_map = {}
            review_llm_drop_reason_raw_origin_map = {}
            review_llm_drop_reason_map = {}
            review_llm_drop_reason_source_map = {}
            review_llm_drop_reason_evidence_map = {}
            review_constraint_reason_map = {}
            review_llm_runtime_debug["forced_reset_by_fallback"] = True
            review_llm_runtime_debug["final_source"] = "review_selector"
            review_llm_runtime_debug["applied"] = False
            review_llm_runtime_debug["applied_reason"] = "forced_reset_by_empty_rerank_result"
            review_llm_runtime_debug["fallback_reason_incomplete"] = False

        review_selected_count = len(parsed_result)
        stage_counts["review"] = len(parsed_result)
    else:
        if append and isinstance(existing_cases, list):
            reference_count_effective = max(1, int(expected_count or 1) - int(existing_unique_count or 0))
        candidate_cases = [x for x in parsed_result if isinstance(x, dict)]
        candidate_count_before_review = len(candidate_cases)
        review_candidate_cases = list(candidate_cases)
        review_selection_input = list(candidate_cases)
        review_candidate_coverage = analyze_coverage(requirement, candidate_cases)
        review_candidate_coverage_context = review_candidate_coverage
        review_candidate_rule_diagnostics = {
            "rule_diagnostics": review_candidate_coverage_context.get("rule_diagnostics") or []
        }
        rerank_result = _rerank_and_cap_by_rule(
            candidate_cases,
            max_per_rule=3,
            include_trace=True,
            coverage_context=review_candidate_coverage,
            rule_diagnostics={"rule_diagnostics": review_candidate_coverage.get("rule_diagnostics") or []},
            generation_profile=generation_coverage_profile,
        )
        if isinstance(rerank_result, tuple) and len(rerank_result) == 2:
            parsed_result = [x for x in (rerank_result[0] or []) if isinstance(x, dict)]
            review_gate_trace = dict(rerank_result[1] or {})
        else:
            parsed_result = [x for x in (rerank_result or []) if isinstance(x, dict)]
            review_gate_trace = {}
        review_selected_count = len(parsed_result)
        stage_counts["review"] = len(parsed_result)

    # Anti-collapse safeguard: avoid severe 50->1 style collapse after rerank when
    # candidate pool still has enough high-quality items.
    if int(candidate_count_before_review or 0) >= 2:
        if int(reference_count_effective or 0) >= 10:
            review_floor_ratio = 0.2
            if generation_coverage_mode == "expanded_regression":
                review_floor_ratio = 0.80
            elif generation_coverage_mode == "full_functional_regression":
                review_floor_ratio = 0.35
            review_post_rerank_floor_count = min(
                int(candidate_count_before_review or 0),
                max(2, int(round(float(reference_count_effective or 0) * review_floor_ratio))),
            )
        else:
            review_post_rerank_floor_count = min(int(candidate_count_before_review or 0), 2)
    else:
        review_post_rerank_floor_count = 1

    if int(len(parsed_result)) < int(review_post_rerank_floor_count or 1):
        review_shortfall_detected = True
        recovery_before = int(len(parsed_result))
        recovered_signatures = {_signature(item) for item in parsed_result if isinstance(item, dict)}
        recovery_pool: list[dict[str, Any]] = []
        for source_case in [*review_selection_input, *candidate_cases]:
            if not isinstance(source_case, dict):
                continue
            sig = _signature(source_case)
            if not sig or sig in recovered_signatures:
                continue
            recovered_signatures.add(sig)
            recovery_pool.append(source_case)
        recovery_pool.sort(
            key=lambda item: tuple(
                [
                    -value
                    for value in _rank_review_case_for_fill(
                        item,
                        coverage_context=review_candidate_coverage_context,
                        rule_diagnostics=review_candidate_rule_diagnostics,
                    )
                ]
            )
            + (_review_case_id(item),)
        )
        for fill_case in recovery_pool:
            if int(len(parsed_result)) >= int(review_post_rerank_floor_count or 1):
                break
            parsed_result.append(fill_case)
        review_post_rerank_recovered_count = max(
            0,
            int(len(parsed_result)) - int(recovery_before),
        )
        if review_post_rerank_recovered_count > 0:
            review_fill_source = (
                "post_rerank_recovery"
                if str(review_fill_source or "none") in {"", "none"}
                else f"{review_fill_source}+post_rerank_recovery"
            )

    parsed_result = normalize_json_structure_fn(parsed_result)
    parsed_result = deduplicate_test_cases_fn(parsed_result)
    parsed_result = reorder_cases_by_closed_loop_fn(parsed_result, start_id=start_id, renumber_ids=True)
    pre_priority_coverage = analyze_coverage(requirement, [x for x in parsed_result if isinstance(x, dict)])
    parsed_result = apply_priority_semantics_to_cases(
        [x for x in parsed_result if isinstance(x, dict)],
        attach_debug=False,
        coverage_context=pre_priority_coverage,
        rule_diagnostics={"rule_diagnostics": pre_priority_coverage.get("rule_diagnostics") or []},
    )
    parsed_result = _enforce_uncertain_priority_floor(
        [x for x in parsed_result if isinstance(x, dict)]
    )
    ui_like_ratio_postprocess_drop_count = 0
    if normalized_forbidden_patterns:
        ui_max_ratio = 0.40
        ui_min_keep = 2
        total_after_priority = int(len(parsed_result))
        if total_after_priority > 0:
            score_profiles: list[dict[str, Any]] = []
            ui_like_count = 0
            for case in parsed_result:
                # Keep UI-like cap aligned with observe_batch_a semantics (no external coverage context).
                profile = score_case_priority(case)
                ui_like_case = bool(profile.get("ui_like_case"))
                if ui_like_case:
                    ui_like_count += 1
                score_profiles.append(profile)

            allowed_ui_like_count = max(int(ui_min_keep), int(float(total_after_priority) * float(ui_max_ratio)))
            need_drop_count = int(max(0, ui_like_count - allowed_ui_like_count))
            if need_drop_count > 0:
                removable: list[tuple[int, int, int, int]] = []
                for index, (case, profile) in enumerate(zip(parsed_result, score_profiles)):
                    if not bool(profile.get("ui_like_case")):
                        continue
                    has_coverage_value = bool(
                        (profile.get("missing_rule_hits") or [])
                        or (profile.get("core_rule_hits") or [])
                        or (profile.get("unique_coverage_hits") or [])
                    )
                    reasons = [str(x) for x in (profile.get("reasons") or [])]
                    main_workflow_hit = bool("main_workflow_hit" in reasons)
                    preferred_pattern_hit = bool(profile.get("preferred_pattern_hit"))
                    reuse_risk_hit = bool(profile.get("reuse_risk_hit"))
                    cross_or_state_hit = bool(profile.get("cross_page_flow_hit") or profile.get("state_transition_hit"))
                    if has_coverage_value or main_workflow_hit or preferred_pattern_hit or reuse_risk_hit or cross_or_state_hit:
                        continue
                    removable.append(
                        (
                            int(profile.get("focus_score") or _focus_score(case)),
                            int(profile.get("coverage_gain_score") or 0),
                            int(str(case.get("priority") or "").strip().upper() in {"P0", "P1"}),
                            int(index),
                        )
                    )

                if removable:
                    removable.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
                    drop_index_set = {
                        int(item[3]) for item in removable[: int(min(need_drop_count, len(removable)))]
                    }
                    if drop_index_set:
                        parsed_result = [
                            case for idx, case in enumerate(parsed_result) if int(idx) not in drop_index_set
                        ]
                        parsed_result = reorder_cases_by_closed_loop_fn(
                            parsed_result,
                            start_id=start_id,
                            renumber_ids=True,
                        )
                        ui_like_ratio_postprocess_drop_count = int(len(drop_index_set))
    try:
        from ..judge.test_case_judge import judge_cases
        from ..judge.test_case_repairer import repair_cases
        from ..judge.training_gate import training_gate

        judged = judge_cases(
            cases=[item for item in parsed_result if isinstance(item, dict)],
            requirement_semantics_context=requirement_semantics_context or {},
            control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
        )
        repaired = repair_cases(
            judged=judged,
            requirement_semantics_context=requirement_semantics_context or {},
            control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
            strategy="rule_first_llm_fallback",
        )
        confirmed_pass_cases, repaired_pass_cases, rejected_cases, pending_cases = training_gate(repaired)
        parsed_result = [*confirmed_pass_cases, *repaired_pass_cases]
        parsed_result = deduplicate_test_cases_fn([item for item in parsed_result if isinstance(item, dict)])
        parsed_result = reorder_cases_by_closed_loop_fn(
            parsed_result,
            start_id=start_id,
            renumber_ids=True,
        )
        judge_summary_payload = {
            "pass_count": int(repaired.pass_count or 0),
            "repairable_count": int(repaired.repairable_count or 0),
            "reject_count": int(repaired.reject_count or 0),
            "pending_count": int(repaired.pending_count or 0),
            "repaired_case_count": int(repaired.repaired_case_count or 0),
            "appended_case_count": int(repaired.appended_case_count or 0),
            "confirmed_pass_out_count": int(len(confirmed_pass_cases)),
            "repaired_pass_out_count": int(len(repaired_pass_cases)),
            "rejected_out_count": int(len(rejected_cases)),
            "pending_out_count": int(len(pending_cases)),
            "core_flow_covered": bool(repaired.core_flow_covered),
            "reuse_risk_covered": bool(repaired.reuse_risk_covered),
            "fact_profile_source": str(fact_profile.get("profile_source") or ""),
            "fact_profile_confidence": float(fact_profile.get("confidence") or 0.0),
            "fact_profile_confirmed_count": int(len(fact_profile.get("confirmed_facts") or [])),
            "fact_profile_forbidden_count": int(len(fact_profile.get("forbidden_facts") or [])),
            "fact_profile_pending_count": int(len(fact_profile.get("pending_items") or [])),
        }
        judge_decision_table_payload = []
        for judged_item in repaired.cases or []:
            signal_set = judged_item.signals
            before_case = judged_item.before_case if isinstance(judged_item.before_case, dict) else {}
            after_case = judged_item.after_case if isinstance(judged_item.after_case, dict) else {}
            signals_payload = {
                "violates_confirmed_fact": bool(signal_set.violates_confirmed_fact),
                "missing_core_flow": bool(signal_set.missing_core_flow),
                "missing_reuse_risk": bool(signal_set.missing_reuse_risk),
                "contains_pending_logic": bool(signal_set.contains_pending_logic),
                "is_semantic_duplicate": bool(getattr(signal_set, "is_semantic_duplicate", False)),
                "duplicate_of_case_id": str(getattr(signal_set, "duplicate_of_case_id", "") or ""),
                "duplicate_similarity": float(getattr(signal_set, "duplicate_similarity", 0.0) or 0.0),
                "confirmed_fact_hits": [str(item) for item in (signal_set.confirmed_fact_hits or [])],
                "confirmed_fact_violations": [
                    str(item) for item in (signal_set.confirmed_fact_violations or [])
                ],
                "reuse_risk_hits": [str(item) for item in (signal_set.reuse_risk_hits or [])],
                "pending_hits": [str(item) for item in (signal_set.pending_hits or [])],
                "vague_or_unconfirmed_hits": [
                    str(item) for item in (getattr(signal_set, "vague_or_unconfirmed_hits", []) or [])
                ],
            }
            judge_decision_table_payload.append(
                {
                    "case_id": str(judged_item.case_id or ""),
                    "status": str(getattr(judged_item.status, "value", judged_item.status)),
                    "reject_reason": str(judged_item.reject_reason or ""),
                    "pending_reason": str(judged_item.pending_reason or ""),
                    "repaired": bool(judged_item.repaired),
                    "repaired_pass": bool(judged_item.repaired_pass),
                    "has_before_case": bool(before_case),
                    "has_after_case": bool(after_case),
                    "before_case_id": str(before_case.get("id") or ""),
                    "after_case_id": str(after_case.get("id") or ""),
                    "signals": signals_payload,
                    "violates_confirmed_fact": bool(signals_payload.get("violates_confirmed_fact")),
                    "contains_pending_logic": bool(signals_payload.get("contains_pending_logic")),
                    "missing_core_flow": bool(signals_payload.get("missing_core_flow")),
                    "missing_reuse_risk": bool(signals_payload.get("missing_reuse_risk")),
                    "confirmed_fact_hits": list(signals_payload.get("confirmed_fact_hits") or []),
                    "confirmed_fact_violations": list(signals_payload.get("confirmed_fact_violations") or []),
                    "pending_hits": list(signals_payload.get("pending_hits") or []),
                    "vague_or_unconfirmed_hits": list(
                        signals_payload.get("vague_or_unconfirmed_hits") or []
                    ),
                    "reuse_risk_hits": list(signals_payload.get("reuse_risk_hits") or []),
                    "missing_reuse_risk_items": [
                        str(item) for item in (signal_set.missing_reuse_risk_items or [])
                    ],
                    "is_semantic_duplicate": bool(signals_payload.get("is_semantic_duplicate")),
                    "duplicate_of_case_id": str(signals_payload.get("duplicate_of_case_id") or ""),
                    "duplicate_similarity": signals_payload.get("duplicate_similarity") or 0,
                    "before_case_snapshot": dict(before_case),
                    "after_case_snapshot": dict(after_case),
                    "notes": [str(item) for item in (signal_set.notes or [])],
                }
            )
    except Exception:
        judge_summary_payload = {}
        judge_decision_table_payload = []
    final_quality_filtered_result: list[dict[str, Any]] = []
    final_quality_drop_total = 0
    for case in [x for x in parsed_result if isinstance(x, dict)]:
        drop_reason = _final_quality_drop_reason(case)
        if drop_reason:
            final_quality_drop_total += 1
            _record_low_quality_drop(
                low_quality_drop_details,
                case,
                reason=drop_reason,
                stage="post_judge_quality_filter",
            )
            continue
        final_quality_filtered_result.append(case)
    if final_quality_drop_total > 0:
        parsed_result = final_quality_filtered_result
        low_quality_dropped_total += int(final_quality_drop_total)
        postprocess_filter_drop_total += int(final_quality_drop_total)
    pre_priority_coverage = analyze_coverage(requirement, [x for x in parsed_result if isinstance(x, dict)])
    parsed_result = apply_priority_semantics_to_cases(
        [x for x in parsed_result if isinstance(x, dict)],
        attach_debug=False,
        coverage_context=pre_priority_coverage,
        rule_diagnostics={"rule_diagnostics": pre_priority_coverage.get("rule_diagnostics") or []},
    )
    parsed_result = _enforce_uncertain_priority_floor(
        [x for x in parsed_result if isinstance(x, dict)]
    )
    parsed_result = _strip_case_meta_list(parsed_result)
    parsed_result, final_description_dedup_drop_signatures = _dedupe_by_final_description(
        [x for x in parsed_result if isinstance(x, dict)]
    )
    if final_description_dedup_drop_signatures:
        parsed_result = reorder_cases_by_closed_loop_fn(
            parsed_result,
            start_id=start_id,
            renumber_ids=True,
        )
    if int(append_final_cap_count or 0) > 0 and len([x for x in parsed_result if isinstance(x, dict)]) > int(append_final_cap_count):
        cap_coverage = analyze_coverage(requirement, [x for x in parsed_result if isinstance(x, dict)])
        cap_rule_diagnostics = {"rule_diagnostics": cap_coverage.get("rule_diagnostics") or []}
        indexed_cases = [
            (index, item)
            for index, item in enumerate(parsed_result)
            if isinstance(item, dict)
        ]
        indexed_cases.sort(
            key=lambda pair: tuple(
                [
                    -value
                    for value in _rank_review_case_for_fill(
                        pair[1],
                        coverage_context=cap_coverage,
                        rule_diagnostics=cap_rule_diagnostics,
                    )
                ]
            )
            + (int(pair[0]),)
        )
        keep_indices = {
            int(index)
            for index, _case in indexed_cases[: int(append_final_cap_count)]
        }
        append_cap_drop_signatures = {
            _signature(case)
            for index, case in enumerate(parsed_result)
            if isinstance(case, dict) and int(index) not in keep_indices
        }
        append_cap_drop_total = int(len(append_cap_drop_signatures))
        parsed_result = [
            case
            for index, case in enumerate(parsed_result)
            if isinstance(case, dict) and int(index) in keep_indices
        ]
    flow_project_profile = dict(project_profile or {})
    feedback_source_meta = dict(getattr(control_state, "source_meta", {}) or {})
    feedback_redundant_caps = (
        feedback_source_meta.get("priority_pool_redundant_scenario_caps")
        if isinstance(feedback_source_meta.get("priority_pool_redundant_scenario_caps"), dict)
        else {}
    )
    if generation_coverage_mode in {"expanded_regression", "full_functional_regression"}:
        scenario_policy = dict(flow_project_profile.get("scenario_cluster_policy") or {})
        scenario_policy["coverage_mode"] = str(generation_coverage_mode or "")
        scenario_policy["intent_duplicate_cap"] = 1
        scenario_policy["strict_duplicate_policy"] = generation_coverage_mode == "expanded_regression"
        flow_project_profile["scenario_cluster_policy"] = scenario_policy
    if feedback_redundant_caps:
        scenario_policy = dict(flow_project_profile.get("scenario_cluster_policy") or {})
        scenario_caps = dict(scenario_policy.get("scenario_caps") or {})
        for scenario_key, cap in feedback_redundant_caps.items():
            key = str(scenario_key or "").strip()
            if not key:
                continue
            try:
                resolved_cap = max(1, int(cap or 1))
            except Exception:
                resolved_cap = 1
            current_cap = scenario_caps.get(key)
            try:
                scenario_caps[key] = min(int(current_cap), resolved_cap) if current_cap is not None else resolved_cap
            except Exception:
                scenario_caps[key] = resolved_cap
        scenario_policy["scenario_caps"] = scenario_caps
        scenario_policy["feedback_redundant_caps_applied"] = True
        flow_project_profile["scenario_cluster_policy"] = scenario_policy
    try:
        parsed_result, flow_governance_summary = govern_cases_by_flow_structure(
            requirement,
            [x for x in parsed_result if isinstance(x, dict)],
            start_id=start_id,
            renumber_ids=True,
            max_per_scenario=2,
            project_profile=flow_project_profile,
        )
    except Exception as exc:
        flow_governance_summary = {
            "applied": False,
            "reason": "exception",
            "exception": str(exc)[:200],
            "scenario_duplicate_pruned_count": 0,
            "flow_reordered": False,
        }

    if (
        int(expected_count or 0) > 0
        and effective_generation_coverage_mode in {"expanded_regression", "full_functional_regression"}
        and not append
    ):
        floor_ratio = 0.80 if effective_generation_coverage_mode == "expanded_regression" else 0.70
        final_target_floor_count = int(round(float(expected_count or 0) * floor_ratio))
        if effective_generation_coverage_mode == "full_functional_regression":
            try:
                full_regression_floor = max(85, int(generation_target_case_range.get("min") or 0))
            except Exception:
                full_regression_floor = 85
            final_target_floor_count = max(int(full_regression_floor or 0), final_target_floor_count)
        current_final_count = len([x for x in parsed_result if isinstance(x, dict)])
        if current_final_count < final_target_floor_count:
            final_floor_recovery_attempted = True
            recovery_pool_seed = [
                item
                for item in [*review_candidate_cases, *review_selection_input, *candidate_cases]
                if isinstance(item, dict)
            ]
            try:
                recovery_structure = analyze_case_structure(
                    requirement,
                    recovery_pool_seed,
                    project_profile=project_profile,
                )
                recovery_group_count = int(
                    len(
                        {
                            str(row.get("duplicate_group_key") or row.get("intent_signature") or row.get("scenario_key") or "")
                            for row in (recovery_structure.get("rows") or [])
                            if isinstance(row, dict)
                        }
                    )
                )
            except Exception:
                recovery_group_count = 0
            allow_relaxed_full_recovery = bool(
                effective_generation_coverage_mode == "full_functional_regression"
                and len(recovery_pool_seed) >= final_target_floor_count
            )
            if recovery_group_count >= final_target_floor_count or allow_relaxed_full_recovery:
                final_floor_recovery_applied = True
                final_signatures_before_recovery = {
                    _signature(item) for item in parsed_result if isinstance(item, dict)
                }
                recovery_seen_signatures = set(final_signatures_before_recovery)
                recovery_pool: list[dict[str, Any]] = []
                for source_case in recovery_pool_seed:
                    sig = _signature(source_case)
                    if not sig or sig in recovery_seen_signatures:
                        continue
                    recovery_seen_signatures.add(sig)
                    expected_text = str(source_case.get("expected_result") or "").strip()
                    expected_quality = str(source_case.get("expected_result_quality") or "").strip().lower()
                    if (
                        expected_quality in {"invalid_case", "non_assertable", "truncated"}
                        or _reasoning_leakage_hits(source_case)
                        or _looks_truncated_text(expected_text)
                        or _is_non_assertable_expected_result(expected_text)
                    ):
                        continue
                    recovery_pool.append(source_case)
                recovery_coverage = analyze_coverage(requirement, recovery_pool_seed)
                recovery_rule_diagnostics = {"rule_diagnostics": recovery_coverage.get("rule_diagnostics") or []}
                recovery_pool.sort(
                    key=lambda item: tuple(
                        [
                            -value
                            for value in _rank_review_case_for_fill(
                                item,
                                coverage_context=recovery_coverage,
                                rule_diagnostics=recovery_rule_diagnostics,
                            )
                        ]
                    )
                    + (_review_case_id(item),)
                )
                recovered: list[dict[str, Any]] = []
                for fill_case in recovery_pool:
                    if current_final_count + len(recovered) >= final_target_floor_count:
                        break
                    recovered.append(fill_case)
                if recovered:
                    merged_for_recovery = deduplicate_test_cases_fn(
                        [*parsed_result, *recovered]
                    )
                    recovery_priority_coverage = analyze_coverage(
                        requirement,
                        [x for x in merged_for_recovery if isinstance(x, dict)],
                    )
                    merged_for_recovery = apply_priority_semantics_to_cases(
                        [x for x in merged_for_recovery if isinstance(x, dict)],
                        attach_debug=False,
                        coverage_context=recovery_priority_coverage,
                        rule_diagnostics={"rule_diagnostics": recovery_priority_coverage.get("rule_diagnostics") or []},
                    )
                    merged_for_recovery = _enforce_uncertain_priority_floor(
                        [x for x in merged_for_recovery if isinstance(x, dict)]
                    )
                    try:
                        from ..judge.test_case_judge import judge_cases as recovery_judge_cases
                        from ..judge.test_case_repairer import repair_cases as recovery_repair_cases
                        from ..judge.training_gate import training_gate as recovery_training_gate

                        recovery_judged = recovery_judge_cases(
                            cases=[item for item in merged_for_recovery if isinstance(item, dict)],
                            requirement_semantics_context=requirement_semantics_context or {},
                            control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
                        )
                        recovery_repaired = recovery_repair_cases(
                            judged=recovery_judged,
                            requirement_semantics_context=requirement_semantics_context or {},
                            control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
                            strategy="rule_first_llm_fallback",
                        )
                        recovery_confirmed, recovery_repaired_pass, _recovery_rejected, _recovery_pending = recovery_training_gate(
                            recovery_repaired
                        )
                        merged_for_recovery = [*recovery_confirmed, *recovery_repaired_pass]
                    except Exception:
                        pass
                    parsed_result, flow_governance_summary = govern_cases_by_flow_structure(
                        requirement,
                        [x for x in merged_for_recovery if isinstance(x, dict)],
                        start_id=start_id,
                        renumber_ids=True,
                        max_per_scenario=2,
                        project_profile=flow_project_profile,
                    )
                    if (
                        effective_generation_coverage_mode == "full_functional_regression"
                        and len([x for x in parsed_result if isinstance(x, dict)]) < final_target_floor_count
                    ):
                        relaxed_flow_profile = dict(flow_project_profile or {})
                        relaxed_policy = dict(relaxed_flow_profile.get("scenario_cluster_policy") or {})
                        relaxed_policy["coverage_mode"] = str(effective_generation_coverage_mode or "")
                        relaxed_policy["disable_scenario_pruning"] = True
                        relaxed_policy["intent_duplicate_cap"] = 1
                        relaxed_policy["relaxed_for_floor_backfill"] = True
                        relaxed_flow_profile["scenario_cluster_policy"] = relaxed_policy
                        relaxed_result, relaxed_summary = govern_cases_by_flow_structure(
                            requirement,
                            [x for x in merged_for_recovery if isinstance(x, dict)],
                            start_id=start_id,
                            renumber_ids=True,
                            max_per_scenario=2,
                            project_profile=relaxed_flow_profile,
                        )
                        if len([x for x in relaxed_result if isinstance(x, dict)]) > len([x for x in parsed_result if isinstance(x, dict)]):
                            parsed_result = relaxed_result
                            flow_governance_summary = relaxed_summary
                            flow_governance_summary["relaxed_for_floor_backfill"] = True
                    final_floor_recovered_count = max(
                        0,
                        len([x for x in parsed_result if isinstance(x, dict)]) - current_final_count,
                    )
                    final_floor_recovery_reason = (
                        "recovered_with_relaxed_scenario_caps"
                        if bool(flow_governance_summary.get("relaxed_for_floor_backfill"))
                        else "recovered_to_explicit_expected_floor"
                    )
            else:
                final_floor_recovery_reason = "insufficient_diverse_candidate_groups"
    if (
        effective_generation_coverage_mode == "full_functional_regression"
        and int(final_target_floor_count or 0) > 0
        and not append
        and len([x for x in parsed_result if isinstance(x, dict)]) < int(final_target_floor_count or 0)
    ):
        current_shortfall_count = len([x for x in parsed_result if isinstance(x, dict)])
        supplement_shortfall = max(1, int(final_target_floor_count or 0) - int(current_shortfall_count or 0))
        if supplement_shortfall <= 5:
            supplement_buffer = 3
        elif supplement_shortfall <= 20:
            supplement_buffer = 5
        else:
            supplement_buffer = max(5, int(round(float(supplement_shortfall) * 0.25)))
        supplement_needed = min(30, int(supplement_shortfall + supplement_buffer))
        final_shortfall_supplement_attempted = True
        existing_case_brief = [
            {
                "id": str(item.get("id") or ""),
                "description": str(item.get("description") or ""),
                "test_module": str(item.get("test_module") or ""),
                "priority": str(item.get("priority") or ""),
            }
            for item in [x for x in parsed_result if isinstance(x, dict)][:140]
        ]
        supplement_coverage = analyze_coverage(
            requirement,
            [x for x in parsed_result if isinstance(x, dict)],
        )
        supplement_missing_rules = [
            str(item)
            for item in (supplement_coverage.get("missing_rules") or [])
            if str(item).strip()
        ][:30]
        supplement_missing_types_raw = supplement_coverage.get("missing_types")
        supplement_missing_types = {
            str(key): [str(item) for item in (value or []) if str(item).strip()][:20]
            for key, value in (
                dict(supplement_missing_types_raw or {}).items()
                if isinstance(supplement_missing_types_raw, dict)
                else []
            )
            if isinstance(value, list) and value
        }
        existing_module_counts: dict[str, int] = {}
        for item in [x for x in parsed_result if isinstance(x, dict)]:
            module_key = str(item.get("test_module") or "").strip() or "unknown"
            existing_module_counts[module_key] = int(existing_module_counts.get(module_key) or 0) + 1
        supplement_prompt = f"""
FULL_REGRESSION_SHORTFALL_SUPPLEMENT:
- The current final set has {current_shortfall_count} cases, below the full-regression floor {int(final_target_floor_count or 0)}.
- Generate up to {supplement_needed} additional high-value, non-duplicate test cases.
- Focus only on the current requirement and the missing coverage evidence below.
- Prefer under-covered business modules, independent functional paths, boundaries, exceptions, and cross-module state synchronization.
- Do not add display-only, copy/toast-only, sorting-only, thumbnail-only, or popup-only cases unless they close a blocking business flow.
- Do not include legacy behavior that conflicts with confirmed current requirements.
- P0 only for blocking main-path closure; otherwise use P1/P2.
- Return ONLY a strict JSON array of test cases with fields: id, description, test_module, preconditions, steps, test_input, expected_result, priority.

MISSING_RULES:
{json.dumps(supplement_missing_rules, ensure_ascii=False)[:8000]}

MISSING_TYPES:
{json.dumps(supplement_missing_types, ensure_ascii=False)[:4000]}

EXISTING_MODULE_COUNTS:
{json.dumps(existing_module_counts, ensure_ascii=False)[:4000]}

EXISTING_FINAL_CASES_TO_AVOID_DUPLICATING:
{json.dumps(existing_case_brief, ensure_ascii=False)[:14000]}
"""
        try:
            yield "@@STATUS@@:Full regression shortfall supplement started...\n"
            supplement_raw = client.generate_response(
                requirement,
                supplement_prompt,
                db=db,
                task_type="generation",
            )
            supplement_parsed = clean_and_parse_json_fn(str(supplement_raw or ""))
            supplement_parsed = normalize_json_structure_fn(supplement_parsed)
            if isinstance(supplement_parsed, list) and supplement_parsed:
                supplement_parsed = deduplicate_test_cases_fn(
                    [x for x in supplement_parsed if isinstance(x, dict)]
                )
                supplement_parsed = apply_priority_semantics_to_cases(
                    [x for x in supplement_parsed if isinstance(x, dict)],
                    attach_debug=False,
                )
                supplement_parsed, supplement_filter_stats = _filter_low_quality_cases_with_stats(
                    supplement_parsed,
                    requirement_text=requirement,
                )
                low_quality_structural_dropped_total += int(
                    supplement_filter_stats.get("invalid_structure_dropped") or 0
                ) + int(supplement_filter_stats.get("weak_case_dropped") or 0)
                semantic_dedup_dropped_total += int(supplement_filter_stats.get("semantic_dedup_dropped") or 0)
                governance_hard_drop_total += int(supplement_filter_stats.get("governance_hard_drop") or 0)
                low_quality_dropped_total = int(low_quality_structural_dropped_total)
                postprocess_filter_drop_total += int(supplement_filter_stats.get("total_dropped") or 0)
                low_quality_drop_details.extend(
                    dict(item)
                    for item in (supplement_filter_stats.get("dropped_details") or [])
                    if isinstance(item, dict)
                )
                supplement_parsed, supplement_conflict_drop = _filter_cases_conflicting_with_confirmed_flow_facts(
                    [x for x in supplement_parsed if isinstance(x, dict)],
                    requirement=str(requirement or ""),
                    kb_context=str(kb_context or ""),
                    fact_profile=fact_profile,
                )
                final_confirmed_conflict_drop_count += int(supplement_conflict_drop or 0)
                existing_sigs = {_signature(item) for item in parsed_result if isinstance(item, dict)}
                unique_supplement: list[dict[str, Any]] = []
                for item in supplement_parsed:
                    sig = _signature(item)
                    if not sig or sig in existing_sigs:
                        continue
                    existing_sigs.add(sig)
                    unique_supplement.append(dict(item))
                if unique_supplement:
                    merged_shortfall = deduplicate_test_cases_fn([*parsed_result, *unique_supplement])
                    shortfall_priority_coverage = analyze_coverage(
                        requirement,
                        [x for x in merged_shortfall if isinstance(x, dict)],
                    )
                    merged_shortfall = apply_priority_semantics_to_cases(
                        [x for x in merged_shortfall if isinstance(x, dict)],
                        attach_debug=False,
                        coverage_context=shortfall_priority_coverage,
                        rule_diagnostics={"rule_diagnostics": shortfall_priority_coverage.get("rule_diagnostics") or []},
                    )
                    merged_shortfall = _enforce_uncertain_priority_floor(
                        [x for x in merged_shortfall if isinstance(x, dict)]
                    )
                    relaxed_flow_profile = dict(flow_project_profile or {})
                    relaxed_policy = dict(relaxed_flow_profile.get("scenario_cluster_policy") or {})
                    relaxed_policy["coverage_mode"] = str(effective_generation_coverage_mode or "")
                    relaxed_policy["disable_scenario_pruning"] = True
                    relaxed_policy["intent_duplicate_cap"] = 1
                    relaxed_policy["relaxed_for_floor_backfill"] = True
                    relaxed_flow_profile["scenario_cluster_policy"] = relaxed_policy
                    supplemented_result, supplemented_summary = govern_cases_by_flow_structure(
                        requirement,
                        [x for x in merged_shortfall if isinstance(x, dict)],
                        start_id=start_id,
                        renumber_ids=True,
                        max_per_scenario=2,
                        project_profile=relaxed_flow_profile,
                    )
                    if len([x for x in supplemented_result if isinstance(x, dict)]) > current_shortfall_count:
                        parsed_result = supplemented_result
                        flow_governance_summary = supplemented_summary
                        flow_governance_summary["relaxed_for_floor_backfill"] = True
                        final_shortfall_supplement_applied = True
                        final_shortfall_supplement_count = max(
                            0,
                            len([x for x in parsed_result if isinstance(x, dict)]) - current_shortfall_count,
                        )
                        final_floor_recovery_applied = True
                        final_floor_recovery_reason = "full_shortfall_supplement_generated"
                        yield f"@@STATUS@@:Full regression shortfall supplement added {final_shortfall_supplement_count} cases.\n"
                    else:
                        final_shortfall_supplement_reason = "supplement_pruned_or_duplicate"
                else:
                    final_shortfall_supplement_reason = "supplement_empty_after_filter"
            else:
                final_shortfall_supplement_reason = "supplement_empty_response"
        except Exception as supplement_err:
            final_shortfall_supplement_reason = f"exception:{str(supplement_err)[:120]}"
    parsed_result, final_filter_conflict_drop_count = _filter_cases_conflicting_with_confirmed_flow_facts(
        [x for x in parsed_result if isinstance(x, dict)],
        requirement=str(requirement or ""),
        kb_context=str(kb_context or ""),
        fact_profile=fact_profile,
    )
    final_confirmed_conflict_drop_count += int(final_filter_conflict_drop_count or 0)
    if (
        int(final_target_floor_count or 0) > 0
        and effective_generation_coverage_mode in {"expanded_regression", "full_functional_regression"}
        and (
            effective_generation_coverage_mode == "full_functional_regression"
            or int(final_confirmed_conflict_drop_count or 0) > 0
        )
        and len([x for x in parsed_result if isinstance(x, dict)]) < int(final_target_floor_count or 0)
    ):
        current_after_conflict = len([x for x in parsed_result if isinstance(x, dict)])
        recovery_pool_seed = [
            item
            for item in [*review_candidate_cases, *review_selection_input, *candidate_cases]
            if isinstance(item, dict)
        ]
        existing_after_conflict = {_signature(item) for item in parsed_result if isinstance(item, dict)}
        post_conflict_pool: list[dict[str, Any]] = []
        seen_after_conflict = set(existing_after_conflict)
        for source_case in recovery_pool_seed:
            sig = _signature(source_case)
            if not sig or sig in seen_after_conflict:
                continue
            seen_after_conflict.add(sig)
            expected_text = str(source_case.get("expected_result") or "").strip()
            expected_quality = str(source_case.get("expected_result_quality") or "").strip().lower()
            if (
                expected_quality in {"invalid_case", "non_assertable", "truncated"}
                or _reasoning_leakage_hits(source_case)
                or _looks_truncated_text(expected_text)
                or _is_non_assertable_expected_result(expected_text)
            ):
                continue
            post_conflict_pool.append(dict(source_case))
        post_conflict_pool, _post_conflict_pool_drop = _filter_cases_conflicting_with_confirmed_flow_facts(
            post_conflict_pool,
            requirement=str(requirement or ""),
            kb_context=str(kb_context or ""),
            fact_profile=fact_profile,
        )
        recovery_coverage = analyze_coverage(requirement, recovery_pool_seed)
        recovery_rule_diagnostics = {"rule_diagnostics": recovery_coverage.get("rule_diagnostics") or []}
        post_conflict_pool.sort(
            key=lambda item: tuple(
                [
                    -value
                    for value in _rank_review_case_for_fill(
                        item,
                        coverage_context=recovery_coverage,
                        rule_diagnostics=recovery_rule_diagnostics,
                    )
                ]
            )
            + (_review_case_id(item),)
        )
        recovered_after_conflict: list[dict[str, Any]] = []
        for fill_case in post_conflict_pool:
            if current_after_conflict + len(recovered_after_conflict) >= int(final_target_floor_count or 0):
                break
            recovered_after_conflict.append(fill_case)
        if recovered_after_conflict:
            merged_after_conflict = deduplicate_test_cases_fn([*parsed_result, *recovered_after_conflict])
            merged_after_conflict = reorder_cases_by_closed_loop_fn(
                [x for x in merged_after_conflict if isinstance(x, dict)],
                start_id=start_id,
                renumber_ids=True,
            )
            recovery_priority_coverage = analyze_coverage(
                requirement,
                [x for x in merged_after_conflict if isinstance(x, dict)],
            )
            merged_after_conflict = apply_priority_semantics_to_cases(
                [x for x in merged_after_conflict if isinstance(x, dict)],
                attach_debug=False,
                coverage_context=recovery_priority_coverage,
                rule_diagnostics={"rule_diagnostics": recovery_priority_coverage.get("rule_diagnostics") or []},
            )
            merged_after_conflict = _enforce_uncertain_priority_floor(
                [x for x in merged_after_conflict if isinstance(x, dict)]
            )
            if effective_generation_coverage_mode == "full_functional_regression":
                relaxed_flow_profile = dict(flow_project_profile or {})
                relaxed_policy = dict(relaxed_flow_profile.get("scenario_cluster_policy") or {})
                relaxed_policy["coverage_mode"] = str(effective_generation_coverage_mode or "")
                relaxed_policy["disable_scenario_pruning"] = True
                relaxed_policy["intent_duplicate_cap"] = 1
                relaxed_policy["relaxed_for_floor_backfill"] = True
                relaxed_flow_profile["scenario_cluster_policy"] = relaxed_policy
                parsed_result, flow_governance_summary = govern_cases_by_flow_structure(
                    requirement,
                    [x for x in merged_after_conflict if isinstance(x, dict)],
                    start_id=start_id,
                    renumber_ids=True,
                    max_per_scenario=2,
                    project_profile=relaxed_flow_profile,
                )
                flow_governance_summary["relaxed_for_floor_backfill"] = True
            else:
                parsed_result, flow_governance_summary = govern_cases_by_flow_structure(
                    requirement,
                    [x for x in merged_after_conflict if isinstance(x, dict)],
                    start_id=start_id,
                    renumber_ids=True,
                    max_per_scenario=2,
                    project_profile=flow_project_profile,
                )
            final_floor_recovery_applied = True
            final_floor_recovery_reason = "recovered_after_confirmed_conflict_filter"
            final_floor_recovered_count = max(
                int(final_floor_recovered_count or 0),
                max(0, len([x for x in parsed_result if isinstance(x, dict)]) - current_after_conflict),
            )
    parsed_result, final_post_recovery_conflict_drop_count = _filter_cases_conflicting_with_confirmed_flow_facts(
        [x for x in parsed_result if isinstance(x, dict)],
        requirement=str(requirement or ""),
        kb_context=str(kb_context or ""),
        fact_profile=fact_profile,
    )
    final_confirmed_conflict_drop_count += int(final_post_recovery_conflict_drop_count or 0)
    final_invalid_quality_filtered_result: list[dict[str, Any]] = []
    final_invalid_quality_drop_total = 0
    for case in [x for x in parsed_result if isinstance(x, dict)]:
        drop_reason = _final_quality_drop_reason(case)
        if drop_reason:
            final_invalid_quality_drop_total += 1
            _record_low_quality_drop(
                low_quality_drop_details,
                case,
                reason=drop_reason,
                stage="post_recovery_quality_filter",
            )
            continue
        final_invalid_quality_filtered_result.append(case)
    if final_invalid_quality_drop_total > 0:
        parsed_result = final_invalid_quality_filtered_result
        low_quality_dropped_total += int(final_invalid_quality_drop_total)
        postprocess_filter_drop_total += int(final_invalid_quality_drop_total)
    parsed_result = reorder_cases_by_closed_loop_fn(
        [x for x in parsed_result if isinstance(x, dict)],
        start_id=start_id,
        renumber_ids=True,
    )
    parsed_result = _enforce_main_path_p0_anchors(
        parsed_result,
        coverage_mode=str(effective_generation_coverage_mode or generation_coverage_mode or ""),
        requirement_text=str(requirement or ""),
    )
    review_priority_overrides: dict[str, str] = {}
    for source_case in review_candidate_cases:
        if not isinstance(source_case, dict):
            continue
        decision_source = str(source_case.get("priority_decision_source") or "").strip()
        decision_final = str(source_case.get("priority_final") or "").strip().upper()
        if (
            decision_source in {"model_p0_guard_downgrade", "main_path_anchor_demoted_non_blocking"}
            and decision_final in {"P1", "P2"}
        ):
            review_priority_overrides[_signature(source_case)] = "P1"
    if review_priority_overrides:
        restored_priority_cases: list[dict[str, Any]] = []
        for item in [x for x in parsed_result if isinstance(x, dict)]:
            updated = dict(item)
            forced_priority = review_priority_overrides.get(_signature(updated))
            if forced_priority:
                updated["priority"] = forced_priority
                updated["priority_final"] = forced_priority
                updated["priority_decision_state"] = "overridden"
                updated["priority_decision_source"] = "review_model_p0_demotion_preserved"
            restored_priority_cases.append(updated)
        parsed_result = restored_priority_cases
    parsed_result = reorder_cases_by_closed_loop_fn(
        [x for x in parsed_result if isinstance(x, dict)],
        start_id=start_id,
        renumber_ids=True,
    )
    parsed_result, execution_plan_summary = _apply_execution_plan_metadata(
        [x for x in parsed_result if isinstance(x, dict)],
        start_id=start_id,
        coverage_mode=str(effective_generation_coverage_mode or generation_coverage_mode or ""),
    )
    try:
        main_chain_cases = [
            dict(item)
            for item in parsed_result
            if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
        ]
        independent_cases = [
            dict(item)
            for item in parsed_result
            if isinstance(item, dict) and str(item.get("execution_group") or "") != "main_smoke"
        ]
        final_order_profile = dict(flow_project_profile or {})
        final_order_policy = dict(final_order_profile.get("scenario_cluster_policy") or {})
        final_order_policy["disable_scenario_pruning"] = True
        final_order_policy["intent_duplicate_cap"] = 1_000_000
        final_order_policy["final_order_only"] = True
        final_order_profile["scenario_cluster_policy"] = final_order_policy
        ordered_independent, final_order_flow_governance_summary = govern_cases_by_flow_structure(
            requirement,
            independent_cases,
            start_id=start_id + len(main_chain_cases),
            renumber_ids=False,
            max_per_scenario=2,
            project_profile=final_order_profile,
        )
        if len(ordered_independent) == len(independent_cases):
            parsed_result = [*main_chain_cases, *ordered_independent]
    except Exception as exc:
        final_order_flow_governance_summary = {
            "applied": False,
            "reason": "exception",
            "exception": str(exc)[:200],
        }
    try:
        final_case_structure = analyze_case_structure(
            requirement,
            [x for x in parsed_result if isinstance(x, dict)],
            project_profile=project_profile,
        )
        final_independent_case_structure = analyze_case_structure(
            requirement,
            [
                x
                for x in parsed_result
                if isinstance(x, dict) and str(x.get("execution_group") or "") != "main_smoke"
            ],
            project_profile=project_profile,
        )
    except Exception:
        final_case_structure = {}
        final_independent_case_structure = {}
    parsed_result = _strip_case_meta_list([x for x in parsed_result if isinstance(x, dict)])
    final_count = len([x for x in parsed_result if isinstance(x, dict)])
    post_review_dedup_drop = max(0, int(review_selected_count or 0) - int(final_count or 0))

    selection_signatures = {_signature(item) for item in review_selection_input if isinstance(item, dict)}
    trace_decisions = dict((review_gate_trace.get("decisions") or {}))
    selected_gate_signatures = set(str(item) for item in (review_gate_trace.get("selected_signatures") or []))
    dedup_drop_signatures = set(str(item) for item in (review_gate_trace.get("dedup_dropped_signatures") or []))
    final_signatures = {_signature(item) for item in parsed_result if isinstance(item, dict)}
    final_priority_by_signature = {
        _signature(item): str(item.get("priority") or item.get("priority_final") or "").strip().upper()
        for item in parsed_result
        if isinstance(item, dict)
    }
    review_candidate_coverage_context = analyze_coverage(
        requirement,
        [x for x in review_candidate_cases if isinstance(x, dict)],
    )
    review_candidate_rule_diagnostics = {
        "rule_diagnostics": review_candidate_coverage_context.get("rule_diagnostics") or []
    }
    try:
        review_case_structure = analyze_case_structure(
            requirement,
            [x for x in review_candidate_cases if isinstance(x, dict)],
            project_profile=project_profile,
        )
    except Exception:
        review_case_structure = {}
    structure_rows_by_index = {
        int(item.get("candidate_index") or 0): dict(item)
        for item in (review_case_structure.get("rows") or [])
        if isinstance(item, dict)
    }

    for index, case in enumerate(review_candidate_cases, start=1):
        if not isinstance(case, dict):
            continue
        signature = _signature(case)
        structure_row = dict(structure_rows_by_index.get(int(index)) or {})
        gate_info = dict(trace_decisions.get(signature) or {})
        rule_keys = list(gate_info.get("rule_keys") or _extract_rule_keys(case))
        bucket = str(gate_info.get("bucket") or _coverage_bucket(case))
        high_signal = bool(gate_info.get("high_signal")) if gate_info else bool(_is_high_signal(case))
        adds_rule = bool(gate_info.get("adds_rule")) if gate_info else False
        adds_bucket = bool(gate_info.get("adds_bucket")) if gate_info else False
        gate_reason = str(gate_info.get("drop_reason") or "")
        retained = signature in final_signatures
        dropped_stage = ""
        dropped_reason = ""
        selected_by_review_llm = signature in review_llm_selected_signatures if review_llm_applied else True
        selected_by_review_must_keep = signature in review_must_keep_signatures
        selected_by_review_constraints = signature in review_constraint_retained_signatures
        review_llm_drop_reason_raw = str(review_llm_drop_reason_raw_map.get(signature) or "")
        review_llm_drop_reason = str(review_llm_drop_reason_map.get(signature) or "")
        review_llm_drop_reason_source = str(review_llm_drop_reason_source_map.get(signature) or "")
        review_llm_drop_reason_evidence = review_llm_drop_reason_evidence_map.get(signature)
        if not isinstance(review_llm_drop_reason_evidence, dict):
            review_llm_drop_reason_evidence = {}
        has_coverage_signal = bool(review_llm_drop_reason_evidence.get("has_coverage_signal"))
        has_high_signal = bool(review_llm_drop_reason_evidence.get("has_high_signal"))
        has_competition_signal = bool(review_llm_drop_reason_evidence.get("has_competition_signal"))
        has_positive_evidence = bool(
            review_llm_drop_reason_evidence.get("has_positive_evidence")
            or has_coverage_signal
            or has_high_signal
            or has_competition_signal
        )
        review_constraint_reason = str(review_constraint_reason_map.get(signature) or "")
        if (
            review_llm_applied
            and (not selected_by_review_llm)
            and (not selected_by_review_constraints)
            and (not selected_by_review_must_keep)
        ):
            dropped_stage = "review_llm"
            dropped_reason = "drop_not_selected_by_review_llm"
            if review_llm_drop_reason:
                dropped_reason = f"drop_not_selected_by_review_llm:{review_llm_drop_reason}"
        elif signature not in selection_signatures:
            dropped_stage = "review_selector"
            if str(review_constraint_reason or "") == "dropped_by_target_max":
                dropped_reason = "drop_outside_target_window"
            elif review_llm_applied and selected_by_review_llm:
                dropped_reason = "drop_after_llm_selection"
            else:
                dropped_reason = "drop_selector_fallback"
        elif signature in append_cap_drop_signatures:
            dropped_stage = "append_target_cap"
            dropped_reason = "drop_exceeds_append_target_count"
        elif signature in final_description_dedup_drop_signatures:
            dropped_stage = "post_review_dedup_or_reorder"
            dropped_reason = "drop_final_description_duplicate"
        elif signature in dedup_drop_signatures:
            dropped_stage = "review_dedup_pre_gate"
            dropped_reason = "drop_dedup_pre_gate"
        elif signature in selection_signatures and signature not in selected_gate_signatures:
            dropped_stage = "review_gate"
            dropped_reason = gate_reason or "drop_review_gate"
        elif signature in selected_gate_signatures and signature not in final_signatures:
            dropped_stage = "post_review_dedup_or_reorder"
            dropped_reason = "drop_post_review_dedup_or_reorder"
        elif retained:
            dropped_stage = "retained"
            dropped_reason = "retained"

        score_profile = score_case_priority(
            case,
            coverage_context=review_candidate_coverage_context,
            rule_diagnostics=review_candidate_rule_diagnostics,
        )
        model_priority_value = str(
            case.get("model_priority_current")
            or case.get("model_priority")
            or ""
        ).strip().upper()
        legacy_priority_value = str(case.get("legacy_priority") or case.get("priority") or "").strip().upper()
        priority_final_value = str(case.get("priority_final") or "").strip().upper()
        priority_decision_state_value = str(case.get("priority_decision_state") or "").strip().lower()
        priority_decision_source_value = str(case.get("priority_decision_source") or "").strip() or "insufficient_evidence"
        priority_confidence_value = str(case.get("priority_confidence") or "").strip() or "low"
        priority_conflict_reason_value = str(case.get("priority_conflict_reason") or "").strip()
        priority_resolution_reason_value = str(case.get("priority_resolution_reason") or "").strip()
        unresolved_priority_decision = bool(
            priority_decision_state_value in {"conflict", "undetermined", "invalid"}
            and priority_final_value not in {"P0", "P1", "P2"}
        )
        if retained and not unresolved_priority_decision:
            planned_priority_value = str(final_priority_by_signature.get(signature) or "").strip().upper()
            if planned_priority_value in {"P0", "P1", "P2"}:
                priority_final_value = planned_priority_value
                if not priority_resolution_reason_value:
                    priority_resolution_reason_value = "priority_final_reflected_from_execution_plan"
                if not priority_decision_source_value or priority_decision_source_value == "insufficient_evidence":
                    priority_decision_source_value = "execution_plan_final_priority"
        if priority_decision_state_value not in {"decided", "conflict", "undetermined", "optional", "invalid"}:
            if priority_final_value in {"P0", "P1", "P2"}:
                priority_decision_state_value = "decided"
            else:
                priority_decision_state_value = "undetermined"
        if priority_final_value not in {"P0", "P1", "P2"}:
            if priority_decision_state_value == "decided" and legacy_priority_value in {"P0", "P1", "P2"}:
                priority_final_value = legacy_priority_value
                if not priority_resolution_reason_value:
                    priority_resolution_reason_value = "priority_final_backfilled_from_legacy_priority"
            else:
                priority_decision_state_value = "invalid"
                if not priority_decision_source_value or priority_decision_source_value == "insufficient_evidence":
                    priority_decision_source_value = "priority_final_missing_after_semantic_resolve"
                if not priority_resolution_reason_value:
                    priority_resolution_reason_value = "missing_priority_final_after_semantic_resolve"
                priority_final_value = ""
        hit_must_cover_rule = bool(_hit_must_cover_rule(rule_keys, score_profile))
        violates_forbidden_pattern = bool(_violates_forbidden_pattern(case))
        hits_soft_constraint = bool(_hits_soft_constraint(case))
        satisfies_quality_hint = bool(_satisfies_quality_hint(case))
        row = {
            "candidate_index": int(index),
            "signature": signature,
            "case_id": str(case.get("id") or ""),
            "description": str(case.get("description") or ""),
            "test_module": str(case.get("test_module") or ""),
            "expected_result": str(case.get("expected_result") or ""),
            "flow_stage": str(structure_row.get("flow_stage") or "unknown"),
            "flow_stage_label": str(structure_row.get("flow_stage_label") or structure_row.get("flow_stage") or "unknown"),
            "flow_rank": structure_row.get("flow_rank"),
            "cross_cutting": [str(item) for item in (structure_row.get("cross_cutting") or [])],
            "scenario_key": str(structure_row.get("scenario_key") or ""),
            "is_scenario_duplicate": bool(structure_row.get("is_scenario_duplicate")),
            "duplicate_cluster_id": str(structure_row.get("duplicate_cluster_id") or ""),
            "duplicate_cluster_size": int(structure_row.get("duplicate_cluster_size") or 0),
            "duplicate_of_case_id": str(structure_row.get("duplicate_of_case_id") or ""),
            "misordered_against_requirement_flow": bool(structure_row.get("misordered_against_requirement_flow")),
            "expected_result_quality": str(case.get("expected_result_quality") or ""),
            "expected_result_quality_reason": str(case.get("expected_result_quality_reason") or ""),
            "expected_result_alignment_warning": bool(case.get("expected_result_alignment_warning")),
            "truncated_text_detected": bool(case.get("truncated_text_detected")),
            "case_quality": str(case.get("case_quality") or "valid_case"),
            "invalid_case_reason": str(case.get("invalid_case_reason") or ""),
            "invalid_case_signals": [str(item) for item in (case.get("invalid_case_signals") or [])],
            "model_priority_current": model_priority_value,
            "model_priority": model_priority_value,
            "legacy_priority": legacy_priority_value,
            "priority_final": priority_final_value,
            "priority_decision_state": priority_decision_state_value,
            "priority_decision_source": priority_decision_source_value,
            "priority_confidence": priority_confidence_value,
            "priority_conflict_reason": priority_conflict_reason_value,
            "priority_resolution_reason": priority_resolution_reason_value,
            "priority_score": int(score_profile.get("priority_score") or 0),
            "suggested_priority": str(score_profile.get("suggested_priority") or "").strip().upper(),
            "priority_reasons": [str(item) for item in (score_profile.get("reasons") or []) if str(item).strip()],
            "selected_by_review_llm": bool(selected_by_review_llm),
            "selected_by_review_must_keep": bool(selected_by_review_must_keep),
            "selected_by_review_constraints": bool(selected_by_review_constraints),
            "review_constraint_reason": review_constraint_reason,
            "review_llm_drop_reason_raw": review_llm_drop_reason_raw,
            "review_llm_drop_reason": review_llm_drop_reason,
            "review_llm_drop_reason_resolved": review_llm_drop_reason,
            "review_llm_drop_reason_source": review_llm_drop_reason_source,
            "review_llm_drop_reason_evidence": review_llm_drop_reason_evidence,
            "has_positive_evidence": bool(has_positive_evidence),
            "has_coverage_signal": bool(has_coverage_signal),
            "has_high_signal": bool(has_high_signal),
            "has_competition_signal": bool(has_competition_signal),
            "review_llm_filter_applied": bool(review_llm_applied),
            "must_keep_candidate": bool(signature in review_must_keep_signatures),
            "must_keep_reasons": list(review_must_keep_reason_map.get(signature) or []),
            "selected_by_review_gate": bool(signature in selected_gate_signatures),
            "retained_final": bool(retained),
            "dropped_stage": dropped_stage,
            "dropped_reason": dropped_reason,
            "rule_keys": rule_keys,
            "bucket": bucket,
            "adds_rule": bool(adds_rule),
            "adds_bucket": bool(adds_bucket),
            "high_signal": bool(high_signal),
            "has_coverage_value": bool(gate_info.get("has_coverage_value")) if gate_info else bool(
                score_profile.get("missing_rule_hits") or score_profile.get("core_rule_hits")
            ),
            "retained_reason": str(gate_info.get("retained_reason") or ""),
            "rerank_rank": int(gate_info.get("rank") or 0),
            "focus_score": int(gate_info.get("focus_score") or _focus_score(case)),
            "covered_rule_ids": [str(item) for item in (score_profile.get("covered_rule_ids") or [])],
            "missing_rule_hits": [str(item) for item in (score_profile.get("missing_rule_hits") or [])],
            "core_rule_hits": [str(item) for item in (score_profile.get("core_rule_hits") or [])],
            "coverage_gain_score": int(score_profile.get("coverage_gain_score") or 0),
            "reuse_risk_hit": bool(score_profile.get("reuse_risk_hit")),
            "hit_must_cover_rule": bool(hit_must_cover_rule),
            "violates_forbidden_pattern": bool(violates_forbidden_pattern),
            "hits_soft_constraint": bool(hits_soft_constraint),
            "satisfies_quality_hint": bool(satisfies_quality_hint),
        }
        review_decision_table.append(row)

    dropped_rows = [row for row in review_decision_table if not bool(row.get("retained_final"))]
    retained_description_keys = {
        _final_description_dedup_key(row)
        for row in review_decision_table
        if bool(row.get("retained_final")) and _final_description_dedup_key(row)
    }
    for row in dropped_rows:
        description_key = _final_description_dedup_key(row)
        if not description_key or description_key not in retained_description_keys:
            continue
        signature = str(row.get("signature") or "").strip()
        if signature:
            final_description_dedup_drop_signatures.add(signature)
    priority_decision_state_breakdown: dict[str, int] = {
        "decided": 0,
        "conflict": 0,
        "undetermined": 0,
        "optional": 0,
        "invalid": 0,
    }
    priority_final_breakdown: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "null": 0}
    legacy_priority_breakdown: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "UNKNOWN": 0}
    for row in review_decision_table:
        decision_state_key = str(row.get("priority_decision_state") or "").strip().lower()
        if decision_state_key not in priority_decision_state_breakdown:
            decision_state_key = "undetermined"
        priority_decision_state_breakdown[decision_state_key] = int(
            priority_decision_state_breakdown.get(decision_state_key, 0)
        ) + 1

        final_priority_key = str(row.get("priority_final") or "").strip().upper()
        if final_priority_key not in {"P0", "P1", "P2"}:
            final_priority_key = "null"
        priority_final_breakdown[final_priority_key] = int(priority_final_breakdown.get(final_priority_key, 0)) + 1

        legacy_priority_key = str(row.get("legacy_priority") or "").strip().upper()
        if legacy_priority_key not in {"P0", "P1", "P2"}:
            legacy_priority_key = "UNKNOWN"
        legacy_priority_breakdown[legacy_priority_key] = int(
            legacy_priority_breakdown.get(legacy_priority_key, 0)
        ) + 1

    priority_conflict_count = int(priority_decision_state_breakdown.get("conflict", 0))
    priority_undetermined_count = int(priority_decision_state_breakdown.get("undetermined", 0))
    priority_optional_count = int(priority_decision_state_breakdown.get("optional", 0))
    priority_invalid_count = int(priority_decision_state_breakdown.get("invalid", 0))
    needs_priority_review = bool(
        priority_conflict_count > 0
        or priority_undetermined_count > 0
        or priority_invalid_count > 0
    )
    review_llm_drop_reason_counts: dict[str, int] = {}
    review_llm_drop_reason_raw_counts: dict[str, int] = {}
    review_llm_drop_reason_source_counts: dict[str, int] = {}
    fallback_with_positive_evidence_count = 0
    fallback_without_positive_evidence_count = 0
    for row in dropped_rows:
        if str(row.get("dropped_stage") or "") != "review_llm":
            continue
        reason_key = str(row.get("review_llm_drop_reason") or "").strip() or "unspecified"
        review_llm_drop_reason_counts[reason_key] = int(review_llm_drop_reason_counts.get(reason_key, 0)) + 1
        raw_reason_key = str(row.get("review_llm_drop_reason_raw") or "").strip() or "unspecified"
        review_llm_drop_reason_raw_counts[raw_reason_key] = int(
            review_llm_drop_reason_raw_counts.get(raw_reason_key, 0)
        ) + 1
        source_key = str(row.get("review_llm_drop_reason_source") or "").strip() or "unresolved"
        review_llm_drop_reason_source_counts[source_key] = int(
            review_llm_drop_reason_source_counts.get(source_key, 0)
        ) + 1
        if reason_key == "fallback_unspecified":
            if bool(row.get("has_positive_evidence")):
                fallback_with_positive_evidence_count += 1
            else:
                fallback_without_positive_evidence_count += 1
    drop_by_review_llm_count = int(sum(1 for row in dropped_rows if row.get("dropped_stage") == "review_llm"))
    fallback_dropped_reason_count = int(
        review_llm_runtime_debug.get("final_dropped_reason_payload_count")
        if str(review_llm_runtime_debug.get("final_source") or "") == "fallback_llm"
        else 0
    )
    fallback_dropped_reason_mapped_count = int(review_llm_drop_reason_source_counts.get("fallback_llm", 0))
    if str(review_llm_runtime_debug.get("final_source") or "") == "fallback_llm":
        fallback_dropped_reason_mapped_count = int(review_llm_runtime_debug.get("final_dropped_reason_count") or 0)
    fallback_dropped_reason_unmapped_count = int(
        review_llm_runtime_debug.get("final_dropped_reason_unmapped_count")
        if str(review_llm_runtime_debug.get("final_source") or "") == "fallback_llm"
        else 0
    )
    llm_reason_total = int(review_llm_drop_reason_source_counts.get("llm", 0)) + int(
        review_llm_drop_reason_source_counts.get("fallback_llm", 0)
    )
    deterministic_backfill_total = int(review_llm_drop_reason_source_counts.get("deterministic_backfill", 0))
    fallback_reason_incomplete = bool(
        str(review_llm_runtime_debug.get("final_source") or "") == "fallback_llm"
        and fallback_dropped_reason_count <= 0
    )
    review_llm_runtime_debug["fallback_reason_incomplete"] = bool(fallback_reason_incomplete)
    fallback_reason_coverage_ratio = round(
        float(fallback_dropped_reason_mapped_count) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 0.0
    llm_reason_coverage_ratio = round(
        float(llm_reason_total) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 0.0
    deterministic_backfill_ratio = round(
        float(deterministic_backfill_total) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 0.0
    final_reason_incomplete = bool(drop_by_review_llm_count > 0 and llm_reason_total <= 0)
    final_reason_coverage_ratio = round(
        float(llm_reason_total) / float(drop_by_review_llm_count),
        4,
    ) if drop_by_review_llm_count > 0 else 1.0
    review_llm_runtime_debug["final_reason_incomplete"] = bool(final_reason_incomplete)
    review_llm_runtime_debug["final_reason_coverage_ratio"] = float(final_reason_coverage_ratio)
    if final_reason_incomplete and str(review_llm_runtime_debug.get("applied_reason") or "") == "mapped_valid_payload":
        review_llm_runtime_debug["applied_reason"] = "mapped_valid_payload_reason_incomplete"
    flow_outline = dict(review_case_structure.get("flow_outline") or {})
    flow_order = [str(item) for item in (flow_outline.get("flow_order") or []) if str(item)]
    flow_labels = dict(flow_outline.get("flow_labels") or {})
    flow_missing_stages = [str(item) for item in (review_case_structure.get("missing_flow_stages") or []) if str(item)]
    scenario_duplicate_clusters = [
        dict(item)
        for item in (review_case_structure.get("duplicate_clusters") or [])
        if isinstance(item, dict)
    ]
    final_duplicate_project_profile = flow_project_profile
    if (
        effective_generation_coverage_mode == "full_functional_regression"
        and (
            bool((flow_governance_summary or {}).get("relaxed_for_floor_backfill"))
            or bool(final_shortfall_supplement_applied)
        )
    ):
        final_duplicate_project_profile = dict(flow_project_profile or {})
        final_duplicate_policy = dict(final_duplicate_project_profile.get("scenario_cluster_policy") or {})
        final_duplicate_policy["coverage_mode"] = str(effective_generation_coverage_mode or "")
        final_duplicate_policy["disable_scenario_pruning"] = True
        final_duplicate_policy["intent_duplicate_cap"] = 1_000_000
        final_duplicate_policy["relaxed_for_floor_backfill"] = True
        final_duplicate_project_profile["scenario_cluster_policy"] = final_duplicate_policy
    final_duplicate_excess = summarize_duplicate_excess_by_policy(
        final_case_structure,
        project_profile=final_duplicate_project_profile,
        default_max=2,
    )
    review_decision_summary = {
        "candidate_total": int(len(review_decision_table)),
        "retained_total": int(sum(1 for row in review_decision_table if bool(row.get("retained_final")))),
        "dropped_total": int(len(dropped_rows)),
        "flow_order": flow_order,
        "flow_labels": flow_labels,
        "flow_stage_breakdown": dict(review_case_structure.get("stage_breakdown") or {}),
        "flow_missing_stages": flow_missing_stages,
        "flow_missing_stage_count": int(review_case_structure.get("missing_flow_stage_count") or 0),
        "flow_misordered_count": int(review_case_structure.get("misordered_count") or 0),
        "scenario_duplicate_cluster_count": int(review_case_structure.get("duplicate_cluster_count") or 0),
        "scenario_duplicate_case_count": int(review_case_structure.get("duplicate_case_count") or 0),
        "scenario_duplicate_clusters": scenario_duplicate_clusters[:20],
        "final_flow_stage_breakdown": dict(final_independent_case_structure.get("stage_breakdown") or {}),
        "final_flow_missing_stages": [
            str(item)
            for item in (final_independent_case_structure.get("missing_flow_stages") or [])
            if str(item)
        ],
        "final_flow_missing_stage_count": int(final_independent_case_structure.get("missing_flow_stage_count") or 0),
        "final_flow_misordered_count": int(final_independent_case_structure.get("misordered_count") or 0),
        "final_scenario_duplicate_cluster_count": int(
            final_duplicate_excess.get("duplicate_excess_cluster_count") or 0
        ),
        "final_scenario_duplicate_case_count": int(
            final_duplicate_excess.get("duplicate_excess_case_count") or 0
        ),
        "final_scenario_duplicate_clusters": [
            dict(item)
            for item in (final_duplicate_excess.get("duplicate_excess_clusters") or [])[:20]
            if isinstance(item, dict)
        ],
        "final_scenario_duplicate_raw_cluster_count": int(final_case_structure.get("duplicate_cluster_count") or 0),
        "final_scenario_duplicate_raw_case_count": int(final_case_structure.get("duplicate_case_count") or 0),
        "final_reasoning_leakage_case_count": int(
            sum(
                1
                for item in parsed_result
                if isinstance(item, dict) and _reasoning_leakage_hits(item)
            )
        ),
        "final_order_flow_governance": dict(final_order_flow_governance_summary or {}),
        "fact_profile_source": str(fact_profile.get("profile_source") or ""),
        "fact_profile_confidence": float(fact_profile.get("confidence") or 0.0),
        "fact_profile_confirmed_count": int(len(fact_profile.get("confirmed_facts") or [])),
        "fact_profile_forbidden_count": int(len(fact_profile.get("forbidden_facts") or [])),
        "fact_profile_pending_count": int(len(fact_profile.get("pending_items") or [])),
        "project_profile_source": str(project_profile.get("profile_source") or ""),
        "project_profile_confidence": float(project_profile.get("confidence") or 0.0),
        "flow_governance_applied": bool(flow_governance_summary.get("applied")),
        "flow_reordered": bool(flow_governance_summary.get("flow_reordered")),
        "flow_governance_reason": str(flow_governance_summary.get("reason") or ""),
        "scenario_duplicate_pruned_count": int(flow_governance_summary.get("scenario_duplicate_pruned_count") or 0),
        "scenario_duplicate_pruned_indices": list(flow_governance_summary.get("scenario_duplicate_pruned_indices") or [])[:100],
        "execution_plan": dict(execution_plan_summary or {}),
        "linear_executable": bool((execution_plan_summary or {}).get("linear_executable")),
        "linear_scope": str((execution_plan_summary or {}).get("linear_scope") or ""),
        "main_chain_case_count": int((execution_plan_summary or {}).get("main_chain_case_count") or 0),
        "independent_case_count": int((execution_plan_summary or {}).get("independent_case_count") or 0),
        "isolation_case_count": int((execution_plan_summary or {}).get("isolation_case_count") or 0),
        "broken_dependency_count": int((execution_plan_summary or {}).get("broken_dependency_count") or 0),
        "state_conflict_count": int((execution_plan_summary or {}).get("state_conflict_count") or 0),
        "role_switch_count": int((execution_plan_summary or {}).get("role_switch_count") or 0),
        "priority_decision_state_breakdown": dict(priority_decision_state_breakdown),
        "priority_final_breakdown": dict(priority_final_breakdown),
        "legacy_priority_breakdown": dict(legacy_priority_breakdown),
        "priority_conflict_count": int(priority_conflict_count),
        "priority_undetermined_count": int(priority_undetermined_count),
        "priority_optional_count": int(priority_optional_count),
        "priority_invalid_count": int(priority_invalid_count),
        "priority_quality_gate_failed": bool(priority_invalid_count > 0),
        "invalid_case_count": int(
            sum(1 for row in review_decision_table if str(row.get("case_quality") or "") == "invalid_case")
        ),
        "reasoning_leakage_case_count": int(
            sum(1 for row in review_decision_table if str(row.get("invalid_case_reason") or "") == "reasoning_leakage")
        ),
        "needs_priority_review": bool(needs_priority_review),
        "review_llm_filter_applied": bool(review_llm_applied),
        "review_input_size": int(len([item for item in review_selection_input if isinstance(item, dict)])),
            "review_output_size": int(review_selected_count or 0),
        "review_target_min_count": int(review_target_min_count or 1),
        "review_target_max_count": int(review_target_max_count or review_target_min_count or 1),
        "review_shortfall_detected": bool(review_shortfall_detected),
        "review_shortfall_before_count": int(review_shortfall_before_count),
        "review_shortfall_recovered_count": int(review_shortfall_recovered_count),
        "review_post_rerank_floor_count": int(review_post_rerank_floor_count or 1),
        "review_post_rerank_recovered_count": int(review_post_rerank_recovered_count or 0),
        "final_target_floor_count": int(final_target_floor_count or 0),
        "final_floor_recovery_attempted": bool(final_floor_recovery_attempted),
        "final_floor_recovery_applied": bool(final_floor_recovery_applied),
        "final_floor_recovered_count": int(final_floor_recovered_count or 0),
        "final_floor_recovery_reason": str(final_floor_recovery_reason or ""),
        "final_confirmed_conflict_drop_count": int(final_confirmed_conflict_drop_count or 0),
        "final_shortfall_supplement_attempted": bool(final_shortfall_supplement_attempted),
        "final_shortfall_supplement_applied": bool(final_shortfall_supplement_applied),
        "final_shortfall_supplement_count": int(final_shortfall_supplement_count or 0),
        "final_shortfall_supplement_reason": str(final_shortfall_supplement_reason or ""),
        "review_fill_source": str(review_fill_source or "none"),
            "review_llm_selected_count": int(len(review_llm_selected_signatures)),
        "review_llm_runtime_debug": dict(review_llm_runtime_debug),
        "review_constraint_selected_count": int(len(review_constraint_retained_signatures)),
        "review_llm_drop_reason_breakdown": review_llm_drop_reason_counts,
        "review_llm_drop_reason_raw_breakdown": review_llm_drop_reason_raw_counts,
        "review_llm_drop_reason_source_breakdown": review_llm_drop_reason_source_counts,
        "fallback_reason_incomplete": bool(fallback_reason_incomplete),
        "final_reason_incomplete": bool(final_reason_incomplete),
        "final_reason_coverage_ratio": float(final_reason_coverage_ratio),
        "fallback_dropped_reason_count": int(fallback_dropped_reason_count),
        "fallback_dropped_reason_mapped_count": int(fallback_dropped_reason_mapped_count),
        "fallback_dropped_reason_unmapped_count": int(fallback_dropped_reason_unmapped_count),
        "fallback_reason_coverage_ratio": float(fallback_reason_coverage_ratio),
        "llm_reason_coverage_ratio": float(llm_reason_coverage_ratio),
        "deterministic_backfill_ratio": float(deterministic_backfill_ratio),
        "reason_source_breakdown": {
            "primary": int(review_llm_drop_reason_source_counts.get("llm", 0)),
            "fallback": int(review_llm_drop_reason_source_counts.get("fallback_llm", 0)),
            "backfill": int(review_llm_drop_reason_source_counts.get("deterministic_backfill", 0)),
        },
        "primary_reason_incomplete": bool(review_llm_runtime_debug.get("primary_reason_incomplete")),
        "primary_dropped_reason_count": int(review_llm_runtime_debug.get("primary_dropped_reason_count") or 0),
        "primary_dropped_reason_payload_count": int(
            review_llm_runtime_debug.get("primary_dropped_reason_payload_count") or 0
        ),
        "primary_reason_coverage_ratio": float(review_llm_runtime_debug.get("primary_reason_coverage_ratio") or 0.0),
        "fallback_with_positive_evidence_count": int(fallback_with_positive_evidence_count),
        "fallback_without_positive_evidence_count": int(fallback_without_positive_evidence_count),
        "review_llm_pool_count": int(review_llm_pool_count),
        "candidate_by_pass": {
            "primary": int(stage_counts.get("primary") or 0),
            "gap": int(stage_counts.get("gap") or 0),
        },
        "must_keep_candidate_count": int(sum(1 for row in review_decision_table if bool(row.get("must_keep_candidate")))),
        "must_keep_retained_count": int(
            sum(
                1
                for row in review_decision_table
                if bool(row.get("must_keep_candidate")) and bool(row.get("retained_final"))
            )
        ),
        "must_keep_dropped_count": int(
            sum(
                1
                for row in review_decision_table
                if bool(row.get("must_keep_candidate")) and not bool(row.get("retained_final"))
            )
        ),
        "drop_by_review_llm_count": int(drop_by_review_llm_count),
        "drop_by_review_selector_count": int(sum(1 for row in dropped_rows if row.get("dropped_stage") == "review_selector")),
        "drop_by_review_gate_count": int(sum(1 for row in dropped_rows if row.get("dropped_stage") == "review_gate")),
        "drop_by_pre_gate_dedup_count": int(
            sum(1 for row in dropped_rows if row.get("dropped_stage") == "review_dedup_pre_gate")
        ),
        "drop_by_post_review_dedup_count": int(
            sum(1 for row in dropped_rows if row.get("dropped_stage") == "post_review_dedup_or_reorder")
        ),
        "drop_no_new_signal_count": int(
            sum(1 for row in dropped_rows if row.get("dropped_reason") == "drop_no_new_rule_no_new_bucket_no_high_signal")
        ),
        "drop_rule_cap_count": int(sum(1 for row in dropped_rows if row.get("dropped_reason") == "drop_rule_cap")),
        "drop_ui_like_redundant_count": int(
            sum(1 for row in dropped_rows if row.get("dropped_reason") == "drop_ui_like_redundant_case")
        ),
        "drop_ui_like_ratio_cap_count": int(
            sum(1 for row in dropped_rows if row.get("dropped_reason") == "drop_ui_like_ratio_cap")
        ),
        "drop_outside_target_window_count": int(
            sum(1 for row in dropped_rows if row.get("dropped_reason") == "drop_outside_target_window")
        ),
        "drop_by_rerank_low_signal_count": int(
            sum(1 for row in dropped_rows if row.get("dropped_reason") == "drop_no_new_rule_no_new_bucket_no_high_signal")
        ),
        "dropped_model_priority_p0_p1_count": int(
            sum(1 for row in dropped_rows if str(row.get("model_priority_current") or "").upper() in {"P0", "P1"})
        ),
        "dropped_core_rule_hit_count": int(sum(1 for row in dropped_rows if bool(row.get("core_rule_hits")))),
        "dropped_missing_rule_hit_count": int(sum(1 for row in dropped_rows if bool(row.get("missing_rule_hits")))),
        "dropped_high_signal_count": int(sum(1 for row in dropped_rows if bool(row.get("high_signal")))),
        "dropped_has_coverage_value_count": int(sum(1 for row in dropped_rows if bool(row.get("has_coverage_value")))),
        "retained_due_to_coverage_value_count": int(
            sum(1 for row in review_decision_table if str(row.get("retained_reason") or "") == "retained_due_to_coverage_value")
        ),
        "must_cover_rule_hit_count": int(sum(1 for row in review_decision_table if bool(row.get("hit_must_cover_rule")))),
        "forbidden_pattern_violation_count": int(
            sum(1 for row in review_decision_table if bool(row.get("violates_forbidden_pattern")))
        ),
        "soft_constraint_hit_count": int(sum(1 for row in review_decision_table if bool(row.get("hits_soft_constraint")))),
        "quality_hint_satisfied_count": int(sum(1 for row in review_decision_table if bool(row.get("satisfies_quality_hint")))),
        "drop_ui_like_ratio_postprocess_count": int(ui_like_ratio_postprocess_drop_count),
        "drop_final_description_duplicate_count": int(len(final_description_dedup_drop_signatures)),
    }

    coverage = {
        "kind": "coverage_check",
        **pre_priority_coverage,
    }
    missing_rules_final = list(coverage.get("missing_rules") or [])
    missing_types_final = any(
        bool(item.get("missing_types"))
        for item in (coverage.get("rule_diagnostics") or [])
        if isinstance(item, dict)
    )
    reference_gap = max(0, int(reference_count_effective or 0) - int(final_count or 0))
    converged = not bool(missing_rules_final) and not bool(missing_types_final)

    reasons: list[str] = []
    if not converged:
        if int(gap_remaining_after_attempts or 0) > 0:
            reasons.append("coverage_gap_still_exists")
        if int(gap_attempts or 0) >= 3 and int(gap_remaining_after_attempts or 0) > 0:
            reasons.append("gap_attempt_limit_reached")
        if gap_stopped_by_provider_error:
            reasons.append("gap_stopped_by_provider_error")
        if missing_rules_final:
            reasons.append("coverage_missing_rules")
        if missing_types_final:
            reasons.append("coverage_missing_types")
        if not reasons:
            reasons.append("coverage_not_converged")
    elif reference_gap > 0:
        reasons.append("quality_converged_before_reference_count")

    final_description_dedup_drop = int(len(final_description_dedup_drop_signatures or set()))
    total_dedup_drop = int(post_review_dedup_drop or 0) + int(final_description_dedup_drop or 0)
    effective_low_quality_dropped_total = max(
        int(low_quality_dropped_total or 0),
        int(len([item for item in (low_quality_drop_details or []) if isinstance(item, dict)])),
    )

    if post_review_dedup_drop > 0:
        reasons.append("dedup_reduced_count_stop")
        reasons.append("no_backfill_after_dedup")
    if final_description_dedup_drop > 0:
        reasons.append("final_description_dedup_reduced_count")

    if effective_low_quality_dropped_total > 0:
        reasons.append("low_quality_filtered")
    if int(semantic_dedup_dropped_total or 0) > 0:
        reasons.append("semantic_dedup_reduced_count")
    if int(governance_hard_drop_total or 0) > 0:
        reasons.append("governance_hard_drop_applied")
    if int(append_cap_drop_total or 0) > 0:
        reasons.append("append_target_cap_applied")
    if int(flow_governance_summary.get("scenario_duplicate_pruned_count") or 0) > 0:
        reasons.append("flow_scenario_duplicate_pruned")
    if bool(flow_governance_summary.get("flow_reordered")):
        reasons.append("flow_structure_reordered")

    duplication_rate_estimate = 0.0
    if int(review_selected_count or 0) > 0:
        duplication_rate_estimate = float(total_dedup_drop or 0) / float(review_selected_count or 1)

    summary_stop_reason: list[str] = []
    if converged:
        summary_stop_reason.append("coverage_satisfied")

    diminishing_returns = bool(
        reference_gap > 0
        or post_review_dedup_drop > 0
        or duplication_rate_estimate > 0.5
        or "quality_converged_before_reference_count" in reasons
    )
    if diminishing_returns or (not converged and reasons):
        summary_stop_reason.append("stopped_due_to_diminishing_returns")

    if converged:
        summary_stop_reason.append("optimal_case_set_reached")

    if converged and effective_low_quality_dropped_total <= 0 and duplication_rate_estimate <= 0.5:
        quality_assessment = "high"
    elif effective_low_quality_dropped_total <= 2 and duplication_rate_estimate <= 0.6:
        quality_assessment = "medium"
    else:
        quality_assessment = "low"
    priority_conflict_count = int(review_decision_summary.get("priority_conflict_count") or 0)
    priority_undetermined_count = int(review_decision_summary.get("priority_undetermined_count") or 0)
    priority_optional_count = int(review_decision_summary.get("priority_optional_count") or 0)
    needs_priority_review = bool(
        review_decision_summary.get("needs_priority_review")
        or priority_conflict_count > 0
        or priority_undetermined_count > 0
    )

    target_min = generation_target_case_range.get("min") if isinstance(generation_target_case_range, dict) else None
    target_max = generation_target_case_range.get("max") if isinstance(generation_target_case_range, dict) else None
    recommended_range = (
        f"{int(target_min)}-{int(target_max)}"
        if target_min is not None and target_max is not None
        else "30-50"
    )
    try:
        target_min_count = int(target_min or 0)
    except Exception:
        target_min_count = 0
    try:
        target_max_count = int(target_max or 0)
    except Exception:
        target_max_count = 0
    expected_count_explicit = bool(int(expected_count or 0) > 0)
    target_final_count = int(expected_count or reference_count_effective or 0) if expected_count_explicit else int(
        reference_count_effective or 0
    )
    if target_final_count <= 0 and target_min is not None and target_max is not None:
        target_final_count = int(round((int(target_min) + int(target_max)) / 2))
    soft_min_count = int(round(float(target_final_count or 0) * 0.80)) if target_final_count > 0 else 0
    hard_min_count = int(round(float(target_final_count or 0) * 0.70)) if target_final_count > 0 else 0
    if str(generation_coverage_mode or "") == "full_functional_regression":
        try:
            full_floor = max(85, int(target_min or 0))
        except Exception:
            full_floor = 85
        hard_min_count = max(int(hard_min_count or 0), int(full_floor or 0))
    valid_unique_candidate_count = int(candidate_count_before_review or 0)
    postprocess_pruned_count = int(post_review_dedup_drop or 0) + int(final_description_dedup_drop or 0) + int(
        semantic_dedup_dropped_total or 0
    ) + int(flow_governance_summary.get("scenario_duplicate_pruned_count") or 0) + int(
        effective_low_quality_dropped_total or 0
    ) + int(governance_hard_drop_total or 0)
    recommended_floor_underfilled = bool(
        expected_count_explicit
        and str(generation_coverage_mode or "") != "full_functional_regression"
        and target_min_count > 0
        and int(valid_unique_candidate_count or 0) >= int(target_min_count)
        and int(final_count or 0) < int(target_min_count)
        and int(postprocess_pruned_count or 0) > 0
    )
    if expected_count_explicit and target_final_count > 0:
        if str(generation_coverage_mode or "") == "full_functional_regression":
            min_acceptable_final = hard_min_count
        elif valid_unique_candidate_count >= int(round(float(target_final_count) * 0.90)):
            min_acceptable_final = soft_min_count
        else:
            min_acceptable_final = min(valid_unique_candidate_count, hard_min_count)
        if recommended_floor_underfilled:
            min_acceptable_final = max(int(min_acceptable_final or 0), int(target_min_count or 0))
    else:
        min_acceptable_final = 0
    target_satisfaction_denominator = int(target_final_count or 0)
    if recommended_floor_underfilled and target_max_count > 0:
        target_satisfaction_denominator = max(int(target_satisfaction_denominator or 0), int(target_max_count or 0))
    target_satisfaction_ratio = round(
        float(final_count or 0) / float(target_satisfaction_denominator or 1),
        4,
    ) if target_satisfaction_denominator > 0 else 1.0
    target_warning = bool(expected_count_explicit and target_final_count > 0 and int(final_count or 0) < soft_min_count)
    underfilled = bool(
        expected_count_explicit
        and min_acceptable_final > 0
        and int(final_count or 0) < int(min_acceptable_final)
    )
    quality_rejected_count = int(effective_low_quality_dropped_total or 0) + int(governance_hard_drop_total or 0)
    try:
        quality_rejected_count += int(judge_summary_payload.get("reject_count") or 0)
    except Exception:
        pass
    judge_reject_count = int(judge_summary_payload.get("rejected_out_count") or judge_summary_payload.get("reject_count") or 0)
    judge_pending_count = int(judge_summary_payload.get("pending_out_count") or judge_summary_payload.get("pending_count") or 0)
    judge_pass_count = int(
        judge_summary_payload.get("confirmed_pass_out_count")
        or judge_summary_payload.get("pass_count")
        or 0
    ) + int(judge_summary_payload.get("repaired_pass_out_count") or 0)
    final_input_count = int(judge_pass_count or review_selected_count or 0)
    final_non_judge_drop_count = max(0, int(final_input_count or 0) - int(final_count or 0))
    scenario_duplicate_pruned_count = int(flow_governance_summary.get("scenario_duplicate_pruned_count") or 0)
    post_review_dedup_reorder_drop_count = max(
        0,
        int(final_non_judge_drop_count or 0)
        - int(scenario_duplicate_pruned_count or 0)
        - int(final_description_dedup_drop or 0),
    )
    review_selector_pruned_count = int(drop_by_review_llm_count or 0) + int(
        review_decision_summary.get("drop_by_review_gate_count") or 0
    )
    duplicate_pruned_count = int(total_dedup_drop or 0) + int(semantic_dedup_dropped_total or 0)
    invalid_pruned_count = int(postprocess_filter_drop_total or 0) + int(governance_hard_drop_total or 0)
    if not underfilled:
        underfill_reason = ""
    elif valid_unique_candidate_count < hard_min_count:
        underfill_reason = "valid_candidate_insufficient"
    elif (
        scenario_duplicate_pruned_count > 0
        and scenario_duplicate_pruned_count >= review_selector_pruned_count
        and duplicate_pruned_count <= 0
    ):
        underfill_reason = "scenario_cap_over_pruned"
    elif review_selector_pruned_count > duplicate_pruned_count and review_selector_pruned_count >= quality_rejected_count:
        underfill_reason = "review_selector_over_pruned"
    elif duplicate_pruned_count >= quality_rejected_count:
        underfill_reason = "duplicate_pruned_under_target"
    elif quality_rejected_count > 0:
        underfill_reason = "quality_rejected_under_target"
    else:
        underfill_reason = "final_count_below_expected_target"
    if not underfilled:
        underfill_root_cause = ""
    elif underfill_reason == "scenario_cap_over_pruned":
        underfill_root_cause = "final_stage_over_pruning"
    elif underfill_reason in {"duplicate_pruned_under_target", "quality_rejected_under_target"} and final_non_judge_drop_count > review_selector_pruned_count:
        underfill_root_cause = "final_stage_over_pruning"
    elif underfill_reason == "review_selector_over_pruned":
        underfill_root_cause = "review_stage_over_pruning"
    elif underfill_reason == "valid_candidate_insufficient":
        underfill_root_cause = "candidate_insufficient"
    else:
        underfill_root_cause = "target_not_satisfied"
    if not underfilled:
        underfill_level = ""
    else:
        shortfall = max(0, int(min_acceptable_final or 0) - int(final_count or 0))
        if shortfall <= 5:
            underfill_level = "mild"
        elif shortfall <= 20:
            underfill_level = "moderate"
        else:
            underfill_level = "severe"
    if underfilled and "underfilled" not in reasons:
        reasons.append("underfilled")
    if underfilled and "underfilled" not in summary_stop_reason:
        summary_stop_reason.append("underfilled")
    if target_warning and "target_count_warning" not in reasons:
        reasons.append("target_count_warning")
    if target_warning and "target_count_warning" not in summary_stop_reason:
        summary_stop_reason.append("target_count_warning")

    final_priority_breakdown: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0, "UNKNOWN": 0}
    final_execution_group_breakdown: dict[str, int] = {}
    final_module_breakdown: dict[str, int] = {}
    final_display_case_count = 0
    final_display_tokens = (
        "display",
        "ui-only",
        "copy",
        "style",
        "layout",
        "analytics",
        "tracking",
        "\u5c55\u793a",
        "\u6587\u6848",
        "\u6837\u5f0f",
        "\u5e03\u5c40",
        "\u57cb\u70b9",
    )
    for final_case in (parsed_result or []):
        if not isinstance(final_case, dict):
            continue
        priority_key = str(
            final_case.get("priority_final")
            or final_case.get("priority")
            or final_case.get("model_priority")
            or "UNKNOWN"
        ).strip().upper()
        if priority_key not in final_priority_breakdown:
            priority_key = "UNKNOWN"
        final_priority_breakdown[priority_key] = int(final_priority_breakdown.get(priority_key, 0)) + 1
        group_key = str(final_case.get("execution_group") or "unknown").strip() or "unknown"
        final_execution_group_breakdown[group_key] = int(final_execution_group_breakdown.get(group_key, 0)) + 1
        module_key = str(final_case.get("test_module") or final_case.get("module") or "").strip()
        if module_key:
            final_module_breakdown[module_key] = int(final_module_breakdown.get(module_key, 0)) + 1
        merged_case_text = " ".join(
            str(final_case.get(key) or "")
            for key in ("execution_group", "test_module", "description", "expected_result")
        ).lower()
        if group_key == "display" or any(token in merged_case_text for token in final_display_tokens):
            final_display_case_count += 1
    final_case_denominator = max(1, int(final_count or 0))
    final_high_priority_count = int(final_priority_breakdown.get("P0", 0)) + int(final_priority_breakdown.get("P1", 0))
    final_module_breakdown_top = {
        key: int(value)
        for key, value in sorted(
            final_module_breakdown.items(),
            key=lambda item: (-int(item[1]), item[0]),
        )[:20]
    }

    generation_summary = {
        "recommended_range": recommended_range,
        "generation_coverage_mode": str(generation_coverage_mode or "core_smoke"),
        "expected_count": int(expected_count or 0),
        "expected_count_explicit": bool(expected_count_explicit),
        "recommended_min": int(target_min or 0) if target_min is not None else 0,
        "recommended_max": int(target_max or 0) if target_max is not None else 0,
        "target_final_count": int(target_final_count or 0),
        "soft_min_count": int(soft_min_count or 0),
        "hard_min_count": int(hard_min_count or 0),
        "min_acceptable_final": int(min_acceptable_final or 0),
        "target_satisfaction_ratio": float(target_satisfaction_ratio),
        "underfilled": bool(underfilled),
        "underfill_level": str(underfill_level),
        "underfill_reason": str(underfill_reason),
        "underfill_root_cause": str(underfill_root_cause),
        "final_count": int(final_count or 0),
        "status": "completed_underfilled" if underfilled else (
            "completed_with_quality_stop" if not converged else "completed_with_optimal_set"
        ),
        "stop_reason": summary_stop_reason,
        "quality_assessment": quality_assessment,
        "needs_priority_review": bool(needs_priority_review),
        "priority_conflict_count": int(priority_conflict_count),
        "priority_undetermined_count": int(priority_undetermined_count),
        "priority_optional_count": int(priority_optional_count),
        "final_priority_breakdown": {
            key: int(value)
            for key, value in final_priority_breakdown.items()
            if int(value) > 0
        },
        "final_execution_group_breakdown": dict(final_execution_group_breakdown),
        "final_module_breakdown_top": final_module_breakdown_top,
        "final_display_case_count": int(final_display_case_count),
        "final_display_ratio": round(float(final_display_case_count) / float(final_case_denominator), 4),
        "final_high_priority_ratio": round(float(final_high_priority_count) / float(final_case_denominator), 4),
    }

    convergence_debug = {
        "suggested_count": int(reference_count_effective or 0),
        "final_count": int(final_count or 0),
        "reference_gap": int(reference_gap or 0),
        "converged": bool(converged),
        "duplication_rate_estimate": float(duplication_rate_estimate),
        "primary_count": int(stage_counts.get("primary") or 0),
        "gap_count": int(stage_counts.get("gap") or 0),
        "review_count": int(stage_counts.get("review") or 0),
        "candidate_count_before_review": int(candidate_count_before_review or 0),
        "review_selected_count": int(review_selected_count or 0),
        "post_review_dedup_drop": int(post_review_dedup_drop or 0),
        "judge_reject_count": int(judge_reject_count or 0),
        "judge_pending_count": int(judge_pending_count or 0),
        "judge_pass_count": int(judge_pass_count or 0),
        "final_input_count": int(final_input_count or 0),
        "final_output_count": int(final_count or 0),
        "final_non_judge_drop_count": int(final_non_judge_drop_count or 0),
        "scenario_duplicate_pruned_count": int(scenario_duplicate_pruned_count or 0),
        "post_review_dedup_reorder_drop_count": int(post_review_dedup_reorder_drop_count or 0),
        "final_description_dedup_drop_count": int(final_description_dedup_drop or 0),
        "total_dedup_drop_count": int(total_dedup_drop or 0),
        "gap_attempts": int(gap_attempts or 0),
        "gap_remaining_after_attempts": int(gap_remaining_after_attempts or 0),
        "missing_rules_count": int(len(missing_rules_final)),
        "missing_types_exists": bool(missing_types_final),
        "low_quality_dropped_count": int(effective_low_quality_dropped_total or 0),
        "low_quality_dropped_examples": [
            dict(item)
            for item in (low_quality_drop_details or [])[:10]
            if isinstance(item, dict)
        ],
        "postprocess_filter_drop_total": int(postprocess_filter_drop_total or 0),
        "semantic_dedup_dropped_count": int(semantic_dedup_dropped_total or 0),
        "governance_hard_drop_count": int(governance_hard_drop_total or 0),
        "duplicate_pruned_count": int(duplicate_pruned_count or 0),
        "invalid_pruned_count": int(invalid_pruned_count or 0),
        "quality_rejected_count": int(quality_rejected_count or 0),
        "review_selector_pruned_count": int(review_selector_pruned_count or 0),
        "valid_unique_candidate_count": int(valid_unique_candidate_count or 0),
        "expected_count": int(expected_count or 0),
        "expected_count_explicit": bool(expected_count_explicit),
        "recommended_min": int(target_min or 0) if target_min is not None else 0,
        "recommended_max": int(target_max or 0) if target_max is not None else 0,
        "target_final_count": int(target_final_count or 0),
        "soft_min_count": int(soft_min_count or 0),
        "hard_min_count": int(hard_min_count or 0),
        "min_acceptable_final": int(min_acceptable_final or 0),
        "target_satisfaction_ratio": float(target_satisfaction_ratio),
        "underfilled": bool(underfilled),
        "underfill_level": str(underfill_level),
        "underfill_reason": str(underfill_reason),
        "underfill_root_cause": str(underfill_root_cause),
        "append_target_count": int(append_target_count or 0),
        "append_final_cap_count": int(append_final_cap_count or 0),
        "append_cap_drop_count": int(append_cap_drop_total or 0),
        "flow_governance": dict(flow_governance_summary or {}),
        "needs_priority_review": bool(needs_priority_review),
        "priority_conflict_count": int(priority_conflict_count),
        "priority_undetermined_count": int(priority_undetermined_count),
        "priority_optional_count": int(priority_optional_count),
        "reasons": reasons,
        "generation_coverage_mode": str(generation_coverage_mode or "core_smoke"),
        "generation_target_case_range": dict(generation_target_case_range or {}),
    }
    return {
        "cases": parsed_result,
        "stage_counts": stage_counts,
        "coverage": coverage,
        "convergence_debug": convergence_debug,
        "generation_summary": generation_summary,
        "review_decision_summary": review_decision_summary,
        "review_decision_table": review_decision_table,
        "judge_summary": judge_summary_payload,
        "judge_decision_table": judge_decision_table_payload,
        "feedback_control_debug": {
            "control_state_applied": bool(control_state.has_signals()),
            "generation_coverage_mode": str(generation_coverage_mode or "core_smoke"),
            "generation_target_case_range": dict(generation_target_case_range or {}),
            "fact_profile_source": str(fact_profile.get("profile_source") or ""),
            "fact_profile_confidence": float(fact_profile.get("confidence") or 0.0),
            "fact_profile_confirmed_count": int(len(fact_profile.get("confirmed_facts") or [])),
            "fact_profile_pending_count": int(len(fact_profile.get("pending_items") or [])),
            "fact_profile_forbidden_count": int(len(fact_profile.get("forbidden_facts") or [])),
            "project_profile_source": str(project_profile.get("profile_source") or ""),
            "project_profile_confidence": float(project_profile.get("confidence") or 0.0),
            "project_profile_flow_count": int(
                len((dict(project_profile.get("flow_outline") or {})).get("flow_order") or [])
            ),
            "manual_quality_profile_source": str(manual_quality_profile.get("profile_source") or ""),
            "manual_quality_profile_version": str(manual_quality_profile.get("profile_version") or ""),
            "manual_quality_profile_trusted_count": int(manual_quality_profile.get("trusted_sample_count") or 0),
            "manual_quality_profile_high_priority_ratio": float(
                manual_quality_profile.get("high_priority_ratio") or 0.0
            ),
            "manual_quality_profile_display_ratio_cap": float(
                manual_quality_profile.get("display_ratio_cap") or 0.0
            ),
            "must_cover_rules_count": int(len(control_state.must_cover_rules or [])),
            "rule_quota_keys": sorted(list((control_state.rule_quota or {}).keys())),
            "soft_constraints_count": int(len(control_state.soft_constraints or [])),
            "quality_fix_hints_count": int(len(control_state.quality_fix_hints or [])),
            "preferred_patterns_count": int(len(control_state.preferred_patterns or [])),
            "forbidden_patterns_count": int(len(control_state.forbidden_patterns or [])),
            "source_meta": dict(control_state.source_meta or {}),
        },
    }

