from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from modules.test_generation_components.control.requirement_fact_ledger import (
    fingerprint_source_evidence_catalog,
    normalize_requirement_fact_ledger,
)
from modules.test_generation_components.control.requirement_scope_ledger import (
    normalize_requirement_scope_boundary_manifest,
)
from modules.test_generation_components.control.requirement_scope_ledger_compiler import (
    RequirementScopeLedgerCompilationResult,
    _partition_binding_targets,
    compile_requirement_scope_ledger,
)


FACT_MEMBERSHIP = "FACT_A1_000000000000000000000001"
FACT_OFFICIAL = "FACT_A1_000000000000000000000002"
FACT_FEEDBACK = "FACT_A1_000000000000000000000003"
FACT_DISCUSSION = "FACT_A1_000000000000000000000004"
FACT_MESSAGE = "FACT_A1_000000000000000000000005"
FACT_CROSS_MODULE = "FACT_A1_000000000000000000000006"

# 这里保留真实需求常见的“父范围、子职责、跨职责交互”数据形态；
# scripted client 只替代外部模型传输，不替代协议规范化与闭合校验。
CATALOG = [
    {
        "ref": "EV_000000000001",
        "quote": "功能域由官方区、反馈区、交流区和消息区四个职责分区组成。",
    },
    {
        "ref": "EV_000000000002",
        "quote": "官方区展示平台发布的公告和课程活动。",
    },
    {
        "ref": "EV_000000000003",
        "quote": "反馈区允许用户提交问题并查看处理状态。",
    },
    {
        "ref": "EV_000000000004",
        "quote": "交流区允许用户发布话题并参与评论。",
    },
    {
        "ref": "EV_000000000005",
        "quote": "消息区汇总系统通知和互动提醒。",
    },
    {
        "ref": "EV_000000000006",
        "quote": "用户评论交流区话题后，话题作者会在消息区收到互动提醒。",
    },
]
CATALOG_FINGERPRINT = fingerprint_source_evidence_catalog(CATALOG)
_DB_SENTINEL = object()


def _fact(
    fact_id: str,
    fact_kind: str,
    statement: str,
    evidence_ref: str,
) -> dict[str, object]:
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


def _fact_declarations() -> list[dict[str, object]]:
    return [
        _fact(
            FACT_MEMBERSHIP,
            "constraint",
            CATALOG[0]["quote"],
            CATALOG[0]["ref"],
        ),
        _fact(
            FACT_OFFICIAL,
            "action",
            CATALOG[1]["quote"],
            CATALOG[1]["ref"],
        ),
        _fact(
            FACT_FEEDBACK,
            "action",
            CATALOG[2]["quote"],
            CATALOG[2]["ref"],
        ),
        _fact(
            FACT_DISCUSSION,
            "action",
            CATALOG[3]["quote"],
            CATALOG[3]["ref"],
        ),
        _fact(
            FACT_MESSAGE,
            "action",
            CATALOG[4]["quote"],
            CATALOG[4]["ref"],
        ),
        _fact(
            FACT_CROSS_MODULE,
            "interaction",
            CATALOG[5]["quote"],
            CATALOG[5]["ref"],
        ),
    ]


def _normalize_fact_fixture(
    *,
    catalog: list[dict[str, str]],
    facts: list[dict[str, object]],
) -> dict[str, Any]:
    normalized = normalize_requirement_fact_ledger(
        {
            "evidence_facts": copy.deepcopy(facts),
            "source_evidence_dispositions": [
                {
                    "evidence_ref": item["ref"],
                    "disposition": "fact_backed",
                }
                for item in catalog
            ],
        },
        source_evidence_catalog=copy.deepcopy(catalog),
        source_catalog_fingerprint=fingerprint_source_evidence_catalog(
            catalog
        ),
    )
    assert normalized["valid"] is True, normalized["errors"]
    return normalized


def _normalized_fact_ledger() -> dict[str, Any]:
    return _normalize_fact_fixture(
        catalog=CATALOG,
        facts=_fact_declarations(),
    )


def _boundary_payload() -> dict[str, object]:
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
                        "fact_ids": [FACT_MEMBERSHIP],
                    }
                ],
            },
            {
                "boundary_id": "SCOPE_OFFICIAL",
                "label": "官方区",
                "decision": "in_scope_leaf",
                "parent_boundary_id": "SCOPE_CURRENT",
                "membership_relation_ids": [],
                "membership_fact_ids": [FACT_MEMBERSHIP],
                "support": [
                    {
                        "signal": "content_ownership",
                        "fact_ids": [FACT_OFFICIAL],
                    }
                ],
            },
            {
                "boundary_id": "SCOPE_FEEDBACK",
                "label": "反馈区",
                "decision": "in_scope_leaf",
                "parent_boundary_id": "SCOPE_CURRENT",
                "membership_relation_ids": [],
                "membership_fact_ids": [FACT_MEMBERSHIP],
                "support": [
                    {
                        "signal": "lifecycle",
                        "fact_ids": [FACT_FEEDBACK],
                    }
                ],
            },
            {
                "boundary_id": "SCOPE_DISCUSSION",
                "label": "交流区",
                "decision": "in_scope_leaf",
                "parent_boundary_id": "SCOPE_CURRENT",
                "membership_relation_ids": [],
                "membership_fact_ids": [FACT_MEMBERSHIP],
                "support": [
                    {
                        "signal": "actor",
                        "fact_ids": [FACT_DISCUSSION],
                    }
                ],
            },
            {
                "boundary_id": "SCOPE_MESSAGE",
                "label": "消息区",
                "decision": "in_scope_leaf",
                "parent_boundary_id": "SCOPE_CURRENT",
                "membership_relation_ids": [],
                "membership_fact_ids": [FACT_MEMBERSHIP],
                "support": [
                    {
                        "signal": "consumer",
                        "fact_ids": [FACT_MESSAGE],
                    }
                ],
            },
        ]
    }


def _boundary_selection_model_response(
    payload: dict[str, object] | None = None,
    *,
    fact_ledger: dict[str, Any] | None = None,
) -> dict[str, object]:
    """把规范清单夹具转换为不含来源拓扑的职责选择 wire。"""

    canonical = copy.deepcopy(payload or _boundary_payload())
    fact_ref_by_id = _scope_fact_ref_by_id(
        fact_ledger or _normalized_fact_ledger()
    )
    records: list[dict[str, object]] = []
    for boundary in canonical.get("boundaries") or []:
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
                        "signal": support.get("signal"),
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


def _membership_response(call: dict[str, Any]) -> str:
    """为冻结选择中的每个非根边界分配一个精确 membership 事实。"""

    payload = _payload(call)
    first_fact_ref = str(payload["frozen_fact_table"]["rows"][0][0])
    return _json_response(
        {
            "membership_assignments": [
                {
                    "boundary_id": boundary["boundary_id"],
                    "membership_kind": "explicit_fact",
                    "membership_ref": first_fact_ref,
                }
                for boundary in payload["frozen_boundary_selection"][
                    "boundaries"
                ]
                if boundary["parent_boundary_id"]
            ]
        }
    )


_BINDING_BY_FACT_ID = {
    FACT_MEMBERSHIP: {
        "fact_id": FACT_MEMBERSHIP,
        "scope_ids": ["SCOPE_CURRENT"],
        "role": "owned_requirement",
    },
    FACT_OFFICIAL: {
        "fact_id": FACT_OFFICIAL,
        "scope_ids": ["SCOPE_OFFICIAL"],
        "role": "owned_requirement",
    },
    FACT_FEEDBACK: {
        "fact_id": FACT_FEEDBACK,
        "scope_ids": ["SCOPE_FEEDBACK"],
        "role": "owned_requirement",
    },
    FACT_DISCUSSION: {
        "fact_id": FACT_DISCUSSION,
        "scope_ids": ["SCOPE_DISCUSSION"],
        "role": "owned_requirement",
    },
    FACT_MESSAGE: {
        "fact_id": FACT_MESSAGE,
        "scope_ids": ["SCOPE_MESSAGE"],
        "role": "owned_requirement",
    },
    FACT_CROSS_MODULE: {
        "fact_id": FACT_CROSS_MODULE,
        "scope_ids": ["SCOPE_DISCUSSION", "SCOPE_MESSAGE"],
        "role": "shared_requirement",
    },
}


def _json_response(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _payload(call: dict[str, Any]) -> dict[str, Any]:
    return json.loads(call["user_input"])


def _scope_fact_ref_by_id(fact_ledger: dict[str, Any]) -> dict[str, str]:
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


def _scope_model_fact_table(fact_ledger: dict[str, Any]) -> dict[str, Any]:
    schema = (
        "fact_ref",
        "fact_kind",
        "statement",
        "requirement_level",
        "priority",
        "testability",
        "confidence",
    )
    fact_ref_by_id = _scope_fact_ref_by_id(fact_ledger)
    facts = sorted(
        (
            item
            for item in fact_ledger["evidence_facts"]
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


def _scope_model_boundary_manifest(
    manifest: dict[str, Any],
    fact_ledger: dict[str, Any],
) -> dict[str, Any]:
    fact_ref_by_id = _scope_fact_ref_by_id(fact_ledger)
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


def _binding_response(call: dict[str, Any]) -> str:
    payload = _payload(call)
    fact_ref_by_id = _scope_fact_ref_by_id(_normalized_fact_ledger())
    fact_id_by_ref = {
        fact_ref: fact_id for fact_id, fact_ref in fact_ref_by_id.items()
    }
    return _json_response(
        {
            "fact_bindings": [
                {
                    "fact_ref": fact_ref,
                    "scope_ids": copy.deepcopy(
                        _BINDING_BY_FACT_ID[fact_id_by_ref[fact_ref]][
                            "scope_ids"
                        ]
                    ),
                    "role": _BINDING_BY_FACT_ID[fact_id_by_ref[fact_ref]][
                        "role"
                    ],
                }
                for fact_ref in payload["target_fact_refs"]
            ]
        }
    )


class _ScriptedClient:
    """记录真实 client 调用形态，并按顺序返回契约测试响应。"""

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
        call = {
            "user_input": user_input,
            "system_prompt": system_prompt,
            **dict(kwargs),
        }
        self.calls.append(call)
        response = self.responses[
            min(len(self.calls) - 1, len(self.responses) - 1)
        ]
        self.last_response_metadata = {
            "http_status": 200,
            "finish_reason": "stop",
        }
        if isinstance(response, tuple) and len(response) == 2:
            response, metadata = response
            self.last_response_metadata = dict(metadata or {})
        if callable(response):
            response = response(call)
        if isinstance(response, Exception):
            raise response
        return str(response)


def _success_client() -> _ScriptedClient:
    return _ScriptedClient(
        _json_response(_boundary_selection_model_response()),
        _membership_response,
        _binding_response,
    )


def _compile(
    client: _ScriptedClient,
    *,
    fact_ledger: dict[str, Any] | None = None,
    source_evidence_catalog: list[dict[str, str]] | None = None,
    max_tokens: int = 4096,
) -> RequirementScopeLedgerCompilationResult:
    return compile_requirement_scope_ledger(
        client=client,
        normalized_fact_ledger=(fact_ledger or _normalized_fact_ledger()),
        source_evidence_catalog=(source_evidence_catalog or CATALOG),
        db=_DB_SENTINEL,
        max_tokens=max_tokens,
        task_type="generation",
        request_timeout_seconds=120,
    )


def _invalid_response(phase: str) -> str:
    if phase == "boundary_selection":
        return _json_response(
            {
                **_boundary_selection_model_response(),
                "membership_assignments": [],
            }
        )
    if phase == "membership":
        return _json_response({"membership_assignments": []})
    return _json_response({"fact_bindings": [], "boundaries": []})


def _phase_attempts(
    result: RequirementScopeLedgerCompilationResult,
    phase: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in result.diagnostics["scope_ledger_compile_attempts"]
        if item["phase"] == phase
    ]


def test_three_phase_compile_publishes_only_the_complete_closed_ledger() -> None:
    client = _success_client()

    result = _compile(client)

    assert result.success is True
    assert result.status == "validated"
    assert result.normalized_ledger["valid"] is True
    assert result.normalized_ledger["errors"] == []
    assert result.normalized_ledger["ledger_version"] == (
        "requirement-scope-ledger-v3"
    )
    assert result.normalized_ledger["fingerprint"]
    assert result.projection["active_scope_ids"] == [
        "SCOPE_CURRENT",
        "SCOPE_DISCUSSION",
        "SCOPE_FEEDBACK",
        "SCOPE_MESSAGE",
        "SCOPE_OFFICIAL",
    ]
    assert len(result.normalized_ledger["fact_bindings"]) == len(CATALOG)
    assert len(client.calls) == 3
    assert _payload(client.calls[0])["input_type"] == (
        "current_requirement_scope_boundary_selection_compile"
    )
    assert _payload(client.calls[1])["input_type"] == (
        "current_requirement_scope_membership_compile"
    )
    assert _payload(client.calls[2])["input_type"] == (
        "current_requirement_scope_binding_compile"
    )
    assert result.diagnostics["scope_ledger_compile_global_status"] == (
        "validated"
    )
    assert result.diagnostics["scope_ledger_binding_completed_shard_count"] == 1
    assert result.diagnostics["scope_ledger_boundary_selection_status"] == (
        "validated"
    )
    assert result.diagnostics["scope_ledger_membership_assignment_status"] == (
        "validated"
    )
    assert result.diagnostics["scope_ledger_membership_assignment_count"] == 4
    assert result.diagnostics["scope_ledger_fingerprint"] == (
        result.normalized_ledger["fingerprint"]
    )


def test_binding_shards_share_global_view_and_partition_targets_exactly() -> None:
    fact_ledger = _normalized_fact_ledger()
    client = _success_client()

    result = _compile(client, fact_ledger=fact_ledger, max_tokens=160)

    assert result.success is True
    selection_input = _payload(client.calls[0])
    membership_input = _payload(client.calls[1])
    binding_inputs = [_payload(call) for call in client.calls[2:]]
    assert len(binding_inputs) > 1
    projected_fact_table = _scope_model_fact_table(fact_ledger)
    assert selection_input["input_version"] == "1"
    assert selection_input["boundary_selection_version"] == (
        "requirement-scope-boundary-selection-v1"
    )
    assert selection_input["frozen_fact_table"] == projected_fact_table
    assert "frozen_source_outline" not in selection_input
    assert "frozen_boundary_selection" not in selection_input
    assert "frozen_boundary_manifest" not in selection_input
    assert membership_input["input_version"] == "2"
    assert membership_input["membership_assignment_version"] == (
        "requirement-scope-membership-assignment-v1"
    )
    assert membership_input["boundary_manifest_version"] == (
        "requirement-scope-boundary-manifest-v3"
    )
    assert membership_input["frozen_fact_table"] == projected_fact_table
    assert "frozen_source_outline" in membership_input
    assert "frozen_boundary_selection" in membership_input
    assert "frozen_boundary_manifest" not in membership_input
    assert all(
        item["input_version"] == "7"
        and item["binding_shard_version"]
        == "requirement-scope-binding-shard-v2"
        and item["frozen_fact_table"] == projected_fact_table
        for item in binding_inputs
    )
    assert all(
        item["frozen_source_outline"]
        == membership_input["frozen_source_outline"]
        for item in binding_inputs
    )
    selection_attempt = next(
        item
        for item in result.diagnostics["scope_ledger_compile_attempts"]
        if item["phase"] == "boundary_selection"
    )
    assert selection_attempt["system_prompt_chars"] == len(
        client.calls[0]["system_prompt"]
    )
    assert selection_attempt["user_input_chars"] == len(
        client.calls[0]["user_input"]
    )
    assert selection_attempt["request_chars"] == (
        selection_attempt["system_prompt_chars"]
        + selection_attempt["user_input_chars"]
    )

    expected_fact_refs = [
        str(row[0]) for row in projected_fact_table["rows"]
    ]
    target_groups = [item["target_fact_refs"] for item in binding_inputs]
    flattened_targets = [
        fact_ref for target_group in target_groups for fact_ref in target_group
    ]
    assert flattened_targets == expected_fact_refs
    assert len(flattened_targets) == len(set(flattened_targets))
    assert all(target_group for target_group in target_groups)
    assert all("target_fact_ids" not in item for item in binding_inputs)

    expected_manifest = normalize_requirement_scope_boundary_manifest(
        _boundary_payload(),
        normalized_fact_ledger=fact_ledger,
        source_evidence_catalog=CATALOG,
    )
    assert expected_manifest["valid"] is True, expected_manifest["errors"]
    manifest_fingerprints = {
        item["frozen_boundary_manifest"]["fingerprint"]
        for item in binding_inputs
    }
    assert manifest_fingerprints == {expected_manifest["fingerprint"]}
    expected_model_manifest = _scope_model_boundary_manifest(
        expected_manifest,
        fact_ledger,
    )
    assert all(
        item["frozen_boundary_manifest"] == expected_model_manifest
        for item in binding_inputs
    )
    assert len({item["target_fact_fingerprint"] for item in binding_inputs}) == (
        len(binding_inputs)
    )
    assert result.diagnostics["scope_ledger_boundary_manifest_fingerprint"] == (
        expected_manifest["fingerprint"]
    )
    assert result.diagnostics["scope_ledger_binding_shard_count"] == len(
        binding_inputs
    )


def test_binding_shard_count_is_derived_from_output_budget() -> None:
    compact_client = _success_client()
    roomy_client = _success_client()

    compact = _compile(compact_client, max_tokens=160)
    roomy = _compile(roomy_client, max_tokens=4096)

    assert compact.success is True
    assert roomy.success is True
    assert compact.diagnostics["scope_ledger_binding_shard_count"] > 1
    assert roomy.diagnostics["scope_ledger_binding_shard_count"] == 1
    assert compact.diagnostics["scope_ledger_binding_shard_count"] > (
        roomy.diagnostics["scope_ledger_binding_shard_count"]
    )
    assert compact.diagnostics["scope_ledger_binding_shard_budget_units"] == 320
    assert roomy.diagnostics["scope_ledger_binding_shard_budget_units"] == 8192


def test_second_binding_shard_failure_clears_all_partial_output() -> None:
    invalid_binding = _invalid_response("binding")
    client = _ScriptedClient(
        _json_response(_boundary_selection_model_response()),
        _membership_response,
        _binding_response,
        invalid_binding,
        invalid_binding,
        _binding_response,
    )

    result = _compile(client, max_tokens=160)

    assert result.success is False
    assert result.status == "contract_invalid"
    assert result.normalized_ledger == {}
    assert result.projection == {}
    assert len(client.calls) == 5
    assert result.diagnostics["scope_ledger_compile_global_status"] == (
        "binding_shard_failed"
    )
    assert result.diagnostics["scope_ledger_binding_completed_shard_count"] == 1
    assert result.diagnostics["scope_ledger_binding_failed_shard_index"] == 2
    assert [
        item["status"]
        for item in result.diagnostics["scope_ledger_binding_shard_summaries"]
    ] == ["validated", "contract_invalid"]


@pytest.mark.parametrize(
    ("phase", "failure_kind"),
    [
        ("boundary_selection", "parse"),
        ("boundary_selection", "contract"),
        ("membership", "parse"),
        ("membership", "contract"),
        ("binding", "parse"),
        ("binding", "contract"),
    ],
)
def test_parse_or_contract_failure_uses_one_independent_fresh_candidate(
    phase: str,
    failure_kind: str,
) -> None:
    bad_response = (
        "not-json"
        if failure_kind == "parse"
        else _invalid_response(phase)
    )
    if phase == "boundary_selection":
        responses = [
            bad_response,
            _json_response(_boundary_selection_model_response()),
            _membership_response,
            _binding_response,
        ]
    elif phase == "membership":
        responses = [
            _json_response(_boundary_selection_model_response()),
            bad_response,
            _membership_response,
            _binding_response,
        ]
    else:
        responses = [
            _json_response(_boundary_selection_model_response()),
            _membership_response,
            bad_response,
            _binding_response,
        ]
    client = _ScriptedClient(*responses)

    result = _compile(client)

    assert result.success is True
    phase_attempts = _phase_attempts(result, phase)
    assert [item["status"] for item in phase_attempts] == [
        "parse_failed" if failure_kind == "parse" else "contract_invalid",
        "validated",
    ]
    assert [item["candidate_mode"] for item in phase_attempts] == [
        "initial",
        "fresh_candidate",
    ]
    phase_start_index = {
        "boundary_selection": 0,
        "membership": 1,
        "binding": 2,
    }[phase]
    fresh_input = _payload(client.calls[phase_start_index + 1])
    assert fresh_input["attempt"] == 2
    assert fresh_input["compilation_mode"] == "independent_recompile"
    assert fresh_input["compilation_policy"] == "fresh_compile"
    assert fresh_input["recompile_reason_codes"]
    assert "previous_candidate" not in fresh_input
    assert len(phase_attempts) == 2


def test_binding_recompile_receives_exact_validator_feedback() -> None:
    def invalid_external_binding(call: dict[str, Any]) -> str:
        payload = _payload(call)
        return _json_response(
            {
                "fact_bindings": [
                    {
                        "fact_ref": fact_ref,
                        "scope_ids": [],
                        "role": "external_context",
                    }
                    for fact_ref in payload["target_fact_refs"]
                ]
            }
        )

    client = _ScriptedClient(
        _json_response(_boundary_selection_model_response()),
        _membership_response,
        invalid_external_binding,
        _binding_response,
    )

    result = _compile(client)

    assert result.success is True, result.diagnostics
    retry_input = _payload(client.calls[3])
    assert retry_input["compilation_mode"] == "independent_recompile"
    assert retry_input["recompile_reason_codes"] == [
        "executable_fact_external_context_forbidden"
    ]
    feedback = retry_input["recompile_contract_feedback"]
    assert {item["fact_ref"] for item in feedback} == set(
        retry_input["target_fact_refs"]
    )
    assert all(
        item["code"] == "executable_fact_external_context_forbidden"
        and item["current_role"] == "external_context"
        and item["current_scope_ids"] == []
        and item["allowed_roles"]
        == ["owned_requirement", "shared_requirement"]
        and item["allowed_active_scope_ids"]
        and item["repair_action"] == "bind_to_active_responsibility_owner"
        for item in feedback
    )
    assert "previous_candidate" not in retry_input


def test_shared_binding_recompile_feedback_exposes_active_scope_contract() -> None:
    def invalid_shared_binding(call: dict[str, Any]) -> str:
        payload = _payload(call)
        return _json_response(
            {
                "fact_bindings": [
                    {
                        "fact_ref": fact_ref,
                        "scope_ids": [],
                        "role": "shared_requirement",
                    }
                    for fact_ref in payload["target_fact_refs"]
                ]
            }
        )

    client = _ScriptedClient(
        _json_response(_boundary_selection_model_response()),
        _membership_response,
        invalid_shared_binding,
        _binding_response,
    )

    result = _compile(client)

    assert result.success is True, result.diagnostics
    feedback = _payload(client.calls[3])["recompile_contract_feedback"]
    assert feedback
    assert all(
        item["code"] == "shared_requirement_binding_invalid"
        and item["current_role"] == "shared_requirement"
        and item["current_scope_ids"] == []
        and item["minimum_shared_scope_count"] == 2
        and item["allowed_roles"]
        == ["owned_requirement", "shared_requirement"]
        and item["allowed_active_scope_ids"]
        and item["repair_action"]
        == "choose_supported_active_coowners_or_one_active_owner"
        for item in feedback
    )


@pytest.mark.parametrize(
    ("phase", "failure_kind"),
    [
        ("boundary_selection", "parse"),
        ("boundary_selection", "contract"),
        ("membership", "parse"),
        ("membership", "contract"),
        ("binding", "parse"),
        ("binding", "contract"),
    ],
)
def test_failed_fresh_candidate_never_starts_a_third_candidate(
    phase: str,
    failure_kind: str,
) -> None:
    bad_response = (
        "not-json"
        if failure_kind == "parse"
        else _invalid_response(phase)
    )
    if phase == "boundary_selection":
        responses = [
            bad_response,
            bad_response,
            _json_response(_boundary_selection_model_response()),
        ]
        expected_call_count = 2
    elif phase == "membership":
        responses = [
            _json_response(_boundary_selection_model_response()),
            bad_response,
            bad_response,
            _membership_response,
        ]
        expected_call_count = 3
    else:
        responses = [
            _json_response(_boundary_selection_model_response()),
            _membership_response,
            bad_response,
            bad_response,
            _binding_response,
        ]
        expected_call_count = 4
    client = _ScriptedClient(*responses)

    result = _compile(client)

    assert result.success is False
    assert result.status == (
        "parse_failed" if failure_kind == "parse" else "contract_invalid"
    )
    assert result.normalized_ledger == {}
    assert result.projection == {}
    assert len(client.calls) == expected_call_count
    phase_attempts = _phase_attempts(result, phase)
    assert len(phase_attempts) == 2
    assert [item["candidate_mode"] for item in phase_attempts] == [
        "initial",
        "fresh_candidate",
    ]


def test_transport_failure_replays_the_exact_same_selection_envelope() -> None:
    client = _ScriptedClient(
        (
            "Error: HTTP 504 - Gateway Time-out",
            {
                "http_status": 504,
                "wire_api": "chat_completions",
                "finish_reason": "",
            },
        ),
        _json_response(_boundary_selection_model_response()),
        _membership_response,
        _binding_response,
    )

    result = _compile(client)

    assert result.success is True
    assert len(client.calls) == 4
    assert client.calls[0] == client.calls[1]
    assert result.diagnostics["scope_ledger_compile_envelope_count"] == 3
    assert result.diagnostics["scope_ledger_compile_physical_call_count"] == 4
    assert result.diagnostics["scope_ledger_compile_transport_failure_count"] == 1
    assert result.diagnostics["scope_ledger_compile_transport_retry_count"] == 1
    assert result.diagnostics["scope_ledger_compile_fresh_candidate_used"] is False


@pytest.mark.parametrize(
    "phase",
    ["boundary_selection", "membership", "binding"],
)
def test_finish_reason_length_fails_closed_without_a_fresh_candidate(
    phase: str,
) -> None:
    phase_responses = {
        "boundary_selection": _json_response(
            _boundary_selection_model_response()
        ),
        "membership": _membership_response,
        "binding": _binding_response,
    }
    phase_prefixes = {
        "boundary_selection": [],
        "membership": [
            _json_response(_boundary_selection_model_response()),
        ],
        "binding": [
            _json_response(_boundary_selection_model_response()),
            _membership_response,
        ],
    }
    truncated_response = (
        phase_responses[phase],
        {"http_status": 200, "finish_reason": "length"},
    )
    client = _ScriptedClient(*phase_prefixes[phase], truncated_response)

    result = _compile(client)

    assert result.success is False
    assert result.status == "output_truncated"
    assert result.normalized_ledger == {}
    assert result.projection == {}
    assert len(client.calls) == len(phase_prefixes[phase]) + 1
    assert result.diagnostics["scope_ledger_compile_fresh_candidate_used"] is False
    assert [item["status"] for item in _phase_attempts(result, phase)] == [
        "output_truncated"
    ]


@pytest.mark.parametrize(
    "phase",
    ["boundary_selection", "membership", "binding"],
)
def test_explicit_incomplete_phase_fails_closed_without_a_fresh_candidate(
    phase: str,
) -> None:
    phase_responses = {
        "boundary_selection": _json_response(
            _boundary_selection_model_response()
        ),
        "membership": _membership_response,
        "binding": _binding_response,
    }
    phase_prefixes = {
        "boundary_selection": [],
        "membership": [
            _json_response(_boundary_selection_model_response()),
        ],
        "binding": [
            _json_response(_boundary_selection_model_response()),
            _membership_response,
        ],
    }
    incomplete_response = (
        phase_responses[phase],
        {
            "http_status": 200,
            "response_status": "incomplete",
            "incomplete_reason": "content_filter",
            "finish_reason": "stop",
        },
    )
    client = _ScriptedClient(*phase_prefixes[phase], incomplete_response)

    result = _compile(client)

    assert result.success is False
    assert result.status == "output_incomplete"
    assert result.normalized_ledger == {}
    assert result.projection == {}
    assert len(client.calls) == len(phase_prefixes[phase]) + 1
    assert result.diagnostics["scope_ledger_compile_fresh_candidate_used"] is False
    phase_attempts = _phase_attempts(result, phase)
    assert [item["status"] for item in phase_attempts] == ["output_incomplete"]
    assert phase_attempts[0]["parse_error_code"] == (
        f"scope_{phase}_output_incomplete"
    )


def _single_scope_fixture(
    fact_count: int,
) -> tuple[
    dict[str, Any],
    dict[str, object],
    list[dict[str, str]],
]:
    catalog: list[dict[str, str]] = []
    facts: list[dict[str, object]] = []
    for index in range(1, fact_count + 1):
        evidence_ref = f"EV_{index:012x}"
        statement = f"工作台支持用户处理第 {index:02d} 类业务事项。"
        catalog.append({"ref": evidence_ref, "quote": statement})
        facts.append(
            _fact(
                f"FACT_A1_{index:024X}",
                "action",
                statement,
                evidence_ref,
            )
        )
    fact_ledger = _normalize_fact_fixture(catalog=catalog, facts=facts)
    boundary_payload = {
        "boundaries": [
            {
                "boundary_id": "SCOPE_WORKSPACE",
                "label": "业务工作台",
                "decision": "in_scope_leaf",
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": [
                    {
                        "signal": "purpose",
                        "fact_ids": [facts[0]["fact_id"]],
                    }
                ],
            }
        ]
    }
    return fact_ledger, boundary_payload, catalog


def test_single_binding_item_over_budget_stops_before_binding_call() -> None:
    client = _success_client()

    result = _compile(client, max_tokens=1)

    assert result.success is False
    assert result.status == "capacity_exceeded"
    assert result.normalized_ledger == {}
    assert result.projection == {}
    assert len(client.calls) == 2
    assert result.diagnostics["scope_ledger_binding_oversized_fact_count"] == (
        len(CATALOG)
    )
    assert result.diagnostics["scope_ledger_compile_global_error_codes"] == [
        "scope_binding_item_capacity_exceeded"
    ]


def test_binding_capacity_uses_manifest_relationships_not_all_boundaries() -> None:
    fact_id = "FACT_A1_000000000000000000000001"
    boundary_ids = [
        f"SCOPE_{index:03d}_{'LONG_ID_SEGMENT_' * 4}"
        for index in range(400)
    ]
    manifest = {
        "fact_ledger_fingerprint": "f" * 64,
        "fingerprint": "b" * 64,
        "boundaries": [
            {
                "boundary_id": boundary_id,
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": (
                    [{"signal": "actor", "fact_ids": [fact_id]}]
                    if index == 0
                    else []
                ),
            }
            for index, boundary_id in enumerate(boundary_ids)
        ],
    }

    shards, shard_budget_units, oversized_fact_count = (
        _partition_binding_targets(
            [fact_id],
            boundary_manifest=manifest,
            max_tokens=4096,
        )
    )

    assert oversized_fact_count == 0
    assert len(shards) == 1
    assert shards[0].target_fact_ids == (fact_id,)
    assert shards[0].budget_units <= shard_budget_units


def test_known_extreme_shared_binding_runs_as_a_single_fact_shard() -> None:
    fact_id = "FACT_A1_000000000000000000000001"
    boundary_ids = [
        f"SCOPE_{index:03d}_{'LONG_ID_SEGMENT_' * 4}"
        for index in range(400)
    ]
    manifest = {
        "fact_ledger_fingerprint": "f" * 64,
        "fingerprint": "b" * 64,
        "boundaries": [
            {
                "boundary_id": boundary_id,
                "parent_boundary_id": "",
                "membership_relation_ids": [],
                "membership_fact_ids": [],
                "support": [{"signal": "actor", "fact_ids": [fact_id]}],
            }
            for boundary_id in boundary_ids
        ],
    }

    shards, shard_budget_units, oversized_fact_count = (
        _partition_binding_targets(
            [fact_id],
            boundary_manifest=manifest,
            max_tokens=4096,
        )
    )

    assert oversized_fact_count == 0
    assert len(shards) == 1
    assert shards[0].target_fact_ids == (fact_id,)
    assert shards[0].budget_units > shard_budget_units


def test_binding_shards_have_no_fixed_global_count_ceiling() -> None:
    previous_shard_limit = 16
    fact_ledger, boundary_payload, source_evidence_catalog = _single_scope_fixture(
        previous_shard_limit + 1
    )

    def binding_response(call: dict[str, Any]) -> str:
        payload = _payload(call)
        return _json_response(
            {
                "fact_bindings": [
                    {
                        "fact_ref": fact_ref,
                        "scope_ids": ["SCOPE_WORKSPACE"],
                        "role": "owned_requirement",
                    }
                    for fact_ref in payload["target_fact_refs"]
                ]
            }
        )

    client = _ScriptedClient(
        _json_response(
            _boundary_selection_model_response(
                boundary_payload,
                fact_ledger=fact_ledger,
            )
        ),
        _membership_response,
        binding_response,
    )

    result = _compile(
        client,
        fact_ledger=fact_ledger,
        source_evidence_catalog=source_evidence_catalog,
        max_tokens=58,
    )

    assert result.success is True, result.diagnostics
    assert result.status == "validated"
    assert len(client.calls) == previous_shard_limit + 3
    assert result.diagnostics["scope_ledger_binding_shard_count"] == (
        previous_shard_limit + 1
    )
    assert result.diagnostics[
        "scope_ledger_binding_completed_shard_count"
    ] == previous_shard_limit + 1
    assert result.diagnostics["scope_ledger_binding_oversized_fact_count"] == 0
    assert result.diagnostics["scope_ledger_compile_global_error_codes"] == []


def _walk_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        keys: list[str] = []
        for key, nested in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(nested))
        return keys
    if isinstance(value, list):
        return [key for item in value for key in _walk_keys(item)]
    return []


def test_diagnostics_never_copy_statements_quotes_or_target_id_lists() -> None:
    fact_ledger = _normalized_fact_ledger()
    client = _success_client()

    result = _compile(client, fact_ledger=fact_ledger, max_tokens=640)

    assert result.success is True
    diagnostics_text = json.dumps(result.diagnostics, ensure_ascii=False)
    forbidden_keys = {
        "statement",
        "quote",
        "target_fact_ids",
        "target_fact_refs",
    }
    assert forbidden_keys.isdisjoint(_walk_keys(result.diagnostics))
    for fact in fact_ledger["evidence_facts"]:
        assert str(fact["statement"]) not in diagnostics_text
        assert str(fact["fact_id"]) not in diagnostics_text
    for evidence in CATALOG:
        assert evidence["quote"] not in diagnostics_text


def test_tampered_frozen_fact_ledger_is_rejected_before_any_model_call() -> None:
    fact_ledger = _normalized_fact_ledger()
    fact_ledger["evidence_facts"][0]["statement"] = "篡改后的事实"
    client = _success_client()

    with pytest.raises(ValueError):
        _compile(client, fact_ledger=fact_ledger)

    assert client.calls == []
