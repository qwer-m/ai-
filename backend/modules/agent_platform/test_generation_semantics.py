"""测试生成工作流的统一来源语义提取与确定性归并。"""

from __future__ import annotations

import hashlib
from typing import Any, TYPE_CHECKING

from modules.knowledge_base_components.document.document_asset_service import (
    document_page_layout,
    document_page_text,
    load_document_manifest,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext


_DOCUMENT_SCHEMA_VERSION = 3
_FACT_STATUSES = {
    "effective",
    "superseded",
    "non_final",
    "reference_only",
    "uncertain",
}
_GOVERNANCE_RELATIONS = {"replaces", "invalidates", "limits", "parameterizes"}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _span(value: Any, *, field_name: str, allow_empty: bool = False) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    start = int(value.get("start", -1))
    end = int(value.get("end", -1))
    if start < 0 or end < start or (not allow_empty and end == start):
        raise ValueError(f"{field_name} 坐标无效: start={start}, end={end}")
    return {"start": start, "end": end}


def _overlaps(left: dict[str, int], right: dict[str, int]) -> bool:
    return left["start"] < right["end"] and right["start"] < left["end"]


def _strikeout_spans(manifest: dict[str, Any], page: dict[str, Any]) -> list[dict[str, Any]]:
    """只读取 manifest v3 明示的删除线标记，不根据文字或样式猜测。"""

    if int(manifest.get("schema_version") or 0) != _DOCUMENT_SCHEMA_VERSION:
        raise ValueError("删除线事实判定只接受 manifest v3")
    page_number = int(page.get("page_number") or 0)
    source_sha256 = str(manifest.get("source_sha256") or "").strip().lower()
    blocks_by_id = {
        str(block.get("block_id") or ""): dict(block)
        for block in list(page.get("blocks") or [])
        if isinstance(block, dict) and str(block.get("block_id") or "")
    }
    raw_marks = [*list(manifest.get("marks") or []), *list(page.get("marks") or [])]
    result: list[dict[str, Any]] = []
    for raw_mark in raw_marks:
        if not isinstance(raw_mark, dict):
            raise ValueError(f"manifest marks 只能包含对象: page_number={page_number}")
        kind = str(raw_mark.get("type") or "").strip().casefold()
        if kind != "strikeout":
            continue
        mark_page = int(raw_mark.get("page_number") or page_number)
        if mark_page != page_number:
            continue
        if str(raw_mark.get("asset_source_sha256") or "").strip().lower() != source_sha256:
            raise ValueError(f"删除线标记资产指纹不一致: page_number={page_number}")
        raw_block_ids = raw_mark.get("target_block_ids")
        raw_spans = raw_mark.get("target_source_spans")
        if not isinstance(raw_block_ids, list) or not raw_block_ids:
            raise ValueError(f"删除线标记缺少 target_block_ids: page_number={page_number}")
        if not isinstance(raw_spans, list) or not raw_spans:
            raise ValueError(f"删除线标记缺少 target_source_spans: page_number={page_number}")
        block_ids = [_required_text(item, "strikeout.target_block_ids") for item in raw_block_ids]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError(f"删除线标记 target_block_ids 重复: page_number={page_number}")
        unknown_blocks = set(block_ids) - set(blocks_by_id)
        if unknown_blocks:
            raise ValueError(
                f"删除线标记引用未知页面块: page_number={page_number}, blocks={sorted(unknown_blocks)}"
            )
        covered_blocks: set[str] = set()
        for raw_span in raw_spans:
            mark_span = _span(raw_span, field_name="strikeout.target_source_spans")
            matched = False
            for block_id in block_ids:
                block_span = _span(
                    blocks_by_id[block_id].get("source_span"),
                    field_name=f"{block_id}.source_span",
                )
                if not _overlaps(mark_span, block_span):
                    continue
                matched = True
                covered_blocks.add(block_id)
                result.append(
                    {
                        "block_id": block_id,
                        "source_span": {
                            "start": max(mark_span["start"], block_span["start"]),
                            "end": min(mark_span["end"], block_span["end"]),
                        },
                    }
                )
            if not matched:
                raise ValueError(
                    f"删除线 target_source_span 未命中 target_block_ids: page_number={page_number}"
                )
        if covered_blocks != set(block_ids):
            raise ValueError(
                f"删除线 target_block_ids 未被 target_source_spans 完整覆盖: page_number={page_number}"
            )
    return result


def _normalized_marks(manifest: dict[str, Any], page: dict[str, Any]) -> list[dict[str, Any]]:
    """向 Agent 暴露 manifest v3 的通用标记，同时保持来源字段原样可追踪。"""

    if int(manifest.get("schema_version") or 0) != _DOCUMENT_SCHEMA_VERSION:
        raise ValueError("来源标记只接受 manifest v3")
    page_number = int(page.get("page_number") or 0)
    source_sha256 = str(manifest.get("source_sha256") or "").strip().lower()
    blocks_by_id = {
        str(block.get("block_id") or ""): dict(block)
        for block in list(page.get("blocks") or [])
        if isinstance(block, dict) and str(block.get("block_id") or "")
    }
    result: list[dict[str, Any]] = []
    for raw_mark in list(page.get("marks") or []):
        if not isinstance(raw_mark, dict):
            raise ValueError(f"manifest marks 只能包含对象: page_number={page_number}")
        mark_type = _required_text(raw_mark.get("type"), "mark.type")
        mark_source = _required_text(raw_mark.get("source"), "mark.source")
        mark_id = _required_text(raw_mark.get("mark_id"), "mark.mark_id")
        asset_hash = str(raw_mark.get("asset_source_sha256") or "").strip().lower()
        if asset_hash != source_sha256:
            raise ValueError(f"来源标记资产指纹不一致: mark_id={mark_id}")
        raw_target_blocks = raw_mark.get("target_block_ids")
        raw_target_spans = raw_mark.get("target_source_spans")
        if not isinstance(raw_target_blocks, list) or not raw_target_blocks:
            raise ValueError(f"来源标记缺少 target_block_ids: mark_id={mark_id}")
        if not isinstance(raw_target_spans, list) or not raw_target_spans:
            raise ValueError(f"来源标记缺少 target_source_spans: mark_id={mark_id}")
        target_blocks = [_required_text(item, "mark.target_block_ids") for item in raw_target_blocks]
        if set(target_blocks) - set(blocks_by_id):
            raise ValueError(f"来源标记引用未知 target_block_ids: mark_id={mark_id}")
        target_spans = [
            _span(item, field_name="mark.target_source_spans")
            for item in raw_target_spans
        ]
        covered_blocks: set[str] = set()
        for target_span in target_spans:
            if not any(
                _overlaps(
                    target_span,
                    _span(
                        blocks_by_id[block_id].get("source_span"),
                        field_name=f"{block_id}.source_span",
                    ),
                )
                for block_id in target_blocks
            ):
                raise ValueError(f"来源标记 target_source_span 未命中目标块: mark_id={mark_id}")
            covered_blocks.update(
                block_id
                for block_id in target_blocks
                if _overlaps(
                    target_span,
                    _span(
                        blocks_by_id[block_id].get("source_span"),
                        field_name=f"{block_id}.source_span",
                    ),
                )
            )
        if covered_blocks != set(target_blocks):
            raise ValueError(f"来源标记 target_block_ids 未被坐标完整覆盖: mark_id={mark_id}")
        mark = {
            "mark_id": mark_id,
            "type": mark_type,
            "source": mark_source,
            "bbox": dict(raw_mark.get("bbox") or {}),
            "target_block_ids": target_blocks,
            "target_source_spans": target_spans,
            "asset_source_sha256": asset_hash,
        }
        for field_name in ("annotation_subtype", "contents", "title"):
            if field_name in raw_mark:
                mark[field_name] = str(raw_mark.get(field_name) or "")
        result.append(mark)
    return result


def prepare_source_semantics(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """按真实文档页或纯文本来源各准备一次语义分析输入。"""

    requirement = _required_text(arguments.get("requirement"), "requirement")
    source = dict(arguments.get("evidence_source") or {})
    evidence_catalog = arguments.get("evidence_catalog")
    if not isinstance(evidence_catalog, dict) or not isinstance(
        evidence_catalog.get("items"), list
    ):
        raise ValueError("evidence_catalog 必须包含来源作用域清单")
    catalog_items = [dict(item) for item in evidence_catalog["items"]]
    source_kind = str(source.get("kind") or "").strip()
    if source_kind == "inline":
        requirement_sha256 = _sha256_text(requirement)
        if str(source.get("content_hash") or "").lower() != requirement_sha256:
            raise ValueError("纯文本需求指纹与真实输入不一致")
        items = [
            {
                "source_kind": "inline",
                "requirement": requirement,
                "requirement_sha256": requirement_sha256,
                "source_scopes": [
                    {
                        "scope_id": str(item.get("evidence_id") or ""),
                        "source_offset_start": int(item.get("source_offset_start") or 0),
                        "source_offset_end": int(item.get("source_offset_end") or 0),
                    }
                    for item in catalog_items
                ],
            }
        ]
    elif source_kind == "knowledge_document":
        document_id = int(source.get("document_id") or 0)
        if document_id < 1 or not bool(source.get("asset_available")):
            raise ValueError("文档来源缺少可读页面资产")
        manifest = load_document_manifest(document_id)
        schema_version = int(manifest.get("schema_version") or 0)
        if schema_version != _DOCUMENT_SCHEMA_VERSION:
            raise ValueError(
                f"source semantics 不支持当前文档资产版本: schema_version={schema_version}"
            )
        source_sha256 = str(manifest.get("source_sha256") or "").strip().lower()
        if source_sha256 != str(source.get("content_hash") or "").strip().lower():
            raise ValueError("文档资产与需求事实源指纹不一致")
        items = []
        seen_pages: set[int] = set()
        for raw_page in list(manifest.get("pages") or []):
            if not isinstance(raw_page, dict):
                raise ValueError("manifest.pages 只能包含对象")
            page = dict(raw_page)
            page_number = int(page.get("page_number") or 0)
            if page_number < 1 or page_number in seen_pages:
                raise ValueError(f"manifest 页码无效或重复: page_number={page_number}")
            seen_pages.add(page_number)
            page_image_sha256 = str(page.get("image_sha256") or "").strip().lower()
            if len(page_image_sha256) != 64:
                raise ValueError(f"页面图像缺少有效 SHA256: page_number={page_number}")
            page_text = document_page_text(document_id, page_number)
            blocks: list[dict[str, Any]] = []
            for raw_block in document_page_layout(document_id, page_number):
                if not isinstance(raw_block, dict):
                    raise ValueError(f"页面布局块必须是对象: page_number={page_number}")
                block = dict(raw_block)
                block_id = _required_text(block.get("block_id"), "block_id")
                source_span = block.get("source_span")
                if source_span is None:
                    continue
                normalized_span = _span(source_span, field_name=f"{block_id}.source_span")
                if normalized_span["end"] > len(page_text):
                    raise ValueError(f"页面布局块 source_span 越界: block_id={block_id}")
                block_text = str(block.get("text") or "")
                if page_text[normalized_span["start"] : normalized_span["end"]] != block_text:
                    raise ValueError(f"页面布局块正文与 source_span 不一致: block_id={block_id}")
                blocks.append(
                    {
                        "block_id": block_id,
                        "text": block_text,
                        "source_span": normalized_span,
                    }
                )
            if not blocks:
                raise ValueError(f"页面没有可锚定的正文块: page_number={page_number}")
            items.append(
                {
                    "source_kind": "document",
                    "document_id": document_id,
                    "page_number": page_number,
                    "page_text": page_text,
                    "blocks": blocks,
                    "asset_source_sha256": source_sha256,
                    "page_image_sha256": page_image_sha256,
                    "region": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                    "marks": _normalized_marks(manifest, page),
                    "strikeout_spans": _strikeout_spans(manifest, page),
                    "source_scopes": [
                        {
                            "scope_id": str(item.get("evidence_id") or ""),
                            "block_ids": [str(value) for value in list(item.get("block_ids") or [])],
                            "source_span": {
                                "start": int(item.get("source_offset_start") or 0),
                                "end": int(item.get("source_offset_end") or 0),
                            },
                        }
                        for item in catalog_items
                        if int(item.get("page_number") or 0) == page_number
                    ],
                }
            )
        if len(items) != int(manifest.get("page_count") or 0):
            raise ValueError("manifest.page_count 与真实页面清单不一致")
    else:
        raise ValueError(f"不支持的需求来源: kind={source_kind}")

    context.artifacts["source_semantics_prepare"] = {
        "source_kind": source_kind,
        "item_count": len(items),
    }
    return {"items": items, "item_count": len(items), "source_kind": source_kind}


def _validated_document_anchor(
    anchor: dict[str, Any],
    prepared: dict[str, Any],
) -> tuple[dict[str, Any], bool, set[str]]:
    document_id = int(anchor.get("document_id") or 0)
    page_number = int(anchor.get("page_number") or 0)
    block_id = _required_text(anchor.get("block_id"), "source_anchor.block_id")
    if document_id != int(prepared["document_id"]) or page_number != int(
        prepared["page_number"]
    ):
        raise ValueError("来源事实的 document_id/page_number 与分析输入不一致")
    asset_hash = str(anchor.get("asset_source_sha256") or "").strip().lower()
    page_hash = str(anchor.get("page_image_sha256") or "").strip().lower()
    if asset_hash != str(prepared["asset_source_sha256"]) or page_hash != str(
        prepared["page_image_sha256"]
    ):
        raise ValueError("来源事实的资产指纹与分析输入不一致")
    blocks = {str(item["block_id"]): dict(item) for item in prepared["blocks"]}
    block = blocks.get(block_id)
    if block is None:
        raise ValueError(f"来源事实引用了未知页面块: block_id={block_id}")
    fact_span = _span(anchor.get("source_span"), field_name="source_anchor.source_span")
    block_span = dict(block["source_span"])
    if fact_span["start"] < block_span["start"] or fact_span["end"] > block_span["end"]:
        raise ValueError(f"来源事实 source_span 不属于引用块: block_id={block_id}")
    quote = _required_text(anchor.get("quote"), "source_anchor.quote")
    if str(prepared["page_text"])[fact_span["start"] : fact_span["end"]] != quote:
        raise ValueError(f"来源事实 quote 未精确命中页面正文: block_id={block_id}")
    superseded_by_mark = any(
        (not str(mark.get("block_id") or "") or str(mark["block_id"]) == block_id)
        and _overlaps(fact_span, dict(mark["source_span"]))
        for mark in list(prepared.get("strikeout_spans") or [])
    )
    source_scopes = [
        dict(scope)
        for scope in list(prepared.get("source_scopes") or [])
        if str(scope.get("scope_id") or "").strip()
        and block_id in list(scope.get("block_ids") or [])
        and _overlaps(fact_span, dict(scope.get("source_span") or {}))
    ]
    if not source_scopes:
        raise ValueError(f"来源事实未命中证据作用域: block_id={block_id}")
    return (
        {
            "source_kind": "document",
            "document_id": document_id,
            "page_number": page_number,
            "block_id": block_id,
            "source_span": fact_span,
            "quote": quote,
            "asset_source_sha256": asset_hash,
            "page_image_sha256": page_hash,
        },
        superseded_by_mark,
        {str(scope["scope_id"]) for scope in source_scopes},
    )


def _validated_inline_anchor(
    anchor: dict[str, Any],
    prepared: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    requirement = str(prepared["requirement"])
    requirement_sha256 = str(anchor.get("requirement_sha256") or "").strip().lower()
    if requirement_sha256 != str(prepared["requirement_sha256"]):
        raise ValueError("纯文本事实的 requirement_sha256 与分析输入不一致")
    start = int(anchor.get("source_offset_start", -1))
    end = int(anchor.get("source_offset_end", -1))
    quote = _required_text(anchor.get("quote"), "source_anchor.quote")
    if start < 0 or end <= start or end > len(requirement):
        raise ValueError("纯文本事实的来源坐标无效")
    if requirement[start:end] != quote:
        raise ValueError("纯文本事实 quote 未精确命中 requirement")
    matching_scopes = {
        str(scope.get("scope_id") or "")
        for scope in list(prepared.get("source_scopes") or [])
        if int(scope.get("source_offset_start") or 0) <= start
        and int(scope.get("source_offset_end") or 0) >= end
    }
    if not matching_scopes:
        raise ValueError("纯文本事实未命中证据作用域")
    return ({
        "source_kind": "inline",
        "requirement_sha256": requirement_sha256,
        "source_offset_start": start,
        "source_offset_end": end,
        "quote": quote,
    }, matching_scopes)


def merge_source_semantics(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验 Agent 事实锚点，并仅把仍生效的事实交给规划和生成。"""

    prepared_items = list(arguments.get("semantic_inputs") or [])
    records = list(arguments.get("semantic_records") or [])
    if not prepared_items or len(prepared_items) != len(records):
        raise ValueError("source semantics 输入与结果数量不一致")
    records_by_index: dict[int, dict[str, Any]] = {}
    for raw_record in records:
        if not isinstance(raw_record, dict):
            raise ValueError("source semantics 结果只能包含对象")
        item_index = int(raw_record.get("item_index", -1))
        if item_index < 0 or item_index >= len(prepared_items) or item_index in records_by_index:
            raise ValueError(f"source semantics item_index 无效或重复: {item_index}")
        records_by_index[item_index] = dict(raw_record)
    if len(records_by_index) != len(prepared_items):
        raise ValueError("source semantics 结果缺少输入项")

    facts: list[dict[str, Any]] = []
    fact_ids: set[str] = set()
    for item_index, prepared in enumerate(prepared_items):
        output = records_by_index[item_index].get("output")
        if not isinstance(output, dict) or not isinstance(output.get("authoritative_facts"), list):
            raise ValueError(f"source semantics 输出缺少 authoritative_facts: item_index={item_index}")
        for raw_fact in output["authoritative_facts"]:
            if not isinstance(raw_fact, dict):
                raise ValueError("authoritative_facts 只能包含对象")
            fact = dict(raw_fact)
            fact_id = _required_text(fact.get("fact_id"), "fact_id")
            if fact_id in fact_ids:
                raise ValueError(f"authoritative fact_id 重复: {fact_id}")
            fact_ids.add(fact_id)
            source_anchor = dict(fact.get("source_anchor") or {})
            source_kind = str(prepared.get("source_kind") or "")
            if str(source_anchor.get("source_kind") or "") != source_kind:
                raise ValueError(f"事实来源类型与分析输入不一致: fact_id={fact_id}")
            superseded_by_mark = False
            if source_kind == "document":
                normalized_anchor, superseded_by_mark, allowed_scope_ids = _validated_document_anchor(
                    source_anchor,
                    prepared,
                )
                anchor_span = normalized_anchor["source_span"]
                anchor_text = str(normalized_anchor["quote"])
            else:
                normalized_anchor, allowed_scope_ids = _validated_inline_anchor(
                    source_anchor, prepared
                )
                anchor_text = str(normalized_anchor["quote"])
            raw_governed_by = fact.get("governed_by")
            if not isinstance(raw_governed_by, list):
                raise ValueError(f"governed_by 必须是数组: fact_id={fact_id}")
            governed_by: list[dict[str, str]] = []
            governance_keys: set[tuple[str, str]] = set()
            for raw_directive in raw_governed_by:
                if not isinstance(raw_directive, dict):
                    raise ValueError(f"governed_by 只能包含对象: fact_id={fact_id}")
                relation = str(raw_directive.get("relation") or "").strip()
                directive_fact_id = _required_text(
                    raw_directive.get("directive_fact_id"),
                    "governed_by.directive_fact_id",
                )
                if relation not in _GOVERNANCE_RELATIONS:
                    raise ValueError(f"governed_by.relation 无效: fact_id={fact_id}")
                key = (relation, directive_fact_id)
                if key in governance_keys or directive_fact_id == fact_id:
                    raise ValueError(f"governed_by 包含重复或自身引用: fact_id={fact_id}")
                governance_keys.add(key)
                governed_by.append(
                    {"relation": relation, "directive_fact_id": directive_fact_id}
                )
            if any(item["directive_fact_id"] == fact_id for item in governed_by):
                raise ValueError(f"governed_by 包含重复或自身引用: fact_id={fact_id}")
            status = str(fact.get("status") or "").strip()
            if status not in _FACT_STATUSES:
                raise ValueError(f"事实状态无效: fact_id={fact_id}, status={status}")
            value_policy = str(fact.get("value_policy") or "").strip()
            if value_policy not in {"exact", "runtime_configured"}:
                raise ValueError(
                    f"事实 value_policy 无效: fact_id={fact_id}, value_policy={value_policy}"
                )
            raw_governed_values = fact.get("governed_values")
            if not isinstance(raw_governed_values, list):
                raise ValueError(f"governed_values 必须是数组: fact_id={fact_id}")
            governed_values = [
                _required_text(item, "governed_values") for item in raw_governed_values
            ]
            if len(governed_values) != len(set(governed_values)):
                raise ValueError(f"governed_values 包含重复值: fact_id={fact_id}")
            if value_policy == "exact" and governed_values:
                raise ValueError(f"exact 事实的 governed_values 必须为空: fact_id={fact_id}")
            if value_policy == "runtime_configured" and not governed_values:
                raise ValueError(
                    f"runtime_configured 事实必须声明 governed_values: fact_id={fact_id}"
                )
            assertion = _required_text(fact.get("assertion"), "assertion")
            for governed_value in governed_values:
                if governed_value not in assertion and governed_value not in anchor_text:
                    raise ValueError(
                        f"governed_value 未命中 assertion 或精确来源原文: "
                        f"fact_id={fact_id}, value={governed_value}"
                    )
            if superseded_by_mark:
                status = "superseded"
            scope_id = _required_text(fact.get("scope_id"), "scope_id")
            if scope_id not in allowed_scope_ids:
                raise ValueError(
                    f"事实 scope_id 与来源锚点不一致: fact_id={fact_id}, scope_id={scope_id}"
                )
            facts.append(
                {
                    "fact_id": fact_id,
                    "assertion": assertion,
                    "scope_id": scope_id,
                    "source_anchor": normalized_anchor,
                    "status": status,
                    "value_policy": value_policy,
                    "governed_values": governed_values,
                    "governed_by": governed_by,
                }
            )

    for fact in facts:
        unknown = {
            item["directive_fact_id"] for item in fact["governed_by"]
        } - fact_ids
        if unknown:
            raise ValueError(
                f"governed_by 引用了未知 fact_id: fact_id={fact['fact_id']}, unknown={sorted(unknown)}"
            )
    effective_facts = [dict(fact) for fact in facts if fact["status"] == "effective"]
    if not effective_facts:
        raise ValueError("source semantics 没有可供规划和生成使用的有效事实")
    result = {
        "authoritative_facts": facts,
        "effective_facts": effective_facts,
        "inspected_page_count": sum(
            1 for item in prepared_items if item.get("source_kind") == "document"
        ),
    }
    context.artifacts["source_semantics"] = result
    return result


def _fact_source_order(fact: dict[str, Any]) -> tuple[int, int, str]:
    """使用真实来源坐标排序，供跨页规则协调判断先后关系。"""

    anchor = dict(fact.get("source_anchor") or {})
    if str(anchor.get("source_kind") or "") == "document":
        span = dict(anchor.get("source_span") or {})
        return (
            int(anchor.get("page_number") or 0),
            int(span.get("start") or 0),
            str(fact.get("fact_id") or ""),
        )
    return (
        0,
        int(anchor.get("source_offset_start") or 0),
        str(fact.get("fact_id") or ""),
    )


def prepare_authority_reconciliation(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """按规划模块聚合跨页事实，仅为确有多来源的模块创建协调任务。"""

    plan = arguments.get("plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("business_modules"), list):
        raise ValueError("plan 必须包含 business_modules")
    raw_facts = arguments.get("authoritative_facts")
    if not isinstance(raw_facts, list) or not raw_facts:
        raise ValueError("authoritative_facts 必须是非空数组")
    facts: list[dict[str, Any]] = []
    fact_ids: set[str] = set()
    for raw_fact in raw_facts:
        if not isinstance(raw_fact, dict):
            raise ValueError("authoritative_facts 每项必须是对象")
        fact = dict(raw_fact)
        fact_id = _required_text(fact.get("fact_id"), "fact_id")
        if fact_id in fact_ids:
            raise ValueError(f"authoritative fact_id 重复: {fact_id}")
        fact_ids.add(fact_id)
        facts.append(fact)

    items: list[dict[str, Any]] = []
    skipped_modules: list[dict[str, Any]] = []
    for module_index, raw_module in enumerate(plan["business_modules"]):
        if not isinstance(raw_module, dict):
            raise ValueError(f"business_modules[{module_index}] 必须是对象")
        module = dict(raw_module)
        evidence_ids = {
            str(value or "").strip()
            for value in list(module.get("evidence_ids") or [])
            if str(value or "").strip()
        }
        if not evidence_ids:
            raise ValueError(f"规划模块缺少 evidence_ids: module_index={module_index}")
        module_facts = sorted(
            [dict(fact) for fact in facts if str(fact.get("scope_id") or "") in evidence_ids],
            key=_fact_source_order,
        )
        if not module_facts:
            raise ValueError(f"规划模块没有权威事实: module_index={module_index}")
        source_positions = {
            (
                str(dict(fact.get("source_anchor") or {}).get("source_kind") or ""),
                int(dict(fact.get("source_anchor") or {}).get("page_number") or 0),
            )
            for fact in module_facts
        }
        if len(source_positions) < 2:
            skipped_modules.append(
                {
                    "module_index": module_index,
                    "module_name": str(module.get("name") or ""),
                    "reason": "single_source_position",
                }
            )
            continue
        items.append(
            {
                "module_index": module_index,
                "module": module,
                "authoritative_facts": module_facts,
            }
        )

    context.artifacts["authority_reconciliation_prepare"] = {
        "review_module_count": len(items),
        "skipped_module_count": len(skipped_modules),
        "skipped_modules": skipped_modules,
    }
    return {"items": items, "review_module_count": len(items)}


def _normalize_reconciled_decision(
    raw_decision: Any,
    *,
    original: dict[str, Any],
    module_fact_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw_decision, dict):
        raise ValueError("authority reconciliation decisions 每项必须是对象")
    decision = dict(raw_decision)
    fact_id = _required_text(decision.get("fact_id"), "decision.fact_id")
    if fact_id != str(original.get("fact_id") or ""):
        raise ValueError(f"authority reconciliation fact_id 与输入顺序不一致: {fact_id}")
    status = str(decision.get("status") or "").strip()
    if status not in _FACT_STATUSES:
        raise ValueError(f"authority reconciliation status 无效: fact_id={fact_id}")
    original_status = str(original.get("status") or "")
    if original_status != "effective" and status != original_status:
        raise ValueError(f"authority reconciliation 不得重新激活或改写失效事实: fact_id={fact_id}")

    value_policy = str(decision.get("value_policy") or "").strip()
    original_policy = str(original.get("value_policy") or "")
    if value_policy not in {"exact", "runtime_configured"}:
        raise ValueError(f"authority reconciliation value_policy 无效: fact_id={fact_id}")
    if original_policy == "runtime_configured" and value_policy != original_policy:
        raise ValueError(f"authority reconciliation 不得把动态配置降级为固定值: fact_id={fact_id}")

    raw_values = decision.get("governed_values")
    if not isinstance(raw_values, list):
        raise ValueError(f"authority reconciliation governed_values 必须是数组: fact_id={fact_id}")
    governed_values = [_required_text(value, "governed_values") for value in raw_values]
    if len(governed_values) != len(set(governed_values)):
        raise ValueError(f"authority reconciliation governed_values 重复: fact_id={fact_id}")
    if value_policy == "exact" and governed_values:
        raise ValueError(f"exact 事实不得携带 governed_values: fact_id={fact_id}")
    if value_policy == "runtime_configured" and not governed_values:
        raise ValueError(f"runtime_configured 事实必须携带 governed_values: fact_id={fact_id}")
    source_text = "\n".join(
        [
            str(original.get("assertion") or ""),
            str(dict(original.get("source_anchor") or {}).get("quote") or ""),
        ]
    )
    for value in governed_values:
        if value not in source_text:
            raise ValueError(
                f"authority reconciliation governed_value 未命中事实原文: fact_id={fact_id}, value={value}"
            )

    raw_governed_by = decision.get("governed_by")
    if not isinstance(raw_governed_by, list):
        raise ValueError(f"authority reconciliation governed_by 必须是数组: fact_id={fact_id}")
    governed_by: list[dict[str, str]] = []
    seen_relations: set[tuple[str, str]] = set()
    original_relations = {
        (
            str(item.get("relation") or ""),
            str(item.get("directive_fact_id") or ""),
        )
        for item in list(original.get("governed_by") or [])
        if isinstance(item, dict)
    }
    for raw_relation in raw_governed_by:
        if not isinstance(raw_relation, dict):
            raise ValueError(f"authority reconciliation governed_by 每项必须是对象: fact_id={fact_id}")
        relation = str(raw_relation.get("relation") or "").strip()
        directive_fact_id = _required_text(
            raw_relation.get("directive_fact_id"),
            "governed_by.directive_fact_id",
        )
        key = (relation, directive_fact_id)
        if relation not in _GOVERNANCE_RELATIONS:
            raise ValueError(f"authority reconciliation relation 无效: fact_id={fact_id}")
        if directive_fact_id == fact_id:
            raise ValueError(f"authority reconciliation 引用了自身事实: fact_id={fact_id}")
        if directive_fact_id not in module_fact_ids and key not in original_relations:
            raise ValueError(f"authority reconciliation 新增了模块外事实引用: fact_id={fact_id}")
        if key in seen_relations:
            raise ValueError(f"authority reconciliation governed_by 重复: fact_id={fact_id}")
        seen_relations.add(key)
        governed_by.append({"relation": relation, "directive_fact_id": directive_fact_id})

    reason = _required_text(decision.get("reason"), "decision.reason")
    return {
        "fact_id": fact_id,
        "status": status,
        "value_policy": value_policy,
        "governed_values": governed_values,
        "governed_by": governed_by,
        "reason": reason,
    }


def merge_authority_reconciliation(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """确定性应用模块级协调结果，禁止遗漏、跨模块引用和冲突裁决。"""

    raw_facts = arguments.get("authoritative_facts")
    prepared_items = arguments.get("prepared_items")
    records = arguments.get("reconciliation_records")
    if not isinstance(raw_facts, list) or not raw_facts:
        raise ValueError("authoritative_facts 必须是非空数组")
    if not isinstance(prepared_items, list) or not isinstance(records, list):
        raise ValueError("authority reconciliation 输入与结果必须是数组")
    if len(prepared_items) != len(records):
        raise ValueError("authority reconciliation 输入与结果数量不一致")

    facts_by_id = {
        _required_text(dict(fact).get("fact_id"), "fact_id"): dict(fact)
        for fact in raw_facts
        if isinstance(fact, dict)
    }
    if len(facts_by_id) != len(raw_facts):
        raise ValueError("authoritative_facts 包含重复 fact_id 或非对象")
    records_by_index: dict[int, dict[str, Any]] = {}
    for raw_record in records:
        if not isinstance(raw_record, dict):
            raise ValueError("reconciliation_records 每项必须是对象")
        item_index = int(raw_record.get("item_index", -1))
        if item_index < 0 or item_index >= len(prepared_items) or item_index in records_by_index:
            raise ValueError(f"reconciliation_records item_index 无效或重复: {item_index}")
        records_by_index[item_index] = dict(raw_record)
    if len(records_by_index) != len(prepared_items):
        raise ValueError("reconciliation_records 缺少输入项")

    decisions_by_fact_id: dict[str, dict[str, Any]] = {}
    for item_index, raw_prepared in enumerate(prepared_items):
        if not isinstance(raw_prepared, dict):
            raise ValueError("prepared_items 每项必须是对象")
        prepared = dict(raw_prepared)
        module_facts = [dict(fact) for fact in list(prepared.get("authoritative_facts") or [])]
        module_fact_ids = {str(fact.get("fact_id") or "") for fact in module_facts}
        output = records_by_index[item_index].get("output")
        if not isinstance(output, dict) or not isinstance(output.get("decisions"), list):
            raise ValueError(f"authority reconciliation 输出缺少 decisions: item_index={item_index}")
        raw_decisions = output["decisions"]
        if len(raw_decisions) != len(module_facts):
            raise ValueError(f"authority reconciliation 必须逐条裁决模块事实: item_index={item_index}")
        for decision_index, original in enumerate(module_facts):
            normalized = _normalize_reconciled_decision(
                raw_decisions[decision_index],
                original=original,
                module_fact_ids=module_fact_ids,
            )
            previous = decisions_by_fact_id.get(normalized["fact_id"])
            comparable = {key: value for key, value in normalized.items() if key != "reason"}
            if previous is not None:
                previous_comparable = {
                    key: value for key, value in previous.items() if key != "reason"
                }
                if previous_comparable != comparable:
                    raise ValueError(
                        "同一事实在多个模块的权威协调结论不一致: "
                        f"fact_id={normalized['fact_id']}"
                    )
            else:
                decisions_by_fact_id[normalized["fact_id"]] = normalized

    reconciled: list[dict[str, Any]] = []
    for raw_fact in raw_facts:
        fact = dict(raw_fact)
        decision = decisions_by_fact_id.get(str(fact.get("fact_id") or ""))
        if decision is not None:
            fact.update(
                {
                    "status": decision["status"],
                    "value_policy": decision["value_policy"],
                    "governed_values": decision["governed_values"],
                    "governed_by": decision["governed_by"],
                }
            )
        reconciled.append(fact)
    effective_facts = [dict(fact) for fact in reconciled if fact.get("status") == "effective"]
    if not effective_facts:
        raise ValueError("authority reconciliation 后没有可供生成使用的有效事实")
    result = {
        "authoritative_facts": reconciled,
        "effective_facts": effective_facts,
        "reviewed_module_count": len(prepared_items),
    }
    context.artifacts["authority_reconciliation"] = {
        **result,
        "decisions": list(decisions_by_fact_id.values()),
    }
    return result
