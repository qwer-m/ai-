import copy
import hashlib
import json

import pytest

from modules.test_generation_components.control.requirement_fact_ledger import (
    fingerprint_source_evidence_catalog,
    normalize_requirement_fact_ledger,
)
from modules.test_generation_components.control.requirement_scope_ledger import (
    normalize_requirement_scope_ledger,
    project_requirement_scope_ledger,
    validate_requirement_scope_ledger_projection,
)
from modules.test_generation_components.control.requirement_semantic_graph import (
    normalize_requirement_semantic_graph,
)
from modules.test_generation_components.control.semantic_contract import (
    evidence_supported,
)


CATALOG = [
    {"ref": "EV_111111111111", "quote": "当前需求包含消息区。"},
    {"ref": "EV_222222222222", "quote": "消息区是可独立导航的职责分区。"},
    {"ref": "EV_333333333333", "quote": "消息区支持用户查看消息。"},
    {"ref": "EV_444444444444", "quote": "外部发送方提供消息数据。"},
    {"ref": "EV_555555555555", "quote": "旧版入口不属于本次范围。"},
]
SOURCE_TEXT = "\n".join(item["quote"] for item in CATALOG)


def _fact(
    fact_id: str,
    statement: str,
    evidence_ref: str,
    *,
    requirement_level: str = "required",
    priority: str = "unspecified",
    testability: str = "testable",
    fact_kind: str = "action",
    confidence: float = 0.95,
) -> dict[str, object]:
    return {
        "fact_id": fact_id,
        "fact_kind": fact_kind,
        "statement": statement,
        "requirement_level": requirement_level,
        "priority": priority,
        "testability": testability,
        "evidence": [evidence_ref],
        "anchor_evidence_ref": evidence_ref,
        "confidence": confidence,
    }


def _fact_declarations() -> list[dict[str, object]]:
    return [
        _fact("F_MEMBERSHIP", "当前需求包含消息区。", "EV_111111111111"),
        _fact(
            "F_SUPPORT",
            "消息区是可独立导航的职责分区。",
            "EV_222222222222",
        ),
        _fact("F_OWN", "消息区支持用户查看消息。", "EV_333333333333"),
        _fact("F_EXTERNAL", "外部发送方提供消息数据。", "EV_444444444444"),
        _fact(
            "F_NOT_SCOPE",
            "旧版入口不属于本次范围。",
            "EV_555555555555",
            requirement_level="optional",
            priority="unspecified",
            testability="non_testable",
        ),
    ]


def _normalized_fact_ledger(
    facts: list[dict[str, object]] | None = None,
    *,
    catalog: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    source_catalog = copy.deepcopy(catalog if catalog is not None else CATALOG)
    declarations = copy.deepcopy(
        facts if facts is not None else _fact_declarations()
    )
    fact_ids_by_ref: dict[str, list[str]] = {}
    for fact in declarations:
        for evidence_ref in fact.get("evidence") or []:
            fact_ids_by_ref.setdefault(str(evidence_ref), []).append(
                str(fact["fact_id"])
            )
    source_fingerprint = fingerprint_source_evidence_catalog(source_catalog)
    payload = {
        "evidence_facts": declarations,
        "source_evidence_dispositions": [
            {
                "evidence_ref": item["ref"],
                "disposition": (
                    "fact_backed"
                    if fact_ids_by_ref.get(item["ref"])
                    else "context_only"
                ),
            }
            for item in source_catalog
        ],
    }
    return normalize_requirement_fact_ledger(
        payload,
        source_evidence_catalog=source_catalog,
        source_catalog_fingerprint=source_fingerprint,
    )


def _ledger_payload() -> dict[str, object]:
    return {
        "boundaries": [
            {
                "boundary_id": "S_PARENT",
                "label": "当前需求",
                "decision": "in_scope_parent",
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": [
                    {
                        "signal": "member_enumeration",
                        "fact_ids": ["F_MEMBERSHIP"],
                    }
                ],
            },
            {
                "boundary_id": "S_MESSAGE",
                "label": "消息区",
                "decision": "in_scope_leaf",
                "parent_boundary_id": "S_PARENT",
                "membership_relation_ids": [],
                "membership_fact_ids": ["F_MEMBERSHIP"],
                "support": [
                    {
                        "signal": "navigable_partition",
                        "fact_ids": ["F_SUPPORT"],
                    }
                ],
            },
            {
                "boundary_id": "B_EXTERNAL",
                "label": "外部发送方",
                "decision": "external_context",
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": [],
            },
            {
                "boundary_id": "B_NOT_SCOPE",
                "label": "旧版入口",
                "decision": "not_scope",
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": [],
            },
            {
                "boundary_id": "B_AMBIGUOUS",
                "label": "待澄清参与方",
                "decision": "ambiguous",
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": [],
            },
        ],
        "fact_bindings": [
            {
                "fact_id": "F_MEMBERSHIP",
                "scope_ids": ["S_PARENT"],
                "role": "owned_requirement",
            },
            {
                "fact_id": "F_SUPPORT",
                "scope_ids": ["S_MESSAGE"],
                "role": "owned_requirement",
            },
            {
                "fact_id": "F_OWN",
                "scope_ids": ["S_MESSAGE"],
                "role": "owned_requirement",
            },
            {
                "fact_id": "F_EXTERNAL",
                "scope_ids": ["B_EXTERNAL"],
                "role": "external_context",
            },
            {
                "fact_id": "F_NOT_SCOPE",
                "scope_ids": ["B_NOT_SCOPE"],
                "role": "non_scope_context",
            },
        ],
    }


def _normalize_ledger(
    payload: dict[str, object],
    *,
    fact_ledger: dict[str, object] | None = None,
    source_evidence_catalog: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return normalize_requirement_scope_ledger(
        payload,
        normalized_fact_ledger=(fact_ledger or _normalized_fact_ledger()),
        source_evidence_catalog=(source_evidence_catalog or CATALOG),
    )


def _graph() -> dict[str, object]:
    return {
        "graph_version": "requirement-semantic-graph-v1",
        "nodes": [
            {
                "node_id": "S_PARENT",
                "kind": "scope",
                "name": "任意父级名称",
                "scope_status": "in_scope",
                "fact_ids": ["F_MEMBERSHIP"],
            },
            {
                "node_id": "S_MESSAGE",
                "kind": "scope",
                "name": "名称不参与匹配",
                "scope_status": "in_scope",
                "fact_ids": ["F_MEMBERSHIP", "F_SUPPORT", "F_OWN"],
            },
            {
                "node_id": "B_EXTERNAL",
                "kind": "scope",
                "name": "外部边界",
                "scope_status": "out_of_scope",
                "fact_ids": ["F_EXTERNAL"],
            },
            {
                "node_id": "B_NOT_SCOPE",
                "kind": "scope",
                "name": "非范围边界",
                "scope_status": "out_of_scope",
                "fact_ids": ["F_NOT_SCOPE"],
            },
            {
                "node_id": "B_AMBIGUOUS",
                "kind": "scope",
                "name": "待澄清边界",
                "scope_status": "unknown",
                "fact_ids": [],
            },
            {
                "node_id": "C_VIEW",
                "kind": "capability",
                "name": "查看消息",
                "fact_ids": ["F_OWN"],
            },
        ],
        "edges": [
            {
                "edge_id": "E_CONTAINS",
                "type": "contains",
                "source_node_id": "S_PARENT",
                "target_node_id": "S_MESSAGE",
                "fact_ids": ["F_MEMBERSHIP"],
                "ownership_role": "none",
            },
            {
                "edge_id": "E_OWNS",
                "type": "owns",
                "source_node_id": "S_MESSAGE",
                "target_node_id": "C_VIEW",
                "fact_ids": ["F_OWN"],
                "ownership_role": "primary",
            },
        ],
    }


def _projection() -> dict[str, object]:
    normalized = _normalize_ledger(_ledger_payload())
    assert normalized["valid"] is True, normalized["errors"]
    return project_requirement_scope_ledger(normalized)


def test_a2_injects_frozen_a1_facts_without_renormalizing() -> None:
    facts = _fact_declarations()
    owned_fact = facts[2]
    owned_fact.update(
        {
            "fact_kind": "Action",
            "statement": "长" * 320,
            "requirement_level": "REQUIRED",
            "priority": "UNSPECIFIED",
            "testability": "NON TESTABLE",
            "confidence": 0.987654,
        }
    )
    fact_ledger = _normalized_fact_ledger(facts)
    assert fact_ledger["valid"] is True, fact_ledger["errors"]

    normalized = _normalize_ledger(
        _ledger_payload(),
        fact_ledger=fact_ledger,
    )

    assert normalized["evidence_facts"] == fact_ledger["evidence_facts"]
    assert normalized["fact_ledger_version"] == fact_ledger[
        "fact_ledger_version"
    ]
    assert normalized["fact_ledger_fingerprint"] == fact_ledger["fingerprint"]
    normalized_owned = next(
        item for item in normalized["evidence_facts"] if item["fact_id"] == "F_OWN"
    )
    assert normalized_owned["fact_kind"] == "action"
    assert len(normalized_owned["statement"]) == 320
    assert normalized_owned["testability"] == "non_testable"
    assert normalized_owned["confidence"] == 0.987654
    assert normalized_owned["evidence"] == ["消息区支持用户查看消息。"]


def test_a2_consumes_resolved_facts_with_matching_source_catalog() -> None:
    normalized = _normalize_ledger(_ledger_payload())

    assert normalized["valid"] is True, normalized["errors"]
    assert normalized["ledger_version"] == "requirement-scope-ledger-v3"
    assert {
        evidence
        for fact in normalized["evidence_facts"]
        for evidence in fact["evidence"]
    } == {item["quote"] for item in CATALOG}
    assert not {
        evidence
        for fact in normalized["evidence_facts"]
        for evidence in fact["evidence"]
        if evidence.startswith("EV_")
    }


def test_a2_rejects_tampered_frozen_fact_ledger() -> None:
    fact_ledger = _normalized_fact_ledger()
    fact_ledger["evidence_facts"][2]["statement"] = "被篡改"

    with pytest.raises(ValueError, match="指纹校验失败"):
        _normalize_ledger(_ledger_payload(), fact_ledger=fact_ledger)


def test_a2_accepts_valid_frozen_long_quote_contract() -> None:
    catalog = copy.deepcopy(CATALOG)
    catalog[2]["quote"] = "长引文" * 140
    fact_ledger = _normalized_fact_ledger(catalog=catalog)
    assert fact_ledger["valid"] is True, fact_ledger["errors"]

    normalized = _normalize_ledger(
        _ledger_payload(),
        fact_ledger=fact_ledger,
        source_evidence_catalog=catalog,
    )
    owned_fact = next(
        fact for fact in normalized["evidence_facts"] if fact["fact_id"] == "F_OWN"
    )

    assert normalized["valid"] is True, normalized["errors"]
    assert owned_fact["evidence"] == [catalog[2]["quote"][:320]]


def test_frozen_a1_facts_do_not_drift_through_a2_and_graph_stage() -> None:
    normalized = _normalize_ledger(_ledger_payload())
    assert normalized["valid"] is True, normalized["errors"]

    graph_result = normalize_requirement_semantic_graph(
        {
            "evidence_facts": normalized["evidence_facts"],
            "semantic_graph": _graph(),
        },
        source_text=SOURCE_TEXT,
        evidence_validator=evidence_supported,
    )

    assert graph_result["evidence_facts"] == normalized["evidence_facts"]
    assert "fact_evidence_unverified" not in {
        item["code"] for item in graph_result["errors"]
    }


def test_source_evidence_catalog_fingerprint_uses_canonical_json() -> None:
    canonical = json.dumps(
        CATALOG,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    key_reordered_catalog = [
        {"quote": item["quote"], "ref": item["ref"]} for item in CATALOG
    ]

    assert fingerprint_source_evidence_catalog(CATALOG) == expected
    assert fingerprint_source_evidence_catalog(key_reordered_catalog) == expected


def test_a2_rejects_invalid_unpublished_fact_ledger() -> None:
    facts = _fact_declarations()
    facts[2]["testability"] = "unknown"
    fact_ledger = _normalized_fact_ledger(facts)
    assert fact_ledger["valid"] is False

    with pytest.raises(ValueError, match="有效冻结对象"):
        _normalize_ledger(_ledger_payload(), fact_ledger=fact_ledger)


def test_projection_validator_accepts_exact_id_topology_and_ownership() -> None:
    projection = _projection()
    assert projection["ambiguous_boundary_ids"] == ["B_AMBIGUOUS"]

    result = validate_requirement_scope_ledger_projection(
        projection,
        {"semantic_graph": _graph()},
    )

    assert result == {"valid": True, "errors": [], "error_codes": []}


def test_projection_validator_rejects_external_or_not_scope_promotion() -> None:
    graph = _graph()
    graph["nodes"][2]["scope_status"] = "in_scope"
    graph["nodes"][3]["scope_status"] = "in_scope"
    graph["nodes"][4]["scope_status"] = "in_scope"

    result = validate_requirement_scope_ledger_projection(_projection(), graph)

    assert result["valid"] is False
    assert "active_scope_id_mismatch" in result["error_codes"]
    assert "inactive_boundary_promoted_to_active_scope" in result["error_codes"]


def test_projection_validator_rejects_contains_and_support_fact_drift() -> None:
    graph = _graph()
    graph["edges"][0]["source_node_id"] = "S_MESSAGE"
    graph["nodes"][1]["fact_ids"].remove("F_SUPPORT")

    result = validate_requirement_scope_ledger_projection(_projection(), graph)

    assert "scope_contains_mismatch" in result["error_codes"]
    assert "scope_support_fact_missing" in result["error_codes"]


def test_projection_validator_requires_parent_own_support_evidence() -> None:
    graph = _graph()
    graph["nodes"][0]["fact_ids"].remove("F_MEMBERSHIP")

    result = validate_requirement_scope_ledger_projection(_projection(), graph)

    assert "scope_support_fact_missing" in result["error_codes"]


def test_projection_validator_requires_child_membership_evidence() -> None:
    graph = _graph()
    graph["nodes"][1]["fact_ids"].remove("F_MEMBERSHIP")

    result = validate_requirement_scope_ledger_projection(_projection(), graph)

    assert "scope_membership_fact_missing" in result["error_codes"]


def test_scope_to_capability_contains_is_not_scope_hierarchy_drift() -> None:
    graph = _graph()
    graph["edges"][1]["type"] = "contains"

    result = validate_requirement_scope_ledger_projection(_projection(), graph)

    assert result == {"valid": True, "errors": [], "error_codes": []}


def test_projection_validator_rejects_capability_owner_and_fact_drift() -> None:
    graph = _graph()
    graph["edges"][1].update(
        {
            "source_node_id": "S_PARENT",
            "fact_ids": ["F_SUPPORT"],
        }
    )

    result = validate_requirement_scope_ledger_projection(_projection(), graph)

    assert "capability_ownership_mismatch" in result["error_codes"]
    assert "capability_owns_fact_binding_mismatch" in result["error_codes"]


def test_projection_validator_accepts_shared_fact_owners_exactly() -> None:
    projection = copy.deepcopy(_projection())
    projection["active_scope_ids"].append("S_OTHER")
    projection["active_scopes"].append(
        {
            "scope_id": "S_OTHER",
            "name": "其他职责",
            "decision": "in_scope_leaf",
            "parent_scope_id": "S_PARENT",
            "membership_fact_ids": [],
            "support_fact_ids": [],
        }
    )
    projection["parent_by_scope_id"]["S_OTHER"] = "S_PARENT"
    projection["fact_bindings"]["F_OWN"] = {
        "scope_ids": ["S_MESSAGE", "S_OTHER"],
        "role": "shared_requirement",
    }
    graph = _graph()
    graph["nodes"].insert(
        2,
        {
            "node_id": "S_OTHER",
            "kind": "scope",
            "name": "另一个名称",
            "scope_status": "in_scope",
            "fact_ids": ["F_OWN"],
        },
    )
    graph["edges"].append(
        {
            "edge_id": "E_CONTAINS_OTHER",
            "type": "contains",
            "source_node_id": "S_PARENT",
            "target_node_id": "S_OTHER",
            "fact_ids": ["F_MEMBERSHIP"],
            "ownership_role": "none",
        }
    )
    graph["edges"][1]["ownership_role"] = "shared"
    graph["edges"].append(
        {
            "edge_id": "E_OWNS_OTHER",
            "type": "owns",
            "source_node_id": "S_OTHER",
            "target_node_id": "C_VIEW",
            "fact_ids": ["F_OWN"],
            "ownership_role": "shared",
        }
    )

    result = validate_requirement_scope_ledger_projection(projection, graph)

    assert result == {"valid": True, "errors": [], "error_codes": []}
