"""Shared document vector index operations."""

from __future__ import annotations

import re
from typing import Any, Optional

from core.processing.biz_key_extractor import extract_biz_key
from core.processing.business_chunking import BusinessChunkerDispatcher
from core.cache_layer.chroma_client import DEFAULT_EMBED_MAX_CHARS
from core.db.model_defs import KnowledgeDocument
from modules.knowledge_base_components.adapters.chroma_vector_store import get_vector_store
from modules.knowledge_base_components.ports.vector_store_port import VectorStorePort
from modules.knowledge_base_components.document.document_asset_service import (
    detect_high_confidence_page_continuations,
    document_page_layout,
    document_page_text,
    load_document_manifest,
)


def _resolve_vector_store(
    *,
    vector_store: Optional[VectorStorePort] = None,
    client=None,
) -> VectorStorePort:
    return vector_store or get_vector_store(client=client)


def _clean_metadata_text(value: Any) -> str:
    """移除控制字符，避免脏文本进入模块和业务键等检索元数据。"""

    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()


def _clean_content_text(value: Any) -> str:
    """清理正文中的不可见控制字符，同时保留分块需要的换行与制表。"""

    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", str(value or ""))


def is_vector_store_ready(*, client=None, vector_store: Optional[VectorStorePort] = None) -> bool:
    return _resolve_vector_store(vector_store=vector_store, client=client).is_ready()


def build_document_index_chunks(
    *,
    content: str,
    doc_type: str,
    default_module: str | None = None,
    default_biz_key: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, str]:
    """按文档类型生成向量索引分块及业务元数据。"""
    normalized_content = _clean_content_text(content)
    chunk_objects = BusinessChunkerDispatcher().chunk(doc_type, normalized_content)
    if not chunk_objects:
        raise ValueError(f"文档未生成可索引分块：doc_type={doc_type}")

    module_hint = _clean_metadata_text(default_module) or next(
        (
            _clean_metadata_text(item.module)
            for item in chunk_objects
            if getattr(item, "module", None)
        ),
        None,
    )
    biz_key = _clean_metadata_text(default_biz_key) or _clean_metadata_text(
        extract_biz_key(normalized_content, module_hint or "")
    )

    payloads: list[dict[str, Any]] = []
    for item in chunk_objects:
        chunk_text = str(getattr(item, "text", "") or "").strip()
        if not chunk_text:
            continue
        module_value = _clean_metadata_text(getattr(item, "module", "")) or module_hint
        biz_key_value = _clean_metadata_text(getattr(item, "biz_key", "")) or biz_key
        requirement_id = _clean_metadata_text(getattr(item, "requirement_id", "")) or None
        test_case_id = _clean_metadata_text(getattr(item, "test_case_id", "")) or None
        payloads.append(
            {
                "chunk_text": chunk_text,
                "metadata": {
                    **dict(getattr(item, "extra_metadata", {}) or {}),
                    "module": module_value,
                    "biz_key": biz_key_value,
                    "requirement_id": requirement_id,
                    "test_case_id": test_case_id,
                    "related_ids": [
                        item_id
                        for item_id in (requirement_id, test_case_id)
                        if item_id
                    ],
                },
            }
        )
    return payloads, module_hint, biz_key


def build_document_asset_index_chunks(
    *,
    doc: KnowledgeDocument,
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None, str]:
    """从逐页资产生成带页码和布局锚点的通用索引块。"""

    payloads: list[dict[str, Any]] = []
    if int(manifest.get("schema_version") or 0) != 3:
        raise ValueError(
            f"文档页面资产版本不受支持，必须重新解析: doc_id={doc.id}, "
            f"schema_version={manifest.get('schema_version')}"
        )
    whole_continuation_ids_by_page = {
        int(link["right_page_number"]): {
            str(value)
            for value in list(link.get("right_continuation_block_ids") or [])
            if str(value).strip()
        }
        for link in detect_high_confidence_page_continuations(manifest)
        if bool(link.get("right_page_is_whole_item"))
    }
    for page in list(manifest.get("pages") or []):
        page_number = int(page.get("page_number") or 0)
        if page_number < 1:
            continue
        page_content = document_page_text(int(doc.id), page_number)
        if not page_content.strip():
            continue
        layout_blocks = document_page_layout(int(doc.id), page_number)
        block_intervals: dict[str, tuple[int, int]] = {}
        run_ids: set[str] = set()
        for block in layout_blocks:
            if str(block.get("type") or "") != "text_line":
                continue
            block_id = str(block.get("block_id") or "").strip()
            span = dict(block.get("source_span") or {})
            start = int(span.get("start") or 0)
            end = int(span.get("end") or 0)
            if not block_id or start < 0 or end <= start:
                raise ValueError(
                    "版式文本行缺少同源坐标，必须重新解析文档: "
                    f"doc_id={doc.id}, page_number={page_number}, block_id={block_id}"
                )
            if page_content[start:end] != str(block.get("text") or ""):
                raise ValueError(
                    "版式文本行与同源页文本坐标不一致: "
                    f"doc_id={doc.id}, page_number={page_number}, block_id={block_id}"
                )
            block_intervals[block_id] = (start, end)
            text_runs = block.get("text_runs")
            if not isinstance(text_runs, list) or not text_runs:
                raise ValueError(
                    "v3 版式文本行缺少 text_runs，必须重新解析文档: "
                    f"doc_id={doc.id}, page_number={page_number}, block_id={block_id}"
                )
            for run in text_runs:
                run_id = str(run.get("run_id") or "").strip()
                run_span = dict(run.get("source_span") or {})
                run_start = int(run_span.get("start") or 0)
                run_end = int(run_span.get("end") or 0)
                if (
                    not run_id
                    or run_id in run_ids
                    or run_start < start
                    or run_end > end
                    or run_end <= run_start
                    or page_content[run_start:run_end] != str(run.get("text") or "")
                    or str(run.get("asset_source_sha256") or "")
                    != str(manifest.get("source_sha256") or "")
                ):
                    raise ValueError(
                        "v3 文本 run 与同源正文不一致: "
                        f"doc_id={doc.id}, page_number={page_number}, run_id={run_id}"
                    )
                run_ids.add(run_id)
        page_marks = [dict(mark) for mark in list(page.get("marks") or [])]
        mark_spans: dict[str, list[tuple[int, int]]] = {}
        for mark in page_marks:
            mark_id = str(mark.get("mark_id") or "").strip()
            target_run_ids = {
                str(value) for value in list(mark.get("target_run_ids") or [])
            }
            if (
                not mark_id
                or not target_run_ids.issubset(run_ids)
                or str(mark.get("asset_source_sha256") or "")
                != str(manifest.get("source_sha256") or "")
            ):
                raise ValueError(
                    "v3 版面 mark 与文本 run 或资产指纹不一致: "
                    f"doc_id={doc.id}, page_number={page_number}, mark_id={mark_id}"
                )
            spans: list[tuple[int, int]] = []
            for raw_span in list(mark.get("target_source_spans") or []):
                span = dict(raw_span or {})
                mark_start = int(span.get("start") or 0)
                mark_end = int(span.get("end") or 0)
                if mark_start < 0 or mark_end <= mark_start or mark_end > len(page_content):
                    raise ValueError(
                        "v3 版面 mark 来源坐标无效: "
                        f"doc_id={doc.id}, page_number={page_number}, mark_id={mark_id}"
                    )
                spans.append((mark_start, mark_end))
            mark_spans[mark_id] = spans
        required_whole_ids = whole_continuation_ids_by_page.get(page_number, set())
        if not required_whole_ids.issubset(block_intervals):
            raise ValueError(
                "整页续项缺少完整版式锚点: "
                f"doc_id={doc.id}, page_number={page_number}, "
                f"missing={sorted(required_whole_ids - set(block_intervals))}"
            )
        for chunk_text, start, end in _source_preserving_page_chunks(page_content):
            if page_content[start:end] != chunk_text:
                raise ValueError(
                    "页面资产分块与真实源文本不一致: "
                    f"doc_id={doc.id}, page_number={page_number}, start={start}, end={end}"
                )
            block_ids = [
                block_id
                for block_id, (block_start, block_end) in block_intervals.items()
                if block_start < end and block_end > start
            ]
            mark_ids = [
                mark_id
                for mark_id, spans in mark_spans.items()
                if any(mark_start < end and mark_end > start for mark_start, mark_end in spans)
            ]
            metadata: dict[str, Any] = {
                # 中文注释：逐页资产的首行不是结构化标题，不向 Agent 传播伪 heading 业务键。
                "biz_key": "",
                "page_number": page_number,
                "page_start": page_number,
                "page_end": page_number,
                "source_text_path": str(page.get("text_path") or ""),
                "source_offset_start": start,
                "source_offset_end": end,
                "block_ids": block_ids,
                "mark_ids": mark_ids,
                "layout_anchor_count": len(block_intervals),
                "layout_mark_count": len(page_marks),
                "layout_anchor_complete": True,
                "asset_revision": int(manifest.get("schema_version") or 1),
                "asset_source_sha256": str(manifest.get("source_sha256") or ""),
            }
            payloads.append({"chunk_text": chunk_text, "metadata": metadata})
    if not payloads:
        raise ValueError(f"文档页面资产未生成可索引文本: doc_id={doc.id}")
    return payloads, None, ""


def _source_preserving_page_chunks(
    page_content: str,
    *,
    start: int = 0,
    end: int | None = None,
) -> list[tuple[str, int, int]]:
    """按 embedding 容量从原文直接切片，不改写换行、空格或标点。"""

    chunks: list[tuple[str, int, int]] = []
    cursor = max(0, int(start))
    text_end = len(page_content) if end is None else min(len(page_content), int(end))
    if cursor > text_end:
        raise ValueError("页面资产分块起始坐标超过结束坐标")
    while cursor < text_end:
        while cursor < text_end and page_content[cursor].isspace():
            cursor += 1
        if cursor >= text_end:
            break
        hard_end = min(text_end, cursor + DEFAULT_EMBED_MAX_CHARS)
        chunk_end = hard_end
        if hard_end < text_end:
            newline = page_content.rfind("\n", cursor + 1, hard_end + 1)
            if newline > cursor:
                chunk_end = newline
        while chunk_end > cursor and page_content[chunk_end - 1].isspace():
            chunk_end -= 1
        if chunk_end <= cursor:
            chunk_end = hard_end
        chunk_text = page_content[cursor:chunk_end]
        if not chunk_text or page_content[cursor:chunk_end] != chunk_text:
            raise ValueError("页面资产源文本分块失去坐标一致性")
        chunks.append((chunk_text, cursor, chunk_end))
        cursor = chunk_end
    return chunks


def delete_document_indexes(
    doc_id: int | str,
    *,
    raise_on_error: bool = False,
    client=None,
    vector_store: Optional[VectorStorePort] = None,
) -> None:
    """Delete raw and summary indexes for one document id."""
    active = _resolve_vector_store(vector_store=vector_store, client=client)
    normalized_id = str(doc_id)
    active.delete_document(normalized_id, raise_on_error=raise_on_error)
    active.delete_document(f"{normalized_id}_summary", raise_on_error=raise_on_error)


def upsert_document_indexes(
    *,
    doc_id: int | str,
    content: str,
    metadata: dict[str, Any],
    summary_text: str = "",
    summary_metadata: Optional[dict[str, Any]] = None,
    chunks: list[dict[str, Any]],
    summary_chunks: Optional[list[dict[str, Any]]] = None,
    raise_on_error: bool = False,
    client=None,
    vector_store: Optional[VectorStorePort] = None,
) -> tuple[bool, bool]:
    """
    Upsert raw and summary vector indexes.

    Returns (indexed_raw, indexed_summary).
    """
    active = _resolve_vector_store(vector_store=vector_store, client=client)
    normalized_id = str(doc_id)
    if not chunks:
        raise ValueError(f"原文索引分块不能为空：doc_id={normalized_id}")
    delete_document_indexes(normalized_id, raise_on_error=raise_on_error, vector_store=active)

    active.add_document(
        doc_id=normalized_id,
        metadata=metadata,
        chunks=chunks,
        raise_on_error=raise_on_error,
    )
    indexed_raw = True

    summary = str(summary_text or "").strip()
    if summary and summary != str(content or ""):
        if not summary_chunks:
            raise ValueError(f"摘要索引分块不能为空：doc_id={normalized_id}")
        active.add_document(
            doc_id=f"{normalized_id}_summary",
            metadata=summary_metadata or metadata,
            chunks=summary_chunks,
            raise_on_error=raise_on_error,
        )
        return indexed_raw, True
    return indexed_raw, False


def _index_lane_exists(
    *,
    vector_store: VectorStorePort,
    doc_id: int | str,
    is_summary: bool,
) -> bool:
    result = vector_store.search_by_metadata(
        where={
            "$and": [
                {"doc_id": str(doc_id)},
                {"is_summary": is_summary},
            ]
        },
        n_results=1,
        raise_on_error=True,
    )
    ids = result.get("ids") or []
    if ids and isinstance(ids[0], list):
        ids = ids[0]
    return bool(ids)


def reindex_document_from_persisted_content(
    doc: KnowledgeDocument,
    *,
    vector_store: Optional[VectorStorePort] = None,
) -> dict[str, Any]:
    """只依据 MySQL 持久化正文重建一个文档的全部派生向量索引。"""

    active = _resolve_vector_store(vector_store=vector_store)
    if not active.is_ready():
        raise RuntimeError("vector store is unavailable")
    content = str(doc.content or "")
    if not content.strip():
        raise ValueError(f"文档正文为空，不能重建索引: doc_id={doc.id}")
    summary = str(doc.summary or "")
    try:
        manifest = load_document_manifest(int(doc.id))
    except FileNotFoundError as exc:
        raise ValueError(
            f"文档缺少 v3 页面资产，必须先重新解析: doc_id={doc.id}"
        ) from exc
    if int(manifest.get("schema_version") or 0) != 3:
        raise ValueError(
            f"文档页面资产不是 v3，必须先重新解析: doc_id={doc.id}"
        )
    asset_hash = str(manifest.get("source_sha256") or "").strip().lower()
    stored_hash = str(doc.content_hash or "").strip().lower()
    if not stored_hash or stored_hash != asset_hash:
        raise ValueError(f"文档资产与记录指纹不一致: doc_id={doc.id}")
    raw_chunks, module_hint, doc_biz_key = build_document_asset_index_chunks(
        doc=doc,
        manifest=manifest,
    )
    summary_chunks = None
    if summary.strip() and summary.strip() != content.strip():
        summary_chunks, _, _ = build_document_index_chunks(
            content=summary,
            doc_type=str(doc.doc_type or ""),
            default_module=module_hint,
            default_biz_key=doc_biz_key,
        )
    base_metadata = {
        "project_id": doc.project_id,
        "doc_type": doc.doc_type,
        "filename": doc.filename,
        "doc_id": doc.id,
        "user_id": doc.user_id,
        "module": module_hint,
        "biz_key": doc_biz_key,
        "requirement_id": None,
        "test_case_id": None,
        "source_doc_name": doc.filename,
        "content_hash": str(doc.content_hash or ""),
        "is_summary": False,
    }
    indexed_raw, indexed_summary = upsert_document_indexes(
        doc_id=doc.id,
        content=content,
        metadata=base_metadata,
        summary_text=summary,
        summary_metadata={
            **base_metadata,
            "filename": f"{doc.filename} (Summary)",
            "is_summary": True,
        },
        chunks=raw_chunks,
        summary_chunks=summary_chunks,
        raise_on_error=True,
        vector_store=active,
    )
    if not _index_lane_exists(vector_store=active, doc_id=doc.id, is_summary=False):
        raise RuntimeError(f"原文索引写后校验失败: doc_id={doc.id}")
    expect_summary = bool(summary.strip() and summary.strip() != content.strip())
    if expect_summary and not _index_lane_exists(
        vector_store=active,
        doc_id=doc.id,
        is_summary=True,
    ):
        raise RuntimeError(f"摘要索引写后校验失败: doc_id={doc.id}")
    return {
        "document_id": int(doc.id),
        "raw_chunk_count": len(raw_chunks),
        "summary_chunk_count": len(summary_chunks or []),
        "indexed_raw": indexed_raw,
        "indexed_summary": indexed_summary,
    }
