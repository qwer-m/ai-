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


def _compact_record(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """只投影有语义的字段，避免在大型语义图中重复发送空值键。"""

    output: dict[str, Any] = {}
    for key in fields:
        value = item.get(key)
        if value in (None, "", [], {}) or value is False:
            continue
        output[key] = value
    return output


def _project_rows(
    items: list[dict[str, Any]],
    *,
    id_field: str,
    fields: tuple[str, ...],
) -> list[list[Any]]:
    """把大型目录投影为带列契约的行，每条记录不再重复字段名。"""

    rows: list[list[Any]] = []
    for item in items:
        row: list[Any] = [str(item.get(id_field) or "").strip()]
        row.extend(item.get(field) for field in fields)
        while len(row) > 1 and row[-1] in (None, "", [], {}, False):
            row.pop()
        rows.append(row)
    return rows


def _build_active_workflow_semantic_catalog(
    workflow_blueprints: list[dict[str, Any]],
    semantic_graph_catalog: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """向模型交付唯一的工作流语义目录，避免在多个章节重复展开步骤。"""

    catalog: list[dict[str, Any]] = []
    graph_catalog = dict(semantic_graph_catalog or {})
    node_columns = list(graph_catalog.get("node_columns") or [])
    graph_nodes_by_id: dict[str, dict[str, Any]] = {}
    for row in graph_catalog.get("nodes") or []:
        if not isinstance(row, list) or not row:
            continue
        graph_nodes_by_id[str(row[0])] = {
            str(column): row[index] if index < len(row) else None
            for index, column in enumerate(node_columns)
        }
    for blueprint in workflow_blueprints:
        if not isinstance(blueprint, dict):
            continue
        workflow_id = str(
            blueprint.get("workflow_id") or blueprint.get("id") or ""
        ).strip()
        if not workflow_id:
            continue
        declared_required_stage_ids = {
            str(item).strip()
            for item in (blueprint.get("required_stage_ids") or [])
            if str(item).strip()
        }
        declared_terminal_states = {
            str(item).strip()
            for item in (blueprint.get("terminal_states") or [])
            if str(item).strip()
        }
        steps: list[dict[str, Any]] = []
        for step in (blueprint.get("steps") or [])[:MAX_WORKFLOW_STEPS]:
            if not isinstance(step, dict):
                continue
            stage_id = str(step.get("id") or "").strip()
            stage_kind = str(step.get("stage_kind") or "").strip()
            if not stage_id or not stage_kind:
                continue
            projected_step = {
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
            graph_node = graph_nodes_by_id.get(
                str(projected_step.get("graph_node_id") or "")
            )
            if graph_node:
                if str(projected_step.get("label") or "").casefold() == str(
                    graph_node.get("name") or ""
                ).casefold():
                    projected_step["label"] = ""
                if list(projected_step.get("fact_ids") or []) == list(
                    graph_node.get("fact_ids") or []
                ):
                    projected_step["fact_ids"] = []
            if stage_id in declared_required_stage_ids:
                projected_step["required"] = False
            if str(projected_step.get("state_out") or "") in declared_terminal_states:
                projected_step["terminal"] = False
            compact_step = _compact_record(
                projected_step,
                (
                    "stage_id",
                    "stage_kind",
                    "label",
                    "state_in",
                    "state_out",
                    "required",
                    "terminal",
                    "graph_node_id",
                    "fact_ids",
                    "scope_candidates",
                    "graph_relation_ids",
                    "module_candidates",
                    "interaction_ids",
                    "required_states",
                    "produced_states",
                ),
            )
            # interaction_ids 的空集同样是有效契约：明确告诉生成模型
            # 不得把 graph_relation_ids 中的控制边误当成业务交互 ID。
            compact_step["interaction_ids"] = list(
                projected_step.get("interaction_ids") or []
            )
            steps.append(compact_step)
        if not steps:
            continue
        stage_ids = [str(item.get("stage_id") or "").strip() for item in steps]
        workflow_projection: dict[str, Any] = {
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
            "stage_order": stage_ids,
        }
        if workflow_projection["required_stage_ids"] == stage_ids:
            workflow_projection["required_stage_ids"] = []
            workflow_projection["all_stages_required"] = True
        if len(set(stage_ids)) == len(stage_ids):
            workflow_projection["stage_by_id"] = {
                stage_id: {
                    key: value
                    for key, value in step.items()
                    if key != "stage_id"
                }
                for stage_id, step in zip(stage_ids, steps)
            }
        else:
            # 重复 stage_id 属于上游契约问题；提示层仍保留全量数据，不静默覆盖。
            workflow_projection["steps"] = steps
        catalog.append(
            _compact_record(
                workflow_projection,
                (
                    "workflow_id",
                    "workflow_name",
                    "required_stage_ids",
                    "all_stages_required",
                    "terminal_states",
                    "stage_order",
                    "stage_by_id",
                    "steps",
                ),
            )
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
    fact_fields = ("statement", "requirement_level", "priority", "testability")
    node_fields = (
        "kind",
        "name",
        "scope_status",
        "boundary_status",
        "workflow_role",
        "fact_ids",
    )
    edge_fields = (
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
    facts = _project_rows(
        sorted(raw_facts, key=lambda item: str(item.get("fact_id"))),
        id_field="fact_id",
        fields=fact_fields,
    )
    nodes = _project_rows(
        sorted(raw_nodes, key=lambda item: str(item.get("node_id"))),
        id_field="node_id",
        fields=node_fields,
    )
    edges = _project_rows(
        sorted(raw_edges, key=lambda item: str(item.get("edge_id"))),
        id_field="edge_id",
        fields=edge_fields,
    )
    if not (facts or nodes or edges):
        return {}
    return {
        "projection_version": "generation-semantic-projection-v1",
        "graph_version": str(graph.get("graph_version") or "").strip(),
        "fact_columns": ["fact_id", *fact_fields],
        "facts": facts,
        "node_columns": ["node_id", *node_fields],
        "nodes": nodes,
        "edge_columns": ["edge_id", *edge_fields],
        "edges": edges,
        "primary_flow": primary_flow,
    }


def _semantic_projection_texts(catalog: dict[str, Any]) -> set[str]:
    """收集语义图中已发布文本，只去掉项目概要里的精确重复项。"""

    texts: set[str] = set()
    for bucket_name, column_name, fields in (
        ("facts", "fact_columns", ("statement",)),
        ("nodes", "node_columns", ("name",)),
        ("edges", "edge_columns", ("trigger", "result_state")),
    ):
        bucket = catalog.get(bucket_name)
        columns = catalog.get(column_name)
        if not isinstance(bucket, list) or not isinstance(columns, list):
            continue
        field_indexes = [columns.index(field) for field in fields if field in columns]
        for item in bucket:
            if not isinstance(item, list):
                continue
            for index in field_indexes:
                value = str(item[index] if index < len(item) else "").strip()
                if value:
                    texts.add(value.casefold())
    return texts


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
        stage_order = [
            str(step.get("id") or f"step_{index:03d}").strip()
            for index, step in enumerate(steps[:MAX_WORKFLOW_STEPS], start=1)
            if str(
                step.get("label")
                or step.get("action")
                or step.get("description")
                or step.get("id")
                or ""
            ).strip()
        ]
        if not stage_order:
            continue
        blueprint_count += 1
        step_count += len(stage_order)
        workflow_id = str(
            blueprint.get("workflow_id") or blueprint.get("id") or "workflow"
        ).strip()
        name = str(blueprint.get("name") or workflow_id).strip()
        plan_lines.append(
            f"* workflow_id={workflow_id}; name={name}; required_stage_order="
            + " -> ".join(stage_order)
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
    generation_scope: str = "full",
) -> tuple[str, dict[str, Any]]:
    state = FeedbackControlState.from_any(control_state)
    normalized_generation_scope = str(generation_scope or "full").strip().lower()
    if normalized_generation_scope not in {"full", "main_chain", "independent"}:
        normalized_generation_scope = "full"
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
    full_semantic_graph_catalog = _build_active_semantic_graph_catalog(
        requirement_semantic_contract,
    )
    if normalized_generation_scope == "full":
        active_semantic_graph_catalog = full_semantic_graph_catalog
        active_workflow_semantic_catalog = _build_active_workflow_semantic_catalog(
            state.workflow_blueprints,
            active_semantic_graph_catalog,
        )
        generation_execution_plan = _build_generation_execution_plan_from_blueprints(
            state.workflow_blueprints
        )
    elif normalized_generation_scope == "main_chain":
        # 主链分片只需要可独立引用的工作流目录；标签、事实和状态直接保留在步骤内，
        # 不再重复发送整张语义图。
        active_semantic_graph_catalog = {}
        active_workflow_semantic_catalog = _build_active_workflow_semantic_catalog(
            state.workflow_blueprints,
        )
        generation_execution_plan = _build_generation_execution_plan_from_blueprints(
            state.workflow_blueprints
        )
    else:
        # 独立覆盖分片由分片规则和功能架构约束，不消费主链工作流与全量语义图。
        active_semantic_graph_catalog = {}
        active_workflow_semantic_catalog = []
        generation_execution_plan = {}
    semantic_projection_texts = _semantic_projection_texts(
        active_semantic_graph_catalog
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
        "control_projection_version": "generation-semantic-projection-v1",
        "generation_scope": normalized_generation_scope,
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
        summary["control_context_chars"] = 0
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
    if normalized_generation_scope == "independent":
        lines.append("* Owned by the main-chain shard; do not generate or reinterpret workflow stages here.")
    elif active_workflow_semantic_catalog:
        lines.append("* Treat workflow blueprints as execution-order contracts, not as reusable RAG examples.")
        lines.append(
            "* Canonical workflow fields are declared once in ACTIVE WORKFLOW SEMANTIC CATALOG; "
            "do not infer a second workflow from this section."
        )
    elif state.workflow_blueprints:
        # 老数据缺少 stage_kind 时无法建立可引用目录，仍保留可读执行顺序。
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
            "* `_semantic.workflow_stage_candidates[].stage_id` MUST copy a key from stage_by_id; "
            "stage_kind MUST copy the value from that same entry."
        )
        lines.append(
            "* Before independent suites, generate one separate executable main-chain candidate for "
            "each required_stage_id, in declared order; when all_stages_required=true, every "
            "stage_order entry is required. Do not use [] to avoid a matching required workflow stage."
        )
        lines.append(
            "* A main-chain case MUST copy the declared module_key/module_name/role values for "
            "module_candidates exactly and copy interaction_ids exactly. "
            "An explicit empty interaction_ids array MUST remain empty; graph_relation_ids are "
            "workflow topology references and MUST NEVER be copied into case interaction_ids. "
            "Missing label/fact_ids inherit from graph_node_id; "
            "only case evidence and confidence are newly cited."
        )
        lines.append(
            "* Copy every directly verified active fact ID into `_semantic.fact_ids`; "
            "reuse the same fact ID for the same atomic behavior across shards, and use the "
            "union of fact IDs only for a genuinely combined case."
        )
        lines.append(
            "* `_semantic.precondition_states` and `_semantic.produced_states` may be empty; the "
            "execution plan inherits authoritative required_states and produced_states from the "
            "matching workflow step. Do not copy the catalog's typed-state arrays into the candidate."
        )
        lines.append(
            "* Declare an additional typed state only when the current case's public fields provide "
            "exact evidence for it. Use canonical entity/state/source/scope/polarity/temporal values, "
            "and do not conflict with the matching workflow step's authoritative states."
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
            "* Each row follows its declared *_columns order; the first cell is the exact identifier. "
            "Use those references as the source of ownership, dependency, constraint, transition, and interaction semantics."
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
            values = [
                str(item).strip()
                for item in (fact_profile.get(key) or [])
                if str(item).strip()
                and str(item).strip().casefold() not in semantic_projection_texts
            ]
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
                features = [
                    str(item).strip()
                    for item in (module.get("features") or [])
                    if str(item).strip()
                    and str(item).strip().casefold() not in semantic_projection_texts
                ]
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
        if flow_order and not active_workflow_semantic_catalog:
            labels = [str(flow_labels.get(key) or key) for key in flow_order]
            lines.append(f"* flow outline: {' -> '.join(labels)}")
        data_flow_edges = [
            item for item in (flow_outline.get("data_flow_edges") or []) if isinstance(item, dict)
        ]
        if data_flow_edges and not active_semantic_graph_catalog:
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

    # 投影只消除精确重复和空字段，不根据字符数静默裁剪尾部工作流或图引用。
    control_context = "\n".join(lines).strip() or "(empty)"
    summary["control_context_chars"] = len(control_context) if control_context != "(empty)" else 0
    return control_context, summary
