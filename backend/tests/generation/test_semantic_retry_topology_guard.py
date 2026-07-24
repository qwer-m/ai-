import copy

import pytest

from modules.test_generation_components.control.requirement_semantic_graph import (
    EDGE_SIGNATURES,
    STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES,
)
from modules.test_generation_components.control.semantic_retry_topology_guard import (
    SemanticRetryTopologyGuard,
    build_semantic_topology_projection,
    compile_semantic_retry_repair_targets,
    derive_allowed_topology_paths,
    evaluate_semantic_retry_topology,
    merge_semantic_retry_repair_values,
    preserve_verified_semantic_evidence,
)


def _semantic_candidate() -> dict:
    return {
        "semantic_contract_version": "requirement-semantic-v1",
        "confidence": 0.91,
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "content",
                    "module_name": "内容管理",
                    "scope_status": "in_scope",
                    "features": ["提交内容"],
                    "evidence": ["用户提交内容"],
                    "confidence": 0.95,
                },
                {
                    "module_key": "message",
                    "module_name": "消息中心",
                    "scope_status": "in_scope",
                    "features": ["接收通知"],
                    "evidence": ["消息中心收到通知"],
                    "confidence": 0.94,
                },
            ],
            "module_interactions": [
                {
                    "interaction_id": "content_to_message",
                    "source_module_key": "content",
                    "target_module_key": "message",
                    "trigger": "提交成功",
                    "result_state": "通知已送达",
                    "evidence": ["提交成功后消息中心收到通知"],
                    "confidence": 0.92,
                }
            ],
        },
        "workflow_blueprints": [
            {
                "workflow_id": "publish_flow",
                "name": "内容发布与通知",
                "primary": True,
                "initial_state": "content_ready",
                "required_stage_ids": ["submit", "receive"],
                "terminal_states": ["message_received"],
                "confidence": 0.9,
                "steps": [
                    {
                        "id": "submit",
                        "label": "提交内容",
                        "action": "提交内容",
                        "stage_kind": "commit",
                        "actor": "business_user",
                        "state_in": "content_ready",
                        "state_out": "content_submitted",
                        "required": True,
                        "terminal": False,
                        "critical": True,
                        "blocking": True,
                        "destructive": False,
                        "module_candidates": [
                            {
                                "module_key": "content",
                                "role": "source",
                                "evidence": ["用户提交内容"],
                                "confidence": 0.96,
                            }
                        ],
                        "interaction_ids": ["content_to_message"],
                        "produced_states": [
                            {
                                "entity": "content",
                                "state": "submitted",
                                "source": "current_stage",
                                "scope": "workflow",
                                "polarity": "positive",
                                "temporal": "after_case",
                                "evidence": ["用户提交内容"],
                                "confidence": 0.93,
                            }
                        ],
                        "evidence": ["用户提交内容"],
                    },
                    {
                        "id": "receive",
                        "label": "接收通知",
                        "action": "查看消息通知",
                        "stage_kind": "consume",
                        "actor": "business_user",
                        "state_in": "content_submitted",
                        "state_out": "message_received",
                        "required": True,
                        "terminal": True,
                        "critical": False,
                        "blocking": False,
                        "destructive": False,
                        "module_candidates": [
                            {
                                "module_key": "message",
                                "role": "target",
                                "evidence": ["消息中心收到通知"],
                                "confidence": 0.95,
                            }
                        ],
                        "interaction_ids": ["content_to_message"],
                        "required_states": [
                            {
                                "entity": "content",
                                "state": "submitted",
                                "source": "previous_stage",
                                "scope": "workflow",
                                "polarity": "positive",
                                "temporal": "after_previous_stage",
                                "evidence": ["提交成功后消息中心收到通知"],
                                "confidence": 0.9,
                            }
                        ],
                        "evidence": ["消息中心收到通知"],
                    },
                ],
            }
        ],
    }


def test_first_parseable_candidate_becomes_immutable_anchor() -> None:
    guard = SemanticRetryTopologyGuard()

    parse_failure = guard.evaluate("{not-json")
    anchor_result = guard.evaluate(_semantic_candidate())

    evidence_repair = copy.deepcopy(_semantic_candidate())
    evidence_repair["confidence"] = 0.99
    evidence_repair["functional_architecture"]["functional_modules"][0]["evidence"] = [
        "来自本轮原文的连续证据"
    ]
    retry_result = guard.evaluate(
        evidence_repair,
        validation_feedback=["workflow_1:step_1:evidence_unverified"],
    )

    assert parse_failure["decision"] == "candidate_not_parseable"
    assert guard.anchored is True
    assert anchor_result["decision"] == "anchor_created"
    assert anchor_result["parseable_candidate_count"] == 1
    assert retry_result["allowed"] is True
    assert retry_result["decision"] == "topology_unchanged"
    assert retry_result["anchor_fingerprint"] == retry_result["candidate_fingerprint"]


def test_evidence_only_retry_blocks_all_business_topology_drift() -> None:
    anchor = _semantic_candidate()
    retry = copy.deepcopy(anchor)
    retry["functional_architecture"]["functional_modules"][0]["module_key"] = "renamed_module"
    retry["functional_architecture"]["module_interactions"][0]["interaction_id"] = "new_link"
    retry["workflow_blueprints"][0]["workflow_id"] = "new_flow"
    retry["workflow_blueprints"][0]["steps"][0]["id"] = "new_step"
    retry["workflow_blueprints"][0]["steps"][0]["state_out"] = "other_state"

    result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=[
            "workflow_1:step_1:evidence_unverified",
            "unknown_evidence_ref:EV_MISSING",
        ],
    )

    blocked_paths = {item["path"] for item in result["blocked_topology_diffs"]}
    assert result["allowed"] is False
    assert result["decision"] == "topology_drift_blocked"
    assert result["feedback_scope"] == "evidence_only"
    assert result["allowed_paths"] == []
    assert "$.functional_architecture.functional_modules[0].module_key" in blocked_paths
    assert "$.functional_architecture.module_interactions[0].interaction_id" in blocked_paths
    assert "$.workflow_blueprints[0].workflow_id" in blocked_paths
    assert "$.workflow_blueprints[0].steps[0].id" in blocked_paths
    assert "$.workflow_blueprints[0].steps[0].state_out" in blocked_paths


def test_interaction_consistency_feedback_only_opens_step_module_candidates() -> None:
    anchor = _semantic_candidate()
    retry = copy.deepcopy(anchor)
    retry["workflow_blueprints"][0]["steps"][0]["module_candidates"].append(
        {
            "module_key": "message",
            "role": "target",
            "evidence": ["消息中心收到通知"],
            "confidence": 0.94,
        }
    )
    feedback = {
        "workflow_consistency_rejections": [
            {
                "workflow_index": 1,
                "step_index": 1,
                "reason": "interaction_modules_not_declared",
                "interaction_id": "content_to_message",
                "missing_module_keys": ["message"],
                "candidate_field": "module_candidates",
            }
        ]
    }

    allowed_result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )
    retry["workflow_blueprints"][0]["workflow_id"] = "drifted_flow"
    blocked_result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )

    assert allowed_result["allowed"] is True
    assert allowed_result["decision"] == "targeted_changes_only"
    assert allowed_result["allowed_paths"] == [
        "$.workflow_blueprints[0].steps[0].module_candidates.**"
    ]
    assert blocked_result["allowed"] is False
    assert blocked_result["blocked_topology_diffs"] == [
        {
            "path": "$.workflow_blueprints[0].workflow_id",
            "change": "value_changed",
            "anchor_kind": "str",
            "candidate_kind": "str",
        }
    ]


def test_missing_interaction_feedback_allows_only_new_interaction_item() -> None:
    base = _semantic_candidate()
    retry = copy.deepcopy(base)
    retry["functional_architecture"]["module_interactions"][0][
        "trigger"
    ] = "unauthorized rewrite"
    retry["functional_architecture"]["module_interactions"].append(
        {
            "interaction_id": "message_to_content",
            "source_module_key": "message",
            "target_module_key": "content",
            "trigger": "return",
            "result_state": "content visible",
            "evidence": ["return to content"],
            "confidence": 0.9,
        }
    )
    retry["workflow_blueprints"][0]["steps"][0]["interaction_ids"].append(
        "message_to_content"
    )
    feedback = {
        "workflow_consistency_rejections": [
            {
                "workflow_index": 1,
                "step_index": 1,
                "reason": "cross_module_interaction_id_missing",
                "field_path": "$.functional_architecture.module_interactions",
            }
        ]
    }

    evaluation = evaluate_semantic_retry_topology(
        base,
        retry,
        validation_feedback=feedback,
    )
    merged, diagnostics = merge_semantic_retry_repair_values(
        base,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )

    assert evaluation["allowed"] is False
    assert evaluation["allowed_paths"] == [
        "$.functional_architecture.module_interactions[1].**",
        "$.workflow_blueprints[0].steps[0].interaction_ids.**",
        "$.workflow_blueprints[0].steps[0].relation_ids.**",
    ]
    assert evaluation["blocked_topology_diffs"] == [
        {
            "path": "$.functional_architecture.module_interactions[0].trigger",
            "change": "value_changed",
            "anchor_kind": "str",
            "candidate_kind": "str",
        }
    ]
    interactions = merged["functional_architecture"]["module_interactions"]
    assert [item["interaction_id"] for item in interactions] == [
        "content_to_message",
        "message_to_content",
    ]
    assert interactions[0]["trigger"] == base["functional_architecture"][
        "module_interactions"
    ][0]["trigger"]
    assert merged["workflow_blueprints"][0]["steps"][0]["interaction_ids"] == [
        "content_to_message",
        "message_to_content",
    ]
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True


def test_allowed_retry_does_not_replace_the_first_anchor() -> None:
    guard = SemanticRetryTopologyGuard()
    anchor = _semantic_candidate()
    repaired = copy.deepcopy(anchor)
    repaired["workflow_blueprints"][0]["steps"][0]["module_candidates"].append(
        {
            "module_key": "message",
            "role": "target",
            "evidence": ["消息中心收到通知"],
            "confidence": 0.94,
        }
    )
    feedback = {
        "workflow_index": 1,
        "step_index": 1,
        "reason": "interaction_modules_not_declared",
        "candidate_field": "module_candidates",
    }

    anchor_result = guard.evaluate(anchor)
    allowed_retry = guard.evaluate(repaired, validation_feedback=feedback)
    same_retry_without_permission = guard.evaluate(repaired, validation_feedback=[])

    assert allowed_retry["allowed"] is True
    assert same_retry_without_permission["allowed"] is False
    assert guard.anchor_fingerprint == anchor_result["anchor_fingerprint"]
    assert allowed_retry["anchor_fingerprint"] == anchor_result["anchor_fingerprint"]


def test_typed_state_feedback_allows_only_reported_field_path() -> None:
    anchor = _semantic_candidate()
    retry = copy.deepcopy(anchor)
    produced_state = retry["workflow_blueprints"][0]["steps"][0]["produced_states"][0]
    produced_state["source"] = "system_event"
    feedback = {
        "typed_state_rejections": [
            {
                "workflow_index": 1,
                "step_index": 1,
                "collection": "produced_states",
                "item_index": 1,
                "reason": "state_schema_invalid",
                "missing_or_invalid_fields": ["source", "evidence"],
            }
        ]
    }

    allowed_result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )
    produced_state["state"] = "changed_state"
    blocked_result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )

    assert allowed_result["allowed"] is True
    assert allowed_result["allowed_paths"] == [
        "$.workflow_blueprints[0].steps[0].produced_states[0].source"
    ]
    assert blocked_result["allowed"] is False
    assert blocked_result["blocked_topology_diffs"][0]["path"] == (
        "$.workflow_blueprints[0].steps[0].produced_states[0].state"
    )


def test_top_level_missing_workflow_is_repairable_only_with_explicit_feedback() -> None:
    anchor = _semantic_candidate()
    workflows = anchor.pop("workflow_blueprints")
    retry = copy.deepcopy(anchor)
    retry["workflow_blueprints"] = workflows

    unscoped = evaluate_semantic_retry_topology(anchor, retry)
    scoped = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback="workflow_declaration_missing",
    )

    assert unscoped["allowed"] is False
    assert scoped["allowed"] is True
    assert scoped["allowed_paths"] == ["$.workflow_blueprints.**"]


def test_projection_and_diagnostics_do_not_carry_evidence_or_changed_values() -> None:
    anchor = _semantic_candidate()
    wrapped_retry = {"requirement_semantic_contract": copy.deepcopy(anchor)}
    wrapped_retry["requirement_semantic_contract"]["confidence"] = 0.2
    wrapped_retry["requirement_semantic_contract"]["functional_architecture"][
        "functional_modules"
    ][0]["evidence"] = ["另一段证据"]

    projection = build_semantic_topology_projection(wrapped_retry)
    result = evaluate_semantic_retry_topology(anchor, wrapped_retry)
    serialized_projection = str(projection).lower()

    assert "evidence" not in serialized_projection
    assert "confidence" not in serialized_projection
    assert result["topology_changed"] is False

    drifted = copy.deepcopy(wrapped_retry)
    drifted["requirement_semantic_contract"]["workflow_blueprints"][0][
        "workflow_id"
    ] = "sensitive_new_value"
    drift_result = evaluate_semantic_retry_topology(anchor, drifted)
    assert "sensitive_new_value" not in str(drift_result)


def test_feedback_path_compiler_understands_string_and_structured_contracts() -> None:
    result = derive_allowed_topology_paths(
        [
            "workflow_1:required_stage_ids_mismatch",
            "workflow_1:step_2:actor_missing_or_invalid",
            {
                "path": "$.functional_architecture.module_interactions[0].trigger",
                "reason": "trigger_missing",
            },
        ]
    )

    assert result["feedback_scope"] == "targeted"
    assert result["allowed_paths"] == [
        "$.workflow_blueprints[0].required_stage_ids.**",
        "$.workflow_blueprints[0].steps[1].actor",
        "$.functional_architecture.module_interactions[0].trigger",
    ]


def test_safe_repair_merge_matches_four_to_three_steps_by_id_without_shifting() -> None:
    base = _semantic_candidate()
    workflow = base["workflow_blueprints"][0]
    step_template = workflow["steps"][0]
    base_steps = []
    for index in range(1, 5):
        step = copy.deepcopy(step_template)
        step["id"] = f"step_{index}"
        step["state_in"] = f"state_{index - 1}"
        step["state_out"] = f"state_{index}"
        step["evidence"] = [f"base-evidence-{index}"]
        step["confidence"] = index / 10
        base_steps.append(step)
    workflow["steps"] = base_steps
    workflow["required_stage_ids"] = [step["id"] for step in base_steps]

    retry = copy.deepcopy(base)
    retry_steps = [
        copy.deepcopy(retry["workflow_blueprints"][0]["steps"][index])
        for index in (0, 2, 3)
    ]
    for step in retry_steps:
        step["evidence"] = [f"retry-{step['id']}"]
        step["confidence"] = 0.99
    retry["workflow_blueprints"][0]["steps"] = retry_steps

    merged, diagnostics = merge_semantic_retry_repair_values(base, retry)
    merged_steps = merged["workflow_blueprints"][0]["steps"]

    assert [step["id"] for step in merged_steps] == [
        "step_1",
        "step_2",
        "step_3",
        "step_4",
    ]
    assert [step["evidence"] for step in merged_steps] == [
        ["retry-step_1"],
        ["base-evidence-2"],
        ["retry-step_3"],
        ["retry-step_4"],
    ]
    assert merged_steps[1]["confidence"] == 0.2
    assert merged_steps[2]["confidence"] == 0.99
    assert diagnostics["topology_preserved"] is True
    assert diagnostics["unmatched_base_identity_count"] >= 1
    assert (
        "$.workflow_blueprints[0].steps[2].evidence"
        in diagnostics["copied_repair_paths"]
    )


def test_safe_repair_merge_discards_interaction_drift_but_keeps_evidence() -> None:
    base = _semantic_candidate()
    base_step = base["workflow_blueprints"][0]["steps"][0]
    base_step["interaction_ids"] = []
    retry = copy.deepcopy(base)
    retry_step = retry["workflow_blueprints"][0]["steps"][0]
    retry_step["interaction_ids"] = ["content_to_message"]
    retry_step["evidence"] = ["重试中修复后的步骤证据"]
    retry_step["module_candidates"][0]["evidence"] = ["重试中修复后的模块证据"]

    merged, diagnostics = merge_semantic_retry_repair_values(base, retry)
    merged_step = merged["workflow_blueprints"][0]["steps"][0]

    assert merged_step["interaction_ids"] == []
    assert merged_step["evidence"] == ["重试中修复后的步骤证据"]
    assert merged_step["module_candidates"][0]["evidence"] == [
        "重试中修复后的模块证据"
    ]
    assert build_semantic_topology_projection(merged) == (
        build_semantic_topology_projection(base)
    )
    assert diagnostics["discarded_topology_diff_count"] == 1
    assert diagnostics["discarded_topology_paths"] == [
        "$.workflow_blueprints[0].steps[0].interaction_ids"
    ]
    assert "content_to_message" not in str(diagnostics)
    assert "重试中修复后的步骤证据" not in str(diagnostics)


def test_safe_repair_merge_supports_wrapped_contract_without_replacing_wrapper() -> None:
    base_contract = _semantic_candidate()
    retry_contract = copy.deepcopy(base_contract)
    retry_contract["confidence"] = 0.77
    retry_contract["workflow_blueprints"][0]["steps"][1]["evidence"] = [
        "wrapper 内修复证据"
    ]
    base = {
        "transport_marker": "base",
        "requirement_semantic_contract": base_contract,
    }
    retry = {
        "transport_marker": "retry",
        "requirement_semantic_contract": retry_contract,
    }

    merged, diagnostics = merge_semantic_retry_repair_values(base, retry)
    merged_contract = merged["requirement_semantic_contract"]

    assert merged["transport_marker"] == "base"
    assert merged_contract["confidence"] == 0.77
    assert merged_contract["workflow_blueprints"][0]["steps"][1]["evidence"] == [
        "wrapper 内修复证据"
    ]
    assert diagnostics["base_wrapper"] is True
    assert diagnostics["retry_wrapper"] is True
    assert diagnostics["topology_preserved"] is True

    plain_merged, plain_diagnostics = merge_semantic_retry_repair_values(
        base_contract,
        retry,
    )
    assert plain_merged["confidence"] == 0.77
    assert plain_diagnostics["base_wrapper"] is False
    assert plain_diagnostics["retry_wrapper"] is True


def test_safe_repair_merge_does_not_merge_or_add_items_without_stable_identity() -> None:
    base = _semantic_candidate()
    base_modules = base["functional_architecture"]["functional_modules"]
    retry = copy.deepcopy(base)
    retry_modules = retry["functional_architecture"]["functional_modules"]
    retry_modules[0].pop("module_key")
    retry_modules[0]["evidence"] = ["没有身份时不得复制"]
    added_module = copy.deepcopy(retry_modules[1])
    added_module["module_key"] = "retry_only_module"
    added_module["evidence"] = ["重试新增模块不得进入 base"]
    retry_modules.append(added_module)

    merged, diagnostics = merge_semantic_retry_repair_values(base, retry)
    merged_modules = merged["functional_architecture"]["functional_modules"]

    assert [item["module_key"] for item in merged_modules] == [
        item["module_key"] for item in base_modules
    ]
    assert merged_modules[0]["evidence"] == base_modules[0]["evidence"]
    assert len(merged_modules) == len(base_modules)
    assert diagnostics["missing_identity_count"] >= 1
    assert diagnostics["unmatched_retry_identity_count"] >= 1
    assert diagnostics["topology_preserved"] is True


def test_safe_repair_merge_matches_nested_collections_by_declared_identity() -> None:
    base = _semantic_candidate()
    base_modules = base["functional_architecture"]["functional_modules"]
    step = base["workflow_blueprints"][0]["steps"][0]
    step["module_candidates"].append(
        {
            "module_key": "message",
            "role": "target",
            "evidence": ["base-message-module"],
            "confidence": 0.8,
        }
    )
    step["produced_states"].append(
        {
            "entity": "message",
            "state": "queued",
            "source": "current_stage",
            "scope": "workflow",
            "polarity": "positive",
            "temporal": "after_case",
            "evidence": ["base-message-state"],
            "confidence": 0.8,
        }
    )
    retry = copy.deepcopy(base)
    retry_modules = retry["functional_architecture"]["functional_modules"]
    retry_modules.reverse()
    for module in retry_modules:
        module["evidence"] = [f"retry-module-{module['module_key']}"]
    retry_step = retry["workflow_blueprints"][0]["steps"][0]
    retry_step["module_candidates"].reverse()
    for module in retry_step["module_candidates"]:
        module["evidence"] = [f"retry-candidate-{module['module_key']}"]
    retry_step["produced_states"].reverse()
    for state in retry_step["produced_states"]:
        state["evidence"] = [f"retry-state-{state['entity']}"]

    merged, diagnostics = merge_semantic_retry_repair_values(base, retry)
    merged_modules = merged["functional_architecture"]["functional_modules"]
    merged_step = merged["workflow_blueprints"][0]["steps"][0]

    assert [item["module_key"] for item in merged_modules] == ["content", "message"]
    assert [item["evidence"] for item in merged_modules] == [
        ["retry-module-content"],
        ["retry-module-message"],
    ]
    assert [item["module_key"] for item in merged_step["module_candidates"]] == [
        "content",
        "message",
    ]
    assert [item["evidence"] for item in merged_step["module_candidates"]] == [
        ["retry-candidate-content"],
        ["retry-candidate-message"],
    ]
    assert [item["entity"] for item in merged_step["produced_states"]] == [
        "content",
        "message",
    ]
    assert [item["evidence"] for item in merged_step["produced_states"]] == [
        ["retry-state-content"],
        ["retry-state-message"],
    ]
    assert diagnostics["topology_preserved"] is True


def test_allowed_workflow_subtree_does_not_admit_unrelated_interaction_addition() -> None:
    full_retry = _semantic_candidate()
    base = copy.deepcopy(full_retry)
    base.pop("workflow_blueprints")
    base["functional_architecture"]["module_interactions"] = []

    merged, diagnostics = merge_semantic_retry_repair_values(
        base,
        full_retry,
        allowed_topology_paths=["$.workflow_blueprints.**"],
    )

    assert merged["workflow_blueprints"] == full_retry["workflow_blueprints"]
    assert merged["functional_architecture"]["module_interactions"] == []
    assert diagnostics["requested_topology_paths"] == [
        "$.workflow_blueprints.**"
    ]
    assert diagnostics["applied_topology_paths"] == [
        "$.workflow_blueprints.**"
    ]
    assert diagnostics["discarded_topology_paths"] == [
        "$.functional_architecture.module_interactions[0]"
    ]
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True
    assert diagnostics["topology_preserved"] is False


def test_allowed_exact_field_only_copies_that_field() -> None:
    base = _semantic_candidate()
    retry = copy.deepcopy(base)
    retry_step = retry["workflow_blueprints"][0]["steps"][0]
    retry_step["actor"] = "system_actor"
    retry_step["state_out"] = "unexpected_state"

    merged, diagnostics = merge_semantic_retry_repair_values(
        base,
        retry,
        allowed_topology_paths=[
            "$.workflow_blueprints[0].steps[0].actor"
        ],
    )
    merged_step = merged["workflow_blueprints"][0]["steps"][0]

    assert merged_step["actor"] == "system_actor"
    assert merged_step["state_out"] == base["workflow_blueprints"][0]["steps"][0][
        "state_out"
    ]
    assert diagnostics["applied_topology_paths"] == [
        "$.workflow_blueprints[0].steps[0].actor"
    ]
    assert diagnostics["discarded_topology_paths"] == [
        "$.workflow_blueprints[0].steps[0].state_out"
    ]
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True


def test_allowed_wildcard_copies_only_the_named_field_for_each_step() -> None:
    base = _semantic_candidate()
    retry = copy.deepcopy(base)
    for index, step in enumerate(retry["workflow_blueprints"][0]["steps"]):
        step["actor"] = f"actor_{index}"
        step["state_out"] = f"retry_state_{index}"

    merged, diagnostics = merge_semantic_retry_repair_values(
        base,
        retry,
        allowed_topology_paths=[
            "$.workflow_blueprints[0].steps[*].actor"
        ],
    )
    merged_steps = merged["workflow_blueprints"][0]["steps"]
    base_steps = base["workflow_blueprints"][0]["steps"]

    assert [step["actor"] for step in merged_steps] == ["actor_0", "actor_1"]
    assert [step["state_out"] for step in merged_steps] == [
        step["state_out"] for step in base_steps
    ]
    assert diagnostics["applied_topology_paths"] == [
        "$.workflow_blueprints[0].steps[0].actor",
        "$.workflow_blueprints[0].steps[1].actor",
    ]
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True


def test_invalid_allowed_paths_are_skipped_without_expanding_topology() -> None:
    base = _semantic_candidate()
    retry = copy.deepcopy(base)
    retry_step = retry["workflow_blueprints"][0]["steps"][0]
    retry_step["actor"] = "must_not_be_copied"
    retry_step["evidence"] = ["evidence 仍由安全值合并"]
    invalid_paths = [
        "$.workflow_blueprints[0].steps[0].evidence",
        "$.workflow_blueprints[0].steps[99].actor",
        "$.workflow_blueprints[0].steps[0].missing_field",
        "$.workflow_blueprints",
        "not-a-path",
    ]

    merged, diagnostics = merge_semantic_retry_repair_values(
        base,
        retry,
        allowed_topology_paths=invalid_paths,
    )
    merged_step = merged["workflow_blueprints"][0]["steps"][0]

    assert merged_step["actor"] == base["workflow_blueprints"][0]["steps"][0][
        "actor"
    ]
    assert merged_step["evidence"] == ["evidence 仍由安全值合并"]
    assert diagnostics["requested_topology_paths"] == invalid_paths
    assert diagnostics["applied_topology_paths"] == []
    assert diagnostics["skipped_topology_path_count"] >= len(invalid_paths)
    assert {
        item["reason"] for item in diagnostics["skipped_topology_paths"]
    } >= {
        "repair_value_path_not_allowed",
        "source_index_out_of_bounds",
        "source_path_missing",
        "container_requires_subtree",
        "path_syntax_invalid",
    }
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True
    assert diagnostics["topology_preserved"] is True


def test_identity_subtree_without_identity_is_skipped_instead_of_using_index() -> None:
    base = _semantic_graph_candidate()
    retry = copy.deepcopy(base)
    retry["semantic_graph"]["edges"][0].pop("edge_id")
    retry["semantic_graph"]["edges"][0]["target_node_id"] = "s_scope"

    merged, diagnostics = merge_semantic_retry_repair_values(
        base,
        retry,
        allowed_topology_paths=["$.semantic_graph.edges[0].**"],
    )

    assert merged["semantic_graph"]["edges"] == base["semantic_graph"]["edges"]
    assert diagnostics["applied_topology_paths"] == []
    assert diagnostics["skipped_topology_paths"] == [
        {
            "path": "$.semantic_graph.edges[0]",
            "reason": "source_identity_missing",
        }
    ]
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True


def _semantic_graph_candidate() -> dict:
    return {
        "semantic_contract_version": "requirement-semantic-v2",
        "evidence_facts": [
            {
                "fact_id": "f_scope",
                "fact_kind": "scope",
                "statement": "职责范围",
                "requirement_level": "required",
                "priority": "p1",
                "testability": "testable",
                "evidence": ["职责范围"],
                "confidence": 0.9,
            },
            {
                "fact_id": "f_action",
                "fact_kind": "action",
                "statement": "执行动作",
                "requirement_level": "required",
                "priority": "p1",
                "testability": "testable",
                "evidence": ["执行动作"],
                "confidence": 0.9,
            },
        ],
        "semantic_graph": {
            "graph_version": "requirement-semantic-graph-v1",
            "nodes": [
                {
                    "node_id": "s_scope",
                    "kind": "scope",
                    "name": "职责范围",
                    "scope_status": "in_scope",
                    "boundary_status": "resolved",
                    "fact_ids": ["f_scope"],
                    "confidence": 0.9,
                },
                {
                    "node_id": "c_action",
                    "kind": "capability",
                    "name": "执行动作",
                    "scope_status": "",
                    "boundary_status": "resolved",
                    "fact_ids": ["f_action"],
                    "confidence": 0.9,
                },
            ],
            "edges": [
                {
                    "edge_id": "e_owner",
                    "type": "owns",
                    "source_node_id": "s_scope",
                    "target_node_id": "c_action",
                    "fact_ids": ["f_action"],
                    "ownership_role": "primary",
                    "confidence": 0.9,
                }
            ],
            "primary_flow": {"node_ids": [], "edge_ids": []},
            "fact_dispositions": [],
        },
        "workflow_blueprints": [],
    }


def _semantic_graph_candidate_with_primary_flow() -> dict:
    candidate = _semantic_graph_candidate()
    candidate["semantic_graph"]["nodes"].append(
        {
            "node_id": "n_done",
            "kind": "state",
            "name": "完成状态",
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["f_action"],
            "confidence": 0.9,
        }
    )
    candidate["semantic_graph"]["edges"].append(
        {
            "edge_id": "e_complete",
            "type": "transitions",
            "source_node_id": "c_action",
            "target_node_id": "n_done",
            "fact_ids": ["f_action"],
            "ownership_role": "none",
            "trigger": "动作执行完成",
            "result_state": "已完成",
            "transferred_entity_node_ids": [],
            "confidence": 0.9,
        }
    )
    candidate["semantic_graph"]["primary_flow"] = {
        "node_ids": ["c_action", "n_done"],
        "edge_ids": ["e_complete"],
    }
    return candidate


def test_interaction_direction_role_feedback_is_scope_identity_bound() -> None:
    targets = compile_semantic_retry_repair_targets(
        {
            "workflow_index": 1,
            "step_index": 2,
            "reason": "interaction_direction_roles_mismatch",
            "role_mismatches": [
                {
                    "module_key": "content",
                    "expected_role": "source",
                    "declared_roles": ["target"],
                },
                {
                    "module_key": "message",
                    "expected_role": "target",
                    "declared_roles": ["source"],
                },
            ],
        }
    )

    assert {
        (
            tuple(item["match"]["scope_id"]),
            item["value_constraint"]["equals"],
        )
        for item in targets
    } == {(('content',), "source"), (('message',), "target")}
    assert all(
        item["path"]
        == "$.workflow_blueprints[0].steps[1].scope_candidates[*].role"
        for item in targets
    )


def test_cross_module_missing_edge_rejects_parallel_additions() -> None:
    anchor = _semantic_graph_candidate()
    anchor["semantic_graph"]["nodes"].append(
        {
            "node_id": "s_target",
            "kind": "scope",
            "name": "目标范围",
            "scope_status": "in_scope",
            "boundary_status": "resolved",
            "workflow_role": "none",
            "fact_ids": ["f_scope"],
            "confidence": 0.9,
        }
    )
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["edges"].extend(
        [
            {
                "edge_id": edge_id,
                "type": "interacts_with",
                "source_node_id": "s_scope",
                "target_node_id": "s_target",
                "fact_ids": ["f_action"],
                "ownership_role": "none",
                "trigger": "动作完成",
                "result_state": "结果送达",
                "transferred_entity_node_ids": [],
                "confidence": 0.9,
            }
            for edge_id in ("e_parallel_a", "e_parallel_b")
        ]
    )
    feedback = {
        "workflow_index": 1,
        "step_index": 1,
        "reason": "cross_module_interaction_id_missing",
        "field_path": "$.semantic_graph.edges",
        "source_node_id": "s_scope",
        "target_node_id": "s_target",
    }

    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )

    assert evaluation["allowed"] is False
    assert evaluation["allowed_diff_count"] == 0
    assert {
        item["path"] for item in evaluation["blocked_topology_diffs"]
    } == {"$.semantic_graph.edges[1]", "$.semantic_graph.edges[2]"}


def test_semantic_graph_collection_reordering_is_not_topology_drift() -> None:
    anchor = _semantic_graph_candidate()
    retry = copy.deepcopy(anchor)
    retry["evidence_facts"].reverse()
    retry["semantic_graph"]["nodes"].reverse()

    result = evaluate_semantic_retry_topology(anchor, retry)

    assert result["allowed"] is True
    assert result["topology_changed"] is False
    assert build_semantic_topology_projection(anchor) == (
        build_semantic_topology_projection(retry)
    )


def test_invalid_primary_flow_feedback_cannot_open_targeted_reselection() -> None:
    anchor = _semantic_graph_candidate_with_primary_flow()
    feedback = {
        "code": "primary_flow_not_simple_path",
        "path": "$.semantic_graph.primary_flow",
        "node_ids": ["c_action", "n_done"],
    }
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["nodes"].reverse()
    retry["semantic_graph"]["primary_flow"] = {"node_ids": [], "edge_ids": []}
    retry["semantic_graph"]["edges"][0]["source_node_id"] = "c_action"

    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )
    assert evaluation["allowed"] is False
    assert evaluation["allowed_paths"] == []
    assert evaluation["feedback_diagnostics"]["topology_repair_suppressed_by"] == [
        "primary_flow_not_simple_path"
    ]

    merged, diagnostics = merge_semantic_retry_repair_values(
        anchor,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )
    assert merged["semantic_graph"]["primary_flow"] == anchor["semantic_graph"][
        "primary_flow"
    ]
    assert merged["semantic_graph"]["edges"][0]["source_node_id"] == "s_scope"
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True


def test_structural_graph_conflict_suppresses_endpoint_and_owner_repairs() -> None:
    feedback = [
        {
            "code": "constraint_endpoint_invalid",
            "path": "$.semantic_graph.edges[2]",
            "id": "e_limit",
        },
        {
            "code": "interaction_endpoint_kind_invalid",
            "path": "$.semantic_graph.edges[3]",
            "id": "e_object_action",
        },
        {
            "code": "capability_owner_missing",
            "path": "$.semantic_graph.edges",
            "id": "c_limit_behavior",
        },
    ]

    analysis = derive_allowed_topology_paths(feedback)

    assert analysis["allowed_paths"] == []
    assert analysis["topology_repair_suppressed_by"] == [
        "constraint_endpoint_invalid",
        "interaction_endpoint_kind_invalid",
    ]
    assert compile_semantic_retry_repair_targets(feedback) == []


@pytest.mark.parametrize(
    "error_code",
    sorted(
        {
            signature["endpoint_error_code"]
            for signature in EDGE_SIGNATURES.values()
        }
    ),
)
def test_every_edge_endpoint_error_has_an_executable_route(
    error_code: str,
) -> None:
    feedback = [
        {
            "code": error_code,
            "path": "$.semantic_graph.edges[0]",
            "id": "edge_under_repair",
        }
    ]

    analysis = derive_allowed_topology_paths(feedback)
    repair_targets = compile_semantic_retry_repair_targets(feedback)

    assert error_code in analysis["recognized_feedback"]
    if error_code in STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES:
        assert analysis["allowed_paths"] == []
        assert analysis["topology_repair_suppressed_by"] == [error_code]
        assert repair_targets == []
    else:
        assert analysis["allowed_paths"]
        assert analysis["topology_repair_suppressed_by"] == []
        assert repair_targets


@pytest.mark.parametrize(
    "error_code",
    sorted(STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES),
)
def test_every_structural_graph_error_suppresses_targeted_repair(
    error_code: str,
) -> None:
    endpoint_field = (
        "target_node_id"
        if error_code == "interaction_target_fact_unbound"
        else "source_node_id"
    )
    feedback = {
        "code": error_code,
        "path": f"$.semantic_graph.edges[0].{endpoint_field}",
        "id": "edge_under_repair",
    }

    analysis = derive_allowed_topology_paths(feedback)
    repair_targets = compile_semantic_retry_repair_targets(feedback)

    assert error_code in analysis["recognized_feedback"]
    assert analysis["allowed_paths"] == []
    assert analysis["topology_repair_suppressed_by"] == [error_code]
    assert repair_targets == []


def test_targeted_edge_repair_uses_identity_after_reorder_with_blocked_drift() -> None:
    anchor = _semantic_graph_candidate()
    anchor["semantic_graph"]["edges"].append(
        {
            "edge_id": "e_relation",
            "type": "interacts_with",
            "source_node_id": "s_scope",
            "target_node_id": "c_action",
            "fact_ids": ["f_action"],
            "ownership_role": "none",
            "trigger": "",
            "result_state": "动作已完成",
            "transferred_entity_node_ids": [],
            "confidence": 0.9,
        }
    )
    feedback = {
        "code": "interaction_contract_incomplete",
        "path": "$.semantic_graph.edges[1]",
        "id": "e_relation",
    }
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["edges"].reverse()
    retry_edges = {
        item["edge_id"]: item for item in retry["semantic_graph"]["edges"]
    }
    retry_edges["e_relation"]["trigger"] = "动作执行完成"
    retry["semantic_graph"]["nodes"][0]["name"] = "未授权范围名称"

    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )
    assert evaluation["allowed"] is False
    assert evaluation["allowed_paths"] == [
        "$.semantic_graph.edges[0].trigger"
    ]

    merged, diagnostics = merge_semantic_retry_repair_values(
        anchor,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )
    merged_edges = {
        item["edge_id"]: item for item in merged["semantic_graph"]["edges"]
    }

    assert merged_edges["e_relation"]["trigger"] == "动作执行完成"
    assert "trigger" not in merged_edges["e_owner"]
    assert merged["semantic_graph"]["nodes"][0]["name"] == "职责范围"
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True


def test_second_retry_targets_existing_edge_by_identity_after_prior_additions() -> None:
    base = _semantic_graph_candidate()
    original_edge = base["semantic_graph"]["edges"][0]
    first_retry = copy.deepcopy(base)
    first_retry["semantic_graph"]["edges"].extend(
        [
            {**copy.deepcopy(original_edge), "edge_id": "a_added"},
            {**copy.deepcopy(original_edge), "edge_id": "z_added"},
        ]
    )

    # 按与候选数组相反的顺序应用新增项，模拟上一轮合并后的索引重排。
    first_merged, first_diagnostics = merge_semantic_retry_repair_values(
        base,
        first_retry,
        allowed_topology_paths=[
            "$.semantic_graph.edges[2].**",
            "$.semantic_graph.edges[1].**",
        ],
    )
    assert first_diagnostics["topology_changes_limited_to_allowed_paths"] is True
    assert [
        item["edge_id"] for item in first_merged["semantic_graph"]["edges"]
    ] == ["e_owner", "z_added", "a_added"]

    second_retry = copy.deepcopy(first_retry)
    second_retry_edges = {
        item["edge_id"]: item for item in second_retry["semantic_graph"]["edges"]
    }
    second_retry_edges["z_added"]["target_node_id"] = "s_scope"
    second_retry_edges["a_added"]["type"] = "transitions"

    second_merged, second_diagnostics = merge_semantic_retry_repair_values(
        first_merged,
        second_retry,
        allowed_topology_paths=["$.semantic_graph.edges[2].target_node_id"],
    )
    second_merged_edges = {
        item["edge_id"]: item for item in second_merged["semantic_graph"]["edges"]
    }

    assert second_merged_edges["z_added"]["target_node_id"] == "s_scope"
    assert second_merged_edges["a_added"]["target_node_id"] == "c_action"
    assert second_merged_edges["a_added"]["type"] == original_edge["type"]
    assert second_diagnostics["topology_changes_limited_to_allowed_paths"] is True
    assert second_diagnostics["discarded_topology_paths"] == [
        "$.semantic_graph.edges[0].type"
    ]


def test_graph_evidence_only_retry_cannot_change_owner_endpoint() -> None:
    anchor = _semantic_graph_candidate()
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["edges"][0]["source_node_id"] = "c_action"
    retry["evidence_facts"][0]["evidence"] = ["修复后的证据"]

    result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback="$.evidence_facts[0].evidence",
    )

    assert result["allowed"] is False
    assert result["feedback_scope"] == "evidence_only"
    assert result["allowed_paths"] == []
    assert result["blocked_topology_diffs"] == [
        {
            "path": "$.semantic_graph.edges[0].source_node_id",
            "change": "value_changed",
            "anchor_kind": "str",
            "candidate_kind": "str",
        }
    ]


def test_graph_targeted_boundary_repair_does_not_authorize_relation_drift() -> None:
    anchor = _semantic_graph_candidate()
    anchor["semantic_graph"]["nodes"][1]["boundary_status"] = "unresolved"
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["nodes"][1]["boundary_status"] = "resolved"
    retry["semantic_graph"]["edges"][0]["target_node_id"] = "s_scope"

    result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback="$.semantic_graph.nodes[0].boundary_status",
    )

    assert result["allowed"] is False
    assert result["allowed_paths"] == [
        "$.semantic_graph.nodes[0].boundary_status"
    ]
    assert result["blocked_topology_diffs"] == [
        {
            "path": "$.semantic_graph.edges[0].target_node_id",
            "change": "value_changed",
            "anchor_kind": "str",
            "candidate_kind": "str",
        }
    ]


def test_structured_boundary_feedback_uses_node_identity_not_array_position() -> None:
    anchor = _semantic_graph_candidate()
    anchor["semantic_graph"]["nodes"][1]["boundary_status"] = "unresolved"
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["nodes"][1]["boundary_status"] = "resolved"
    retry["semantic_graph"]["nodes"][0]["name"] = "drifted scope"

    result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback={
            "code": "required_node_boundary_unresolved",
            "path": "$.semantic_graph.nodes[1].boundary_status",
            "id": "c_action",
        },
    )

    assert result["allowed"] is False
    assert result["allowed_paths"] == [
        "$.semantic_graph.nodes[1].boundary_status"
    ]
    assert result["allowed_diff_count"] == 1
    assert result["blocked_topology_diffs"] == [
        {
            "path": "$.semantic_graph.nodes[1].name",
            "change": "value_changed",
            "anchor_kind": "str",
            "candidate_kind": "str",
        }
    ]


def test_scope_alias_feedback_only_opens_conflicting_node_names_and_aliases() -> None:
    anchor = _semantic_graph_candidate()
    anchor["semantic_graph"]["nodes"][0]["aliases"] = ["shared-boundary"]
    secondary_scope = copy.deepcopy(anchor["semantic_graph"]["nodes"][0])
    secondary_scope.update(
        {
            "node_id": "s_secondary",
            "name": "secondary scope",
            "aliases": ["shared-boundary"],
        }
    )
    anchor["semantic_graph"]["nodes"].append(secondary_scope)
    feedback = {
        "code": "scope_alias_boundary_ambiguous",
        "path": "$.semantic_graph.nodes",
        "node_ids": ["s_secondary", "s_scope", "s_secondary"],
        "count": 2,
    }

    targets = compile_semantic_retry_repair_targets(feedback)

    assert targets == [
        {
            "code": "scope_alias_boundary_ambiguous",
            "path": "$.semantic_graph.nodes[*].name",
            "operation": "replace_value",
            "match": {"node_id": ["s_scope", "s_secondary"]},
        },
        {
            "code": "scope_alias_boundary_ambiguous",
            "path": "$.semantic_graph.nodes[*].aliases.**",
            "operation": "repair_subtree",
            "match": {"node_id": ["s_scope", "s_secondary"]},
        },
    ]

    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["nodes"][0]["aliases"] = ["primary-boundary"]
    retry["semantic_graph"]["nodes"][2]["name"] = "distinct secondary scope"
    allowed = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )

    assert allowed["allowed"] is True
    assert set(allowed["allowed_paths"]) == {
        "$.semantic_graph.nodes[0].aliases.**",
        "$.semantic_graph.nodes[2].name",
    }

    retry["semantic_graph"]["nodes"][0]["boundary_status"] = "ambiguous"
    retry["semantic_graph"]["nodes"][1]["name"] = "unauthorized capability rename"
    blocked = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )

    assert blocked["allowed"] is False
    assert {item["path"] for item in blocked["blocked_topology_diffs"]} == {
        "$.semantic_graph.nodes[0].name",
        "$.semantic_graph.nodes[1].boundary_status",
    }


def test_graph_collection_paths_do_not_grant_subtree_permission() -> None:
    for path in (
        "$.semantic_graph",
        "$.semantic_graph.nodes",
        "$.semantic_graph.edges",
        "$.semantic_graph.fact_dispositions",
    ):
        feedback = derive_allowed_topology_paths(path)

        assert feedback["feedback_scope"] == "unscoped"
        assert feedback["allowed_paths"] == []

    anchor = _semantic_graph_candidate()
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["edges"][0]["source_node_id"] = "c_action"

    result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback="$.semantic_graph.edges",
    )

    assert result["allowed"] is False
    assert result["allowed_paths"] == []
    assert result["blocked_topology_diffs"][0]["path"] == (
        "$.semantic_graph.edges[0].source_node_id"
    )


def test_bare_edge_item_path_cannot_authorize_edge_removal() -> None:
    anchor = _semantic_graph_candidate()
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["edges"] = []
    feedback = [
        "$.semantic_graph.edges[0]",
        {
            "code": "edge_endpoint_unknown",
            "path": "$.semantic_graph.edges[0].source_node_id",
            "id": "e_owner",
        },
    ]

    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )
    merged, diagnostics = merge_semantic_retry_repair_values(
        anchor,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )

    assert evaluation["allowed"] is False
    assert evaluation["allowed_paths"] == []
    assert len(merged["semantic_graph"]["edges"]) == 1
    assert diagnostics["applied_topology_paths"] == []


def test_identity_repair_targets_win_over_many_bare_container_paths() -> None:
    edge_ids = [f"R_{index:03d}" for index in range(40)]
    feedback = [
        *(f"$.semantic_graph.edges[{index}]" for index in range(40)),
        *(
            {
                "code": "edge_endpoint_unknown",
                "path": f"$.semantic_graph.edges[{index}]",
                "id": edge_id,
            }
            for index, edge_id in enumerate(edge_ids)
        ),
    ]

    targets = compile_semantic_retry_repair_targets(feedback)

    assert targets == [
        {
            "code": "edge_endpoint_unknown",
            "path": "$.semantic_graph.edges[*].source_node_id",
            "operation": "replace_value",
            "match": {"edge_id": edge_ids},
        },
        {
            "code": "edge_endpoint_unknown",
            "path": "$.semantic_graph.edges[*].target_node_id",
            "operation": "replace_value",
            "match": {"edge_id": edge_ids},
        },
    ]


def test_precise_endpoint_repair_merges_without_container_skip() -> None:
    anchor = _semantic_graph_candidate()
    anchor["semantic_graph"]["edges"][0]["source_node_id"] = "missing_node"
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["edges"][0]["source_node_id"] = "s_scope"
    feedback = {
        "code": "edge_endpoint_unknown",
        "path": "$.semantic_graph.edges[0].source_node_id",
        "id": "e_owner",
    }

    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )
    merged, diagnostics = merge_semantic_retry_repair_values(
        anchor,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )

    assert evaluation["allowed"] is True
    assert evaluation["allowed_paths"] == [
        "$.semantic_graph.edges[0].source_node_id"
    ]
    assert merged["semantic_graph"]["edges"][0]["source_node_id"] == "s_scope"
    assert diagnostics["applied_topology_paths"] == [
        "$.semantic_graph.edges[0].source_node_id"
    ]
    assert "container_requires_subtree" not in {
        item["reason"] for item in diagnostics["skipped_topology_paths"]
    }


def test_fact_reference_repair_copies_identified_fact_id_subtree() -> None:
    anchor = _semantic_graph_candidate()
    anchor["semantic_graph"]["nodes"][1]["fact_ids"] = ["c_action"]
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["nodes"][1]["fact_ids"] = ["f_action"]
    feedback = {
        "code": "node_fact_reference_unknown",
        "path": "$.semantic_graph.nodes[1].fact_ids",
        "id": "c_action",
    }

    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )
    merged, diagnostics = merge_semantic_retry_repair_values(
        anchor,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )

    assert evaluation["allowed"] is True
    assert evaluation["allowed_paths"] == [
        "$.semantic_graph.nodes[1].fact_ids.**"
    ]
    assert merged["semantic_graph"]["nodes"][1]["fact_ids"] == ["f_action"]
    assert diagnostics["applied_topology_paths"] == [
        "$.semantic_graph.nodes[1].fact_ids.**"
    ]


def test_dependency_rejection_authorizes_upstream_evidence_not_reference_rewrite() -> None:
    targets = compile_semantic_retry_repair_targets(
        [
            {
                "code": "fact_evidence_unverified",
                "path": "$.evidence_facts[0].evidence",
                "id": "F_001",
            },
            {
                "code": "node_fact_dependency_rejected",
                "path": "$.semantic_graph.nodes[0].fact_ids",
                "id": "N_001",
            },
        ]
    )

    assert targets == [
        {
            "code": "fact_evidence_unverified",
            "path": "$.evidence_facts[*].evidence",
            "operation": "replace_with_verified_evidence_refs",
            "match": {"fact_id": ["F_001"]},
        }
    ]

def test_missing_required_facts_group_additions_without_losing_fact_constraints() -> None:
    targets = compile_semantic_retry_repair_targets(
        [
            {
                "code": "missing_required_fact",
                "path": "$.semantic_graph",
                "id": fact_id,
            }
            for fact_id in ("F_007", "F_011", "F_014")
        ]
    )

    expected_candidate_match = [
        {"contains": {"fact_ids": fact_id}}
        for fact_id in ("F_007", "F_011", "F_014")
    ]
    assert targets == [
        {
            "code": "missing_required_fact",
            "path": "$.semantic_graph.nodes[*]",
            "operation": "add_item",
            "candidate_match": expected_candidate_match,
        },
        {
            "code": "missing_required_fact",
            "path": "$.semantic_graph.edges[*]",
            "operation": "add_item",
            "candidate_match": expected_candidate_match,
        },
    ]


@pytest.mark.parametrize(
    "code",
    [
        "primary_flow_missing_for_workflow",
        "primary_flow_edge_unknown",
        "primary_flow_edge_id_duplicate",
        "primary_flow_not_simple_path",
    ],
)
def test_primary_flow_error_never_compiles_targeted_reselection(code: str) -> None:
    feedback = {
        "code": code,
        "path": "$.semantic_graph.primary_flow",
    }

    assert compile_semantic_retry_repair_targets(feedback) == []
    analysis = derive_allowed_topology_paths(feedback)
    assert analysis["feedback_scope"] == "unscoped"
    assert analysis["topology_repair_suppressed_by"] == [code]


def test_targeted_field_repair_preserves_primary_flow_selection() -> None:
    anchor = _semantic_graph_candidate_with_primary_flow()
    action = next(
        node
        for node in anchor["semantic_graph"]["nodes"]
        if node["node_id"] == "c_action"
    )
    action["boundary_status"] = "ambiguous"
    feedback = {
        "code": "required_node_boundary_unresolved",
        "path": "$.semantic_graph.nodes[1].boundary_status",
        "id": "c_action",
    }
    retry = copy.deepcopy(anchor)
    retry_action = next(
        node
        for node in retry["semantic_graph"]["nodes"]
        if node["node_id"] == "c_action"
    )
    retry_action["boundary_status"] = "resolved"
    retry["semantic_graph"]["primary_flow"] = {"node_ids": [], "edge_ids": []}

    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )

    assert evaluation["allowed"] is False
    assert evaluation["allowed_paths"] == [
        "$.semantic_graph.nodes[1].boundary_status"
    ]
    assert any(
        item["path"].startswith("$.semantic_graph.primary_flow")
        for item in evaluation["blocked_topology_diffs"]
    )
    merged, diagnostics = merge_semantic_retry_repair_values(
        anchor,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )
    merged_action = next(
        node
        for node in merged["semantic_graph"]["nodes"]
        if node["node_id"] == "c_action"
    )
    assert merged_action["boundary_status"] == "resolved"
    assert merged["semantic_graph"]["primary_flow"] == anchor["semantic_graph"][
        "primary_flow"
    ]
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True


def test_primary_flow_edge_order_is_topology_not_an_orderless_id_set() -> None:
    anchor = _semantic_graph_candidate_with_primary_flow()
    anchor["semantic_graph"]["nodes"].append(
        {
            "node_id": "n_archive",
            "kind": "state",
            "name": "归档状态",
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["f_action"],
            "confidence": 0.9,
        }
    )
    anchor["semantic_graph"]["edges"].append(
        {
            "edge_id": "e_archive",
            "type": "transitions",
            "source_node_id": "n_done",
            "target_node_id": "n_archive",
            "fact_ids": ["f_action"],
            "ownership_role": "none",
            "trigger": "完成后归档",
            "result_state": "已归档",
            "transferred_entity_node_ids": [],
            "confidence": 0.9,
        }
    )
    anchor["semantic_graph"]["primary_flow"] = {
        "node_ids": ["c_action", "n_done", "n_archive"],
        "edge_ids": ["e_complete", "e_archive"],
    }
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["primary_flow"]["edge_ids"].reverse()

    evaluation = evaluate_semantic_retry_topology(anchor, retry)

    assert evaluation["allowed"] is False
    assert evaluation["topology_changed"] is True
    assert any(
        item["path"].startswith("$.semantic_graph.primary_flow.edge_ids")
        for item in evaluation["blocked_topology_diffs"]
    )


def test_raw_workflow_role_field_error_does_not_grant_unconstrained_role_rewrite() -> None:
    anchor = _semantic_graph_candidate()
    feedback = {
        "code": "node_workflow_role_invalid",
        "path": "$.semantic_graph.nodes[1].workflow_role",
        "id": "c_action",
    }

    assert compile_semantic_retry_repair_targets(feedback) == []

    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["nodes"][1]["workflow_role"] = "entry"
    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback,
    )
    assert evaluation["allowed"] is False
    assert evaluation["allowed_paths"] == []


def test_fact_disposition_reason_repair_is_identity_bound_and_cannot_delete_item() -> None:
    anchor = _semantic_graph_candidate()
    anchor["semantic_graph"]["fact_dispositions"] = [
        {
            "fact_id": "f_context",
            "disposition": "context_only",
        },
        {
            "fact_id": "f_other",
            "disposition": "context_only",
            "reason": "保留已有原因",
        },
    ]
    feedback = {
        "code": "fact_disposition_reason_missing",
        "path": "$.semantic_graph.fact_dispositions[0].reason",
        "id": "f_context",
    }

    targets = compile_semantic_retry_repair_targets(feedback)

    assert targets == [
        {
            "code": "fact_disposition_reason_missing",
            "path": "$.semantic_graph.fact_dispositions[*].reason",
            "operation": "replace_value",
            "match": {"fact_id": ["f_context"]},
        }
    ]
    assert all(item["operation"] != "remove_item" for item in targets)

    repaired = copy.deepcopy(anchor)
    repaired["semantic_graph"]["fact_dispositions"].reverse()
    repaired_by_fact = {
        item["fact_id"]: item
        for item in repaired["semantic_graph"]["fact_dispositions"]
    }
    repaired_by_fact["f_context"]["reason"] = "补充来源可追溯的原因"
    accepted = evaluate_semantic_retry_topology(
        anchor,
        repaired,
        validation_feedback=feedback,
    )
    assert accepted["allowed"] is True
    assert accepted["allowed_paths"] == [
        "$.semantic_graph.fact_dispositions[1].reason"
    ]

    deleted = copy.deepcopy(anchor)
    deleted["semantic_graph"]["fact_dispositions"] = [
        item
        for item in deleted["semantic_graph"]["fact_dispositions"]
        if item["fact_id"] != "f_context"
    ]
    rejected = evaluate_semantic_retry_topology(
        anchor,
        deleted,
        validation_feedback=feedback,
    )
    assert rejected["allowed"] is False
    assert rejected["allowed_paths"] == []
    assert rejected["blocked_topology_diffs"] == [
        {
            "path": "$.semantic_graph.fact_dispositions[0]",
            "change": "item_removed",
            "identity_fields": ["fact_id"],
        }
    ]

    merged, diagnostics = merge_semantic_retry_repair_values(
        anchor,
        deleted,
        allowed_topology_paths=rejected["allowed_paths"],
    )
    assert merged["semantic_graph"]["fact_dispositions"] == anchor[
        "semantic_graph"
    ]["fact_dispositions"]
    assert diagnostics["applied_topology_paths"] == []


def test_primary_flow_error_suppresses_other_targeted_topology_repair() -> None:
    feedback_items = [
        {
            "code": "primary_flow_not_simple_path",
            "path": "$.semantic_graph.primary_flow",
            "node_ids": ["c_action", "n_done"],
        },
        {
            "code": "orphan_node",
            "path": "$.semantic_graph.nodes",
            "id": "n_isolated",
        },
    ]

    assert compile_semantic_retry_repair_targets(feedback_items[1]) == [
        {
            "code": "orphan_node",
            "path": "$.semantic_graph.edges[*]",
            "operation": "add_item",
            "candidate_match": [
                {
                    "any_equals": [
                        {"field": "source_node_id", "value": "n_isolated"},
                        {"field": "target_node_id", "value": "n_isolated"},
                    ]
                }
            ],
        }
    ]
    assert compile_semantic_retry_repair_targets(feedback_items) == []
    analysis = derive_allowed_topology_paths(feedback_items)
    assert analysis["topology_repair_suppressed_by"] == [
        "primary_flow_not_simple_path",
    ]

    anchor = _semantic_graph_candidate_with_primary_flow()
    retry = copy.deepcopy(anchor)
    orphan_edge = copy.deepcopy(retry["semantic_graph"]["edges"][-1])
    orphan_edge["edge_id"] = "e_orphan"
    orphan_edge["target_node_id"] = "n_isolated"
    retry["semantic_graph"]["edges"].append(orphan_edge)
    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=feedback_items,
    )
    assert evaluation["allowed"] is False
    assert evaluation["allowed_paths"] == []
    assert evaluation["blocked_topology_diffs"] == [
        {
            "path": "$.semantic_graph.edges[1]",
            "change": "item_added",
            "identity_fields": ["edge_id"],
        }
    ]


def test_addition_targets_do_not_merge_owner_and_orphan_constraints() -> None:
    targets = compile_semantic_retry_repair_targets(
        [
            {
                "code": "capability_owner_missing",
                "path": "$.semantic_graph.edges",
                "id": "N_CAPABILITY",
            },
            {
                "code": "orphan_node",
                "path": "$.semantic_graph.nodes[4]",
                "id": "N_ORPHAN",
            },
        ]
    )

    assert targets == [
        {
            "code": "capability_owner_missing",
            "path": "$.semantic_graph.edges[*]",
            "operation": "add_item",
            "candidate_match": [
                {
                    "equals": {
                        "type": "owns",
                        "target_node_id": "N_CAPABILITY",
                    }
                }
            ],
        },
        {
            "code": "orphan_node",
            "path": "$.semantic_graph.edges[*]",
            "operation": "add_item",
            "candidate_match": [
                {
                    "any_equals": [
                        {"field": "source_node_id", "value": "N_ORPHAN"},
                        {"field": "target_node_id", "value": "N_ORPHAN"},
                    ]
                }
            ],
        },
    ]


def test_missing_owner_allows_only_identified_owner_addition() -> None:
    complete = _semantic_graph_candidate()
    owner_edge = copy.deepcopy(complete["semantic_graph"]["edges"][0])
    # 新增边的规范化排序位于已有边之前，回拷仍应按稳定身份找到原始项。
    owner_edge["edge_id"] = "a_owner"
    anchor = copy.deepcopy(complete)
    anchor["semantic_graph"]["edges"] = [
        {
            "edge_id": "e_flow",
            "type": "transitions",
            "source_node_id": "s_scope",
            "target_node_id": "c_action",
            "fact_ids": ["f_action"],
            "ownership_role": "none",
            "confidence": 0.9,
        }
    ]
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["edges"][0]["target_node_id"] = "s_scope"
    retry["semantic_graph"]["edges"].append(owner_edge)
    feedback = {
        "code": "capability_owner_missing",
        "path": "$.semantic_graph.edges",
        "id": "c_action",
    }

    evaluation = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback=[
            "invalid_workflow_contract",
            "semantic_graph_invalid",
            "$.semantic_graph.edges",
            feedback,
        ],
    )

    assert evaluation["allowed"] is False
    assert evaluation["allowed_paths"] == ["$.semantic_graph.edges[1].**"]
    assert evaluation["allowed_diff_count"] == 1
    assert evaluation["blocked_topology_diffs"] == [
        {
            "path": "$.semantic_graph.edges[0].target_node_id",
            "change": "value_changed",
            "anchor_kind": "str",
            "candidate_kind": "str",
        }
    ]

    merged, diagnostics = merge_semantic_retry_repair_values(
        anchor,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )
    merged_edges = {
        item["edge_id"]: item for item in merged["semantic_graph"]["edges"]
    }
    assert merged_edges["e_flow"]["target_node_id"] == "c_action"
    assert merged_edges["a_owner"]["target_node_id"] == "c_action"
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True
    assert diagnostics["discarded_topology_paths"] == [
        "$.semantic_graph.edges[1].target_node_id"
    ]

    wrong_target = evaluate_semantic_retry_topology(
        {**anchor, "semantic_graph": {**anchor["semantic_graph"], "edges": []}},
        complete,
        validation_feedback={**feedback, "id": "other_capability"},
    )
    assert wrong_target["allowed"] is False
    assert wrong_target["allowed_paths"] == []


def test_uncovered_fact_allows_only_new_item_consuming_identified_fact() -> None:
    anchor = _semantic_graph_candidate()
    anchor["evidence_facts"].append(
        {
            "fact_id": "f_extra",
            "fact_kind": "state",
            "statement": "额外状态",
            "requirement_level": "optional",
            "priority": "p2",
            "testability": "testable",
            "evidence": ["额外状态"],
            "confidence": 0.9,
        }
    )
    retry = copy.deepcopy(anchor)
    retry["semantic_graph"]["nodes"].append(
        {
            "node_id": "n_extra",
            "kind": "state",
            "name": "额外状态",
            "scope_status": "",
            "boundary_status": "resolved",
            "workflow_role": "none",
            "fact_ids": ["f_extra"],
            "confidence": 0.9,
        }
    )
    retry["semantic_graph"]["edges"][0]["source_node_id"] = "c_action"

    result = evaluate_semantic_retry_topology(
        anchor,
        retry,
        validation_feedback={
            "code": "uncovered_fact",
            "path": "$.semantic_graph",
            "id": "f_extra",
        },
    )

    assert result["allowed"] is False
    assert result["allowed_paths"] == ["$.semantic_graph.nodes[2].**"]
    assert result["allowed_diff_count"] == 1
    assert result["blocked_topology_diffs"][0]["path"] == (
        "$.semantic_graph.edges[0].source_node_id"
    )


def test_workflow_count_feedback_allows_only_stable_identity_removal() -> None:
    base = _semantic_candidate()
    secondary = copy.deepcopy(base["workflow_blueprints"][0])
    secondary["workflow_id"] = "secondary_flow"
    secondary["primary"] = False
    base["workflow_blueprints"].append(secondary)
    retry = copy.deepcopy(base)
    retry["workflow_blueprints"] = retry["workflow_blueprints"][:1]

    guard = SemanticRetryTopologyGuard()
    guard.evaluate(base)
    evaluation = guard.evaluate(
        retry,
        validation_feedback=["workflow_2:workflow_count_exceeds_limit"],
    )

    assert evaluation["allowed"] is True
    assert evaluation["allowed_paths"] == ["$.workflow_blueprints[1]"]
    assert evaluation["topology_diffs"] == [
        {
            "path": "$.workflow_blueprints[1]",
            "change": "item_removed",
            "identity_fields": ["workflow_id"],
        }
    ]

    merged, diagnostics = merge_semantic_retry_repair_values(
        base,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )

    assert [
        item["workflow_id"] for item in merged["workflow_blueprints"]
    ] == ["publish_flow"]
    assert diagnostics["applied_topology_paths"] == [
        "$.workflow_blueprints[1]"
    ]
    assert diagnostics["topology_changes_limited_to_allowed_paths"] is True


def test_workflow_count_removal_does_not_authorize_primary_rewrite() -> None:
    base = _semantic_candidate()
    secondary = copy.deepcopy(base["workflow_blueprints"][0])
    secondary["workflow_id"] = "secondary_flow"
    secondary["primary"] = False
    base["workflow_blueprints"].append(secondary)
    retry = copy.deepcopy(base)
    retry["workflow_blueprints"] = retry["workflow_blueprints"][:1]
    retry["workflow_blueprints"][0]["name"] = "unauthorized rewrite"

    guard = SemanticRetryTopologyGuard()
    guard.evaluate(base)
    evaluation = guard.evaluate(
        retry,
        validation_feedback=["workflow_2:workflow_count_exceeds_limit"],
    )
    assert evaluation["allowed"] is False
    assert evaluation["blocked_topology_diffs"] == [
        {
            "path": "$.workflow_blueprints[0].name",
            "change": "value_changed",
            "anchor_kind": "str",
            "candidate_kind": "str",
        }
    ]

    merged, diagnostics = merge_semantic_retry_repair_values(
        base,
        retry,
        allowed_topology_paths=evaluation["allowed_paths"],
    )
    assert merged["workflow_blueprints"][0]["name"] == base[
        "workflow_blueprints"
    ][0]["name"]
    assert len(merged["workflow_blueprints"]) == 1
    assert diagnostics["discarded_topology_paths"] == [
        "$.workflow_blueprints[0].name"
    ]


def test_retry_preserves_verified_evidence_and_accepts_unverified_repair() -> None:
    base = _semantic_candidate()
    retry = copy.deepcopy(base)
    base_steps = base["workflow_blueprints"][0]["steps"]
    retry_steps = retry["workflow_blueprints"][0]["steps"]
    base_steps[0]["evidence"] = ["已验证原文"]
    retry_steps[0]["evidence"] = ["无关改写"]
    base_steps[1]["evidence"] = ["旧的无效证据"]
    retry_steps[1]["evidence"] = ["新的有效原文"]

    accepted, diagnostics = preserve_verified_semantic_evidence(
        base,
        retry,
        source_text="已验证原文 新的有效原文",
    )
    accepted_steps = accepted["workflow_blueprints"][0]["steps"]

    assert accepted_steps[0]["evidence"] == ["已验证原文"]
    assert accepted_steps[1]["evidence"] == ["新的有效原文"]
    assert diagnostics["preserved_verified_evidence_count"] >= 1
    assert (
        diagnostics["retry_topology_fingerprint"]
        == diagnostics["result_topology_fingerprint"]
    )


def test_retry_does_not_preserve_evidence_when_owner_semantics_changed() -> None:
    base = _semantic_candidate()
    retry = copy.deepcopy(base)
    base_step = base["workflow_blueprints"][0]["steps"][0]
    retry_step = retry["workflow_blueprints"][0]["steps"][0]
    base_step["action"] = "旧动作"
    base_step["evidence"] = ["旧动作原文"]
    retry_step["action"] = "新动作"
    retry_step["evidence"] = ["新动作原文"]

    accepted, diagnostics = preserve_verified_semantic_evidence(
        base,
        retry,
        source_text="旧动作原文 新动作原文",
    )
    accepted_step = accepted["workflow_blueprints"][0]["steps"][0]

    assert accepted_step["action"] == "新动作"
    assert accepted_step["evidence"] == ["新动作原文"]
    assert "$.workflow_blueprints[0].steps[0].evidence" not in diagnostics[
        "preserved_verified_evidence_paths"
    ]
