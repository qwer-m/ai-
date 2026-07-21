from __future__ import annotations

from typing import Any, Callable


AnalyzeCaseStructureFn = Callable[..., dict[str, Any]]
CaseValueScoreFn = Callable[[dict[str, Any], int], tuple[Any, ...]]
CaseTextFieldFn = Callable[[dict[str, Any], str], str]
ScenarioRegistryMetaFn = Callable[[], dict[str, Any]]
DiagnoseRegistryImpactFn = Callable[..., dict[str, Any]]


def _scenario_policy(project_profile: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(project_profile, dict) and isinstance(project_profile.get("scenario_cluster_policy"), dict):
        return dict(project_profile.get("scenario_cluster_policy") or {})
    return {}


def _scenario_kind_from_key(
    scenario_key: str,
    *,
    default_scenario_caps: dict[str, int],
) -> str:
    value = str(scenario_key or "")
    parts = [part for part in value.split(":") if part]
    known_kinds = set(default_scenario_caps) | {"intent", "semantic", "toast", "list", "navigate"}
    for part in parts:
        if part in known_kinds:
            return part
    return parts[-1] if parts else value


def _scenario_max_keep(
    scenario_key: str,
    *,
    default_max: int,
    project_profile: dict[str, Any] | None,
    default_scenario_caps: dict[str, int],
    scenario_caps_by_mode: dict[str, dict[str, int]],
) -> int:
    kind = _scenario_kind_from_key(
        scenario_key,
        default_scenario_caps=default_scenario_caps,
    )
    policy = _scenario_policy(project_profile)
    if bool(policy.get("disable_scenario_pruning")):
        return 1_000_000
    caps = policy.get("scenario_caps") if isinstance(policy.get("scenario_caps"), dict) else {}
    try:
        if kind in caps:
            return max(1, int(caps.get(kind) or 1))
    except Exception:
        pass
    mode = str(policy.get("coverage_mode") or policy.get("generation_coverage_mode") or "").strip()
    mode_caps = scenario_caps_by_mode.get(mode) or {}
    if kind in mode_caps:
        return max(1, int(mode_caps.get(kind) or 1))
    if kind in default_scenario_caps:
        return max(1, int(default_scenario_caps[kind]))
    if mode_caps:
        return max(1, int(mode_caps.get("default") or default_max or 2))
    return max(1, int(default_max or 2))


def summarize_duplicate_excess_by_policy(
    structure: dict[str, Any],
    *,
    project_profile: dict[str, Any] | None = None,
    default_max: int = 2,
    default_scenario_caps: dict[str, int],
    scenario_caps_by_mode: dict[str, dict[str, int]],
) -> dict[str, Any]:
    clusters = [
        dict(item)
        for item in (structure.get("duplicate_clusters") or [])
        if isinstance(item, dict)
    ] if isinstance(structure, dict) else []
    scenario_policy = _scenario_policy(project_profile)
    disable_category_pruning = bool(scenario_policy.get("disable_scenario_pruning"))
    intent_duplicate_cap = max(1, int(scenario_policy.get("intent_duplicate_cap") or 1))
    excess_clusters: list[dict[str, Any]] = []
    excess_case_count = 0

    for cluster in clusters:
        scenario_key = str(cluster.get("scenario_key") or "")
        group_type = str(cluster.get("group_type") or "scenario")
        size = max(0, int(cluster.get("size") or 0))
        if size <= 1:
            continue
        if disable_category_pruning:
            continue
        cap = intent_duplicate_cap if group_type == "intent" else _scenario_max_keep(
            scenario_key,
            default_max=default_max,
            project_profile=project_profile,
            default_scenario_caps=default_scenario_caps,
            scenario_caps_by_mode=scenario_caps_by_mode,
        )
        excess = max(0, size - int(cap or 1))
        if excess <= 0:
            continue
        item = dict(cluster)
        item["allowed_cap"] = int(cap or 1)
        item["excess_case_count"] = int(excess)
        excess_clusters.append(item)
        excess_case_count += int(excess)

    return {
        "duplicate_excess_cluster_count": int(len(excess_clusters)),
        "duplicate_excess_case_count": int(excess_case_count),
        "duplicate_excess_clusters": excess_clusters,
        "raw_duplicate_cluster_count": int(structure.get("duplicate_cluster_count") or len(clusters)) if isinstance(structure, dict) else 0,
        "raw_duplicate_case_count": int(structure.get("duplicate_case_count") or 0) if isinstance(structure, dict) else 0,
    }


def _renumber_cases(cases: list[dict[str, Any]], start_id: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    current = int(start_id or 1)
    for case in cases:
        if not isinstance(case, dict):
            continue
        item = dict(case)
        item["id"] = f"TC-{current:03d}"
        current += 1
        output.append(item)
    return output


def govern_cases_by_flow_structure(
    requirement_context: str,
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    renumber_ids: bool = True,
    max_per_scenario: int = 2,
    project_profile: dict[str, Any] | None = None,
    analyze_case_structure_fn: AnalyzeCaseStructureFn,
    case_value_score_fn: CaseValueScoreFn,
    case_text_field_fn: CaseTextFieldFn,
    scenario_registry_meta_fn: ScenarioRegistryMetaFn,
    diagnose_registry_impact_fn: DiagnoseRegistryImpactFn,
    default_scenario_caps: dict[str, int],
    scenario_caps_by_mode: dict[str, dict[str, int]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_cases = [item for item in (cases or []) if isinstance(item, dict)]
    if not normalized_cases:
        return [], {
            "applied": False,
            "reason": "empty_cases",
            "scenario_duplicate_pruned_count": 0,
            "flow_reordered": False,
        }

    structure = analyze_case_structure_fn(requirement_context, normalized_cases, project_profile=project_profile)
    flow_outline = dict(structure.get("flow_outline") or {})
    flow_order = [str(item) for item in (flow_outline.get("flow_order") or []) if str(item)]
    cross_order = [str(item) for item in (flow_outline.get("cross_cutting") or []) if str(item)]
    rows = [dict(item) for item in (structure.get("rows") or []) if isinstance(item, dict)]
    row_by_index = {int(row.get("candidate_index") or 0): row for row in rows}
    drop_indices: set[int] = set()
    cap_policy_used: dict[str, int] = {}
    scenario_policy = _scenario_policy(project_profile)
    disable_category_pruning = bool(scenario_policy.get("disable_scenario_pruning"))
    intent_duplicate_cap = max(1, int(scenario_policy.get("intent_duplicate_cap") or 1))
    duplicate_clusters = [
        dict(item)
        for item in (structure.get("duplicate_clusters") or [])
        if isinstance(item, dict)
    ]
    for cluster in duplicate_clusters:
        scenario_key = str(cluster.get("scenario_key") or "")
        if ":semantic:" in scenario_key:
            continue
        group_type = str(cluster.get("group_type") or "scenario")
        indices = [int(item) for item in (cluster.get("candidate_indices") or []) if int(item or 0) > 0]
        if disable_category_pruning:
            continue
        max_keep = intent_duplicate_cap if group_type == "intent" else _scenario_max_keep(
            scenario_key,
            default_max=max_per_scenario,
            project_profile=project_profile,
            default_scenario_caps=default_scenario_caps,
            scenario_caps_by_mode=scenario_caps_by_mode,
        )
        cap_policy_used[
            _scenario_kind_from_key(
                scenario_key,
                default_scenario_caps=default_scenario_caps,
            )
        ] = int(max_keep)
        if len(indices) <= max_keep:
            continue
        ranked = sorted(
            indices,
            key=lambda idx: case_value_score_fn(normalized_cases[idx - 1], idx),
            reverse=True,
        )
        drop_indices.update(ranked[max_keep:])

    kept_pairs = [
        (index, case)
        for index, case in enumerate(normalized_cases, start=1)
        if index not in drop_indices
    ]
    flow_rank = {stage: idx for idx, stage in enumerate(flow_order)}
    cross_rank = {stage: idx for idx, stage in enumerate(cross_order)}
    stage_base = len(flow_rank)

    def _sort_key(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int, str]:
        index, case = pair
        row = row_by_index.get(index) or {}
        stage = str(row.get("flow_stage") or "unknown")
        crosses = [str(item) for item in (row.get("cross_cutting") or []) if str(item)]
        module = case_text_field_fn(case, "test_module")
        primary_cross = ""
        for cross in crosses:
            label = str((flow_outline.get("cross_cutting_labels") or {}).get(cross) or "")
            if label and label in module:
                primary_cross = cross
                break
        if primary_cross:
            group = stage_base + cross_rank.get(primary_cross, len(cross_rank))
        elif stage in flow_rank:
            group = flow_rank[stage]
        elif crosses:
            group = stage_base + min(cross_rank.get(cross, len(cross_rank)) for cross in crosses)
        else:
            group = stage_base + len(cross_rank) + 1
        return (int(group), int(row.get("flow_rank") or 9999), int(index), str(row.get("scenario_key") or ""))

    ordered_pairs = sorted(kept_pairs, key=_sort_key) if (flow_order or cross_order) else kept_pairs
    ordered_cases = [dict(case) for _index, case in ordered_pairs]
    if renumber_ids:
        ordered_cases = _renumber_cases(ordered_cases, start_id)

    original_order = [int(index) for index, _case in kept_pairs]
    new_order = [int(index) for index, _case in ordered_pairs]
    registry_impact = diagnose_registry_impact_fn(
        normalized_cases,
        scenario_keys=[
            str(row_by_index.get(i, {}).get("scenario_key") or "")
            for i in range(1, len(normalized_cases) + 1)
        ],
        primary_domain=str(structure.get("primary_domain") or ""),
        mode=str(project_profile.get("generation_coverage_mode") or "").strip() if project_profile else "",
    )
    registry_meta = dict(scenario_registry_meta_fn())
    registry_meta["scenario_policy_documents"] = list(registry_impact.get("matched_documents") or [])
    return ordered_cases, {
        "applied": bool(flow_order or cross_order or drop_indices),
        "reason": "" if (flow_order or cross_order or drop_indices) else "no_flow_outline",
        **registry_meta,
        "flow_reordered": bool(original_order != new_order),
        "flow_order": flow_order,
        "cross_cutting_order": cross_order,
        "scenario_duplicate_pruned_count": int(len(drop_indices)),
        "scenario_duplicate_pruned_indices": sorted(drop_indices)[:100],
        "scenario_cap_policy": cap_policy_used,
        "scenario_duplicate_cluster_count": int(structure.get("duplicate_cluster_count") or 0),
        "flow_misordered_count_before": int(structure.get("misordered_count") or 0),
        "missing_flow_stage_count": int(structure.get("missing_flow_stage_count") or 0),
        "registry_impact": registry_impact,
    }
