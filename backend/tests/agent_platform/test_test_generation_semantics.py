from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate

from modules.agent_platform import test_generation_semantics
from modules.agent_platform.test_generation_workflow import (
    AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA,
    AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA,
    SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA,
    SOURCE_SEMANTICS_INPUT_SCHEMA,
)


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


def test_document_anchor_uses_block_id_to_disambiguate_repeated_quote() -> None:
    page_text = "重复\n唯一\n重复"
    prepared = {
        "document_id": 9,
        "page_number": 1,
        "page_text": page_text,
        "blocks": [
            {"block_id": "P0001-T0001", "source_span": {"start": 0, "end": 2}},
            {"block_id": "P0001-T0002", "source_span": {"start": 3, "end": 5}},
            {"block_id": "P0001-T0003", "source_span": {"start": 6, "end": 8}},
        ],
        "asset_source_sha256": "a" * 64,
        "page_image_sha256": "b" * 64,
        "strikeout_spans": [],
        "source_scopes": [
            {
                "scope_id": "EV-0001",
                "allowed_block_ids": [
                    "P0001-T0001",
                    "P0001-T0002",
                    "P0001-T0003",
                ],
                "source_span": {"start": 0, "end": len(page_text)},
            }
        ],
    }

    anchor, _, scope_ids = test_generation_semantics._validated_document_anchor(
        {
            "document_id": 9,
            "page_number": 1,
            "block_id": "P0001-T0003",
            "quote": "重复",
        },
        prepared,
    )

    assert anchor["source_span"] == {"start": 6, "end": 8}
    assert anchor["block_id"] == "P0001-T0003"
    assert scope_ids == {"EV-0001"}

    expanded_anchor, _, _ = test_generation_semantics._validated_document_anchor(
        {
            "document_id": 9,
            "page_number": 1,
            "block_id": "P0001-T0003",
            "quote": "重复（模型补充的扩展说明）",
        },
        prepared,
    )
    assert expanded_anchor["source_span"] == {"start": 6, "end": 8}
    assert expanded_anchor["quote"] == "重复"


def test_document_anchor_derives_canonical_values_from_one_anchor_kind() -> None:
    first_block_text = "模版 示意图 说明"
    second_block_text = "批改 分为两个部分"
    page_text = f"{first_block_text}\n{second_block_text}"
    second_block_start = len(first_block_text) + 1
    prepared = {
        "document_id": 259,
        "page_number": 6,
        "page_text": page_text,
        "blocks": [
            {
                "block_id": "P0006-T0011",
                "source_span": {"start": 0, "end": len(first_block_text)},
            },
            {
                "block_id": "P0006-T0012",
                "source_span": {
                    "start": second_block_start,
                    "end": len(page_text),
                },
            },
        ],
        "asset_source_sha256": "a" * 64,
        "page_image_sha256": "b" * 64,
        "strikeout_spans": [],
        "source_scopes": [
            {
                "scope_id": "EV-0006",
                "allowed_block_ids": ["P0006-T0011", "P0006-T0012"],
                "source_span": {"start": 0, "end": len(page_text)},
            }
        ],
    }

    quote_anchor, _, _ = test_generation_semantics._validated_document_anchor(
        {"document_id": 259, "page_number": 6, "quote": first_block_text},
        prepared,
    )
    assert quote_anchor["block_id"] == "P0006-T0011"
    assert quote_anchor["source_span"] == {"start": 0, "end": len(first_block_text)}

    block_anchor, _, _ = test_generation_semantics._validated_document_anchor(
        {
            "document_id": 259,
            "page_number": 6,
            "block_id": ["P0006-T0012", "P0006-T0011"],
        },
        prepared,
    )
    assert block_anchor["block_id"] == ["P0006-T0011", "P0006-T0012"]
    assert block_anchor["source_span"] == {"start": 0, "end": len(page_text)}
    assert block_anchor["quote"] == page_text

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
    prepared_items = [*prepared["text_items"], *prepared["vision_items"]]
    assert "work_assignments" not in prepared_items[0]
    assert len(prepared_items[0]["marks"]) == 1
    assert prepared_items[0]["source_scopes"] == [
        {
            "scope_id": "EV-0001",
            "allowed_block_ids": ["P0001-T0001", "P0001-T0002"],
            "source_span": {"start": 0, "end": len(page_text)},
        }
    ]
    assert "block_ids" not in prepared_items[0]["source_scopes"][0]
    validate(instance=prepared_items[0], schema=SOURCE_SEMANTICS_INPUT_SCHEMA)
    assert prepared_items[0]["strikeout_spans"] == [
        {"block_id": "P0001-T0001", "source_span": {"start": 0, "end": 5}}
    ]
    normalized_anchor, superseded, allowed_scope_ids = (
        test_generation_semantics._validated_document_anchor(
            {
                "document_id": 9,
                "page_number": 1,
                "block_id": ["P0001-T0001", "P0001-T0002"],
                "source_span": {"start": 0, "end": len(page_text)},
                "quote": page_text,
                "asset_source_sha256": "a" * 64,
                "page_image_sha256": "b" * 64,
            },
            prepared_items[0],
        )
    )
    assert normalized_anchor["block_id"] == ["P0001-T0001", "P0001-T0002"]
    assert superseded is True
    assert allowed_scope_ids == {"EV-0001"}

    normalized = test_generation_semantics.postprocess_source_semantics_item(
        context,
        {
            "item_input": prepared_items[0],
            "item_output": {
                "authoritative_facts": [
                    {
                        "fact_id": "F-SPAN",
                        "assertion": "次数由后台配置",
                        "scope_id": "EV-0001",
                        "source_anchor": {
                            "document_id": 9,
                            "page_number": 1,
                            "source_span": {"start": 6, "end": 13},
                        },
                        "status": "effective",
                        "value_policy": "runtime_configured",
                        "governed_value_spans": [{"start": 6, "end": 8}],
                        "governed_by": [],
                    }
                ]
            },
        },
    )
    assert normalized["authoritative_facts"][0]["governed_values"] == ["次数"]
    assert "governed_value_spans" not in normalized["authoritative_facts"][0]

    policy_span_normalized = test_generation_semantics.postprocess_source_semantics_item(
        context,
        {
            "item_input": prepared_items[0],
            "item_output": {
                "authoritative_facts": [
                    {
                        "fact_id": "F-POLICY-SPAN",
                        "assertion": "次数由后台配置",
                        "scope_id": "EV-0001",
                        "source_anchor": {
                            "document_id": 9,
                            "page_number": 1,
                            "source_span": {"start": 6, "end": 13},
                        },
                        "status": "effective",
                        "value_policy": "runtime_configured",
                        "governed_value_spans": [{"start": 8, "end": 13}],
                        "governed_by": [],
                    }
                ]
            },
        },
    )
    assert policy_span_normalized["authoritative_facts"][0]["governed_values"] == [
        "由后台配置"
    ]

    aggregate_output = {
        "authoritative_facts": [
            {
                "fact_id": fact_id,
                "assertion": "固定规则",
                "scope_id": "EV-0001",
                "source_anchor": {
                    "document_id": 9,
                    "page_number": 1,
                    "source_span": {"start": 0, "end": 5},
                },
                "status": "effective",
                "value_policy": "exact",
                "governed_value_spans": [{"start": 0, "end": 2}],
                "governed_by": [],
            }
            for fact_id in ("F-EXACT-1", "F-EXACT-2")
        ]
        + [
            {
                "fact_id": "F-RUNTIME",
                "assertion": "次数由后台配置",
                "scope_id": "EV-0001",
                "source_anchor": {
                    "document_id": 9,
                    "page_number": 1,
                    "source_span": {"start": 6, "end": 13},
                },
                "status": "effective",
                "value_policy": "runtime_configured",
                "governed_value_spans": [],
                "governed_by": [],
            }
        ]
    }
    exact_spans_normalized = test_generation_semantics.postprocess_source_semantics_item(
        context,
        {"item_input": prepared_items[0], "item_output": aggregate_output},
    )
    assert [
        fact["governed_values"]
        for fact in exact_spans_normalized["authoritative_facts"][:2]
    ] == [[], []]
    assert [
        fact["source_anchor"]["source_span"]
        for fact in exact_spans_normalized["authoritative_facts"][:2]
    ] == [{"start": 0, "end": 5}, {"start": 0, "end": 5}]

    split_anchor, _, _ = test_generation_semantics._validated_document_anchor(
        {
            "document_id": 9,
            "page_number": 1,
            "block_id": ["P0001-T0001", "P0001-T0002"],
            "source_span": {"start": 0, "end": len(page_text)},
            "quote": page_text.split("\n"),
            "asset_source_sha256": "a" * 64,
            "page_image_sha256": "b" * 64,
        },
        prepared_items[0],
    )
    assert split_anchor["quote"] == page_text
    assert split_anchor["block_id"] == ["P0001-T0001", "P0001-T0002"]

    compact_anchor, _, _ = test_generation_semantics._validated_document_anchor(
        {
            "document_id": 9,
            "page_number": 1,
            "source_span": {
                "start": 0,
                "end": len(page_text),
            },
        },
        prepared_items[0],
    )
    assert compact_anchor["quote"] == page_text
    assert compact_anchor["block_id"] == ["P0001-T0001", "P0001-T0002"]
    assert compact_anchor["asset_source_sha256"] == "a" * 64
    assert compact_anchor["page_image_sha256"] == "b" * 64


    recovered_anchor, _, _ = test_generation_semantics._validated_document_anchor(
        {
            "document_id": 9,
            "page_number": 1,
            "source_span": {"start": 6, "end": 6},
            "quote": page_text[6:13],
        },
        prepared_items[0],
    )
    assert recovered_anchor["source_span"] == {"start": 6, "end": 13}
    assert recovered_anchor["quote"] == page_text[6:13]

    expanded_anchor = test_generation_semantics._expanded_anchor_for_governed_values(
        {
            "document_id": 9,
            "page_number": 1,
            "block_id": "P0001-T0001",
            "source_span": {"start": 0, "end": 5},
            "quote": page_text[:5],
        },
        fact={"governed_value_spans": [{"start": 6, "end": 8}]},
        prepared=prepared_items[0],
    )
    assert expanded_anchor["source_span"] == {"start": 0, "end": 8}
    assert expanded_anchor["block_id"] == ["P0001-T0001", "P0001-T0002"]
    assert expanded_anchor["quote"] == page_text[:8]

    compact_span_normalized = test_generation_semantics.postprocess_source_semantics_item(
        context,
        {
            "item_input": prepared_items[0],
            "item_output": {
                "authoritative_facts": [
                    {
                        "fact_id": "F-COMPACT-SPAN",
                        "assertion": "次数由后台配置",
                        "source_anchor": {
                            "document_id": 9,
                            "page_number": 1,
                            "block_id": "P0001-T0001",
                        },
                        "status": "effective",
                        "value_policy": "runtime_configured",
                        "governed_value_spans": [{"start": 6, "end": 8}],
                        "governed_by": [],
                    }
                ]
            },
        },
    )
    compact_fact = compact_span_normalized["authoritative_facts"][0]
    assert compact_fact["source_anchor"]["source_span"] == {"start": 0, "end": 8}
    assert compact_fact["source_anchor"]["block_id"] == [
        "P0001-T0001",
        "P0001-T0002",
    ]
    assert compact_fact["governed_values"] == ["次数"]


    with pytest.raises(ValueError, match="坐标无效"):
        test_generation_semantics._validated_document_anchor(
            {
                "document_id": 9,
                "page_number": 1,
                "source_span": {"start": 6, "end": 6},
                "quote": page_text[:5],
            },
            prepared_items[0],
        )

    with pytest.raises(ValueError, match="超出页面正文长度.*page_text_length"):
        test_generation_semantics._validated_document_anchor(
            {
                "document_id": 9,
                "page_number": 1,
                "source_span": {
                    "start": len(page_text),
                    "end": len(page_text) + 1,
                },
            },
            prepared_items[0],
        )

    with pytest.raises(ValueError, match="未完整覆盖来源坐标"):
        test_generation_semantics._validated_document_anchor(
            {
                "document_id": 9,
                "page_number": 1,
                "block_id": ["P0001-T0001", "P0001-T0002"],
                "source_span": {"start": 0, "end": len(page_text)},
                "quote": [page_text.split("\n")[0]],
                "asset_source_sha256": "a" * 64,
                "page_image_sha256": "b" * 64,
            },
            prepared_items[0],
        )

    single_anchor, _, _ = test_generation_semantics._validated_document_anchor(
        {
            "document_id": 9,
            "page_number": 1,
            "block_id": ["P0001-T0001"],
            "source_span": {"start": 0, "end": 5},
            "quote": [page_text[:5]],
            "asset_source_sha256": "a" * 64,
            "page_image_sha256": "b" * 64,
        },
        prepared_items[0],
    )
    assert single_anchor["block_id"] == "P0001-T0001"
    assert "block_ids" not in single_anchor

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
    facts[0]["source_anchor"].pop("source_kind")
    merged = test_generation_semantics.merge_source_semantics(
        context,
        {
                    "semantic_inputs": [
                        *prepared["text_items"],
                        *prepared["vision_items"],
                    ],
            "semantic_records": [{"item_index": 0, "output": {"authoritative_facts": facts}}],
        },
    )
    assert [fact["status"] for fact in merged["authoritative_facts"]] == [
        "superseded",
        "effective",
    ]
    assert [fact["fact_id"] for fact in merged["effective_facts"]] == ["DOC9-P0001-F-002"]
    assert merged["authoritative_facts"][0]["source_anchor"]["source_kind"] == "document"
    assert merged["planning_scopes"] == [{
        "scope_id": "EV-0001",
        "facts": [{
            "fact_id": "DOC9-P0001-F-002",
            "assertion": "次数由后台配置",
            "value_policy": "runtime_configured",
            "governed_values": ["次数"],
            "governed_by": [{"relation": "invalidates", "directive_fact_id": "DOC9-P0001-F-001"}],
        }],
    }]


def test_compressed_document_semantics_maps_local_governed_span_to_original_page(
    monkeypatch,
) -> None:
    """压缩视图裁掉首块后，动态值坐标仍按原页事实源校验。"""

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
    context = SimpleNamespace(
        artifacts={},
        run_input={
            "enable_context_compression": True,
            "context_compression_max_tokens": 128,
        },
    )
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
                        # 模型视图只需送入第二个真实块。
                        "block_ids": ["P0001-T0002"],
                        "source_offset_start": 6,
                        "source_offset_end": len(page_text),
                        "text": page_text[6:],
                    }
                ],
            },
        },
    )

    compressed_item = prepared["vision_items"][0]
    assert [block["block_id"] for block in compressed_item["blocks"]] == [
        "P0001-T0002"
    ]
    assert compressed_item["page_text"] == "次数由后台配置"
    assert compressed_item["source_scopes"] == [
        {
            "scope_id": "EV-0001",
            "allowed_block_ids": ["P0001-T0002"],
            "source_span": {"start": 0, "end": 7},
        }
    ]
    assert context.artifacts["source_semantics_source_pages"]["9:1"]["blocks"]
    assert context.artifacts["source_semantics_source_pages"]["9:1"]["source_scopes"]
    assert context.artifacts["source_semantics_coordinate_maps"]["9:1"] == [
        {
            "block_id": "P0001-T0002",
            "local_span": {"start": 0, "end": 7},
            "original_span": {"start": 6, "end": 13},
        }
    ]

    normalized = test_generation_semantics.postprocess_source_semantics_item(
        context,
        {
            "item_input": compressed_item,
            "item_output": {
                "authoritative_facts": [
                    {
                        "fact_id": "F-LOCAL",
                        "assertion": "次数由后台配置",
                        "source_anchor": {
                            "document_id": 9,
                            "page_number": 1,
                            "block_id": "P0001-T0002",
                        },
                        "status": "effective",
                        "value_policy": "runtime_configured",
                        # 模型按压缩页正文返回局部坐标 0..2。
                        "governed_value_spans": [{"start": 0, "end": 2}],
                        "governed_by": [],
                    }
                ]
            },
        },
    )
    fact = normalized["authoritative_facts"][0]
    assert fact["source_anchor"]["source_span"] == {"start": 6, "end": 13}
    assert fact["governed_values"] == ["次数"]


def test_compressed_page_view_rejects_unknown_block_instead_of_using_first_block() -> None:
    with pytest.raises(ValueError, match="不存在的页面块"):
        test_generation_semantics._compressed_page_view(
            page_input={
                "page_number": 1,
                "page_text": "真实页面正文",
                "blocks": [
                    {
                        "block_id": "P0001-T0001",
                        "text": "真实页面正文",
                        "source_span": {"start": 0, "end": 6},
                    }
                ],
                "marks": [],
                "strikeout_spans": [],
            },
            page_scopes=[
                {
                    "evidence_id": "EV-0001",
                    "block_ids": ["P0001-T9999"],
                    "source_offset_start": 0,
                    "source_offset_end": 6,
                }
            ],
        )


def test_fragmented_page_drops_value_span_outside_selected_fact_block() -> None:
    page_text = "页首规则\n五年级500字"
    context = SimpleNamespace(
        artifacts={
            "source_semantics_source_pages": {
                "9:1": {
                    "source_kind": "document",
                    "document_id": 9,
                    "page_number": 1,
                    "page_text": page_text,
                    "blocks": [
                        {
                            "block_id": "P0001-T0001",
                            "text": "页首规则",
                            "source_span": {"start": 0, "end": 4},
                        },
                        {
                            "block_id": "P0001-T0002",
                            "text": "五年级500字",
                            "source_span": {"start": 5, "end": len(page_text)},
                        },
                    ],
                    "asset_source_sha256": "a" * 64,
                    "page_image_sha256": "b" * 64,
                    "marks": [],
                    "strikeout_spans": [],
                    "source_scopes": [
                        {
                            "scope_id": "EV-0001",
                            "allowed_block_ids": ["P0001-T0001", "P0001-T0002"],
                            "source_span": {"start": 0, "end": len(page_text)},
                        }
                    ],
                }
            },
            "source_semantics_coordinate_maps": {
                "9:1": [
                    {
                        "block_id": "P0001-T0001",
                        "local_span": {"start": 0, "end": 4},
                        "original_span": {"start": 0, "end": 4},
                    },
                        {
                            "block_id": "P0001-T0002",
                            "local_span": {"start": 5, "end": len(page_text)},
                            "original_span": {"start": 5, "end": len(page_text)},
                    },
                ]
            },
        }
    )
    item_input = {
        "source_kind": "document_batch",
        "document_id": 9,
        "pages": [
            {
                "source_kind": "document",
                "document_id": 9,
                "page_number": 1,
                "page_text": page_text,
                "blocks": context.artifacts["source_semantics_source_pages"]["9:1"]["blocks"],
                "asset_source_sha256": "a" * 64,
                "page_image_sha256": "b" * 64,
                "marks": [],
                "strikeout_spans": [],
                "source_scopes": [
                    {
                        "scope_id": "EV-0001",
                        "allowed_block_ids": ["P0001-T0002"],
                        "source_span": {"start": 5, "end": 11},
                    }
                ],
            }
        ],
    }

    normalized = test_generation_semantics.postprocess_source_semantics_item(
        context,
        {
            "item_input": item_input,
            "item_output": {
                "authoritative_facts": [
                    {
                        "fact_id": "F-LOCAL",
                        "assertion": "五年级字数要求为500字",
                        "source_anchor": {
                            "document_id": 9,
                            "page_number": 1,
                            "block_id": "P0001-T0002",
                        },
                        "status": "effective",
                        "value_policy": "runtime_configured",
                        # 0..2 落在页首块，不属于当前事实块，不能当作整页坐标。
                        "governed_value_spans": [{"start": 0, "end": 2}],
                        "governed_by": [],
                    }
                ]
            },
        },
    )

    fact = normalized["authoritative_facts"][0]
    assert fact["source_anchor"]["block_id"] == "P0001-T0002"
    assert fact["source_anchor"]["source_span"] == {
        "start": 5,
        "end": len(page_text),
    }
    assert fact["governed_values"] == []


def test_source_semantics_drops_cross_field_enum_used_as_governance_relation() -> None:
    requirement = "提交后进入审核中"
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
    item_input = prepared["text_items"][0]
    item_output = {
        "authoritative_facts": [
            {
                "fact_id": "F-001",
                "assertion": requirement,
                "source_anchor": {
                    "source_offset_start": 0,
                    "source_offset_end": len(requirement),
                },
                "status": "effective",
                "value_policy": "exact",
                "governed_value_spans": [],
                "governed_by": [
                    {
                        "relation": "reference_only",
                        "directive_fact_id": "F-002",
                    }
                ],
            },
            {
                "fact_id": "F-002",
                "assertion": requirement,
                "source_anchor": {
                    "source_offset_start": 0,
                    "source_offset_end": len(requirement),
                },
                "status": "effective",
                "value_policy": "exact",
                "governed_value_spans": [],
                "governed_by": [],
            },
        ]
    }

    validate(item_output, SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA)
    normalized = test_generation_semantics.postprocess_source_semantics_item(
        context,
        {"item_input": item_input, "item_output": item_output},
    )

    assert normalized["authoritative_facts"][0]["governed_by"] == []


def test_merge_source_semantics_namespaces_parallel_page_fact_ids() -> None:
    def prepared_item(page_number: int, scope_id: str, text: str) -> dict:
        block_id = f"P{page_number:04d}-T0001"
        return {
            "source_kind": "document",
            "document_id": 243,
            "page_number": page_number,
            "page_text": text,
            "blocks": [
                {
                    "block_id": block_id,
                    "text": text,
                    "source_span": {"start": 0, "end": len(text)},
                }
            ],
            "asset_source_sha256": "a" * 64,
            "page_image_sha256": "b" * 64,
            "marks": [],
            "strikeout_spans": [],
            "source_scopes": [
                {
                    "scope_id": scope_id,
                    "allowed_block_ids": [block_id],
                    "source_span": {"start": 0, "end": len(text)},
                }
            ],
        }

    def fact(item: dict, fact_id: str, assertion: str, governed_by: list[dict] | None = None) -> dict:
        return {
            "fact_id": fact_id,
            "assertion": assertion,
            "scope_id": item["source_scopes"][0]["scope_id"],
            "source_anchor": {
                "document_id": item["document_id"],
                "page_number": item["page_number"],
                "source_span": {"start": 0, "end": len(assertion)},
                "quote": assertion,
                "asset_source_sha256": item["asset_source_sha256"],
                "page_image_sha256": item["page_image_sha256"],
            },
            "status": "effective",
            "value_policy": "exact",
            "governed_values": [],
            "governed_by": governed_by or [],
        }

    first = prepared_item(1, "EV-0001", "第一页规则")
    second = prepared_item(2, "EV-0002", "第二页规则")
    second_page_fact = fact(second, "F001", "第二页规则")
    # 模型即使串写相邻页面 scope，平台也只接受真实锚点派生的作用域。
    second_page_fact["scope_id"] = "EV-0001"
    merged = test_generation_semantics.merge_source_semantics(
        SimpleNamespace(artifacts={}),
        {
            "semantic_inputs": [first, second],
            "semantic_records": [
                {
                    "item_index": 0,
                    "output": {"authoritative_facts": [fact(first, "F001", "第一页规则")]},
                },
                {
                    "item_index": 1,
                    "output": {
                        "authoritative_facts": [
                            second_page_fact,
                            fact(
                                second,
                                "F002",
                                "第二页规则",
                                [{"relation": "invalidates", "directive_fact_id": "F001"}],
                            ),
                        ]
                    },
                },
            ],
        },
    )
    assert [fact["fact_id"] for fact in merged["authoritative_facts"]] == [
        "DOC243-P0001-F001",
        "DOC243-P0002-F001",
        "DOC243-P0002-F002",
    ]
    assert merged["authoritative_facts"][2]["governed_by"] == [
        {"relation": "invalidates", "directive_fact_id": "DOC243-P0002-F001"}
    ]
    assert merged["authoritative_facts"][1]["scope_id"] == "EV-0002"


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
    assert "work_assignments" not in prepared["text_items"][0]
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
                    "semantic_inputs": [
                        *prepared["text_items"],
                        *prepared["vision_items"],
                    ],
                "semantic_records": [
                    {"item_index": 0, "output": {"authoritative_facts": [fact]}}
                ],
            },
        )


def test_source_semantics_rejects_duplicate_catalog_ids_before_model_call() -> None:
    requirement = "提交后进入审核中"
    requirement_hash = hashlib.sha256(requirement.encode("utf-8")).hexdigest()

    with pytest.raises(ValueError, match="重复 evidence_id"):
        test_generation_semantics.prepare_source_semantics(
            SimpleNamespace(artifacts={}),
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
                        },
                        {
                            "evidence_id": "EV-0001",
                            "source_offset_start": 0,
                            "source_offset_end": len(requirement),
                        },
                    ],
                },
            },
        )


def test_merge_source_semantics_rejects_duplicate_fact_ids_on_same_page() -> None:
    text = "页面规则"
    prepared = {
        "source_kind": "document",
        "document_id": 243,
        "page_number": 1,
        "page_text": text,
        "blocks": [{"block_id": "P0001-T0001", "text": text, "source_span": {"start": 0, "end": len(text)}}],
        "asset_source_sha256": "a" * 64,
        "page_image_sha256": "b" * 64,
        "marks": [],
        "strikeout_spans": [],
        "source_scopes": [{
            "scope_id": "EV-0001",
            "allowed_block_ids": ["P0001-T0001"],
            "source_span": {"start": 0, "end": len(text)},
        }],
    }
    raw_fact = {
        "fact_id": "F001",
        "assertion": text,
        "scope_id": "EV-0001",
        "source_anchor": {
            "document_id": 243,
            "page_number": 1,
            "source_span": {"start": 0, "end": len(text)},
            "quote": text,
        },
        "status": "effective",
        "value_policy": "exact",
        "governed_values": [],
        "governed_by": [],
    }
    with pytest.raises(ValueError, match="当前来源内 authoritative fact_id 重复"):
        test_generation_semantics.merge_source_semantics(
            SimpleNamespace(artifacts={}),
            {
                "semantic_inputs": [prepared],
                "semantic_records": [{
                    "item_index": 0,
                    "output": {"authoritative_facts": [raw_fact, dict(raw_fact)]},
                }],
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
                "semantic_inputs": [
                    *prepared["text_items"],
                    *prepared["vision_items"],
                ],
                "semantic_records": [
                    {"item_index": 0, "output": {"authoritative_facts": [fact]}}
                ],
            },
        )


def test_governance_config_phrase_does_not_require_runtime_coverage() -> None:
    requirement = "商品价格和赠送数量以最终后台配置为准"
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
    prepared_input = prepared["text_items"][0]
    assert "dynamic_value_declarations" not in prepared_input
    validate(instance=prepared_input, schema=SOURCE_SEMANTICS_INPUT_SCHEMA)
    exact_fact = {
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
        "value_policy": "exact",
        "governed_values": [],
        "governed_by": [],
    }
    arguments = {
        "semantic_inputs": [
            *prepared["text_items"],
            *prepared["vision_items"],
        ],
        "semantic_records": [
            {"item_index": 0, "output": {"authoritative_facts": [exact_fact]}}
        ],
    }

    merged = test_generation_semantics.merge_source_semantics(context, arguments)

    assert merged["effective_facts"][0]["value_policy"] == "exact"


def test_explicit_runtime_value_signal_is_not_deterministically_classified() -> None:
    requirement = "数量由后台配置决定"
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
    prepared_input = prepared["text_items"][0]
    assert "dynamic_value_declarations" not in prepared_input
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
        "value_policy": "exact",
        "governed_values": [],
        "governed_by": [],
    }

    merged = test_generation_semantics.merge_source_semantics(
        context,
        {
            "semantic_inputs": [prepared_input],
            "semantic_records": [
                {"item_index": 0, "output": {"authoritative_facts": [fact]}}
            ],
        },
    )

    assert merged["effective_facts"][0]["value_policy"] == "exact"


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
        _reconciliation_fact("F-NEW", page_number=2, assertion="新规则替代旧规则：未上传时点击按钮显示提示"),
        _reconciliation_fact("F-ONE", page_number=3, assertion="单页规则"),
    ]
    prepared = test_generation_semantics.prepare_authority_reconciliation(
        context,
        {
            "plan": {
                "business_modules": [
                    {
                        "name": "模块A",
                        "evidence_ids": ["EV-0001", "EV-0002"],
                        "fact_ids": ["F-OLD", "F-NEW"],
                    },
                    {
                        "name": "模块B",
                        "evidence_ids": ["EV-0003"],
                        "fact_ids": ["F-ONE"],
                    },
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


def test_merge_authority_reconciliation_accepts_sparse_changes_and_preserves_others() -> None:
    context = SimpleNamespace(artifacts={})
    old_fact = _reconciliation_fact(
        "F-OLD", page_number=1, assertion="未上传时按钮不可点击"
    )
    new_fact = _reconciliation_fact(
        "F-NEW", page_number=2, assertion="新规则替代旧规则"
    )
    merged = test_generation_semantics.merge_authority_reconciliation(
        context,
        {
            "authoritative_facts": [old_fact, new_fact],
            "prepared_items": [
                {
                    "module_index": 0,
                    "module": {"name": "模块A", "evidence_ids": ["EV-0001", "EV-0002"]},
                    "authoritative_facts": [old_fact, new_fact],
                }
            ],
            "reconciliation_records": [
                {
                    "item_index": 0,
                    "output": {
                        "decisions": [
                            {
                                "fact_id": "F-OLD",
                                "status": "superseded",
                                "governed_by": [
                                    {"relation": "replaces", "directive_fact_id": "F-NEW"}
                                ],
                                "reason": "后续事实明确替代旧规则。",
                            }
                        ]
                    },
                }
            ],
        },
    )

    assert [fact["fact_id"] for fact in merged["effective_facts"]] == ["F-NEW"]
    assert merged["authoritative_facts"][1] == new_fact


def test_authority_postprocessor_normalizes_passive_relation_from_real_run() -> None:
    old_fact = _reconciliation_fact(
        "DOC259-P0018-F0018-001",
        page_number=18,
        assertion="每个主题包含1篇范文",
    )
    new_fact = _reconciliation_fact(
        "DOC259-P0018-F0018-006",
        page_number=18,
        assertion="每个主题包含1篇或数篇范文",
    )
    model_output = {
        "decisions": [
            {
                "fact_id": "DOC259-P0018-F0018-001",
                "status": "superseded",
                "governed_by": [
                    {
                        "fact_id": "DOC259-P0018-F0018-006",
                        "relation": "superseded_by",
                    }
                ],
                "reason": "后续事实将数量范围扩展为1篇或数篇，替代原先仅1篇的说法。",
            }
        ]
    }
    validate(model_output, AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA)
    with pytest.raises(JsonSchemaValidationError):
        validate(model_output, AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA)
    invalid_structure = {
        "decisions": [
            {
                **model_output["decisions"][0],
                "governed_by": ["DOC259-P0018-F0018-006"],
            }
        ]
    }
    with pytest.raises(JsonSchemaValidationError):
        validate(invalid_structure, AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA)

    normalized = test_generation_semantics.postprocess_authority_reconciliation_item(
        SimpleNamespace(),
        {
            "item_input": {
                "module_index": 0,
                "module": {"name": "范文内容", "evidence_ids": ["EV-0018"]},
                "authoritative_facts": [old_fact, new_fact],
            },
            "item_output": model_output,
        },
    )

    assert normalized["decisions"][0]["governed_by"] == [
        {
            "directive_fact_id": "DOC259-P0018-F0018-006",
            "relation": "replaces",
        }
    ]
    validate(normalized, AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA)


def test_authority_item_postprocessor_ignores_noop_patch_before_merge() -> None:
    fact = _reconciliation_fact(
        "F-001",
        page_number=1,
        assertion="当前规则保持生效",
    )
    item_input = {
        "module_index": 0,
        "module": {"name": "模块A", "evidence_ids": ["EV-0001"]},
        "authoritative_facts": [fact],
    }

    assert test_generation_semantics.postprocess_authority_reconciliation_item(
        SimpleNamespace(),
        {"item_input": item_input, "item_output": {"decisions": []}},
    ) == {"decisions": []}

    assert test_generation_semantics.postprocess_authority_reconciliation_item(
        SimpleNamespace(),
        {
            "item_input": item_input,
            "item_output": {
                "decisions": [
                    {
                        "fact_id": "F-001",
                        "status": "effective",
                        "value_policy": "exact",
                        "governed_values": [],
                        "governed_by": [],
                        "reason": "保持当前规则。",
                    }
                ]
            },
        },
    ) == {"decisions": []}


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


def test_dense_text_page_is_fragmented_by_layout_blocks_without_fact_id_collision(
    monkeypatch,
) -> None:
    block_texts = [f"第{index:02d}块课程规则内容" for index in range(1, 46)]
    page_text = "\n".join(block_texts)
    blocks: list[dict] = []
    cursor = 0
    for index, text in enumerate(block_texts, start=1):
        blocks.append(
            {
                "block_id": f"P0001-T{index:04d}",
                "text": text,
                "source_span": {"start": cursor, "end": cursor + len(text)},
            }
        )
        cursor += len(text) + 1
    manifest = {
        "schema_version": 3,
        "document_id": 9,
        "source_sha256": "a" * 64,
        "page_count": 1,
        "pages": [
            {
                "page_number": 1,
                "image_sha256": "b" * 64,
                "blocks": blocks,
                "marks": [],
            }
        ],
    }
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
        lambda document_id, page_number: blocks,
    )
    context = SimpleNamespace(
        artifacts={},
        run_input={"enable_context_compression": True},
    )
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
                        "block_ids": [block["block_id"] for block in blocks],
                        "source_offset_start": 0,
                        "source_offset_end": len(page_text),
                        "text": page_text,
                    }
                ],
            },
        },
    )

    assert len(prepared["text_items"]) == 3
    assert prepared["vision_items"] == []
    assert context.artifacts["source_semantics_prepare"]["fragmented_page_count"] == 1
    assert context.artifacts["source_semantics_prepare"]["text_fragment_count"] == 3
    assert context.artifacts["source_semantics_prepare"]["max_text_fragment_blocks"] == 20
    for item in prepared["text_items"]:
        validate(instance=item, schema=SOURCE_SEMANTICS_INPUT_SCHEMA)
        assert len(item["pages"]) == 1
        assert len(item["pages"][0]["blocks"]) <= 20

    normalized_records = []
    for item_index, item in enumerate(prepared["text_items"][:2]):
        page = item["pages"][0]
        block = page["blocks"][0]
        output = {
            "authoritative_facts": [
                {
                    "fact_id": "F001",
                    "assertion": block["text"],
                    "source_anchor": {
                        "document_id": 9,
                        "page_number": 1,
                        "block_id": block["block_id"],
                    },
                    "status": "effective",
                    "value_policy": "exact",
                    "governed_value_spans": [],
                    "governed_by": [],
                }
            ]
        }
        validate(instance=output, schema=SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA)
        normalized_records.append(
            {
                "item_index": item_index,
                "output": test_generation_semantics.postprocess_source_semantics_item(
                    context,
                    {"item_input": item, "item_output": output},
                ),
            }
        )

    first_fact_id = normalized_records[0]["output"]["authoritative_facts"][0]["fact_id"]
    second_fact_id = normalized_records[1]["output"]["authoritative_facts"][0]["fact_id"]
    assert first_fact_id != second_fact_id
    assert "-S0-" in first_fact_id
    assert "-S" in second_fact_id

    merged = test_generation_semantics.merge_source_semantics(
        context,
        {
            "semantic_inputs": prepared["text_items"][:2],
            "semantic_records": normalized_records,
        },
    )
    assert merged["inspected_page_count"] == 1
