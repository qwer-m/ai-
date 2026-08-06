from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from modules.agent_platform import test_generation_semantics


def _document_manifest() -> dict:
    source_hash = "a" * 64
    blocks = [
        {
            "block_id": "P0001-T0001",
            "text": "旧值10次",
            "source_span": {"start": 0, "end": 5},
        },
        {
            "block_id": "P0001-T0002",
            "text": "次数由后台配置",
            "source_span": {"start": 6, "end": 13},
        },
    ]
    return {
        "schema_version": 3,
        "document_id": 9,
        "source_sha256": source_hash,
        "page_count": 1,
        "pages": [
            {
                "page_number": 1,
                "image_sha256": "b" * 64,
                "blocks": blocks,
                "marks": [
                    {
                        "mark_id": "P0001-M0001",
                        "type": "strikeout",
                        "source": "pdf_annotation",
                        "bbox": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.1},
                        "target_block_ids": ["P0001-T0001"],
                        "target_source_spans": [{"start": 0, "end": 5}],
                        "asset_source_sha256": source_hash,
                        "annotation_subtype": "StrikeOut",
                        "contents": "",
                        "title": "",
                    }
                ],
            }
        ],
    }


def test_document_semantics_reads_each_page_once_and_forces_strikeout_superseded(
    monkeypatch,
) -> None:
    manifest = _document_manifest()
    page_text = "旧值10次\n次数由后台配置"
    monkeypatch.setattr(
        test_generation_semantics,
        "load_document_manifest",
        lambda document_id: manifest,
    )
    monkeypatch.setattr(
        test_generation_semantics,
        "document_page_text",
        lambda document_id, page_number: page_text,
    )
    monkeypatch.setattr(
        test_generation_semantics,
        "document_page_layout",
        lambda document_id, page_number: manifest["pages"][0]["blocks"],
    )
    context = SimpleNamespace(artifacts={})
    prepared = test_generation_semantics.prepare_source_semantics(
        context,
        {
            "requirement": page_text,
            "evidence_source": {
                "kind": "knowledge_document",
                "document_id": 9,
                "asset_available": True,
                "content_hash": "a" * 64,
            },
            "evidence_catalog": {
                "document_id": 9,
                "items": [
                    {
                        "evidence_id": "EV-0001",
                        "page_number": 1,
                        "block_ids": ["P0001-T0001", "P0001-T0002"],
                        "source_offset_start": 0,
                        "source_offset_end": len(page_text),
                    }
                ],
            },
        },
    )
    assert prepared["item_count"] == 1
    assert len(prepared["items"][0]["marks"]) == 1
    assert prepared["items"][0]["strikeout_spans"] == [
        {"block_id": "P0001-T0001", "source_span": {"start": 0, "end": 5}}
    ]

    facts = [
        {
            "fact_id": "F-001",
            "assertion": "旧值为10次",
            "scope_id": "EV-0001",
            "source_anchor": {
                "source_kind": "document",
                "document_id": 9,
                "page_number": 1,
                "block_id": "P0001-T0001",
                "source_span": {"start": 0, "end": 5},
                "quote": "旧值10次",
                "asset_source_sha256": "a" * 64,
                "page_image_sha256": "b" * 64,
            },
            "status": "effective",
            "value_policy": "exact",
            "governed_values": [],
            "governed_by": [],
        },
        {
            "fact_id": "F-002",
            "assertion": "次数由后台配置",
            "scope_id": "EV-0001",
            "source_anchor": {
                "source_kind": "document",
                "document_id": 9,
                "page_number": 1,
                "block_id": "P0001-T0002",
                "source_span": {"start": 6, "end": 13},
                "quote": "次数由后台配置",
                "asset_source_sha256": "a" * 64,
                "page_image_sha256": "b" * 64,
            },
            "status": "effective",
            "value_policy": "runtime_configured",
            "governed_values": ["次数"],
            "governed_by": [
                {"relation": "invalidates", "directive_fact_id": "F-001"}
            ],
        },
    ]
    merged = test_generation_semantics.merge_source_semantics(
        context,
        {
            "semantic_inputs": prepared["items"],
            "semantic_records": [{"item_index": 0, "output": {"authoritative_facts": facts}}],
        },
    )
    assert [fact["status"] for fact in merged["authoritative_facts"]] == [
        "superseded",
        "effective",
    ]
    assert [fact["fact_id"] for fact in merged["effective_facts"]] == ["F-002"]


def test_inline_semantics_requires_exact_hash_offset_and_quote() -> None:
    requirement = "提交后进入审核中"
    requirement_hash = hashlib.sha256(requirement.encode("utf-8")).hexdigest()
    context = SimpleNamespace(artifacts={})
    prepared = test_generation_semantics.prepare_source_semantics(
        context,
        {
            "requirement": requirement,
            "evidence_source": {
                "kind": "inline",
                "content_hash": requirement_hash,
            },
            "evidence_catalog": {
                "document_id": None,
                "items": [
                    {
                        "evidence_id": "EV-0001",
                        "source_offset_start": 0,
                        "source_offset_end": len(requirement),
                    }
                ],
            },
        },
    )
    fact = {
        "fact_id": "F-I-001",
        "assertion": requirement,
        "scope_id": "EV-0001",
        "source_anchor": {
            "source_kind": "inline",
            "requirement_sha256": requirement_hash,
            "source_offset_start": 0,
            "source_offset_end": len(requirement),
            "quote": "提交后进入等待中",
        },
        "status": "effective",
        "value_policy": "exact",
        "governed_values": [],
        "governed_by": [],
    }
    with pytest.raises(ValueError, match="quote 未精确命中"):
        test_generation_semantics.merge_source_semantics(
            context,
            {
                "semantic_inputs": prepared["items"],
                "semantic_records": [
                    {"item_index": 0, "output": {"authoritative_facts": [fact]}}
                ],
            },
        )


def test_runtime_configured_fact_rejects_unanchored_governed_value() -> None:
    requirement = "展示次数由后台配置"
    requirement_hash = hashlib.sha256(requirement.encode("utf-8")).hexdigest()
    context = SimpleNamespace(artifacts={})
    prepared = test_generation_semantics.prepare_source_semantics(
        context,
        {
            "requirement": requirement,
            "evidence_source": {"kind": "inline", "content_hash": requirement_hash},
            "evidence_catalog": {
                "document_id": None,
                "items": [
                    {
                        "evidence_id": "EV-0001",
                        "source_offset_start": 0,
                        "source_offset_end": len(requirement),
                    }
                ],
            },
        },
    )
    fact = {
        "fact_id": "F-I-001",
        "assertion": requirement,
        "scope_id": "EV-0001",
        "source_anchor": {
            "source_kind": "inline",
            "requirement_sha256": requirement_hash,
            "source_offset_start": 0,
            "source_offset_end": len(requirement),
            "quote": requirement,
        },
        "status": "effective",
        "value_policy": "runtime_configured",
        "governed_values": ["固定5次"],
        "governed_by": [],
    }
    with pytest.raises(ValueError, match="governed_value 未命中"):
        test_generation_semantics.merge_source_semantics(
            context,
            {
                "semantic_inputs": prepared["items"],
                "semantic_records": [
                    {"item_index": 0, "output": {"authoritative_facts": [fact]}}
                ],
            },
        )


def _reconciliation_fact(
    fact_id: str,
    *,
    page_number: int,
    assertion: str,
    status: str = "effective",
) -> dict:
    return {
        "fact_id": fact_id,
        "assertion": assertion,
        "scope_id": f"EV-{page_number:04d}",
        "source_anchor": {
            "source_kind": "document",
            "document_id": 9,
            "page_number": page_number,
            "block_id": f"P{page_number:04d}-T0001",
            "source_span": {"start": 0, "end": len(assertion)},
            "quote": assertion,
            "asset_source_sha256": "a" * 64,
            "page_image_sha256": "b" * 64,
        },
        "status": status,
        "value_policy": "exact",
        "governed_values": [],
        "governed_by": [],
    }


def test_prepare_authority_reconciliation_only_reviews_multi_source_modules() -> None:
    context = SimpleNamespace(artifacts={})
    facts = [
        _reconciliation_fact("F-OLD", page_number=1, assertion="未上传时按钮不可点击"),
        _reconciliation_fact("F-NEW", page_number=2, assertion="未上传时点击按钮显示提示"),
        _reconciliation_fact("F-ONE", page_number=3, assertion="单页规则"),
    ]
    prepared = test_generation_semantics.prepare_authority_reconciliation(
        context,
        {
            "plan": {
                "business_modules": [
                    {"name": "模块A", "evidence_ids": ["EV-0001", "EV-0002"]},
                    {"name": "模块B", "evidence_ids": ["EV-0003"]},
                ]
            },
            "authoritative_facts": facts,
        },
    )
    assert prepared["review_module_count"] == 1
    assert [fact["fact_id"] for fact in prepared["items"][0]["authoritative_facts"]] == [
        "F-OLD",
        "F-NEW",
    ]
    assert context.artifacts["authority_reconciliation_prepare"]["skipped_module_count"] == 1


def test_merge_authority_reconciliation_applies_cross_page_replacement() -> None:
    context = SimpleNamespace(artifacts={})
    old_fact = _reconciliation_fact(
        "F-OLD", page_number=1, assertion="未上传时按钮不可点击"
    )
    new_fact = _reconciliation_fact(
        "F-NEW", page_number=2, assertion="未上传时点击按钮显示提示"
    )
    prepared = [
        {
            "module_index": 0,
            "module": {"name": "模块A", "evidence_ids": ["EV-0001", "EV-0002"]},
            "authoritative_facts": [old_fact, new_fact],
        }
    ]
    decisions = [
        {
            "fact_id": "F-OLD",
            "status": "superseded",
            "value_policy": "exact",
            "governed_values": [],
            "governed_by": [
                {"relation": "invalidates", "directive_fact_id": "F-NEW"}
            ],
            "reason": "后续规则明确修订同一交互。",
        },
        {
            "fact_id": "F-NEW",
            "status": "effective",
            "value_policy": "exact",
            "governed_values": [],
            "governed_by": [],
            "reason": "保留后续明确规则。",
        },
    ]
    merged = test_generation_semantics.merge_authority_reconciliation(
        context,
        {
            "authoritative_facts": [old_fact, new_fact],
            "prepared_items": prepared,
            "reconciliation_records": [
                {"item_index": 0, "output": {"decisions": decisions}}
            ],
        },
    )
    assert [fact["fact_id"] for fact in merged["effective_facts"]] == ["F-NEW"]
    assert merged["authoritative_facts"][0]["governed_by"] == [
        {"relation": "invalidates", "directive_fact_id": "F-NEW"}
    ]


def test_merge_authority_reconciliation_rejects_inactive_fact_reactivation() -> None:
    context = SimpleNamespace(artifacts={})
    inactive = _reconciliation_fact(
        "F-OLD",
        page_number=1,
        assertion="已删除规则",
        status="superseded",
    )
    active = _reconciliation_fact("F-NEW", page_number=2, assertion="当前规则")
    with pytest.raises(ValueError, match="不得重新激活"):
        test_generation_semantics.merge_authority_reconciliation(
            context,
            {
                "authoritative_facts": [inactive, active],
                "prepared_items": [
                    {
                        "module_index": 0,
                        "module": {
                            "name": "模块A",
                            "evidence_ids": ["EV-0001", "EV-0002"],
                        },
                        "authoritative_facts": [inactive, active],
                    }
                ],
                "reconciliation_records": [
                    {
                        "item_index": 0,
                        "output": {
                            "decisions": [
                                {
                                    "fact_id": "F-OLD",
                                    "status": "effective",
                                    "value_policy": "exact",
                                    "governed_values": [],
                                    "governed_by": [],
                                    "reason": "错误复活。",
                                },
                                {
                                    "fact_id": "F-NEW",
                                    "status": "effective",
                                    "value_policy": "exact",
                                    "governed_values": [],
                                    "governed_by": [],
                                    "reason": "保持。",
                                },
                            ]
                        },
                    }
                ],
            },
        )
