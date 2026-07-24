from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from modules.test_generation_components.control import (
    requirement_fact_ledger_compiler as fact_ledger_compiler,
)
from modules.test_generation_components.control.model_envelope_call import (
    strict_json_output_contract_prompt,
)
from modules.test_generation_components.control.requirement_fact_ledger import (
    REQUIREMENT_FACT_LEDGER_VERSION,
    build_requirement_fact_ledger_prompt,
    build_requirement_fact_ledger_user_input,
    fingerprint_requirement_fact_ledger,
    fingerprint_source_evidence_catalog,
    normalize_requirement_fact_model_response,
    normalize_requirement_fact_ledger,
    normalize_source_evidence_catalog,
    validate_requirement_fact_ledger_fingerprints,
)
from modules.test_generation_components.control.requirement_fact_ledger_compiler import (
    RequirementFactLedgerCompilationResult,
    compile_requirement_atomic_fact_ledger,
)
from modules.test_generation_components.control.requirement_semantic_graph import (
    FACT_KINDS,
    normalize_requirement_semantic_graph,
)


CATALOG = [
    {
        "ref": "EV_aaaaaaaaaaaa",
        "quote": "申请人提交记录后，系统创建待处理任务。",
    },
    {
        "ref": "EV_bbbbbbbbbbbb",
        "quote": "P0：审核员只能处理分配给自己的任务，并在通过后把任务标记为完成。",
    },
    {
        "ref": "EV_cccccccccccc",
        "quote": "本段说明仅描述材料的阅读约定。",
    },
]
CATALOG_FINGERPRINT = fingerprint_source_evidence_catalog(CATALOG)
_DB_SENTINEL = object()


def _fact(
    fact_id: str,
    statement: str,
    evidence: list[str],
    *,
    fact_kind: str = "action",
    requirement_level: str = "required",
    priority: str = "unspecified",
    testability: str = "testable",
    anchor_evidence_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "fact_kind": fact_kind,
        "statement": statement,
        "requirement_level": requirement_level,
        "priority": priority,
        "testability": testability,
        "evidence": evidence,
        "anchor_evidence_ref": (
            anchor_evidence_ref
            if anchor_evidence_ref is not None
            else evidence[0]
            if evidence
            else ""
        ),
        "confidence": 0.95,
    }


def _valid_response() -> dict[str, Any]:
    return {
        "evidence_facts": [
            _fact(
                "FACT_SUBMIT",
                "申请人提交记录后系统创建待处理任务",
                ["EV_aaaaaaaaaaaa"],
                fact_kind="Action",
            ),
            _fact(
                "FACT_PERMISSION",
                "审核员只能处理分配给自己的任务",
                ["EV_bbbbbbbbbbbb"],
                priority="p0",
            ),
            _fact(
                "FACT_COMPLETE",
                "审核通过后任务标记为完成",
                ["EV_bbbbbbbbbbbb"],
            ),
        ],
        "source_evidence_dispositions": [
            {
                "evidence_ref": "EV_aaaaaaaaaaaa",
                "disposition": "fact_backed",
            },
            {
                "evidence_ref": "EV_bbbbbbbbbbbb",
                "disposition": "fact_backed",
            },
            {
                "evidence_ref": "EV_cccccccccccc",
                "disposition": "context_only",
            },
        ],
    }


def _normalize(payload: Any) -> dict[str, Any]:
    return normalize_requirement_fact_ledger(
        payload,
        source_evidence_catalog=CATALOG,
        source_catalog_fingerprint=CATALOG_FINGERPRINT,
    )


def _normalize_model(payload: Any) -> dict[str, Any]:
    return normalize_requirement_fact_model_response(
        payload,
        source_evidence_catalog=CATALOG,
        source_catalog_fingerprint=CATALOG_FINGERPRINT,
    )


def _error_codes(result: dict[str, Any]) -> set[str]:
    return {
        str(item.get("code"))
        for item in result.get("errors") or []
        if isinstance(item, dict)
    }


class _ScriptedClient:
    """按真实模型客户端协议记录请求，仅控制返回序列。"""

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


class _AdaptiveClient:
    """根据真实请求载荷生成分片响应，不伪造生产目录或跳过契约。"""

    def __init__(self, responder: Any) -> None:
        self.responder = responder
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
        payload = json.loads(user_input)
        response = self.responder(payload, len(self.calls))
        self.last_response_metadata = {}
        if isinstance(response, tuple) and len(response) == 2:
            response, metadata = response
            self.last_response_metadata = dict(metadata or {})
        return str(response)


def _model_wire_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """把 canonical 测试声明投影为当前 source-centered 模型 wire。"""

    if "source_evidence_records" in payload:
        return copy.deepcopy(payload)
    if not {
        "evidence_facts",
        "source_evidence_dispositions",
    } & set(payload):
        return copy.deepcopy(payload)

    facts_by_owner: dict[str, list[dict[str, Any]]] = {}
    for raw_fact in payload.get("evidence_facts") or []:
        if not isinstance(raw_fact, dict):
            continue
        owner_ref = str(raw_fact.get("anchor_evidence_ref") or "")
        owned_fact = {
            str(key): copy.deepcopy(value)
            for key, value in raw_fact.items()
            if key != "anchor_evidence_ref"
        }
        facts_by_owner.setdefault(owner_ref, []).append(owned_fact)

    records: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for raw_disposition in payload.get("source_evidence_dispositions") or []:
        if not isinstance(raw_disposition, dict):
            records.append(copy.deepcopy(raw_disposition))
            continue
        evidence_ref = str(raw_disposition.get("evidence_ref") or "")
        owned_facts = facts_by_owner.pop(evidence_ref, [])
        record = {
            "evidence_ref": evidence_ref,
            "owned_facts": owned_facts,
        }
        records.append(record)
        seen_refs.add(evidence_ref)

    for owner_ref, owned_facts in facts_by_owner.items():
        if owner_ref in seen_refs:
            continue
        records.append(
            {
                "evidence_ref": owner_ref,
                "owned_facts": owned_facts,
            }
        )

    wire = {
        "source_evidence_records": records,
    }
    wire.update(
        {
            str(key): copy.deepcopy(value)
            for key, value in payload.items()
            if key
            not in {"evidence_facts", "source_evidence_dispositions"}
        }
    )
    return wire


def _json_response(payload: dict[str, Any]) -> str:
    return json.dumps(_model_wire_payload(payload), ensure_ascii=False)


def _compile(
    client: _ScriptedClient,
) -> RequirementFactLedgerCompilationResult:
    return compile_requirement_atomic_fact_ledger(
        client=client,
        source_evidence_catalog=CATALOG,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
        request_timeout_seconds=120,
    )


def _request_payload(call: dict[str, Any]) -> dict[str, Any]:
    return json.loads(call["user_input"])


def _request_target_refs(payload: dict[str, Any]) -> list[str]:
    return [
        str(item["ref"])
        for item in payload["target_source_evidence_catalog"]
    ]


def _request_context_refs(payload: dict[str, Any]) -> list[str]:
    return [
        str(item["ref"])
        for item in payload["context_source_evidence_catalog"]
    ]


def _budget_split_catalog(count: int = 4) -> list[dict[str, Any]]:
    return [
        {
            "ref": f"EV_{index:012x}",
            "quote": f"第 {index} 条通用需求片段",
        }
        for index in range(1, count + 1)
    ]


def _local_chunk_response(
    payload: dict[str, Any],
    *,
    evidence_refs: list[str] | None = None,
    local_fact_id: str = "FACT_LOCAL",
    confidence: float = 0.9,
    anchor_evidence_ref: str | None = None,
) -> dict[str, Any]:
    target_refs = [
        str(item["ref"])
        for item in payload["target_source_evidence_catalog"]
    ]
    fact_evidence = list(
        target_refs[:1] if evidence_refs is None else evidence_refs
    )
    fact_anchor = (
        anchor_evidence_ref
        if anchor_evidence_ref is not None
        else next(
            (ref for ref in fact_evidence if ref in target_refs),
            target_refs[0] if target_refs else "",
        )
    )
    facts = (
        [
            {
                **_fact(
                    local_fact_id,
                    f"处理 {fact_evidence[0]} 对应的原子需求",
                    fact_evidence,
                    anchor_evidence_ref=fact_anchor,
                ),
                "confidence": confidence,
            }
        ]
        if fact_evidence
        else []
    )
    return {
        "evidence_facts": facts,
        "source_evidence_dispositions": [
            {
                "evidence_ref": evidence_ref,
                "disposition": (
                    "fact_backed"
                    if evidence_ref in fact_evidence
                    else "context_only"
                ),
            }
            for evidence_ref in target_refs
        ],
    }


def _compile_budget_split(
    client: Any,
    catalog: list[dict[str, str]],
) -> RequirementFactLedgerCompilationResult:
    return compile_requirement_atomic_fact_ledger(
        client=client,
        source_evidence_catalog=catalog,
        max_tokens=1024,
        request_timeout_seconds=120,
    )


def test_catalog_normalization_and_fingerprint_are_canonical() -> None:
    reordered_keys = [
        {"quote": item["quote"], "ref": item["ref"]} for item in CATALOG
    ]

    normalized = normalize_source_evidence_catalog(reordered_keys)

    assert normalized["valid"] is True
    assert normalized["items"] == CATALOG
    assert normalized["quote_by_ref"]["EV_aaaaaaaaaaaa"] == CATALOG[0][
        "quote"
    ]
    assert normalized["fingerprint"] == CATALOG_FINGERPRINT


def test_valid_response_freezes_refs_quotes_and_three_fingerprints() -> None:
    result = _normalize(_valid_response())

    assert result["valid"] is True
    assert result["fact_ledger_version"] == REQUIREMENT_FACT_LEDGER_VERSION
    assert result["source_catalog_fingerprint"] == CATALOG_FINGERPRINT
    assert result["raw_declarations"]["evidence_facts"][0]["evidence"] == [
        "EV_bbbbbbbbbbbb"
    ]
    facts_by_id = {
        item["fact_id"]: item for item in result["evidence_facts"]
    }
    assert facts_by_id["FACT_SUBMIT"]["fact_kind"] == "action"
    assert facts_by_id["FACT_SUBMIT"]["evidence"] == [CATALOG[0]["quote"]]
    assert facts_by_id["FACT_PERMISSION"]["evidence"] == [
        CATALOG[1]["quote"]
    ]
    assert result["raw_declarations_fingerprint"]
    assert result["evidence_facts_fingerprint"]
    assert result["fingerprint"] == fingerprint_requirement_fact_ledger(result)
    assert validate_requirement_fact_ledger_fingerprints(result)["valid"] is True


def test_a1_facts_enter_existing_graph_normalizer_without_field_drift() -> None:
    ledger = _normalize(_valid_response())
    source_text = "\n".join(item["quote"] for item in CATALOG)

    graph_result = normalize_requirement_semantic_graph(
        {
            "evidence_facts": copy.deepcopy(ledger["evidence_facts"]),
            "semantic_graph": {
                "graph_version": "requirement-semantic-graph-v1",
                "nodes": [],
                "edges": [],
                "fact_dispositions": [],
                "primary_flow": {"node_ids": [], "edge_ids": []},
            },
        },
        source_text=source_text,
        evidence_validator=lambda evidence, source: bool(evidence)
        and all(str(item) in source for item in evidence),
    )

    assert graph_result["evidence_facts"] == ledger["evidence_facts"]


@pytest.mark.parametrize("unknown_field", ["boundaries", "fact_bindings", "graph"])
def test_response_rejects_every_unknown_top_level_field(
    unknown_field: str,
) -> None:
    payload = _valid_response()
    payload[unknown_field] = []

    result = _normalize(payload)

    assert result["valid"] is False
    assert "fact_ledger_response_field_unknown" in _error_codes(result)


@pytest.mark.parametrize(
    ("evidence", "expected_code"),
    [
        ([CATALOG[0]["quote"]], "fact_evidence_ref_invalid"),
        (["EV_dddddddddddd"], "fact_evidence_ref_unknown"),
        (
            ["EV_aaaaaaaaaaaa", "EV_dddddddddddd"],
            "fact_evidence_ref_unknown",
        ),
    ],
    ids=["raw_quote", "unknown_ref", "mixed_refs"],
)
def test_raw_unknown_and_mixed_evidence_fail_closed(
    evidence: list[str],
    expected_code: str,
) -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["evidence"] = evidence

    result = _normalize(payload)

    assert result["valid"] is False
    assert expected_code in _error_codes(result)
    assert result["fingerprint"] == ""


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda fact: fact.pop("anchor_evidence_ref"),
            "fact_anchor_evidence_ref_missing",
        ),
        (
            lambda fact: fact.update(
                {"anchor_evidence_ref": "EV_dddddddddddd"}
            ),
            "fact_anchor_evidence_ref_unknown",
        ),
        (
            lambda fact: fact.update(
                {"anchor_evidence_ref": "EV_bbbbbbbbbbbb"}
            ),
            "fact_anchor_evidence_not_cited",
        ),
    ],
    ids=["missing", "unknown", "not_cited"],
)
def test_fact_anchor_is_required_known_and_cited(
    mutation: Any,
    expected_code: str,
) -> None:
    payload = _valid_response()
    mutation(payload["evidence_facts"][0])

    result = _normalize(payload)

    assert result["valid"] is False
    assert expected_code in _error_codes(result)


def test_required_fact_with_unknown_testability_is_rejected_by_shared_normalizer() -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["testability"] = "unknown"

    result = _normalize(payload)

    assert result["valid"] is False
    assert "required_fact_testability_unresolved" in _error_codes(result)


@pytest.mark.parametrize("fact_kind", sorted(FACT_KINDS))
def test_fact_kind_closed_enum_accepts_each_declared_semantic_kind(
    fact_kind: str,
) -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["fact_kind"] = fact_kind.upper()

    result = _normalize(payload)

    assert result["valid"] is True
    facts_by_id = {
        item["fact_id"]: item for item in result["evidence_facts"]
    }
    assert facts_by_id["FACT_SUBMIT"]["fact_kind"] == fact_kind


@pytest.mark.parametrize(
    "fact_kind",
    ["requirement", "behavior", "visual_behavior", ""],
)
def test_fact_kind_outside_closed_enum_fails_closed(fact_kind: str) -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["fact_kind"] = fact_kind

    result = _normalize(payload)

    assert result["valid"] is False
    assert "fact_kind_invalid" in _error_codes(result)


def test_priority_without_matching_explicit_ev_token_is_rejected() -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["priority"] = "p1"

    result = _normalize(payload)

    assert result["valid"] is False
    assert "fact_priority_not_evidence_declared" in _error_codes(result)
    priority_errors = [
        item
        for item in result["errors"]
        if item.get("code") == "fact_priority_not_evidence_declared"
    ]
    assert priority_errors == [
        {
            "code": "fact_priority_not_evidence_declared",
            "path": "$.evidence_facts[0].priority",
            "identifier": "FACT_SUBMIT",
            "priority": "p1",
            "priority_anchor_ref": "EV_aaaaaaaaaaaa",
        }
    ]


def test_priority_must_match_the_token_in_its_own_evidence() -> None:
    payload = _valid_response()
    payload["evidence_facts"][1]["priority"] = "p1"

    result = _normalize(payload)

    assert result["valid"] is False
    assert "fact_priority_not_evidence_declared" in _error_codes(result)


def test_priority_token_in_uncited_catalog_evidence_does_not_authorize_fact() -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["priority"] = "p0"

    result = _normalize(payload)

    assert result["valid"] is False
    assert "fact_priority_not_evidence_declared" in _error_codes(result)


@pytest.mark.parametrize(
    "evidence_refs",
    [
        ["EV_aaaaaaaaaaaa", "EV_bbbbbbbbbbbb"],
        ["EV_bbbbbbbbbbbb", "EV_aaaaaaaaaaaa"],
    ],
    ids=["catalog_order", "reversed_array_order"],
)
def test_later_priority_token_cannot_override_earliest_evidence_anchor(
    evidence_refs: list[str],
) -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["evidence"] = evidence_refs
    payload["evidence_facts"][0]["priority"] = "p0"

    result = _normalize(payload)

    assert result["valid"] is False
    priority_error = next(
        item
        for item in result["errors"]
        if item.get("code") == "fact_priority_not_evidence_declared"
        and item.get("identifier") == "FACT_SUBMIT"
    )
    assert priority_error["priority_anchor_ref"] == "EV_aaaaaaaaaaaa"


def test_priority_anchor_may_have_additional_later_supporting_evidence() -> None:
    payload = _valid_response()
    payload["evidence_facts"][1]["evidence"] = [
        "EV_bbbbbbbbbbbb",
        "EV_cccccccccccc",
    ]
    payload["source_evidence_dispositions"][2] = {
        "evidence_ref": "EV_cccccccccccc",
        "disposition": "context_only",
    }

    result = _normalize(payload)

    assert result["valid"] is True


def test_supporting_evidence_cannot_be_declared_non_requirement() -> None:
    payload = _valid_response()
    payload["evidence_facts"][1]["evidence"] = [
        "EV_bbbbbbbbbbbb",
        "EV_cccccccccccc",
    ]
    payload["source_evidence_dispositions"][2] = {
        "evidence_ref": "EV_cccccccccccc",
        "disposition": "non_requirement",
    }

    result = _normalize(payload)

    assert result["valid"] is False
    assert "source_disposition_context_only_required" in _error_codes(result)


def test_supporting_evidence_cannot_claim_fact_backed_without_anchor() -> None:
    payload = _valid_response()
    payload["evidence_facts"][1]["evidence"] = [
        "EV_bbbbbbbbbbbb",
        "EV_cccccccccccc",
    ]
    payload["source_evidence_dispositions"][2] = {
        "evidence_ref": "EV_cccccccccccc",
        "disposition": "fact_backed",
    }

    result = _normalize(payload)

    assert result["valid"] is False
    assert "source_disposition_fact_backed_without_anchor_fact" in _error_codes(
        result
    )


@pytest.mark.parametrize("disposition", ["context_only", "non_requirement"])
def test_unreferenced_evidence_preserves_non_owning_disposition(
    disposition: str,
) -> None:
    payload = _valid_response()
    payload["source_evidence_dispositions"][2] = {
        "evidence_ref": "EV_cccccccccccc",
        "disposition": disposition,
    }

    result = _normalize(payload)

    assert result["valid"] is True
    frozen = {
        item["evidence_ref"]: item
        for item in result["raw_declarations"][
            "source_evidence_dispositions"
        ]
    }
    assert frozen["EV_cccccccccccc"] == {
        "evidence_ref": "EV_cccccccccccc",
        "fact_ids": [],
        "disposition": disposition,
    }


def test_unreferenced_evidence_cannot_claim_fact_backed() -> None:
    payload = _valid_response()
    payload["source_evidence_dispositions"][2] = {
        "evidence_ref": "EV_cccccccccccc",
        "disposition": "fact_backed",
    }

    result = _normalize(payload)

    assert result["valid"] is False
    assert "source_disposition_fact_backed_without_anchor_fact" in _error_codes(
        result
    )


def test_anchored_evidence_cannot_be_declared_non_requirement() -> None:
    payload = _valid_response()
    payload["source_evidence_dispositions"][0] = {
        "evidence_ref": "EV_aaaaaaaaaaaa",
        "disposition": "non_requirement",
    }

    result = _normalize(payload)

    assert result["valid"] is False
    assert "source_disposition_fact_backed_required" in _error_codes(result)


@pytest.mark.parametrize(
    ("source_token", "declared_priority"),
    [
        ("P0", "p0"),
        ("p1", "p1"),
        ("Ｐ２", "p2"),
        ("P3", "p3"),
    ],
)
def test_priority_accepts_each_case_and_width_normalized_explicit_token(
    source_token: str,
    declared_priority: str,
) -> None:
    catalog = [
        {
            "ref": "EV_dddddddddddd",
            "quote": f"【{source_token}】用户提交后系统保存记录。",
        }
    ]
    payload = {
        "evidence_facts": [
            _fact(
                "FACT_SAVE",
                "用户提交后系统保存记录",
                ["EV_dddddddddddd"],
                priority=declared_priority,
            )
        ],
        "source_evidence_dispositions": [
            {
                "evidence_ref": "EV_dddddddddddd",
                "disposition": "fact_backed",
            }
        ],
    }

    result = normalize_requirement_fact_ledger(
        payload,
        source_evidence_catalog=catalog,
        source_catalog_fingerprint=fingerprint_source_evidence_catalog(catalog),
    )

    assert result["valid"] is True


@pytest.mark.parametrize("source_token", ["P0_flag", "_P0_", "XP0"])
def test_priority_does_not_match_token_inside_identifier(
    source_token: str,
) -> None:
    catalog = [
        {
            "ref": "EV_dddddddddddd",
            "quote": f"字段 {source_token} 仅是内部标识。",
        }
    ]
    payload = {
        "evidence_facts": [
            _fact(
                "FACT_SAVE",
                "系统保存记录",
                ["EV_dddddddddddd"],
                priority="p0",
            )
        ],
        "source_evidence_dispositions": [
            {
                "evidence_ref": "EV_dddddddddddd",
                "disposition": "fact_backed",
            }
        ],
    }

    result = normalize_requirement_fact_ledger(
        payload,
        source_evidence_catalog=catalog,
        source_catalog_fingerprint=fingerprint_source_evidence_catalog(catalog),
    )

    assert result["valid"] is False
    assert "fact_priority_not_evidence_declared" in _error_codes(result)


@pytest.mark.parametrize(
    "statement",
    [
        "排序规则同上",
        "精选排序同原规则",
        "系统按上述规则排序",
        "评论和回复评论的样式修改如原型图所示",
        "评论和回复评论的样式修改如原型图",
        "消息页面样式如图所示",
        "系统展示内容如下：",
        "Same as above.",
        "The sorting behavior is the same as original.",
        "The system sorts by the aforementioned rule.",
        "The comment layout is as shown in the figure below.",
        "The available states are as follows:",
    ],
)
def test_statement_rejects_unresolved_neighbor_visual_or_original_reference(
    statement: str,
) -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["statement"] = statement

    result = _normalize(payload)

    assert result["valid"] is False
    assert "fact_statement_unresolved_reference" in _error_codes(result)


def test_statement_allows_relation_when_both_rules_are_named() -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["statement"] = (
        "精选排序与热门排序采用相同规则"
    )

    result = _normalize(payload)

    assert result["valid"] is True


def test_statement_allows_named_regression_invariant_without_inventing_baseline() -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["statement"] = (
        "回复内容的可观察显示行为必须保持不变"
    )

    result = _normalize(payload)

    assert result["valid"] is True


@pytest.mark.parametrize(
    "statement",
    [
        "用户可见图片上传入口",
        "系统支持 PNG 常见图片格式",
        "系统展示状态如下：成功、失败",
        "The valid states are as follows: success and failure.",
    ],
)
def test_statement_allows_self_contained_visual_or_inline_values(
    statement: str,
) -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["statement"] = statement

    result = _normalize(payload)

    assert result["valid"] is True


@pytest.mark.parametrize(
    "statement",
    [
        "新详情页布局同原有详情页布局",
        "处理模块同上游系统交互",
        "消息模块同前端组件交互",
        "New ranking is the same as original ranking.",
    ],
)
def test_statement_allows_original_relation_when_both_subjects_are_named(
    statement: str,
) -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["statement"] = statement

    result = _normalize(payload)

    assert result["valid"] is True


@pytest.mark.parametrize(
    ("confidence", "expected_code"),
    [
        (True, "fact_confidence_invalid"),
        ("0.9", "fact_confidence_invalid"),
        (float("nan"), "fact_confidence_invalid"),
        (float("inf"), "fact_confidence_invalid"),
        (0, "fact_confidence_out_of_range"),
        (-0.1, "fact_confidence_out_of_range"),
        (1.00001, "fact_confidence_out_of_range"),
        (9, "fact_confidence_out_of_range"),
    ],
    ids=[
        "bool",
        "string",
        "nan",
        "infinity",
        "zero",
        "negative",
        "above_one",
        "clamp_regression",
    ],
)
def test_fact_confidence_requires_finite_number_in_open_unit_interval(
    confidence: Any,
    expected_code: str,
) -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["confidence"] = confidence

    result = _normalize(payload)

    assert result["valid"] is False
    assert expected_code in _error_codes(result)
    assert result["fingerprint"] == ""


def test_non_empty_catalog_cannot_be_silently_downgraded_to_context() -> None:
    payload = {
        "evidence_facts": [],
        "source_evidence_dispositions": [
            {
                "evidence_ref": item["ref"],
                "disposition": "context_only",
            }
            for item in CATALOG
        ],
    }

    result = _normalize(payload)

    assert result["valid"] is False
    assert "fact_ledger_empty" in _error_codes(result)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda payload: payload["source_evidence_dispositions"].pop(),
            "source_evidence_disposition_missing",
        ),
        (
            lambda payload: payload["source_evidence_dispositions"].append(
                copy.deepcopy(payload["source_evidence_dispositions"][0])
            ),
            "source_disposition_ref_duplicate",
        ),
        (
            lambda payload: payload["source_evidence_dispositions"][0].update(
                {"fact_ids": ["FACT_PERMISSION"]}
            ),
            "source_disposition_field_unknown",
        ),
        (
            lambda payload: payload["source_evidence_dispositions"][0].update(
                {"disposition": "context_only"}
            ),
            "source_disposition_fact_backed_required",
        ),
    ],
    ids=["missing", "duplicate", "legacy_fact_ids", "wrong_disposition"],
)
def test_source_catalog_must_be_closed_by_exact_dispositions(
    mutation: Any,
    expected_code: str,
) -> None:
    payload = _valid_response()
    mutation(payload)

    result = _normalize(payload)

    assert result["valid"] is False
    assert expected_code in _error_codes(result)


def test_fingerprint_validation_detects_mutated_fact_after_freeze() -> None:
    result = _normalize(_valid_response())
    mutated = copy.deepcopy(result)
    mutated["evidence_facts"][0]["statement"] = "冻结后被改写"

    verification = validate_requirement_fact_ledger_fingerprints(mutated)

    assert verification["valid"] is False
    assert verification["error_codes"] == ["fact_ledger_fingerprint_mismatch"]


def test_fingerprint_validation_rejects_unknown_fact_field_after_freeze() -> None:
    result = _normalize(_valid_response())
    mutated = copy.deepcopy(result)
    mutated["evidence_facts"][0]["model_override"] = "INJECTED_AFTER_FREEZE"

    verification = validate_requirement_fact_ledger_fingerprints(mutated)

    assert verification["valid"] is False
    assert verification["error_codes"] == [
        "fact_ledger_frozen_field_unknown"
    ]
    assert verification["errors"][0]["path"] == (
        "$.evidence_facts[0].model_override"
    )


def test_source_centered_model_wire_lowers_to_same_canonical_ledger() -> None:
    canonical = _normalize(_valid_response())
    wire = _model_wire_payload(_valid_response())

    result = _normalize_model(wire)

    assert result["valid"] is True, result["errors"]
    assert result["raw_declarations"] == canonical["raw_declarations"]
    assert result["evidence_facts"] == canonical["evidence_facts"]
    assert result["raw_declarations_fingerprint"] == canonical[
        "raw_declarations_fingerprint"
    ]
    assert result["evidence_facts_fingerprint"] == canonical[
        "evidence_facts_fingerprint"
    ]
    assert result["fingerprint"] == canonical["fingerprint"]
    assert all(
        "anchor_evidence_ref" not in fact
        for record in wire["source_evidence_records"]
        for fact in record.get("owned_facts") or []
    )


@pytest.mark.parametrize("invalid_owned_facts", [None, {}, "facts"])
def test_source_centered_model_wire_requires_uniform_owned_facts_array(
    invalid_owned_facts: Any,
) -> None:
    payload = _model_wire_payload(_valid_response())
    payload["source_evidence_records"][2][
        "owned_facts"
    ] = invalid_owned_facts

    result = _normalize_model(payload)

    assert result["valid"] is False
    assert "source_record_owned_facts_not_list" in _error_codes(result)


def test_source_centered_model_wire_requires_owned_facts_field() -> None:
    missing_field = _model_wire_payload(_valid_response())
    missing_field["source_evidence_records"][2].pop("owned_facts")

    missing_result = _normalize_model(missing_field)

    assert missing_result["valid"] is False
    assert "source_record_owned_facts_not_list" in _error_codes(
        missing_result
    )


def test_source_centered_model_wire_never_repairs_owner_or_old_anchor() -> None:
    missing_owner_evidence = _model_wire_payload(_valid_response())
    owner_record = missing_owner_evidence["source_evidence_records"][0]
    owner_record["owned_facts"][0]["evidence"] = [CATALOG[1]["ref"]]

    missing_owner_result = _normalize_model(missing_owner_evidence)

    assert "fact_anchor_evidence_not_cited" in _error_codes(
        missing_owner_result
    )

    injected_anchor = _model_wire_payload(_valid_response())
    injected_anchor["source_evidence_records"][0]["owned_facts"][0][
        "anchor_evidence_ref"
    ] = CATALOG[1]["ref"]

    injected_anchor_result = _normalize_model(injected_anchor)

    assert "source_owned_fact_field_unknown" in _error_codes(
        injected_anchor_result
    )


def test_source_centered_model_wire_requires_complete_target_manifest() -> None:
    missing_target = _model_wire_payload(_valid_response())
    missing_target["source_evidence_records"].pop()

    result = _normalize_model(missing_target)

    assert result["valid"] is False
    assert "source_evidence_disposition_missing" in _error_codes(result)


def test_source_centered_support_target_projects_context_without_model_role() -> None:
    payload = _valid_response()
    payload["evidence_facts"][0]["evidence"].append(CATALOG[2]["ref"])

    result = _normalize_model(_model_wire_payload(payload))

    assert result["valid"] is True
    disposition_by_ref = {
        item["evidence_ref"]: item["disposition"]
        for item in result["raw_declarations"][
            "source_evidence_dispositions"
        ]
    }
    assert disposition_by_ref[CATALOG[2]["ref"]] == "context_only"


def test_model_wire_rejects_v3_disposition_without_compatibility() -> None:
    payload = _model_wire_payload(_valid_response())
    payload["source_evidence_records"][0]["disposition"] = "fact_backed"

    result = _normalize_model(payload)

    assert result["valid"] is False
    assert "source_evidence_record_field_unknown" in _error_codes(result)


def test_model_wire_rejects_legacy_flat_response_without_compatibility() -> None:
    result = _normalize_model(_valid_response())

    assert result["valid"] is False
    assert {
        "fact_ledger_response_field_unknown",
        "fact_model_response_field_missing",
    }.issubset(_error_codes(result))


def test_prompt_is_atomic_generic_and_has_no_scope_or_workflow_output() -> None:
    prompt = build_requirement_fact_ledger_prompt()

    assert "One fact expresses one cohesive actor/action/object/constraint/outcome unit" in prompt
    assert "independently truth-evaluable semantic claim" in prompt
    assert "one independently testable claim" in prompt
    assert "Each statement must be self-contained" in prompt
    assert 'Never use unresolved shorthand or external-position pointers such as "same as above"' in prompt
    assert '"as shown in the figure"' in prompt
    assert '"如原型图所示"' in prompt
    assert "every compared subject, rule, visible state, or value is explicitly named" in prompt
    assert "One transition fact has one trigger or precondition" in prompt
    assert "split them into separate facts" in prompt
    assert "fact_kind: action|algorithm|constraint|interaction|ui_element" in prompt
    assert "action: one observable actor or system operation" in prompt
    assert "algorithm: one explicit calculation" in prompt
    assert "constraint: one invariant, permission, prohibition" in prompt
    assert "interaction: one explicitly named source actor or component" in prompt
    assert "ui_element: one named UI element or entry" in prompt
    assert "priority must be unspecified unless" in prompt
    assert "earliest cited EV" in prompt
    assert "priority anchor" in prompt
    assert "never borrow a token" in prompt
    assert "Never infer priority" in prompt
    assert "source_evidence_records" in prompt
    assert "exactly one source_evidence_record for every target_source_evidence_catalog item" in prompt
    assert "smallest source_order" in prompt
    assert "anchor_evidence_ref" in prompt
    assert "Never output anchor_evidence_ref, fact_ids, or disposition yourself" in prompt
    assert "regression-invariant constraint" in prompt
    assert "global projection will bind its cited-support role" in prompt
    assert "statement must contain at most 320 characters" in prompt
    assert "evidence must contain at most 6 refs" in prompt
    assert "finite JSON number with 0 < confidence <= 1" in prompt
    shared_json_contract = strict_json_output_contract_prompt()
    assert prompt.count(shared_json_contract) == 1
    final_owner_check = prompt.split(
        "Final owner check (perform immediately before returning):", 1
    )[1]
    assert "T = every target_source_evidence_catalog ref" in final_owner_check
    assert "R = every emitted source record evidence_ref" in final_owner_check
    assert "O = target refs whose own record contains one or more owned_facts" in final_owner_check
    assert "R equals T with no omission or duplicate" in final_owner_check
    assert "Do not emit any source disposition" in final_owner_check
    assert "Every emitted FACT must be nested in exactly one SOURCE_RECORD" in final_owner_check
    assert "directly declared only by a context item" in final_owner_check
    assert "omit that fact from this shard" in final_owner_check
    assert "merely to gain local ownership" in final_owner_check
    assert "generic parent or heading with a specific child item" in final_owner_check
    assert "supplies the distinguishing action, object, state, or constraint" in final_owner_check
    assert "If multiple target refs are equally direct" in final_owner_check
    assert "Inspect every target quote for independently fact-bearing" in final_owner_check
    assert "do not hide a missing or incorrect owner" in final_owner_check
    assert "Perform a final statement-closure scan over every emitted FACT" in final_owner_check
    assert "do not return it" in final_owner_check
    assert "Resolve a marker only from explicitly named catalog content" in final_owner_check
    assert 'invalid "某对象的显示规则同原规则"' in final_owner_check
    assert 'valid "某对象的可观察显示行为必须保持不变"' in final_owner_check
    assert "Never invent the absent rule" in final_owner_check
    assert "verify the ownership projection input" in final_owner_check
    assert "no record contains disposition" in final_owner_check
    assert "Strict JSON output contract:" not in final_owner_check
    assert prompt.endswith(
        "Do not repeat a fact under a cited support record; direct declaration, not citation coverage, determines ownership."
    )
    assert '"boundaries"' not in prompt
    assert '"fact_bindings"' not in prompt
    assert '"semantic_graph"' not in prompt
    assert '"workflow_blueprints"' not in prompt
    for business_term in ("论坛", "作文", "官方区", "反馈区"):
        assert business_term not in prompt


def test_user_input_contains_only_catalog_and_fresh_compile_metadata() -> None:
    raw = build_requirement_fact_ledger_user_input(
        CATALOG,
        source_catalog_fingerprint=CATALOG_FINGERPRINT,
        attempt=2,
        compilation_mode="independent_recompile",
        recompile_reason_codes=["fact_ledger_response_field_unknown"],
    )
    payload = json.loads(raw)

    assert payload["input_version"] == "4"
    assert payload["target_source_evidence_catalog"] == [
        {**item, "source_order": index}
        for index, item in enumerate(CATALOG)
    ]
    assert payload["context_source_evidence_catalog"] == []
    assert payload["source_catalog_fingerprint"] == CATALOG_FINGERPRINT
    assert payload["compilation_policy"] == "fresh_compile"
    assert payload["compilation_mode"] == "independent_recompile"
    assert "previous_candidate" not in payload
    assert "retry_context" not in payload
    assert "source_evidence_catalog" not in payload
    assert "target_evidence_refs" not in payload


def test_shard_wire_keeps_global_context_order_and_places_target_catalog_last() -> None:
    target_ref = CATALOG[1]["ref"]
    raw = build_requirement_fact_ledger_user_input(
        CATALOG,
        source_catalog_fingerprint=CATALOG_FINGERPRINT,
        target_evidence_refs=[target_ref],
    )
    payload = json.loads(raw)

    assert list(payload)[-1] == "target_source_evidence_catalog"
    assert payload["context_source_evidence_catalog"] == [
        {**CATALOG[0], "source_order": 0},
        {**CATALOG[2], "source_order": 2},
    ]
    assert payload["target_source_evidence_catalog"] == [
        {**CATALOG[1], "source_order": 1}
    ]


def test_initial_candidate_succeeds_with_one_envelope() -> None:
    client = _ScriptedClient(
        (_json_response(_valid_response()), {"http_status": 200})
    )

    result = _compile(client)

    assert result.success is True
    assert result.status == "validated"
    assert result.normalized_ledger["valid"] is True
    assert result.evidence_facts == result.normalized_ledger["evidence_facts"]
    assert result.raw_declarations == result.normalized_ledger[
        "raw_declarations"
    ]
    assert len(client.calls) == 1
    assert result.diagnostics["fact_ledger_compile_envelope_count"] == 1
    assert result.diagnostics["fact_ledger_compile_physical_call_count"] == 1
    assert result.diagnostics["fact_ledger_compile_fresh_candidate_used"] is False
    assert result.diagnostics["fact_ledger_compile_validated_attempt"] == 1
    request = _request_payload(client.calls[0])
    assert request["source_catalog_fingerprint"] == CATALOG_FINGERPRINT
    assert request["attempt"] == 1
    assert request["compilation_mode"] == "initial"


def test_compiler_diagnostics_do_not_persist_raw_declarations_or_quotes() -> None:
    client = _ScriptedClient(_json_response(_valid_response()))

    result = _compile(client)
    diagnostic_text = json.dumps(result.diagnostics, ensure_ascii=False)

    assert result.success is True
    assert '"raw_declarations":' not in diagnostic_text
    assert CATALOG[0]["quote"] not in diagnostic_text
    assert "申请人提交记录后系统创建待处理任务" not in diagnostic_text


def test_parse_failure_starts_one_independent_fresh_candidate() -> None:
    client = _ScriptedClient("not-json", _json_response(_valid_response()))

    result = _compile(client)

    assert result.success is True
    assert len(client.calls) == 2
    assert [
        item["status"]
        for item in result.diagnostics["fact_ledger_compile_attempts"]
    ] == ["parse_failed", "validated"]
    fresh = _request_payload(client.calls[1])
    assert fresh["attempt"] == 2
    assert fresh["compilation_mode"] == "independent_recompile"
    assert fresh["compilation_policy"] == "fresh_compile"
    assert fresh["recompile_reason_codes"] == [
        "fact_ledger_json_parse_failed"
    ]
    assert "previous_candidate" not in fresh


def test_contract_failure_starts_one_independent_fresh_candidate() -> None:
    invalid = _valid_response()
    invalid["boundaries"] = []
    client = _ScriptedClient(
        _json_response(invalid),
        _json_response(_valid_response()),
    )

    result = _compile(client)

    assert result.success is True
    assert len(client.calls) == 2
    assert result.diagnostics[
        "fact_ledger_compile_fresh_candidate_trigger_codes"
    ] == ["fact_ledger_response_field_unknown"]


def test_compiler_rejects_legacy_flat_model_response_without_compatibility() -> None:
    legacy_response = json.dumps(_valid_response(), ensure_ascii=False)
    client = _ScriptedClient(legacy_response, legacy_response)

    result = _compile(client)

    assert result.success is False
    assert result.status == "contract_invalid"
    assert len(client.calls) == 2
    assert {
        "fact_ledger_response_field_unknown",
        "fact_model_response_field_missing",
    }.issubset(
        set(
            result.diagnostics[
                "fact_ledger_compile_fresh_candidate_trigger_codes"
            ]
        )
    )


def test_each_candidate_envelope_has_an_independent_transport_budget() -> None:
    invalid = _valid_response()
    invalid["boundaries"] = []
    gateway_timeout = (
        "Error: HTTP 504 - Gateway Time-out",
        {"http_status": 504, "wire_api": "chat_completions"},
    )
    client = _ScriptedClient(
        gateway_timeout,
        _json_response(invalid),
        gateway_timeout,
        _json_response(_valid_response()),
    )

    result = _compile(client)

    assert result.success is True
    assert len(client.calls) == 4
    assert client.calls[0] == client.calls[1]
    assert client.calls[2] == client.calls[3]
    assert client.calls[0] != client.calls[2]
    assert result.diagnostics["fact_ledger_compile_envelope_count"] == 2
    assert result.diagnostics["fact_ledger_compile_physical_call_count"] == 4
    assert result.diagnostics["fact_ledger_compile_transport_failure_count"] == 2
    assert result.diagnostics["fact_ledger_compile_transport_retry_count"] == 2


def test_transport_exhaustion_stops_without_fresh_candidate() -> None:
    timeout = (
        "Error: HTTP 504 - Gateway Time-out",
        {"http_status": 504},
    )
    client = _ScriptedClient(
        timeout,
        timeout,
        _json_response(_valid_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == "transport_exhausted"
    assert result.normalized_ledger == {}
    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]
    assert result.diagnostics["fact_ledger_compile_envelope_count"] == 1
    assert result.diagnostics["fact_ledger_compile_fresh_candidate_used"] is False


def test_fatal_model_error_stops_without_replay_or_fresh_candidate() -> None:
    client = _ScriptedClient(
        (
            "Error: HTTP 401 - unauthorized",
            {"http_status": 401, "wire_api": "chat_completions"},
        ),
        _json_response(_valid_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == "fatal_model_error"
    assert len(client.calls) == 1
    assert result.diagnostics["fact_ledger_compile_transport_retry_count"] == 0


def test_fresh_invalid_candidate_never_starts_a_third_candidate() -> None:
    invalid = _valid_response()
    invalid["fact_bindings"] = []
    client = _ScriptedClient(
        _json_response(invalid),
        _json_response(invalid),
        _json_response(_valid_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == "contract_invalid"
    assert len(client.calls) == 2
    assert result.normalized_ledger == {}


def test_mismatched_caller_fingerprint_fails_before_model_call() -> None:
    client = _ScriptedClient(_json_response(_valid_response()))

    with pytest.raises(ValueError, match="来源目录不一致"):
        compile_requirement_atomic_fact_ledger(
            client=client,
            source_evidence_catalog=CATALOG,
            source_catalog_fingerprint="wrong-fingerprint",
        )

    assert client.calls == []


def test_budget_partition_never_splits_a_partition_group() -> None:
    group_id = "PG_000000000001"
    catalog = [
        {
            "ref": "EV_000000000001",
            "quote": "独立需求一",
        },
        *[
            {
                "ref": f"EV_{index:012x}",
                "quote": f"同组需求 {index}",
                "partition_group_id": group_id,
            }
            for index in (2, 3)
        ],
        {
            "ref": "EV_000000000004",
            "quote": "独立需求四",
        },
    ]
    normalized = normalize_source_evidence_catalog(catalog)

    chunks, chunk_budget, oversized_count = (
        fact_ledger_compiler._partition_source_evidence_catalog(
            normalized["items"],
            max_tokens=1000,
        )
    )

    group_chunk_indexes = {
        chunk.index
        for chunk in chunks
        if any(
            item.get("partition_group_id") == group_id
            for item in chunk.items
        )
    }
    assert normalized["valid"] is True
    assert chunk_budget == 600
    assert group_chunk_indexes == {2}
    assert [item["ref"] for item in chunks[1].items] == [
        "EV_000000000002",
        "EV_000000000003",
    ]
    assert oversized_count == 0


def test_oversized_partition_group_is_one_independent_shard() -> None:
    group_id = "PG_000000000002"
    catalog = [
        {
            "ref": "EV_000000000001",
            "quote": "前置独立需求",
        },
        *[
            {
                "ref": f"EV_{index:012x}",
                "quote": f"超预算同组需求 {index}",
                "partition_group_id": group_id,
            }
            for index in (2, 3, 4)
        ],
        {
            "ref": "EV_000000000005",
            "quote": "后置独立需求",
        },
    ]
    normalized = normalize_source_evidence_catalog(catalog)

    chunks, chunk_budget, oversized_count = (
        fact_ledger_compiler._partition_source_evidence_catalog(
            normalized["items"],
            max_tokens=600,
        )
    )

    assert normalized["valid"] is True
    assert oversized_count == 1
    assert len(chunks) == 3
    assert [item["ref"] for item in chunks[1].items] == [
        "EV_000000000002",
        "EV_000000000003",
        "EV_000000000004",
    ]
    assert chunks[1].budget_units > chunk_budget


def test_budget_partition_uses_full_catalog_and_mutually_exclusive_targets() -> None:
    catalog = _budget_split_catalog()
    client = _AdaptiveClient(
        lambda payload, _call_index: _json_response(
            _local_chunk_response(payload)
        )
    )

    result = _compile_budget_split(client, catalog)

    assert result.success is True
    assert len(client.calls) == 2
    requests = [_request_payload(call) for call in client.calls]
    assert all(item["compilation_scope"] == "catalog_shard" for item in requests)
    target_groups = [_request_target_refs(item) for item in requests]
    context_groups = [_request_context_refs(item) for item in requests]
    assert [ref for group in target_groups for ref in group] == [
        item["ref"] for item in catalog
    ]
    assert set(target_groups[0]).isdisjoint(target_groups[1])
    for request, targets, contexts in zip(
        requests,
        target_groups,
        context_groups,
        strict=True,
    ):
        assert set(targets).isdisjoint(contexts)
        assert sorted(
            [*targets, *contexts],
            key=lambda ref: next(
                item["source_order"]
                for item in [
                    *request["target_source_evidence_catalog"],
                    *request["context_source_evidence_catalog"],
                ]
                if item["ref"] == ref
            ),
        ) == [item["ref"] for item in catalog]
    assert result.diagnostics["fact_ledger_compile_chunk_count"] == 2
    assert result.diagnostics["fact_ledger_compile_candidate_attempt_limit"] == 4
    assert result.diagnostics["fact_ledger_compile_physical_call_count"] == 2
    assert result.diagnostics["fact_ledger_compile_fresh_candidate_used"] is False
    fact_ids = [
        item["fact_id"]
        for item in result.raw_declarations["evidence_facts"]
    ]
    assert len(fact_ids) == len(set(fact_ids)) == 2
    assert all(item.startswith("FACT_A1_") for item in fact_ids)
    disposition_by_ref = {
        item["evidence_ref"]: item["disposition"]
        for item in result.raw_declarations[
            "source_evidence_dispositions"
        ]
    }
    assert disposition_by_ref[catalog[1]["ref"]] == "non_requirement"
    assert disposition_by_ref[catalog[3]["ref"]] == "non_requirement"
    assert validate_requirement_fact_ledger_fingerprints(
        result.normalized_ledger
    )["valid"] is True


def test_cross_target_fact_has_one_owner_and_rebuilds_later_disposition() -> None:
    catalog = _budget_split_catalog()
    first_ref = catalog[0]["ref"]
    later_ref = catalog[2]["ref"]

    def respond(payload: dict[str, Any], _call_index: int) -> str:
        evidence_refs = (
            [first_ref, later_ref]
            if first_ref in _request_target_refs(payload)
            else []
        )
        return _json_response(
            _local_chunk_response(payload, evidence_refs=evidence_refs)
        )

    result = _compile_budget_split(_AdaptiveClient(respond), catalog)

    assert result.success is True
    assert len(result.raw_declarations["evidence_facts"]) == 1
    fact_id = result.raw_declarations["evidence_facts"][0]["fact_id"]
    dispositions = {
        item["evidence_ref"]: item
        for item in result.raw_declarations[
            "source_evidence_dispositions"
        ]
    }
    assert dispositions[first_ref] == {
        "evidence_ref": first_ref,
        "fact_ids": [fact_id],
        "disposition": "fact_backed",
    }
    assert dispositions[later_ref] == {
        "evidence_ref": later_ref,
        "fact_ids": [fact_id],
        "disposition": "context_only",
    }


def test_global_merge_projects_cross_shard_support_from_fact_evidence() -> None:
    catalog = _budget_split_catalog()
    owner_ref = catalog[0]["ref"]
    support_ref = catalog[2]["ref"]

    def respond(payload: dict[str, Any], _call_index: int) -> str:
        target_refs = _request_target_refs(payload)
        if owner_ref in target_refs:
            return _json_response(
                {
                    "evidence_facts": [
                        _fact(
                            "FACT_CROSS_SHARD",
                            "来源之间存在可验证的辅助关系",
                            [owner_ref, support_ref],
                            anchor_evidence_ref=owner_ref,
                        )
                    ],
                    "source_evidence_dispositions": [
                        {
                            "evidence_ref": evidence_ref,
                            "disposition": (
                                "fact_backed"
                                if evidence_ref == owner_ref
                                else "context_only"
                            ),
                        }
                        for evidence_ref in target_refs
                    ],
                }
            )
        return _json_response(
            {
                "evidence_facts": [],
                "source_evidence_dispositions": [
                    {
                        "evidence_ref": evidence_ref,
                        "disposition": (
                            "non_requirement"
                            if evidence_ref == support_ref
                            else "context_only"
                        ),
                    }
                    for evidence_ref in target_refs
                ],
            }
        )

    result = _compile_budget_split(_AdaptiveClient(respond), catalog)

    assert result.success is True
    assert result.status == "validated"
    dispositions = {
        item["evidence_ref"]: item["disposition"]
        for item in result.raw_declarations[
            "source_evidence_dispositions"
        ]
    }
    assert dispositions[owner_ref] == "fact_backed"
    assert dispositions[support_ref] == "context_only"
    assert result.diagnostics["fact_ledger_compile_global_status"] == (
        "validated"
    )


def test_compiler_derives_one_to_many_and_many_to_one_fact_relations() -> None:
    shared_statement = "申请记录与审核任务之间存在处理关联"
    local_statement = "审核员只能处理分配给自己的任务"
    payload = {
        "evidence_facts": [
            _fact(
                "FACT_SHARED",
                shared_statement,
                [CATALOG[0]["ref"], CATALOG[1]["ref"]],
                anchor_evidence_ref=CATALOG[0]["ref"],
            ),
            _fact(
                "FACT_LOCAL",
                local_statement,
                [CATALOG[1]["ref"]],
                anchor_evidence_ref=CATALOG[1]["ref"],
            ),
        ],
        "source_evidence_dispositions": [
            {
                "evidence_ref": CATALOG[0]["ref"],
                "disposition": "fact_backed",
            },
            {
                "evidence_ref": CATALOG[1]["ref"],
                "disposition": "fact_backed",
            },
            {
                "evidence_ref": CATALOG[2]["ref"],
                "disposition": "context_only",
            },
        ],
    }

    result = _compile(_ScriptedClient(_json_response(payload)))

    assert result.success is True
    facts_by_statement = {
        item["statement"]: item["fact_id"]
        for item in result.raw_declarations["evidence_facts"]
    }
    shared_fact_id = facts_by_statement[shared_statement]
    local_fact_id = facts_by_statement[local_statement]
    dispositions = {
        item["evidence_ref"]: item
        for item in result.raw_declarations[
            "source_evidence_dispositions"
        ]
    }
    assert dispositions[CATALOG[0]["ref"]]["fact_ids"] == [
        shared_fact_id
    ]
    assert set(dispositions[CATALOG[1]["ref"]]["fact_ids"]) == {
        shared_fact_id,
        local_fact_id,
    }


def test_merge_rejects_anchored_non_owner_projection_input() -> None:
    invalid_chunk = {
        "raw_declarations": copy.deepcopy(_valid_response())
    }
    invalid_chunk["raw_declarations"]["source_evidence_dispositions"][0][
        "disposition"
    ] = "context_only"

    merged, error_codes, _ = (
        fact_ledger_compiler._merge_chunk_raw_declarations(
            [invalid_chunk],
            source_evidence_catalog=CATALOG,
        )
    )

    assert "fact_ledger_anchored_disposition_mismatch" in error_codes


def test_same_global_fact_with_different_anchors_fails_closed() -> None:
    catalog = _budget_split_catalog()
    first_ref = catalog[0]["ref"]
    later_ref = catalog[2]["ref"]

    def respond(payload: dict[str, Any], _call_index: int) -> str:
        target_refs = _request_target_refs(payload)
        anchor_ref = first_ref if first_ref in target_refs else later_ref
        return _json_response(
            _local_chunk_response(
                payload,
                evidence_refs=[first_ref, later_ref],
                anchor_evidence_ref=anchor_ref,
            )
        )

    result = _compile_budget_split(_AdaptiveClient(respond), catalog)

    assert result.success is False
    assert result.status == "contract_invalid"
    assert result.normalized_ledger == {}
    assert result.diagnostics["fact_ledger_compile_global_status"] == (
        "merge_invalid"
    )
    assert "fact_ledger_global_fact_anchor_conflict" in result.diagnostics[
        "fact_ledger_compile_global_error_codes"
    ]


def test_target_anchor_allows_earlier_context_as_supporting_evidence() -> None:
    catalog = _budget_split_catalog()
    payload = _local_chunk_response(
        {
            "target_source_evidence_catalog": [
                {"ref": catalog[2]["ref"]}
            ],
        },
        evidence_refs=[catalog[0]["ref"], catalog[2]["ref"]],
    )

    result = normalize_requirement_fact_ledger(
        payload,
        source_evidence_catalog=catalog,
        source_catalog_fingerprint=fingerprint_source_evidence_catalog(catalog),
        target_evidence_refs=[catalog[2]["ref"]],
        shard_mode=True,
    )

    assert result["valid"] is True
    declaration = result["raw_declarations"]["evidence_facts"][0]
    assert declaration["anchor_evidence_ref"] == catalog[2]["ref"]
    assert declaration["evidence"] == [
        catalog[0]["ref"],
        catalog[2]["ref"],
    ]


def test_context_anchor_is_rejected_as_non_owner() -> None:
    catalog = _budget_split_catalog()
    payload = _local_chunk_response(
        {
            "target_source_evidence_catalog": [
                {"ref": catalog[2]["ref"]}
            ],
        },
        evidence_refs=[catalog[0]["ref"], catalog[2]["ref"]],
        anchor_evidence_ref=catalog[0]["ref"],
    )

    result = normalize_requirement_fact_ledger(
        payload,
        source_evidence_catalog=catalog,
        source_catalog_fingerprint=fingerprint_source_evidence_catalog(catalog),
        target_evidence_refs=[catalog[2]["ref"]],
        shard_mode=True,
    )

    assert result["valid"] is False
    assert "fact_target_owner_mismatch" in _error_codes(result)


def test_second_shard_cannot_hide_earlier_owner_as_seventh_evidence_ref() -> None:
    catalog = [
        {
            "ref": "EV_000000000001",
            "quote": "前置需求上下文" * 190,
        },
        *[
            {
                "ref": f"EV_{index:012x}",
                "quote": f"第 {index} 条通用原子需求",
            }
            for index in range(2, 9)
        ],
    ]
    first_ref = catalog[0]["ref"]

    def respond(payload: dict[str, Any], _call_index: int) -> str:
        target_refs = _request_target_refs(payload)
        if first_ref in target_refs:
            return _json_response(_local_chunk_response(payload))
        owned_refs = target_refs[:6]
        return _json_response(
            _local_chunk_response(
                payload,
                evidence_refs=[*owned_refs, first_ref],
            )
        )

    client = _AdaptiveClient(respond)
    result = compile_requirement_atomic_fact_ledger(
        client=client,
        source_evidence_catalog=catalog,
        max_tokens=3500,
        request_timeout_seconds=120,
    )

    assert result.diagnostics["fact_ledger_compile_chunk_count"] == 2
    assert result.success is False
    assert result.status == "contract_invalid"
    assert result.normalized_ledger == {}
    assert len(client.calls) == 3
    assert result.diagnostics["fact_ledger_compile_failed_chunk_index"] == 2
    assert "fact_evidence_count_exceeds_limit" in result.diagnostics[
        "fact_ledger_compile_fresh_candidate_trigger_codes"
    ]


def test_overlong_same_prefix_statements_never_merge_into_published_fact() -> None:
    statement_prefix = "甲" * 320
    assert len(statement_prefix) == 320
    payload = {
        "evidence_facts": [
            _fact(
                "FACT_LONG_A",
                statement_prefix + "甲",
                [CATALOG[0]["ref"]],
            ),
            _fact(
                "FACT_LONG_B",
                statement_prefix + "乙",
                [CATALOG[0]["ref"]],
            ),
        ],
        "source_evidence_dispositions": [
            {
                "evidence_ref": CATALOG[0]["ref"],
                "disposition": "fact_backed",
            },
            *[
                {
                    "evidence_ref": item["ref"],
                    "disposition": "context_only",
                }
                for item in CATALOG[1:]
            ],
        ],
    }
    response = _json_response(payload)

    result = _compile(_ScriptedClient(response, response))

    assert result.success is False
    assert result.status == "contract_invalid"
    assert result.normalized_ledger == {}
    assert "fact_statement_exceeds_limit" in result.diagnostics[
        "fact_ledger_compile_fresh_candidate_trigger_codes"
    ]
    assert result.diagnostics[
        "fact_ledger_compile_collapsed_duplicate_fact_count"
    ] == 0


def test_chunk_failure_discards_every_previously_validated_partial_ledger() -> None:
    catalog = _budget_split_catalog()
    first_target_ref = catalog[0]["ref"]

    def respond(payload: dict[str, Any], _call_index: int) -> str:
        if first_target_ref in _request_target_refs(payload):
            return _json_response(_local_chunk_response(payload))
        return "not-json"

    client = _AdaptiveClient(respond)
    result = _compile_budget_split(client, catalog)

    assert result.success is False
    assert result.status == "parse_failed"
    assert result.normalized_ledger == {}
    assert len(client.calls) == 3
    assert result.diagnostics["fact_ledger_compile_completed_chunk_count"] == 1
    assert result.diagnostics["fact_ledger_compile_failed_chunk_index"] == 2


@pytest.mark.parametrize("partial_text", ["{", ""])
def test_length_finish_reason_fails_as_truncated_without_same_size_fresh(
    partial_text: str,
) -> None:
    client = _ScriptedClient(
        (
            partial_text,
            {"finish_reason": "length", "content_len": len(partial_text)},
        ),
        _json_response(_valid_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == "output_truncated"
    assert result.normalized_ledger == {}
    assert len(client.calls) == 1
    assert result.diagnostics["fact_ledger_compile_fresh_candidate_used"] is False
    assert result.diagnostics["fact_ledger_compile_attempts"][0][
        "parse_error_code"
    ] == "fact_ledger_output_truncated"


def test_explicit_incomplete_response_fails_without_same_size_fresh() -> None:
    client = _ScriptedClient(
        (
            _json_response(_valid_response()),
            {
                "response_status": "incomplete",
                "incomplete_reason": "content_filter",
                # 显式冲突时，不完整状态必须压过 stop。
                "finish_reason": "stop",
            },
        ),
        _json_response(_valid_response()),
    )

    result = _compile(client)

    assert result.success is False
    assert result.status == "output_incomplete"
    assert result.normalized_ledger == {}
    assert len(client.calls) == 1
    assert result.diagnostics["fact_ledger_compile_fresh_candidate_used"] is False
    assert result.diagnostics["fact_ledger_compile_attempts"][0][
        "parse_error_code"
    ] == "fact_ledger_output_incomplete"


def test_duplicate_semantic_fact_collapses_with_conservative_confidence() -> None:
    payload = _valid_response()
    duplicate_a = copy.deepcopy(payload["evidence_facts"][0])
    duplicate_b = copy.deepcopy(duplicate_a)
    duplicate_a["fact_id"] = "FACT_DUP_A"
    duplicate_a["confidence"] = 0.91
    duplicate_b["fact_id"] = "FACT_DUP_B"
    duplicate_b["confidence"] = 0.63
    payload["evidence_facts"] = [duplicate_a, duplicate_b]
    payload["source_evidence_dispositions"] = [
        {
            "evidence_ref": CATALOG[0]["ref"],
            "disposition": "fact_backed",
        },
        {
            "evidence_ref": CATALOG[1]["ref"],
            "disposition": "context_only",
        },
        {
            "evidence_ref": CATALOG[2]["ref"],
            "disposition": "context_only",
        },
    ]

    result = _compile(_ScriptedClient(_json_response(payload)))

    assert result.success is True
    assert len(result.raw_declarations["evidence_facts"]) == 1
    assert result.raw_declarations["evidence_facts"][0]["confidence"] == 0.63
    assert len(
        result.raw_declarations["source_evidence_dispositions"][0][
            "fact_ids"
        ]
    ) == 1
    assert result.diagnostics[
        "fact_ledger_compile_collapsed_duplicate_fact_count"
    ] == 1


def test_all_context_chunks_are_local_valid_but_global_ledger_fails_closed() -> None:
    catalog = _budget_split_catalog()
    client = _AdaptiveClient(
        lambda payload, _call_index: _json_response(
            _local_chunk_response(payload, evidence_refs=[])
        )
    )

    result = _compile_budget_split(client, catalog)

    assert len(client.calls) == 2
    assert result.success is False
    assert result.status == "contract_invalid"
    assert result.normalized_ledger == {}
    assert result.diagnostics["fact_ledger_compile_completed_chunk_count"] == 2
    assert result.diagnostics["fact_ledger_compile_global_status"] == (
        "contract_invalid"
    )
    assert "fact_ledger_empty" in result.diagnostics[
        "fact_ledger_compile_global_error_codes"
    ]


def test_chunk_limit_preflight_stops_before_any_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _budget_split_catalog()
    client = _AdaptiveClient(
        lambda _payload, _call_index: pytest.fail("preflight 后不应调用模型")
    )
    monkeypatch.setattr(fact_ledger_compiler, "MAX_FACT_LEDGER_CHUNKS", 1)

    result = _compile_budget_split(client, catalog)

    assert result.success is False
    assert result.normalized_ledger == {}
    assert client.calls == []
    assert result.diagnostics["fact_ledger_compile_global_status"] == (
        "chunk_limit_exceeded"
    )


def test_stable_fact_identity_orders_ev_refs_by_catalog_manifest() -> None:
    ev_2 = "EV_000000000002"
    ev_10 = "EV_000000000010"
    fact = _fact("LOCAL", "跨来源原子事实", [ev_2, ev_10])
    catalog_ref_order = {ev_10: 0, ev_2: 1}

    identity = fact_ledger_compiler._stable_global_fact_identity(
        fact,
        catalog_ref_order=catalog_ref_order,
    )
    reverse_identity = fact_ledger_compiler._stable_global_fact_identity(
        {**fact, "evidence": [ev_10, ev_2]},
        catalog_ref_order=catalog_ref_order,
    )

    assert identity["evidence"] == [ev_10, ev_2]
    assert fact_ledger_compiler._stable_global_fact_id(
        identity
    ) == fact_ledger_compiler._stable_global_fact_id(reverse_identity)
