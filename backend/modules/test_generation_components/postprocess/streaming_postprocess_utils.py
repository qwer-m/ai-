from __future__ import annotations

import json
from typing import Any, Callable, Iterable

RETRYABLE_RESPONSE_ERROR_REASONS = {"empty_response", "error_response"}


def _dict_case_items(items: Iterable[Any]) -> list[dict[str, Any]]:
    return [item for item in items if isinstance(item, dict)]


def _dict_case_count(items: Iterable[Any]) -> int:
    return len(_dict_case_items(items))


def _dict_case_copies(items: Iterable[Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in items if isinstance(item, dict)]


def _merged_unique_total(
    new_cases: Any,
    *,
    append: bool,
    existing_cases: list[dict[str, Any]],
    count_unique_test_cases_fn: Callable[[list[dict[str, Any]]], int],
) -> int:
    merged: list[dict[str, Any]] = []
    if append and isinstance(existing_cases, list):
        merged.extend(existing_cases)
    if isinstance(new_cases, list):
        merged.extend(new_cases)
    return count_unique_test_cases_fn(merged)


def _case_execution_group(case: dict[str, Any], default: str = "") -> str:
    if not isinstance(case, dict):
        return default
    group = str(case.get("execution_group") or default).strip()
    return group or default


def _clip_text(value: Any, limit: int, *, strip: bool = False) -> str:
    text = str(value or "")
    if strip:
        text = text.strip()
    return text[: max(0, int(limit))]


def _json_for_prompt(value: Any, *, limit: int | None = None, compact: bool = False) -> str:
    if compact:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(value, ensure_ascii=False)
    return text if limit is None else _clip_text(text, int(limit))


def _rule_diagnostics_payload(coverage_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(coverage_context, dict):
        return {"rule_diagnostics": []}
    return {"rule_diagnostics": coverage_context.get("rule_diagnostics") or []}


def _cases_and_trace_from_result(result: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(result, tuple) and len(result) == 2:
        return _dict_case_items(result[0] or []), dict(result[1] or {})
    return _dict_case_items(result or []), {}


def _client_response_metadata(client: Any) -> dict[str, Any]:
    return dict(getattr(client, "last_response_metadata", {}) or {})


def _select_review_model(client: Any, prompt: str) -> str:
    try:
        return str(client.select_model(prompt, "review"))
    except Exception:
        return ""


def _parsed_response_error_reason(response_text: Any, parsed_payload: Any) -> str:
    payload_trimmed = str(response_text or "").strip()
    if not payload_trimmed:
        return "empty_response"
    if payload_trimmed.startswith(("Error:", "Exception")):
        return "error_response"
    if isinstance(parsed_payload, dict) and bool(str(parsed_payload.get("error") or "").strip()):
        return "schema_parse_error"
    return ""


def _review_payload_debug_counts(result: dict[str, Any] | None) -> dict[str, int]:
    payload = result if isinstance(result, dict) else {}
    return {
        "mapped_count": _dict_case_count(payload.get("mapped") or []),
        "mapped_signature_count": int(len(payload.get("mapped_signatures") or set())),
        "dropped_reason_count": int(len(payload.get("dropped_reason_map") or {})),
        "dropped_reason_payload_count": int(payload.get("dropped_reason_payload_count") or 0),
        "dropped_reason_unmapped_count": int(payload.get("dropped_reason_unmapped_count") or 0),
    }


def _fact_profile_debug_fields(fact_profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = fact_profile if isinstance(fact_profile, dict) else {}
    return {
        "fact_profile_source": str(profile.get("profile_source") or ""),
        "fact_profile_confidence": float(profile.get("confidence") or 0.0),
        "fact_profile_confirmed_count": int(len(profile.get("confirmed_facts") or [])),
        "fact_profile_forbidden_count": int(len(profile.get("forbidden_facts") or [])),
        "fact_profile_pending_count": int(len(profile.get("pending_items") or [])),
    }


def _project_profile_debug_fields(
    project_profile: dict[str, Any] | None,
    *,
    include_flow_count: bool = False,
) -> dict[str, Any]:
    profile = project_profile if isinstance(project_profile, dict) else {}
    fields: dict[str, Any] = {
        "project_profile_source": str(profile.get("profile_source") or ""),
        "project_profile_confidence": float(profile.get("confidence") or 0.0),
    }
    if include_flow_count:
        flow_outline = dict(profile.get("flow_outline") or {})
        fields["project_profile_flow_count"] = int(len(flow_outline.get("flow_order") or []))
    return fields


def build_feedback_control_debug_payload(
    *,
    control_state: Any,
    generation_coverage_mode: str,
    generation_target_case_range: dict[str, Any] | None,
    fact_profile: dict[str, Any] | None,
    project_profile: dict[str, Any] | None,
    manual_quality_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    manual_profile = manual_quality_profile if isinstance(manual_quality_profile, dict) else {}
    return {
        "control_state_applied": bool(control_state.has_signals()),
        "generation_coverage_mode": str(generation_coverage_mode or "core_smoke"),
        "generation_target_case_range": dict(generation_target_case_range or {}),
        **_fact_profile_debug_fields(fact_profile),
        **_project_profile_debug_fields(project_profile, include_flow_count=True),
        "manual_quality_profile_source": str(manual_profile.get("profile_source") or ""),
        "manual_quality_profile_version": str(manual_profile.get("profile_version") or ""),
        "manual_quality_profile_trusted_count": int(manual_profile.get("trusted_sample_count") or 0),
        "manual_quality_profile_high_priority_ratio": float(manual_profile.get("high_priority_ratio") or 0.0),
        "manual_quality_profile_display_ratio_cap": float(manual_profile.get("display_ratio_cap") or 0.0),
        "must_cover_rules_count": int(len(control_state.must_cover_rules or [])),
        "rule_quota_keys": sorted(list((control_state.rule_quota or {}).keys())),
        "soft_constraints_count": int(len(control_state.soft_constraints or [])),
        "quality_fix_hints_count": int(len(control_state.quality_fix_hints or [])),
        "preferred_patterns_count": int(len(control_state.preferred_patterns or [])),
        "forbidden_patterns_count": int(len(control_state.forbidden_patterns or [])),
        "source_meta": dict(control_state.source_meta or {}),
    }


def _low_quality_filter_stats_delta(
    stats: dict[str, Any],
) -> tuple[int, int, int, int, list[dict[str, Any]]]:
    structural_drop = int(stats.get("invalid_structure_dropped") or 0) + int(
        stats.get("weak_case_dropped") or 0
    )
    semantic_dedup_drop = int(stats.get("semantic_dedup_dropped") or 0)
    governance_hard_drop = int(stats.get("governance_hard_drop") or 0)
    total_drop = int(stats.get("total_dropped") or 0)
    dropped_details = [
        dict(item)
        for item in (stats.get("dropped_details") or [])
        if isinstance(item, dict)
    ]
    return structural_drop, semantic_dedup_drop, governance_hard_drop, total_drop, dropped_details


class LowQualityFilterStatsAccumulator:
    def __init__(self, initial_stats: dict[str, Any] | None = None) -> None:
        self.low_quality_structural_dropped_total = 0
        self.final_quality_dropped_total = 0
        self.semantic_dedup_dropped_total = 0
        self.governance_hard_drop_total = 0
        self.postprocess_filter_drop_total = 0
        self.low_quality_dropped_total = 0
        self.dropped_details: list[dict[str, Any]] = []
        if initial_stats is not None:
            self.accumulate(initial_stats)

    @property
    def drop_details(self) -> list[dict[str, Any]]:
        return self.dropped_details

    @property
    def low_quality_drop_details(self) -> list[dict[str, Any]]:
        return self.dropped_details

    def accumulate(self, stats: dict[str, Any]) -> None:
        (
            structural_drop,
            semantic_dedup_drop,
            governance_hard_drop,
            total_drop,
            dropped_details,
        ) = _low_quality_filter_stats_delta(stats)
        self.low_quality_structural_dropped_total += int(structural_drop)
        self.semantic_dedup_dropped_total += int(semantic_dedup_drop)
        self.governance_hard_drop_total += int(governance_hard_drop)
        self.low_quality_dropped_total = int(
            self.low_quality_structural_dropped_total
            + self.final_quality_dropped_total
        )
        self.postprocess_filter_drop_total += int(total_drop)
        self.dropped_details.extend(dropped_details)

    def add_postprocess_quality_drop(self, drop_total: int) -> None:
        count = int(drop_total or 0)
        if count <= 0:
            return
        self.final_quality_dropped_total += count
        self.low_quality_dropped_total = int(
            self.low_quality_structural_dropped_total
            + self.final_quality_dropped_total
        )
        self.postprocess_filter_drop_total += count


def _flow_profile_with_scenario_policy(
    project_profile: dict[str, Any] | None,
    **policy_updates: Any,
) -> dict[str, Any]:
    profile = dict(project_profile or {})
    scenario_policy = dict(profile.get("scenario_cluster_policy") or {})
    scenario_policy.update(policy_updates)
    profile["scenario_cluster_policy"] = scenario_policy
    return profile


def build_flow_project_profile_for_governance(
    project_profile: dict[str, Any] | None,
    *,
    generation_coverage_mode: str,
    feedback_redundant_caps: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = dict(project_profile or {})
    coverage_mode = str(generation_coverage_mode or "")
    if coverage_mode in {"expanded_regression", "full_functional_regression"}:
        profile = _flow_profile_with_scenario_policy(
            profile,
            coverage_mode=coverage_mode,
            intent_duplicate_cap=1,
            strict_duplicate_policy=coverage_mode == "expanded_regression",
        )

    feedback_caps = dict(feedback_redundant_caps or {})
    if not feedback_caps:
        return profile

    scenario_policy = dict(profile.get("scenario_cluster_policy") or {})
    scenario_caps = dict(scenario_policy.get("scenario_caps") or {})
    for scenario_key, cap in feedback_caps.items():
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
    profile["scenario_cluster_policy"] = scenario_policy
    return profile


def resolve_generation_coverage_profile(
    *,
    expected_count: Any,
    generation_mode: str,
    generation_coverage_mode: str,
    full_regression_recommended_floor: int = 85,
) -> dict[str, Any]:
    mode_rank = {
        "core_smoke": 0,
        "standard_regression": 1,
        "expanded_regression": 2,
        "full_functional_regression": 3,
    }
    try:
        expected_count_value = max(0, int(expected_count or 0))
    except Exception:
        expected_count_value = 0

    effective_mode = str(generation_coverage_mode or "")
    effective_source = "feedback_control_state" if effective_mode in mode_rank else ""
    requested_mode = str(generation_mode or "").strip().lower()
    explicit_mode_override = False
    expected_count_mode = ""
    if expected_count_value >= 80:
        expected_count_mode = "full_functional_regression"
    elif expected_count_value >= 60:
        expected_count_mode = "expanded_regression"
    elif expected_count_value > 0:
        expected_count_mode = "standard_regression"

    if mode_rank.get(expected_count_mode, -1) > mode_rank.get(effective_mode, -1):
        effective_mode = expected_count_mode
        effective_source = "expected_count"
    if (
        requested_mode in mode_rank
        and mode_rank.get(requested_mode, -1) > mode_rank.get(effective_mode, -1)
    ):
        effective_mode = requested_mode
        effective_source = "generation_mode"
        explicit_mode_override = True
    if effective_mode not in mode_rank:
        effective_mode = expected_count_mode or "standard_regression"
        effective_source = "fallback"

    explicit_expected_count_floor_preserved = bool(
        expected_count_value > 0
        and expected_count_value < int(full_regression_recommended_floor or 0)
        and effective_mode == "full_functional_regression"
    )
    return {
        "expected_count_value": int(expected_count_value),
        "effective_generation_coverage_mode": effective_mode,
        "effective_generation_coverage_mode_source": effective_source,
        "explicit_generation_mode_override": bool(explicit_mode_override),
        "explicit_expected_count_floor_preserved": bool(explicit_expected_count_floor_preserved),
        "full_regression_recommended_floor": int(full_regression_recommended_floor or 0),
    }


def _resolve_full_regression_floor(
    *,
    explicit_expected_count_floor_preserved: bool,
    expected_count_value: int,
    generation_target_case_range: dict[str, Any] | None,
    full_regression_recommended_floor: int,
) -> int:
    if explicit_expected_count_floor_preserved:
        return int(expected_count_value or 0)
    try:
        profile_floor = int((generation_target_case_range or {}).get("min") or 0)
    except Exception:
        profile_floor = 0
    return max(int(full_regression_recommended_floor or 0), int(profile_floor or 0))


def resolve_generation_coverage_state(
    *,
    expected_count: Any,
    generation_mode: str,
    generation_coverage_mode: str,
    generation_target_case_range: dict[str, Any] | None,
    full_regression_recommended_floor: int = 85,
) -> dict[str, Any]:
    coverage_profile = resolve_generation_coverage_profile(
        expected_count=expected_count,
        generation_mode=generation_mode,
        generation_coverage_mode=generation_coverage_mode,
        full_regression_recommended_floor=full_regression_recommended_floor,
    )
    expected_count_value = int(coverage_profile.get("expected_count_value") or 0)
    resolved_full_regression_floor = _resolve_full_regression_floor(
        explicit_expected_count_floor_preserved=bool(
            coverage_profile.get("explicit_expected_count_floor_preserved")
        ),
        expected_count_value=expected_count_value,
        generation_target_case_range=generation_target_case_range,
        full_regression_recommended_floor=int(
            coverage_profile.get("full_regression_recommended_floor") or 0
        ),
    )
    effective_generation_coverage_mode = str(
        coverage_profile.get("effective_generation_coverage_mode") or ""
    )
    return {
        "coverage_profile": coverage_profile,
        "expected_count_value": expected_count_value,
        "full_regression_recommended_floor": int(
            coverage_profile.get("full_regression_recommended_floor") or 0
        ),
        "effective_generation_coverage_mode": effective_generation_coverage_mode,
        "effective_generation_coverage_mode_source": str(
            coverage_profile.get("effective_generation_coverage_mode_source") or ""
        ),
        "explicit_generation_mode_override": bool(
            coverage_profile.get("explicit_generation_mode_override")
        ),
        "generation_coverage_mode": effective_generation_coverage_mode,
        "explicit_expected_count_floor_preserved": bool(
            coverage_profile.get("explicit_expected_count_floor_preserved")
        ),
        "resolved_full_regression_floor": int(resolved_full_regression_floor or 0),
    }


def _resolve_expected_min_floor_for_recovery(
    *,
    expected_count_value: int,
    effective_generation_coverage_mode: str,
    valid_candidate_count: int,
    full_regression_floor: int,
) -> int:
    target_final_count = int(expected_count_value or 0)
    if target_final_count <= 0:
        return 0
    soft_min_count = int(round(float(target_final_count) * 0.80))
    hard_min_count = int(round(float(target_final_count) * 0.70))
    if str(effective_generation_coverage_mode or "") == "full_functional_regression":
        return max(int(hard_min_count or 0), int(full_regression_floor or 0))
    candidate_count = max(0, int(valid_candidate_count or 0))
    if candidate_count >= int(round(float(target_final_count) * 0.90)):
        return int(soft_min_count or 0)
    return min(candidate_count, int(hard_min_count or 0))
