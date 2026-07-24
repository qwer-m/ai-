from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from modules.test_generation_components.control.model_envelope_call import (
    strict_json_output_contract_prompt,
)
from modules.test_generation_components.control.requirement_fact_ledger import (
    fingerprint_source_evidence_catalog,
    normalize_requirement_fact_ledger,
)
from modules.test_generation_components.control.requirement_graph_stage_contract import (
    RequirementGraphStageContractError,
    assemble_requirement_graph_stage_response,
    build_requirement_graph_stage_prompt,
    build_requirement_graph_stage_user_input,
    validate_requirement_graph_stage_projection,
)
from modules.test_generation_components.control.requirement_scope_ledger import (
    fingerprint_requirement_scope_ledger,
    normalize_requirement_scope_ledger,
    project_requirement_scope_ledger,
)
from modules.test_generation_components.control.requirement_semantic_graph import (
    normalize_requirement_semantic_graph,
)


CATALOG = [
    {
        "ref": "EV_111111111111",
        "quote": "当前需求包含一个父级职责和一个可独立导航的子职责。",
    },
    {
        "ref": "EV_222222222222",
        "quote": "子职责具有独立入口并拥有自己的内容。",
    },
    {
        "ref": "EV_333333333333",
        "quote": "用户可以在子职责中查看与自己相关的记录。",
    },
]
CATALOG_FINGERPRINT = fingerprint_source_evidence_catalog(CATALOG)


def _fact(
    fact_id: str,
    statement: str,
    evidence_ref: str,
    *,
    fact_kind: str = "action",
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "fact_kind": fact_kind,
        "statement": statement,
        "requirement_level": "required",
        "priority": "unspecified",
        "testability": "testable",
        "evidence": [evidence_ref],
        "anchor_evidence_ref": evidence_ref,
        "confidence": 0.95,
    }


def _normalized_ledger() -> dict[str, Any]:
    facts = [
        _fact(
            "F_PARENT_MEMBERSHIP",
            "当前需求包含父级职责和子职责。",
            "EV_111111111111",
            fact_kind="constraint",
        ),
        _fact(
            "F_CHILD_SUPPORT",
            "子职责具有独立入口并拥有自己的内容。",
            "EV_222222222222",
            fact_kind="constraint",
        ),
        _fact(
            "F_CHILD_BEHAVIOR",
            "用户可以在子职责中查看与自己相关的记录。",
            "EV_333333333333",
        ),
    ]
    fact_ledger = normalize_requirement_fact_ledger(
        {
            "evidence_facts": facts,
            "source_evidence_dispositions": [
                {
                    "evidence_ref": item["ref"],
                    "disposition": "fact_backed",
                }
                for item in CATALOG
            ],
        },
        source_evidence_catalog=CATALOG,
        source_catalog_fingerprint=CATALOG_FINGERPRINT,
    )
    assert fact_ledger["valid"] is True, fact_ledger["errors"]
    payload = {
        "boundaries": [
            {
                "boundary_id": "S_PARENT",
                "label": "父级职责",
                "decision": "in_scope_parent",
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": [
                    {
                        "signal": "member_enumeration",
                        "fact_ids": ["F_PARENT_MEMBERSHIP"],
                    }
                ],
            },
            {
                "boundary_id": "S_CHILD",
                "label": "子职责",
                "decision": "in_scope_leaf",
                "parent_boundary_id": "S_PARENT",
                "membership_relation_ids": [],
                "membership_fact_ids": ["F_PARENT_MEMBERSHIP"],
                "support": [
                    {
                        "signal": "navigable_partition",
                        "fact_ids": ["F_CHILD_SUPPORT"],
                    }
                ],
            },
        ],
        "fact_bindings": [
            {
                "fact_id": "F_PARENT_MEMBERSHIP",
                "scope_ids": ["S_PARENT"],
                "role": "owned_requirement",
            },
            {
                "fact_id": "F_CHILD_SUPPORT",
                "scope_ids": ["S_CHILD"],
                "role": "owned_requirement",
            },
            {
                "fact_id": "F_CHILD_BEHAVIOR",
                "scope_ids": ["S_CHILD"],
                "role": "owned_requirement",
            },
        ],
    }
    normalized = normalize_requirement_scope_ledger(
        payload,
        normalized_fact_ledger=fact_ledger,
        source_evidence_catalog=CATALOG,
    )
    assert normalized["valid"] is True, normalized["errors"]
    assert normalized["ledger_version"] == "requirement-scope-ledger-v3"
    assert normalized["source_outline_fingerprint"]
    assert all(
        "membership_relation_ids" in boundary
        for boundary in normalized["boundaries"]
    )
    return normalized


def _semantic_graph() -> dict[str, Any]:
    return {
        "graph_version": "requirement-semantic-graph-v1",
        "nodes": [
            {
                "node_id": "S_PARENT",
                "kind": "scope",
                "name": "父级职责",
                "aliases": [],
                "scope_status": "in_scope",
                "boundary_status": "resolved",
                "fact_ids": ["F_PARENT_MEMBERSHIP"],
                "confidence": 0.95,
            },
            {
                "node_id": "S_CHILD",
                "kind": "scope",
                "name": "子职责",
                "aliases": [],
                "scope_status": "in_scope",
                "boundary_status": "resolved",
                "fact_ids": [
                    "F_PARENT_MEMBERSHIP",
                    "F_CHILD_SUPPORT",
                    "F_CHILD_BEHAVIOR",
                ],
                "confidence": 0.95,
            },
            {
                "node_id": "C_VIEW",
                "kind": "capability",
                "name": "查看相关记录",
                "aliases": [],
                "scope_status": "",
                "boundary_status": "resolved",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
                "confidence": 0.94,
            },
        ],
        "edges": [
            {
                "edge_id": "E_CONTAINS",
                "type": "contains",
                "source_node_id": "S_PARENT",
                "target_node_id": "S_CHILD",
                "fact_ids": ["F_PARENT_MEMBERSHIP"],
                "ownership_role": "none",
                "trigger": "",
                "result_state": "",
                "transferred_entity_node_ids": [],
                "confidence": 0.95,
            },
            {
                "edge_id": "E_OWNS",
                "type": "owns",
                "source_node_id": "S_CHILD",
                "target_node_id": "C_VIEW",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
                "ownership_role": "primary",
                "trigger": "",
                "result_state": "",
                "transferred_entity_node_ids": [],
                "confidence": 0.95,
            },
        ],
        "primary_flow": {"node_ids": [], "edge_ids": []},
        "fact_dispositions": [],
    }


def _stage_response() -> dict[str, Any]:
    return {
        "confidence": 0.93,
        "semantic_graph": _semantic_graph(),
        "workflow_blueprints": [],
    }


def _normalize_assembled_graph(contract: dict[str, Any]) -> dict[str, Any]:
    known_quotes = {item["quote"] for item in CATALOG}
    return normalize_requirement_semantic_graph(
        contract,
        source_text="",
        evidence_validator=lambda evidence, _source: bool(evidence)
        and all(item in known_quotes for item in evidence),
    )


def test_prompt_declares_graph_only_response_and_code_owned_version() -> None:
    prompt = build_requirement_graph_stage_prompt()

    assert prompt.count(strict_json_output_contract_prompt()) == 1
    assert (
        'RESPONSE := {"confidence":<NUMBER>,"semantic_graph":'
        in prompt
    )
    assert '"semantic_contract_version"' not in prompt
    assert "Code injects the semantic contract version and frozen facts" in prompt
    assert "Never output or nest `evidence_facts`" in prompt
    assert "source_evidence_catalog" not in prompt
    assert "Create exactly one in-scope scope node" in prompt
    assert "`repair_targets` are the complete mutation permission set" in prompt
    assert "`forbidden_topology_changes` remain prohibited" in prompt
    assert "Use no fixed number, names, document type" in prompt


def test_initial_input_contains_only_frozen_ledger_view_not_source_catalog() -> None:
    ledger = _normalized_ledger()

    raw_input = build_requirement_graph_stage_user_input(ledger)
    payload = json.loads(raw_input)

    assert payload["compilation_mode"] == "initial"
    assert payload["compilation_policy"] == "fresh_compile"
    assert payload["retry_context"] is None
    assert set(payload["frozen_context"]) == {
        "ledger_fingerprint",
        "evidence_facts",
        "ledger_projection",
    }
    assert payload["frozen_context"]["ledger_fingerprint"] == ledger[
        "fingerprint"
    ]
    assert payload["frozen_context"]["evidence_facts"] == ledger[
        "evidence_facts"
    ]
    assert payload["frozen_context"][
        "ledger_projection"
    ] == project_requirement_scope_ledger(ledger)
    assert "source_evidence_catalog" not in raw_input
    assert '"boundaries"' not in raw_input


def test_targeted_retry_carries_only_previous_graph_and_workflows() -> None:
    previous_contract = {
        "semantic_contract_version": "requirement-semantic-v2",
        "confidence": 0.71,
        "evidence_facts": copy.deepcopy(
            _normalized_ledger()["evidence_facts"]
        ),
        "semantic_graph": _semantic_graph(),
        "workflow_blueprints": [],
    }

    raw_input = build_requirement_graph_stage_user_input(
        _normalized_ledger(),
        attempt=2,
        compilation_mode="targeted_repair",
        retry_feedback=[{"code": "scope_contains_mismatch", "id": "S_CHILD"}],
        previous_candidate=previous_contract,
        repair_targets=[
            {
                "code": "scope_contains_mismatch",
                "path": "$.semantic_graph.edges[*]",
                "operation": "repair_subtree",
                "match": {"edge_id": ["E_CONTAINS"]},
            }
        ],
        forbidden_topology_changes=[
            {
                "path": "$.semantic_graph.nodes",
                "change": "add_or_remove_item",
            }
        ],
    )
    payload = json.loads(raw_input)
    previous = payload["retry_context"]["previous_candidate"]

    assert payload["compilation_policy"] == "targeted_repair"
    assert set(previous) == {"semantic_graph", "workflow_blueprints"}
    assert previous["semantic_graph"] == previous_contract["semantic_graph"]
    assert "evidence_facts" not in previous
    assert "confidence" not in previous
    assert "semantic_contract_version" not in previous
    assert payload["retry_context"]["repair_targets"] == [
        {
            "code": "scope_contains_mismatch",
            "path": "$.semantic_graph.edges[*]",
            "operation": "repair_subtree",
            "match": {"edge_id": ["E_CONTAINS"]},
        }
    ]
    assert payload["retry_context"]["forbidden_topology_changes"] == [
        {
            "path": "$.semantic_graph.nodes",
            "change": "add_or_remove_item",
        }
    ]


def test_independent_recompile_never_carries_previous_candidate() -> None:
    raw_input = build_requirement_graph_stage_user_input(
        _normalized_ledger(),
        attempt=2,
        compilation_mode="independent_recompile",
        recompile_reason_codes=["active_scope_id_mismatch", "S_CHILD"],
    )
    payload = json.loads(raw_input)

    assert payload["compilation_policy"] == "fresh_compile"
    assert payload["retry_context"] is None
    assert payload["recompile_reason_codes"] == [
        "active_scope_id_mismatch"
    ]


def test_independent_recompile_rejects_supplied_previous_candidate() -> None:
    with pytest.raises(RequirementGraphStageContractError) as caught:
        build_requirement_graph_stage_user_input(
            _normalized_ledger(),
            attempt=2,
            compilation_mode="independent_recompile",
            previous_candidate=_stage_response(),
        )

    assert caught.value.code == "graph_stage_previous_candidate_forbidden"


def test_independent_recompile_rejects_old_validation_feedback() -> None:
    with pytest.raises(RequirementGraphStageContractError) as caught:
        build_requirement_graph_stage_user_input(
            _normalized_ledger(),
            attempt=2,
            compilation_mode="independent_recompile",
            retry_feedback=[
                {"code": "active_scope_id_mismatch", "id": "S_CHILD"}
            ],
            recompile_reason_codes=["active_scope_id_mismatch"],
        )

    assert caught.value.code == "graph_stage_fresh_retry_feedback_forbidden"


def test_retry_feedback_cannot_smuggle_old_graph_payload() -> None:
    with pytest.raises(RequirementGraphStageContractError) as caught:
        build_requirement_graph_stage_user_input(
            _normalized_ledger(),
            attempt=2,
            compilation_mode="targeted_repair",
            retry_feedback=[{"semantic_graph": _semantic_graph()}],
            previous_candidate=_stage_response(),
            repair_targets=[],
            forbidden_topology_changes=[],
        )

    assert (
        caught.value.code
        == "graph_stage_retry_feedback_payload_forbidden"
    )


def test_assembly_injects_version_and_exact_frozen_facts() -> None:
    ledger = _normalized_ledger()
    response = _stage_response()

    assembled = assemble_requirement_graph_stage_response(
        response,
        normalized_scope_ledger=ledger,
    )

    assert set(assembled) == {
        "semantic_contract_version",
        "confidence",
        "evidence_facts",
        "semantic_graph",
        "workflow_blueprints",
    }
    assert assembled["semantic_contract_version"] == "requirement-semantic-v2"
    assert assembled["evidence_facts"] == ledger["evidence_facts"]
    assert assembled["semantic_graph"] == response["semantic_graph"]
    assert assembled["workflow_blueprints"] == []

    response["semantic_graph"]["nodes"][0]["name"] = "响应外部修改"
    ledger["evidence_facts"][0]["statement"] = "冻结后外部修改"
    assert assembled["semantic_graph"]["nodes"][0]["name"] == "父级职责"
    assert assembled["evidence_facts"][0]["statement"] != "冻结后外部修改"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda response: response.update({"evidence_facts": []}),
        lambda response: response["semantic_graph"]["nodes"][0].update(
            {"scope_ledger": {}}
        ),
        lambda response: response["workflow_blueprints"].append(
            {"fact_bindings": {}}
        ),
    ],
    ids=["root_facts", "nested_ledger", "nested_bindings"],
)
def test_assembly_rejects_echoed_frozen_fields(mutation: Any) -> None:
    response = _stage_response()
    mutation(response)

    with pytest.raises(RequirementGraphStageContractError) as caught:
        assemble_requirement_graph_stage_response(
            response,
            normalized_scope_ledger=_normalized_ledger(),
        )

    assert caught.value.code == "graph_stage_frozen_field_echoed"


def test_assembly_rejects_model_owned_semantic_version() -> None:
    response = _stage_response()
    response["semantic_contract_version"] = "requirement-semantic-v2"

    with pytest.raises(RequirementGraphStageContractError) as caught:
        assemble_requirement_graph_stage_response(
            response,
            normalized_scope_ledger=_normalized_ledger(),
        )

    assert caught.value.code == "graph_stage_response_field_unknown"
    assert caught.value.details == {"fields": ["semantic_contract_version"]}


@pytest.mark.parametrize(
    ("mutation", "expected_path", "expected_field"),
    [
        (
            lambda response: response["semantic_graph"]["nodes"][0].update(
                {"module_name": "旧模块字段"}
            ),
            "$.semantic_graph.nodes[0].module_name",
            "module_name",
        ),
        (
            lambda response: response["workflow_blueprints"].append(
                {
                    "workflow_id": "WF_1",
                    "name": "工作流",
                    "primary": True,
                    "confidence": 0.9,
                    "initial_state": "ready",
                    "required_stage_ids": ["STEP_1"],
                    "terminal_states": ["done"],
                    "fact_ids": ["F_CHILD_BEHAVIOR"],
                    "steps": [
                        {
                            "id": "STEP_1",
                            "evidence": ["不允许由 B 回传原文证据"],
                        }
                    ],
                }
            ),
            "$.workflow_blueprints[0].steps[0].evidence",
            "evidence",
        ),
    ],
    ids=["graph_node_legacy_field", "workflow_step_raw_evidence"],
)
def test_assembly_rejects_unknown_nested_response_fields(
    mutation: Any,
    expected_path: str,
    expected_field: str,
) -> None:
    response = _stage_response()
    mutation(response)

    with pytest.raises(RequirementGraphStageContractError) as caught:
        assemble_requirement_graph_stage_response(
            response,
            normalized_scope_ledger=_normalized_ledger(),
        )

    assert caught.value.code == "graph_stage_response_nested_field_unknown"
    assert caught.value.path == expected_path
    assert caught.value.details == {"field": expected_field}


def test_projection_gate_rejects_scope_drift_only_after_graph_normalization() -> None:
    response = _stage_response()
    response["semantic_graph"]["nodes"][1]["node_id"] = "S_RENAMED"

    assembled = assemble_requirement_graph_stage_response(
        response,
        normalized_scope_ledger=_normalized_ledger(),
    )
    normalized_graph = _normalize_assembled_graph(assembled)
    projection_result = validate_requirement_graph_stage_projection(
        normalized_graph,
        normalized_scope_ledger=_normalized_ledger(),
    )

    assert projection_result["valid"] is False
    assert "active_scope_id_mismatch" in projection_result["error_codes"]


def test_projection_gate_runs_after_reversed_ownership_is_canonicalized() -> None:
    response = _stage_response()
    ownership = response["semantic_graph"]["edges"][1]
    ownership["source_node_id"] = "C_VIEW"
    ownership["target_node_id"] = "S_CHILD"

    assembled = assemble_requirement_graph_stage_response(
        response,
        normalized_scope_ledger=_normalized_ledger(),
    )
    normalized_graph = _normalize_assembled_graph(assembled)
    projection_result = validate_requirement_graph_stage_projection(
        normalized_graph,
        normalized_scope_ledger=_normalized_ledger(),
    )

    assert normalized_graph["publishable"] is True
    assert normalized_graph["semantic_graph"]["edges"][1]["type"] == "owns"
    normalized_ownership = next(
        item
        for item in normalized_graph["semantic_graph"]["edges"]
        if item["edge_id"] == "E_OWNS"
    )
    assert normalized_ownership["source_node_id"] == "S_CHILD"
    assert normalized_ownership["target_node_id"] == "C_VIEW"
    assert projection_result == {"valid": True, "errors": [], "error_codes": []}


def test_input_and_assembly_reject_mutated_frozen_ledger() -> None:
    ledger = _normalized_ledger()
    ledger["boundaries"][1]["label"] = "未重新签名的修改"

    with pytest.raises(RequirementGraphStageContractError) as input_error:
        build_requirement_graph_stage_user_input(ledger)
    with pytest.raises(RequirementGraphStageContractError) as assembly_error:
        assemble_requirement_graph_stage_response(
            _stage_response(),
            normalized_scope_ledger=ledger,
        )

    assert input_error.value.code == "scope_ledger_fingerprint_mismatch"
    assert assembly_error.value.code == "scope_ledger_fingerprint_mismatch"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda ledger: ledger["evidence_facts"][0].update(
            {"model_override": "INJECTED_AFTER_SCOPE_FREEZE"}
        ),
        lambda ledger: ledger["boundaries"][0].update(
            {"model_override": "INJECTED_AFTER_SCOPE_FREEZE"}
        ),
        lambda ledger: ledger["fact_bindings"][0].update(
            {"model_override": "INJECTED_AFTER_SCOPE_FREEZE"}
        ),
    ],
    ids=["fact", "boundary", "binding"],
)
def test_input_rejects_unknown_semantic_field_even_when_fingerprint_is_unchanged(
    mutation: Any,
) -> None:
    ledger = _normalized_ledger()
    declared_fingerprint = ledger["fingerprint"]
    mutation(ledger)

    assert fingerprint_requirement_scope_ledger(ledger) == declared_fingerprint
    with pytest.raises(RequirementGraphStageContractError) as caught:
        build_requirement_graph_stage_user_input(ledger)

    assert caught.value.code == "scope_ledger_frozen_shape_invalid"
    assert caught.value.details == {
        "error_codes": ["scope_ledger_frozen_field_unknown"]
    }
