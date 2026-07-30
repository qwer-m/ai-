import copy

import pytest

from modules.test_generation_components.control.requirement_semantic_graph import (
    EDGE_SIGNATURES,
    EDGE_TYPES,
    STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES,
    adapt_workflows_from_semantic_graph,
    edge_signature_contract_prompt,
    normalize_requirement_semantic_graph,
    project_functional_architecture_from_graph,
)
from modules.test_generation_components.control.project_profile_activation import (
    build_project_profile,
)
from modules.test_generation_components.control.requirement_evidence_view import (
    build_requirement_business_evidence_view,
)
from modules.test_generation_components.control.semantic_contract import (
    evidence_supported,
    graph_typed_state_identity_rejections,
    normalize_requirement_semantic_contract,
)
from modules.test_generation_components.prompting.structured_context import (
    build_structured_prompt_context,
)


SOURCE = """
系统包含接入域和处理域。
接入域接收批次请求。
处理域执行批次任务。
批次请求到达后触发接收，接收完成后开始执行。
执行完成后形成完成状态。
接收完成后也可直接形成完成状态。
接入域把批次请求交给处理域，处理域返回完成状态。
说明文字只用于介绍运行背景。
"""


def test_evidence_supported_preserves_cjk_ocr_component_characters() -> None:
    source = "技法巩固\n结果⻚\n继续下一步"

    assert evidence_supported(["结果⻚"], source) is True
    assert evidence_supported(["结果"], source) is False


def _fact(
    fact_id: str,
    statement: str,
    evidence: str,
    *,
    level: str = "required",
    priority: str = "p1",
    testability: str = "testable",
) -> dict:
    return {
        "fact_id": fact_id,
        "fact_kind": "behavior",
        "statement": statement,
        "requirement_level": level,
        "priority": priority,
        "testability": testability,
        "evidence": [evidence],
        "confidence": 0.94,
    }


def _node(
    node_id: str,
    kind: str,
    name: str,
    fact_ids: list[str],
    *,
    scope_status: str = "",
    workflow_role: str = "none",
) -> dict:
    return {
        "node_id": node_id,
        "kind": kind,
        "name": name,
        "aliases": [],
        "scope_status": scope_status,
        "boundary_status": "resolved",
        "workflow_role": workflow_role,
        "fact_ids": fact_ids,
        "confidence": 0.93,
    }


def _edge(
    edge_id: str,
    edge_type: str,
    source: str,
    target: str,
    fact_ids: list[str],
    *,
    ownership_role: str = "none",
    trigger: str = "",
    result_state: str = "",
) -> dict:
    return {
        "edge_id": edge_id,
        "type": edge_type,
        "source_node_id": source,
        "target_node_id": target,
        "fact_ids": fact_ids,
        "ownership_role": ownership_role,
        "trigger": trigger,
        "result_state": result_state,
        "transferred_entity_node_ids": [],
        "confidence": 0.92,
    }


def _payload() -> dict:
    return {
        "evidence_facts": [
            _fact("f_scope", "系统由两个职责域构成", "系统包含接入域和处理域"),
            _fact(
                "f_receive",
                "接入职责接收批次请求",
                "接入域接收批次请求",
                priority="p0",
            ),
            _fact("f_execute", "处理职责执行批次任务", "处理域执行批次任务"),
            _fact(
                "f_trigger",
                "批次请求到达后触发接收",
                "批次请求到达后触发接收",
            ),
            _fact(
                "f_transition",
                "接收完成后开始执行",
                "接收完成后开始执行",
            ),
            _fact("f_terminal", "执行完成形成完成状态", "执行完成后形成完成状态"),
            _fact(
                "f_interaction",
                "接入职责向处理职责传递请求并接收状态",
                "接入域把批次请求交给处理域，处理域返回完成状态",
                level="optional",
                priority="p2",
            ),
        ],
        "semantic_graph": {
            "graph_version": "requirement-semantic-graph-v1",
            "nodes": [
                _node("s_ingress", "scope", "接入域", ["f_scope", "f_receive"], scope_status="in_scope"),
                _node("s_process", "scope", "处理域", ["f_scope", "f_execute"], scope_status="in_scope"),
                _node("t_arrival", "trigger", "批次请求到达", ["f_trigger"]),
                _node(
                    "c_receive",
                    "capability",
                    "接收批次请求",
                    ["f_receive", "f_interaction"],
                ),
                _node(
                    "c_execute",
                    "capability",
                    "执行批次任务",
                    ["f_execute", "f_interaction"],
                ),
                _node("st_complete", "state", "完成状态", ["f_terminal"]),
            ],
            "edges": [
                _edge("e_own_receive", "owns", "s_ingress", "c_receive", ["f_receive"], ownership_role="primary"),
                _edge("e_own_execute", "owns", "s_process", "c_execute", ["f_execute"], ownership_role="primary"),
                _edge("e_trigger_receive", "triggers", "t_arrival", "c_receive", ["f_trigger"]),
                _edge("e_receive_execute", "transitions", "c_receive", "c_execute", ["f_transition"]),
                _edge("e_execute_complete", "transitions", "c_execute", "st_complete", ["f_terminal"]),
                _edge(
                    "e_handoff",
                    "interacts_with",
                    "c_receive",
                    "c_execute",
                    ["f_interaction"],
                    trigger="批次请求交接",
                    result_state="完成状态已返回",
                ),
            ],
            "fact_dispositions": [],
            "primary_flow": {
                "node_ids": [
                    "t_arrival",
                    "c_receive",
                    "c_execute",
                    "st_complete",
                ],
                "edge_ids": [
                    "e_trigger_receive",
                    "e_receive_execute",
                    "e_execute_complete",
                ],
            },
        },
    }


def _normalize(payload: dict) -> dict:
    return normalize_requirement_semantic_graph(
        payload,
        source_text=SOURCE,
        evidence_validator=evidence_supported,
    )


def _error_codes(result: dict) -> set[str]:
    return {str(item.get("code")) for item in result.get("errors") or []}


def _workflow_topology_errors(result: dict) -> list[dict]:
    return list((result.get("diagnostics") or {}).get("workflow_topology_errors") or [])


def _workflow_topology_codes(result: dict) -> set[str]:
    return {
        str(item.get("code"))
        for item in _workflow_topology_errors(result)
    }


def test_graph_is_publishable_and_projects_compatibility_architecture() -> None:
    result = _normalize(_payload())

    assert result["publishable"] is True
    assert result["diagnostics"]["workflow_topology_status"] == "linearizable"
    assert _workflow_topology_errors(result) == []
    assert result["semantic_graph"]["derived_critical_entry_ids"] == ["t_arrival"]
    architecture = project_functional_architecture_from_graph(result)
    assert architecture["source"] == "semantic_graph_projection"
    assert [item["module_key"] for item in architecture["functional_modules"]] == [
        "s_ingress",
        "s_process",
    ]
    assert architecture["functional_modules"][0]["features"] == ["接收批次请求"]
    assert architecture["functional_modules"][0]["fact_ids"] == [
        "f_interaction",
        "f_receive",
        "f_scope",
    ]
    assert architecture["functional_modules"][1]["fact_ids"] == [
        "f_execute",
        "f_interaction",
        "f_scope",
    ]
    assert architecture["module_interactions"][0]["source_module_key"] == "s_ingress"
    assert architecture["module_interactions"][0]["target_module_key"] == "s_process"


def test_scope_alias_conflict_reports_stable_node_ids() -> None:
    payload = _payload()
    scope_nodes = [
        node
        for node in payload["semantic_graph"]["nodes"]
        if node["kind"] == "scope"
    ]
    scope_nodes[0]["aliases"] = ["shared-boundary"]
    scope_nodes[1]["aliases"] = ["shared-boundary"]

    result = _normalize(payload)

    conflicts = [
        item
        for item in result["errors"]
        if item.get("code") == "scope_alias_boundary_ambiguous"
    ]
    assert conflicts == [
        {
            "code": "scope_alias_boundary_ambiguous",
            "path": "$.semantic_graph.nodes",
            "node_ids": ["s_ingress", "s_process"],
            "count": 2,
        }
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda payload: payload["semantic_graph"].update(
                {"graph_version": "requirement-semantic-graph-v0"}
            ),
            "graph_version_invalid",
        ),
        (
            lambda payload: payload["evidence_facts"][0].update(
                {"requirement_level": "mandatory"}
            ),
            "fact_requirement_level_invalid",
        ),
        (
            lambda payload: payload["semantic_graph"]["nodes"][0].update(
                {"scope_status": "active"}
            ),
            "node_scope_status_invalid",
        ),
        (
            lambda payload: payload["semantic_graph"]["edges"][0].update(
                {"ownership_role": "owner"}
            ),
            "edge_ownership_role_invalid",
        ),
        (
            lambda payload: payload["semantic_graph"]["fact_dispositions"].append(
                {
                    "fact_id": "f_scope",
                    "disposition": "ignored",
                    "reason": "非法枚举不能静默改写",
                }
            ),
            "fact_disposition_invalid",
        ),
    ],
)
def test_graph_rejects_invalid_versions_and_declared_enums(
    mutation,
    expected_code: str,
) -> None:
    payload = _payload()
    mutation(payload)

    result = _normalize(payload)

    assert result["publishable"] is False
    assert expected_code in _error_codes(result)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda payload: payload["evidence_facts"].append(
                _fact(
                    "f_uncovered",
                    "背景说明未进入图",
                    "说明文字只用于介绍运行背景",
                    level="optional",
                )
            ),
            "uncovered_fact",
        ),
        (
            lambda payload: payload["semantic_graph"]["nodes"][2].update(
                {"fact_ids": ["f_missing"]}
            ),
            "node_fact_reference_unknown",
        ),
        (
            lambda payload: payload["semantic_graph"]["edges"][0].update(
                {"fact_ids": ["f_missing"]}
            ),
            "edge_fact_reference_unknown",
        ),
    ],
)
def test_graph_enforces_bidirectional_fact_coverage(mutation, expected_code: str) -> None:
    payload = _payload()
    mutation(payload)

    result = _normalize(payload)

    assert result["publishable"] is False
    assert expected_code in _error_codes(result)


def test_declared_but_rejected_fact_does_not_masquerade_as_unknown_reference() -> None:
    payload = _payload()
    rejected_fact_id = payload["semantic_graph"]["nodes"][2]["fact_ids"][0]
    rejected_fact = next(
        item
        for item in payload["evidence_facts"]
        if item["fact_id"] == rejected_fact_id
    )
    rejected_fact["evidence"] = ["这是一句不在需求原文中的改写"]

    result = _normalize(payload)
    codes = _error_codes(result)

    assert result["publishable"] is False
    assert "fact_evidence_unverified" in codes
    assert "node_fact_dependency_rejected" in codes
    assert "node_fact_reference_unknown" not in codes
    assert "edge_endpoint_dependency_rejected" in codes
    assert "edge_endpoint_unknown" not in codes


def test_node_id_in_fact_ids_is_rejected_as_cross_registry_reference() -> None:
    payload = _payload()
    node = payload["semantic_graph"]["nodes"][2]
    node["fact_ids"] = [node["node_id"]]

    result = _normalize(payload)
    matching = [
        item
        for item in result["errors"]
        if item.get("code") == "node_fact_reference_unknown"
        and item.get("id") == node["node_id"]
    ]

    assert result["publishable"] is False
    assert matching == [
        {
            "code": "node_fact_reference_unknown",
            "path": "$.semantic_graph.nodes[2].fact_ids",
            "id": node["node_id"],
            "count": 1,
        }
    ]


def test_unknown_edge_endpoint_reports_only_the_invalid_field() -> None:
    payload = _payload()
    edge = payload["semantic_graph"]["edges"][0]
    edge["source_node_id"] = "N_missing"

    result = _normalize(payload)
    matching = [
        item
        for item in result["errors"]
        if item.get("code") == "edge_endpoint_unknown"
        and item.get("id") == edge["edge_id"]
    ]

    assert matching == [
        {
            "code": "edge_endpoint_unknown",
            "path": "$.semantic_graph.edges[0].source_node_id",
            "id": edge["edge_id"],
        }
    ]


def test_only_explicit_non_testable_optional_fact_can_be_dispositioned() -> None:
    accepted = _payload()
    accepted["evidence_facts"].append(
        _fact(
            "f_context",
            "运行背景说明",
            "说明文字只用于介绍运行背景",
            level="optional",
            testability="non_testable",
        )
    )
    accepted["semantic_graph"]["fact_dispositions"].append(
        {
            "fact_id": "f_context",
            "disposition": "context_only",
            "reason": "不形成可验证行为",
        }
    )
    rejected = copy.deepcopy(accepted)
    rejected["evidence_facts"][-1]["requirement_level"] = "required"

    assert _normalize(accepted)["publishable"] is True
    rejected_result = _normalize(rejected)
    assert rejected_result["publishable"] is False
    assert "required_fact_disposition_forbidden" in _error_codes(rejected_result)


@pytest.mark.parametrize("missing_reason", [None, "", "   "])
def test_fact_disposition_missing_reason_reports_exact_field(
    missing_reason,
) -> None:
    payload = _payload()
    payload["evidence_facts"].append(
        _fact(
            "f_context",
            "运行背景说明",
            "说明文字只用于介绍运行背景",
            level="optional",
            testability="non_testable",
        )
    )
    disposition = {
        "fact_id": "f_context",
        "disposition": "context_only",
    }
    if missing_reason is not None:
        disposition["reason"] = missing_reason
    payload["semantic_graph"]["fact_dispositions"].append(disposition)

    result = _normalize(payload)

    assert result["publishable"] is False
    assert {
        "code": "fact_disposition_reason_missing",
        "path": "$.semantic_graph.fact_dispositions[0].reason",
        "id": "f_context",
    } in result["errors"]
    assert "fact_disposition_schema_invalid" not in _error_codes(result)


def test_optional_testable_fact_can_be_explicitly_out_of_scope() -> None:
    payload = _payload()
    payload["evidence_facts"].append(
        _fact(
            "f_optional_scope",
            "运行背景不在本次测试范围",
            "说明文字只用于介绍运行背景",
            level="optional",
            testability="testable",
        )
    )
    payload["semantic_graph"]["fact_dispositions"].append(
        {
            "fact_id": "f_optional_scope",
            "disposition": "out_of_scope",
            "reason": "当前需求明确排除该范围",
        }
    )

    result = _normalize(payload)

    assert result["publishable"] is True


@pytest.mark.parametrize("disposition", ["context_only", "non_testable"])
def test_testable_fact_cannot_bypass_disposition_semantics(disposition: str) -> None:
    payload = _payload()
    payload["evidence_facts"].append(
        _fact(
            "f_optional_context",
            "运行背景仍可验证",
            "说明文字只用于介绍运行背景",
            level="optional",
            testability="testable",
        )
    )
    payload["semantic_graph"]["fact_dispositions"].append(
        {
            "fact_id": "f_optional_context",
            "disposition": disposition,
            "reason": "不能用错误 disposition 跳过可测试事实",
        }
    )

    result = _normalize(payload)

    assert result["publishable"] is False
    assert "testable_fact_disposition_forbidden" in _error_codes(result)


def test_scope_hierarchy_accepts_arbitrary_depth_and_rejects_cycle() -> None:
    payload = _payload()
    payload["semantic_graph"]["nodes"].extend(
        [
            _node("s_root", "scope", "任务系统", ["f_scope"], scope_status="in_scope"),
            _node("s_runtime", "scope", "运行域", ["f_scope"], scope_status="in_scope"),
        ]
    )
    payload["semantic_graph"]["edges"].extend(
        [
            _edge("e_root_runtime", "contains", "s_root", "s_runtime", ["f_scope"]),
            _edge("e_runtime_ingress", "contains", "s_runtime", "s_ingress", ["f_scope"]),
            _edge("e_runtime_process", "contains", "s_runtime", "s_process", ["f_scope"]),
        ]
    )

    accepted = _normalize(payload)
    assert accepted["publishable"] is True
    assert accepted["diagnostics"]["max_scope_depth"] == 2
    architecture = project_functional_architecture_from_graph(accepted)
    assert [item["module_key"] for item in architecture["functional_modules"]] == [
        "s_ingress",
        "s_process",
    ]

    payload["semantic_graph"]["edges"].append(
        _edge("e_cycle", "contains", "s_ingress", "s_root", ["f_scope"])
    )
    rejected = _normalize(payload)
    assert rejected["publishable"] is False
    assert "scope_hierarchy_cycle" in _error_codes(rejected)


@pytest.mark.parametrize("scope_count", [1, 3, 7])
def test_contract_accepts_variable_independent_scope_counts(scope_count: int) -> None:
    source = "\n".join(
        f"范围{index}负责处理任务{index}。" for index in range(1, scope_count + 1)
    )
    payload = {
        "evidence_facts": [
            _fact(
                f"f_{index}",
                f"范围{index}处理任务{index}",
                f"范围{index}负责处理任务{index}",
            )
            for index in range(1, scope_count + 1)
        ],
        "semantic_graph": {
            "graph_version": "requirement-semantic-graph-v1",
            "nodes": [
                _node(
                    f"s_{index}",
                    "scope",
                    f"范围{index}",
                    [f"f_{index}"],
                    scope_status="in_scope",
                )
                for index in range(1, scope_count + 1)
            ],
            "edges": [],
            "fact_dispositions": [],
        },
    }

    result = normalize_requirement_semantic_graph(
        payload,
        source_text=source,
        evidence_validator=evidence_supported,
    )

    assert result["publishable"] is True
    assert result["diagnostics"]["scope_count"] == scope_count


def test_capability_ownership_does_not_promote_carrier_or_constraint_to_scope() -> None:
    missing_owner = _payload()
    missing_owner["semantic_graph"]["edges"] = [
        edge
        for edge in missing_owner["semantic_graph"]["edges"]
        if edge["edge_id"] != "e_own_receive"
    ]
    carrier_owner = _payload()
    carrier_owner["semantic_graph"]["nodes"].append(
        _node("n_channel", "carrier", "请求通道", ["f_receive"])
    )
    carrier_owner["semantic_graph"]["edges"][0]["source_node_id"] = "n_channel"

    missing_result = _normalize(missing_owner)
    carrier_result = _normalize(carrier_owner)

    assert "capability_owner_missing" in _error_codes(missing_result)
    assert "ownership_endpoint_invalid" in _error_codes(carrier_result)


def test_scope_contains_capability_with_explicit_owner_is_canonicalized() -> None:
    payload = _payload()
    owner_edge = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_own_receive"
    )
    owner_edge["type"] = "contains"

    result = _normalize(payload)
    normalized_owner = next(
        edge
        for edge in result["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_own_receive"
    )

    assert result["publishable"] is True
    assert normalized_owner["type"] == "owns"
    assert "contains_endpoint_kind_invalid" not in _error_codes(result)
    assert "capability_owner_missing" not in _error_codes(result)
    assert result["diagnostics"]["declaration_repairs"] == [
        {
            "code": "contains_capability_canonicalized_to_owns",
            "path": "$.semantic_graph.edges[0]",
            "id": "e_own_receive",
            "field": "type",
            "from": "contains",
            "to": "owns",
        }
    ]


def test_scope_contains_capability_without_ownership_stays_invalid() -> None:
    payload = _payload()
    owner_edge = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_own_receive"
    )
    owner_edge.update({"type": "contains", "ownership_role": "none"})

    result = _normalize(payload)

    assert result["publishable"] is False
    assert "contains_endpoint_kind_invalid" in _error_codes(result)
    assert "capability_owner_missing" in _error_codes(result)
    assert result["diagnostics"]["declaration_repair_count"] == 0


def test_reversed_ownership_edge_is_canonicalized_by_endpoint_kinds() -> None:
    payload = _payload()
    owner = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_own_receive"
    )
    owner["source_node_id"], owner["target_node_id"] = (
        owner["target_node_id"],
        owner["source_node_id"],
    )

    result = _normalize(payload)
    normalized = next(
        edge
        for edge in result["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_own_receive"
    )

    assert result["publishable"] is True
    assert normalized["source_node_id"] == "s_ingress"
    assert normalized["target_node_id"] == "c_receive"
    assert any(
        item["code"] == "reversed_ownership_edge_canonicalized"
        for item in result["diagnostics"]["declaration_repairs"]
    )


def test_reversed_constraint_edge_is_canonicalized_by_endpoint_kinds() -> None:
    payload = _payload()
    payload["semantic_graph"]["nodes"].append(
        _node("limit_rule", "constraint", "执行限制", ["f_terminal"])
    )
    payload["semantic_graph"]["edges"].append(
        _edge(
            "e_limit",
            "constrained_by",
            "limit_rule",
            "c_execute",
            ["f_terminal"],
        )
    )

    result = _normalize(payload)
    normalized = next(
        edge
        for edge in result["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_limit"
    )

    assert result["publishable"] is True
    assert normalized["source_node_id"] == "c_execute"
    assert normalized["target_node_id"] == "limit_rule"
    assert any(
        item["code"] == "reversed_constraint_edge_canonicalized"
        for item in result["diagnostics"]["declaration_repairs"]
    )


def test_constraint_without_constraint_endpoint_requires_structural_recompile() -> None:
    payload = _payload()
    interaction = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_handoff"
    )
    interaction["type"] = "constrained_by"

    result = _normalize(payload)

    assert "constraint_endpoint_invalid" in _error_codes(result)
    assert result["diagnostics"]["structural_recompile_error_codes"] == [
        "constraint_endpoint_invalid"
    ]


def test_interaction_rejects_entity_endpoint_and_requires_structural_recompile() -> None:
    payload = _payload()
    payload["semantic_graph"]["nodes"].append(
        _node("request_entity", "entity", "请求实体", ["f_interaction"])
    )
    interaction = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_handoff"
    )
    interaction["target_node_id"] = "request_entity"

    result = _normalize(payload)

    assert "interaction_endpoint_kind_invalid" in _error_codes(result)
    assert result["diagnostics"]["structural_recompile_error_codes"] == [
        "interaction_endpoint_kind_invalid"
    ]


def test_edge_signature_prompt_and_runtime_share_one_registry() -> None:
    prompt = edge_signature_contract_prompt()
    endpoint_error_codes = {
        signature["endpoint_error_code"]
        for signature in EDGE_SIGNATURES.values()
    }

    assert EDGE_TYPES == frozenset(EDGE_SIGNATURES)
    assert all(f"- {edge_type}:" in prompt for edge_type in EDGE_TYPES)
    assert endpoint_error_codes <= STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES


def test_structural_recompile_errors_are_signature_errors_plus_cross_field_contracts() -> None:
    endpoint_error_codes = {
        signature["endpoint_error_code"]
        for signature in EDGE_SIGNATURES.values()
    }
    cross_field_error_codes = {
        "interaction_scope_endpoint_unresolved",
        "interaction_same_scope",
        "interaction_source_fact_unbound",
        "interaction_target_fact_unbound",
    }

    assert STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES == frozenset(
        endpoint_error_codes | cross_field_error_codes
    )


def test_interaction_requires_directional_resolvable_scope_endpoints() -> None:
    payload = _payload()
    interaction = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_handoff"
    )
    interaction["target_node_id"] = "c_receive"

    result = _normalize(payload)

    assert result["publishable"] is False
    assert "edge_self_reference" in _error_codes(result)


@pytest.mark.parametrize(
    ("node_id", "expected_code"),
    [
        ("c_receive", "interaction_source_fact_unbound"),
        ("c_execute", "interaction_target_fact_unbound"),
    ],
)
def test_interaction_fact_must_bind_both_endpoint_nodes(
    node_id: str,
    expected_code: str,
) -> None:
    payload = _payload()
    node = next(
        item
        for item in payload["semantic_graph"]["nodes"]
        if item["node_id"] == node_id
    )
    node["fact_ids"].remove("f_interaction")

    result = _normalize(payload)

    assert result["publishable"] is False
    assert expected_code in _error_codes(result)
    assert expected_code in result["diagnostics"][
        "structural_recompile_error_codes"
    ]


def _bind_handoff_to_scope_endpoints(payload: dict) -> None:
    interaction = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_handoff"
    )
    interaction.update(
        {
            "source_node_id": "s_ingress",
            "target_node_id": "s_process",
        }
    )
    for scope_id in ("s_ingress", "s_process"):
        scope = next(
            node
            for node in payload["semantic_graph"]["nodes"]
            if node["node_id"] == scope_id
        )
        if "f_interaction" not in scope["fact_ids"]:
            scope["fact_ids"].append("f_interaction")


def test_interaction_accepts_scope_endpoints_bound_by_the_relation_fact() -> None:
    payload = _payload()
    _bind_handoff_to_scope_endpoints(payload)

    result = _normalize(payload)

    assert result["publishable"] is True
    interaction = next(
        edge
        for edge in result["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_handoff"
    )
    assert interaction["source_scope_id"] == "s_ingress"
    assert interaction["target_scope_id"] == "s_process"


@pytest.mark.parametrize(
    ("scope_id", "expected_code"),
    [
        ("s_ingress", "interaction_source_fact_unbound"),
        ("s_process", "interaction_target_fact_unbound"),
    ],
)
def test_interaction_scope_endpoint_must_share_the_relation_fact(
    scope_id: str,
    expected_code: str,
) -> None:
    payload = _payload()
    _bind_handoff_to_scope_endpoints(payload)
    scope = next(
        node
        for node in payload["semantic_graph"]["nodes"]
        if node["node_id"] == scope_id
    )
    scope["fact_ids"].remove("f_interaction")

    result = _normalize(payload)

    assert result["publishable"] is False
    assert expected_code in _error_codes(result)
    assert result["diagnostics"]["structural_recompile_error_codes"] == [
        expected_code
    ]


def test_external_entry_carrier_control_flow_does_not_project_external_module() -> None:
    payload = _payload()
    external_entry = next(
        node
        for node in payload["semantic_graph"]["nodes"]
        if node["node_id"] == "t_arrival"
    )
    external_entry.update(
        {
            "kind": "carrier",
            "name": "external entry carrier",
        }
    )

    result = _normalize(payload)
    architecture = project_functional_architecture_from_graph(result)

    assert result["publishable"] is True
    assert result["semantic_graph"]["derived_critical_entry_ids"] == ["t_arrival"]
    assert [
        item["module_key"]
        for item in architecture["functional_modules"]
    ] == ["s_ingress", "s_process"]
    assert all(
        item["module_key"] != "t_arrival"
        for item in architecture["functional_modules"]
    )


def test_transferred_entity_reference_is_valid_incident_for_entity_node() -> None:
    payload = _payload()
    payload["semantic_graph"]["nodes"].append(
        _node("entity_request", "entity", "批次请求实体", ["f_interaction"])
    )
    interaction = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_handoff"
    )
    interaction["transferred_entity_node_ids"] = ["entity_request"]

    result = _normalize(payload)

    assert result["publishable"] is True
    assert "orphan_node" not in _error_codes(result)


@pytest.mark.parametrize(
    ("entity_ids", "expected_code"),
    [
        (["missing_entity"], "transferred_entity_reference_unknown"),
        (["c_receive"], "transferred_entity_kind_invalid"),
    ],
)
def test_transferred_entity_ids_must_reference_existing_entity_nodes(
    entity_ids: list[str],
    expected_code: str,
) -> None:
    payload = _payload()
    interaction = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_handoff"
    )
    interaction["transferred_entity_node_ids"] = entity_ids

    result = _normalize(payload)

    assert result["publishable"] is False
    assert expected_code in _error_codes(result)


def test_transferred_entity_ids_must_be_a_list() -> None:
    payload = _payload()
    interaction = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_handoff"
    )
    interaction["transferred_entity_node_ids"] = "entity_request"

    result = _normalize(payload)

    assert result["publishable"] is False
    assert "transferred_entity_ids_not_list" in _error_codes(result)


def test_required_edge_rejects_optional_or_ambiguous_endpoint() -> None:
    payload = _payload()
    receive_fact = next(
        fact for fact in payload["evidence_facts"] if fact["fact_id"] == "f_receive"
    )
    receive_fact.update({"requirement_level": "optional", "priority": "p2"})
    receive_node = next(
        node
        for node in payload["semantic_graph"]["nodes"]
        if node["node_id"] == "c_receive"
    )
    receive_node["boundary_status"] = "ambiguous"

    result = _normalize(payload)

    assert result["publishable"] is False
    assert {
        "required_edge_endpoint_not_required",
        "required_edge_endpoint_boundary_unresolved",
    }.issubset(_error_codes(result))


def test_required_edge_rejects_out_of_scope_scope_endpoint() -> None:
    payload = _payload()
    ingress_scope = next(
        node
        for node in payload["semantic_graph"]["nodes"]
        if node["node_id"] == "s_ingress"
    )
    ingress_scope["scope_status"] = "out_of_scope"
    trigger_edge = next(
        edge
        for edge in payload["semantic_graph"]["edges"]
        if edge["edge_id"] == "e_trigger_receive"
    )
    trigger_edge["source_node_id"] = "s_ingress"

    result = _normalize(payload)

    assert result["publishable"] is False
    assert "required_edge_scope_not_in_scope" in _error_codes(result)


def test_required_shortcut_edge_does_not_override_explicit_primary_flow() -> None:
    payload = _payload()
    payload["evidence_facts"].append(
        _fact(
            "f_shortcut",
            "接收完成后可直接形成完成状态",
            "接收完成后也可直接形成完成状态",
        )
    )
    payload["semantic_graph"]["edges"].append(
        _edge(
            "e_receive_complete_shortcut",
            "transitions",
            "c_receive",
            "st_complete",
            ["f_shortcut"],
        )
    )

    result = _normalize(payload)
    graph = result["semantic_graph"]
    shortcut = next(
        edge
        for edge in graph["edges"]
        if edge["edge_id"] == "e_receive_complete_shortcut"
    )

    assert result["publishable"] is True
    assert result["diagnostics"]["workflow_topology_status"] == "linearizable"
    assert _workflow_topology_errors(result) == []
    assert graph["primary_flow"] == {
        "node_ids": [
            "t_arrival",
            "c_receive",
            "c_execute",
            "st_complete",
        ],
        "edge_ids": [
            "e_trigger_receive",
            "e_receive_execute",
            "e_execute_complete",
        ],
    }
    assert shortcut["required"] is True
    assert shortcut["source_node_id"] == "c_receive"
    assert shortcut["target_node_id"] == "st_complete"
    assert result["diagnostics"]["required_control_linear_component_count"] == 1


def test_node_workflow_roles_are_derived_only_from_primary_flow() -> None:
    payload = _payload()
    declared_roles = {
        "s_ingress": "terminal",
        "s_process": "entry",
        "t_arrival": "terminal",
        "c_receive": "entry",
        "c_execute": "none",
        "st_complete": "intermediate",
    }
    for node in payload["semantic_graph"]["nodes"]:
        node["workflow_role"] = declared_roles[node["node_id"]]

    result = _normalize(payload)
    roles = {
        str(node["node_id"]): str(node["workflow_role"])
        for node in result["semantic_graph"]["nodes"]
    }

    assert result["publishable"] is True
    assert roles == {
        "c_execute": "intermediate",
        "c_receive": "intermediate",
        "s_ingress": "none",
        "s_process": "none",
        "st_complete": "terminal",
        "t_arrival": "entry",
    }
    assert result["semantic_graph"]["derived_critical_entry_ids"] == ["t_arrival"]
    assert result["diagnostics"]["workflow_topology_status"] == "linearizable"


@pytest.mark.parametrize(
    ("declaration_mode", "expected_primary_flow_status"),
    [
        ("explicit_empty", "independent_only"),
        ("missing", "not_declared"),
    ],
)
def test_empty_or_missing_primary_flow_is_independent_only(
    declaration_mode: str,
    expected_primary_flow_status: str,
) -> None:
    payload = _payload()
    for node in payload["semantic_graph"]["nodes"]:
        node["workflow_role"] = "entry"
    if declaration_mode == "explicit_empty":
        payload["semantic_graph"]["primary_flow"] = {
            "node_ids": [],
            "edge_ids": [],
        }
    else:
        payload["semantic_graph"].pop("primary_flow")

    result = _normalize(payload)

    assert result["publishable"] is True
    assert result["semantic_graph"]["primary_flow"] == {
        "node_ids": [],
        "edge_ids": [],
    }
    assert result["semantic_graph"]["derived_critical_entry_ids"] == []
    assert result["diagnostics"]["workflow_topology_status"] == "independent_only"
    assert result["diagnostics"]["primary_flow_status"] == (
        expected_primary_flow_status
    )
    assert {
        str(node["workflow_role"])
        for node in result["semantic_graph"]["nodes"]
    } == {"none"}


@pytest.mark.parametrize(
    ("invalid_kind", "expected_code"),
    [
        ("not_object", "primary_flow_not_object"),
        ("unknown_node", "primary_flow_node_unknown"),
        ("unknown_edge", "primary_flow_edge_unknown"),
        ("non_required_edge", "primary_flow_edge_not_required_control"),
        ("wrong_order", "primary_flow_not_simple_path"),
    ],
)
def test_invalid_primary_flow_is_not_linearizable(
    invalid_kind: str,
    expected_code: str,
) -> None:
    payload = _payload()
    primary_flow = payload["semantic_graph"]["primary_flow"]
    if invalid_kind == "not_object":
        payload["semantic_graph"]["primary_flow"] = "invalid"
    elif invalid_kind == "unknown_node":
        primary_flow["node_ids"][1] = "n_missing"
    elif invalid_kind == "unknown_edge":
        primary_flow["edge_ids"][1] = "e_missing"
    elif invalid_kind == "non_required_edge":
        transition_fact = next(
            fact
            for fact in payload["evidence_facts"]
            if fact["fact_id"] == "f_transition"
        )
        transition_fact.update(
            {
                "requirement_level": "optional",
                "priority": "p2",
            }
        )
    elif invalid_kind == "wrong_order":
        primary_flow["edge_ids"][0], primary_flow["edge_ids"][1] = (
            primary_flow["edge_ids"][1],
            primary_flow["edge_ids"][0],
        )

    result = _normalize(payload)

    assert result["diagnostics"]["workflow_topology_status"] == (
        "not_linearizable"
    )
    assert result["diagnostics"]["primary_flow_status"] == "invalid"
    assert expected_code in _workflow_topology_codes(result)
    assert result["semantic_graph"]["primary_flow"] == {
        "node_ids": [],
        "edge_ids": [],
    }
    assert result["semantic_graph"]["derived_critical_entry_ids"] == []
    assert {
        str(node["workflow_role"])
        for node in result["semantic_graph"]["nodes"]
    } == {"none"}


def test_primary_flow_diagnostics_survive_semantic_contract_normalization() -> None:
    payload = _payload()
    primary_flow = payload["semantic_graph"]["primary_flow"]
    primary_flow["edge_ids"][0], primary_flow["edge_ids"][1] = (
        primary_flow["edge_ids"][1],
        primary_flow["edge_ids"][0],
    )

    contract = normalize_requirement_semantic_contract(
        payload,
        requirement_text=SOURCE,
        workflow_blueprints=[],
    )
    validation = contract["semantic_graph_validation"]

    assert validation["publishable"] is True
    assert validation["diagnostics"]["workflow_topology_status"] == (
        "not_linearizable"
    )
    assert validation["diagnostics"]["primary_flow_status"] == "invalid"
    assert "primary_flow_not_simple_path" in validation["diagnostics"][
        "workflow_topology_error_codes"
    ]
    assert validation["diagnostics"]["workflow_topology_errors"]


def test_terminal_to_entry_back_edge_does_not_create_false_cycle() -> None:
    payload = _payload()
    payload["semantic_graph"]["edges"].append(
        _edge(
            "e_restart",
            "transitions",
            "st_complete",
            "t_arrival",
            ["f_terminal"],
        )
    )

    result = _normalize(payload)

    assert result["publishable"] is True
    assert _error_codes(result) == set()
    assert result["semantic_graph"]["derived_critical_entry_ids"] == ["t_arrival"]
    assert result["diagnostics"]["workflow_topology_status"] == "linearizable"
    assert "required_component_cycle" not in _workflow_topology_codes(result)
    assert result["diagnostics"]["required_control_cycle_component_count"] == 0
    assert any(
        edge["edge_id"] == "e_restart"
        for edge in result["semantic_graph"]["edges"]
    )


def test_required_p0_unresolved_boundary_blocks_publish() -> None:
    payload = _payload()
    entry = next(
        node
        for node in payload["semantic_graph"]["nodes"]
        if node["node_id"] == "t_arrival"
    )
    entry["boundary_status"] = "ambiguous"

    result = _normalize(payload)

    assert result["publishable"] is False
    assert "required_node_boundary_unresolved" in _error_codes(result)


def test_graph_fingerprint_is_order_independent_without_reordering_facts() -> None:
    first = _payload()
    second = copy.deepcopy(first)
    expected_primary_flow = copy.deepcopy(first["semantic_graph"]["primary_flow"])
    second["evidence_facts"].reverse()
    second["semantic_graph"]["nodes"].reverse()
    second["semantic_graph"]["edges"].reverse()

    first_result = _normalize(first)
    second_result = _normalize(second)

    assert first_result["publishable"] is True
    assert second_result["publishable"] is True
    assert first_result["semantic_graph"]["primary_flow"] == expected_primary_flow
    assert second_result["semantic_graph"]["primary_flow"] == expected_primary_flow
    assert first_result["topology_fingerprint"] == second_result["topology_fingerprint"]
    assert [item["fact_id"] for item in first_result["evidence_facts"]] == [
        item["fact_id"] for item in first["evidence_facts"]
    ]
    assert [item["fact_id"] for item in second_result["evidence_facts"]] == [
        item["fact_id"] for item in second["evidence_facts"]
    ]
    assert first_result["semantic_graph"] == second_result["semantic_graph"]


def test_workflow_adapter_resolves_only_graph_scope_and_fact_references() -> None:
    normalized = _normalize(_payload())
    workflows = [
        {
            "workflow_id": "batch_flow",
            "steps": [
                {
                    "id": "receive",
                    "fact_ids": ["f_receive"],
                    "evidence": ["旧证据不能透传"],
                    "module_candidates": [
                        {
                            "module_key": "legacy_module",
                            "role": "primary",
                            "evidence": ["旧模块不能透传"],
                            "confidence": 1.0,
                        }
                    ],
                    "interaction_ids": ["legacy_interaction"],
                    "scope_candidates": [
                        {
                            "scope_id": "s_ingress",
                            "role": "primary",
                            "fact_ids": ["f_receive"],
                            "evidence": ["旧 scope candidate 证据"],
                            "confidence": 0.95,
                        }
                    ],
                    "relation_ids": ["e_trigger_receive", "e_handoff"],
                    "required_states": [],
                    "produced_states": [],
                }
            ],
        }
    ]

    adapted = adapt_workflows_from_semantic_graph(workflows, normalized)
    step = adapted[0]["steps"][0]

    assert step["module_candidates"] == [
        {
            "module_key": "s_ingress",
            "module_name": "接入域",
            "role": "primary",
            "confidence": 0.95,
            "evidence": ["接入域接收批次请求"],
        }
    ]
    assert step["interaction_ids"] == ["e_handoff"]
    assert step["graph_relation_ids"] == ["e_handoff", "e_trigger_receive"]
    assert step["relation_ids"] == ["e_handoff", "e_trigger_receive"]
    assert step["evidence"] == ["接入域接收批次请求"]
    assert step["scope_candidates"][0]["evidence"] == ["接入域接收批次请求"]


def test_workflow_adapter_does_not_infer_scope_or_keep_legacy_fields() -> None:
    normalized = _normalize(_payload())
    workflows = [
        {
            "workflow_id": "strict_flow",
            "evidence": ["旧工作流证据"],
            "steps": [
                {
                    "id": "strict_step",
                    "scope_id": "s_ingress",
                    "fact_ids": ["f_receive"],
                    "evidence": ["旧步骤证据"],
                    "module_candidates": [
                        {
                            "module_key": "s_ingress",
                            "role": "primary",
                            "evidence": ["旧模块证据"],
                            "confidence": 1.0,
                        }
                    ],
                    "interaction_ids": ["e_handoff"],
                    "required_states": [
                        {
                            "entity": "request",
                            "state": "ready",
                            "fact_ids": ["f_receive"],
                            "evidence": ["旧状态证据"],
                        }
                    ],
                    "produced_states": [],
                }
            ],
        }
    ]

    adapted = adapt_workflows_from_semantic_graph(workflows, normalized)
    workflow = adapted[0]
    step = workflow["steps"][0]

    assert "evidence" not in workflow
    assert "scope_id" not in step
    assert step["module_candidates"] == []
    assert step["interaction_ids"] == []
    assert step["evidence"] == ["接入域接收批次请求"]
    assert step["required_states"][0]["fact_ids"] == ["f_receive"]
    assert step["required_states"][0]["evidence"] == ["接入域接收批次请求"]


def _typed_state_workflow(fact_ids: object = None) -> list[dict]:
    state = {
        "entity": "batch_request",
        "state": "received",
        "evidence": ["接入域接收批次请求"],
    }
    if fact_ids is not None:
        state["fact_ids"] = fact_ids
    return [
        {
            "workflow_id": "batch_flow",
            "steps": [
                {
                    "id": "receive",
                    "fact_ids": ["f_receive"],
                    "scope_candidates": [
                        {
                            "scope_id": "s_ingress",
                            "fact_ids": ["f_receive"],
                        }
                    ],
                    "relation_ids": ["e_trigger_receive"],
                    "required_states": [state],
                    "produced_states": [],
                }
            ],
        }
    ]


def test_graph_typed_state_fact_identity_accepts_current_step_subgraph() -> None:
    normalized = _normalize(_payload())

    rejections = graph_typed_state_identity_rejections(
        _typed_state_workflow(["f_receive", "f_trigger"]),
        normalized,
    )

    assert rejections == []


def test_graph_typed_state_does_not_use_evidence_as_missing_fact_identity() -> None:
    normalized = _normalize(_payload())

    rejections = graph_typed_state_identity_rejections(
        _typed_state_workflow(),
        normalized,
    )

    assert rejections == [
        {
            "workflow_index": 1,
            "step_index": 1,
            "collection": "required_states",
            "state_index": 1,
            "reason": "graph_typed_state_fact_ids_invalid",
            "field_path": (
                "$.workflow_blueprints[0].steps[0]"
                ".required_states[0].fact_ids"
            ),
        }
    ]


def test_graph_typed_state_rejects_known_but_unrelated_fact_identity() -> None:
    normalized = _normalize(_payload())

    rejections = graph_typed_state_identity_rejections(
        _typed_state_workflow(["f_execute"]),
        normalized,
    )

    assert rejections[0]["reason"] == "graph_typed_state_fact_binding_invalid"
    assert rejections[0]["graph_node_id"] == "c_receive"
    assert rejections[0]["unrelated_fact_ids"] == ["f_execute"]
    assert "f_receive" in rejections[0]["expected_fact_ids"]


def test_graph_typed_state_rejects_unknown_fact_identity() -> None:
    normalized = _normalize(_payload())

    rejections = graph_typed_state_identity_rejections(
        _typed_state_workflow(["f_missing"]),
        normalized,
    )

    assert rejections[0]["reason"] == "graph_typed_state_fact_ids_unknown"
    assert rejections[0]["unknown_fact_ids"] == ["f_missing"]


def test_project_profile_does_not_run_legacy_structure_candidates_for_graph() -> None:
    payload = _payload()
    contract = normalize_requirement_semantic_contract(
        payload,
        requirement_text=SOURCE,
        workflow_blueprints=[],
    )

    profile = build_project_profile(
        requirement_text=SOURCE,
        semantic_contract=contract,
    )

    assert profile["document_structure_candidates"] == {}
    assert profile["functional_architecture"]["source"] == (
        "semantic_graph_projection"
    )


def test_project_profile_reuses_canonical_business_evidence_view() -> None:
    full_requirement = SOURCE.replace(
        "接入域接收批次请求。",
        "接入域接收\n[图片]\n批次请求。",
    )
    evidence_view, _ = build_requirement_business_evidence_view(full_requirement)
    repeated_view, _ = build_requirement_business_evidence_view(evidence_view)
    contract = normalize_requirement_semantic_contract(
        _payload(),
        requirement_text=evidence_view,
        workflow_blueprints=[],
    )

    assert repeated_view == evidence_view
    assert contract["semantic_graph_validation"]["publishable"] is True

    profile = build_project_profile(
        requirement_text=full_requirement,
        semantic_contract=contract,
    )

    assert profile["requirement_semantic_contract"]["semantic_graph_validation"][
        "publishable"
    ] is True
    assert profile["module_order"] == ["接入域", "处理域"]
    assert profile["functional_architecture"]["module_interactions"][0][
        "interaction_id"
    ]

    context = build_structured_prompt_context(
        requirement=full_requirement,
        architecture_requirement=full_requirement,
        feedback_control_state={
            "source_meta": {"requirement_semantic_contract": contract}
        },
    )
    assert [item["module_name"] for item in context["module_catalog"]] == [
        "接入域",
        "处理域",
    ]
    assert context["module_interactions"]


def test_invalid_graph_does_not_enter_project_profile() -> None:
    payload = _payload()
    payload["semantic_graph"]["graph_version"] = "unsupported-version"
    payload["status"] = "applied_with_workflows"
    payload["functional_architecture"] = {
        "functional_modules": [
            {
                "module_key": "poisoned_scope",
                "module_name": "不应透传的旧模块",
                "scope_status": "in_scope",
                "evidence": [SOURCE],
                "confidence": 1.0,
            }
        ],
        "module_interactions": [],
    }
    payload["workflow_blueprints"] = [
        {
            "workflow_id": "poisoned_flow",
            "name": "不应透传的旧工作流",
            "steps": [{"id": "poisoned_step"}],
        }
    ]
    contract = normalize_requirement_semantic_contract(
        payload,
        requirement_text=SOURCE,
        workflow_blueprints=payload.get("workflow_blueprints") or [],
    )

    normalized_profile = build_project_profile(
        requirement_text=SOURCE,
        semantic_contract=contract,
    )
    raw_profile = build_project_profile(
        requirement_text=SOURCE,
        semantic_contract=payload,
    )

    assert contract["semantic_graph_validation"]["publishable"] is False
    for profile in (normalized_profile, raw_profile):
        assert profile["requirement_semantic_contract"]["status"] == (
            "semantic_graph_invalid"
        )
        assert profile["requirement_semantic_contract"]["workflow_blueprints"] == []
        assert profile["module_order"] == []
        assert profile["flow_outline"]["flow_order"] == []
        assert profile["functional_architecture"]["functional_modules"] == []
        assert profile["functional_architecture"]["module_interactions"] == []
