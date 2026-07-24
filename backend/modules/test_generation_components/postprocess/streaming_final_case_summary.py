from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Callable

from .case_access import case_text_field
from .streaming_case_keys import final_description_dedup_key
from .streaming_postprocess_utils import _case_execution_group, _dict_case_items
from .streaming_ui_like import is_display_only_final_case

DisplayPredicate = Callable[[dict[str, Any]], bool]
DescriptionKeyFn = Callable[[dict[str, Any]], str]


def _dict_samples(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return []
    sample_limit = max(0, int(limit or 0))
    samples: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        samples.append(dict(item))
        if len(samples) >= sample_limit:
            break
    return samples


def final_case_breakdown(
    cases: list[dict[str, Any]],
    *,
    final_count: int,
    display_predicate: DisplayPredicate = is_display_only_final_case,
) -> dict[str, Any]:
    priority_breakdown: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0, "UNKNOWN": 0}
    execution_group_breakdown: dict[str, int] = {}
    module_breakdown: dict[str, int] = {}
    display_case_count = 0

    for final_case in _dict_case_items(cases):
        priority_key = str(
            final_case.get("priority_final")
            or final_case.get("priority")
            or final_case.get("model_priority")
            or "UNKNOWN"
        ).strip().upper()
        if priority_key not in priority_breakdown:
            priority_key = "UNKNOWN"
        priority_breakdown[priority_key] = int(priority_breakdown.get(priority_key, 0)) + 1

        group_key = _case_execution_group(final_case, "unknown")
        execution_group_breakdown[group_key] = int(execution_group_breakdown.get(group_key, 0)) + 1

        module_key = case_text_field(final_case, "test_module")
        if module_key:
            module_breakdown[module_key] = int(module_breakdown.get(module_key, 0)) + 1

        if display_predicate(final_case):
            display_case_count += 1

    denominator = max(1, int(final_count or 0))
    high_priority_count = int(priority_breakdown.get("P0", 0)) + int(priority_breakdown.get("P1", 0))
    module_breakdown_top = {
        key: int(value)
        for key, value in sorted(
            module_breakdown.items(),
            key=lambda item: (-int(item[1]), item[0]),
        )[:20]
    }

    return {
        "final_priority_breakdown": {
            key: int(value)
            for key, value in priority_breakdown.items()
            if int(value) > 0
        },
        "final_execution_group_breakdown": dict(execution_group_breakdown),
        "final_module_breakdown_top": module_breakdown_top,
        "final_display_case_count": int(display_case_count),
        "final_display_ratio": round(float(display_case_count) / float(denominator), 4),
        "final_high_priority_ratio": round(float(high_priority_count) / float(denominator), 4),
    }


def summarize_final_description_dedup_drops(
    review_decision_table: Iterable[Any] | None,
    *,
    description_key_fn: DescriptionKeyFn = final_description_dedup_key,
    sample_limit: int = 20,
) -> dict[str, Any]:
    rows = _dict_case_items(review_decision_table or [])
    rows_with_description_keys = [(row, str(description_key_fn(row) or "")) for row in rows]
    retained_description_keys = {
        description_key
        for row, description_key in rows_with_description_keys
        if bool(row.get("retained_final")) and description_key
    }
    final_description_dedup_drop_signatures: set[str] = set()
    final_description_dedup_drop_samples: list[dict[str, str]] = []
    sampled_signatures: set[str] = set()
    sample_limit = max(0, int(sample_limit or 0))

    for row, description_key in rows_with_description_keys:
        if bool(row.get("retained_final")):
            continue
        if not description_key or description_key not in retained_description_keys:
            continue
        signature = str(row.get("signature") or "").strip()
        if not signature:
            continue
        final_description_dedup_drop_signatures.add(signature)
        if signature in sampled_signatures or len(final_description_dedup_drop_samples) >= sample_limit:
            continue
        sampled_signatures.add(signature)
        final_description_dedup_drop_samples.append(
            {
                "signature": signature,
                "description_key": description_key,
                "case_id": case_text_field(row, "id"),
                "test_module": case_text_field(row, "test_module"),
            }
        )

    return {
        "final_description_dedup_drop_signatures": set(final_description_dedup_drop_signatures),
        "final_description_dedup_drop_count": int(len(final_description_dedup_drop_signatures)),
        "final_description_dedup_drop_samples": final_description_dedup_drop_samples,
    }


def summarize_priority_decision_breakdown(
    review_decision_table: Iterable[Any] | None,
) -> dict[str, Any]:
    rows = _dict_case_items(review_decision_table or [])
    priority_decision_state_breakdown: dict[str, int] = {
        "decided": 0,
        "conflict": 0,
        "undetermined": 0,
        "optional": 0,
        "invalid": 0,
    }
    priority_final_breakdown: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "null": 0}
    legacy_priority_breakdown: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "UNKNOWN": 0}

    for row in rows:
        decision_state_key = str(row.get("priority_decision_state") or "").strip().lower()
        if decision_state_key not in priority_decision_state_breakdown:
            decision_state_key = "undetermined"
        priority_decision_state_breakdown[decision_state_key] = int(
            priority_decision_state_breakdown.get(decision_state_key, 0)
        ) + 1

        final_priority_key = str(row.get("priority_final") or "").strip().upper()
        if final_priority_key not in {"P0", "P1", "P2"}:
            final_priority_key = "null"
        priority_final_breakdown[final_priority_key] = (
            int(priority_final_breakdown.get(final_priority_key, 0)) + 1
        )

        legacy_priority_key = str(row.get("legacy_priority") or "").strip().upper()
        if legacy_priority_key not in {"P0", "P1", "P2"}:
            legacy_priority_key = "UNKNOWN"
        legacy_priority_breakdown[legacy_priority_key] = (
            int(legacy_priority_breakdown.get(legacy_priority_key, 0)) + 1
        )

    priority_conflict_count = int(priority_decision_state_breakdown.get("conflict", 0))
    priority_undetermined_count = int(priority_decision_state_breakdown.get("undetermined", 0))
    priority_optional_count = int(priority_decision_state_breakdown.get("optional", 0))
    priority_invalid_count = int(priority_decision_state_breakdown.get("invalid", 0))

    return {
        "priority_decision_state_breakdown": dict(priority_decision_state_breakdown),
        "priority_final_breakdown": dict(priority_final_breakdown),
        "legacy_priority_breakdown": dict(legacy_priority_breakdown),
        "priority_conflict_count": int(priority_conflict_count),
        "priority_undetermined_count": int(priority_undetermined_count),
        "priority_optional_count": int(priority_optional_count),
        "priority_invalid_count": int(priority_invalid_count),
        "priority_quality_gate_failed": bool(priority_invalid_count > 0),
        "needs_priority_review": bool(
            priority_conflict_count > 0
            or priority_undetermined_count > 0
            or priority_invalid_count > 0
        ),
    }


def summarize_final_description_dedup_and_priority_breakdown(
    review_decision_table: Iterable[Any] | None,
    *,
    description_key_fn: DescriptionKeyFn = final_description_dedup_key,
    sample_limit: int = 20,
) -> dict[str, Any]:
    rows = _dict_case_items(review_decision_table or [])
    return {
        **summarize_final_description_dedup_drops(
            rows,
            description_key_fn=description_key_fn,
            sample_limit=sample_limit,
        ),
        **summarize_priority_decision_breakdown(rows),
    }


def final_dedup_priority_summary_fields(summary: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(summary or {})
    priority_invalid_count = int(payload.get("priority_invalid_count") or 0)
    return {
        "priority_decision_state_breakdown": dict(payload.get("priority_decision_state_breakdown") or {}),
        "priority_final_breakdown": dict(payload.get("priority_final_breakdown") or {}),
        "legacy_priority_breakdown": dict(payload.get("legacy_priority_breakdown") or {}),
        "priority_conflict_count": int(payload.get("priority_conflict_count") or 0),
        "priority_undetermined_count": int(payload.get("priority_undetermined_count") or 0),
        "priority_optional_count": int(payload.get("priority_optional_count") or 0),
        "priority_invalid_count": int(priority_invalid_count),
        "priority_quality_gate_failed": bool(priority_invalid_count > 0),
        "needs_priority_review": bool(payload.get("needs_priority_review")),
    }


def build_review_flow_structure_fields(
    review_case_structure: dict[str, Any] | None,
) -> dict[str, Any]:
    review_structure = dict(review_case_structure or {})
    flow_outline = dict(review_structure.get("flow_outline") or {})
    return {
        "flow_order": [str(item) for item in (flow_outline.get("flow_order") or []) if str(item)],
        "flow_labels": dict(flow_outline.get("flow_labels") or {}),
        "flow_stage_breakdown": dict(review_structure.get("stage_breakdown") or {}),
        "flow_missing_stages": [
            str(item)
            for item in (review_structure.get("missing_flow_stages") or [])
            if str(item)
        ],
        "flow_missing_stage_count": int(review_structure.get("missing_flow_stage_count") or 0),
        "flow_misordered_count": int(review_structure.get("misordered_count") or 0),
        "scenario_duplicate_cluster_count": int(review_structure.get("duplicate_cluster_count") or 0),
        "scenario_duplicate_case_count": int(review_structure.get("duplicate_case_count") or 0),
        "scenario_duplicate_clusters": _dict_samples(review_structure.get("duplicate_clusters"), limit=20),
    }


def build_final_flow_structure_fields(
    *,
    final_independent_case_structure: dict[str, Any] | None,
    final_duplicate_excess: dict[str, Any] | None,
    final_case_structure: dict[str, Any] | None,
    final_order_flow_governance_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    final_independent_structure = dict(final_independent_case_structure or {})
    duplicate_excess = dict(final_duplicate_excess or {})
    final_structure = dict(final_case_structure or {})
    final_order_governance = dict(final_order_flow_governance_summary or {})
    final_execution_plan = dict(final_order_governance.get("execution_orchestration_plan") or {})
    final_execution_group_order = list(
        final_order_governance.get("execution_group_order")
        or final_execution_plan.get("execution_group_order")
        or []
    )
    return {
        "final_flow_stage_breakdown": dict(final_independent_structure.get("stage_breakdown") or {}),
        "final_flow_missing_stages": [
            str(item)
            for item in (final_independent_structure.get("missing_flow_stages") or [])
            if str(item)
        ],
        "final_flow_missing_stage_count": int(final_independent_structure.get("missing_flow_stage_count") or 0),
        "final_flow_misordered_count": int(final_independent_structure.get("misordered_count") or 0),
        "final_scenario_duplicate_cluster_count": int(duplicate_excess.get("duplicate_excess_cluster_count") or 0),
        "final_scenario_duplicate_case_count": int(duplicate_excess.get("duplicate_excess_case_count") or 0),
        "final_scenario_duplicate_clusters": _dict_samples(duplicate_excess.get("duplicate_excess_clusters"), limit=20),
        "final_scenario_duplicate_raw_cluster_count": int(final_structure.get("duplicate_cluster_count") or 0),
        "final_scenario_duplicate_raw_case_count": int(final_structure.get("duplicate_case_count") or 0),
        "final_order_flow_governance": final_order_governance,
        "final_execution_group_order": final_execution_group_order,
        "final_execution_orchestration_plan": final_execution_plan,
    }


def build_flow_profile_governance_fields(
    *,
    fact_profile: dict[str, Any] | None,
    project_profile: dict[str, Any] | None,
    flow_governance_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    fact = dict(fact_profile or {})
    project = dict(project_profile or {})
    flow_governance = dict(flow_governance_summary or {})
    return {
        "fact_profile_source": str(fact.get("profile_source") or ""),
        "fact_profile_confidence": float(fact.get("confidence") or 0.0),
        "fact_profile_confirmed_count": int(len(fact.get("confirmed_facts") or [])),
        "fact_profile_forbidden_count": int(len(fact.get("forbidden_facts") or [])),
        "fact_profile_pending_count": int(len(fact.get("pending_items") or [])),
        "project_profile_source": str(project.get("profile_source") or ""),
        "project_profile_confidence": float(project.get("confidence") or 0.0),
        "flow_governance_applied": bool(flow_governance.get("applied")),
        "flow_reordered": bool(flow_governance.get("flow_reordered")),
        "flow_governance_reason": str(flow_governance.get("reason") or ""),
        "scenario_duplicate_pruned_count": int(flow_governance.get("scenario_duplicate_pruned_count") or 0),
        "scenario_duplicate_pruned_indices": list(flow_governance.get("scenario_duplicate_pruned_indices") or [])[:100],
    }


def build_execution_plan_flow_summary_fields(
    execution_plan_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    execution_plan = dict(execution_plan_summary or {})
    return {
        "execution_plan": dict(execution_plan),
        "linear_executable": bool(execution_plan.get("linear_executable")),
        "linear_scope": str(execution_plan.get("linear_scope") or ""),
        "main_chain_case_count": int(execution_plan.get("main_chain_case_count") or 0),
        "independent_case_count": int(execution_plan.get("independent_case_count") or 0),
        "isolation_case_count": int(execution_plan.get("isolation_case_count") or 0),
        "broken_dependency_count": int(execution_plan.get("broken_dependency_count") or 0),
        "state_conflict_count": int(execution_plan.get("state_conflict_count") or 0),
        "role_switch_count": int(execution_plan.get("role_switch_count") or 0),
    }


def review_flow_structure_summary_fields(
    *,
    review_case_structure: dict[str, Any] | None,
    final_independent_case_structure: dict[str, Any] | None,
    final_duplicate_excess: dict[str, Any] | None,
    final_case_structure: dict[str, Any] | None,
    final_order_flow_governance_summary: dict[str, Any] | None,
    fact_profile: dict[str, Any] | None,
    project_profile: dict[str, Any] | None,
    flow_governance_summary: dict[str, Any] | None,
    execution_plan_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        **build_review_flow_structure_fields(review_case_structure),
        **build_final_flow_structure_fields(
            final_independent_case_structure=final_independent_case_structure,
            final_duplicate_excess=final_duplicate_excess,
            final_case_structure=final_case_structure,
            final_order_flow_governance_summary=final_order_flow_governance_summary,
        ),
        **build_flow_profile_governance_fields(
            fact_profile=fact_profile,
            project_profile=project_profile,
            flow_governance_summary=flow_governance_summary,
        ),
        **build_execution_plan_flow_summary_fields(execution_plan_summary),
    }
