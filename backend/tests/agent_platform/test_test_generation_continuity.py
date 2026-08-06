from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from modules.knowledge_base_components.document import document_index_service
from modules.agent_platform.test_generation_batching import (
    _attach_high_confidence_continuations,
    _build_evidence_catalog_from_fragments,
    _stable_payload_hash,
)
from modules.agent_platform.test_generation_continuity import (
    MAX_CONTINUITY_AUDIT_ITEMS,
    merge_continuity_audit,
    prepare_continuity_audit_items,
)
from modules.knowledge_base_components.document.document_asset_service import (
    _page_text_with_source_spans,
    detect_high_confidence_page_continuations,
)
from modules.knowledge_base_components.document.document_index_service import (
    _source_preserving_page_chunks,
    build_document_asset_index_chunks,
)


BASE_PAGE = 73


def _evidence_id(position: int) -> str:
    return f"EV-{position:04d}"


def _plan() -> dict:
    return {
        "requirement_summary": "通用列表边界。",
        "business_modules": [
            {
                "name": f"模块{index}",
                "objective": f"验证第{index}类通用事实",
                "actors": ["角色"],
                "lifecycle": None,
            }
            for index in range(12)
        ],
        "coverage_focus": ["边界连续性"],
        "risks": ["列表截断"],
    }


def _marker(ordinal: int, block_id: str) -> dict:
    return {
        "kind": "arabic",
        "ordinal": ordinal,
        "raw": f"{ordinal}.",
        "suffix": ".",
        "block_id": block_id,
        "line_text": f"{ordinal}. 通用分项",
    }


def _layout_block(block_id: str, text: str, y: float) -> dict:
    return {
        "block_id": block_id,
        "type": "text_line",
        "text": text,
        "bbox": {"x": 0.12, "y": y, "width": 0.4, "height": 0.02},
        "source_bbox": {"x0": 72, "top": y * 800, "x1": 300, "bottom": y * 800 + 12},
        "indent": {"x": 72, "normalized_x": 0.12},
        "font_name": "GenericSans",
        "font_size": 11.0,
        "source": "pdf_text",
    }


def test_catalog_attaches_only_whole_right_item_continuation() -> None:
    left_text = "通用引导\n1. 分项甲\n2. 分项乙"
    right_text = "3. 分项丙\n4. 分项丁"
    common = {
        "document_id": 903,
        "biz_key": "",
        "asset_source_sha256": "f" * 64,
    }
    fragments = [
        {
            **common,
            "chunk_index": 0,
            "text": left_text,
            "page_number": BASE_PAGE,
            "block_ids": ["left-intro", "left-one", "left-two"],
            "source_offset_start": 0,
            "source_offset_end": len(left_text),
        },
        {
            **common,
            "chunk_index": 1,
            "text": right_text,
            "page_number": BASE_PAGE + 1,
            "block_ids": ["right-three", "right-four"],
            "source_offset_start": 0,
            "source_offset_end": len(right_text),
        },
    ]
    manifest = {
        "pages": [
            {
                "page_number": BASE_PAGE,
                "blocks": [
                    _layout_block("left-intro", "通用引导", 0.61),
                    _layout_block("left-one", "1. 分项甲", 0.66),
                    _layout_block("left-two", "2. 分项乙", 0.72),
                ],
            },
            {
                "page_number": BASE_PAGE + 1,
                "blocks": [
                    _layout_block("right-three", "3. 分项丙", 0.08),
                    _layout_block("right-four", "4. 分项丁", 0.13),
                ],
            },
        ]
    }
    assert _page_text_with_source_spans(manifest["pages"][0]["blocks"]) == left_text
    assert _page_text_with_source_spans(manifest["pages"][1]["blocks"]) == right_text

    catalog = _build_evidence_catalog_from_fragments(fragments, manifest=manifest)

    assert len(catalog) == 2
    assert catalog[1]["text"] == "3. 分项丙\n4. 分项丁"
    assert catalog[1]["continuation"]["previous_evidence_id"] == catalog[0]["evidence_id"]
    assert catalog[1]["continuation"]["right_range"] == {
        "start": 0,
        "end": len(catalog[1]["text"]),
        "head_end": len(catalog[1]["text"]),
    }
    assert catalog[1]["block_ids"] == ["right-three", "right-four"]


def test_catalog_does_not_attach_partial_page_body_continuation() -> None:
    left_text = "2. 支持分项\n3. 边界分项"
    right_text = "上一项说明第一行\n上一项说明第二行\n4. 后续独立分项"
    manifest = {
        "pages": [
            {
                "page_number": BASE_PAGE,
                "blocks": [
                    _layout_block("left-two", "2. 支持分项", 0.66),
                    _layout_block("left-three", "3. 边界分项", 0.72),
                ],
            },
            {
                "page_number": BASE_PAGE + 1,
                "blocks": [
                    _layout_block("right-body-one", "上一项说明第一行", 0.05),
                    _layout_block("right-body-two", "上一项说明第二行", 0.08),
                    _layout_block("right-four", "4. 后续独立分项", 0.12),
                ],
            },
        ]
    }
    link = detect_high_confidence_page_continuations(manifest)[0]
    assert link["right_page_is_whole_item"] is False

    common = {
        "document_id": 904,
        "biz_key": "",
        "asset_source_sha256": "a" * 64,
    }
    catalog = _build_evidence_catalog_from_fragments(
        [
            {
                **common,
                "chunk_index": 0,
                "text": left_text,
                "page_number": BASE_PAGE,
                "block_ids": ["left-two", "left-three"],
                "source_offset_start": 0,
                "source_offset_end": len(left_text),
            },
            {
                **common,
                "chunk_index": 1,
                "text": right_text,
                "page_number": BASE_PAGE + 1,
                "block_ids": ["right-body-one", "right-body-two", "right-four"],
                "source_offset_start": 0,
                "source_offset_end": len(right_text),
            },
        ],
        manifest=manifest,
    )
    assert catalog[1]["text"] == right_text
    assert catalog[1]["continuation"] is None


def test_whole_right_item_with_multiple_persisted_chunks_is_blocked() -> None:
    manifest = {
        "pages": [
            {
                "page_number": BASE_PAGE,
                "blocks": [
                    _layout_block("left-one", "1. 分项甲", 0.66),
                    _layout_block("left-two", "2. 分项乙", 0.72),
                ],
            },
            {
                "page_number": BASE_PAGE + 1,
                "blocks": [
                    _layout_block("right-three", "3. 分项丙", 0.08),
                    _layout_block("right-four", "4. 分项丁", 0.13),
                ],
            },
        ]
    }
    catalog = [
        {
            "evidence_id": "EV-0001",
            "text": "1. 分项甲\n2. 分项乙",
            "page_number": BASE_PAGE,
            "source_offset_start": 0,
            "source_offset_end": 15,
            "block_ids": ["left-one", "left-two"],
        },
        {
            "evidence_id": "EV-0002",
            "text": "3. 分项丙",
            "page_number": BASE_PAGE + 1,
            "source_offset_start": 0,
            "source_offset_end": 6,
            "block_ids": ["right-three"],
        },
        {
            "evidence_id": "EV-0003",
            "text": "4. 分项丁",
            "page_number": BASE_PAGE + 1,
            "source_offset_start": 7,
            "source_offset_end": 13,
            "block_ids": ["right-four"],
        },
    ]

    with pytest.raises(ValueError, match="唯一右页证据块"):
        _attach_high_confidence_continuations(catalog, manifest=manifest)


def test_layout_block_ids_have_exact_same_source_spans() -> None:
    blocks = [
        _layout_block("line-one", "第一行连续文本", 0.08),
        _layout_block("line-two", "第二行连续文本", 0.12),
        _layout_block("line-three", "第三行连续文本", 0.16),
    ]
    page_text = _page_text_with_source_spans(blocks)
    intervals = {
        block["block_id"]: (
            block["source_span"]["start"],
            block["source_span"]["end"],
        )
        for block in blocks
    }
    third_line_start = page_text.index("第三行")
    chunks = _source_preserving_page_chunks(
        page_text,
        start=0,
        end=third_line_start,
    ) + _source_preserving_page_chunks(page_text, start=third_line_start)
    chunk_block_ids = [
        [
            block_id
            for block_id, (block_start, block_end) in intervals.items()
            if block_start < end and block_end > start
        ]
        for _, start, end in chunks
    ]

    assert chunk_block_ids == [["line-one", "line-two"], ["line-three"]]
    assert set(chunk_block_ids[0]).isdisjoint(chunk_block_ids[1])
    assert all(
        page_text[start:end] == block["text"]
        for block, (start, end) in zip(blocks, intervals.values())
    )


def test_asset_index_does_not_truncate_more_than_100_block_ids(monkeypatch) -> None:
    blocks = [
        _layout_block(f"line-{index:03d}", f"第{index:03d}行连续文本", index / 200)
        for index in range(105)
    ]
    page_text = _page_text_with_source_spans(blocks)
    for index, block in enumerate(blocks):
        block["text_runs"] = [
            {
                "run_id": f"line-{index:03d}-R0001",
                "text": block["text"],
                "source_span": dict(block["source_span"]),
                "asset_source_sha256": "b" * 64,
            }
        ]
    manifest = {
        "schema_version": 3,
        "source_sha256": "b" * 64,
        "pages": [
            {
                "page_number": 1,
                "text_path": "pages/page-0001.txt",
                "blocks": blocks,
                "marks": [],
            }
        ],
    }
    monkeypatch.setattr(document_index_service, "document_page_text", lambda *_: page_text)
    monkeypatch.setattr(document_index_service, "document_page_layout", lambda *_: blocks)

    payloads, _, _ = build_document_asset_index_chunks(
        doc=SimpleNamespace(id=905),
        manifest=manifest,
    )

    assert len(payloads) == 1
    assert payloads[0]["metadata"]["layout_anchor_complete"] is True
    assert payloads[0]["metadata"]["layout_anchor_count"] == 105
    assert payloads[0]["metadata"]["block_ids"] == [
        f"line-{index:03d}" for index in range(105)
    ]


def _catalog(*, with_link: bool = True) -> dict:
    left_text = "通用说明\n8. 通用分项\n9. 通用分项"
    right_text = "10. 通用分项\n11. 通用分项"
    marker_start = left_text.index("9.")
    continuation = None
    if with_link:
        continuation = {
            "confidence": "high",
            "previous_evidence_id": _evidence_id(1),
            "left_tail_span": {"start": 0, "end": len(left_text)},
            "left_marker_span": {
                "start": marker_start,
                "end": marker_start + 2,
            },
            "minimum_governing_span": {
                "start": 0,
                "end": len(left_text),
            },
            "right_range": {
                "start": 0,
                "end": len(right_text),
                "head_end": len(right_text),
            },
            "left_marker": _marker(9, "left-marker"),
            "right_marker": _marker(10, "right-marker"),
            "support_markers": [_marker(8, "left-support")],
            "style": {
                "font_name": "GenericSans",
                "font_size": 11.0,
                "normalized_indent": 0.12,
            },
            "left_tail_block_ids": ["left-support", "left-marker"],
            "right_head_block_ids": ["right-marker", "right-support"],
            "right_continuation_block_ids": ["right-marker", "right-support"],
            "right_continuation_line_texts": [
                "10. 通用分项",
                "11. 通用分项",
            ],
        }
    items = []
    for position, (page_number, text, item_continuation) in enumerate(
        [
            (BASE_PAGE, left_text, None),
            (BASE_PAGE + 1, right_text, continuation),
        ],
        start=1,
    ):
        items.append(
            {
                "evidence_id": _evidence_id(position),
                "document_id": 901,
                "chunk_index": position,
                "biz_key": "",
                "text": text,
                "page_number": page_number,
                "block_ids": [f"block-{position}"],
                "source_offset_start": 0,
                "source_offset_end": len(text),
                "asset_source_sha256": "d" * 64,
                "continuation": item_continuation,
            }
        )
    return {"document_id": 901, "items": items}


def _routing() -> dict:
    return {
        "evidence_accounting": [
            {
                "evidence_id": _evidence_id(1),
                "module_indexes": [8, 10],
                "disposition": "assigned",
                "reason": "左项同时包含两类事实",
            },
            {
                "evidence_id": _evidence_id(2),
                "module_indexes": [8],
                "disposition": "assigned",
                "reason": "Reviewer 直接归属",
            },
        ]
    }


def _prepared() -> tuple[dict, list[dict]]:
    arguments = {
        "draft_plan": _plan(),
        "evidence_catalog": _catalog(),
        "routing": _routing(),
    }
    result = prepare_continuity_audit_items(
        SimpleNamespace(artifacts={}),
        arguments,
    )
    return arguments, result["items"]


def _output(prepared: dict) -> dict:
    minimum_span = prepared["structure"]["minimum_governing_span"]
    return {
        "previous_evidence_id": prepared["left_evidence"]["evidence_id"],
        "evidence_id": prepared["right_evidence"]["evidence_id"],
        "relation": "inherits_entire_item",
        "governing_scopes": [
            {
                "span": {
                    "start": minimum_span["start"],
                    "end": minimum_span["end"],
                },
                "module_indexes": [10],
            }
        ],
        "spans": [
            {"start": 0, "end": len(prepared["right_evidence"]["text"])}
        ],
        "reason": "右项整体延续左侧边界分项且只受一个模块支配",
    }


def _record(item_index: int, prepared: dict, output: dict) -> dict:
    return {
        "item_index": item_index,
        "input_hash": _stable_payload_hash(prepared),
        "output": output,
    }


def test_continuity_merge_can_inherit_only_a_subset_of_left_accounting() -> None:
    arguments, prepared = _prepared()
    merged = merge_continuity_audit(
        SimpleNamespace(artifacts={}),
        {
            **arguments,
            "prepared_items": prepared,
            "continuity_records": [_record(0, prepared[0], _output(prepared[0]))],
        },
    )

    assert merged["evidence_accounting"][0]["module_indexes"] == [8, 10]
    assert merged["evidence_accounting"][1]["module_indexes"] == [10]


def test_continuity_empty_links_skip_agent_map_without_changing_routing() -> None:
    arguments = {
        "draft_plan": _plan(),
        "evidence_catalog": _catalog(with_link=False),
        "routing": _routing(),
    }
    prepared = prepare_continuity_audit_items(
        SimpleNamespace(artifacts={}),
        arguments,
    )
    assert prepared == {"items": [], "link_count": 0}

    merged = merge_continuity_audit(
        SimpleNamespace(artifacts={}),
        {
            **arguments,
            "prepared_items": [],
            "continuity_records": [],
        },
    )
    assert merged == _routing()


def test_continuity_independent_relation_does_not_change_routing() -> None:
    arguments, prepared = _prepared()
    output = _output(prepared[0])
    output.update(
        {
            "relation": "independent",
            "governing_scopes": [],
            "spans": [],
            "reason": "右项是独立事实，不受左侧列表治理",
        }
    )

    merged = merge_continuity_audit(
        SimpleNamespace(artifacts={}),
        {
            **arguments,
            "prepared_items": prepared,
            "continuity_records": [_record(0, prepared[0], output)],
        },
    )

    assert merged == _routing()


def test_prepare_continuity_rejects_items_over_agent_map_limit() -> None:
    text = "引导行\n1. 通用分项\n2. 通用分项"
    items: list[dict] = []
    accounting: list[dict] = []
    for position in range(1, MAX_CONTINUITY_AUDIT_ITEMS + 3):
        continuation = None
        if position > 1:
            continuation = deepcopy(_catalog()["items"][1]["continuation"])
            continuation["previous_evidence_id"] = _evidence_id(position - 1)
            continuation["left_tail_span"] = {"start": 0, "end": len(text)}
            continuation["minimum_governing_span"] = {
                "start": 0,
                "end": len(text),
            }
            continuation["right_range"] = {
                "start": 0,
                "end": len(text),
                "head_end": len(text),
            }
        items.append(
            {
                "evidence_id": _evidence_id(position),
                "document_id": 902,
                "chunk_index": position,
                "biz_key": "",
                "text": text,
                "page_number": BASE_PAGE + position,
                "block_ids": [f"block-{position}"],
                "source_offset_start": 0,
                "source_offset_end": len(text),
                "asset_source_sha256": "e" * 64,
                "continuation": continuation,
            }
        )
        accounting.append(
            {
                "evidence_id": _evidence_id(position),
                "module_indexes": [0],
                "disposition": "assigned",
                "reason": "通用证据归属",
            }
        )

    with pytest.raises(ValueError, match="超过 agent_map 上限"):
        prepare_continuity_audit_items(
            SimpleNamespace(artifacts={}),
            {
                "draft_plan": _plan(),
                "evidence_catalog": {"document_id": 902, "items": items},
                "routing": {"evidence_accounting": accounting},
            },
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("hash", "输入指纹不一致"),
        ("id", "错误证据 ID"),
        ("right_span", "精确覆盖右项全文"),
        ("module_out_of_range", "非空有序有效下标"),
        ("not_subset", "左项已记账模块的子集"),
        ("left_span_outside", "left_tail_span 全局坐标内"),
        ("left_span_misses_minimum", "完整覆盖 minimum_governing_span"),
    ],
)
def test_continuity_merge_rejects_invalid_contracts(
    mutation: str,
    message: str,
) -> None:
    arguments, prepared = _prepared()
    output = _output(prepared[0])
    record = _record(0, prepared[0], output)
    if mutation == "hash":
        record["input_hash"] = "0" * 64
    elif mutation == "id":
        output["evidence_id"] = _evidence_id(99)
    elif mutation == "right_span":
        output["spans"][0]["end"] -= 1
    elif mutation == "module_out_of_range":
        output["governing_scopes"][0]["module_indexes"] = [12]
    elif mutation == "not_subset":
        output["governing_scopes"][0]["module_indexes"] = [9]
    elif mutation == "left_span_outside":
        output["governing_scopes"][0]["span"] = {"start": 0, "end": 1}
        prepared[0]["structure"]["left_tail_span"]["start"] = 2
        prepared[0]["structure"]["minimum_governing_span"]["start"] = 2
        arguments["evidence_catalog"]["items"][1]["continuation"]["left_tail_span"]["start"] = 2
        arguments["evidence_catalog"]["items"][1]["continuation"]["minimum_governing_span"]["start"] = 2
        prepared[0]["left_evidence"]["tail_span"]["start"] = 2
        prepared[0]["left_evidence"]["tail_text"] = arguments["evidence_catalog"]["items"][0]["text"][2:]
        record = _record(0, prepared[0], output)
    elif mutation == "left_span_misses_minimum":
        output["governing_scopes"][0]["span"] = {
            "start": 0,
            "end": len(arguments["evidence_catalog"]["items"][0]["text"]) - 1,
        }

    with pytest.raises(ValueError, match=message):
        merge_continuity_audit(
            SimpleNamespace(artifacts={}),
            {
                **arguments,
                "prepared_items": prepared,
                "continuity_records": [record],
            },
        )


@pytest.mark.parametrize(
    ("relation", "message"),
    [
        ("inherits_leading_span", "必须先在源头重新分块"),
        ("uncertain", "结果不确定"),
    ],
)
def test_continuity_merge_blocks_partial_or_uncertain_relation(
    relation: str,
    message: str,
) -> None:
    arguments, prepared = _prepared()
    output = _output(prepared[0])
    output["relation"] = relation
    if relation == "inherits_leading_span":
        output["spans"] = [{"start": 0, "end": 2}]
    else:
        output["governing_scopes"] = []
        output["spans"] = []

    with pytest.raises(ValueError, match=message):
        merge_continuity_audit(
            SimpleNamespace(artifacts={}),
            {
                **arguments,
                "prepared_items": prepared,
                "continuity_records": [_record(0, prepared[0], output)],
            },
        )
