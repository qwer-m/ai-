import copy
import json
import re

import pytest

from modules.test_generation_components.control.model_envelope_call import (
    strict_json_output_contract_prompt,
)
from modules.test_generation_components.control.requirement_fact_ledger import (
    fingerprint_source_evidence_catalog,
    normalize_requirement_fact_ledger,
)
from modules.test_generation_components.control.requirement_scope_ledger import (
    _project_scope_model_fact_table,
    build_requirement_scope_binding_prompt,
    build_requirement_scope_binding_user_input,
    build_requirement_scope_boundary_selection_prompt,
    build_requirement_scope_boundary_selection_user_input,
    build_requirement_scope_membership_prompt,
    build_requirement_scope_membership_user_input,
    fingerprint_requirement_scope_boundary_manifest,
    fingerprint_requirement_scope_boundary_selection,
    normalize_requirement_scope_binding_shard,
    normalize_requirement_scope_boundary_selection,
    normalize_requirement_scope_boundary_selection_model_response,
    normalize_requirement_scope_ledger,
    normalize_requirement_scope_membership_model_response,
)


CATALOG = [
    {
        "ref": "EV_aaaaaaaaaaaa",
        "quote": "2. 功能分区",
    },
    {
        "ref": "EV_bbbbbbbbbbbb",
        "quote": "1. 消息区",
    },
    {
        "ref": "EV_cccccccccccc",
        "quote": "2. 交流区",
    },
    {
        "ref": "EV_dddddddddddd",
        "quote": "用户可独立进入消息区，并在其中查看归属自己的消息。",
    },
]
CATALOG_FINGERPRINT = fingerprint_source_evidence_catalog(CATALOG)


def _fact(
    fact_id: str,
    statement: str,
    evidence_ref: str,
) -> dict[str, object]:
    return {
        "fact_id": fact_id,
        "fact_kind": "action",
        "statement": statement,
        "requirement_level": "required",
        "priority": "unspecified",
        "testability": "testable",
        "evidence": [evidence_ref],
        "anchor_evidence_ref": evidence_ref,
        "confidence": 0.95,
    }


def _base_facts() -> list[dict[str, object]]:
    return [
            _fact(
                "FACT_PARENT_MEMBERSHIP",
                "当前功能域包括消息区和交流区。",
                "EV_aaaaaaaaaaaa",
            ),
            _fact(
                "FACT_MESSAGE_PARTITION",
                "用户可独立进入消息区，并在其中查看归属自己的消息。",
                "EV_dddddddddddd",
            ),
        ]


def _normalized_fact_ledger(
    facts: list[dict[str, object]] | None = None,
    *,
    source_catalog: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    catalog = source_catalog if source_catalog is not None else CATALOG
    declarations = copy.deepcopy(facts if facts is not None else _base_facts())
    fact_ids_by_ref: dict[str, list[str]] = {}
    for fact in declarations:
        for evidence_ref in fact.get("evidence") or []:
            fact_ids_by_ref.setdefault(str(evidence_ref), []).append(
                str(fact["fact_id"])
            )
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
            for item in catalog
        ],
    }
    normalized = normalize_requirement_fact_ledger(
        payload,
        source_evidence_catalog=catalog,
        source_catalog_fingerprint=fingerprint_source_evidence_catalog(catalog),
    )
    assert normalized["valid"] is True, normalized["errors"]
    return normalized


def _ledger() -> dict[str, object]:
    return {
        "boundaries": [
            {
                "boundary_id": "SCOPE_CURRENT",
                "label": "当前功能域",
                "decision": "in_scope_parent",
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": [
                    {
                        "signal": "member_enumeration",
                        "fact_ids": ["FACT_PARENT_MEMBERSHIP"],
                    }
                ],
            },
            {
                "boundary_id": "SCOPE_MESSAGE",
                "label": "消息区",
                "decision": "in_scope_leaf",
                "parent_boundary_id": "SCOPE_CURRENT",
                "membership_relation_ids": ["R001"],
                "membership_fact_ids": [],
                "support": [
                    {
                        "signal": "navigable_partition",
                        "fact_ids": ["FACT_MESSAGE_PARTITION"],
                    }
                ],
            },
        ],
        "fact_bindings": [
            {
                "fact_id": "FACT_PARENT_MEMBERSHIP",
                "scope_ids": ["SCOPE_CURRENT"],
                "role": "owned_requirement",
            },
            {
                "fact_id": "FACT_MESSAGE_PARTITION",
                "scope_ids": ["SCOPE_MESSAGE"],
                "role": "owned_requirement",
            },
        ],
    }


def _selection_boundaries() -> list[dict[str, object]]:
    boundaries = copy.deepcopy(_ledger()["boundaries"])
    for boundary in boundaries:
        boundary["membership_relation_ids"] = []
        boundary["membership_fact_ids"] = []
    return boundaries


def _scope_fact_ref_by_id(
    frozen: dict[str, object] | None = None,
) -> dict[str, str]:
    fact_ledger = frozen or _normalized_fact_ledger()
    facts = sorted(
        (
            item
            for item in fact_ledger["evidence_facts"]
            if isinstance(item, dict)
        ),
        key=lambda item: str(item.get("fact_id") or ""),
    )
    width = max(3, len(str(len(facts))))
    return {
        str(item["fact_id"]): f"F{index:0{width}d}"
        for index, item in enumerate(facts, start=1)
    }


def _boundary_selection_model_response(
    frozen: dict[str, object] | None = None,
) -> dict[str, object]:
    fact_ref_by_id = _scope_fact_ref_by_id(frozen)
    records: list[dict[str, object]] = []
    for boundary in copy.deepcopy(_ledger()["boundaries"]):
        records.append(
            {
                "boundary_id": boundary.get("boundary_id"),
                "label": boundary.get("label"),
                "decision": (
                    "in_scope"
                    if boundary.get("decision")
                    in {"in_scope_parent", "in_scope_leaf"}
                    else boundary.get("decision")
                ),
                "parent_boundary_id": boundary.get("parent_boundary_id"),
                "support": [
                    {
                        "signal": copy.deepcopy(support.get("signal")),
                        "fact_refs": [
                            fact_ref_by_id[str(fact_id)]
                            for fact_id in support.get("fact_ids") or []
                        ],
                    }
                    for support in boundary.get("support") or []
                ],
            }
        )
    return {"boundary_records": records}


def _boundary_selection(
    frozen: dict[str, object] | None = None,
) -> dict[str, object]:
    fact_ledger = frozen or _normalized_fact_ledger()
    normalized = normalize_requirement_scope_boundary_selection_model_response(
        _boundary_selection_model_response(fact_ledger),
        fact_ledger,
    )
    assert normalized["valid"] is True, normalized["errors"]
    return normalized


def _membership_model_response(
    *,
    membership_kind: str = "source_relation",
    membership_ref: str = "R001",
) -> dict[str, object]:
    return {
        "membership_assignments": [
            {
                "boundary_id": "SCOPE_MESSAGE",
                "membership_kind": membership_kind,
                "membership_ref": membership_ref,
            }
        ]
    }


def _normalize(
    payload: dict[str, object],
    *,
    facts: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return normalize_requirement_scope_ledger(
        payload,
        normalized_fact_ledger=_normalized_fact_ledger(facts),
        source_evidence_catalog=CATALOG,
    )


def test_navigable_partition_is_a_valid_substantive_leaf_signal() -> None:
    normalized = _normalize(_ledger())

    assert normalized["valid"] is True
    assert normalized["errors"] == []


def test_membership_and_support_may_use_the_same_fact_independently() -> None:
    payload = _ledger()
    child = payload["boundaries"][1]
    child["membership_relation_ids"] = []
    child["membership_fact_ids"] = ["FACT_MESSAGE_PARTITION"]

    normalized = _normalize(payload)

    assert normalized["valid"] is True, normalized["errors"]


def test_canonical_manifest_rejects_support_fact_under_multiple_signals() -> None:
    payload = _ledger()
    payload["boundaries"][1]["support"].append(
        {
            "signal": "actor",
            "fact_ids": ["FACT_MESSAGE_PARTITION"],
        }
    )

    normalized = _normalize(payload)

    assert normalized["valid"] is False
    assert "boundary_support_fact_duplicate" in {
        item["code"] for item in normalized["errors"]
    }


def test_canonical_selection_rejects_overlapping_groups_with_the_same_signal() -> None:
    boundaries = _selection_boundaries()
    boundaries[0]["support"].append(
        {
            "signal": "member_enumeration",
            "fact_ids": [
                "FACT_PARENT_MEMBERSHIP",
                "FACT_MESSAGE_PARTITION",
            ],
        }
    )

    selection = normalize_requirement_scope_boundary_selection(
        {"boundaries": boundaries},
        _normalized_fact_ledger(),
    )

    assert selection["valid"] is False
    assert "boundary_support_fact_duplicate" in {
        item["code"] for item in selection["errors"]
    }


def test_canonical_selection_rejects_disjoint_groups_with_the_same_signal() -> None:
    boundaries = _selection_boundaries()
    boundaries[0]["support"].append(
        {
            "signal": "member_enumeration",
            "fact_ids": ["FACT_MESSAGE_PARTITION"],
        }
    )

    selection = normalize_requirement_scope_boundary_selection(
        {"boundaries": boundaries},
        _normalized_fact_ledger(),
    )

    assert selection["valid"] is False
    assert "boundary_support_signal_duplicate" in {
        item["code"] for item in selection["errors"]
    }


def test_bare_membership_enumeration_cannot_be_active_leaf() -> None:
    payload = _ledger()
    payload["boundaries"][1]["support"] = []
    payload["fact_bindings"] = payload["fact_bindings"][:1]

    normalized = _normalize(payload, facts=_base_facts()[:1])

    assert normalized["valid"] is False
    assert "active_leaf_support_missing" in {
        item["code"] for item in normalized["errors"]
    }


def test_page_is_not_an_allowed_boundary_signal() -> None:
    payload = copy.deepcopy(_ledger())
    payload["boundaries"][1]["support"][0]["signal"] = "page"

    normalized = _normalize(payload)

    assert normalized["valid"] is False
    assert "boundary_support_signal_invalid" in {
        item["code"] for item in normalized["errors"]
    }


def test_selection_prompt_limits_boundaries_to_substantive_responsibilities() -> None:
    prompt = build_requirement_scope_boundary_selection_prompt()

    assert prompt.count(strict_json_output_contract_prompt()) == 1
    assert "navigable_partition" in prompt
    assert "A page, entry, dialog, viewer, button" in prompt
    assert "globally minimal responsibility boundaries" in prompt
    assert "No source outline or relation inventory is provided" in prompt
    assert "This stage is the only stage allowed to create" in prompt
    assert "Never output membership_relation_refs" in prompt
    assert "Declare active boundaries as in_scope" in prompt
    assert "derives canonical in_scope_parent or in_scope_leaf" in prompt
    assert "copied byte-for-byte from frozen_fact_table.fact_ref" in prompt
    assert "F013 must never become F13" in prompt
    assert "exact member of the declared signal enum" in prompt


def test_membership_prompt_freezes_selection_and_requires_scalar_proof() -> None:
    prompt = build_requirement_scope_membership_prompt()

    assert prompt.count(strict_json_output_contract_prompt()) == 1
    assert "every non-root boundary" in prompt
    assert "The selection is immutable" in prompt
    assert "member-specific" in prompt
    assert "cannot represent a sibling inventory" in prompt
    assert "Choose one minimal proof" in prompt
    assert "mutually exclusive" in prompt
    assert "Unused relations are valid" in prompt
    assert "An active non-root using none will fail closed" in prompt


def test_prompt_preserves_current_requirement_language_for_readability() -> None:
    prompt = build_requirement_scope_boundary_selection_prompt()

    assert "predominant language of frozen statements" in prompt
    assert "For Chinese facts, use concise Chinese" in prompt
    assert "Keep IDs and enum tokens in protocol English" in prompt


def test_binding_prompt_exposes_final_active_leaf_owner_closure() -> None:
    prompt = build_requirement_scope_binding_prompt()

    assert "every active leaf must receive an owned_requirement" in prompt
    assert "substantive support facts" in prompt
    assert "preserve that support-owner closure" in prompt


def test_root_membership_is_rejected() -> None:
    payload = _ledger()
    payload["boundaries"][0]["membership_fact_ids"] = [
        "FACT_PARENT_MEMBERSHIP"
    ]

    normalized = _normalize(payload)

    assert "root_boundary_membership_not_empty" in {
        item["code"] for item in normalized["errors"]
    }


def test_active_child_requires_source_relation_or_explicit_membership_fact() -> None:
    missing = _ledger()
    missing["boundaries"][1]["membership_relation_ids"] = []
    missing["boundaries"][1]["membership_fact_ids"] = []

    missing_result = _normalize(missing)

    assert "active_child_membership_missing" in {
        item["code"] for item in missing_result["errors"]
    }


def test_distinct_sibling_boundaries_may_consume_distinct_member_relations() -> None:
    payload = _ledger()
    payload["boundaries"].append(
        {
            "boundary_id": "SCOPE_OTHER_SIBLING",
            "label": "其他同级分区",
            "decision": "not_scope",
            "parent_boundary_id": "SCOPE_CURRENT",
            "membership_relation_ids": ["R002"],
            "membership_fact_ids": [],
            "support": [],
        }
    )

    normalized = _normalize(payload)

    assert normalized["valid"] is True, normalized["errors"]


def test_source_relation_alone_cannot_establish_active_leaf() -> None:
    payload = _ledger()
    payload["boundaries"][1]["membership_fact_ids"] = []
    payload["boundaries"][1]["support"] = []

    normalized = _normalize(payload)

    assert "active_leaf_support_missing" in {
        item["code"] for item in normalized["errors"]
    }


def test_owned_requirement_can_be_used_as_leaf_support() -> None:
    payload = _ledger()
    payload["fact_bindings"][1]["role"] = "owned_requirement"

    normalized = _normalize(payload)

    assert normalized["valid"] is True, normalized["errors"]


def test_shared_requirement_can_be_used_as_leaf_support() -> None:
    payload = _ledger()
    payload["fact_bindings"][1] = {
        "fact_id": "FACT_MESSAGE_PARTITION",
        "scope_ids": ["SCOPE_CURRENT", "SCOPE_MESSAGE"],
        "role": "shared_requirement",
    }

    normalized = _normalize(payload)

    assert normalized["valid"] is True, normalized["errors"]


def test_membership_fact_can_be_reused_by_sibling_children() -> None:
    payload = _ledger()
    payload["boundaries"][1]["membership_relation_ids"] = []
    payload["boundaries"][1]["membership_fact_ids"] = [
        "FACT_PARENT_MEMBERSHIP"
    ]
    payload["boundaries"].append(
        {
            "boundary_id": "SCOPE_SIBLING_CONTEXT",
            "label": "同级上下文",
            "decision": "not_scope",
            "parent_boundary_id": "SCOPE_CURRENT",
            "membership_relation_ids": [],
            "membership_fact_ids": ["FACT_PARENT_MEMBERSHIP"],
            "support": [],
        }
    )

    normalized = _normalize(payload)

    assert normalized["valid"] is True, normalized["errors"]


def test_member_enumeration_is_not_substantive_leaf_support() -> None:
    payload = _ledger()
    payload["boundaries"][1]["support"][0]["signal"] = "member_enumeration"

    normalized = _normalize(payload)

    assert "active_leaf_substantive_support_missing" in {
        item["code"] for item in normalized["errors"]
    }


def test_non_scope_context_may_bind_inactive_boundary() -> None:
    for decision in ("not_scope", "ambiguous"):
        payload = _ledger()
        facts = [
            *_base_facts(),
            _fact(
                "FACT_NOT_SCOPE",
                "该约束不属于已确认的当前职责范围。",
                "EV_bbbbbbbbbbbb",
            ),
        ]
        facts[-1]["requirement_level"] = "optional"
        payload["boundaries"].append(
            {
                "boundary_id": "BOUNDARY_INACTIVE",
                "label": "非活动边界",
                "decision": decision,
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": [],
            }
        )
        payload["fact_bindings"].append(
            {
                "fact_id": "FACT_NOT_SCOPE",
                "scope_ids": ["BOUNDARY_INACTIVE"],
                "role": "non_scope_context",
            }
        )

        bound_result = _normalize(payload, facts=facts)

        assert bound_result["valid"] is True, bound_result["errors"]

        payload["fact_bindings"][-1]["scope_ids"] = []
        unbound_result = _normalize(payload, facts=facts)

        assert "testable_fact_without_scope_or_external_context" in {
            item["code"] for item in unbound_result["errors"]
        }


def test_external_context_rejects_required_or_testable_fact() -> None:
    payload = _ledger()
    external_fact = _fact(
        "FACT_EXTERNAL_CONTEXT",
        "外部服务的背景说明。",
        "EV_bbbbbbbbbbbb",
    )
    external_fact["requirement_level"] = "optional"
    facts = [*_base_facts(), external_fact]
    payload["boundaries"].append(
        {
            "boundary_id": "BOUNDARY_EXTERNAL",
            "label": "外部服务",
            "decision": "external_context",
            "parent_boundary_id": "",
            "membership_relation_ids": [],
            "membership_fact_ids": [],
            "support": [],
        }
    )
    payload["fact_bindings"].append(
        {
            "fact_id": "FACT_EXTERNAL_CONTEXT",
            "scope_ids": ["BOUNDARY_EXTERNAL"],
            "role": "external_context",
        }
    )

    executable = _normalize(payload, facts=facts)

    assert "executable_fact_external_context_forbidden" in {
        item["code"] for item in executable["errors"]
    }
    executable_error = next(
        item
        for item in executable["errors"]
        if item["code"] == "executable_fact_external_context_forbidden"
    )
    assert executable_error["current_role"] == "external_context"
    assert executable_error["current_scope_ids"] == ["BOUNDARY_EXTERNAL"]
    assert executable_error["allowed_roles"] == [
        "owned_requirement",
        "shared_requirement",
    ]
    assert executable_error["allowed_active_scope_ids"]
    assert executable_error["repair_action"] == (
        "bind_to_active_responsibility_owner"
    )

    external_fact["testability"] = "non_testable"
    contextual = _normalize(payload, facts=facts)

    assert contextual["valid"] is True, contextual["errors"]


def test_model_cannot_override_frozen_facts_or_injected_versions() -> None:
    payload = _ledger()
    payload.update(
        {
            "ledger_version": "model-value",
            "fact_ledger_fingerprint": "model-value",
            "evidence_facts": [],
        }
    )

    normalized = _normalize(payload)

    assert normalized["valid"] is False
    assert normalized["evidence_facts"] == _normalized_fact_ledger()[
        "evidence_facts"
    ]
    assert normalized["ledger_version"] == "requirement-scope-ledger-v3"
    assert {
        item["path"] for item in normalized["errors"]
    } >= {"$.ledger_version", "$.fact_ledger_fingerprint", "$.evidence_facts"}


def test_scope_ledger_preserves_frozen_fact_source_order() -> None:
    facts = list(reversed(_base_facts()))
    frozen = _normalized_fact_ledger(facts)

    normalized = normalize_requirement_scope_ledger(
        _ledger(),
        normalized_fact_ledger=frozen,
        source_evidence_catalog=CATALOG,
    )

    assert normalized["valid"] is True, normalized["errors"]
    assert [item["fact_id"] for item in normalized["evidence_facts"]] == [
        "FACT_MESSAGE_PARTITION",
        "FACT_PARENT_MEMBERSHIP",
    ]


def _boundary_manifest(
    frozen: dict[str, object] | None = None,
) -> dict[str, object]:
    fact_ledger = frozen or _normalized_fact_ledger()
    normalized = normalize_requirement_scope_membership_model_response(
        _membership_model_response(),
        fact_ledger,
        _boundary_selection(fact_ledger),
        CATALOG,
    )
    assert normalized["valid"] is True, normalized["errors"]
    return normalized


def _frozen_fact_ids(frozen: dict[str, object]) -> list[str]:
    return [
        str(item["fact_id"])
        for item in frozen["evidence_facts"]
        if isinstance(item, dict)
    ]


def _scope_model_fact_table(frozen: dict[str, object]) -> dict[str, object]:
    schema = (
        "fact_ref",
        "fact_kind",
        "statement",
        "requirement_level",
        "priority",
        "testability",
        "confidence",
    )
    fact_ref_by_id = _scope_fact_ref_by_id(frozen)
    facts = sorted(
        (
            item
            for item in frozen["evidence_facts"]
            if isinstance(item, dict)
        ),
        key=lambda item: str(item.get("fact_id") or ""),
    )
    return {
        "schema": list(schema),
        "rows": [
            [
                fact_ref_by_id[str(item["fact_id"])],
                *[
                    copy.deepcopy(item[field])
                    for field in schema[1:]
                ],
            ]
            for item in facts
        ],
    }


def test_scope_fact_table_is_lossless_and_does_not_repeat_schema_per_fact() -> None:
    schema = [
        "fact_ref",
        "fact_kind",
        "statement",
        "requirement_level",
        "priority",
        "testability",
        "confidence",
    ]
    facts = [
        {
            "fact_id": f"FACT_SCALE_{index:03d}",
            "fact_kind": "constraint",
            "statement": f"通用约束 {index}",
            "requirement_level": "required",
            "priority": "unspecified",
            "testability": "testable",
            "confidence": 0.9,
        }
        for index in range(220)
    ]

    table = _project_scope_model_fact_table({"evidence_facts": facts})
    reconstructed = [
        dict(zip(table["schema"], row)) for row in table["rows"]
    ]
    expected = [
        {
            "fact_ref": f"F{index:03d}",
            **{
                key: copy.deepcopy(value)
                for key, value in fact.items()
                if key != "fact_id"
            },
        }
        for index, fact in enumerate(facts, start=1)
    ]
    table_json = json.dumps(table, ensure_ascii=False, separators=(",", ":"))
    object_json = json.dumps(expected, ensure_ascii=False, separators=(",", ":"))

    assert table["schema"] == schema
    assert len(table["rows"]) == len(facts)
    assert all(len(row) == len(schema) for row in table["rows"])
    assert reconstructed == expected
    assert [row[0] for row in table["rows"]] == [
        f"F{index:03d}" for index in range(1, len(facts) + 1)
    ]
    assert "fact_id" not in table["schema"]
    assert not any(
        str(fact["fact_id"]) in table_json
        for fact in facts
    )
    assert len(table_json) < len(object_json)
    assert all(table_json.count(f'"{field}"') == 1 for field in schema)


def _binding_by_fact_id() -> dict[str, dict[str, object]]:
    return {
        str(item["fact_id"]): copy.deepcopy(item)
        for item in _ledger()["fact_bindings"]
        if isinstance(item, dict)
    }


def _binding_model_item(
    binding: dict[str, object],
    frozen: dict[str, object],
) -> dict[str, object]:
    return {
        "fact_ref": _scope_fact_ref_by_id(frozen)[str(binding["fact_id"])],
        "scope_ids": copy.deepcopy(binding.get("scope_ids")),
        "role": copy.deepcopy(binding.get("role")),
    }


def _scope_model_boundary_manifest(
    manifest: dict[str, object],
    frozen: dict[str, object],
) -> dict[str, object]:
    fact_ref_by_id = _scope_fact_ref_by_id(frozen)
    return {
        "manifest_version": manifest["manifest_version"],
        "fingerprint": manifest["fingerprint"],
        "source_outline_fingerprint": manifest["source_outline_fingerprint"],
        "boundaries": [
            {
                "boundary_id": copy.deepcopy(boundary.get("boundary_id")),
                "label": copy.deepcopy(boundary.get("label")),
                "decision": copy.deepcopy(boundary.get("decision")),
                "parent_boundary_id": copy.deepcopy(
                    boundary.get("parent_boundary_id")
                ),
                "membership_relation_refs": copy.deepcopy(
                    boundary.get("membership_relation_ids") or []
                ),
                "membership_fact_refs": [
                    fact_ref_by_id[str(fact_id)]
                    for fact_id in boundary.get("membership_fact_ids") or []
                ],
                "support": [
                    {
                        "signal": copy.deepcopy(support.get("signal")),
                        "fact_refs": [
                            fact_ref_by_id[str(fact_id)]
                            for fact_id in support.get("fact_ids") or []
                        ],
                    }
                    for support in boundary.get("support") or []
                    if isinstance(support, dict)
                ],
            }
            for boundary in manifest["boundaries"]
            if isinstance(boundary, dict)
        ],
    }


def test_selection_prompt_has_single_writer_response_grammar() -> None:
    prompt = build_requirement_scope_boundary_selection_prompt()
    response_grammar = next(
        line for line in prompt.splitlines() if line.startswith("RESPONSE := ")
    )

    assert re.findall(r'"([a-z_]+)":', response_grammar) == [
        "boundary_records"
    ]
    assert "complete immutable" in prompt
    assert "omitted evidence and evidence_verified fields" in prompt
    assert "globally minimal responsibility boundaries" in prompt
    assert "smallest sufficient exact fact index" in prompt
    assert "Do not output source topology, membership evidence" in prompt
    assert "UI entry existence, click validity, P0, required, and testable" in prompt
    assert "scope fence" in prompt
    assert "frozen_source_outline" not in prompt
    assert "source_evidence_catalog" not in prompt


def test_selection_model_wire_lowers_to_frozen_selection_without_membership() -> None:
    frozen = _normalized_fact_ledger()
    lowered = normalize_requirement_scope_boundary_selection_model_response(
        _boundary_selection_model_response(frozen),
        frozen,
    )
    expected_boundaries = copy.deepcopy(_ledger()["boundaries"])
    for boundary in expected_boundaries:
        boundary["membership_relation_ids"] = []
        boundary["membership_fact_ids"] = []
    canonical = normalize_requirement_scope_boundary_selection(
        {"boundaries": expected_boundaries},
        frozen,
    )

    assert lowered["valid"] is True, lowered["errors"]
    assert lowered["boundaries"] == canonical["boundaries"]
    assert lowered["selection_version"] == (
        "requirement-scope-boundary-selection-v1"
    )
    assert lowered["fingerprint"] == (
        fingerprint_requirement_scope_boundary_selection(lowered)
    )
    assert lowered["fingerprint"] == canonical["fingerprint"]
    assert all(
        not boundary["membership_relation_ids"]
        and not boundary["membership_fact_ids"]
        for boundary in lowered["boundaries"]
    )


def test_selection_model_wire_rejects_membership_fields() -> None:
    frozen = _normalized_fact_ledger()
    payload = _boundary_selection_model_response(frozen)
    payload["boundary_records"][1]["membership_relation_refs"] = ["R001"]
    payload["boundary_records"][1]["membership_fact_refs"] = ["F002"]

    result = normalize_requirement_scope_boundary_selection_model_response(
        payload,
        frozen,
    )

    assert result["valid"] is False
    assert "boundary_record_field_unknown" in {
        item["code"] for item in result["errors"]
    }


def test_selection_model_wire_rejects_duplicate_support_fact_ref() -> None:
    frozen = _normalized_fact_ledger()
    payload = _boundary_selection_model_response(frozen)
    refs = payload["boundary_records"][1]["support"][0]["fact_refs"]
    refs.append(refs[0])

    result = normalize_requirement_scope_boundary_selection_model_response(
        payload,
        frozen,
    )

    assert result["valid"] is False
    assert "boundary_support_fact_ref_duplicate" in {
        item["code"] for item in result["errors"]
    }


def test_selection_model_wire_rejects_old_boundary_shape() -> None:
    frozen = _normalized_fact_ledger()
    payload = _boundary_selection_model_response(frozen)
    payload["boundary_records"][1]["fact_roles"] = []
    del payload["boundary_records"][1]["support"]

    result = normalize_requirement_scope_boundary_selection_model_response(
        payload,
        frozen,
    )

    codes = {item["code"] for item in result["errors"]}
    assert result["valid"] is False
    assert "boundary_record_field_unknown" in codes
    assert "boundary_record_field_missing" in codes


@pytest.mark.parametrize(
    ("fact_ref", "expected_code"),
    [
        ("FACT_PARENT_MEMBERSHIP", "boundary_support_fact_ref_invalid"),
        (" F002", "boundary_support_fact_ref_invalid"),
        ("f002", "boundary_support_fact_ref_invalid"),
        ("F999", "boundary_support_fact_ref_unknown"),
    ],
)
def test_selection_model_wire_requires_exact_known_support_fact_ref(
    fact_ref: str,
    expected_code: str,
) -> None:
    frozen = _normalized_fact_ledger()
    payload = _boundary_selection_model_response(frozen)
    payload["boundary_records"][1]["support"][0]["fact_refs"][0] = fact_ref

    result = normalize_requirement_scope_boundary_selection_model_response(
        payload,
        frozen,
    )

    assert result["valid"] is False
    assert expected_code in {item["code"] for item in result["errors"]}


def test_selection_model_wire_rejects_derived_active_decision_values() -> None:
    frozen = _normalized_fact_ledger()
    payload = _boundary_selection_model_response(frozen)
    payload["boundary_records"][0]["decision"] = "in_scope_parent"

    result = normalize_requirement_scope_boundary_selection_model_response(
        payload,
        frozen,
    )

    assert result["valid"] is False
    assert "boundary_model_decision_invalid" in {
        item["code"] for item in result["errors"]
    }


def test_selection_model_wire_rejects_old_flat_manifest_response() -> None:
    result = normalize_requirement_scope_boundary_selection_model_response(
        {"boundaries": copy.deepcopy(_ledger()["boundaries"])},
        _normalized_fact_ledger(),
    )
    codes = {item["code"] for item in result["errors"]}

    assert result["valid"] is False
    assert "scope_boundary_model_response_field_unknown" in codes
    assert "scope_boundary_model_response_field_missing" in codes
    assert result["fingerprint"] == ""


def test_membership_model_wire_lowers_scalar_proof_to_manifest_v3() -> None:
    frozen = _normalized_fact_ledger()
    selection = _boundary_selection(frozen)

    result = normalize_requirement_scope_membership_model_response(
        _membership_model_response(),
        frozen,
        selection,
        CATALOG,
    )

    assert result["valid"] is True, result["errors"]
    assert result["manifest_version"] == "requirement-scope-boundary-manifest-v3"
    child = next(
        item
        for item in result["boundaries"]
        if item["boundary_id"] == "SCOPE_MESSAGE"
    )
    assert child["membership_relation_ids"] == ["R001"]
    assert child["membership_fact_ids"] == []
    assert (
        len(child["membership_relation_ids"])
        + len(child["membership_fact_ids"])
        == 1
    )
    assert result["diagnostics"]["membership_assignment_count"] == 1
    assert result["diagnostics"]["membership_assignment_fingerprint"]


def test_membership_model_wire_requires_exact_non_root_coverage() -> None:
    frozen = _normalized_fact_ledger()
    selection = _boundary_selection(frozen)

    payloads_and_codes = [
        ({"membership_assignments": []}, "membership_assignment_boundary_missing"),
        (
            {
                "membership_assignments": [
                    *_membership_model_response()["membership_assignments"],
                    {
                        "boundary_id": "SCOPE_CURRENT",
                        "membership_kind": "none",
                        "membership_ref": "",
                    },
                ]
            },
            "membership_assignment_root_forbidden",
        ),
        (
            {
                "membership_assignments": [
                    *_membership_model_response()["membership_assignments"],
                    {
                        "boundary_id": "SCOPE_UNKNOWN",
                        "membership_kind": "none",
                        "membership_ref": "",
                    },
                ]
            },
            "membership_assignment_boundary_unknown",
        ),
        (
            {
                "membership_assignments": [
                    *_membership_model_response()["membership_assignments"],
                    *_membership_model_response()["membership_assignments"],
                ]
            },
            "membership_assignment_boundary_duplicate",
        ),
    ]
    for payload, expected_code in payloads_and_codes:
        result = normalize_requirement_scope_membership_model_response(
            payload,
            frozen,
            selection,
            CATALOG,
        )
        assert result["valid"] is False
        assert expected_code in {item["code"] for item in result["errors"]}


@pytest.mark.parametrize(
    ("kind", "membership_ref", "expected_code"),
    [
        ("source_relation", " R001", "membership_assignment_relation_ref_invalid"),
        ("source_relation", "r001", "membership_assignment_relation_ref_invalid"),
        ("source_relation", "R999", "membership_assignment_relation_ref_unknown"),
        ("explicit_fact", "FACT_PARENT_MEMBERSHIP", "membership_assignment_fact_ref_invalid"),
        ("explicit_fact", " F002", "membership_assignment_fact_ref_invalid"),
        ("explicit_fact", "F999", "membership_assignment_fact_ref_unknown"),
        ("none", "R001", "membership_assignment_none_ref_not_empty"),
        ("removed_kind", "", "membership_assignment_kind_invalid"),
    ],
)
def test_membership_model_wire_requires_exact_kind_and_ref(
    kind: str,
    membership_ref: str,
    expected_code: str,
) -> None:
    frozen = _normalized_fact_ledger()
    result = normalize_requirement_scope_membership_model_response(
        _membership_model_response(
            membership_kind=kind,
            membership_ref=membership_ref,
        ),
        frozen,
        _boundary_selection(frozen),
        CATALOG,
    )

    assert result["valid"] is False
    assert expected_code in {item["code"] for item in result["errors"]}


def test_membership_model_wire_allows_one_explicit_fact_proof() -> None:
    frozen = _normalized_fact_ledger()
    fact_ref = _scope_fact_ref_by_id(frozen)["FACT_PARENT_MEMBERSHIP"]

    result = normalize_requirement_scope_membership_model_response(
        _membership_model_response(
            membership_kind="explicit_fact",
            membership_ref=fact_ref,
        ),
        frozen,
        _boundary_selection(frozen),
        CATALOG,
    )

    assert result["valid"] is True, result["errors"]
    child = result["boundaries"][1]
    assert child["membership_relation_ids"] == []
    assert child["membership_fact_ids"] == ["FACT_PARENT_MEMBERSHIP"]


def test_membership_model_wire_rejects_active_none() -> None:
    frozen = _normalized_fact_ledger()
    result = normalize_requirement_scope_membership_model_response(
        _membership_model_response(membership_kind="none", membership_ref=""),
        frozen,
        _boundary_selection(frozen),
        CATALOG,
    )

    assert result["valid"] is False
    assert "active_child_membership_missing" in {
        item["code"] for item in result["errors"]
    }


def test_membership_model_wire_allows_inactive_none() -> None:
    frozen = _normalized_fact_ledger()
    boundaries = copy.deepcopy(_ledger()["boundaries"])
    for boundary in boundaries:
        boundary["membership_relation_ids"] = []
        boundary["membership_fact_ids"] = []
    boundaries.append(
        {
            "boundary_id": "SCOPE_INACTIVE",
            "label": "Inactive context",
            "decision": "not_scope",
            "parent_boundary_id": "SCOPE_CURRENT",
            "membership_relation_ids": [],
            "membership_fact_ids": [],
            "support": [],
        }
    )
    selection = normalize_requirement_scope_boundary_selection(
        {"boundaries": boundaries},
        frozen,
    )
    assert selection["valid"] is True, selection["errors"]

    result = normalize_requirement_scope_membership_model_response(
        {
            "membership_assignments": [
                *_membership_model_response()["membership_assignments"],
                {
                    "boundary_id": "SCOPE_INACTIVE",
                    "membership_kind": "none",
                    "membership_ref": "",
                },
            ]
        },
        frozen,
        selection,
        CATALOG,
    )

    assert result["valid"] is True, result["errors"]
    assert result["diagnostics"]["membership_none_count"] == 1


def test_membership_model_wire_cannot_mutate_frozen_selection() -> None:
    frozen = _normalized_fact_ledger()
    payload = _membership_model_response()
    payload["membership_assignments"][0]["label"] = "mutated"
    payload["boundaries"] = []

    result = normalize_requirement_scope_membership_model_response(
        payload,
        frozen,
        _boundary_selection(frozen),
        CATALOG,
    )

    codes = {item["code"] for item in result["errors"]}
    assert result["valid"] is False
    assert "membership_assignment_field_unknown" in codes
    assert "scope_membership_model_response_field_unknown" in codes


def test_binding_prompt_limits_output_ownership_to_target_facts() -> None:
    prompt = build_requirement_scope_binding_prompt()
    response_grammar = next(
        line for line in prompt.splitlines() if line.startswith("RESPONSE := ")
    )

    assert prompt.count(strict_json_output_contract_prompt()) == 1
    assert re.findall(r'"([a-z_]+)":', response_grammar) == ["fact_bindings"]
    assert "complete immutable compact projection" in prompt
    assert "omitted evidence and evidence_verified fields" in prompt
    assert "mutually exclusive output ownership set" in prompt
    assert "Do not create, rename, merge, split, reclassify, or reparent" in prompt
    assert "exactly one fact_binding for every target_fact_ref" in prompt
    assert "membership_fact_refs and support.fact_refs" in prompt
    assert (
        "role: external_context|non_scope_context|owned_requirement|shared_requirement"
        in prompt
    )
    assert "parent_membership" not in prompt
    assert "boundary_support" not in prompt
    assert "Never output boundaries" in prompt
    assert "source_evidence_catalog" not in prompt


def test_boundary_manifest_is_compact_frozen_and_fact_bound() -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)

    assert manifest["manifest_version"] == "requirement-scope-boundary-manifest-v3"
    assert manifest["fact_ledger_fingerprint"] == frozen["fingerprint"]
    assert manifest["fingerprint"] == (
        fingerprint_requirement_scope_boundary_manifest(manifest)
    )
    assert set(manifest) == {
        "manifest_version",
        "fact_ledger_version",
        "fact_ledger_fingerprint",
        "source_outline_fingerprint",
        "boundaries",
        "valid",
        "errors",
        "fingerprint",
        "diagnostics",
    }
    assert "evidence_facts" not in manifest
    assert "fact_bindings" not in manifest


def test_selection_model_wire_rejects_all_model_injected_fields() -> None:
    payload = _boundary_selection_model_response()
    payload["fact_bindings"] = []
    payload["boundary_records"][0]["model_override"] = "injected"

    selection = normalize_requirement_scope_boundary_selection_model_response(
        payload,
        _normalized_fact_ledger(),
    )
    codes = {item["code"] for item in selection["errors"]}

    assert selection["valid"] is False
    assert selection["fingerprint"] == ""
    assert "scope_boundary_model_response_field_unknown" in codes
    assert "boundary_record_field_unknown" in codes


def test_boundary_label_over_limit_fails_closed_without_silent_truncation() -> None:
    payload = _boundary_selection_model_response()
    payload["boundary_records"][0]["label"] = "边" * 161

    selection = normalize_requirement_scope_boundary_selection_model_response(
        payload,
        _normalized_fact_ledger(),
    )
    codes = {item["code"] for item in selection["errors"]}

    assert selection["valid"] is False
    assert selection["fingerprint"] == ""
    assert "boundary_label_exceeds_limit" in codes
    assert (
        "at most 160 characters"
        in build_requirement_scope_boundary_selection_prompt()
    )


def test_selection_membership_and_binding_inputs_use_distinct_frozen_contracts() -> None:
    frozen = _normalized_fact_ledger()
    selection = _boundary_selection(frozen)
    manifest = _boundary_manifest(frozen)
    fact_ids = _frozen_fact_ids(frozen)

    selection_input = json.loads(
        build_requirement_scope_boundary_selection_user_input(frozen)
    )
    membership_input = json.loads(
        build_requirement_scope_membership_user_input(
            frozen,
            selection,
            source_evidence_catalog=CATALOG,
        )
    )
    binding_input = json.loads(
        build_requirement_scope_binding_user_input(
            frozen,
            manifest,
            [fact_ids[0]],
            source_evidence_catalog=CATALOG,
        )
    )

    assert selection_input["input_type"] == (
        "current_requirement_scope_boundary_selection_compile"
    )
    assert membership_input["input_type"] == (
        "current_requirement_scope_membership_compile"
    )
    assert binding_input["input_type"] == (
        "current_requirement_scope_binding_compile"
    )
    projected_fact_table = _scope_model_fact_table(frozen)
    assert selection_input["input_version"] == "1"
    assert membership_input["input_version"] == "2"
    assert binding_input["input_version"] == "7"
    assert binding_input["recompile_contract_feedback"] == []
    assert all(
        item["frozen_fact_table"] == projected_fact_table
        for item in (selection_input, membership_input, binding_input)
    )
    assert all(
        "frozen_facts" not in item
        for item in (selection_input, membership_input, binding_input)
    )
    assert projected_fact_table["schema"] == [
        "fact_ref",
        "fact_kind",
        "statement",
        "requirement_level",
        "priority",
        "testability",
        "confidence",
    ]
    assert all(
        len(row) == len(projected_fact_table["schema"])
        for row in projected_fact_table["rows"]
    )
    assert all(
        item["fact_ledger_fingerprint"] == frozen["fingerprint"]
        for item in (selection_input, membership_input, binding_input)
    )
    assert "frozen_source_outline" not in selection_input
    assert "frozen_boundary_selection" not in selection_input
    assert membership_input["frozen_boundary_selection"]["fingerprint"] == (
        selection["fingerprint"]
    )
    assert all(
        set(boundary) == {
            "boundary_id",
            "label",
            "decision",
            "parent_boundary_id",
            "support",
        }
        for boundary in membership_input["frozen_boundary_selection"][
            "boundaries"
        ]
    )
    assert binding_input["target_fact_refs"] == [
        _scope_fact_ref_by_id(frozen)[fact_ids[0]]
    ]
    assert "target_fact_ids" not in binding_input
    assert binding_input["target_fact_fingerprint"]
    assert binding_input["frozen_boundary_manifest"] == (
        _scope_model_boundary_manifest(manifest, frozen)
    )
    assert all(
        "membership_fact_ids" not in boundary
        for boundary in binding_input["frozen_boundary_manifest"]["boundaries"]
    )
    assert all(
        "membership_relation_ids" not in boundary
        for boundary in binding_input["frozen_boundary_manifest"]["boundaries"]
    )
    assert binding_input["target_topology_usage"] == [
        {
                "fact_ref": _scope_fact_ref_by_id(frozen)[fact_ids[0]],
                "explicit_membership_edges": [],
                "support_scope_ids": ["SCOPE_CURRENT"],
            }
        ]
    assert membership_input["frozen_source_outline"] == (
        binding_input["frozen_source_outline"]
    )
    assert all(
        "source_evidence_catalog" not in item
        for item in (selection_input, membership_input, binding_input)
    )


def test_source_outline_projects_exact_ordered_relations_without_inventing_facts() -> None:
    frozen = _normalized_fact_ledger()
    membership_input = json.loads(
        build_requirement_scope_membership_user_input(
            frozen,
            _boundary_selection(frozen),
            source_evidence_catalog=CATALOG,
        )
    )
    outline = membership_input["frozen_source_outline"]

    assert outline["outline_version"] == "requirement-source-outline-v1"
    assert outline["source_catalog_fingerprint"] == CATALOG_FINGERPRINT
    assert outline["group_count"] == 1
    assert outline["relation_count"] == 2
    assert outline["groups"] == [
        {
            "group_ref": "G001",
            "parent": {
                "source_ref": "S001",
                "source_text": "2. 功能分区",
                "source_disposition": "fact_backed",
                "anchored_fact_refs": [
                    _scope_fact_ref_by_id(frozen)["FACT_PARENT_MEMBERSHIP"]
                ],
            },
            "members": [
                {
                    "relation_ref": "R001",
                    "source_ref": "S002",
                    "source_text": "1. 消息区",
                    "source_disposition": "context_only",
                    "anchored_fact_refs": [],
                },
                {
                    "relation_ref": "R002",
                    "source_ref": "S003",
                    "source_text": "2. 交流区",
                    "source_disposition": "context_only",
                    "anchored_fact_refs": [],
                },
            ],
        }
    ]
    assert outline["fingerprint"]


def test_source_outline_rejects_relations_without_preceding_parent_source() -> None:
    rootless_catalog = [
        {"ref": "EV_111111111111", "quote": "1. 官方区"},
        {"ref": "EV_222222222222", "quote": "2. 反馈区"},
    ]
    frozen = _normalized_fact_ledger(
        [
            _fact(
                "FACT_OFFICIAL_PARTITION",
                "官方区负责发布平台公告。",
                "EV_111111111111",
            ),
            _fact(
                "FACT_FEEDBACK_PARTITION",
                "反馈区负责接收用户问题反馈。",
                "EV_222222222222",
            ),
        ],
        source_catalog=rootless_catalog,
    )
    selection = normalize_requirement_scope_boundary_selection(
        {
            "boundaries": [
                {
                    "boundary_id": "SCOPE_COMMUNITY",
                    "label": "社区功能",
                    "decision": "in_scope_parent",
                    "parent_boundary_id": "",
                    "membership_relation_ids": [],
                    "membership_fact_ids": [],
                    "support": [
                        {
                            "signal": "purpose",
                            "fact_ids": ["FACT_OFFICIAL_PARTITION"],
                        }
                    ],
                },
                {
                    "boundary_id": "SCOPE_FEEDBACK",
                    "label": "反馈区",
                    "decision": "in_scope_leaf",
                    "parent_boundary_id": "SCOPE_COMMUNITY",
                    "membership_relation_ids": [],
                    "membership_fact_ids": [],
                    "support": [
                        {
                            "signal": "content_ownership",
                            "fact_ids": ["FACT_FEEDBACK_PARTITION"],
                        }
                    ],
                },
            ]
        },
        frozen,
    )
    assert selection["valid"] is True, selection["errors"]

    membership_input = json.loads(
        build_requirement_scope_membership_user_input(
            frozen,
            selection,
            source_evidence_catalog=rootless_catalog,
        )
    )
    outline = membership_input["frozen_source_outline"]

    assert outline["group_count"] == 0
    assert outline["relation_count"] == 0
    assert outline["groups"] == []

    manifest = normalize_requirement_scope_membership_model_response(
        {
            "membership_assignments": [
                {
                    "boundary_id": "SCOPE_FEEDBACK",
                    "membership_kind": "source_relation",
                    "membership_ref": "R002",
                }
            ]
        },
        frozen,
        selection,
        rootless_catalog,
    )
    assert manifest["valid"] is False
    assert "membership_assignment_relation_ref_unknown" in {
        item["code"] for item in manifest["errors"]
    }


def test_source_catalog_must_match_frozen_a1_fingerprint_exactly() -> None:
    frozen = _normalized_fact_ledger()
    tampered_catalog = copy.deepcopy(CATALOG)
    tampered_catalog[1]["quote"] = "1. 被篡改的来源成员"

    with pytest.raises(ValueError, match="与 A1 来源指纹不匹配"):
        build_requirement_scope_membership_user_input(
            frozen,
            _boundary_selection(frozen),
            source_evidence_catalog=tampered_catalog,
        )


def test_each_binding_shard_sees_same_full_facts_and_frozen_manifest() -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)
    fact_ids = _frozen_fact_ids(frozen)
    inputs = [
        json.loads(
            build_requirement_scope_binding_user_input(
                frozen,
                manifest,
                [fact_id],
                source_evidence_catalog=CATALOG,
            )
        )
        for fact_id in fact_ids
    ]

    projected_fact_table = _scope_model_fact_table(frozen)
    assert all(
        item["frozen_fact_table"] == projected_fact_table for item in inputs
    )
    assert len(
        {
            item["frozen_boundary_manifest"]["fingerprint"]
            for item in inputs
        }
    ) == 1
    assert len({item["target_fact_fingerprint"] for item in inputs}) == len(
        fact_ids
    )


def test_binding_input_rejects_tampered_manifest_before_model_call() -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)
    manifest["boundaries"][0]["label"] = "篡改边界"

    with pytest.raises(ValueError, match="boundary_manifest 指纹不匹配"):
        build_requirement_scope_binding_user_input(
            frozen,
            manifest,
            [_frozen_fact_ids(frozen)[0]],
            source_evidence_catalog=CATALOG,
        )


def test_binding_input_rejects_unknown_duplicate_and_noncanonical_targets() -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)
    fact_ids = _frozen_fact_ids(frozen)

    invalid_targets = [
        ["FACT_UNKNOWN"],
        [fact_ids[0], fact_ids[0]],
        list(reversed(fact_ids)),
    ]
    for target_ids in invalid_targets:
        with pytest.raises(ValueError, match="target_fact_ids 无效"):
            build_requirement_scope_binding_user_input(
                frozen,
                manifest,
                target_ids,
                source_evidence_catalog=CATALOG,
            )


def test_binding_shard_accepts_exact_target_and_rejects_partial_or_extra_output() -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)
    fact_ids = _frozen_fact_ids(frozen)
    bindings = _binding_by_fact_id()
    target = fact_ids[0]
    other = fact_ids[1]

    valid = normalize_requirement_scope_binding_shard(
        {
            "fact_bindings": [
                _binding_model_item(bindings[target], frozen)
            ]
        },
        frozen,
        manifest,
        [target],
        CATALOG,
    )
    assert valid["valid"] is True, valid["errors"]
    assert valid["shard_version"] == "requirement-scope-binding-shard-v2"
    assert valid["target_fact_fingerprint"]
    assert valid["fingerprint"]

    missing = normalize_requirement_scope_binding_shard(
        {"fact_bindings": []},
        frozen,
        manifest,
        [target],
        CATALOG,
    )
    assert "scope_binding_target_missing" in {
        item["code"] for item in missing["errors"]
    }

    extra = normalize_requirement_scope_binding_shard(
        {
            "fact_bindings": [
                _binding_model_item(bindings[target], frozen),
                _binding_model_item(bindings[other], frozen),
            ]
        },
        frozen,
        manifest,
        [target],
        CATALOG,
    )
    assert "scope_binding_fact_not_target" in {
        item["code"] for item in extra["errors"]
    }


def test_binding_shard_rejects_boundary_mutation_and_scope_unknown() -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)
    target = _frozen_fact_ids(frozen)[0]
    binding = _binding_by_fact_id()[target]
    binding["scope_ids"] = ["SCOPE_UNKNOWN"]

    normalized = normalize_requirement_scope_binding_shard(
        {
            "fact_bindings": [_binding_model_item(binding, frozen)],
            "boundaries": copy.deepcopy(manifest["boundaries"]),
        },
        frozen,
        manifest,
        [target],
        CATALOG,
    )
    codes = {item["code"] for item in normalized["errors"]}

    assert normalized["valid"] is False
    assert "scope_binding_response_field_unknown" in codes
    assert "fact_binding_scope_unknown" in codes


def test_binding_shard_projects_known_active_ids_from_non_scope_context() -> None:
    facts = _base_facts()
    facts[0]["requirement_level"] = "optional"
    facts[0]["testability"] = "non_testable"
    frozen = _normalized_fact_ledger(facts)
    manifest = _boundary_manifest(frozen)
    target = "FACT_PARENT_MEMBERSHIP"
    fact_ref = _scope_fact_ref_by_id(frozen)[target]

    result = normalize_requirement_scope_binding_shard(
        {
            "fact_bindings": [
                {
                    "fact_ref": fact_ref,
                    "scope_ids": ["SCOPE_CURRENT"],
                    "role": "non_scope_context",
                }
            ]
        },
        frozen,
        manifest,
        [target],
        CATALOG,
    )

    assert result["valid"] is True, result["errors"]
    assert result["fact_bindings"] == [
        {
            "fact_id": target,
            "scope_ids": [],
            "role": "non_scope_context",
        }
    ]
    assert result["diagnostics"][
        "projected_non_scope_context_binding_count"
    ] == 1
    assert result["diagnostics"][
        "projected_non_scope_context_scope_id_count"
    ] == 1


def test_non_scope_projection_keeps_unknown_ids_and_testability_guards() -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)
    target = "FACT_PARENT_MEMBERSHIP"
    fact_ref = _scope_fact_ref_by_id(frozen)[target]

    unknown = normalize_requirement_scope_binding_shard(
        {
            "fact_bindings": [
                {
                    "fact_ref": fact_ref,
                    "scope_ids": ["SCOPE_UNKNOWN"],
                    "role": "non_scope_context",
                }
            ]
        },
        frozen,
        manifest,
        [target],
        CATALOG,
    )
    projected_required = normalize_requirement_scope_binding_shard(
        {
            "fact_bindings": [
                {
                    "fact_ref": fact_ref,
                    "scope_ids": ["SCOPE_CURRENT"],
                    "role": "non_scope_context",
                }
            ]
        },
        frozen,
        manifest,
        [target],
        CATALOG,
    )

    assert "fact_binding_scope_unknown" in {
        item["code"] for item in unknown["errors"]
    }
    assert unknown["fact_bindings"][0]["scope_ids"] == ["SCOPE_UNKNOWN"]
    assert "testable_fact_without_scope_or_external_context" in {
        item["code"] for item in projected_required["errors"]
    }


def test_scope_ledger_preserves_bindings_beyond_previous_global_limit() -> None:
    fact_count = 321
    catalog = [
        {
            "ref": f"EV_{index:012x}",
            "quote": f"工作台支持处理第 {index} 项独立业务要求",
        }
        for index in range(1, fact_count + 1)
    ]
    facts = [
        {
            **_fact(
                f"FACT_SCALE_{index:04d}",
                item["quote"],
                item["ref"],
            ),
            "anchor_evidence_ref": item["ref"],
        }
        for index, item in enumerate(catalog, start=1)
    ]
    frozen = _normalized_fact_ledger(facts, source_catalog=catalog)
    boundary = {
        "boundary_id": "SCOPE_WORKSPACE",
        "label": "业务工作台",
        "decision": "in_scope_leaf",
        "parent_boundary_id": "",
        "membership_relation_ids": [],
        "membership_fact_ids": [],
        "support": [
            {
                "signal": "purpose",
                "fact_ids": [str(facts[0]["fact_id"])],
            }
        ],
    }
    result = normalize_requirement_scope_ledger(
        {
            "boundaries": [boundary],
            "fact_bindings": [
                {
                    "fact_id": str(fact["fact_id"]),
                    "scope_ids": ["SCOPE_WORKSPACE"],
                    "role": "owned_requirement",
                }
                for fact in facts
            ],
        },
        normalized_fact_ledger=frozen,
        source_evidence_catalog=catalog,
    )

    assert result["valid"] is True, result["errors"]
    assert len(result["fact_bindings"]) == fact_count
    assert result["diagnostics"]["fact_binding_count"] == fact_count


@pytest.mark.parametrize("old_role", ["parent_membership", "boundary_support"])
def test_binding_shard_rejects_removed_topology_roles(old_role: str) -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)
    binding = copy.deepcopy(_binding_by_fact_id()["FACT_PARENT_MEMBERSHIP"])
    binding["role"] = old_role
    result = normalize_requirement_scope_binding_shard(
        {
            "fact_bindings": [
                _binding_model_item(binding, frozen)
            ]
        },
        frozen,
        manifest,
        ["FACT_PARENT_MEMBERSHIP"],
        CATALOG,
    )

    assert result["valid"] is False
    assert "fact_binding_role_invalid" in {
        item["code"] for item in result["errors"]
    }


def test_merged_shards_publish_scope_ledger_v3() -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)
    fact_ids = _frozen_fact_ids(frozen)
    binding_by_id = _binding_by_fact_id()
    shard_results = [
        normalize_requirement_scope_binding_shard(
            {
                "fact_bindings": [
                    _binding_model_item(binding_by_id[fact_id], frozen)
                ]
            },
            frozen,
            manifest,
            [fact_id],
            CATALOG,
        )
        for fact_id in fact_ids
    ]
    assert all(item["valid"] is True for item in shard_results)

    final = normalize_requirement_scope_ledger(
        {
            "boundaries": copy.deepcopy(manifest["boundaries"]),
            "fact_bindings": [
                copy.deepcopy(binding)
                for shard in shard_results
                for binding in shard["fact_bindings"]
            ],
        },
        normalized_fact_ledger=frozen,
        source_evidence_catalog=CATALOG,
    )

    assert final["valid"] is True, final["errors"]
    assert final["ledger_version"] == "requirement-scope-ledger-v3"
    assert final["evidence_facts"] == frozen["evidence_facts"]
    assert len(final["fact_bindings"]) == len(fact_ids)


@pytest.mark.parametrize(
    ("fact_ref", "expected_code"),
    [
        ("FACT_PARENT_MEMBERSHIP", "scope_binding_fact_ref_invalid"),
        (" F002", "scope_binding_fact_ref_invalid"),
        ("f002", "scope_binding_fact_ref_invalid"),
        ("F999", "scope_binding_fact_ref_unknown"),
    ],
)
def test_binding_model_wire_requires_exact_known_fact_ref(
    fact_ref: str,
    expected_code: str,
) -> None:
    frozen = _normalized_fact_ledger()
    manifest = _boundary_manifest(frozen)
    target = "FACT_PARENT_MEMBERSHIP"
    binding = _binding_model_item(_binding_by_fact_id()[target], frozen)
    binding["fact_ref"] = fact_ref

    result = normalize_requirement_scope_binding_shard(
        {"fact_bindings": [binding]},
        frozen,
        manifest,
        [target],
        CATALOG,
    )

    assert result["valid"] is False
    assert expected_code in {item["code"] for item in result["errors"]}
