import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.prompting.structured_context import build_structured_prompt_context
from modules.test_generation_components.prompting.structured_control_context import _build_control_context
from modules.test_generation_components.control.project_profile_activation import normalize_project_profile


def test_control_context_keeps_tail_architecture_after_large_rule_catalog() -> None:
    rules = [f"RULE-{index:03d}: {'x' * 100}" for index in range(100)]
    control_context, _ = _build_control_context(
        control_state={
            "must_cover_rules": rules,
            "source_meta": {
                "project_profile": {
                    "profile_source": "current_requirement",
                    "functional_architecture": {
                        "functional_modules": [
                            {"module_key": "source", "module_name": "Source Module"},
                            {"module_key": "target", "module_name": "Target Module"},
                        ],
                        "module_interactions": [
                            {
                                "interaction_id": "source_to_target",
                                "source_module": "Source Module",
                                "target_module": "Target Module",
                                "trigger": "verified trigger",
                            }
                        ],
                    },
                }
            },
        }
    )

    assert len(control_context) > 6000
    assert "### FUNCTIONAL MODULE CONTRACT" in control_context
    assert "[source_to_target] Source Module -> Target Module" in control_context


def test_structured_context_groups_requirement_and_supplement_by_biz_key() -> None:
    rag_result = {
        "debug": {
            "final_chunks": [
                {
                    "filename": "close_req.md",
                    "doc_type": "requirement",
                    "biz_key": "org_close_rule",
                    "module": "机构关闭",
                    "chunk_text": "REQ-023: 关闭机构前必须校验余额为0。",
                },
                {
                    "filename": "open_req.md",
                    "doc_type": "requirement",
                    "biz_key": "org_open_rule",
                    "module": "机构开通",
                    "chunk_text": "REQ-101: 开通机构前需完成审批。",
                },
            ]
        }
    }
    output = build_structured_prompt_context(
        requirement="REQ-024: 存在未结算订单时禁止关闭机构。",
        rag_result=rag_result,
        existing_cases=[
            {
                "id": "TC-001",
                "biz_key": "org_close_rule",
                "test_module": "机构关闭",
                "priority": "P0",
                "description": "余额不为0禁止关闭",
            },
            {
                "id": "TC-101",
                "biz_key": "org_open_rule",
                "test_module": "机构开通",
                "priority": "P1",
                "description": "审批通过后允许开通",
            },
        ],
        current_biz_key="org_close_rule",
        only_current_biz=False,
    )

    assert "[Requirements - grouped by biz_key]" in output["requirement_context"]
    assert "### biz_key: org_close_rule (当前业务)" in output["requirement_context"]
    assert "### biz_key: org_open_rule (参考)" in output["requirement_context"]
    assert "[Supplement - grouped by biz_key]" in output["supplement_context"]
    assert "### biz_key: org_close_rule (当前业务)" in output["testcase_context"]
    assert "### biz_key: org_open_rule (参考)" in output["testcase_context"]


def test_only_current_biz_keeps_only_current_scope() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-001: 关闭机构前需要校验余额。",
        rag_result={
            "debug": {
                "final_chunks": [
                    {
                        "filename": "close_req.md",
                        "doc_type": "requirement",
                        "biz_key": "org_close_rule",
                        "module": "机构关闭",
                        "chunk_text": "REQ-001: 关闭机构前需要校验余额。",
                    },
                    {
                        "filename": "open_req.md",
                        "doc_type": "requirement",
                        "biz_key": "org_open_rule",
                        "module": "机构开通",
                        "chunk_text": "REQ-101: 开通机构前需审批。",
                    },
                ]
            }
        },
        existing_cases=[
            {
                "id": "TC-001",
                "biz_key": "org_close_rule",
                "test_module": "机构关闭",
                "priority": "P0",
                "description": "关闭主流程",
            },
            {
                "id": "TC-002",
                "biz_key": "org_open_rule",
                "test_module": "机构开通",
                "priority": "P1",
                "description": "开通审批流程",
            },
        ],
        current_biz_key="org_close_rule",
        only_current_biz=True,
    )

    assert "org_open_rule" not in output["testcase_context"]
    assert "org_open_rule" not in output["requirement_context"]
    assert output["biz_key_isolation_log"]["mode"] == "strict_current_only"


def test_testcase_context_preserves_reference_module_order() -> None:
    output = build_structured_prompt_context(
        requirement="按参考用例顺序生成",
        existing_cases=[
            {
                "id": "TC-001",
                "biz_key": "learning_flow",
                "test_module": "督导端入口",
                "priority": "P0",
                "description": "入口展示",
            },
            {
                "id": "TC-002",
                "biz_key": "learning_flow",
                "test_module": "作业拍照批改",
                "priority": "P0",
                "description": "拍照批改",
            },
            {
                "id": "TC-003",
                "biz_key": "learning_flow",
                "test_module": "习题本",
                "priority": "P0",
                "description": "习题本展示",
            },
        ],
        current_biz_key="learning_flow",
        only_current_biz=False,
    )

    text = output["testcase_context"]

    assert text.index("#### test_module: 督导端入口") < text.index("#### test_module: 作业拍照批改")
    assert text.index("#### test_module: 作业拍照批改") < text.index("#### test_module: 习题本")


def test_testcase_context_accepts_alias_case_fields() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-ALIAS: close org",
        existing_cases=[
            {
                "caseId": "TC-ALIAS",
                "metadata": {"biz_key": "org_close_rule"},
                "module": "org-close",
                "Priority": "P0",
                "title": "verify alias close path",
            }
        ],
        current_biz_key="org_close_rule",
        only_current_biz=True,
    )

    text = output["testcase_context"]

    assert "### biz_key: org_close_rule" in text
    assert "#### test_module: org-close" in text
    assert "* TC-ALIAS: verify alias close path" in text
    assert output["module_order_hint"] == ["org-close"]
    assert output["context_by_biz"]["org_close_rule"]["module_order_hint"] == ["org-close"]


def test_module_order_hint_prefers_requirement_document_order_over_reference_cases() -> None:
    output = build_structured_prompt_context(
        requirement="按需求文档流程生成",
        rag_result={
            "debug": {
                "final_chunks": [
                    {
                        "filename": "req.md",
                        "doc_type": "requirement",
                        "biz_key": "learning_flow",
                        "module": "习题本",
                        "chunk_text": "REQ-001: 先查看习题本。",
                    },
                    {
                        "filename": "req.md",
                        "doc_type": "requirement",
                        "biz_key": "learning_flow",
                        "module": "周末提升计划",
                        "chunk_text": "REQ-002: 再进入周末提升计划。",
                    },
                ]
            }
        },
        existing_cases=[
            {
                "id": "TC-001",
                "biz_key": "learning_flow",
                "test_module": "周末提升计划",
                "priority": "P0",
                "description": "参考用例顺序靠前",
            },
            {
                "id": "TC-002",
                "biz_key": "learning_flow",
                "test_module": "习题本",
                "priority": "P0",
                "description": "参考用例顺序靠后",
            },
        ],
        current_biz_key="learning_flow",
        only_current_biz=False,
    )

    assert output["module_order_source"] == "requirement_document"
    assert output["module_order_hint"] == ["习题本", "周末提升计划"]
    assert output["context_by_biz"]["learning_flow"]["module_order_hint"] == ["习题本", "周末提升计划"]


def test_missing_fields_fallback_and_degrade_when_current_unknown() -> None:
    output = build_structured_prompt_context(
        requirement="登录失败超过5次触发异常提示。",
        kb_context=(
            "--- Relevant Knowledge: login_spec.md (requirement) ---\n"
            "登录失败超过5次需要图形验证码。\n"
        ),
        existing_cases=[{"description": "字段缺失也要可回退"}],
        current_biz_key="",
        only_current_biz=True,
    )

    assert output["current_biz_key"] == "unknown"
    assert "### biz_key: unknown (当前业务)" in output["testcase_context"]
    assert output["biz_key_isolation_log"]["mode"] == "reference_allowed_current_unknown"
    assert output["biz_key_order"]


def test_control_context_includes_preferred_patterns() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-901: keep settlement consistency",
        feedback_control_state={
            "must_cover_rules": ["RULE-901"],
            "preferred_patterns": ["deterministic settlement assertion chain"],
        },
    )

    assert "### PREFERRED PATTERNS" in output["control_context"]
    assert "deterministic settlement assertion chain" in output["control_context"]
    assert int(output["control_summary"].get("preferred_patterns_count") or 0) == 1
    assert "PREFERRED PATTERN QUOTA" not in output["control_context"]
    assert "Must generate at least" not in output["control_context"]


def test_project_profile_does_not_invent_a_default_scenario_cap() -> None:
    profile = normalize_project_profile(
        {
            "functional_architecture": {
                "functional_modules": [{"module_name": "发布区"}],
            }
        }
    )

    assert profile["scenario_cluster_policy"] == {}


def test_manual_quality_profile_is_diagnostic_only() -> None:
    output = build_structured_prompt_context(
        requirement="recent course schedule regression",
        feedback_control_state={
            "source_meta": {
                "manual_quality_profile": {
                    "kind": "manual_quality_profile",
                    "profile_source": "priority_sample_pool_manual_verified",
                    "profile_version": "stable-1",
                    "trusted_sample_count": 12,
                    "priority_distribution": {"P0": 4, "P1": 6, "P2": 2},
                    "module_distribution_top": {
                        "本周课程模块": 5,
                        "排课-学习计划-第1步": 4,
                    },
                    "execution_lifecycle_fields": ["ST", "release", "补充项"],
                    "high_priority_ratio": 0.83,
                    "display_ratio_cap": 0.25,
                }
            }
        },
    )

    context = output["control_context"]
    assert "MANUAL QUALITY PROFILE" not in context
    assert "target P0/P1 ratio" not in context
    assert "display-only cap" not in context
    assert output["control_summary"]["manual_quality_profile_high_priority_ratio"] == 0.83
    assert output["control_summary"]["manual_quality_profile_display_ratio_cap"] == 0.25


def test_control_context_includes_workflow_blueprints() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-904: checkout must close the paid order flow",
        feedback_control_state={
            "workflow_blueprints": [
                {
                    "id": "checkout_flow",
                    "name": "checkout flow",
                    "required_stage_ids": ["submit", "verify"],
                    "terminal_states": ["paid"],
                    "steps": [
                        {
                            "id": "submit",
                            "stage_kind": "commit",
                            "label": "Submit order",
                            "required": True,
                            "module_candidates": [
                                {
                                    "module_key": "checkout",
                                    "module_name": "Checkout",
                                    "role": "primary",
                                }
                            ],
                            "interaction_ids": ["submit_order"],
                            "required_states": [
                                {
                                    "entity": "order",
                                    "state": "draft",
                                    "source": "previous_stage",
                                    "scope": "workflow",
                                    "polarity": "positive",
                                    "temporal": "after_previous_stage",
                                }
                            ],
                            "produced_states": [
                                {
                                    "entity": "order",
                                    "state": "submitted",
                                    "source": "current_stage",
                                    "scope": "workflow",
                                    "polarity": "positive",
                                    "temporal": "after_case",
                                }
                            ],
                        },
                        {
                            "id": "verify",
                            "stage_kind": "completion_sync",
                            "label": "Verify paid status",
                            "required": True,
                            "terminal": True,
                        },
                    ],
                }
            ]
        },
    )

    assert "### WORKFLOW BLUEPRINTS" in output["control_context"]
    assert '"workflow_id":"checkout_flow"' in output["control_context"]
    assert '"stage_order":["submit","verify"]' in output["control_context"]
    assert '"stage_by_id":{"submit":{"stage_kind":"commit"' in output["control_context"]
    assert "### GENERATION EXECUTION PLAN" in output["control_context"]
    assert "* Generate main-chain cases first" in output["control_context"]
    assert "workflow_id=checkout_flow; name=checkout flow; required_stage_order=submit -> verify" in output[
        "control_context"
    ]
    assert "### ACTIVE WORKFLOW SEMANTIC CATALOG" in output["control_context"]
    assert '"workflow_id":"checkout_flow"' in output["control_context"]
    assert '"module_candidates":[{"module_key":"checkout"' in output["control_context"]
    assert '"interaction_ids":["submit_order"]' in output["control_context"]
    assert '"required_states":[{"entity":"order","state":"draft"' in output["control_context"]
    assert '"produced_states":[{"entity":"order","state":"submitted"' in output["control_context"]
    assert "a workflow_name is never a workflow_id" in output["control_context"]
    assert (
        "generate one separate executable main-chain candidate for each required_stage_id"
        in output["control_context"]
    )
    assert (
        "MUST copy the declared module_key/module_name/role values for module_candidates "
        "exactly and copy interaction_ids exactly."
        in output["control_context"]
    )
    assert "only case evidence and confidence are newly cited" in output["control_context"]
    assert (
        "`_semantic.precondition_states` and `_semantic.produced_states` may be empty; the "
        "execution plan inherits authoritative required_states and produced_states"
        in output["control_context"]
    )
    assert "Do not copy the catalog's typed-state arrays" in output["control_context"]
    assert (
        "additional typed state only when the current case's public fields provide exact evidence"
        in output["control_context"]
    )
    assert (
        "do not conflict with the matching workflow step's authoritative states"
        in output["control_context"]
    )
    assert "Map required_states to `_semantic.precondition_states`" not in output["control_context"]
    assert "never translate internal state identifiers" not in output["control_context"]
    assert "permission/security -> exception/recovery -> boundary/state rollback" in output["control_context"]
    assert int(output["control_summary"].get("workflow_blueprint_count") or 0) == 1
    assert int(output["control_summary"].get("generation_execution_plan_blueprint_count") or 0) == 1
    assert int(output["control_summary"].get("generation_execution_plan_step_count") or 0) == 2
    assert output["control_summary"]["active_workflow_semantic_catalog_count"] == 1
    assert output["control_summary"].get("generation_execution_independent_suite_order") == [
        "permission/security",
        "exception/recovery",
        "boundary/state rollback",
        "independent functional",
        "UI/display",
    ]


def test_control_context_keeps_complete_publishable_semantic_graph_contract() -> None:
    control_context, summary = _build_control_context(
        control_state={
            "workflow_blueprints": [
                {
                    "id": "submit_flow",
                    "steps": [
                        {
                            "id": "submit",
                            "stage_kind": "commit",
                            "label": "Submit order",
                            "graph_node_id": "submit_capability",
                            "fact_ids": ["f_submit"],
                            "scope_candidates": [
                                {
                                    "scope_id": "order_scope",
                                    "role": "primary",
                                    "fact_ids": ["f_submit"],
                                }
                            ],
                            "graph_relation_ids": [
                                "order_owns_submit",
                                "submit_to_review",
                            ],
                        },
                        {
                            "id": "review",
                            "stage_kind": "consume",
                            "label": "Review order",
                            "graph_node_id": "review_capability",
                            "fact_ids": ["f_review"],
                            "scope_candidates": [
                                {
                                    "scope_id": "order_scope",
                                    "role": "primary",
                                    "fact_ids": ["f_review"],
                                }
                            ],
                            "graph_relation_ids": [
                                "order_owns_review",
                                "submit_to_review",
                            ],
                        }
                    ],
                }
            ],
            "source_meta": {
                "requirement_semantic_contract": {
                    "semantic_contract_version": "requirement-semantic-v2",
                    "evidence_facts": [
                        {
                            "fact_id": "f_submit",
                            "statement": "User submits an order",
                            "requirement_level": "required",
                            "priority": "p1",
                            "testability": "testable",
                        },
                        {
                            "fact_id": "f_review",
                            "statement": "Reviewer checks the submitted order",
                            "requirement_level": "required",
                            "priority": "p1",
                            "testability": "testable",
                        }
                    ],
                    "semantic_graph_validation": {"publishable": True},
                    "semantic_graph": {
                        "graph_version": "requirement-semantic-graph-v1",
                        "nodes": [
                            {
                                "node_id": "order_scope",
                                "kind": "scope",
                                "name": "Order",
                                "scope_status": "in_scope",
                                "boundary_status": "resolved",
                                "workflow_role": "none",
                                "fact_ids": ["f_submit"],
                            },
                            {
                                "node_id": "submit_capability",
                                "kind": "capability",
                                "name": "Submit order",
                                "scope_status": "",
                                "boundary_status": "resolved",
                                "workflow_role": "entry",
                                "fact_ids": ["f_submit"],
                            },
                            {
                                "node_id": "review_capability",
                                "kind": "capability",
                                "name": "Review order",
                                "scope_status": "",
                                "boundary_status": "resolved",
                                "workflow_role": "terminal",
                                "fact_ids": ["f_review"],
                            },
                        ],
                        "edges": [
                            {
                                "edge_id": "order_owns_submit",
                                "type": "owns",
                                "source_node_id": "order_scope",
                                "target_node_id": "submit_capability",
                                "trigger": "",
                                "result_state": "",
                                "transferred_entity_node_ids": [],
                                "fact_ids": ["f_submit"],
                            },
                            {
                                "edge_id": "order_owns_review",
                                "type": "owns",
                                "source_node_id": "order_scope",
                                "target_node_id": "review_capability",
                                "trigger": "",
                                "result_state": "",
                                "transferred_entity_node_ids": [],
                                "fact_ids": ["f_review"],
                            },
                            {
                                "edge_id": "submit_to_review",
                                "type": "transitions",
                                "source_node_id": "submit_capability",
                                "target_node_id": "review_capability",
                                "trigger": "Order submitted",
                                "result_state": "Review available",
                                "transferred_entity_node_ids": [],
                                "fact_ids": ["f_review"],
                            }
                        ],
                        "primary_flow": {
                            "node_ids": [
                                "submit_capability",
                                "review_capability",
                            ],
                            "edge_ids": ["submit_to_review"],
                        },
                    },
                }
            },
        }
    )

    assert "### ACTIVE SEMANTIC GRAPH CATALOG" in control_context
    assert '"graph_node_id":"submit_capability"' in control_context
    assert '"graph_relation_ids":["order_owns_submit","submit_to_review"]' in control_context
    assert '"interaction_ids":[]' in control_context
    assert "graph_relation_ids are workflow topology references" in control_context
    assert "MUST NEVER be copied into case interaction_ids" in control_context
    assert '"fact_columns":["fact_id","statement"' in control_context
    assert '["f_review","Reviewer checks the submitted order"' in control_context
    assert '"node_columns":["node_id","kind"' in control_context
    assert '["review_capability","capability","Review order"' in control_context
    assert '"edge_columns":["edge_id","type"' in control_context
    assert '["order_owns_submit","owns","order_scope","submit_capability"' in control_context
    assert '["order_owns_review","owns","order_scope","review_capability"' in control_context
    assert '"primary_flow":{"node_ids":["submit_capability","review_capability"],"edge_ids":["submit_to_review"]}' in control_context
    assert summary["active_semantic_graph_fact_count"] == 2
    assert summary["active_semantic_graph_node_count"] == 3
    assert summary["active_semantic_graph_edge_count"] == 3


def test_control_context_keeps_independent_graph_and_items_beyond_sixty_four() -> None:
    item_count = 65
    control_context, summary = _build_control_context(
        control_state={
            "source_meta": {
                "requirement_semantic_contract": {
                    "semantic_contract_version": "requirement-semantic-v2",
                    "evidence_facts": [
                        {
                            "fact_id": f"f_{index:03d}",
                            "statement": f"Requirement fact {index:03d}",
                            "requirement_level": "required",
                            "priority": "p1",
                            "testability": "testable",
                        }
                        for index in range(item_count)
                    ],
                    "semantic_graph_validation": {"publishable": True},
                    "semantic_graph": {
                        "graph_version": "requirement-semantic-graph-v1",
                        "nodes": [
                            {
                                "node_id": f"node_{index:03d}",
                                "kind": "capability",
                                "name": f"Capability {index:03d}",
                                "scope_status": "in_scope",
                                "boundary_status": "resolved",
                                "workflow_role": "none",
                                "fact_ids": [f"f_{index:03d}"],
                            }
                            for index in range(item_count)
                        ],
                        "edges": [
                            {
                                "edge_id": f"edge_{index:03d}",
                                "type": "depends_on",
                                "source_node_id": f"node_{index:03d}",
                                "target_node_id": f"node_{(index + 1) % item_count:03d}",
                                "trigger": "",
                                "result_state": "",
                                "transferred_entity_node_ids": [],
                                "fact_ids": [f"f_{index:03d}"],
                            }
                            for index in range(item_count)
                        ],
                        "primary_flow": {"node_ids": [], "edge_ids": []},
                    },
                }
            }
        }
    )

    assert "### ACTIVE SEMANTIC GRAPH CATALOG" in control_context
    assert '["f_064","Requirement fact 064"' in control_context
    assert '["node_064","capability","Capability 064"' in control_context
    assert '["edge_064","depends_on","node_064","node_000"' in control_context
    assert '"primary_flow":{"node_ids":[],"edge_ids":[]}' in control_context
    assert summary["workflow_blueprint_count"] == 0
    assert summary["active_semantic_graph_fact_count"] == item_count
    assert summary["active_semantic_graph_node_count"] == item_count
    assert summary["active_semantic_graph_edge_count"] == item_count


def test_control_context_rejects_stale_graph_even_with_old_publishable_flag() -> None:
    control_context, summary = _build_control_context(
        control_state={
            "source_meta": {
                "requirement_semantic_contract": {
                    "semantic_contract_version": "requirement-semantic-v2",
                    "evidence_facts": [{"fact_id": "f_stale", "statement": "stale"}],
                    "semantic_graph_validation": {"publishable": True},
                    "semantic_graph": {
                        "graph_version": "unsupported-version",
                        "nodes": [
                            {
                                "node_id": "stale_node",
                                "kind": "constraint",
                                "fact_ids": ["f_stale"],
                            }
                        ],
                        "edges": [],
                    },
                }
            }
        }
    )

    assert "### ACTIVE SEMANTIC GRAPH CATALOG" not in control_context
    assert summary["active_semantic_graph_fact_count"] == 0
    assert summary["active_semantic_graph_node_count"] == 0
    assert summary["active_semantic_graph_edge_count"] == 0


def test_active_workflow_catalog_keeps_typed_state_fact_identity() -> None:
    control_context, _ = _build_control_context(
        control_state={
            "workflow_blueprints": [
                {
                    "id": "state_flow",
                    "steps": [
                        {
                            "id": "state_step",
                            "stage_kind": "commit",
                            "label": "提交状态",
                            "required_states": [
                                {
                                    "entity": "content",
                                    "state": "ready",
                                    "source": "external_fixture",
                                    "scope": "entity",
                                    "polarity": "positive",
                                    "temporal": "before_case",
                                    "fact_ids": ["f_content_ready"],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )

    assert '"fact_ids":["f_content_ready"]' in control_context


def test_control_context_keeps_workflow_steps_beyond_old_twelve_step_limit() -> None:
    steps = [
        {"id": f"stage_{index:02d}", "label": f"Stage {index:02d}"}
        for index in range(1, 14)
    ]

    output = build_structured_prompt_context(
        requirement="REQ: execute all declared workflow stages",
        feedback_control_state={
            "workflow_blueprints": [
                {"id": "long_flow", "name": "long flow", "steps": steps}
            ]
        },
    )

    assert "required_stage_order=stage_01 -> stage_02" in output["control_context"]
    assert "stage_13" in output["control_context"]
    assert "Stage 13" in output["control_context"]
    assert output["control_summary"]["generation_execution_plan_step_count"] == 13


def test_structured_context_builds_fact_and_project_profiles() -> None:
    output = build_structured_prompt_context(
        requirement="REQ-100: Inventory imports must not include archived records.",
        rag_result={
            "debug": {
                "final_chunks": [
                    {
                        "filename": "inventory_req.md",
                        "doc_type": "requirement",
                        "biz_key": "inventory_flow",
                        "module": "Upload Center",
                        "chunk_text": "REQ-101: Upload Center validates files before Review Queue.",
                    },
                    {
                        "filename": "inventory_req.md",
                        "doc_type": "requirement",
                        "biz_key": "inventory_flow",
                        "module": "Review Queue",
                        "chunk_text": "REQ-102: Review Queue approval happens before Dashboard statistics.",
                    },
                ]
            }
        },
        current_biz_key="inventory_flow",
        only_current_biz=True,
    )

    assert output["fact_profile"]["confirmed_facts"]
    requirement_semantics = [
        *output["fact_profile"]["confirmed_facts"],
        *output["fact_profile"]["scoped_rules"],
    ]
    assert any(
        "must not include archived records" in item.lower()
        for item in requirement_semantics
    )
    assert output["fact_profile"]["forbidden_facts"] == []
    assert output["project_profile"]["flow_outline"]["flow_order"]
    assert output["project_profile"]["flow_outline"]["data_flow_edges"] == []
    assert output["project_profile"]["scenario_cluster_policy"] == {}
    assert "### FACT PROFILE" in output["control_context"]
    assert "### PROJECT STRUCTURE PROFILE" in output["control_context"]
    assert "* data-flow edges:" not in output["control_context"]
    assert output["feedback_control_state"]["source_meta"]["fact_profile"]["forbidden_facts"] == []
    assert output["feedback_control_state"]["source_meta"]["project_profile"]["flow_outline"]["flow_order"]


def test_structured_context_excludes_requirement_parse_diagnostics_from_fact_profile() -> None:
    output = build_structured_prompt_context(
        requirement="""
论坛详情页必须展示评论入口，并支持用户发表回复。

[Requirement Understanding]
{"version":"requirement-understanding-v1","visual_facts":[{"source":"pdf_visual:X46.jpg","text":"版主回复标签仅版主内容展示，信息被隐藏"}]}

[Parsed Requirement Evidence]
- pdf_visual: filename=X46.jpg, strategy=pdf_image_ocr, chars=917, ocr_source=cloud, cloud_fallback=true

[Multimodal Evidence Alignment]
- pdf_visual:X46.jpg -> requirement score=1.00; requirement="论坛"; evidence="版主回复标签仅版主内容展示"
""",
    )

    merged = "\n".join(output["fact_profile"].get("confirmed_facts") or [])
    merged += "\n".join(output["fact_profile"].get("hard_flow_constraints") or [])
    assert "评论入口" in merged
    assert "pdf_visual" not in merged
    assert "信息被隐藏" not in merged


def test_legacy_preferred_quota_env_does_not_turn_soft_patterns_into_a_quota(monkeypatch) -> None:
    monkeypatch.setenv("TESTGEN_ENABLE_STRONG_PREFERRED_QUOTA_AB", "true")
    monkeypatch.setenv("TESTGEN_PREFERRED_FLOW_CASE_QUOTA", "2")
    monkeypatch.setenv("TESTGEN_UI_CASE_RATIO_CAP", "0.4")
    output = build_structured_prompt_context(
        requirement="REQ-902: settlement flow reliability",
        feedback_control_state={
            "preferred_patterns": ["multi-step settlement closure path"],
        },
    )

    assert "### PREFERRED PATTERN QUOTA (AB)" not in output["control_context"]
    assert "at least 2 workflow/state-transition cases" not in output["control_context"]
    assert "preferred_quota_variant" not in output["control_summary"]
    assert "preferred_flow_case_quota" not in output["control_summary"]


def test_structured_context_extracts_requirement_semantics_and_reuse_risks() -> None:
    requirement = """
    已确认：先选版本，再选年级。
    复用单词消消乐页面，完成后回首页，不是回原列表页。
    学课文 -> 词组消消乐 -> 选词填空。
    待确认：按钮是否仅在全部完成后才展示。
    """
    output = build_structured_prompt_context(
        requirement=requirement,
        rag_result={
            "debug": {
                "final_chunks": [
                    {
                        "filename": "lesson_req.md",
                        "doc_type": "requirement",
                        "biz_key": "lesson_flow",
                        "module": "学习流程",
                        "chunk_text": "复用选词填空页面，返回目标必须是首页。",
                    }
                ]
            }
        },
        current_biz_key="lesson_flow",
        only_current_biz=True,
    )

    assert "先选版本，再选年级" in output["requirement_semantics_context"]
    assert "待确认" in output["requirement_semantics_context"]
    assert "复用单词消消乐页面" in output["requirement_semantics_context"]
    assert "词组消消乐" in output["requirement_semantics_context"]
    assert output["confirmed_facts"]
    assert output["pending_items"]
    assert output["reuse_declarations"]
    assert output["hard_flow_constraints"]
    assert any("wrong_return_target_risk" in item for item in output["reuse_risks"])
    assert "### REUSE RISKS" in output["control_context"]
    assert int(output["control_summary"].get("reuse_risks_count") or 0) >= 1
