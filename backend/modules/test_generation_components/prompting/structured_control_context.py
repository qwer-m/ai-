from __future__ import annotations

import json
from typing import Any

from ..control.feedback_control_state import FeedbackControlState
from ..control.semantic_contract import (
    MAX_WORKFLOW_STEPS,
    REQUIREMENT_SEMANTIC_CONTRACT_VERSION,
)
from ..control.requirement_semantic_graph import SEMANTIC_GRAPH_VERSION
from ..postprocess.streaming_execution_plan_ordering import execution_side_suite_order_labels


def _workflow_step_execution_label(step: dict[str, Any], *, index: int) -> str:
    step_id = str(step.get("id") or f"step_{index:03d}").strip()
    stage_kind = str(step.get("stage_kind") or "").strip()
    label = str(
        step.get("label")
        or step.get("action")
        or step.get("description")
        or step_id
    ).strip()
    state_in = str(step.get("state_in") or step.get("source_state") or "").strip()
    state_out = str(step.get("state_out") or step.get("target_state") or "").strip()
    state_transition = f"{state_in}->{state_out}" if state_in and state_out else ""
    module_candidates = [
        str(item.get("module_name") or item.get("module_key") or "").strip()
        for item in (step.get("module_candidates") or [])
        if isinstance(item, dict) and str(item.get("module_name") or item.get("module_key") or "").strip()
    ]
    required_states = [
        f"{str(item.get('entity') or '')}.{str(item.get('state') or '')}[{str(item.get('source') or 'unknown')}]"
        for item in (step.get("required_states") or [])
        if isinstance(item, dict) and str(item.get("entity") or "").strip() and str(item.get("state") or "").strip()
    ]
    produced_states = [
        f"{str(item.get('entity') or '')}.{str(item.get('state') or '')}"
        for item in (step.get("produced_states") or [])
        if isinstance(item, dict) and str(item.get("entity") or "").strip() and str(item.get("state") or "").strip()
    ]
    semantic_parts = [
        f"modules={','.join(module_candidates)}" if module_candidates else "",
        f"requires={','.join(required_states)}" if required_states else "",
        f"produces={','.join(produced_states)}" if produced_states else "",
    ]
    parts = [
        part
        for part in (
            f"stage_id={step_id}",
            stage_kind,
            label,
            state_transition,
            *semantic_parts,
        )
        if part
    ]
    return " / ".join(parts)


def _typed_state_catalog_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    catalog = {
        key: item.get(key)
        for key in (
            "entity",
            "state",
            "source",
            "scope",
            "polarity",
            "temporal",
        )
        if str(item.get(key) or "").strip()
    }
    fact_ids = [
        str(fact_id).strip()
        for fact_id in (item.get("fact_ids") or [])
        if str(fact_id).strip()
    ]
    if fact_ids:
        catalog["fact_ids"] = fact_ids
    return catalog


def _build_active_workflow_semantic_catalog(
    workflow_blueprints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """向模型交付可直接复制的真实 ID 和状态契约，避免用工作流名称猜 ID。"""
    catalog: list[dict[str, Any]] = []
    for blueprint in workflow_blueprints:
        if not isinstance(blueprint, dict):
            continue
        workflow_id = str(
            blueprint.get("workflow_id") or blueprint.get("id") or ""
        ).strip()
        if not workflow_id:
            continue
        steps: list[dict[str, Any]] = []
        for step in (blueprint.get("steps") or [])[:MAX_WORKFLOW_STEPS]:
            if not isinstance(step, dict):
                continue
            stage_id = str(step.get("id") or "").strip()
            stage_kind = str(step.get("stage_kind") or "").strip()
            if not stage_id or not stage_kind:
                continue
            steps.append(
                {
                    "stage_id": stage_id,
                    "stage_kind": stage_kind,
                    "label": str(step.get("label") or step.get("action") or "").strip(),
                    "state_in": str(step.get("state_in") or "").strip(),
                    "state_out": str(step.get("state_out") or "").strip(),
                    "required": bool(step.get("required")),
                    "terminal": bool(step.get("terminal")),
                    "graph_node_id": str(step.get("graph_node_id") or "").strip(),
                    "fact_ids": [
                        str(item).strip()
                        for item in (step.get("fact_ids") or [])
                        if str(item).strip()
                    ],
                    "scope_candidates": [
                        {
                            "scope_id": str(item.get("scope_id") or "").strip(),
                            "role": str(item.get("role") or "").strip(),
                            "fact_ids": [
                                str(fact_id).strip()
                                for fact_id in (item.get("fact_ids") or [])
                                if str(fact_id).strip()
                            ],
                        }
                        for item in (step.get("scope_candidates") or [])
                        if isinstance(item, dict)
                        and str(item.get("scope_id") or "").strip()
                    ],
                    "graph_relation_ids": [
                        str(item).strip()
                        for item in (
                            step.get("graph_relation_ids")
                            or step.get("relation_ids")
                            or []
                        )
                        if str(item).strip()
                    ],
                    "module_candidates": [
                        {
                            "module_key": str(item.get("module_key") or "").strip(),
                            "module_name": str(item.get("module_name") or "").strip(),
                            "role": str(item.get("role") or "").strip(),
                        }
                        for item in (step.get("module_candidates") or [])
                        if isinstance(item, dict)
                        and str(item.get("module_key") or "").strip()
                    ],
                    "interaction_ids": [
                        str(item).strip()
                        for item in (step.get("interaction_ids") or [])
                        if str(item).strip()
                    ],
                    "required_states": [
                        state
                        for state in (
                            _typed_state_catalog_item(item)
                            for item in (step.get("required_states") or [])
                        )
                        if state
                    ],
                    "produced_states": [
                        state
                        for state in (
                            _typed_state_catalog_item(item)
                            for item in (step.get("produced_states") or [])
                        )
                        if state
                    ],
                }
            )
        if not steps:
            continue
        catalog.append(
            {
                "workflow_id": workflow_id,
                "workflow_name": str(blueprint.get("name") or "").strip(),
                "required_stage_ids": [
                    str(item).strip()
                    for item in (blueprint.get("required_stage_ids") or [])
                    if str(item).strip()
                ],
                "terminal_states": [
                    str(item).strip()
                    for item in (blueprint.get("terminal_states") or [])
                    if str(item).strip()
                ],
                "steps": steps,
            }
        )
    return catalog


def _build_active_semantic_graph_catalog(
    semantic_contract: dict[str, Any],
) -> dict[str, Any]:
    """交付完整的已发布语义图，使主链和独立功能共用同一事实源。"""

    validation = dict(semantic_contract.get("semantic_graph_validation") or {})
    graph = dict(semantic_contract.get("semantic_graph") or {})
    if (
        semantic_contract.get("semantic_contract_version")
        != REQUIREMENT_SEMANTIC_CONTRACT_VERSION
        or graph.get("graph_version") != SEMANTIC_GRAPH_VERSION
        or validation.get("publishable") is not True
    ):
        return {}
    raw_facts = [
        dict(item)
        for item in (semantic_contract.get("evidence_facts") or [])
        if isinstance(item, dict) and str(item.get("fact_id") or "").strip()
    ]
    raw_nodes = [
        dict(item)
        for item in (graph.get("nodes") or [])
        if isinstance(item, dict) and str(item.get("node_id") or "").strip()
    ]
    raw_edges = [
        dict(item)
        for item in (graph.get("edges") or [])
        if isinstance(item, dict) and str(item.get("edge_id") or "").strip()
    ]
    fact_ids = {str(item.get("fact_id") or "").strip() for item in raw_facts}
    node_ids = {str(item.get("node_id") or "").strip() for item in raw_nodes}
    edge_ids = {str(item.get("edge_id") or "").strip() for item in raw_edges}
    primary_flow_value = graph.get("primary_flow")
    primary_flow = (
        {
            "node_ids": [
                str(item).strip()
                for item in (primary_flow_value.get("node_ids") or [])
                if str(item).strip()
            ],
            "edge_ids": [
                str(item).strip()
                for item in (primary_flow_value.get("edge_ids") or [])
                if str(item).strip()
            ],
        }
        if isinstance(primary_flow_value, dict)
        else {"node_ids": [], "edge_ids": []}
    )
    edges_by_id = {
        str(item.get("edge_id") or "").strip(): item for item in raw_edges
    }
    primary_node_ids = primary_flow["node_ids"]
    primary_edge_ids = primary_flow["edge_ids"]
    primary_flow_valid = bool(
        (not primary_node_ids and not primary_edge_ids)
        or (
            len(primary_node_ids) >= 2
            and len(primary_edge_ids) == len(primary_node_ids) - 1
            and len(set(primary_node_ids)) == len(primary_node_ids)
            and len(set(primary_edge_ids)) == len(primary_edge_ids)
            and all(
                edge_id in edges_by_id
                and str(edges_by_id[edge_id].get("type") or "").strip()
                in {"triggers", "transitions"}
                and str(
                    edges_by_id[edge_id].get("source_node_id") or ""
                ).strip()
                == primary_node_ids[index]
                and str(
                    edges_by_id[edge_id].get("target_node_id") or ""
                ).strip()
                == primary_node_ids[index + 1]
                for index, edge_id in enumerate(primary_edge_ids)
            )
        )
    )
    if (
        len(fact_ids) != len(raw_facts)
        or len(node_ids) != len(raw_nodes)
        or len(edge_ids) != len(raw_edges)
        or any(
            not set(str(value).strip() for value in (node.get("fact_ids") or []))
            <= fact_ids
            for node in raw_nodes
        )
        or any(
            str(edge.get("source_node_id") or "").strip() not in node_ids
            or str(edge.get("target_node_id") or "").strip() not in node_ids
            or not set(
                str(value).strip() for value in (edge.get("fact_ids") or [])
            )
            <= fact_ids
            for edge in raw_edges
        )
        or not set(primary_flow["node_ids"]) <= node_ids
        or not set(primary_flow["edge_ids"]) <= edge_ids
        or not primary_flow_valid
    ):
        return {}
    # publishable 由语义图契约先行校验数量上限与引用完整性；
    # 提示层不得再根据 workflow 引用或固定数量静默裁剪。
    facts = [
        {
            key: fact.get(key)
            for key in (
                "fact_id",
                "statement",
                "requirement_level",
                "priority",
                "testability",
            )
        }
        for fact in sorted(raw_facts, key=lambda item: str(item.get("fact_id")))
    ]
    nodes = [
        {
            key: node.get(key)
            for key in (
                "node_id",
                "kind",
                "name",
                "scope_status",
                "boundary_status",
                "workflow_role",
                "fact_ids",
            )
        }
        for node in sorted(raw_nodes, key=lambda item: str(item.get("node_id")))
    ]
    edges = [
        {
            key: edge.get(key)
            for key in (
                "edge_id",
                "type",
                "source_node_id",
                "target_node_id",
                "source_scope_id",
                "target_scope_id",
                "trigger",
                "result_state",
                "transferred_entity_node_ids",
                "fact_ids",
            )
        }
        for edge in sorted(raw_edges, key=lambda item: str(item.get("edge_id")))
    ]
    if not (facts or nodes or edges):
        return {}
    return {
        "graph_version": str(graph.get("graph_version") or "").strip(),
        "facts": facts,
        "nodes": nodes,
        "edges": edges,
        "primary_flow": primary_flow,
    }


def _build_generation_execution_plan_from_blueprints(
    workflow_blueprints: list[dict[str, Any]],
) -> dict[str, Any]:
    independent_suite_order = execution_side_suite_order_labels()
    plan_lines: list[str] = [
        "### GENERATION EXECUTION PLAN",
        "* Generate main-chain cases first, in the exact workflow blueprint step order.",
        "* Do not interleave independent suites into the main chain unless a case advances the confirmed workflow state.",
    ]
    blueprint_count = 0
    step_count = 0
    for blueprint in workflow_blueprints:
        if not isinstance(blueprint, dict):
            continue
        steps = [step for step in (blueprint.get("steps") or []) if isinstance(step, dict)]
        step_labels = [
            _workflow_step_execution_label(step, index=index)
            for index, step in enumerate(steps[:MAX_WORKFLOW_STEPS], start=1)
        ]
        step_labels = [label for label in step_labels if label.strip()]
        if not step_labels:
            continue
        blueprint_count += 1
        step_count += len(step_labels)
        workflow_id = str(
            blueprint.get("workflow_id") or blueprint.get("id") or "workflow"
        ).strip()
        name = str(blueprint.get("name") or workflow_id).strip()
        plan_lines.append(f"* workflow_id={workflow_id}; name={name}:")
        plan_lines.extend(
            f"  {index}. {label}"
            for index, label in enumerate(step_labels, start=1)
        )

    if not blueprint_count:
        return {
            "lines": [],
            "blueprint_count": 0,
            "step_count": 0,
            "independent_suite_order": list(independent_suite_order),
        }

    plan_lines.append(
        "* Then generate independent suites in order: "
        + " -> ".join(independent_suite_order)
        + "."
    )
    return {
        "lines": plan_lines,
        "blueprint_count": int(blueprint_count),
        "step_count": int(step_count),
        "independent_suite_order": list(independent_suite_order),
    }


def _build_control_context(
    *,
    control_state: FeedbackControlState | dict[str, Any] | None,
    include_soft_constraints_in_text: bool = False,
    include_quality_fix_hints_in_text: bool = False,
) -> tuple[str, dict[str, Any]]:
    state = FeedbackControlState.from_any(control_state)
    generation_profile = dict((state.source_meta or {}).get("generation_coverage_profile") or {})
    fact_profile = dict((state.source_meta or {}).get("fact_profile") or {})
    project_profile = dict((state.source_meta or {}).get("project_profile") or {})
    requirement_semantic_contract = dict(
        (state.source_meta or {}).get("requirement_semantic_contract") or {}
    )
    manual_quality_profile = dict((state.source_meta or {}).get("manual_quality_profile") or {})
    project_flow_outline = dict(project_profile.get("flow_outline") or {})
    functional_architecture = dict(project_profile.get("functional_architecture") or {})
    functional_modules = [
        item for item in (functional_architecture.get("functional_modules") or []) if isinstance(item, dict)
    ]
    generation_execution_plan = _build_generation_execution_plan_from_blueprints(state.workflow_blueprints)
    active_workflow_semantic_catalog = _build_active_workflow_semantic_catalog(
        state.workflow_blueprints
    )
    active_semantic_graph_catalog = _build_active_semantic_graph_catalog(
        requirement_semantic_contract,
    )
    generation_coverage_mode = str(generation_profile.get("coverage_mode") or "").strip()
    summary = {
        "control_state_applied": bool(state.has_signals()),
        "must_cover_rules_count": int(len(state.must_cover_rules)),
        "must_have_scenarios_count": int(len(state.must_have_scenarios)),
        "forbidden_patterns_count": int(len(state.forbidden_patterns)),
        "preferred_patterns_count": int(len(state.preferred_patterns)),
        "reuse_risks_count": int(len(state.reuse_risks)),
        "soft_constraints_count": int(len(state.soft_constraints)),
        "rule_quota_keys": sorted(list((state.rule_quota or {}).keys())),
        "quality_fix_hints_count": int(len(state.quality_fix_hints)),
        "workflow_blueprint_count": int(len(state.workflow_blueprints)),
        "generation_execution_plan_blueprint_count": int(generation_execution_plan.get("blueprint_count") or 0),
        "generation_execution_plan_step_count": int(generation_execution_plan.get("step_count") or 0),
        "active_workflow_semantic_catalog_count": int(
            len(active_workflow_semantic_catalog)
        ),
        "active_semantic_graph_fact_count": int(
            len(active_semantic_graph_catalog.get("facts") or [])
        ),
        "active_semantic_graph_node_count": int(
            len(active_semantic_graph_catalog.get("nodes") or [])
        ),
        "active_semantic_graph_edge_count": int(
            len(active_semantic_graph_catalog.get("edges") or [])
        ),
        "generation_execution_independent_suite_order": list(
            generation_execution_plan.get("independent_suite_order") or []
        ),
        "soft_constraints_in_prompt": bool(include_soft_constraints_in_text),
        "quality_fix_hints_in_prompt": bool(include_quality_fix_hints_in_text),
        "generation_coverage_mode": generation_coverage_mode,
        "generation_case_density": str(generation_profile.get("case_density") or "").strip(),
        "generation_target_case_range": dict(generation_profile.get("target_case_range") or {}),
        "fact_profile_source": str(fact_profile.get("profile_source") or "").strip(),
        "fact_profile_confidence": float(fact_profile.get("confidence") or 0.0),
        "fact_profile_confirmed_count": int(len(fact_profile.get("confirmed_facts") or [])),
        "fact_profile_pending_count": int(len(fact_profile.get("pending_items") or [])),
        "fact_profile_forbidden_count": int(len(fact_profile.get("forbidden_facts") or [])),
        "project_profile_source": str(project_profile.get("profile_source") or "").strip(),
        "project_profile_confidence": float(project_profile.get("confidence") or 0.0),
        "project_profile_flow_count": int(len(project_flow_outline.get("flow_order") or [])),
        "project_profile_cross_cutting_count": int(len(project_flow_outline.get("cross_cutting") or [])),
        "functional_module_count": int(len(functional_modules)),
        "functional_interaction_count": int(len(functional_architecture.get("module_interactions") or [])),
        "requirement_semantic_contract_status": str(
            requirement_semantic_contract.get("status") or ""
        ),
        "excluded_functional_module_count": int(len(functional_architecture.get("excluded_modules") or [])),
        "manual_quality_profile_source": str(manual_quality_profile.get("profile_source") or "").strip(),
        "manual_quality_profile_version": str(manual_quality_profile.get("profile_version") or "").strip(),
        "manual_quality_profile_trusted_count": int(manual_quality_profile.get("trusted_sample_count") or 0),
        "manual_quality_profile_high_priority_ratio": float(manual_quality_profile.get("high_priority_ratio") or 0.0),
        "manual_quality_profile_display_ratio_cap": float(manual_quality_profile.get("display_ratio_cap") or 0.0),
        "source_meta": dict(state.source_meta or {}),
    }

    has_prompt_signals = bool(
        state.must_cover_rules
        or state.must_have_scenarios
        or state.rule_quota
        or state.forbidden_patterns
        or state.preferred_patterns
        or state.reuse_risks
        or state.workflow_blueprints
        or active_semantic_graph_catalog
        or generation_coverage_mode
        or fact_profile
        or project_profile
        or (include_soft_constraints_in_text and state.soft_constraints)
        or (include_quality_fix_hints_in_text and state.quality_fix_hints)
    )
    if not has_prompt_signals:
        return "(empty)", summary

    lines: list[str] = ["[Generation Control - Structured]"]
    lines.append("### MUST COVER RULES")
    if state.must_cover_rules:
        lines.extend([f"* {item}" for item in state.must_cover_rules])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### MUST HAVE SCENARIOS")
    if state.must_have_scenarios:
        lines.extend([f"* {item}" for item in state.must_have_scenarios])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### RULE QUOTA")
    if state.rule_quota:
        for rule, quota in sorted(state.rule_quota.items(), key=lambda item: (item[0], -int(item[1] or 0))):
            lines.append(f"* {rule}: >= {int(quota)}")
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### FORBIDDEN PATTERNS")
    if state.forbidden_patterns:
        lines.extend([f"* {item}" for item in state.forbidden_patterns])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### PREFERRED PATTERNS")
    if state.preferred_patterns:
        lines.extend([f"* {item}" for item in state.preferred_patterns])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### REUSE RISKS")
    if state.reuse_risks:
        lines.extend([f"* {item}" for item in state.reuse_risks])
    else:
        lines.append("* (none)")

    lines.append("")
    lines.append("### WORKFLOW BLUEPRINTS")
    if state.workflow_blueprints:
        lines.append("* Treat workflow blueprints as execution-order contracts, not as reusable RAG examples.")
        for blueprint in state.workflow_blueprints:
            if not isinstance(blueprint, dict):
                continue
            name = str(blueprint.get("name") or blueprint.get("id") or "workflow").strip()
            source = str(
                blueprint.get("repository_source")
                or blueprint.get("source")
                or blueprint.get("source_type")
                or "unknown"
            ).strip()
            source_suffix = f" [source={source}]" if source and source.lower() != "unknown" else ""
            steps = [step for step in (blueprint.get("steps") or []) if isinstance(step, dict)]
            labels = [
                " / ".join(
                    item
                    for item in (
                        str(step.get("stage_kind") or "").strip(),
                        str(step.get("label") or step.get("action") or step.get("id") or "").strip(),
                        (
                            f"{step.get('state_in')}->{step.get('state_out')}"
                            if str(step.get("state_in") or "").strip()
                            and str(step.get("state_out") or "").strip()
                            else ""
                        ),
                    )
                    if item
                )
                for step in steps[:MAX_WORKFLOW_STEPS]
                if str(step.get("label") or step.get("action") or step.get("id") or "").strip()
            ]
            if labels:
                lines.append(f"* {name}{source_suffix}: {' -> '.join(labels)}")
    else:
        lines.append("* (none)")

    if active_workflow_semantic_catalog:
        lines.append("")
        lines.append("### ACTIVE WORKFLOW SEMANTIC CATALOG")
        lines.append(
            json.dumps(
                {"workflows": active_workflow_semantic_catalog},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        lines.append(
            "* `_semantic.workflow_stage_candidates[].workflow_id` MUST copy the exact "
            "workflow_id above; a workflow_name is never a workflow_id."
        )
        lines.append(
            "* `_semantic.workflow_stage_candidates[].stage_id` and stage_kind MUST copy "
            "the exact stage_id and stage_kind from the same workflow."
        )
        lines.append(
            "* Before independent suites, generate at least one valid main-chain case for "
            "every required_stage_id, in declared order. Do not use [] to avoid a matching "
            "required workflow stage."
        )
        lines.append(
            "* A main-chain case MUST copy the declared module_candidates, interaction_ids, "
            "required_states, and produced_states. Only case evidence and confidence are newly cited."
        )

    if active_semantic_graph_catalog:
        lines.append("")
        lines.append("### ACTIVE SEMANTIC GRAPH CATALOG")
        lines.append(
            json.dumps(
                active_semantic_graph_catalog,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        lines.append(
            "* Use graph_node_id, fact_ids, scope_candidates, and graph_relation_ids as the "
            "source of workflow ownership, dependency, constraint, transition, and interaction semantics."
        )

    if generation_execution_plan.get("lines"):
        lines.append("")
        lines.extend(list(generation_execution_plan.get("lines") or []))

    if fact_profile:
        lines.append("")
        lines.append("### FACT PROFILE")
        lines.append(f"* source: {str(fact_profile.get('profile_source') or 'unknown')}")
        lines.append(f"* confidence: {float(fact_profile.get('confidence') or 0.0):.2f}")
        lines.append("* Use this as factual guardrail. Current requirement wins on conflict.")
        for title, key, limit in (
            ("confirmed facts", "confirmed_facts", 8),
            ("forbidden facts", "forbidden_facts", 8),
            ("pending items", "pending_items", 6),
            ("hard flow constraints", "hard_flow_constraints", 6),
        ):
            values = [str(item).strip() for item in (fact_profile.get(key) or []) if str(item).strip()]
            if not values:
                continue
            lines.append(f"* {title}:")
            lines.extend([f"  - {item}" for item in values[:limit]])

    if project_profile:
        flow_outline = dict(project_profile.get("flow_outline") or {})
        flow_order = [str(item) for item in (flow_outline.get("flow_order") or []) if str(item).strip()]
        flow_labels = dict(flow_outline.get("flow_labels") or {})
        cross_cutting = [str(item) for item in (flow_outline.get("cross_cutting") or []) if str(item).strip()]
        cross_labels = dict(flow_outline.get("cross_cutting_labels") or {})
        lines.append("")
        lines.append("### PROJECT STRUCTURE PROFILE")
        lines.append(f"* source: {str(project_profile.get('profile_source') or 'unknown')}")
        lines.append(f"* confidence: {float(project_profile.get('confidence') or 0.0):.2f}")
        lines.append("* Use this as ordering and coverage structure only; it is not a fact source.")
        lines.append("* Final test cases should follow the flow outline first; put cross-cutting modules after the main flow unless a case explicitly validates their interaction with a main-flow step.")
        if functional_modules:
            module_names = [str(item.get("module_name") or "").strip() for item in functional_modules]
            module_names = [item for item in module_names if item]
            lines.append("")
            lines.append("### FUNCTIONAL MODULE CONTRACT")
            lines.append(f"* allowed test_module values: {', '.join(module_names)}")
            lines.append("* test_module MUST use one allowed primary module value; keep related or ambiguous module candidates in _semantic.module_candidates.")
            lines.append("* Generate module-internal features first, then explicit cross-module interactions.")
            lines.append("* Workflow entry controls and state-changing UI interactions belong to the business flow; only presentation-only checks belong to UI/display.")
            for module in functional_modules:
                name = str(module.get("module_name") or "").strip()
                features = [str(item).strip() for item in (module.get("features") or []) if str(item).strip()]
                aliases = [str(item).strip() for item in (module.get("aliases") or []) if str(item).strip()]
                detail = f"; explicit features: {' | '.join(features)}" if features else ""
                alias_text = f"; aliases: {', '.join(aliases)}" if aliases else ""
                lines.append(f"* {name}{alias_text}{detail}")
            excluded_modules = [
                item for item in (functional_architecture.get("excluded_modules") or []) if isinstance(item, dict)
            ]
            if excluded_modules:
                excluded_labels = [
                    f"{str(item.get('module_name') or '')}({str(item.get('scope_reason') or 'out_of_scope')})"
                    for item in excluded_modules
                ]
                lines.append(f"* out-of-scope modules, DO NOT generate: {', '.join(excluded_labels)}")
            interactions = [
                item for item in (functional_architecture.get("module_interactions") or []) if isinstance(item, dict)
            ]
            if interactions:
                lines.append("* explicit module interactions:")
                for item in interactions:
                    lines.append(
                        "  - "
                        f"[{str(item.get('interaction_id') or '')}] "
                        f"{str(item.get('source_module') or '')} -> {str(item.get('target_module') or '')}: "
                        f"{str(item.get('trigger') or '')}"
                    )
        if flow_order:
            labels = [str(flow_labels.get(key) or key) for key in flow_order]
            lines.append(f"* flow outline: {' -> '.join(labels)}")
        data_flow_edges = [
            item for item in (flow_outline.get("data_flow_edges") or []) if isinstance(item, dict)
        ]
        if data_flow_edges:
            edge_labels = [
                f"{str(item.get('from_label') or item.get('from') or '')} -> {str(item.get('to_label') or item.get('to') or '')}"
                for item in data_flow_edges
            ]
            lines.append(f"* data-flow edges: {'; '.join(edge_labels)}")
        if cross_cutting:
            labels = [str(cross_labels.get(key) or key) for key in cross_cutting]
            lines.append(f"* cross-cutting modules: {', '.join(labels)}")
        scenario_policy = dict(project_profile.get("scenario_cluster_policy") or {})
        explicit_scenario_cap = scenario_policy.get("default_max_per_scenario")
        try:
            explicit_scenario_cap = int(explicit_scenario_cap)
        except (TypeError, ValueError):
            explicit_scenario_cap = 0
        if explicit_scenario_cap > 0:
            lines.append(
                f"* explicit max per scenario: {explicit_scenario_cap}"
            )

    if generation_coverage_mode:
        target_range = dict(generation_profile.get("target_case_range") or {})
        lines.append("")
        lines.append("### GENERATION COVERAGE MODE")
        lines.append(f"* mode: {generation_coverage_mode}")
        if target_range:
            lines.append(
                f"* target case range: {int(target_range.get('min') or 0)}-{int(target_range.get('max') or 0)}"
            )
        lines.append("* Use this as a coverage-density strategy, not a quota.")
        if generation_coverage_mode == "full_functional_regression":
            lines.append("* Expand module x state x exception x cross-module coverage before stopping.")
            lines.append("* Prefer high-value failure/recovery, permission, moderation, retry, upload/download, and state-sync cases over additional display-only variants.")
        elif generation_coverage_mode == "expanded_regression":
            lines.append("* Keep broad requirement coverage, then prune near-duplicates and generic low-value checks.")
        elif generation_coverage_mode == "standard_regression":
            lines.append("* Balance core flow, state transitions, boundary/exception, and regression coverage.")
        else:
            lines.append("* Keep a compact high-value core set.")

    if include_soft_constraints_in_text:
        lines.append("")
        lines.append("### SOFT CONSTRAINTS (NEGATIVE BIAS)")
        if state.soft_constraints:
            lines.extend([f"* {item}" for item in state.soft_constraints])
        else:
            lines.append("* (none)")

    if include_quality_fix_hints_in_text:
        lines.append("")
        lines.append("### QUALITY FIX HINTS")
        if state.quality_fix_hints:
            lines.extend([f"* {item}" for item in state.quality_fix_hints])
        else:
            lines.append("* (none)")

    # 结构化控制契约必须完整进入模型上下文；静默裁剪会丢失尾部工作流、模块或交互。
    return "\n".join(lines).strip() or "(empty)", summary
