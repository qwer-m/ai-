from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from modules.test_generation_components.control.requirement_graph_stage_compiler import (
    RequirementGraphStageCompilationResult,
    compile_requirement_graph_stage,
)
from modules.test_generation_components.control.requirement_graph_partition_contract import (
    DEFAULT_GRAPH_PARTITION_MAX_FACTS,
    DEFAULT_GRAPH_PARTITION_MAX_NODES,
    RequirementGraphPartitionContractError,
    build_mechanical_context_partition_result,
    build_mechanical_requirement_graph,
    build_requirement_graph_local_edge_prompt,
    build_requirement_graph_partition_prompt,
    build_requirement_graph_partition_user_input,
    build_requirement_graph_relation_prompt,
    build_requirement_graph_relation_user_input,
    build_requirement_graph_workflow_prompt,
    build_requirement_graph_workflow_user_input,
    partition_requirement_graph_facts,
    select_requirement_graph_relation_facts,
    validate_requirement_graph_partition_response,
    validate_requirement_graph_local_edge_response,
    validate_requirement_graph_relation_response,
    validate_requirement_graph_workflow_response,
)
from modules.test_generation_components.control.requirement_fact_ledger import (
    fingerprint_source_evidence_catalog,
    normalize_requirement_fact_ledger,
)
from modules.test_generation_components.control.requirement_scope_ledger import (
    normalize_requirement_scope_ledger,
)
from modules.test_generation_components.control.model_envelope_call import (
    strict_json_output_contract_prompt,
)
from modules.test_generation_components.control.requirement_semantic_graph import (
    normalize_requirement_semantic_graph,
)


CATALOG = [
    {
        "ref": "EV_111111111111",
        "quote": "当前范围包含一个父级职责和一个可独立进入的子职责。",
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
_DB_SENTINEL = object()


def test_all_graph_phase_prompts_apply_feedback_after_second_attempt() -> None:
    prompts = [
        build_requirement_graph_partition_prompt(),
        build_requirement_graph_local_edge_prompt(),
        build_requirement_graph_relation_prompt(),
        build_requirement_graph_workflow_prompt(),
    ]

    assert all("On every attempt greater than 1" in prompt for prompt in prompts)
    assert all("every prior_errors item" in prompt for prompt in prompts)
    shared_json_contract = strict_json_output_contract_prompt()
    assert all(prompt.count(shared_json_contract) == 1 for prompt in prompts)
    assert "Similar statements, numeric variants" in prompts[0]
    assert "boundary_status exactly to resolved" in prompts[0]
    assert "Frozen fact_kind is authoritative" in prompts[0]
    assert "constraint_fact_ids and non_constraint_fact_ids" in prompts[0]
    assert "A single-scope interaction is local behavior" in prompts[0]
    assert "reversing it cannot repair the mismatch" in prompts[1]
    assert "never a string or null" in prompts[3]
    assert "workflow_step_contracts" in prompts[3]
    assert "set required=true" in prompts[3]
    assert "required step.id values in step order" in prompts[3]
    assert "never to graph node IDs" in prompts[3]
    assert "workflow_role=entry" in prompts[3]
    assert "workflow_role=terminal" in prompts[3]
    assert "not a passive UI, list, empty-state" in prompts[3]
    assert "Prefer a user or external trigger" in prompts[3]
    assert "source=previous_stage must have the same entity" in prompts[3]


class _ScriptedClient:
    """按真实模型客户端协议记录请求，只控制模型返回序列。"""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.last_response_metadata: dict[str, Any] = {}

    def generate_response(
        self,
        user_input: str,
        system_prompt: str | None,
        **kwargs: Any,
    ) -> str:
        self.calls.append(
            {
                "user_input": user_input,
                "system_prompt": system_prompt,
                **dict(kwargs),
            }
        )
        response = self.responses[
            min(len(self.calls) - 1, len(self.responses) - 1)
        ]
        self.last_response_metadata = {}
        if isinstance(response, tuple) and len(response) == 2:
            response, metadata = response
            self.last_response_metadata = dict(metadata or {})
        if isinstance(response, Exception):
            raise response
        return str(response)


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
                "当前范围包含父级职责和子职责。",
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
    return normalized


def _semantic_graph(*, parent_confidence: float = 0.95) -> dict[str, Any]:
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
                "confidence": parent_confidence,
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


def _stage_response(*, parent_confidence: float = 0.95) -> dict[str, Any]:
    return {
        "confidence": 0.93,
        "semantic_graph": _semantic_graph(
            parent_confidence=parent_confidence
        ),
        "workflow_blueprints": [],
    }


def _json_response(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _partition_stage_response() -> dict[str, Any]:
    return {
        "confidence": 0.94,
        "nodes": [
            {
                "node_id": "P001_C_VIEW",
                "kind": "capability",
                "name": "查看相关记录",
                "aliases": [],
                "scope_status": "",
                "boundary_status": "resolved",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
                "confidence": 0.94,
            }
        ],
        "edges": [],
        "fact_dispositions": [],
    }


def _normalize(assembled: dict[str, Any]) -> dict[str, Any]:
    known_quotes = {item["quote"] for item in CATALOG}
    return normalize_requirement_semantic_graph(
        assembled,
        source_text="",
        evidence_validator=lambda evidence, _source: bool(evidence)
        and all(item in known_quotes for item in evidence),
    )


def _candidate_evaluator(assembled: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(assembled)
    parent = next(
        item
        for item in normalized["semantic_graph"]["nodes"]
        if item["node_id"] == "S_PARENT"
    )
    if float(parent.get("confidence") or 0.0) < 0.9:
        return {
            "valid": False,
            "status": "node_confidence_invalid",
            "semantic_contract": normalized,
            "normalization_diagnostics": {
                "semantic_graph_rejections": [
                    {
                        "code": "node_confidence_low",
                        "path": "$.semantic_graph.nodes[0].confidence",
                        "id": "S_PARENT",
                    }
                ]
            },
            "forbidden_topology_changes": [
                {
                    "path": "$.semantic_graph.nodes",
                    "change": "add_or_remove_item",
                }
            ],
        }
    return {
        "valid": normalized.get("publishable") is True,
        "status": "validated",
        "semantic_contract": normalized,
        "normalization_diagnostics": {},
    }


def _no_independent_recompile(_evaluation: dict[str, Any]) -> list[str]:
    return []


def _compile(
    client: _ScriptedClient,
    *,
    evaluator: Any = _candidate_evaluator,
    resolver: Any = _no_independent_recompile,
) -> RequirementGraphStageCompilationResult:
    return compile_requirement_graph_stage(
        client=client,
        normalized_scope_ledger=_normalized_ledger(),
        candidate_evaluator=evaluator,
        independent_recompile_code_resolver=resolver,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
        request_timeout_seconds=120,
    )


def _payload(call: dict[str, Any]) -> dict[str, Any]:
    return json.loads(call["user_input"])


def test_initial_candidate_validates_with_one_envelope_and_call() -> None:
    client = _ScriptedClient(
        (_json_response(_stage_response()), {"http_status": 200})
    )

    result = _compile(client)

    assert result.success is True
    assert result.status == "validated"
    assert result.evaluation["valid"] is True
    assert result.assembled_candidate["semantic_contract_version"] == (
        "requirement-semantic-v2"
    )
    assert len(client.calls) == 1
    assert result.diagnostics["semantic_compile_envelope_count"] == 1
    assert result.diagnostics["semantic_compile_attempt_count"] == 1
    assert result.diagnostics["semantic_compile_candidate_attempt_count"] == 1
    assert result.diagnostics["semantic_compile_physical_call_count"] == 1
    assert result.diagnostics["semantic_compile_validated_attempt"] == 1
    assert result.diagnostics["semantic_compile_targeted_repair_used"] is False
    assert (
        result.diagnostics["semantic_compile_independent_recompile_used"]
        is False
    )
    attempt = result.diagnostics["semantic_compile_attempts"][0]
    assert attempt["workflow_topology_status"] == "independent_only"
    assert "workflow_topology_error_codes" not in attempt
    assert "workflow_consistency_rejection_count" not in attempt
    assert "projection_error_codes" not in attempt


@pytest.mark.parametrize(
    "mutation",
    [
        lambda facts: facts[0].update(
            {"statement": "TAMPERED_BY_EVALUATOR"}
        ),
        lambda facts: facts[0].update({"model_override": "INJECTED"}),
        lambda facts: facts.pop(),
        lambda facts: facts.reverse(),
    ],
    ids=["rewrite", "unknown_field", "remove", "reorder"],
)
def test_candidate_evaluator_cannot_rewrite_frozen_facts(
    mutation: Any,
) -> None:
    def evaluator(assembled: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize(assembled)
        mutation(normalized["evidence_facts"])
        return {
            "valid": True,
            "status": "validated",
            "semantic_contract": normalized,
            "normalization_diagnostics": {},
        }

    client = _ScriptedClient(_json_response(_stage_response()))

    result = _compile(client, evaluator=evaluator)

    assert result.success is False
    assert result.status == "candidate_evaluator_contract_invalid"
    assert result.evaluation == {}
    assert len(client.calls) == 1
    assert (
        result.diagnostics["semantic_compile_independent_recompile_used"]
        is False
    )
    attempt = result.diagnostics["semantic_compile_attempts"][0]
    assert attempt["frozen_fact_contract_match"] is False
    assert "TAMPERED_BY_EVALUATOR" not in json.dumps(
        result.diagnostics,
        ensure_ascii=False,
    )


def test_json_parse_failure_starts_one_independent_fresh_candidate() -> None:
    client = _ScriptedClient(
        "not-json",
        _json_response(_stage_response()),
    )

    result = _compile(client)

    assert result.success is True
    assert len(client.calls) == 2
    assert [
        item["status"] for item in result.diagnostics["semantic_compile_attempts"]
    ] == ["parse_failed", "validated"]
    fresh = _payload(client.calls[1])
    assert fresh["attempt"] == 2
    assert fresh["compilation_mode"] == "independent_recompile"
    assert fresh["compilation_policy"] == "fresh_compile"
    assert fresh["retry_context"] is None
    assert fresh["recompile_reason_codes"] == [
        "graph_stage_json_parse_failed"
    ]
    assert result.diagnostics[
        "semantic_compile_independent_recompile_trigger_codes"
    ] == ["graph_stage_json_parse_failed"]


def test_strict_response_field_violation_uses_independent_fresh_candidate() -> None:
    invalid = _stage_response()
    invalid["evidence_facts"] = []
    client = _ScriptedClient(
        _json_response(invalid),
        _json_response(_stage_response()),
    )

    result = _compile(client)

    assert result.success is True
    first = result.diagnostics["semantic_compile_attempts"][0]
    assert first["status"] == "response_contract_invalid"
    assert first["response_contract_error_code"] == "graph_stage_frozen_field_echoed"
    assert first["response_contract_error_field"] == "evidence_facts"
    assert _payload(client.calls[1])["retry_context"] is None


def test_projection_mismatch_uses_normalized_contract_then_fresh_candidate() -> None:
    drifted = _stage_response()
    drifted["semantic_graph"]["nodes"][1]["node_id"] = "S_RENAMED"
    client = _ScriptedClient(
        _json_response(drifted),
        _json_response(_stage_response()),
    )

    result = _compile(client)

    assert result.success is True
    first = result.diagnostics["semantic_compile_attempts"][0]
    assert first["status"] == "projection_invalid"
    assert "active_scope_id_mismatch" in first["projection_error_codes"]
    fresh = _payload(client.calls[1])
    assert "active_scope_id_mismatch" in fresh["recompile_reason_codes"]


def test_attempt_diagnostic_only_keeps_compact_topology_codes() -> None:
    def evaluator(assembled: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize(assembled)
        normalized["semantic_graph_validation"] = {
            "diagnostics": {
                "workflow_topology_status": "not_linearizable",
                "workflow_topology_error_codes": [
                    "required_control_cycle",
                    "不应落盘的原文",
                ],
                "workflow_topology_errors": [
                    {
                        "code": "required_control_cycle",
                        "source_excerpt": "不应落盘的另一段原文",
                    }
                ],
            }
        }
        return {
            "valid": False,
            "status": "workflow_declaration_invalid",
            "semantic_contract": normalized,
            "normalization_diagnostics": {},
        }

    client = _ScriptedClient(_json_response(_stage_response()))

    result = _compile(client, evaluator=evaluator)

    assert result.success is False
    first = result.diagnostics["semantic_compile_attempts"][0]
    assert first["workflow_topology_status"] == "not_linearizable"
    assert first["workflow_topology_error_codes"] == [
        "required_control_cycle"
    ]
    assert "不应落盘的原文" not in json.dumps(
        first,
        ensure_ascii=False,
    )
    assert "不应落盘的另一段原文" not in json.dumps(
        first,
        ensure_ascii=False,
    )


def test_caller_unrepairable_codes_start_independent_fresh_candidate() -> None:
    resolver_calls = 0

    def resolver(_evaluation: dict[str, Any]) -> list[str]:
        nonlocal resolver_calls
        resolver_calls += 1
        return ["required_structure_unrepairable"] if resolver_calls == 1 else []

    client = _ScriptedClient(
        _json_response(_stage_response()),
        _json_response(_stage_response()),
    )

    result = _compile(client, resolver=resolver)

    assert result.success is True
    assert len(client.calls) == 2
    assert _payload(client.calls[1])["recompile_reason_codes"] == [
        "required_structure_unrepairable"
    ]
    assert _payload(client.calls[1])["retry_context"] is None


def test_independent_recompile_can_feed_one_targeted_repair() -> None:
    resolver_calls = 0

    def resolver(_evaluation: dict[str, Any]) -> list[str]:
        nonlocal resolver_calls
        resolver_calls += 1
        return ["required_structure_unrepairable"] if resolver_calls == 1 else []

    client = _ScriptedClient(
        _json_response(_stage_response()),
        _json_response(_stage_response(parent_confidence=0.4)),
        _json_response(_stage_response(parent_confidence=0.96)),
    )

    result = _compile(client, resolver=resolver)

    assert result.success is True
    assert len(client.calls) == 3
    assert [
        _payload(call)["compilation_mode"] for call in client.calls
    ] == ["initial", "independent_recompile", "targeted_repair"]
    targeted = _payload(client.calls[2])
    previous = targeted["retry_context"]["previous_candidate"]
    assert previous["semantic_graph"]["nodes"][0]["confidence"] == 0.4
    assert targeted["retry_context"]["repair_targets"] == [
        {
            "code": "node_confidence_low",
            "path": "$.semantic_graph.nodes[*].confidence",
            "operation": "replace_value",
            "match": {"node_id": ["S_PARENT"]},
        }
    ]
    assert result.diagnostics[
        "semantic_compile_independent_recompile_outcome"
    ] == "repairable_invalid"
    assert result.diagnostics["semantic_compile_targeted_repair_outcome"] == (
        "validated"
    )


def test_ordinary_contract_error_uses_one_targeted_repair() -> None:
    client = _ScriptedClient(
        _json_response(_stage_response(parent_confidence=0.4)),
        _json_response(_stage_response(parent_confidence=0.96)),
    )

    result = _compile(client)

    assert result.success is True
    assert len(client.calls) == 2
    targeted = _payload(client.calls[1])
    assert targeted["compilation_mode"] == "targeted_repair"
    assert targeted["compilation_policy"] == "targeted_repair"
    assert targeted["recompile_reason_codes"] == []
    retry_context = targeted["retry_context"]
    assert set(retry_context["previous_candidate"]) == {
        "semantic_graph",
        "workflow_blueprints",
    }
    assert "evidence_facts" not in retry_context["previous_candidate"]
    assert retry_context["repair_targets"] == [
        {
            "code": "node_confidence_low",
            "path": "$.semantic_graph.nodes[*].confidence",
            "operation": "replace_value",
            "match": {"node_id": ["S_PARENT"]},
        }
    ]
    assert retry_context["forbidden_topology_changes"] == [
        {
            "path": "$.semantic_graph.nodes",
            "change": "add_or_remove_item",
        }
    ]
    assert result.diagnostics["semantic_compile_targeted_repair_used"] is True
    assert result.diagnostics["semantic_compile_targeted_repair_outcome"] == (
        "validated"
    )


def test_targeted_repair_keeps_raw_workflow_candidate_identity() -> None:
    """规范化器即使丢弃坏 workflow，定向修复仍必须看到原候选。"""

    def workflow_response(initial_state: str) -> dict[str, Any]:
        response = _stage_response()
        response["workflow_blueprints"] = [
            {
                "workflow_id": "WF_MAIN",
                "name": "主流程",
                "primary": True,
                "confidence": 0.93,
                "initial_state": initial_state,
                "required_stage_ids": [],
                "terminal_states": [],
                "fact_ids": [],
                "steps": [],
            }
        ]
        return response

    def evaluator(assembled: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize(assembled)
        workflows = assembled.get("workflow_blueprints") or []
        initial_state = str((workflows[0] if workflows else {}).get("initial_state") or "")
        if not initial_state:
            # 模拟真实 normalize 在无效声明上不发布 workflow 的行为。
            normalized["workflow_blueprints"] = []
            return {
                "valid": False,
                "status": "workflow_declaration_invalid",
                "semantic_contract": normalized,
                "normalization_diagnostics": {
                    "workflow_consistency_rejections": [
                        {
                            "reason": "initial_state_missing",
                            "workflow_index": 1,
                            "source_excerpt": "不应写入诊断的需求原文",
                        }
                    ]
                },
            }
        normalized["workflow_blueprints"] = copy.deepcopy(workflows)
        return {
            "valid": True,
            "status": "validated",
            "semantic_contract": normalized,
            "normalization_diagnostics": {},
        }

    client = _ScriptedClient(
        _json_response(workflow_response("")),
        _json_response(workflow_response("ready")),
    )

    result = _compile(client, evaluator=evaluator)

    assert result.success is True
    assert len(client.calls) == 2
    retry_context = _payload(client.calls[1])["retry_context"]
    previous_workflows = retry_context["previous_candidate"][
        "workflow_blueprints"
    ]
    assert len(previous_workflows) == 1
    assert previous_workflows[0]["workflow_id"] == "WF_MAIN"
    assert previous_workflows[0]["initial_state"] == ""
    assert retry_context["repair_targets"] == [
        {
            "code": "initial_state_missing",
            "path": "$.workflow_blueprints[0].initial_state",
            "operation": "replace_value",
        }
    ]
    first_attempt = result.diagnostics["semantic_compile_attempts"][0]
    assert first_attempt["workflow_consistency_rejection_count"] == 1
    assert first_attempt["workflow_consistency_rejection_codes"] == [
        "initial_state_missing"
    ]
    assert "workflow_consistency_rejections" not in first_attempt
    assert "不应写入诊断的需求原文" not in json.dumps(
        first_attempt,
        ensure_ascii=False,
    )
    assert result.diagnostics["semantic_compile_attempts"][1][
        "retry_topology_guard"
    ]["allowed"] is True


def test_targeted_repair_topology_overreach_fails_closed_without_merge() -> None:
    overreaching = _stage_response(parent_confidence=0.96)
    overreaching["semantic_graph"]["nodes"][0]["name"] = "越权改名"
    client = _ScriptedClient(
        _json_response(_stage_response(parent_confidence=0.4)),
        _json_response(overreaching),
        _json_response(_stage_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == "retry_topology_drift_blocked"
    assert len(client.calls) == 2
    assert result.assembled_candidate == {}
    assert result.evaluation == {}
    assert "越权改名" not in json.dumps(
        result.diagnostics,
        ensure_ascii=False,
    )
    assert (
        result.diagnostics["semantic_compile_independent_recompile_used"]
        is False
    )
    guard = result.diagnostics["semantic_compile_attempts"][1][
        "retry_topology_guard"
    ]
    assert guard["allowed"] is False
    assert guard["blocked_diff_count"] >= 1


def test_parseable_candidate_then_transport_failure_does_not_publish_old_candidate() -> None:
    gateway_timeout = (
        "Error: HTTP 504 - Gateway Time-out",
        {"http_status": 504, "wire_api": "chat_completions"},
    )
    client = _ScriptedClient(
        _json_response(_stage_response(parent_confidence=0.4)),
        gateway_timeout,
        gateway_timeout,
        _json_response(_stage_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == "transport_exhausted"
    assert result.assembled_candidate == {}
    assert result.evaluation == {}
    assert len(client.calls) == 3
    assert client.calls[1] == client.calls[2]
    assert result.diagnostics["semantic_compile_targeted_repair_used"] is True
    assert result.diagnostics["semantic_compile_targeted_repair_outcome"] == (
        "transport_exhausted"
    )


def test_targeted_strict_failure_can_use_only_one_independent_candidate() -> None:
    invalid_targeted = _stage_response(parent_confidence=0.96)
    invalid_targeted["semantic_contract_version"] = "requirement-semantic-v2"
    client = _ScriptedClient(
        _json_response(_stage_response(parent_confidence=0.4)),
        _json_response(invalid_targeted),
        _json_response(_stage_response()),
    )

    result = _compile(client)

    assert result.success is True
    assert len(client.calls) == 3
    assert [
        _payload(call)["compilation_mode"] for call in client.calls
    ] == ["initial", "targeted_repair", "independent_recompile"]
    fresh = _payload(client.calls[2])
    assert fresh["retry_context"] is None
    assert fresh["recompile_reason_codes"] == [
        "graph_stage_response_field_unknown"
    ]


def test_transport_replay_reuses_exact_envelope_without_candidate_retry() -> None:
    client = _ScriptedClient(
        (
            "Error: HTTP 504 - Gateway Time-out",
            {"http_status": 504, "wire_api": "chat_completions"},
        ),
        (_json_response(_stage_response()), {"http_status": 200}),
    )

    result = _compile(client)

    assert result.success is True
    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]
    assert result.diagnostics["semantic_compile_envelope_count"] == 1
    assert result.diagnostics["semantic_compile_candidate_attempt_count"] == 1
    assert result.diagnostics["semantic_compile_physical_call_count"] == 2
    assert result.diagnostics["semantic_compile_transport_failure_count"] == 1
    assert result.diagnostics["semantic_compile_transport_retry_count"] == 1
    assert result.diagnostics["semantic_compile_timeout_count"] == 1


@pytest.mark.parametrize(
    ("metadata", "expected_status", "expected_termination"),
    [
        (
            {"http_status": 200, "finish_reason": "length"},
            "output_truncated",
            "truncated",
        ),
        (
            {
                "http_status": 200,
                "response_status": "incomplete",
                "incomplete_reason": "content_filter",
                "finish_reason": "stop",
            },
            "output_incomplete",
            "incomplete",
        ),
    ],
)
def test_non_complete_termination_stops_without_repair_or_fresh_candidate(
    metadata: dict[str, Any],
    expected_status: str,
    expected_termination: str,
) -> None:
    client = _ScriptedClient(
        (_json_response(_stage_response()), metadata),
        _json_response(_stage_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == expected_status
    assert result.assembled_candidate == {}
    assert result.evaluation == {}
    assert len(client.calls) == 1
    assert result.diagnostics["semantic_compile_targeted_repair_used"] is False
    assert (
        result.diagnostics["semantic_compile_independent_recompile_used"]
        is False
    )
    assert result.diagnostics["semantic_compile_attempts"][0][
        "response_termination"
    ] == expected_termination


def test_transport_exhaustion_stops_without_fresh_candidate() -> None:
    gateway_timeout = (
        "Error: HTTP 504 - Gateway Time-out",
        {"http_status": 504, "wire_api": "chat_completions"},
    )
    client = _ScriptedClient(
        gateway_timeout,
        gateway_timeout,
        _json_response(_stage_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == "transport_exhausted"
    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]
    assert result.diagnostics["semantic_compile_envelope_count"] == 1
    assert result.diagnostics["semantic_compile_physical_call_count"] == 2
    assert result.diagnostics["semantic_compile_timeout_count"] == 2
    assert (
        result.diagnostics["semantic_compile_independent_recompile_used"]
        is False
    )


def test_non_timeout_transport_replay_does_not_increase_timeout_count() -> None:
    client = _ScriptedClient(
        (
            "Error: HTTP 503 - Service Unavailable",
            {"http_status": 503, "wire_api": "chat_completions"},
        ),
        (_json_response(_stage_response()), {"http_status": 200}),
    )

    result = _compile(client)

    assert result.success is True
    assert result.diagnostics["semantic_compile_transport_failure_count"] == 1
    assert result.diagnostics["semantic_compile_transport_retry_count"] == 1
    assert result.diagnostics["semantic_compile_timeout_count"] == 0


def test_fatal_model_error_stops_without_replay_or_fresh_candidate() -> None:
    client = _ScriptedClient(
        (
            "Error: HTTP 401 - unauthorized",
            {"http_status": 401, "wire_api": "chat_completions"},
        ),
        _json_response(_stage_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == "fatal_model_error"
    assert len(client.calls) == 1
    assert result.diagnostics["semantic_compile_physical_call_count"] == 1
    assert result.diagnostics["semantic_compile_transport_retry_count"] == 0
    assert result.diagnostics["semantic_compile_timeout_count"] == 0


@pytest.mark.parametrize("callback_name", ["evaluator", "resolver"])
def test_callback_failure_is_not_hidden_by_model_retry(callback_name: str) -> None:
    def broken(_value: dict[str, Any]) -> Any:
        raise RuntimeError("callback failed")

    client = _ScriptedClient(_json_response(_stage_response()))
    kwargs = {callback_name: broken}

    result = _compile(client, **kwargs)

    assert result.success is False
    assert len(client.calls) == 1
    assert result.status in {
        "candidate_evaluator_failed",
        "recompile_resolver_failed",
    }
    assert (
        result.diagnostics["semantic_compile_independent_recompile_used"]
        is False
    )


def test_frozen_fact_mismatch_reports_exact_fact_ids() -> None:
    def evaluator(assembled: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize(assembled)
        normalized["evidence_facts"] = normalized["evidence_facts"][:-1]
        return {
            "valid": True,
            "status": "validated",
            "semantic_contract": normalized,
            "normalization_diagnostics": {},
        }

    client = _ScriptedClient(_json_response(_stage_response()))

    result = _compile(client, evaluator=evaluator)

    assert result.success is False
    assert result.status == "candidate_evaluator_contract_invalid"
    attempt = result.diagnostics["semantic_compile_attempts"][0]
    assert attempt["missing_frozen_fact_ids"] == ["F_CHILD_BEHAVIOR"]
    assert attempt["extra_evaluated_fact_ids"] == []
    assert attempt["changed_frozen_fact_ids"] == []


def test_partition_plan_is_ordered_exclusive_and_complete() -> None:
    ledger = _normalized_ledger()

    partitions = partition_requirement_graph_facts(ledger, max_facts=2)

    flattened = [fact_id for item in partitions for fact_id in item.fact_ids]
    assert len(partitions) == 1
    assert flattened == ["F_CHILD_BEHAVIOR"]
    assert {item.shard_id for item in partitions} == {"P001"}


def test_partition_plan_preserves_fact_ledger_source_order_not_hash_order() -> None:
    ledger = _normalized_ledger()
    source_fact = next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    source_binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    first_fact = {**copy.deepcopy(source_fact), "fact_id": "Z_SOURCE_FIRST"}
    first_binding = {
        **copy.deepcopy(source_binding),
        "fact_id": "Z_SOURCE_FIRST",
    }
    fact_index = ledger["evidence_facts"].index(source_fact)
    binding_index = ledger["fact_bindings"].index(source_binding)
    ledger["evidence_facts"].insert(fact_index, first_fact)
    ledger["fact_bindings"].insert(binding_index, first_binding)

    partitions = partition_requirement_graph_facts(ledger, max_facts=1)

    assert [item.fact_ids for item in partitions] == [
        ("Z_SOURCE_FIRST",),
        ("F_CHILD_BEHAVIOR",),
    ]


def test_default_fact_shard_capacity_does_not_exceed_node_capacity() -> None:
    ledger = _normalized_ledger()
    source_fact = next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    source_binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    for index in range(DEFAULT_GRAPH_PARTITION_MAX_FACTS * 2):
        fact_id = f"F_CAPACITY_{index:02d}"
        ledger["evidence_facts"].append(
            {**copy.deepcopy(source_fact), "fact_id": fact_id}
        )
        ledger["fact_bindings"].append(
            {**copy.deepcopy(source_binding), "fact_id": fact_id}
        )

    partitions = partition_requirement_graph_facts(ledger)

    assert DEFAULT_GRAPH_PARTITION_MAX_FACTS <= DEFAULT_GRAPH_PARTITION_MAX_NODES
    assert len(partitions) == 3
    assert all(
        len(partition.fact_ids) <= DEFAULT_GRAPH_PARTITION_MAX_NODES
        for partition in partitions
    )


def test_relation_selection_requires_two_frozen_active_scope_bindings() -> None:
    ledger = _normalized_ledger()
    fact = next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    fact["fact_kind"] = "interaction"

    assert select_requirement_graph_relation_facts(ledger) == []

    binding["role"] = "shared_requirement"
    binding["scope_ids"] = ["S_PARENT", "S_CHILD"]

    assert select_requirement_graph_relation_facts(ledger) == [
        "F_CHILD_BEHAVIOR"
    ]


def test_context_facts_do_not_inherit_first_active_owner() -> None:
    ledger = _normalized_ledger()
    binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    binding["role"] = "external_context"
    binding["scope_ids"] = []

    partitions = partition_requirement_graph_facts(ledger)

    assert len(partitions) == 1
    assert partitions[0].owner_scope_ids == ()
    assert partitions[0].fact_ids == ("F_CHILD_BEHAVIOR",)


def test_partition_contract_rejects_required_fact_disposition() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"] = []
    response["fact_dispositions"] = [
        {
            "fact_id": "F_CHILD_BEHAVIOR",
            "disposition": "context_only",
            "reason": "错误处置 required fact",
        }
    ]

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_partition_response(
            response,
            normalized_scope_ledger=ledger,
            partition=partition,
            require_local_closure=False,
        )

    assert caught.value.code == "graph_partition_disposition_invalid"
    assert caught.value.details == {
        "fact_id": "F_CHILD_BEHAVIOR",
        "reasons": [
            "required_fact_disposition_forbidden",
            "testable_fact_disposition_forbidden",
        ],
    }


def test_partition_contract_allows_frozen_context_disposition() -> None:
    ledger = _normalized_ledger()
    fact = next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    fact["requirement_level"] = "unspecified"
    fact["testability"] = "non_testable"
    binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    binding["role"] = "external_context"
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"] = []
    response["fact_dispositions"] = [
        {
            "fact_id": "F_CHILD_BEHAVIOR",
            "disposition": "context_only",
            "reason": "A2 已冻结为外部上下文，不进入当前业务图",
        }
    ]

    validated = validate_requirement_graph_partition_response(
        response,
        normalized_scope_ledger=ledger,
        partition=partition,
        require_local_closure=False,
    )

    assert validated["nodes"] == []
    assert validated["fact_dispositions"][0]["disposition"] == "context_only"


def test_pure_context_partition_is_compiled_mechanically() -> None:
    ledger = _normalized_ledger()
    fact = next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    fact["requirement_level"] = "optional"
    binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    binding["role"] = "non_scope_context"
    binding["scope_ids"] = []
    partition = partition_requirement_graph_facts(ledger)[0]

    result = build_mechanical_context_partition_result(ledger, partition)

    assert result is not None
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["fact_dispositions"] == [
        {
            "fact_id": "F_CHILD_BEHAVIOR",
            "disposition": "out_of_scope",
            "reason": "A2 已冻结为当前范围之外的可选事实",
        }
    ]


def test_partition_contract_canonicalizes_non_scope_status() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"][0]["scope_status"] = "out_of_scope"

    validated = validate_requirement_graph_partition_response(
        response,
        normalized_scope_ledger=ledger,
        partition=partition,
    )

    assert validated["nodes"][0]["scope_status"] == ""


@pytest.mark.parametrize(
    ("mutate", "expected_reasons"),
    [
        (
            lambda response: response["nodes"].append(
                copy.deepcopy(response["nodes"][0])
            ),
            ["duplicate_node_id"],
        ),
        (
            lambda response: response["nodes"][0].update(
                {"name": "", "boundary_status": "unresolved"}
            ),
            ["empty_name", "boundary_status_not_resolved"],
        ),
        (
            lambda response: response["nodes"][0].update({"aliases": "view"}),
            ["aliases_not_array"],
        ),
    ],
)
def test_partition_node_invalid_reports_machine_readable_details(
    mutate: Any,
    expected_reasons: list[str],
) -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    mutate(response)

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_partition_response(
            response,
            normalized_scope_ledger=ledger,
            partition=partition,
            require_local_closure=False,
        )

    assert caught.value.code == "graph_partition_node_invalid"
    expected_details = {
        "node_id": "P001_C_VIEW",
        "reasons": expected_reasons,
        "kind": "capability",
        "boundary_status": (
            "unresolved"
            if "boundary_status_not_resolved" in expected_reasons
            else "resolved"
        ),
    }
    if "boundary_status_not_resolved" in expected_reasons:
        expected_details.update(
            {
                "required_boundary_status": "resolved",
                "repair_hint": (
                    "A2 owner binding is already frozen for this partition. "
                    "Set boundary_status=resolved on every emitted local node; "
                    "the model must not reopen boundary resolution."
                ),
            }
        )
    assert caught.value.details == expected_details


def test_partition_contract_rejects_invalid_edge_signature_with_details() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"].append(
        {
            "node_id": "P001_E_RECORD",
            "kind": "entity",
            "name": "记录",
            "aliases": [],
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "confidence": 0.9,
        }
    )
    response["edges"] = [
        {
            "edge_id": "P001_E_INVALID",
            "type": "transitions",
            "source_node_id": "P001_C_VIEW",
            "target_node_id": "P001_E_RECORD",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "ownership_role": "none",
            "trigger": "",
            "result_state": "",
            "transferred_entity_node_ids": [],
            "confidence": 0.9,
        }
    ]

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_partition_response(
            response,
            normalized_scope_ledger=ledger,
            partition=partition,
        )

    assert caught.value.code == "graph_partition_semantic_invalid"
    issue = caught.value.details["issues"][0]
    assert issue["edge_id"] == "P001_E_INVALID"
    assert issue["reasons"] == [
        "transition_endpoint_kind_invalid"
    ]
    assert issue["source_kind"] == "capability"
    assert issue["target_kind"] == "entity"


def test_partition_contract_rejects_non_capability_orphan() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"][0]["kind"] = "entity"

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_partition_response(
            response,
            normalized_scope_ledger=ledger,
            partition=partition,
        )

    assert caught.value.code == "graph_partition_semantic_invalid"
    assert caught.value.details == {
        "issues": [
            {
                "path": "$.nodes",
                "reasons": ["orphan_node"],
                "node_ids": ["P001_C_VIEW"],
            }
        ]
    }


def test_partition_node_phase_rejects_non_capability_without_proving_partner() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"][0]["kind"] = "entity"

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_partition_response(
            response,
            normalized_scope_ledger=ledger,
            partition=partition,
            require_local_closure=False,
        )

    assert caught.value.code == "graph_partition_node_closure_unready"
    assert caught.value.details["nodes"] == [
        {
            "node_id": "P001_C_VIEW",
            "kind": "entity",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "endpoint_compatible_node_ids": [],
            "reason": "shared_proving_fact_missing",
            "suggested_kind": "capability",
        }
    ]
    assert "Do not invent edges" in caught.value.details["repair_hint"]


def test_single_scope_interaction_is_not_reserved_for_relation() -> None:
    ledger = _normalized_ledger()
    next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )["fact_kind"] = "interaction"
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"][0]["kind"] = "trigger"

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_partition_response(
            response,
            normalized_scope_ledger=ledger,
            partition=partition,
            require_local_closure=False,
        )

    assert caught.value.code == "graph_partition_node_closure_unready"
    assert caught.value.details["nodes"][0]["suggested_kind"] == "capability"

    response["nodes"][0]["kind"] = "capability"
    validated = validate_requirement_graph_partition_response(
        response,
        normalized_scope_ledger=ledger,
        partition=partition,
        require_local_closure=False,
    )
    assert validated["nodes"][0]["kind"] == "capability"


def test_partition_node_phase_requires_constraint_fact_to_freeze_constraint_node() -> None:
    ledger = _normalized_ledger()
    source_fact = next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    source_binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    ledger["evidence_facts"].append(
        {
            **copy.deepcopy(source_fact),
            "fact_id": "F_CHILD_CONSTRAINT",
            "fact_kind": "constraint",
        }
    )
    ledger["fact_bindings"].append(
        {**copy.deepcopy(source_binding), "fact_id": "F_CHILD_CONSTRAINT"}
    )
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"][0]["fact_ids"].append("F_CHILD_CONSTRAINT")

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_partition_response(
            response,
            normalized_scope_ledger=ledger,
            partition=partition,
            require_local_closure=False,
        )

    assert caught.value.code == "graph_partition_constraint_node_missing"
    assert caught.value.details["fact_ids"] == ["F_CHILD_CONSTRAINT"]
    assert caught.value.details["required_node_kind"] == "constraint"
    assert caught.value.details["constraint_fact_ids"] == [
        "F_CHILD_CONSTRAINT"
    ]
    assert caught.value.details["non_constraint_fact_ids"] == [
        "F_CHILD_BEHAVIOR"
    ]


def test_partition_input_projects_frozen_constraint_fact_ids() -> None:
    ledger = _normalized_ledger()
    source_fact = next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    source_binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    ledger["evidence_facts"].append(
        {
            **copy.deepcopy(source_fact),
            "fact_id": "F_CHILD_CONSTRAINT",
            "fact_kind": "constraint",
        }
    )
    ledger["fact_bindings"].append(
        {**copy.deepcopy(source_binding), "fact_id": "F_CHILD_CONSTRAINT"}
    )
    partition = partition_requirement_graph_facts(ledger)[0]

    payload = json.loads(
        build_requirement_graph_partition_user_input(ledger, partition)
    )

    assert payload["input_version"] == "3"
    assert payload["constraint_fact_ids"] == ["F_CHILD_CONSTRAINT"]
    assert payload["non_constraint_fact_ids"] == ["F_CHILD_BEHAVIOR"]


def test_partition_node_phase_rejects_constraint_node_without_constraint_fact() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"][0]["kind"] = "constraint"

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_partition_response(
            response,
            normalized_scope_ledger=ledger,
            partition=partition,
            require_local_closure=False,
        )

    assert caught.value.code == "graph_partition_constraint_node_fact_missing"
    assert caught.value.details == {
        "node_id": "P001_C_VIEW",
        "fact_ids": ["F_CHILD_BEHAVIOR"],
        "fact_kinds": ["action"],
        "allowed_constraint_fact_ids": [],
        "invalid_constraint_anchor_fact_ids": ["F_CHILD_BEHAVIOR"],
        "repair_hint": "A constraint node must cite at least one target fact whose fact_kind is constraint. Use capability for a standalone observable/action/UI requirement even when its wording contains a negative condition.",
    }


def test_partition_node_phase_allows_required_constraint_with_required_capability_partner() -> None:
    ledger = _normalized_ledger()
    source_fact = next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    source_binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    ledger["evidence_facts"].append(
        {
            **copy.deepcopy(source_fact),
            "fact_id": "F_CHILD_CONSTRAINT",
            "fact_kind": "constraint",
        }
    )
    ledger["fact_bindings"].append(
        {**copy.deepcopy(source_binding), "fact_id": "F_CHILD_CONSTRAINT"}
    )
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"][0]["fact_ids"].append("F_CHILD_CONSTRAINT")
    response["nodes"].append(
        {
            "node_id": "P001_K_VISIBLE",
            "kind": "constraint",
            "name": "记录可见约束",
            "aliases": [],
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["F_CHILD_CONSTRAINT"],
            "confidence": 0.9,
        }
    )

    frozen = validate_requirement_graph_partition_response(
        response,
        normalized_scope_ledger=ledger,
        partition=partition,
        require_local_closure=False,
    )

    assert [item["kind"] for item in frozen["nodes"]] == [
        "capability",
        "constraint",
    ]


def test_mechanical_merge_connects_standalone_constraint_to_frozen_owner_scope() -> None:
    ledger = _normalized_ledger()
    source_fact = next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    source_binding = next(
        item
        for item in ledger["fact_bindings"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )
    ledger["evidence_facts"].append(
        {
            **copy.deepcopy(source_fact),
            "fact_id": "F_CHILD_CONSTRAINT",
            "fact_kind": "constraint",
        }
    )
    ledger["fact_bindings"].append(
        {**copy.deepcopy(source_binding), "fact_id": "F_CHILD_CONSTRAINT"}
    )
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"].append(
        {
            "node_id": "P001_K_VISIBLE",
            "kind": "constraint",
            "name": "记录可见约束",
            "aliases": [],
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["F_CHILD_CONSTRAINT"],
            "confidence": 0.9,
        }
    )
    frozen = validate_requirement_graph_partition_response(
        response,
        normalized_scope_ledger=ledger,
        partition=partition,
        require_local_closure=False,
    )

    graph = build_mechanical_requirement_graph(ledger, [frozen])

    constrained = next(
        edge for edge in graph["edges"] if edge["type"] == "constrained_by"
    )
    assert constrained["source_node_id"] == "S_CHILD"
    assert constrained["target_node_id"] == "P001_K_VISIBLE"
    assert constrained["fact_ids"] == ["F_CHILD_CONSTRAINT"]


def test_local_nodes_are_frozen_before_edge_validation() -> None:
    ledger = _normalized_ledger()
    next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )["fact_kind"] = "constraint"
    partition = partition_requirement_graph_facts(ledger)[0]
    node_response = _partition_stage_response()
    node_response["nodes"] = [
        {
            "node_id": "P001_E_RECORD",
            "kind": "entity",
            "name": "记录",
            "aliases": [],
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "confidence": 0.9,
        },
        {
            "node_id": "P001_K_VISIBLE",
            "kind": "constraint",
            "name": "记录可见约束",
            "aliases": [],
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "confidence": 0.9,
        },
    ]
    frozen_nodes = validate_requirement_graph_partition_response(
        node_response,
        normalized_scope_ledger=ledger,
        partition=partition,
        require_local_closure=False,
    )

    edge_result = validate_requirement_graph_local_edge_response(
        {
            "confidence": 0.9,
            "edges": [
                {
                    "edge_id": "P001_E_CONSTRAINT",
                    "type": "constrained_by",
                    "source_node_id": "P001_E_RECORD",
                    "target_node_id": "P001_K_VISIBLE",
                    "fact_ids": ["F_CHILD_BEHAVIOR"],
                    "ownership_role": "none",
                    "trigger": "",
                    "result_state": "",
                    "transferred_entity_node_ids": [],
                    "confidence": 0.9,
                }
            ],
        },
        normalized_scope_ledger=ledger,
        partition=partition,
        local_result=frozen_nodes,
    )

    assert edge_result["edges"][0]["edge_id"] == "P001_E_CONSTRAINT"
    assert frozen_nodes["edges"] == []


def test_transferred_entity_reference_counts_as_local_incident_node() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    response = _partition_stage_response()
    response["nodes"].extend(
        [
            {
                "node_id": "P001_T_VIEW",
                "kind": "trigger",
                "name": "触发查看",
                "aliases": [],
                "scope_status": "",
                "boundary_status": "resolved",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
                "confidence": 0.9,
            },
            {
                "node_id": "P001_E_RECORD",
                "kind": "entity",
                "name": "相关记录",
                "aliases": [],
                "scope_status": "",
                "boundary_status": "resolved",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
                "confidence": 0.9,
            },
        ]
    )
    response["edges"] = [
        {
            "edge_id": "P001_E_TRIGGER",
            "type": "triggers",
            "source_node_id": "P001_T_VIEW",
            "target_node_id": "P001_C_VIEW",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "ownership_role": "none",
            "trigger": "进入子职责",
            "result_state": "记录可见",
            "transferred_entity_node_ids": ["P001_E_RECORD"],
            "confidence": 0.9,
        }
    ]

    validated = validate_requirement_graph_partition_response(
        response,
        normalized_scope_ledger=ledger,
        partition=partition,
    )

    assert validated["edges"][0]["transferred_entity_node_ids"] == [
        "P001_E_RECORD"
    ]


def test_local_edge_invalid_payload_reports_actionable_details() -> None:
    ledger = _normalized_ledger()
    next(
        item
        for item in ledger["evidence_facts"]
        if item["fact_id"] == "F_CHILD_BEHAVIOR"
    )["fact_kind"] = "constraint"
    partition = partition_requirement_graph_facts(ledger)[0]
    node_response = _partition_stage_response()
    node_response["nodes"] = [
        {
            "node_id": "P001_E_RECORD",
            "kind": "entity",
            "name": "记录",
            "aliases": [],
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "confidence": 0.9,
        },
        {
            "node_id": "P001_K_VISIBLE",
            "kind": "constraint",
            "name": "记录可见约束",
            "aliases": [],
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "confidence": 0.9,
        },
    ]
    frozen_nodes = validate_requirement_graph_partition_response(
        node_response,
        normalized_scope_ledger=ledger,
        partition=partition,
        require_local_closure=False,
    )

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_local_edge_response(
            {
                "confidence": 0.9,
                "edges": [
                    {
                        "edge_id": "P001_E_CONSTRAINT",
                        "type": "constrained_by",
                        "source_node_id": "P001_E_RECORD",
                        "target_node_id": "P001_K_VISIBLE",
                        "fact_ids": [],
                        "ownership_role": "none",
                        "trigger": "",
                        "result_state": "",
                        "transferred_entity_node_ids": [],
                        "confidence": 0.9,
                    }
                ],
            },
            normalized_scope_ledger=ledger,
            partition=partition,
            local_result=frozen_nodes,
        )

    assert caught.value.code == "graph_partition_id_list_invalid"
    assert caught.value.path == "$.edges[0].fact_ids"
    assert caught.value.details == {
        "reason": "empty_not_allowed",
        "repair_hint": "omit the parent object when no fact proves it; otherwise cite one or more unique target_fact_ids",
    }

    forbidden_type = {
        "confidence": 0.9,
        "edges": [
            {
                "edge_id": "P001_E_INTERACTION",
                "type": "interacts_with",
                "source_node_id": "P001_E_RECORD",
                "target_node_id": "P001_K_VISIBLE",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
                "ownership_role": "none",
                "trigger": "查看",
                "result_state": "可见",
                "transferred_entity_node_ids": [],
                "confidence": 0.9,
            }
        ],
    }
    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_local_edge_response(
            forbidden_type,
            normalized_scope_ledger=ledger,
            partition=partition,
            local_result=frozen_nodes,
        )

    assert caught.value.code == "graph_partition_edge_invalid"
    assert caught.value.details["reasons"] == ["local_edge_type_forbidden"]
    assert "interacts_with" not in caught.value.details[
        "allowed_local_edge_types"
    ]


def test_mechanical_merge_uses_frozen_scope_hierarchy_and_ownership() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    local = validate_requirement_graph_partition_response(
        _partition_stage_response(),
        normalized_scope_ledger=ledger,
        partition=partition,
    )

    graph = build_mechanical_requirement_graph(ledger, [local])

    assert {
        (edge["type"], edge["source_node_id"], edge["target_node_id"])
        for edge in graph["edges"]
    } >= {
        ("contains", "S_PARENT", "S_CHILD"),
        ("owns", "S_CHILD", "P001_C_VIEW"),
    }
    owns = next(edge for edge in graph["edges"] if edge["type"] == "owns")
    assert owns["fact_ids"] == ["F_CHILD_BEHAVIOR"]
    assert owns["ownership_role"] == "primary"


def test_mechanical_scope_carries_required_binding_anchor_for_required_edges() -> None:
    ledger = _normalized_ledger()
    for fact in ledger["evidence_facts"]:
        if fact["fact_id"] in {"F_PARENT_MEMBERSHIP", "F_CHILD_SUPPORT"}:
            fact["requirement_level"] = "optional"
    partition = partition_requirement_graph_facts(ledger)[0]
    local = validate_requirement_graph_partition_response(
        _partition_stage_response(),
        normalized_scope_ledger=ledger,
        partition=partition,
    )

    graph = build_mechanical_requirement_graph(ledger, [local])
    child_scope = next(
        node for node in graph["nodes"] if node["node_id"] == "S_CHILD"
    )

    assert "F_CHILD_BEHAVIOR" in child_scope["fact_ids"]
    normalized = normalize_requirement_semantic_graph(
        {
            "semantic_contract_version": "requirement-semantic-v2",
            "evidence_facts": ledger["evidence_facts"],
            "semantic_graph": graph,
        },
        source_text="",
        evidence_validator=lambda evidence, _source: bool(evidence),
    )
    assert normalized["publishable"] is True, normalized["errors"]


def test_mechanical_merge_projects_source_relation_with_child_support_facts() -> None:
    ledger = _normalized_ledger()
    child = next(
        boundary
        for boundary in ledger["boundaries"]
        if boundary["boundary_id"] == "S_CHILD"
    )
    child["membership_relation_ids"] = ["R001"]
    child["membership_fact_ids"] = []
    partition = partition_requirement_graph_facts(ledger)[0]
    local = validate_requirement_graph_partition_response(
        _partition_stage_response(),
        normalized_scope_ledger=ledger,
        partition=partition,
    )

    graph = build_mechanical_requirement_graph(ledger, [local])

    contains = next(
        edge for edge in graph["edges"] if edge["type"] == "contains"
    )
    assert contains["source_node_id"] == "S_PARENT"
    assert contains["target_node_id"] == "S_CHILD"
    assert contains["fact_ids"] == ["F_CHILD_SUPPORT"]


def test_large_input_route_reuses_final_graph_gate(monkeypatch: Any) -> None:
    import modules.test_generation_components.control.requirement_graph_stage_compiler as compiler_module

    monkeypatch.setattr(
        compiler_module,
        "DEFAULT_GRAPH_STAGE_PARTITION_FACT_THRESHOLD",
        2,
    )
    client = _ScriptedClient(_json_response(_partition_stage_response()))

    result = _compile(client)

    assert result.success is True
    assert result.status == "validated"
    assert result.diagnostics["semantic_compile_mode"] == "partitioned"
    assert result.diagnostics["partition_compile_fact_shard_count"] == 1
    assert result.diagnostics["partition_compile_completed_fact_shard_count"] == 1
    assert result.diagnostics["partition_compile_workflow_called"] is False
    assert result.diagnostics["semantic_compile_physical_call_count"] == 1
    assert len(client.calls) == 1


def test_partitioned_final_gate_exception_returns_persistable_diagnostics(
    monkeypatch: Any,
) -> None:
    import modules.test_generation_components.control.requirement_graph_stage_compiler as compiler_module

    monkeypatch.setattr(
        compiler_module,
        "DEFAULT_GRAPH_STAGE_PARTITION_FACT_THRESHOLD",
        2,
    )
    client = _ScriptedClient(_json_response(_partition_stage_response()))

    def _raise_final_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("final gate crashed")

    monkeypatch.setattr(compiler_module, "_evaluate_candidate", _raise_final_gate)

    result = _compile(client)

    assert result.success is False
    assert result.status == "contract_invalid"
    assert result.assembled_candidate == {}
    assert result.evaluation == {}
    assert result.diagnostics["partition_compile_success"] is True
    assert result.diagnostics["semantic_compile_final_gate_error_code"] == (
        "graph_partition_final_gate_exception"
    )
    assert result.diagnostics["semantic_compile_final_gate_error_type"] == (
        "RuntimeError"
    )
    assert result.diagnostics["semantic_compile_final_gate_error_message"] == (
        "final gate crashed"
    )
    final_attempt = result.diagnostics["semantic_compile_attempts"][-1]
    assert final_attempt["phase"] == "final_gate"
    assert final_attempt["error_code"] == "graph_partition_final_gate_exception"


def test_partition_relation_and_workflow_references_are_frozen() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    local = validate_requirement_graph_partition_response(
        _partition_stage_response(),
        normalized_scope_ledger=ledger,
        partition=partition,
    )
    graph = build_mechanical_requirement_graph(ledger, [local])
    relation = {
        "confidence": 0.92,
        "edges": [
            {
                "edge_id": "R001_E_START",
                "type": "triggers",
                "source_node_id": "S_CHILD",
                "target_node_id": "P001_C_VIEW",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
                "ownership_role": "none",
                "trigger": "进入子职责",
                "result_state": "可查看记录",
                "transferred_entity_node_ids": [],
                "confidence": 0.92,
            }
        ],
    }

    validated_relation = validate_requirement_graph_relation_response(
        relation,
        graph=graph,
        relation_shard_id="R001",
        fact_ids=("F_CHILD_BEHAVIOR",),
    )
    graph["edges"].extend(validated_relation["edges"])
    empty_workflow = validate_requirement_graph_workflow_response(
        {
            "confidence": 0.9,
            "primary_flow": {"node_ids": [], "edge_ids": []},
            "workflow_blueprints": [],
        },
        graph=graph,
    )

    assert validated_relation["edges"][0]["edge_id"] == "R001_E_START"
    assert empty_workflow["primary_flow"] == {"node_ids": [], "edge_ids": []}
    invalid_workflow = {
        "confidence": 0.9,
        "primary_flow": {
            "node_ids": ["S_CHILD", "P001_C_UNKNOWN"],
            "edge_ids": ["R001_E_START"],
        },
        "workflow_blueprints": [],
    }
    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            invalid_workflow,
            graph=graph,
        )
    assert caught.value.code == "graph_partition_reference_unknown"


def test_partition_workflow_reports_directed_edge_disconnection() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    local = validate_requirement_graph_partition_response(
        _partition_stage_response(),
        normalized_scope_ledger=ledger,
        partition=partition,
    )
    graph = build_mechanical_requirement_graph(ledger, [local])
    graph["nodes"].append(
        {
            "node_id": "P001_T_OTHER",
            "kind": "trigger",
            "name": "另一触发点",
            "aliases": [],
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "confidence": 0.9,
        }
    )
    graph["edges"].extend(
        [
            {
                "edge_id": "P001_E_FIRST",
                "type": "triggers",
                "source_node_id": "S_CHILD",
                "target_node_id": "P001_C_VIEW",
            },
            {
                "edge_id": "P001_E_REVERSED",
                "type": "triggers",
                "source_node_id": "P001_T_OTHER",
                "target_node_id": "P001_C_VIEW",
            },
        ]
    )

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            {
                "confidence": 0.9,
                "primary_flow": {
                    "node_ids": [
                        "S_CHILD",
                        "P001_C_VIEW",
                        "P001_T_OTHER",
                    ],
                    "edge_ids": ["P001_E_FIRST", "P001_E_REVERSED"],
                },
                "workflow_blueprints": [],
            },
            graph=graph,
        )

    assert caught.value.code == "graph_partition_primary_flow_invalid"
    assert caught.value.details == {
        "reason": "directed_edge_disconnected",
        "index": 1,
        "edge_id": "P001_E_REVERSED",
        "expected_source_node_id": "P001_C_VIEW",
        "expected_target_node_id": "P001_T_OTHER",
        "actual_source_node_id": "P001_T_OTHER",
        "actual_target_node_id": "P001_C_VIEW",
        "repair_hint": "Do not reverse an existing edge. Select a shorter valid directed path, including a single edge with its source and target nodes, or return both arrays empty.",
    }


def _disconnected_control_chain_graph() -> dict[str, Any]:
    node_ids = [
        "P026_N01",
        "P026_N02",
        "P021_N06",
        "P021_N07",
        "P021_N01",
    ]
    return {
        "nodes": [
            {
                "node_id": node_id,
                "kind": "capability",
                "name": node_id,
                "fact_ids": [f"{node_id}_FACT"],
            }
            for node_id in node_ids
        ],
        "edges": [
            {
                "edge_id": "P026_E01",
                "type": "transitions",
                "source_node_id": "P026_N01",
                "target_node_id": "P026_N02",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
            },
            {
                "edge_id": "P021_E02",
                "type": "transitions",
                "source_node_id": "P021_N06",
                "target_node_id": "P021_N07",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
            },
            {
                "edge_id": "P021_E03",
                "type": "transitions",
                "source_node_id": "P021_N07",
                "target_node_id": "P021_N01",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
            },
        ],
    }


def test_workflow_input_projects_disconnected_chains_as_separate_paths() -> None:
    payload = json.loads(
        build_requirement_graph_workflow_user_input(
            _normalized_ledger(),
            _disconnected_control_chain_graph(),
        )
    )

    assert payload["input_version"] == "3"
    assert [
        (component["node_ids"], component["edge_ids"])
        for component in payload["control_components"]
    ] == [
        (
            ["P021_N01", "P021_N06", "P021_N07"],
            ["P021_E02", "P021_E03"],
        ),
        (["P026_N01", "P026_N02"], ["P026_E01"]),
    ]
    assert [
        (candidate["component_id"], candidate["node_ids"], candidate["edge_ids"])
        for candidate in payload["directed_path_candidates"]
    ] == [
        (
            "C001",
            ["P021_N06", "P021_N07", "P021_N01"],
            ["P021_E02", "P021_E03"],
        ),
        (
            "C002",
            ["P026_N01", "P026_N02"],
            ["P026_E01"],
        ),
    ]
    assert all(
        len(candidate["edge_ids"]) == len(candidate["node_ids"]) - 1
        for candidate in payload["directed_path_candidates"]
    )
    contracts = {
        contract["node_id"]: contract
        for contract in payload["workflow_step_contracts"]
    }
    assert contracts["P026_N01"]["required"] is True
    assert contracts["P026_N01"]["identity_fact_ids"] == [
        "P026_N01_FACT"
    ]


def _workflow_contract_graph() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "node_id": "S_OWNER",
                "kind": "scope",
                "name": "职责",
                "fact_ids": ["F_SCOPE"],
            },
            {
                "node_id": "N_ENTRY",
                "kind": "capability",
                "name": "进入",
                "fact_ids": ["F_ENTRY"],
            },
            {
                "node_id": "N_DONE",
                "kind": "capability",
                "name": "完成",
                "fact_ids": ["F_DONE"],
            },
        ],
        "edges": [
            {
                "edge_id": "E_OWN_ENTRY",
                "type": "owns",
                "source_node_id": "S_OWNER",
                "target_node_id": "N_ENTRY",
                "fact_ids": ["F_ENTRY"],
            },
            {
                "edge_id": "E_OWN_DONE",
                "type": "owns",
                "source_node_id": "S_OWNER",
                "target_node_id": "N_DONE",
                "fact_ids": ["F_DONE"],
            },
            {
                "edge_id": "E_FLOW",
                "type": "transitions",
                "source_node_id": "N_ENTRY",
                "target_node_id": "N_DONE",
                "fact_ids": ["F_ENTRY", "F_DONE"],
            },
        ],
    }


def _workflow_contract_step(
    *,
    step_id: str,
    node_fact_id: str,
    state_in: str,
    state_out: str,
    terminal: bool,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "label": step_id,
        "action": step_id,
        "stage_kind": "commit" if terminal else "entry",
        "actor": "用户",
        "state_in": state_in,
        "state_out": state_out,
        "required": True,
        "terminal": terminal,
        "critical": True,
        "blocking": False,
        "destructive": False,
        "scope_candidates": [
            {
                "scope_id": "S_OWNER",
                "role": "primary",
                "fact_ids": [node_fact_id],
                "confidence": 1.0,
            }
        ],
        "relation_ids": ["E_FLOW"],
        "required_states": [],
        "produced_states": [],
        "match_keywords": [],
        "fact_ids": [node_fact_id],
    }


def _workflow_contract_response() -> dict[str, Any]:
    return {
        "confidence": 0.9,
        "primary_flow": {
            "node_ids": ["N_ENTRY", "N_DONE"],
            "edge_ids": ["E_FLOW"],
        },
        "workflow_blueprints": [
            {
                "workflow_id": "WF_MAIN",
                "name": "主流程",
                "primary": True,
                "confidence": 0.9,
                "initial_state": "ready",
                "required_stage_ids": ["STEP_ENTRY", "STEP_DONE"],
                "terminal_states": ["done"],
                "fact_ids": ["F_ENTRY", "F_DONE"],
                "steps": [
                    _workflow_contract_step(
                        step_id="STEP_ENTRY",
                        node_fact_id="F_ENTRY",
                        state_in="ready",
                        state_out="entered",
                        terminal=False,
                    ),
                    _workflow_contract_step(
                        step_id="STEP_DONE",
                        node_fact_id="F_DONE",
                        state_in="entered",
                        state_out="done",
                        terminal=True,
                    ),
                ],
            }
        ],
    }


def test_partition_workflow_accepts_mechanical_step_contract() -> None:
    validated = validate_requirement_graph_workflow_response(
        _workflow_contract_response(),
        graph=_workflow_contract_graph(),
    )

    assert validated["primary_flow"]["edge_ids"] == ["E_FLOW"]
    assert validated["workflow_blueprints"][0]["steps"][0][
        "scope_candidates"
    ][0]["scope_id"] == "S_OWNER"


def test_partition_workflow_rejects_passive_system_display_as_primary_entry() -> None:
    response = _workflow_contract_response()
    entry = response["workflow_blueprints"][0]["steps"][0]
    entry["label"] = "Display list empty state"
    entry["action"] = "Display list empty state"
    entry["actor"] = "系统"

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            response,
            graph=_workflow_contract_graph(),
        )

    assert caught.value.code == "graph_partition_primary_entry_invalid"
    assert caught.value.details["reason"] == "passive_automated_entry"


def test_partition_workflow_allows_explicit_automated_trigger_as_primary_entry() -> None:
    response = _workflow_contract_response()
    entry = response["workflow_blueprints"][0]["steps"][0]
    entry["label"] = "Scheduled request triggers processing"
    entry["action"] = "Scheduled request triggers processing"
    entry["actor"] = "system_service"

    validated = validate_requirement_graph_workflow_response(
        response,
        graph=_workflow_contract_graph(),
    )

    assert validated["primary_flow"]["node_ids"][0] == "N_ENTRY"


def test_partition_workflow_rejects_optional_primary_flow_step() -> None:
    response = _workflow_contract_response()
    response["workflow_blueprints"][0]["steps"][1]["required"] = False

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            response,
            graph=_workflow_contract_graph(),
        )

    assert caught.value.code == "graph_partition_workflow_step_contract_invalid"
    assert caught.value.details["reason"] == (
        "node_identity_or_required_invalid"
    )
    assert caught.value.details["node_id"] == "N_DONE"
    assert caught.value.details["expected_required"] is True


def test_partition_workflow_rejects_unbound_typed_state_fact() -> None:
    response = _workflow_contract_response()
    response["workflow_blueprints"][0]["steps"][0]["produced_states"] = [
        {
            "entity": "作品",
            "state": "完成",
            "source": "current_stage",
            "scope": "workflow",
            "polarity": "positive",
            "temporal": "during_case",
            "fact_ids": ["F_UNBOUND"],
            "confidence": 0.9,
        }
    ]

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            response,
            graph=_workflow_contract_graph(),
        )

    assert caught.value.code == "graph_partition_workflow_step_contract_invalid"
    assert caught.value.details["reason"] == "typed_state_fact_ids_invalid"
    assert caught.value.details["node_id"] == "N_ENTRY"


def test_partition_workflow_rejects_unproduced_previous_stage_state() -> None:
    response = _workflow_contract_response()
    first_step, second_step = response["workflow_blueprints"][0]["steps"]
    first_step["produced_states"] = [
        {
            "entity": "document",
            "state": "recognized",
            "source": "current_stage",
            "scope": "workflow",
            "polarity": "positive",
            "temporal": "after_case",
            "fact_ids": ["F_ENTRY"],
            "confidence": 0.9,
        }
    ]
    second_step["required_states"] = [
        {
            "entity": "document",
            "state": "reviewed",
            "source": "previous_stage",
            "scope": "workflow",
            "polarity": "positive",
            "temporal": "after_previous_stage",
            "fact_ids": ["F_ENTRY"],
            "confidence": 0.9,
        }
    ]

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            response,
            graph=_workflow_contract_graph(),
        )

    assert caught.value.code == "graph_partition_workflow_step_contract_invalid"
    assert caught.value.details["reason"] == "previous_stage_state_not_produced"
    assert caught.value.details["required_state_identity"] == [
        "document",
        "reviewed",
        "workflow",
        "positive",
    ]
    assert caught.value.details["previous_produced_state_identities"] == [
        ["document", "recognized", "workflow", "positive"]
    ]


def test_partition_workflow_rejects_previous_stage_state_on_first_step() -> None:
    response = _workflow_contract_response()
    response["workflow_blueprints"][0]["steps"][0]["required_states"] = [
        {
            "entity": "document",
            "state": "ready",
            "source": "previous_stage",
            "scope": "workflow",
            "polarity": "positive",
            "temporal": "after_previous_stage",
            "fact_ids": ["F_ENTRY"],
            "confidence": 0.9,
        }
    ]

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            response,
            graph=_workflow_contract_graph(),
        )

    assert caught.value.code == "graph_partition_workflow_step_contract_invalid"
    assert caught.value.details["reason"] == (
        "previous_stage_state_without_predecessor"
    )
    assert caught.value.details["step_index"] == 0
    assert caught.value.details["previous_produced_state_identities"] == []


def test_partition_workflow_accepts_matching_previous_stage_state() -> None:
    response = _workflow_contract_response()
    first_step, second_step = response["workflow_blueprints"][0]["steps"]
    first_step["produced_states"] = [
        {
            "entity": "document",
            "state": "recognized",
            "source": "current_stage",
            "scope": "workflow",
            "polarity": "positive",
            "temporal": "after_case",
            "fact_ids": ["F_ENTRY"],
            "confidence": 0.9,
        }
    ]
    second_step["required_states"] = [
        {
            "entity": "document",
            "state": "recognized",
            "source": "previous_stage",
            "scope": "workflow",
            "polarity": "positive",
            "temporal": "after_previous_stage",
            "fact_ids": ["F_ENTRY"],
            "confidence": 0.9,
        }
    ]

    validated = validate_requirement_graph_workflow_response(
        response,
        graph=_workflow_contract_graph(),
    )

    assert validated["workflow_blueprints"][0]["steps"][1][
        "required_states"
    ][0]["state"] == "recognized"


def test_partition_workflow_rejects_invalid_stage_kind_and_state_enum() -> None:
    response = _workflow_contract_response()
    response["workflow_blueprints"][0]["steps"][0]["stage_kind"] = "action"

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            response,
            graph=_workflow_contract_graph(),
        )

    assert caught.value.details["reason"] == "stage_kind_invalid"
    assert "action" not in caught.value.details["allowed_stage_kinds"]

    response = _workflow_contract_response()
    response["workflow_blueprints"][0]["steps"][0]["required_states"] = [
        {
            "entity": "作品",
            "state": "已进入",
            "source": "current_stage",
            "scope": "workflow",
            "polarity": "positive",
            "temporal": "during_case",
            "fact_ids": ["F_ENTRY"],
            "confidence": 0.9,
        }
    ]

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            response,
            graph=_workflow_contract_graph(),
        )

    assert caught.value.details["reason"] == "typed_state_schema_invalid"
    assert caught.value.details["invalid_fields"] == ["source"]


def test_partition_workflow_rejects_declared_state_closure_mismatch() -> None:
    response = _workflow_contract_response()
    workflow = response["workflow_blueprints"][0]
    workflow["initial_state"] = "wrong_initial"
    workflow["terminal_states"] = ["wrong_terminal"]

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            response,
            graph=_workflow_contract_graph(),
        )

    assert caught.value.code == "graph_partition_workflow_closure_invalid"
    assert caught.value.details["issues"] == [
        {
            "field": "initial_state",
            "expected": "ready",
            "actual": "wrong_initial",
        },
        {
            "field": "terminal_states",
            "expected": ["done"],
            "actual": ["wrong_terminal"],
        },
    ]


def test_partition_workflow_rejects_typed_object_in_scalar_state_field() -> None:
    response = _workflow_contract_response()
    workflow = response["workflow_blueprints"][0]
    workflow["initial_state"] = {
        "entity": "作品",
        "state": "ready",
        "source": "same_case_setup",
    }
    workflow["terminal_states"] = [{"state": "done"}]

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            response,
            graph=_workflow_contract_graph(),
        )

    assert caught.value.code == (
        "graph_partition_workflow_closure_type_invalid"
    )
    assert caught.value.details["issues"] == [
        {"field": "initial_state", "actual_type": "dict"},
        {
            "field": "terminal_states",
            "actual_type": "list",
            "invalid_item_indexes": [0],
        },
    ]


def test_partition_workflow_rejects_cross_component_path_at_exact_break() -> None:
    graph = _disconnected_control_chain_graph()

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(
            {
                "confidence": 0.9,
                "primary_flow": {
                    "node_ids": [
                        "P026_N01",
                        "P026_N02",
                        "P021_N06",
                        "P021_N07",
                        "P021_N01",
                    ],
                    "edge_ids": ["P026_E01", "P021_E02", "P021_E03"],
                },
                "workflow_blueprints": [],
            },
            graph=graph,
        )

    assert caught.value.code == "graph_partition_primary_flow_invalid"
    assert caught.value.details == {
        "reason": "cross_component_path",
        "component_ids": ["C001", "C002"],
        "break_index": 1,
        "source_node_id": "P026_N02",
        "target_node_id": "P021_N06",
        "source_component_id": "C002",
        "target_component_id": "C001",
        "directed_path_candidate_ids": [
            "PATH_C001_001",
            "PATH_C002_001",
        ],
        "repair_hint": (
            "Do not concatenate disconnected paths. Select nodes and edges "
            "from exactly one control component, preferably one complete "
            "directed_path_candidate, or return both arrays empty."
        ),
    }


def test_relation_contract_rejects_interaction_inside_same_scope() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    local = validate_requirement_graph_partition_response(
        _partition_stage_response(),
        normalized_scope_ledger=ledger,
        partition=partition,
    )
    graph = build_mechanical_requirement_graph(ledger, [local])
    relation = {
        "confidence": 0.92,
        "edges": [
            {
                "edge_id": "R001_E_INVALID",
                "type": "interacts_with",
                "source_node_id": "S_CHILD",
                "target_node_id": "P001_C_VIEW",
                "fact_ids": ["F_CHILD_BEHAVIOR"],
                "ownership_role": "none",
                "trigger": "查看记录",
                "result_state": "记录可见",
                "transferred_entity_node_ids": [],
                "confidence": 0.92,
            }
        ],
    }

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_relation_response(
            relation,
            graph=graph,
            relation_shard_id="R001",
            fact_ids=("F_CHILD_BEHAVIOR",),
        )

    assert caught.value.code == "graph_partition_relation_semantic_invalid"
    assert "interaction_same_scope" in caught.value.details["issues"][0][
        "reasons"
    ]


def test_partition_workflow_rejects_step_edge_id_alias() -> None:
    ledger = _normalized_ledger()
    partition = partition_requirement_graph_facts(ledger)[0]
    local = validate_requirement_graph_partition_response(
        _partition_stage_response(),
        normalized_scope_ledger=ledger,
        partition=partition,
    )
    graph = build_mechanical_requirement_graph(ledger, [local])
    graph["edges"].append(
        {
            "edge_id": "R001_E_START",
            "type": "triggers",
            "source_node_id": "S_CHILD",
            "target_node_id": "P001_C_VIEW",
            "fact_ids": ["F_CHILD_BEHAVIOR"],
            "ownership_role": "none",
            "trigger": "进入子职责",
            "result_state": "可查看记录",
            "transferred_entity_node_ids": [],
            "confidence": 0.92,
        }
    )
    step = {
        "id": "STEP_1",
        "label": "进入子职责",
        "action": "进入子职责",
        "stage_kind": "action",
        "actor": "用户",
        "state_in": "ready",
        "state_out": "viewable",
        "required": True,
        "terminal": False,
        "critical": True,
        "blocking": False,
        "destructive": False,
        "scope_candidates": [],
        "edge_id": "R001_E_START",
        "required_states": [],
        "produced_states": [],
        "match_keywords": [],
        "fact_ids": ["F_CHILD_BEHAVIOR"],
    }
    response = {
        "confidence": 0.9,
        "primary_flow": {
            "node_ids": ["S_CHILD", "P001_C_VIEW"],
            "edge_ids": ["R001_E_START"],
        },
        "workflow_blueprints": [
            {
                "workflow_id": "WF_MAIN",
                "name": "主流程",
                "primary": True,
                "confidence": 0.9,
                "initial_state": "ready",
                "required_stage_ids": ["STEP_1"],
                "terminal_states": ["viewable"],
                "fact_ids": ["F_CHILD_BEHAVIOR"],
                "steps": [step],
            }
        ],
    }

    with pytest.raises(RequirementGraphPartitionContractError) as caught:
        validate_requirement_graph_workflow_response(response, graph=graph)

    assert caught.value.code == "graph_partition_fields_invalid"
    assert caught.value.details == {
        "missing": ["relation_ids"],
        "extra": ["edge_id"],
    }


def test_relation_input_uses_only_target_fact_graph_view() -> None:
    ledger = _normalized_ledger()
    ledger["evidence_facts"][-1]["fact_kind"] = "interaction"
    partition = partition_requirement_graph_facts(ledger)[0]
    local = validate_requirement_graph_partition_response(
        _partition_stage_response(),
        normalized_scope_ledger=ledger,
        partition=partition,
    )
    graph = build_mechanical_requirement_graph(ledger, [local])
    graph["nodes"].append(
        {
            "node_id": "C_IRRELEVANT",
            "kind": "capability",
            "name": "无关能力",
            "aliases": [],
            "scope_status": "",
            "boundary_status": "resolved",
            "fact_ids": ["F_PARENT_MEMBERSHIP"],
            "confidence": 0.9,
        }
    )
    graph["edges"].append(
        {
            "edge_id": "E_IRRELEVANT",
            "type": "triggers",
            "source_node_id": "S_PARENT",
            "target_node_id": "C_IRRELEVANT",
            "fact_ids": ["F_PARENT_MEMBERSHIP"],
            "ownership_role": "none",
            "trigger": "",
            "result_state": "",
            "transferred_entity_node_ids": [],
            "confidence": 0.9,
        }
    )

    payload = json.loads(
        build_requirement_graph_relation_user_input(
            ledger,
            graph,
            relation_shard_id="R001",
            fact_ids=("F_CHILD_BEHAVIOR",),
        )
    )

    assert {node["node_id"] for node in payload["nodes"]} == {
        "P001_C_VIEW",
        "S_CHILD",
    }
    assert "S_CHILD" in payload["active_scope_ids"]
    assert "C_IRRELEVANT" not in {
        node["node_id"] for node in payload["nodes"]
    }
    assert "E_IRRELEVANT" not in {
        edge["edge_id"] for edge in payload["existing_edges"]
    }


def test_partition_phase_failure_does_not_publish_partial_graph(
    monkeypatch: Any,
) -> None:
    import modules.test_generation_components.control.requirement_graph_stage_compiler as compiler_module

    monkeypatch.setattr(
        compiler_module,
        "DEFAULT_GRAPH_STAGE_PARTITION_FACT_THRESHOLD",
        2,
    )
    client = _ScriptedClient("{}", "{}", "{}", "{}", "{}", "{}")

    result = _compile(client)

    assert result.success is False
    assert result.status == "contract_invalid"
    assert result.assembled_candidate == {}
    assert result.evaluation == {}
    assert len(client.calls) == 6
    assert result.diagnostics["partition_compile_failed_phase"] == "local"
    assert result.diagnostics["partition_compile_completed_fact_shard_count"] == 0
    assert result.diagnostics["semantic_compile_candidate_attempt_count"] == 0
    first_payload = _payload(client.calls[0])
    second_payload = _payload(client.calls[1])
    third_payload = _payload(client.calls[2])
    assert first_payload["attempt"] == 1
    assert first_payload["recompile_reason_codes"] == []
    assert second_payload["attempt"] == 2
    assert second_payload["recompile_reason_codes"] == [
        "graph_partition_fields_invalid"
    ]
    assert second_payload["recompile_feedback"]["code"] == (
        "graph_partition_fields_invalid"
    )
    assert set(second_payload["recompile_feedback"]["details"]["missing"]) == {
        "confidence",
        "nodes",
        "edges",
        "fact_dispositions",
    }
    assert client.calls[0]["user_input"] != client.calls[1]["user_input"]
    assert third_payload["attempt"] == 3
    assert third_payload["recompile_feedback"]["prior_errors"] == [
        second_payload["recompile_feedback"]
    ]


def test_partition_node_invalid_details_reach_third_fresh_attempt(
    monkeypatch: Any,
) -> None:
    import modules.test_generation_components.control.requirement_graph_stage_compiler as compiler_module

    monkeypatch.setattr(
        compiler_module,
        "DEFAULT_GRAPH_STAGE_PARTITION_FACT_THRESHOLD",
        2,
    )
    first = _partition_stage_response()
    first["nodes"][0]["kind"] = "scope"
    second = _partition_stage_response()
    second["nodes"][0]["boundary_status"] = "unresolved"
    client = _ScriptedClient(
        _json_response(first),
        _json_response(second),
        _json_response(_partition_stage_response()),
    )

    result = _compile(client)

    assert result.success is True
    assert len(client.calls) == 3
    second_payload = _payload(client.calls[1])
    third_payload = _payload(client.calls[2])
    assert second_payload["recompile_feedback"] == {
        "code": "graph_partition_node_invalid",
        "details": {
            "node_id": "P001_C_VIEW",
            "reasons": ["invalid_kind"],
            "kind": "scope",
            "boundary_status": "resolved",
        },
    }
    assert third_payload["recompile_feedback"] == {
        "code": "graph_partition_node_invalid",
        "details": {
            "node_id": "P001_C_VIEW",
            "reasons": ["boundary_status_not_resolved"],
            "kind": "capability",
            "boundary_status": "unresolved",
            "required_boundary_status": "resolved",
            "repair_hint": (
                "A2 owner binding is already frozen for this partition. "
                "Set boundary_status=resolved on every emitted local node; "
                "the model must not reopen boundary resolution."
            ),
        },
        "prior_errors": [second_payload["recompile_feedback"]],
    }
