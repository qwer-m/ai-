from __future__ import annotations

from typing import Any

from .scenario_registry import (
    default_scenario_caps,
    diagnose_registry_impact,
    infer_domain_tags,
    infer_primary_domain_tag,
    mode_scenario_caps,
    scenario_registry_meta,
)
from .coverage_case_classifier import (
    _flatten_case_text,
    classify_case_cross_cutting,
    classify_case_flow_stage,
    classify_case_intent_signature,
    classify_case_scenario_key,
)
from .coverage_case_complexity import case_complexity_profile
from .domain_gate import current_domain_gate
from .flow_outline import extract_flow_outline
from .flow_structure_governance import (
    govern_cases_by_flow_structure as _govern_cases_by_flow_structure_impl,
    summarize_duplicate_excess_by_policy as _summarize_duplicate_excess_by_policy_impl,
)
from .rule_coverage import analyze_requirement_rule_coverage
from ..postprocess.case_access import case_id, case_priority, case_steps, case_text_field, case_value


_DEFAULT_SCENARIO_CAPS: dict[str, int] = default_scenario_caps()

_SCENARIO_CAPS_BY_MODE: dict[str, dict[str, int]] = {
    "core_smoke": {
        "default": 1,
        "intent": 2,
        "toast": 2,
        "list": 2,
        "navigate": 2,
    },
    "standard_regression": {
        "default": 2,
        "intent": 2,
    },
    "expanded_regression": {
        "default": 3,
        "intent": 3,
    },
    "full_functional_regression": {
        "default": 5,
        "toast": 8,
        "list": 8,
        "navigate": 8,
        "intent": 5,
    },
}
for _mode_name, _mode_caps in mode_scenario_caps().items():
    _SCENARIO_CAPS_BY_MODE.setdefault(_mode_name, {}).update(_mode_caps)


def analyze_case_structure(
    requirement_context: str,
    cases: list[dict[str, Any]],
    *,
    project_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Annotate candidate cases with flow-stage, scenario-cluster and ordering diagnostics."""
    normalized_cases = [item for item in (cases or []) if isinstance(item, dict)]
    requirement_text = str(requirement_context or "").strip()
    requirement_domain_gate = current_domain_gate(requirement_text)
    domain_signal_text = requirement_text
    if not domain_signal_text:
        domain_signal_text = "\n".join(_flatten_case_text(item) for item in normalized_cases[:20])
    primary_domain = (
        str(requirement_domain_gate.get("primary_domain") or "")
        if bool(requirement_domain_gate.get("allows_historical_profile"))
        else ""
    )
    if not primary_domain and not requirement_text:
        primary_domain = infer_primary_domain_tag(domain_signal_text)
    if (
        not primary_domain
        and bool(requirement_domain_gate.get("allows_historical_profile"))
        and len(domain_signal_text) < 240
    ):
        flow_outline_hint = {}
        if isinstance(project_profile, dict):
            flow_outline_hint = dict(project_profile.get("flow_outline") or {})
        flow_labels_hint = dict(flow_outline_hint.get("flow_labels") or {})
        profile_hint_text = "\n".join(str(item) for item in flow_labels_hint.values() if str(item).strip())
        augmented_domain_signal = "\n".join(
            item
            for item in (
                domain_signal_text,
                profile_hint_text,
                "\n".join(_flatten_case_text(item) for item in normalized_cases[:8]),
            )
            if item
        )
        primary_domain = infer_primary_domain_tag(augmented_domain_signal)
    domain_tags = infer_domain_tags(domain_signal_text)
    flow_outline = extract_flow_outline(requirement_context, normalized_cases, project_profile=project_profile)
    flow_order = [str(item) for item in (flow_outline.get("flow_order") or []) if str(item)]
    flow_labels = dict(flow_outline.get("flow_labels") or {})
    flow_rank = {stage: index for index, stage in enumerate(flow_order)}

    rows: list[dict[str, Any]] = []
    scenario_groups: dict[str, list[int]] = {}
    intent_groups: dict[str, list[int]] = {}
    stage_breakdown: dict[str, int] = {}
    max_seen_rank = -1

    for index, case in enumerate(normalized_cases, start=1):
        stage = classify_case_flow_stage(case, flow_outline)
        cross_cutting = classify_case_cross_cutting(case, flow_outline)
        scenario_key = classify_case_scenario_key(
            case,
            stage,
            primary_domain=primary_domain,
            domain_tags=domain_tags,
        )
        intent_signature = classify_case_intent_signature(case, stage)
        duplicate_group_key = intent_signature if ":semantic:" in scenario_key and intent_signature else scenario_key
        complexity = case_complexity_profile(case)
        rank = flow_rank.get(stage)
        has_explicit_execution_sequence = case.get("execution_sequence") not in (None, "")
        misordered = bool(
            not has_explicit_execution_sequence
            and rank is not None
            and rank < max_seen_rank
        )
        if rank is not None and not has_explicit_execution_sequence:
            max_seen_rank = max(max_seen_rank, int(rank))
        stage_breakdown[stage] = int(stage_breakdown.get(stage, 0)) + 1
        scenario_groups.setdefault(scenario_key, []).append(index)
        intent_groups.setdefault(intent_signature, []).append(index)
        rows.append(
            {
                "candidate_index": int(index),
                "case_id": case_id(case),
                "flow_stage": stage,
                "flow_stage_label": str(flow_labels.get(stage) or stage),
                "flow_rank": int(rank) if rank is not None else None,
                "cross_cutting": cross_cutting,
                "scenario_key": scenario_key,
                "intent_signature": intent_signature,
                "duplicate_group_key": duplicate_group_key,
                **complexity,
                "misordered_against_requirement_flow": misordered,
            }
        )

    duplicate_clusters: list[dict[str, Any]] = []
    row_by_index = {int(row["candidate_index"]): row for row in rows}
    grouped_candidates: list[tuple[str, str, list[int]]] = [
        ("scenario", key, value) for key, value in scenario_groups.items()
    ]
    grouped_candidates.extend(("intent", key, value) for key, value in intent_groups.items())
    seen_cluster_sets: set[tuple[int, ...]] = set()
    for scenario_key, group_type, indices in [
        (key, kind, value)
        for kind, key, value in sorted(
            grouped_candidates,
            key=lambda item: (item[2][0], 0 if item[0] == "scenario" else 1, item[1]),
        )
    ]:
        if len(indices) <= 1:
            continue
        index_tuple = tuple(int(item) for item in indices)
        if index_tuple in seen_cluster_sets:
            continue
        seen_cluster_sets.add(index_tuple)
        cluster_id = f"SC-{len(duplicate_clusters) + 1:03d}"
        first_index = int(indices[0])
        first_row = row_by_index.get(first_index) or {}
        duplicate_of = str(first_row.get("case_id") or f"candidate:{first_index}")
        for idx in indices:
            row = row_by_index.get(int(idx))
            if not row:
                continue
            row["duplicate_cluster_id"] = cluster_id
            row["duplicate_cluster_size"] = int(len(indices))
            if int(idx) != first_index:
                row["duplicate_of_case_id"] = duplicate_of
                row["is_scenario_duplicate"] = True
            else:
                row["duplicate_of_case_id"] = ""
                row["is_scenario_duplicate"] = False
        duplicate_clusters.append(
            {
                "cluster_id": cluster_id,
                "scenario_key": scenario_key,
                "group_type": group_type,
                "size": int(len(indices)),
                "first_case_id": duplicate_of,
                "candidate_indices": [int(item) for item in indices],
            }
        )

    for row in rows:
        row.setdefault("duplicate_cluster_id", "")
        row.setdefault("duplicate_cluster_size", 0)
        row.setdefault("duplicate_of_case_id", "")
        row.setdefault("is_scenario_duplicate", False)

    covered_flow_stages = {str(row.get("flow_stage") or "") for row in rows}
    missing_flow_stages = [stage for stage in flow_order if stage not in covered_flow_stages]
    return {
        "flow_outline": flow_outline,
        "primary_domain": primary_domain,
        "domain_tags": sorted(domain_tags),
        "domain_gate_status": str(requirement_domain_gate.get("status") or ""),
        "domain_gate_reason": str(requirement_domain_gate.get("reason") or ""),
        "domain_gate_allows_historical_profile": bool(
            requirement_domain_gate.get("allows_historical_profile")
        ),
        "rows": rows,
        "stage_breakdown": stage_breakdown,
        "missing_flow_stages": missing_flow_stages,
        "missing_flow_stage_count": int(len(missing_flow_stages)),
        "misordered_count": int(sum(1 for row in rows if bool(row.get("misordered_against_requirement_flow")))),
        "duplicate_clusters": duplicate_clusters[:50],
        "duplicate_cluster_count": int(len(duplicate_clusters)),
        "duplicate_case_count": int(sum(max(0, int(cluster.get("size") or 0) - 1) for cluster in duplicate_clusters)),
    }


def _priority_score(value: Any) -> int:
    priority = str(value or "").strip().upper()
    if priority == "P0":
        return 3
    if priority == "P1":
        return 2
    if priority == "P2":
        return 1
    return 0


def _case_value_score(case: dict[str, Any], original_index: int) -> tuple[int, int, int, int, int, int, int]:
    step_count = len(case_steps(case))
    text_len = len(case_text_field(case, "description")) + len(case_text_field(case, "expected_result"))
    preconditions = case_value(case, "preconditions", [])
    precondition_count = len([item for item in preconditions if str(item).strip()]) if isinstance(preconditions, list) else 0
    complexity_score = int(case_complexity_profile(case).get("complexity_score") or 0)
    return (
        _priority_score(case_priority(case, prefer_final=True)),
        -_legacy_compatibility_penalty(case),
        -min(complexity_score, 8),
        min(step_count, 8),
        min(precondition_count, 6),
        min(text_len, 400),
        -int(original_index),
    )


def _legacy_compatibility_penalty(case: dict[str, Any]) -> int:
    text = _flatten_case_text(case)
    legacy_hits = (
        "旧版本",
        "旧版",
        "兼容模式",
        "legacy",
        "compatibility mode",
    )
    return sum(1 for token in legacy_hits if token and token in text)


def summarize_duplicate_excess_by_policy(
    structure: dict[str, Any],
    *,
    project_profile: dict[str, Any] | None = None,
    default_max: int = 2,
) -> dict[str, Any]:
    return _summarize_duplicate_excess_by_policy_impl(
        structure,
        project_profile=project_profile,
        default_max=default_max,
        default_scenario_caps=_DEFAULT_SCENARIO_CAPS,
        scenario_caps_by_mode=_SCENARIO_CAPS_BY_MODE,
    )


def govern_cases_by_flow_structure(
    requirement_context: str,
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    renumber_ids: bool = True,
    max_per_scenario: int = 2,
    project_profile: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _govern_cases_by_flow_structure_impl(
        requirement_context,
        cases,
        start_id=start_id,
        renumber_ids=renumber_ids,
        max_per_scenario=max_per_scenario,
        project_profile=project_profile,
        analyze_case_structure_fn=analyze_case_structure,
        case_value_score_fn=_case_value_score,
        case_text_field_fn=case_text_field,
        scenario_registry_meta_fn=scenario_registry_meta,
        diagnose_registry_impact_fn=diagnose_registry_impact,
        default_scenario_caps=_DEFAULT_SCENARIO_CAPS,
        scenario_caps_by_mode=_SCENARIO_CAPS_BY_MODE,
    )

def analyze_coverage(requirement_context: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """中文注释：规则级覆盖诊断（可直接驱动 gap 阶段精准补漏）。"""
    normalized_cases = [item for item in (cases or []) if isinstance(item, dict)]
    case_texts = [_flatten_case_text(case) for case in normalized_cases]
    return analyze_requirement_rule_coverage(requirement_context, case_texts)
