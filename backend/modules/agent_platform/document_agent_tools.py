"""Agent 运行期按需读取通用文档资产的工具。"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from core.db.model_defs import KnowledgeDocument
from modules.knowledge_base_components.document.document_asset_service import (
    document_page_layout,
    document_page_text,
    load_document_manifest,
    search_document_pages,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext, ToolRegistry


def _owned_document(context: ToolExecutionContext, document_id: Any) -> KnowledgeDocument:
    normalized_id = int(document_id or 0)
    document = (
        context.db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == normalized_id,
            KnowledgeDocument.project_id == context.project_id,
        )
        .first()
    )
    if document is None:
        raise ValueError("文档不存在或不属于当前项目")
    if document.user_id not in (None, 0, context.user_id):
        raise ValueError("无权读取该文档资产")
    if str(document.parse_status or "") != "success":
        raise ValueError("文档资产尚未准备完成")
    return document


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": int(manifest.get("document_id") or 0),
        "original_filename": str(manifest.get("original_filename") or ""),
        "media_type": str(manifest.get("media_type") or ""),
        "source_sha256": str(manifest.get("source_sha256") or ""),
        "source_size": int(manifest.get("source_size") or 0),
        "page_count": int(manifest.get("page_count") or 0),
        "pages": [
            {
                "page_number": int(page.get("page_number") or 0),
                "text_chars": int(page.get("text_chars") or 0),
                "image_available": bool(page.get("image_path")),
                "width": int(page.get("width") or 0),
                "height": int(page.get("height") or 0),
                "block_count": len(list(page.get("blocks") or [])),
            }
            for page in list(manifest.get("pages") or [])
        ],
    }


def get_manifest(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    document = _owned_document(context, arguments.get("document_id"))
    return _public_manifest(load_document_manifest(int(document.id)))


def search_document(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    document = _owned_document(context, arguments.get("document_id"))
    results = search_document_pages(
        int(document.id),
        str(arguments.get("query") or ""),
        limit=int(arguments.get("limit") or 5),
    )
    return {"document_id": int(document.id), "results": results}


def _public_layout_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只暴露 Agent 引用正文所需的稳定版式字段，隔离解析层内部元数据。"""

    public_blocks: list[dict[str, Any]] = []
    for block in blocks:
        item = {
            "block_id": str(block.get("block_id") or ""),
            "type": str(block.get("type") or ""),
            "text": str(block.get("text") or ""),
            "bbox": dict(block.get("bbox") or {}),
            "source": str(block.get("source") or ""),
        }
        source_span = block.get("source_span")
        if isinstance(source_span, dict):
            item["source_span"] = {
                "start": int(source_span.get("start") or 0),
                "end": int(source_span.get("end") or 0),
            }
        public_blocks.append(item)
    return public_blocks


def get_page_text(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    document = _owned_document(context, arguments.get("document_id"))
    page_number = int(arguments.get("page_number") or 0)
    return {
        "document_id": int(document.id),
        "page_number": page_number,
        "text": document_page_text(int(document.id), page_number),
        "layout_blocks": _public_layout_blocks(
            document_page_layout(int(document.id), page_number)
        ),
    }


BUILTIN_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tool_key": "document_get_manifest",
        "name": "读取文档页面清单",
        "description": "document.get_manifest：读取原文件指纹和可按需访问的页面清单。",
        "handler_key": "document.get_manifest",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "integer", "minimum": 1}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "integer", "minimum": 1},
                "original_filename": {"type": "string"},
                "media_type": {"type": "string"},
                "source_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                "source_size": {"type": "integer", "minimum": 1},
                "page_count": {"type": "integer", "minimum": 0},
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page_number": {"type": "integer", "minimum": 1},
                            "text_chars": {"type": "integer", "minimum": 0},
                            "image_available": {"type": "boolean"},
                            "width": {"type": "integer", "minimum": 0},
                            "height": {"type": "integer", "minimum": 0},
                            "block_count": {"type": "integer", "minimum": 0},
                        },
                        "required": ["page_number", "text_chars", "image_available", "width", "height", "block_count"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["document_id", "original_filename", "media_type", "source_sha256", "source_size", "page_count", "pages"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "document_search",
        "name": "检索文档页面",
        "description": "document.search：在逐页文本中检索，返回应进一步检查的真实页码。",
        "handler_key": "document.search",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "integer", "minimum": 1},
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["document_id", "query", "limit"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "integer", "minimum": 1},
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page_number": {"type": "integer", "minimum": 1},
                            "score": {"type": "integer", "minimum": 1},
                            "matched_terms": {"type": "array", "items": {"type": "string"}},
                            "snippet": {"type": "string"},
                        },
                        "required": ["page_number", "score", "matched_terms", "snippet"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["document_id", "results"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "document_get_page_text",
        "name": "读取文档逐页文本",
        "description": "document.get_page_text：读取指定页的真实文本。",
        "handler_key": "document.get_page_text",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "integer", "minimum": 1},
                "page_number": {"type": "integer", "minimum": 1},
            },
            "required": ["document_id", "page_number"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "integer", "minimum": 1},
                "page_number": {"type": "integer", "minimum": 1},
                "text": {"type": "string"},
                "layout_blocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "block_id": {"type": "string", "minLength": 1},
                            "type": {"type": "string", "enum": ["text_line", "image"]},
                            "text": {"type": "string"},
                            "bbox": {
                                "type": "object",
                                "properties": {
                                    "x": {"type": "number", "minimum": 0, "maximum": 1},
                                    "y": {"type": "number", "minimum": 0, "maximum": 1},
                                    "width": {"type": "number", "minimum": 0, "maximum": 1},
                                    "height": {"type": "number", "minimum": 0, "maximum": 1},
                                },
                                "required": ["x", "y", "width", "height"],
                                "additionalProperties": False,
                            },
                            "source": {"type": "string", "enum": ["pdf_text", "pdf_image"]},
                            "source_span": {
                                "type": "object",
                                "properties": {
                                    "start": {"type": "integer", "minimum": 0},
                                    "end": {"type": "integer", "minimum": 1},
                                },
                                "required": ["start", "end"],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["block_id", "type", "text", "bbox", "source"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["document_id", "page_number", "text", "layout_blocks"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
)


def register_document_agent_tools(registry: ToolRegistry) -> None:
    registry.register("document.get_manifest", get_manifest)
    registry.register("document.search", search_document)
    registry.register("document.get_page_text", get_page_text)
