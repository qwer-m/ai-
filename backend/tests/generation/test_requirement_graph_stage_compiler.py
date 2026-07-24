from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from modules.test_generation_components.control.requirement_graph_stage_compiler import (
    RequirementGraphStageCompilationResult,
    compile_requirement_graph_stage,
)
from modules.test_generation_components.control.requirement_fact_ledger import (
    fingerprint_source_evidence_catalog,
    normalize_requirement_fact_ledger,
)
from modules.test_generation_components.control.requirement_scope_ledger import (
    normalize_requirement_scope_ledger,
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
