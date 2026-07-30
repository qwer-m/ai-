import copy

from modules.test_generation_components.control.project_profile_activation import build_project_profile
from modules.test_generation_components.control.semantic_contract import (
    canonicalize_requirement_semantic_candidate,
    empty_requirement_semantic_contract,
    normalize_case_semantic,
    normalize_requirement_semantic_contract,
    normalize_typed_states,
    validate_case_semantic_contract,
)
from modules.test_generation_components.postprocess.case_contract import project_persistable_cases
from modules.test_generation_components.postprocess.execution_plan_case_state import (
    typed_state_contract_conflicts,
)
from modules.test_generation_components.postprocess.json_normalizer import normalize_json_structure
from modules.test_generation_components.postprocess.module_contract import (
    apply_functional_module_phase,
    case_matches_functional_phase,
    enforce_functional_module_contract,
)
from modules.test_generation_components.postprocess.streaming_case_source_metadata import (
    apply_case_source_metadata,
)
from modules.test_generation_components.prompting.structured_context import build_structured_prompt_context


REQUIREMENT = """
# 内容工作台
用户在内容工作台提交内容。
# 消息中心
内容提交完成后，系统事件在消息中心创建通知。
"""


def test_typed_state_normalization_preserves_declared_fact_identity() -> None:
    states = normalize_typed_states(
        [
            {
                "entity": "notification",
                "state": "created",
                "source": "current_stage",
                "scope": "entity",
                "polarity": "positive",
                "temporal": "after_case",
                "confidence": 0.95,
                "evidence": ["消息中心创建通知"],
                "fact_ids": ["f_notice_created", "f_notice_visible"],
            }
        ],
        source_text="内容提交后，消息中心创建通知并展示通知。",
        state_role="produced",
    )

    assert states[0]["fact_ids"] == ["f_notice_created", "f_notice_visible"]


def test_semantic_candidate_canonicalizes_nested_workflows_before_validation() -> None:
    primary = {"workflow_id": "main", "primary": True, "steps": []}
    secondary = {"workflow_id": "secondary", "primary": False, "steps": []}
    candidate = {
        "semantic_contract_version": "requirement-semantic-v1",
        "functional_architecture": {
            "functional_modules": [],
            "module_interactions": [],
            "workflow_blueprints": [primary, secondary],
        },
    }

    canonical, diagnostics = canonicalize_requirement_semantic_candidate(candidate)

    assert "workflow_blueprints" not in canonical["functional_architecture"]
    assert canonical["workflow_blueprints"] == [primary, secondary]
    assert diagnostics["nested_workflows_promoted"] is True
    assert diagnostics["extra_non_primary_workflow_count"] == 1
    assert "workflow_blueprints" in candidate["functional_architecture"]


def test_semantic_candidate_keeps_root_workflows_on_location_conflict() -> None:
    root_workflow = {"workflow_id": "root", "primary": True, "steps": []}
    nested_workflow = {"workflow_id": "nested", "primary": True, "steps": []}
    candidate = {
        "functional_architecture": {
            "workflow_blueprints": [nested_workflow],
        },
        "workflow_blueprints": [root_workflow],
    }

    canonical, diagnostics = canonicalize_requirement_semantic_candidate(candidate)

    assert canonical["workflow_blueprints"] == [root_workflow]
    assert "workflow_blueprints" not in canonical["functional_architecture"]
    assert diagnostics["workflow_location_conflict"] is True
    assert diagnostics["architecture_empty_collections_added"] == [
        "functional_modules",
        "module_interactions",
    ]


def test_semantic_candidate_orders_unique_primary_before_secondary_flows() -> None:
    secondary = {"workflow_id": "secondary", "primary": False, "steps": []}
    primary = {"workflow_id": "main", "primary": True, "steps": []}
    candidate = {
        "functional_architecture": {
            "functional_modules": [],
            "module_interactions": [],
        },
        "workflow_blueprints": [secondary, primary],
    }

    canonical, diagnostics = canonicalize_requirement_semantic_candidate(candidate)

    assert [
        item["workflow_id"] for item in canonical["workflow_blueprints"]
    ] == ["main", "secondary"]
    assert diagnostics["primary_workflow_reordered"] is True
    assert diagnostics["extra_non_primary_workflow_count"] == 1


def _contract_payload() -> dict:
    return {
        "confidence": 0.91,
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "content",
                    "module_name": "内容工作台",
                    "scope_status": "in_scope",
                    "evidence": ["用户在内容工作台提交内容"],
                    "confidence": 0.95,
                },
                {
                    "module_key": "message",
                    "module_name": "消息中心",
                    "scope_status": "in_scope",
                    "evidence": ["系统事件在消息中心创建通知"],
                    "confidence": 0.94,
                },
            ],
            "module_interactions": [
                {
                    "interaction_id": "content_message_notice",
                    "source_module_key": "content",
                    "target_module_key": "message",
                    "trigger": "内容提交完成",
                    "transferred_entity": "通知",
                    "result_state": "created",
                    "evidence": ["内容提交完成后，系统事件在消息中心创建通知"],
                    "confidence": 0.92,
                },
                {
                    "interaction_id": "invalid_reference",
                    "source_module_key": "content",
                    "target_module_key": "missing",
                    "trigger": "不存在的目标",
                    "evidence": ["内容提交完成后"],
                    "confidence": 0.9,
                },
            ],
        },
    }


def _case_semantic() -> dict:
    return {
        "module_candidates": [
            {
                "module_key": "message",
                "module_name": "消息中心",
                "role": "target",
                "confidence": 0.96,
                "evidence": ["消息中心创建一条内容更新通知"],
            },
            {
                "module_key": "content",
                "module_name": "内容工作台",
                "role": "source",
                "confidence": 0.83,
                "evidence": ["提交内容"],
            },
        ],
        "interaction_ids": ["content_message_notice"],
        "workflow_stage_candidates": [
            {
                "workflow_id": "content_flow",
                "stage_id": "notice_created",
                "stage_kind": "downstream_visibility",
                "confidence": 0.9,
                "evidence": ["消息中心创建一条内容更新通知"],
            }
        ],
        "precondition_states": [
            {
                "entity": "notification_event",
                "state": "ready",
                "source": "system_event",
                "scope": "cross_module",
                "polarity": "positive",
                "temporal": "before_case",
                "evidence": ["系统事件"],
                "confidence": 0.93,
            }
        ],
        "produced_states": [
            {
                "entity": "notification",
                "state": "created",
                "source": "current_stage",
                "scope": "entity",
                "polarity": "positive",
                "temporal": "after_case",
                "evidence": ["创建一条内容更新通知"],
                "confidence": 0.95,
            }
        ],
    }


def _case() -> dict:
    return {
        "id": "TC-001",
        "description": "内容提交后创建消息通知",
        "test_module": "待归类",
        "preconditions": ["系统事件已准备"],
        "steps": ["提交内容", "打开消息中心"],
        "test_input": "有效内容",
        "expected_result": "消息中心创建一条内容更新通知",
        "priority": "P1",
        "_semantic": _case_semantic(),
    }


def _strict_requirement_contract() -> dict:
    return normalize_requirement_semantic_contract(
        _contract_payload(),
        requirement_text=REQUIREMENT,
        workflow_blueprints=[
            {
                "workflow_id": "content_flow",
                "steps": [
                    {
                        "id": "notice_created",
                        "stage_kind": "downstream_visibility",
                        "required_states": copy.deepcopy(
                            _case_semantic()["precondition_states"]
                        ),
                        "produced_states": copy.deepcopy(
                            _case_semantic()["produced_states"]
                        ),
                    }
                ],
            }
        ],
    )


def test_strict_case_semantic_gate_accepts_verified_active_contract() -> None:
    case = _case()
    rejections: list[dict] = []

    normalized = normalize_json_structure(
        [case],
        require_case_semantic_contract=True,
        requirement_semantic_contract=_strict_requirement_contract(),
        semantic_rejections=rejections,
        semantic_source_stage="contract_test",
    )

    assert len(normalized) == 1
    assert normalized[0]["_semantic"]["interaction_ids"] == ["content_message_notice"]
    assert rejections == []


def test_verified_case_state_matches_declared_workflow_state_end_to_end() -> None:
    contract = _strict_requirement_contract()
    case = _case()
    case_text = "\n".join(
        [
            case["description"],
            case["test_module"],
            *case["preconditions"],
            *case["steps"],
            case["test_input"],
            case["expected_result"],
        ]
    )

    validation = validate_case_semantic_contract(
        case["_semantic"],
        case_text=case_text,
        requirement_contract=contract,
    )
    step_contract = contract["workflow_blueprints"][0]["steps"][0]
    normalized_case = {**case, "_semantic": validation["semantic"]}

    assert validation["valid"] is True
    assert validation["semantic"]["produced_states"][0]["source"] == "current_stage"
    assert typed_state_contract_conflicts(
        normalized_case,
        step_meta=step_contract,
    ) == []


def test_strict_case_semantic_gate_rejects_current_stage_precondition() -> None:
    semantic = _case_semantic()
    semantic["precondition_states"][0]["source"] = "current_stage"

    validation = validate_case_semantic_contract(
        semantic,
        case_text="\n".join(
            [
                _case()["description"],
                *_case()["preconditions"],
                *_case()["steps"],
                _case()["expected_result"],
            ]
        ),
        requirement_contract=_strict_requirement_contract(),
    )

    assert validation["valid"] is False
    assert "precondition_state:source_not_allowed_for_precondition" in validation[
        "rejection_reasons"
    ]


def test_strict_case_semantic_gate_rejects_noncanonical_current_step_source() -> None:
    semantic = _case_semantic()
    semantic["produced_states"][0]["source"] = "current_step"

    validation = validate_case_semantic_contract(
        semantic,
        case_text="\n".join(
            [
                _case()["description"],
                *_case()["steps"],
                _case()["expected_result"],
            ]
        ),
        requirement_contract=_strict_requirement_contract(),
    )

    assert validation["valid"] is False
    assert any(
        item.get("item_type") == "produced_state"
        and item.get("reason") == "state_schema_invalid"
        and item.get("invalid_enum_values") == {"source": "current_step"}
        for item in validation["rejected_semantic_items"]
    )


def test_strict_case_semantic_gate_rejects_missing_semantic_but_legacy_normalization_keeps_it() -> None:
    case = _case()
    case.pop("_semantic")
    rejections: list[dict] = []

    strict = normalize_json_structure(
        [case],
        require_case_semantic_contract=True,
        requirement_semantic_contract=_strict_requirement_contract(),
        semantic_rejections=rejections,
    )
    legacy = normalize_json_structure([case])

    assert strict == []
    assert len(legacy) == 1
    assert "_semantic" not in legacy[0]
    assert rejections[0]["rejection_reasons"] == ["semantic_object_missing"]


def test_strict_case_semantic_gate_rejects_invalid_required_arrays_and_empty_modules() -> None:
    semantic = _case_semantic()
    semantic["module_candidates"] = []
    semantic["produced_states"] = "not-an-array"

    validation = validate_case_semantic_contract(
        semantic,
        case_text="\n".join(
            [
                _case()["description"],
                *_case()["preconditions"],
                *_case()["steps"],
                _case()["expected_result"],
            ]
        ),
        requirement_contract=_strict_requirement_contract(),
    )

    assert validation["valid"] is False
    assert "module_candidates:no_verified_candidate" in validation["rejection_reasons"]
    assert "produced_states:collection_not_list" in validation["rejection_reasons"]


def test_strict_case_semantic_gate_rejects_raw_fields_that_normalization_could_fill() -> None:
    case_text = "\n".join(
        [
            _case()["description"],
            _case()["test_module"],
            *_case()["preconditions"],
            *_case()["steps"],
            _case()["test_input"],
            _case()["expected_result"],
        ]
    )
    variants: list[tuple[dict, str, str]] = []

    missing_module_name = copy.deepcopy(_case_semantic())
    missing_module_name["module_candidates"][0].pop("module_name")
    variants.append((missing_module_name, "module_candidate", "module_name"))

    scalar_module_evidence = copy.deepcopy(_case_semantic())
    scalar_module_evidence["module_candidates"][0]["evidence"] = "消息中心创建一条内容更新通知"
    variants.append((scalar_module_evidence, "module_candidate", "evidence"))

    missing_temporal = copy.deepcopy(_case_semantic())
    missing_temporal["precondition_states"][0].pop("temporal")
    variants.append((missing_temporal, "precondition_state", "temporal"))

    scalar_stage_evidence = copy.deepcopy(_case_semantic())
    scalar_stage_evidence["workflow_stage_candidates"][0]["evidence"] = "消息中心创建一条内容更新通知"
    variants.append((scalar_stage_evidence, "workflow_stage_candidate", "evidence"))

    for semantic, item_type, invalid_field in variants:
        validation = validate_case_semantic_contract(
            semantic,
            case_text=case_text,
            requirement_contract=_strict_requirement_contract(),
        )
        assert validation["valid"] is False
        assert any(
            item.get("item_type") == item_type
            and item.get("reason") == "item_schema_invalid"
            and invalid_field in (item.get("missing_or_invalid_fields") or [])
            for item in validation["rejected_semantic_items"]
        )


def test_strict_case_semantic_gate_requires_declared_interaction_for_multiple_modules() -> None:
    semantic = _case_semantic()
    semantic["interaction_ids"] = []

    validation = validate_case_semantic_contract(
        semantic,
        case_text="\n".join(
            [
                _case()["description"],
                *_case()["preconditions"],
                *_case()["steps"],
                _case()["expected_result"],
            ]
        ),
        requirement_contract=_strict_requirement_contract(),
    )

    assert validation["valid"] is False
    assert "interaction_ids:required_for_multiple_modules" in validation["rejection_reasons"]


def test_ambiguous_primary_and_related_modules_do_not_imply_interaction() -> None:
    semantic = _case_semantic()
    semantic["module_candidates"][0]["role"] = "primary"
    semantic["module_candidates"][1]["role"] = "related"
    semantic["interaction_ids"] = []
    semantic["workflow_stage_candidates"] = []

    validation = validate_case_semantic_contract(
        semantic,
        case_text="\n".join(
            [
                _case()["description"],
                *_case()["preconditions"],
                *_case()["steps"],
                _case()["expected_result"],
            ]
        ),
        requirement_contract=_strict_requirement_contract(),
    )

    assert validation["valid"] is True
    assert "interaction_ids:required_for_multiple_modules" not in validation[
        "rejection_reasons"
    ]


def test_generic_evidence_cannot_verify_case_semantics() -> None:
    semantic = _case_semantic()
    semantic["module_candidates"] = [
        {
            "module_key": "message",
            "module_name": "消息中心",
            "role": "primary",
            "confidence": 0.9,
            "evidence": ["页面"],
        }
    ]
    semantic["interaction_ids"] = []
    semantic["workflow_stage_candidates"] = []
    semantic["precondition_states"] = []
    semantic["produced_states"] = []

    validation = validate_case_semantic_contract(
        semantic,
        case_text="打开页面并查看消息中心",
    )

    assert validation["valid"] is False
    assert "module_candidates:no_verified_candidate" in validation["rejection_reasons"]


def test_strict_case_semantic_gate_binds_exact_test_module_as_module_evidence() -> None:
    case = _case()
    case["test_module"] = "消息中心"
    case["_semantic"] = {
        "module_candidates": [
            {
                "module_key": "message",
                "module_name": "消息中心",
                "role": "primary",
                "confidence": 0.9,
                "evidence": ["未出现在用例正文中的模块证据"],
            }
        ],
        "interaction_ids": [],
        "workflow_stage_candidates": [],
        "precondition_states": [],
        "produced_states": [],
    }
    rejections: list[dict] = []

    normalized = normalize_json_structure(
        [case],
        require_case_semantic_contract=True,
        requirement_semantic_contract=_strict_requirement_contract(),
        semantic_rejections=rejections,
    )

    assert len(normalized) == 1
    assert normalized[0]["_semantic"]["module_candidates"][0]["evidence"] == [
        "消息中心"
    ]
    assert rejections == []


def test_strict_case_semantic_gate_does_not_bind_mismatched_test_module() -> None:
    semantic = {
        "module_candidates": [
            {
                "module_key": "message",
                "module_name": "消息中心",
                "role": "primary",
                "confidence": 0.9,
                "evidence": ["未出现在用例正文中的模块证据"],
            }
        ],
        "interaction_ids": [],
        "workflow_stage_candidates": [],
        "precondition_states": [],
        "produced_states": [],
    }

    validation = validate_case_semantic_contract(
        semantic,
        case_text="打开消息列表并检查通知",
        case_test_module="内容工作台",
        requirement_contract=_strict_requirement_contract(),
    )

    assert validation["valid"] is False
    assert "module_candidates:no_verified_candidate" in validation["rejection_reasons"]
    assert any(
        item.get("item_type") == "module_candidate"
        and item.get("reason") == "evidence_unverified"
        for item in validation["rejected_semantic_items"]
    )


def test_strict_case_semantic_gate_rejects_unknown_module_key_with_active_name() -> None:
    semantic = {
        "module_candidates": [
            {
                "module_key": "invented_message",
                "module_name": "消息中心",
                "role": "primary",
                "confidence": 0.9,
                "evidence": ["消息中心"],
            }
        ],
        "interaction_ids": [],
        "workflow_stage_candidates": [],
        "precondition_states": [],
        "produced_states": [],
    }

    validation = validate_case_semantic_contract(
        semantic,
        case_text="消息中心",
        case_test_module="消息中心",
        requirement_contract=_strict_requirement_contract(),
    )

    assert validation["valid"] is False
    assert "module_candidates:no_verified_candidate" in validation["rejection_reasons"]
    assert any(
        item.get("item_type") == "module_candidate"
        and item.get("reason") == "module_identity_not_exact"
        for item in validation["rejected_semantic_items"]
    )


def test_strict_case_semantic_gate_rejects_extra_module_not_bound_by_test_module() -> None:
    semantic = {
        "module_candidates": [
            {
                "module_key": "message",
                "module_name": "消息中心",
                "role": "primary",
                "confidence": 0.9,
                "evidence": ["未出现在用例正文中的消息模块证据"],
            },
            {
                "module_key": "content",
                "module_name": "内容工作台",
                "role": "related",
                "confidence": 0.8,
                "evidence": ["未出现在用例正文中的内容模块证据"],
            },
        ],
        "interaction_ids": [],
        "workflow_stage_candidates": [],
        "precondition_states": [],
        "produced_states": [],
    }

    validation = validate_case_semantic_contract(
        semantic,
        case_text="打开消息列表并检查通知",
        case_test_module="消息中心",
        requirement_contract=_strict_requirement_contract(),
    )

    assert validation["valid"] is False
    assert len(validation["semantic"]["module_candidates"]) == 1
    assert validation["semantic"]["module_candidates"][0]["module_key"] == "message"
    assert any(
        item.get("item_type") == "module_candidate"
        and item.get("identifier") == "content"
        and item.get("reason") == "evidence_unverified"
        for item in validation["rejected_semantic_items"]
    )


def test_requirement_semantic_schema_does_not_accept_architecture_or_collection_aliases() -> None:
    payload = {
        "architecture": _contract_payload()["functional_architecture"],
        "functional_architecture": {
            "modules": _contract_payload()["functional_architecture"]["functional_modules"],
            "interactions": _contract_payload()["functional_architecture"]["module_interactions"],
        },
    }

    contract = normalize_requirement_semantic_contract(payload, requirement_text=REQUIREMENT)

    assert contract["functional_architecture"]["functional_modules"] == []
    assert contract["functional_architecture"]["module_interactions"] == []


def test_requirement_contract_keeps_evidence_and_rejects_invalid_module_reference() -> None:
    contract = normalize_requirement_semantic_contract(
        _contract_payload(),
        requirement_text=REQUIREMENT,
    )

    architecture = contract["functional_architecture"]
    assert [item["module_key"] for item in architecture["functional_modules"]] == ["content", "message"]
    assert [item["interaction_id"] for item in architecture["module_interactions"]] == [
        "content_message_notice"
    ]
    assert architecture["module_interactions"][0]["evidence_verified"] is True


def test_unverified_module_and_interaction_do_not_enter_active_architecture() -> None:
    payload = copy.deepcopy(_contract_payload())
    payload["functional_architecture"]["functional_modules"].append(
        {
            "module_key": "hallucinated_admin",
            "module_name": "Admin console",
            "scope_status": "in_scope",
            "evidence": ["admin console exists"],
            "confidence": 0.99,
        }
    )
    payload["functional_architecture"]["module_interactions"].append(
        {
            "interaction_id": "hallucinated_sync",
            "source_module_key": "content",
            "target_module_key": "message",
            "trigger": "imagined trigger",
            "evidence": ["imagined cross-module synchronization"],
            "confidence": 0.99,
        }
    )

    contract = normalize_requirement_semantic_contract(
        payload,
        requirement_text=REQUIREMENT,
    )
    architecture = contract["functional_architecture"]

    assert [item["module_key"] for item in architecture["functional_modules"]] == [
        "content",
        "message",
    ]
    assert [item["interaction_id"] for item in architecture["module_interactions"]] == [
        "content_message_notice"
    ]
    rejected = {
        (item.get("item_type"), item.get("identifier"), item.get("reason"))
        for item in architecture["rejected_semantic_items"]
    }
    assert (
        "functional_module",
        "hallucinated_admin",
        "evidence_unverified",
    ) in rejected
    assert (
        "module_interaction",
        "hallucinated_sync",
        "evidence_unverified",
    ) in rejected

    profile = build_project_profile(requirement_text=REQUIREMENT, semantic_contract=contract)
    assert "Admin console" not in profile["module_order"]
    assert all(
        item.get("interaction_id") != "hallucinated_sync"
        for item in profile["functional_architecture"]["module_interactions"]
    )


def test_interaction_targeting_out_of_scope_module_is_not_active() -> None:
    payload = copy.deepcopy(_contract_payload())
    payload["functional_architecture"]["functional_modules"].append(
        {
            "module_key": "excluded_event",
            "module_name": "系统事件",
            "scope_status": "out_of_scope",
            "evidence": ["系统事件"],
            "confidence": 0.9,
        }
    )
    payload["functional_architecture"]["module_interactions"].append(
        {
            "interaction_id": "content_to_excluded_event",
            "source_module_key": "content",
            "target_module_key": "excluded_event",
            "trigger": "内容提交完成",
            "evidence": ["内容提交完成后"],
            "confidence": 0.9,
        }
    )

    contract = normalize_requirement_semantic_contract(payload, requirement_text=REQUIREMENT)
    architecture = contract["functional_architecture"]

    assert [item["module_key"] for item in architecture["excluded_modules"]] == [
        "excluded_event"
    ]
    assert all(
        item.get("interaction_id") != "content_to_excluded_event"
        for item in architecture["module_interactions"]
    )
    assert any(
        item.get("identifier") == "content_to_excluded_event"
        and item.get("reason") == "module_reference_not_active"
        for item in architecture["rejected_semantic_items"]
    )


def test_architecture_does_not_synthesize_module_scope_or_interaction_identity() -> None:
    payload = copy.deepcopy(_contract_payload())
    payload["functional_architecture"]["functional_modules"].extend(
        [
            {
                "module_name": "系统事件",
                "scope_status": "in_scope",
                "evidence": ["系统事件"],
                "confidence": 0.9,
            },
            {
                "module_key": "unknown_scope",
                "module_name": "消息中心",
                "scope_status": "unknown",
                "evidence": ["消息中心"],
                "confidence": 0.9,
            },
        ]
    )
    payload["functional_architecture"]["module_interactions"].append(
        {
            "source_module_key": "content",
            "target_module_key": "message",
            "trigger": "内容提交完成",
            "evidence": ["内容提交完成后"],
            "confidence": 0.9,
        }
    )

    contract = normalize_requirement_semantic_contract(payload, requirement_text=REQUIREMENT)
    architecture = contract["functional_architecture"]

    assert [item["module_key"] for item in architecture["functional_modules"]] == [
        "content",
        "message",
    ]
    assert [item["interaction_id"] for item in architecture["module_interactions"]] == [
        "content_message_notice"
    ]
    assert any(
        item.get("item_type") == "functional_module"
        and item.get("reason") == "module_schema_invalid"
        for item in architecture["rejected_semantic_items"]
    )
    assert any(
        item.get("identifier") == "unknown_scope"
        and item.get("reason") == "scope_not_in_scope"
        for item in architecture["rejected_semantic_items"]
    )
    assert any(
        item.get("item_type") == "module_interaction"
        and item.get("reason") == "interaction_schema_invalid"
        for item in architecture["rejected_semantic_items"]
    )


def test_case_semantic_keeps_entity_state_source_and_multiple_module_candidates() -> None:
    semantic = normalize_case_semantic(
        _case_semantic(),
        case_text="提交内容后，系统事件在消息中心创建一条内容更新通知",
    )

    assert [item["role"] for item in semantic["module_candidates"]] == ["target", "source"]
    assert semantic["interaction_ids"] == ["content_message_notice"]
    assert semantic["precondition_states"][0]["source"] == "system_event"
    assert semantic["precondition_states"][0]["entity"] == "notification_event"
    assert semantic["precondition_states"][0]["evidence_verified"] is True


def test_unverified_case_module_candidate_is_diagnostic_only() -> None:
    payload = copy.deepcopy(_case_semantic())
    for candidate in payload["module_candidates"]:
        candidate["evidence"] = ["not present in case"]

    semantic = normalize_case_semantic(payload, case_text="内容提交后创建消息通知")

    assert semantic["module_candidates"] == []
    assert any(
        item.get("item_type") == "module_candidate"
        and item.get("reason") == "evidence_unverified"
        for item in semantic["rejected_semantic_items"]
    )


def test_unverified_workflow_stage_candidate_is_diagnostic_only() -> None:
    payload = copy.deepcopy(_case_semantic())
    payload["workflow_stage_candidates"][0]["evidence"] = ["not present in case"]

    semantic = normalize_case_semantic(
        payload,
        case_text="内容提交完成后，系统事件在消息中心创建通知",
    )

    assert semantic["workflow_stage_candidates"] == []
    assert any(
        item.get("item_type") == "workflow_stage_candidate"
        and item.get("reason") == "evidence_unverified"
        for item in semantic["rejected_semantic_items"]
    )


def test_unverified_typed_state_is_diagnostic_only() -> None:
    payload = copy.deepcopy(_case_semantic())
    payload["precondition_states"][0]["evidence"] = ["not present in case"]

    semantic = normalize_case_semantic(
        payload,
        case_text="内容提交完成后，消息中心创建通知",
    )

    assert semantic["precondition_states"] == []
    assert any(
        item.get("item_type") == "precondition_state"
        and item.get("reason") == "evidence_unverified"
        for item in semantic["rejected_semantic_items"]
    )


def test_internal_semantic_survives_normalization_and_review_metadata_recovery() -> None:
    normalized = normalize_json_structure([_case()])
    assert normalized[0]["_semantic"]["interaction_ids"] == ["content_message_notice"]

    reviewed = [
        {
            **normalized[0],
            "id": "TC-099",
            "origin_case_id": normalized[0]["id"],
        }
    ]
    reviewed[0].pop("_semantic")
    restored = apply_case_source_metadata(reviewed, source_cases=normalized)

    assert restored[0]["_semantic"]["produced_states"][0]["entity"] == "notification"


def test_public_projection_strips_internal_semantic_contract() -> None:
    public_cases = project_persistable_cases(normalize_json_structure([_case()]))

    assert len(public_cases) == 1
    assert "_semantic" not in public_cases[0]
    assert set(public_cases[0]) == {
        "id",
        "description",
        "test_module",
        "preconditions",
        "steps",
        "test_input",
        "expected_result",
        "priority",
        "priority_final",
    }


def test_project_profile_uses_model_contract_as_module_source() -> None:
    contract = normalize_requirement_semantic_contract(
        _contract_payload(),
        requirement_text=REQUIREMENT,
    )
    profile = build_project_profile(
        requirement_text=REQUIREMENT,
        semantic_contract=contract,
    )

    assert profile["profile_source"] == "model_semantic_contract"
    assert profile["module_order"] == ["内容工作台", "消息中心"]
    assert [item["interaction_id"] for item in profile["functional_architecture"]["module_interactions"]] == [
        "content_message_notice"
    ]


def test_semantic_compilation_failure_does_not_fall_back_to_document_module_guessing() -> None:
    context = build_structured_prompt_context(
        requirement=REQUIREMENT,
        architecture_requirement=REQUIREMENT,
        feedback_control_state={
            "source_meta": {
                "requirement_semantic_contract": empty_requirement_semantic_contract(
                    status="model_call_failed"
                )
            }
        },
    )

    assert context["module_catalog"] == []
    assert context["module_interactions"] == []
    assert context["project_profile"]["profile_source"] == "model_semantic_contract"
    assert context["project_profile"]["requirement_semantic_contract"]["status"] == "model_call_failed"


def test_semantic_contract_enters_generation_control_context() -> None:
    contract = normalize_requirement_semantic_contract(
        _contract_payload(),
        requirement_text=REQUIREMENT,
    )
    context = build_structured_prompt_context(
        requirement=REQUIREMENT,
        architecture_requirement=REQUIREMENT,
        feedback_control_state={"source_meta": {"requirement_semantic_contract": contract}},
    )

    assert [item["module_name"] for item in context["module_catalog"]] == ["内容工作台", "消息中心"]
    assert context["module_interactions"][0]["interaction_id"] == "content_message_notice"
    assert "[content_message_notice]" in context["control_context"]


def test_cross_module_phase_uses_interaction_id_instead_of_body_cooccurrence() -> None:
    phase = {
        "phase": "cross_module",
        "interactions": [
            {
                "interaction_id": "content_message_notice",
                "source_module": "内容工作台",
                "target_module": "消息中心",
            }
        ],
    }
    valid = _case()
    same_words_without_semantic = {**_case(), "_semantic": {}}

    assert case_matches_functional_phase(valid, phase) is True
    assert case_matches_functional_phase(same_words_without_semantic, phase) is False
    applied = apply_functional_module_phase([valid, same_words_without_semantic], phase)
    assert len(applied) == 1
    assert applied[0]["functional_interaction_ids"] == ["content_message_notice"]


def test_module_contract_accepts_exact_alias_or_semantic_reference_but_not_prefix_guess() -> None:
    contract = normalize_requirement_semantic_contract(
        _contract_payload(),
        requirement_text=REQUIREMENT,
    )
    profile = build_project_profile(requirement_text=REQUIREMENT, semantic_contract=contract)
    exact_alias = {**_case(), "test_module": "消息中心", "_semantic": {}}
    semantic_reference = _case()
    prefix_only = {**_case(), "id": "TC-003", "test_module": "消息中心-通知详情", "_semantic": {}}

    accepted, summary = enforce_functional_module_contract(
        [exact_alias, semantic_reference, prefix_only],
        project_profile=profile,
    )

    assert [item["test_module"] for item in accepted] == ["消息中心", "消息中心"]
    assert summary["semantic_resolution_count"] == 1
    assert summary["rejected_modules"] == ["消息中心-通知详情"]
