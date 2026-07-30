import json
import sys
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.prompting.structured_control_context import (
    _build_control_context,
)


def test_control_projection_compacts_graph_without_losing_tail_references() -> None:
    item_count = 80
    facts = [
        {
            "fact_id": f"fact_{index:03d}",
            "statement": f"Capability {index:03d} must remain testable",
            "requirement_level": "required",
            "priority": "p1",
            "testability": "testable",
        }
        for index in range(item_count)
    ]
    nodes = [
        {
            "node_id": f"node_{index:03d}",
            "kind": "capability",
            "name": f"Capability {index:03d}",
            "scope_status": "in_scope",
            "boundary_status": "resolved",
            "workflow_role": "none",
            "fact_ids": [f"fact_{index:03d}"],
        }
        for index in range(item_count)
    ]
    edges = [
        {
            "edge_id": f"edge_{index:03d}",
            "type": "depends_on",
            "source_node_id": f"node_{index:03d}",
            "target_node_id": f"node_{(index + 1) % item_count:03d}",
            "source_scope_id": "",
            "target_scope_id": "",
            "trigger": "",
            "result_state": "",
            "transferred_entity_node_ids": [],
            "fact_ids": [f"fact_{index:03d}"],
        }
        for index in range(item_count)
    ]
    semantic_contract = {
        "semantic_contract_version": "requirement-semantic-v2",
        "evidence_facts": facts,
        "semantic_graph_validation": {"publishable": True},
        "semantic_graph": {
            "graph_version": "requirement-semantic-graph-v1",
            "nodes": nodes,
            "edges": edges,
            "primary_flow": {"node_ids": [], "edge_ids": []},
        },
    }

    control_context, summary = _build_control_context(
        control_state={
            "source_meta": {
                "requirement_semantic_contract": semantic_contract,
                "project_profile": {
                    "functional_architecture": {
                        "functional_modules": [
                            {
                                "module_key": "capability",
                                "module_name": "Capability",
                                "features": [
                                    "Capability 079 must remain testable",
                                    "A distinct module-level behavior",
                                ],
                            }
                        ]
                    }
                },
            }
        }
    )

    graph_start = control_context.index("### ACTIVE SEMANTIC GRAPH CATALOG")
    graph_end = control_context.index("### PROJECT STRUCTURE PROFILE")
    graph_section = control_context[graph_start:graph_end]
    verbose_graph = json.dumps(
        {
            "facts": facts,
            "nodes": nodes,
            "edges": edges,
            "primary_flow": {"node_ids": [], "edge_ids": []},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    assert len(graph_section) < len(verbose_graph) * 0.72
    assert '["fact_079","Capability 079 must remain testable"' in graph_section
    assert '["node_079","capability","Capability 079"' in graph_section
    assert '["edge_079","depends_on","node_079","node_000"' in graph_section
    assert control_context.count("Capability 079 must remain testable") == 1
    assert "A distinct module-level behavior" in control_context
    assert summary["active_semantic_graph_fact_count"] == item_count
    assert summary["active_semantic_graph_node_count"] == item_count
    assert summary["active_semantic_graph_edge_count"] == item_count
    assert summary["control_context_chars"] == len(control_context)


def test_generation_shard_projection_separates_workflow_and_independent_context() -> None:
    control_state = {
        "workflow_blueprints": [
            {
                "workflow_id": "wf_publish",
                "name": "发布流程",
                "required_stage_ids": ["stage_submit"],
                "terminal_states": ["submitted"],
                "steps": [
                    {
                        "id": "stage_submit",
                        "stage_kind": "commit",
                        "label": "提交内容",
                        "state_in": "draft",
                        "state_out": "submitted",
                        "required": True,
                        "terminal": True,
                        "graph_node_id": "node_submit",
                        "fact_ids": ["fact_submit"],
                        "module_candidates": [
                            {
                                "module_key": "content",
                                "module_name": "内容管理",
                                "role": "owner",
                            }
                        ],
                    }
                ],
            }
        ],
        "source_meta": {
            "requirement_semantic_contract": {
                "semantic_contract_version": "requirement-semantic-v2",
                "semantic_graph_validation": {"publishable": True},
                "evidence_facts": [
                    {
                        "fact_id": "fact_submit",
                        "statement": "用户提交内容后状态变为已提交",
                    }
                ],
                "semantic_graph": {
                    "graph_version": "requirement-semantic-graph-v1",
                    "nodes": [
                        {
                            "node_id": "node_submit",
                            "kind": "action",
                            "name": "提交内容",
                            "fact_ids": ["fact_submit"],
                        }
                    ],
                    "edges": [],
                    "primary_flow": {"node_ids": [], "edge_ids": []},
                },
            },
            "project_profile": {
                "functional_architecture": {
                    "functional_modules": [
                        {
                            "module_key": "content",
                            "module_name": "内容管理",
                            "role": "owner",
                            "features": ["提交内容", "查看内容"],
                        }
                    ]
                }
            },
        },
    }

    full_context, _ = _build_control_context(control_state=control_state)
    main_context, main_summary = _build_control_context(
        control_state=control_state,
        generation_scope="main_chain",
    )
    independent_context, independent_summary = _build_control_context(
        control_state=control_state,
        generation_scope="independent",
    )

    assert "### ACTIVE SEMANTIC GRAPH CATALOG" in full_context
    assert "### ACTIVE SEMANTIC GRAPH CATALOG" not in main_context
    assert "### ACTIVE WORKFLOW SEMANTIC CATALOG" in main_context
    assert "stage_submit" in main_context
    assert "提交内容" in main_context
    assert "fact_submit" in main_context
    assert "### ACTIVE WORKFLOW SEMANTIC CATALOG" not in independent_context
    assert "### ACTIVE SEMANTIC GRAPH CATALOG" not in independent_context
    assert "Owned by the main-chain shard" in independent_context
    assert "内容管理" in independent_context
    assert len(main_context) < len(full_context)
    assert len(independent_context) < len(full_context)
    assert main_summary["generation_scope"] == "main_chain"
    assert independent_summary["generation_scope"] == "independent"
