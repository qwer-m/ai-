"""测试生成工作流的统一来源语义提取与确定性归并。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, TYPE_CHECKING

from modules.knowledge_base_components.document.document_asset_service import (
    document_page_layout,
    document_page_text,
    load_document_manifest,
)
from .context_compression import (
    compress_evidence_catalog,
    context_compression_enabled,
    context_compression_max_tokens,
    evidence_catalog_fingerprint,
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
_CROSS_FIELD_RELATION_VALUES = {
    *_FACT_STATUSES,
    "exact",
    "runtime_configured",
}
GOVERNANCE_RELATION_ALIASES = {
    "superseded_by": "replaces",
    "replaced_by": "replaces",
    "invalidated_by": "invalidates",
    "limited_by": "limits",
    "parameterized_by": "parameterizes",
}
_AUTHORITY_REVIEW_SIGNALS = (
    "替代",
    "作废",
    "废弃",
    "修订",
    "不再",
    "取消",
    "暂不",
    "非最终",
    "以配置为准",
    "以后台为准",
    "以运行时为准",
)
# 文本批次只承载连续两页，避免单个请求因正文和结构化事实过大触发上游网关超时。
_TEXT_PAGE_BATCH_SIZE = 2
# 文本批次还受正文长度约束，避免单个长批次触发上游网关超时。
_TEXT_PAGE_BATCH_CHAR_LIMIT = 800
# 单页布局块过多时，模型会为每个块展开结构化事实并截断 JSON；按真实块边界拆分请求。
_TEXT_PAGE_FRAGMENT_MAX_BLOCKS = 20
_MIN_TEXT_CHARS_WITHOUT_VISION = 160
_MAX_NON_TEXT_AREA_WITHOUT_VISION = 0.30


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _serialized_json_chars(value: Any) -> int:
    """按实际紧凑 JSON 序列化估算模型输入字符量。"""

    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _requires_visual_analysis(page: dict[str, Any], page_text: str) -> bool:
    """只把文本不足或非文本区域占主导的页面送入视觉模型。"""

    text_char_count = sum(not char.isspace() for char in page_text)
    non_text_area = 0.0
    for raw_block in list(page.get("blocks") or []):
        if not isinstance(raw_block, dict) or str(raw_block.get("type") or "") == "text_line":
            continue
        bbox = dict(raw_block.get("bbox") or {})
        non_text_area += float(bbox.get("width") or 0.0) * float(
            bbox.get("height") or 0.0
        )
    return (
        text_char_count < _MIN_TEXT_CHARS_WITHOUT_VISION
        or non_text_area >= _MAX_NON_TEXT_AREA_WITHOUT_VISION
    )


def _batch_text_document_pages(
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按连续页最多两页组成强文本模型任务，避免单请求过大。"""

    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for page in pages:
        page_number = int(page.get("page_number") or 0)
        page_chars = len(str(page.get("page_text") or ""))
        contiguous = not current or page_number == int(current[-1]["page_number"]) + 1
        exceeds_char_limit = (
            current
            and current_chars + page_chars > _TEXT_PAGE_BATCH_CHAR_LIMIT
        )
        if current and (
            not contiguous
            or len(current) >= _TEXT_PAGE_BATCH_SIZE
            or exceeds_char_limit
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(page)
        current_chars += page_chars
    if current:
        batches.append(current)

    result: list[dict[str, Any]] = []
    for batch in batches:
        result.append(
            {
                "source_kind": "document_batch",
                "document_id": int(batch[0]["document_id"]),
                "pages": batch,
            }
        )
    return result


def _page_scope_block_ids(
    page_input: dict[str, Any],
    page_scopes: list[dict[str, Any]],
) -> list[str]:
    """按原页顺序解析作用域实际覆盖的布局块。"""

    blocks = [dict(item) for item in list(page_input.get("blocks") or [])]
    block_ids = [
        str(block.get("block_id") or "")
        for block in blocks
        if str(block.get("block_id") or "")
    ]
    known = set(block_ids)
    selected = {
        str(value)
        for scope in page_scopes
        for value in list(scope.get("block_ids") or scope.get("allowed_block_ids") or [])
        if str(value).strip()
    }
    unknown = sorted(selected - known)
    if unknown:
        raise ValueError(
            "来源作用域引用了不存在的页面块: "
            f"page_number={page_input.get('page_number')}, block_ids={unknown[:10]}"
        )
    if selected:
        return [block_id for block_id in block_ids if block_id in selected]

    selected_by_span: set[str] = set()
    for scope in page_scopes:
        raw_scope = dict(scope.get("source_span") or {})
        start = int(scope.get("source_offset_start") or raw_scope.get("start") or 0)
        end = int(scope.get("source_offset_end") or raw_scope.get("end") or 0)
        selected_by_span.update(
            str(block.get("block_id") or "")
            for block in blocks
            if int(dict(block.get("source_span") or {}).get("start") or 0) < end
            and int(dict(block.get("source_span") or {}).get("end") or 0) > start
        )
    result = [block_id for block_id in block_ids if block_id in selected_by_span]
    if not result:
        raise ValueError(
            "来源作用域未命中任何页面块: "
            f"page_number={page_input.get('page_number')}"
        )
    return result


def _fragment_page_scopes(
    page_scopes: list[dict[str, Any]],
    fragment_block_ids: list[str],
) -> list[dict[str, Any]]:
    """把页级证据作用域裁到当前连续块组。"""

    selected = set(fragment_block_ids)
    result: list[dict[str, Any]] = []
    for raw_scope in page_scopes:
        scope = dict(raw_scope)
        scope_block_ids = [
            str(value)
            for value in list(scope.get("block_ids") or scope.get("allowed_block_ids") or [])
            if str(value).strip()
        ]
        allowed = [block_id for block_id in scope_block_ids if block_id in selected]
        if scope_block_ids and not allowed:
            continue
        scope["block_ids"] = allowed or list(fragment_block_ids)
        scope.pop("allowed_block_ids", None)
        result.append(scope)
    if not result:
        raise ValueError("文本页分片没有可用来源作用域")
    return result


def _text_page_model_views(
    *,
    page_input: dict[str, Any],
    page_scopes: list[dict[str, Any]],
    compression_enabled: bool,
) -> list[dict[str, Any]]:
    """将高密度文本页按连续真实布局块拆成独立模型视图。"""

    selected_block_ids = _page_scope_block_ids(page_input, page_scopes)
    if len(selected_block_ids) <= _TEXT_PAGE_FRAGMENT_MAX_BLOCKS:
        if not compression_enabled:
            return [page_input]
        return [
            _compressed_page_view(page_input=deepcopy(page_input), page_scopes=page_scopes)
        ]

    views: list[dict[str, Any]] = []
    for start in range(0, len(selected_block_ids), _TEXT_PAGE_FRAGMENT_MAX_BLOCKS):
        fragment_block_ids = selected_block_ids[
            start : start + _TEXT_PAGE_FRAGMENT_MAX_BLOCKS
        ]
        views.append(
            _compressed_page_view(
                page_input=deepcopy(page_input),
                page_scopes=_fragment_page_scopes(page_scopes, fragment_block_ids),
            )
        )
    return views


def _compression_model_catalog(
    context: ToolExecutionContext,
    evidence_catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """读取证据压缩视图；缺少前置 evidence 节点时按显式开关即时构建。"""

    raw_items = [dict(item) for item in list(evidence_catalog.get("items") or [])]
    artifacts = getattr(context, "artifacts", None)
    if not isinstance(artifacts, dict):
        artifacts = {}
        try:
            context.artifacts = artifacts
        except AttributeError:
            pass
    run_input = getattr(context, "run_input", {})
    if not isinstance(run_input, dict):
        run_input = {}
    artifact = dict(artifacts.get("context_compression") or {})
    current_max_tokens = context_compression_max_tokens(run_input)
    explicit_option = (
        "enable_context_compression" in run_input
        or "compress" in run_input
    )
    if not artifact and not explicit_option:
        # 直接调用工具的旧客户端没有压缩配置，保持历史输入不变。
        return raw_items, {
            "enabled": False,
            "selected_evidence_ids": [str(item.get("evidence_id") or "") for item in raw_items],
        }

    enabled = bool(
        artifact.get("enabled")
        if "enabled" in artifact
        else context_compression_enabled(run_input)
    )
    selected_ids = {
        str(value).strip()
        for value in list(artifact.get("selected_evidence_ids") or [])
        if str(value).strip()
    }
    raw_ids = [str(item.get("evidence_id") or "") for item in raw_items]
    current_catalog_fingerprint = evidence_catalog_fingerprint(evidence_catalog)
    artifact_raw_ids = [
        str(value).strip()
        for value in list(artifact.get("raw_evidence_ids") or [])
        if str(value).strip()
    ]
    artifact_catalog_fingerprint = str(
        artifact.get("evidence_catalog_fingerprint") or ""
    ).strip()
    def _artifact_budget(name: str) -> int | None:
        raw_value = artifact.get(name)
        try:
            return int(raw_value) if raw_value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    artifact_max_tokens = _artifact_budget("max_tokens")
    artifact_effective_max_tokens = _artifact_budget("effective_max_tokens")
    # 中文注释：选择结果允许是当前目录的子集，但只能复用明确绑定到同一
    # 目录版本的产物；旧产物缺少身份信息时必须重新计算，不能静默漏掉新增证据。
    catalog_identity_matches = bool(
        artifact_raw_ids
        and artifact_raw_ids == raw_ids
        and artifact_catalog_fingerprint
        and artifact_catalog_fingerprint == current_catalog_fingerprint
    )
    budget_identity_matches = bool(
        artifact_max_tokens == current_max_tokens
        and artifact_effective_max_tokens == current_max_tokens
    )
    # 中文注释：预算是压缩选择结果的一部分；调用方调整预算时不能继续
    # 使用旧排序/截断结果，即使目录指纹仍然一致。
    if (
        not catalog_identity_matches
        or not budget_identity_matches
        or not selected_ids
        or not selected_ids.issubset(set(raw_ids))
    ):
        compressed_catalog, stats = compress_evidence_catalog(
            evidence_catalog,
            enabled=enabled,
            max_tokens=current_max_tokens,
        )
        selected_ids = {
            str(item.get("evidence_id") or "")
            for item in list(compressed_catalog.get("items") or [])
        }
        artifact = {**artifact, **stats}
    # 压缩视图只保留已校验的原目录项，不接受 compressor 返回的自由文本。
    selected_items = [item for item in raw_items if str(item.get("evidence_id") or "") in selected_ids]
    if not selected_items:
        selected_items = raw_items
        selected_ids = set(raw_ids)
    artifact["enabled"] = bool(enabled)
    artifact["raw_evidence_ids"] = list(raw_ids)
    artifact["evidence_catalog_fingerprint"] = current_catalog_fingerprint
    artifact["selected_evidence_ids"] = [
        str(item.get("evidence_id") or "") for item in selected_items
    ]
    artifact["candidate_selected_evidence_ids"] = [
        str(value).strip()
        for value in list(artifact.get("candidate_selected_evidence_ids") or [])
        if str(value).strip()
    ]
    artifacts["context_compression"] = artifact
    try:
        context.artifacts = artifacts
    except AttributeError:
        pass
    return selected_items, artifact


def _page_store_key(document_id: int, page_number: int) -> str:
    return f"{int(document_id)}:{int(page_number)}"


def _project_page_marks(
    marks: list[dict[str, Any]],
    strikeout_spans: list[dict[str, Any]],
    block_map: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把标记坐标投影到压缩正文，保留可见的删除线语义。"""

    projected_marks: list[dict[str, Any]] = []
    for raw_mark in marks:
        mark = dict(raw_mark)
        target_ids = [
            str(value)
            for value in list(mark.get("target_block_ids") or [])
            if str(value) in block_map
        ]
        if not target_ids:
            continue
        local_spans: list[dict[str, int]] = []
        for raw_span in list(mark.get("target_source_spans") or []):
            span = dict(raw_span or {})
            start = int(span.get("start") or 0)
            end = int(span.get("end") or 0)
            for block_id in target_ids:
                mapping = block_map[block_id]
                original = dict(mapping["original_span"])
                overlap_start = max(start, int(original["start"]))
                overlap_end = min(end, int(original["end"]))
                if overlap_end <= overlap_start:
                    continue
                local = dict(mapping["local_span"])
                local_spans.append(
                    {
                        "start": int(local["start"]) + overlap_start - int(original["start"]),
                        "end": int(local["start"]) + overlap_end - int(original["start"]),
                    }
                )
        if not local_spans:
            continue
        mark["target_block_ids"] = target_ids
        mark["target_source_spans"] = local_spans
        projected_marks.append(mark)

    projected_strikeouts: list[dict[str, Any]] = []
    for raw_span in strikeout_spans:
        block_id = str(raw_span.get("block_id") or "")
        mapping = block_map.get(block_id)
        if mapping is None:
            continue
        span = dict(raw_span.get("source_span") or {})
        start = int(span.get("start") or 0)
        end = int(span.get("end") or 0)
        original = dict(mapping["original_span"])
        overlap_start = max(start, int(original["start"]))
        overlap_end = min(end, int(original["end"]))
        if overlap_end <= overlap_start:
            continue
        local = dict(mapping["local_span"])
        projected_strikeouts.append(
            {
                "block_id": block_id,
                "source_span": {
                    "start": int(local["start"]) + overlap_start - int(original["start"]),
                    "end": int(local["start"]) + overlap_end - int(original["start"]),
                },
            }
        )
    return projected_marks, projected_strikeouts


def _compressed_page_view(
    *,
    page_input: dict[str, Any],
    page_scopes: list[dict[str, Any]],
) -> dict[str, Any]:
    """用已选证据块构造模型输入，坐标在校验阶段由原页恢复。"""

    original_blocks = [dict(item) for item in list(page_input.get("blocks") or [])]
    selected_block_ids = {
        str(value)
        for scope in page_scopes
        for value in list(
            scope.get("block_ids")
            or scope.get("allowed_block_ids")
            or []
        )
        if str(value).strip()
    }
    selected_blocks = [
        block for block in original_blocks if str(block.get("block_id") or "") in selected_block_ids
    ]
    if selected_block_ids:
        known_block_ids = {
            str(block.get("block_id") or "")
            for block in original_blocks
            if str(block.get("block_id") or "")
        }
        unknown_block_ids = sorted(selected_block_ids - known_block_ids)
        if unknown_block_ids:
            raise ValueError(
                "压缩证据引用了不存在的页面块: "
                f"page_number={page_input.get('page_number')}, block_ids={unknown_block_ids[:10]}"
            )
    if not selected_blocks:
        # metadata 缺少 block_ids 时，用证据真实坐标选取对应布局块。
        for scope in page_scopes:
            scope_span = dict(scope.get("source_span") or {})
            raw_span = {
                "start": int(scope.get("source_offset_start") or scope_span.get("start") or 0),
                "end": int(scope.get("source_offset_end") or scope_span.get("end") or 0),
            }
            selected_blocks.extend(
                block
                for block in original_blocks
                if int(dict(block.get("source_span") or {}).get("start") or 0) < raw_span["end"]
                and int(dict(block.get("source_span") or {}).get("end") or 0) > raw_span["start"]
            )
    if not selected_blocks:
        raise ValueError(
            "压缩证据作用域未命中任何页面块: "
            f"page_number={page_input.get('page_number')}"
        )
    selected_blocks = list({str(block.get("block_id")): block for block in selected_blocks}.values())
    selected_blocks.sort(
        key=lambda block: (
            int(dict(block.get("source_span") or {}).get("start") or 0),
            str(block.get("block_id") or ""),
        )
    )

    model_blocks: list[dict[str, Any]] = []
    block_map: dict[str, dict[str, Any]] = {}
    text_parts: list[str] = []
    cursor = 0
    for block in selected_blocks:
        block_id = str(block.get("block_id") or "")
        text = str(block.get("text") or "")
        if not block_id or not text:
            continue
        if text_parts:
            text_parts.append("\n")
            cursor += 1
        local_span = {"start": cursor, "end": cursor + len(text)}
        original_span = dict(block.get("source_span") or {})
        model_blocks.append(
            {
                "block_id": block_id,
                "text": text,
                "source_span": local_span,
            }
        )
        block_map[block_id] = {
            "original_span": original_span,
            "local_span": local_span,
        }
        text_parts.append(text)
        cursor += len(text)
    if not model_blocks:
        raise ValueError(
            f"压缩证据视图没有可用页面块: page_number={page_input.get('page_number')}"
        )

    model_scopes: list[dict[str, Any]] = []
    selected_ids = set(block_map)

    def local_scope_span(
        scope: dict[str, Any],
        allowed_block_ids: list[str],
    ) -> dict[str, int] | None:
        """把证据作用域边界重定位到压缩正文，避免模型丢失范围约束。"""

        raw_scope = dict(scope.get("source_span") or {})
        try:
            raw_start = int(
                scope.get("source_offset_start")
                if scope.get("source_offset_start") not in (None, "")
                else raw_scope.get("start", 0)
            )
            raw_end = int(
                scope.get("source_offset_end")
                if scope.get("source_offset_end") not in (None, "")
                else raw_scope.get("end", 0)
            )
        except (TypeError, ValueError):
            return None
        projected: list[dict[str, int]] = []
        for block_id in allowed_block_ids:
            mapping = block_map.get(str(block_id))
            if mapping is None:
                continue
            original = dict(mapping["original_span"])
            local = dict(mapping["local_span"])
            overlap_start = max(raw_start, int(original["start"]))
            overlap_end = min(raw_end, int(original["end"]))
            if overlap_end <= overlap_start:
                continue
            projected.append(
                {
                    "start": int(local["start"]) + overlap_start - int(original["start"]),
                    "end": int(local["start"]) + overlap_end - int(original["start"]),
                }
            )
        if not projected:
            return None
        return {
            "start": min(item["start"] for item in projected),
            "end": max(item["end"] for item in projected),
        }

    for scope in page_scopes:
        scope_block_ids = list(
            scope.get("block_ids")
            or scope.get("allowed_block_ids")
            or []
        )
        allowed = [
            str(value)
            for value in scope_block_ids
            if str(value) in selected_ids
        ]
        if not allowed:
            scope_span = dict(scope.get("source_span") or {})
            raw_span = {
                "start": int(scope.get("source_offset_start") or scope_span.get("start") or 0),
                "end": int(scope.get("source_offset_end") or scope_span.get("end") or 0),
            }
            allowed = [
                str(block.get("block_id") or "")
                for block in selected_blocks
                if int(dict(block.get("source_span") or {}).get("start") or 0) < raw_span["end"]
                and int(dict(block.get("source_span") or {}).get("end") or 0) > raw_span["start"]
            ]
        if allowed:
            normalized_allowed = list(dict.fromkeys(allowed))
            model_scope = {
                "scope_id": str(scope.get("evidence_id") or scope.get("scope_id") or ""),
                "allowed_block_ids": normalized_allowed,
            }
            projected_span = local_scope_span(scope, normalized_allowed)
            if projected_span is not None:
                model_scope["source_span"] = projected_span
            model_scopes.append(model_scope)
    if not model_scopes:
        raise ValueError(
            "压缩证据作用域无法映射到模型页面: "
            f"page_number={page_input.get('page_number')}"
        )

    projected_marks, projected_strikeouts = _project_page_marks(
        list(page_input.get("marks") or []),
        list(page_input.get("strikeout_spans") or []),
        block_map,
    )
    return {
        **page_input,
        # 保留首尾空白，确保 blocks.source_span 与模型看到的 page_text 完全一致。
        "page_text": "".join(text_parts),
        "blocks": model_blocks,
        "marks": projected_marks,
        "strikeout_spans": projected_strikeouts,
        "source_scopes": model_scopes,
    }


def _page_coordinate_map(
    original_page: dict[str, Any],
    model_page: dict[str, Any],
) -> list[dict[str, dict[str, int] | str]]:
    """建立压缩页面块坐标到原页面块坐标的确定性映射。"""

    original_by_id = {
        str(block.get("block_id") or ""): dict(block)
        for block in list(original_page.get("blocks") or [])
        if isinstance(block, dict) and str(block.get("block_id") or "")
    }
    mappings: list[dict[str, dict[str, int] | str]] = []
    for raw_block in list(model_page.get("blocks") or []):
        if not isinstance(raw_block, dict):
            continue
        block_id = str(raw_block.get("block_id") or "")
        original = original_by_id.get(block_id)
        if original is None:
            continue
        try:
            local_span = _span(raw_block.get("source_span"), field_name="压缩块.source_span")
            original_span = _span(original.get("source_span"), field_name="原始块.source_span")
        except ValueError:
            continue
        mappings.append(
            {
                "block_id": block_id,
                "local_span": local_span,
                "original_span": original_span,
            }
        )
    return mappings


def _context_artifacts(context: ToolExecutionContext) -> dict[str, Any]:
    """读取运行 artifact，并兼容旧测试/旧工具上下文。"""

    artifacts = getattr(context, "artifacts", None)
    if not isinstance(artifacts, dict):
        artifacts = {}
        try:
            context.artifacts = artifacts
        except AttributeError:
            pass
    return artifacts


def _source_item_uses_compressed_coordinates(
    context: ToolExecutionContext,
    item: dict[str, Any],
) -> bool:
    """依据运行 artifact 判断模型坐标是否相对压缩页正文。"""

    artifacts = _context_artifacts(context)
    coordinate_maps = dict(artifacts.get("source_semantics_coordinate_maps") or {})
    source_kind = str(item.get("source_kind") or "")
    if source_kind == "document":
        key = _page_store_key(
            int(item.get("document_id") or 0),
            int(item.get("page_number") or 0),
        )
        return bool(coordinate_maps.get(key))
    if source_kind == "document_batch":
        return any(
            bool(coordinate_maps.get(
                _page_store_key(
                    int(dict(page).get("document_id") or item.get("document_id") or 0),
                    int(dict(page).get("page_number") or 0),
                )
            ))
            for page in list(item.get("pages") or [])
            if isinstance(page, dict)
        )
    return False


def _translate_model_span(
    raw_span: Any,
    mappings: list[dict[str, dict[str, int] | str]],
    *,
    block_ids: set[str] | None = None,
    assume_local: bool = False,
) -> dict[str, int] | None:
    """将压缩页面局部坐标转换为原页坐标；已是原页坐标时保持不变。"""

    if not isinstance(raw_span, dict):
        return None
    try:
        start = int(raw_span.get("start", -1))
        end = int(raw_span.get("end", -1))
    except (TypeError, ValueError):
        return None
    if start < 0 or end <= start:
        return None
    candidates = [
        item
        for item in mappings
        if block_ids is None or str(item.get("block_id") or "") in block_ids
    ]
    if not candidates:
        return None

    def local_span(item: dict[str, Any]) -> dict[str, int]:
        return dict(item["local_span"])

    def original_span(item: dict[str, Any]) -> dict[str, int]:
        return dict(item["original_span"])

    original_fits = [
        item
        for item in candidates
        if original_span(item)["start"] <= start
        and end <= original_span(item)["end"]
    ]
    local_fits = [
        item
        for item in candidates
        if local_span(item)["start"] <= start
        and end <= local_span(item)["end"]
    ]
    # 压缩输入按新契约优先解释为局部坐标；只落在原页坐标的旧结果仍保留，
    # 避免兼容旧 Agent 时发生二次平移。未压缩/兼容调用则优先原页坐标。
    if assume_local and original_fits and not local_fits:
        return {"start": start, "end": end}
    if not assume_local and original_fits:
        return {"start": start, "end": end}

    # 优先处理完全落在一个压缩块中的坐标，动态值通常属于此情形。
    containing = [
        item
        for item in candidates
        if local_span(item)["start"] <= start
        and end <= local_span(item)["end"]
    ]
    if containing:
        item = min(containing, key=lambda value: local_span(value)["end"] - local_span(value)["start"])
        local = local_span(item)
        original = original_span(item)
        return {
            "start": original["start"] + start - local["start"],
            "end": original["start"] + end - local["start"],
        }

    # 跨压缩块时使用首尾原始块的包络；中间被省略的正文会由原页 quote 恢复。
    overlapping = [
        item
        for item in candidates
        if local_span(item)["start"] < end and start < local_span(item)["end"]
    ]
    if overlapping:
        overlapping.sort(key=lambda value: local_span(value)["start"])
        first = overlapping[0]
        last = overlapping[-1]
        first_local = local_span(first)
        last_local = local_span(last)
        first_original = original_span(first)
        last_original = original_span(last)
        translated_start = first_original["start"] + max(0, start - first_local["start"])
        translated_end = last_original["start"] + min(
            last_original["end"] - last_original["start"],
            end - last_local["start"],
        )
        if translated_end > translated_start:
            return {"start": translated_start, "end": translated_end}

    # 模型已按原始页绝对坐标返回时，不重复平移。
    if any(
        dict(item["original_span"])["start"] <= start
        and end <= dict(item["original_span"])["end"]
        for item in candidates
    ):
        return {"start": start, "end": end}
    return None


def _translate_source_semantics_output(
    context: ToolExecutionContext,
    output: dict[str, Any],
    *,
    assume_local_coordinates: bool = False,
) -> dict[str, Any]:
    """在原页校验前转换压缩输入产生的局部锚点坐标。"""

    translated = deepcopy(output)
    artifacts = _context_artifacts(context)
    coordinate_maps = dict(artifacts.get("source_semantics_coordinate_maps") or {})
    source_pages = dict(artifacts.get("source_semantics_source_pages") or {})
    facts = translated.get("authoritative_facts")
    if not isinstance(facts, list):
        return translated
    for raw_fact in facts:
        if not isinstance(raw_fact, dict):
            continue
        anchor = raw_fact.get("source_anchor")
        if not isinstance(anchor, dict):
            continue
        # 已经过单项后处理的事实已经是原页坐标，避免 merge 阶段重复平移。
        normalized_fact = (
            "governed_value_spans" not in raw_fact
            and "governed_values" in raw_fact
            and "source_kind" in anchor
            and "quote" in anchor
        )
        if normalized_fact:
            continue
        document_id = int(anchor.get("document_id") or 0)
        page_number = int(anchor.get("page_number") or 0)
        if document_id < 1 or page_number < 1:
            continue
        key = _page_store_key(document_id, page_number)
        mappings = [
            dict(item)
            for item in list(coordinate_maps.get(key) or [])
            if isinstance(item, dict)
        ]
        if not mappings:
            continue
        raw_block_ids = anchor.get("block_id")
        if isinstance(raw_block_ids, list):
            block_ids = {str(value) for value in raw_block_ids if str(value).strip()}
        elif raw_block_ids is None:
            block_ids = set()
        else:
            block_ids = {str(raw_block_ids)}
        block_filter = block_ids or None
        source_span = _translate_model_span(
            anchor.get("source_span"),
            mappings,
            block_ids=block_filter,
            assume_local=assume_local_coordinates,
        )
        if source_span is not None:
            anchor["source_span"] = source_span
            original_page = source_pages.get(key)
            if isinstance(original_page, dict):
                page_text = str(original_page.get("page_text") or "")
                if source_span["end"] <= len(page_text):
                    # 中文注释：quote 由可信原页切片生成，不采用模型压缩文本作为来源证据。
                    anchor["quote"] = page_text[source_span["start"] : source_span["end"]]
        governed_spans = raw_fact.get("governed_value_spans")
        if isinstance(governed_spans, list):
            mapped_spans: list[dict[str, int]] = []
            for raw_span in governed_spans:
                mapped = _translate_model_span(
                    raw_span,
                    mappings,
                    block_ids=block_filter,
                    assume_local=assume_local_coordinates,
                )
                # 分片后同一原页会有多套局部坐标；无法由当前事实块
                # 唯一映射的坐标不得原样写回，否则会被误当成整页坐标并扩大来源范围。
                if mapped is not None:
                    mapped_spans.append(mapped)
            raw_fact["governed_value_spans"] = mapped_spans
    return translated


def _hydrate_source_semantics_item(
    context: ToolExecutionContext,
    item: dict[str, Any],
) -> dict[str, Any]:
    """用运行 artifact 中的真实页恢复压缩输入，供平台确定性校验。"""

    artifacts = _context_artifacts(context)
    stores = dict(artifacts.get("source_semantics_source_pages") or {})

    def hydrate_page(page: dict[str, Any]) -> dict[str, Any]:
        document_id = int(page.get("document_id") or 0)
        page_number = int(page.get("page_number") or 0)
        original = stores.get(_page_store_key(document_id, page_number))
        if not isinstance(original, dict):
            return page
        hydrated = deepcopy(original)
        selected_scopes = [
            dict(scope)
            for scope in list(page.get("source_scopes") or [])
            if isinstance(scope, dict) and str(scope.get("scope_id") or "")
        ]
        if selected_scopes:
            original_scopes = {
                str(scope.get("scope_id") or ""): dict(scope)
                for scope in list(original.get("source_scopes") or [])
                if isinstance(scope, dict) and str(scope.get("scope_id") or "")
            }
            original_blocks = {
                str(block.get("block_id") or ""): dict(block)
                for block in list(original.get("blocks") or [])
                if isinstance(block, dict) and str(block.get("block_id") or "")
            }
            hydrated_scopes: list[dict[str, Any]] = []
            for selected_scope in selected_scopes:
                scope_id = str(selected_scope.get("scope_id") or "")
                original_scope = original_scopes.get(scope_id)
                if original_scope is None:
                    raise ValueError(f"压缩来源作用域不存在于原页: scope_id={scope_id}")
                allowed = [
                    str(value)
                    for value in list(selected_scope.get("allowed_block_ids") or [])
                    if str(value) in original_blocks
                ]
                hydrated_scope = dict(original_scope)
                if allowed:
                    spans = [
                        _span(
                            original_blocks[block_id].get("source_span"),
                            field_name=f"{block_id}.source_span",
                        )
                        for block_id in allowed
                    ]
                    hydrated_scope["allowed_block_ids"] = allowed
                    hydrated_scope["source_span"] = {
                        "start": min(span["start"] for span in spans),
                        "end": max(span["end"] for span in spans),
                    }
                hydrated_scopes.append(hydrated_scope)
            hydrated["source_scopes"] = hydrated_scopes
        return hydrated

    source_kind = str(item.get("source_kind") or "")
    if source_kind == "document":
        return hydrate_page(dict(item))
    if source_kind == "document_batch":
        hydrated = dict(item)
        hydrated["pages"] = [hydrate_page(dict(page)) for page in list(item.get("pages") or [])]
        return hydrated
    return item


def _hydrate_semantic_arguments(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    hydrated = deepcopy(arguments)
    for key in ("semantic_inputs", "text_inputs", "vision_inputs"):
        values = hydrated.get(key)
        if isinstance(values, list):
            hydrated[key] = [
                _hydrate_source_semantics_item(context, dict(value))
                if isinstance(value, dict)
                else value
                for value in values
            ]
    for key in ("semantic_records", "text_records", "vision_records"):
        values = hydrated.get(key)
        if not isinstance(values, list):
            continue
        translated_records: list[Any] = []
        for value in values:
            if not isinstance(value, dict):
                translated_records.append(value)
                continue
            record = dict(value)
            output = record.get("output")
            if isinstance(output, dict):
                record["output"] = _translate_source_semantics_output(context, output)
            translated_records.append(record)
        hydrated[key] = translated_records
    return hydrated


def _validated_source_quote(
    value: Any,
    *,
    source_text: str,
    field_name: str,
) -> str:
    """校验模型引用并返回真实坐标中的规范原文。"""

    if isinstance(value, list):
        if not value:
            raise ValueError(f"{field_name} 数组不能为空")
        parts: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item:
                raise ValueError(f"{field_name}[{index}] 必须是非空字符串")
            parts.append(item)
        cursor = 0
        for index, part in enumerate(parts):
            position = source_text.find(part, cursor)
            if position < 0 or source_text[cursor:position].strip():
                raise ValueError(f"{field_name}[{index}] 未按顺序精确命中来源坐标")
            cursor = position + len(part)
        if source_text[cursor:].strip():
            raise ValueError(f"{field_name} 数组未完整覆盖来源坐标")
        return source_text
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} 必须是非空字符串")
    if value != source_text:
        raise ValueError(f"{field_name} 未精确命中来源坐标")
    return source_text


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
    artifacts = _context_artifacts(context)
    catalog_items = [dict(item) for item in evidence_catalog["items"]]
    model_catalog_items, compression_info = _compression_model_catalog(
        context,
        evidence_catalog,
    )
    model_catalog_ids = [
        _required_text(item.get("evidence_id"), "压缩证据 evidence_id")
        for item in model_catalog_items
    ]
    catalog_evidence_ids = [
        _required_text(item.get("evidence_id"), "evidence_id") for item in catalog_items
    ]
    if not catalog_evidence_ids:
        raise ValueError("evidence_catalog.items 不能为空")
    if len(catalog_evidence_ids) != len(set(catalog_evidence_ids)):
        raise ValueError("evidence_catalog.items 存在重复 evidence_id")

    source_kind = str(source.get("kind") or "").strip()
    fragmented_page_count = 0
    text_fragment_count = 0
    max_text_fragment_blocks = 0
    max_text_fragment_json_chars = 0
    if source_kind == "inline":
        requirement_sha256 = _sha256_text(requirement)
        if str(source.get("content_hash") or "").lower() != requirement_sha256:
            raise ValueError("纯文本需求指纹与真实输入不一致")
        text_items = [
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
                    for item in model_catalog_items
                ],
            }
        ]
        vision_items: list[dict[str, Any]] = []
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
        text_pages: list[dict[str, Any]] = []
        vision_items = []
        raw_text_pages: list[dict[str, Any]] = []
        raw_vision_pages: list[dict[str, Any]] = []
        seen_pages: set[int] = set()
        prepared_evidence_ids: list[str] = []
        source_page_store: dict[str, dict[str, Any]] = {}
        source_coordinate_maps: dict[str, list[dict[str, Any]]] = {}
        raw_source_chars = 0
        model_source_chars = 0
        raw_block_count = 0
        model_block_count = 0
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
            raw_page_scopes = [
                item
                for item in catalog_items
                if int(item.get("page_number") or 0) == page_number
            ]
            page_scopes = [
                item
                for item in model_catalog_items
                if int(item.get("page_number") or 0) == page_number
            ]
            if not raw_page_scopes:
                raise ValueError(f"文档页面缺少原始来源作用域: page_number={page_number}")
            if not page_scopes:
                raise ValueError(f"文档页面缺少来源作用域: page_number={page_number}")
            prepared_evidence_ids.extend(
                _required_text(item.get("evidence_id"), "evidence_id")
                for item in page_scopes
            )
            raw_page_input = {
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
                            "allowed_block_ids": [
                                str(value) for value in list(item.get("block_ids") or [])
                            ],
                            "source_span": {
                                "start": int(item.get("source_offset_start") or 0),
                                "end": int(item.get("source_offset_end") or 0),
                            },
                        }
                        for item in raw_page_scopes
                    ],
                }
            if bool(compression_info.get("enabled")):
                # 中文注释：模型请求使用块级压缩视图；只有正文/块/标记真的发生
                # 变化时才保存原页，避免“启用但无删减”的普通页面膨胀运行上下文。
                model_page_input = deepcopy(raw_page_input)
                model_page_input["source_scopes"] = [
                    {
                        "scope_id": str(item.get("evidence_id") or ""),
                        "allowed_block_ids": [
                            str(value) for value in list(item.get("block_ids") or [])
                        ],
                        "source_span": {
                            "start": int(item.get("source_offset_start") or 0),
                            "end": int(item.get("source_offset_end") or 0),
                        },
                    }
                    for item in page_scopes
                ]
                page_input = _compressed_page_view(
                    page_input=model_page_input,
                    page_scopes=page_scopes,
                )
                view_changed = any(
                    page_input.get(field) != raw_page_input.get(field)
                    for field in (
                        "page_text",
                        "blocks",
                        "marks",
                        "strikeout_spans",
                    )
                )
                if view_changed:
                    page_key = _page_store_key(document_id, page_number)
                    source_page_store[page_key] = deepcopy(raw_page_input)
                    source_coordinate_maps[page_key] = _page_coordinate_map(
                        raw_page_input,
                        page_input,
                    )
            else:
                page_input = raw_page_input
            raw_source_chars += len(page_text)
            raw_block_count += len(blocks)
            if _requires_visual_analysis(page, page_text):
                vision_items.append(page_input)
                raw_vision_pages.append(raw_page_input)
                model_source_chars += len(str(page_input.get("page_text") or ""))
                model_block_count += len(list(page_input.get("blocks") or []))
            else:
                page_views = _text_page_model_views(
                    page_input=raw_page_input,
                    page_scopes=page_scopes,
                    compression_enabled=bool(compression_info.get("enabled")),
                )
                text_pages.extend(page_views)
                raw_text_pages.append(raw_page_input)
                text_fragment_count += len(page_views)
                if len(page_views) > 1:
                    fragmented_page_count += 1
                max_text_fragment_blocks = max(
                    max_text_fragment_blocks,
                    *(len(list(view.get("blocks") or [])) for view in page_views),
                )
                max_text_fragment_json_chars = max(
                    max_text_fragment_json_chars,
                    *(_serialized_json_chars(view) for view in page_views),
                )
                model_source_chars += sum(
                    len(str(view.get("page_text") or "")) for view in page_views
                )
                model_block_count += sum(
                    len(list(view.get("blocks") or [])) for view in page_views
                )
                if len(page_views) > 1:
                    page_key = _page_store_key(document_id, page_number)
                    source_page_store[page_key] = deepcopy(raw_page_input)
                    combined_mappings = [
                        mapping
                        for view in page_views
                        for mapping in _page_coordinate_map(raw_page_input, view)
                    ]
                    source_coordinate_maps[page_key] = sorted(
                        combined_mappings,
                        key=lambda mapping: (
                            int(dict(mapping.get("original_span") or {}).get("start") or 0),
                            str(mapping.get("block_id") or ""),
                        ),
                    )
        if prepared_evidence_ids != model_catalog_ids:
            raise ValueError("文档来源作用域顺序或覆盖范围与证据目录不一致")
        if len(seen_pages) != int(manifest.get("page_count") or 0):
            raise ValueError("manifest.page_count 与真实页面清单不一致")
        text_items = _batch_text_document_pages(text_pages)
        raw_text_items = _batch_text_document_pages(raw_text_pages)
        raw_model_inputs = [*raw_text_items, *raw_vision_pages]
        model_inputs = [*text_items, *vision_items]
        raw_input_payload_chars = _serialized_json_chars(raw_model_inputs)
        model_input_payload_chars = _serialized_json_chars(model_inputs)
        if source_page_store:
            artifacts["source_semantics_source_pages"] = source_page_store
            artifacts["source_semantics_coordinate_maps"] = source_coordinate_maps
    else:
        raise ValueError(f"不支持的需求来源: kind={source_kind}")

    if source_kind == "inline":
        raw_source_chars = len(requirement)
        model_source_chars = len(requirement)
        raw_block_count = 0
        model_block_count = 0
        raw_input_payload_chars = _serialized_json_chars(
            {
                "text_items": [
                    {
                        "source_kind": "inline",
                        "requirement": requirement,
                        "requirement_sha256": _sha256_text(requirement),
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
            }
        )
        model_input_payload_chars = _serialized_json_chars(
            {"text_items": text_items, "vision_items": vision_items}
        )
    item_count = len(text_items) + len(vision_items)
    compression_info = dict(artifacts.get("context_compression") or compression_info)
    compression_info.update(
        {
            "model_catalog_count": len(model_catalog_items),
            "model_catalog_chars": sum(
                len(str(item.get("text") or "")) for item in model_catalog_items
            ),
            "raw_catalog_count": len(catalog_items),
            "raw_catalog_chars": sum(
                len(str(item.get("text") or "")) for item in catalog_items
            ),
            "raw_source_chars": raw_source_chars,
            "model_source_chars": model_source_chars,
            "raw_block_count": raw_block_count,
            "model_block_count": model_block_count,
            "fragmented_page_count": fragmented_page_count,
            "text_fragment_count": text_fragment_count,
            "max_text_fragment_blocks": max_text_fragment_blocks,
            "max_text_fragment_json_chars": max_text_fragment_json_chars,
            "model_input_reduction_ratio": round(
                (raw_source_chars - model_source_chars) / raw_source_chars,
                6,
            ) if raw_source_chars else 0.0,
            "raw_input_payload_chars": int(raw_input_payload_chars),
            "model_input_payload_chars": int(model_input_payload_chars),
            "serialized_input_reduction_ratio": round(
                (raw_input_payload_chars - model_input_payload_chars)
                / raw_input_payload_chars,
                6,
            ) if raw_input_payload_chars else 0.0,
            "model_reduction_applied": bool(
                compression_info.get("enabled")
                and model_input_payload_chars < raw_input_payload_chars
            ),
            "compression_mode": (
                "lossless_structural"
                if compression_info.get("enabled")
                and model_input_payload_chars < raw_input_payload_chars
                else "full_authoritative"
                if compression_info.get("enabled")
                else "disabled"
            ),
            "model_view_mode": (
                "lossless_structural"
                if compression_info.get("enabled")
                and model_input_payload_chars < raw_input_payload_chars
                else "full_authoritative"
                if compression_info.get("enabled")
                else "disabled"
            ),
            "compression_reason": (
                "page_blocks_or_metadata_reduced"
                if compression_info.get("enabled")
                and model_input_payload_chars < raw_input_payload_chars
                else "authoritative_source_retained"
                if compression_info.get("enabled")
                else "disabled_by_run_input"
            ),
        }
    )
    artifacts["context_compression"] = compression_info
    artifacts["source_semantics_prepare"] = {
        "source_kind": source_kind,
        "item_count": item_count,
        "text_item_count": len(text_items),
        "vision_item_count": len(vision_items),
        "compression_enabled": bool(compression_info.get("enabled")),
        "model_catalog_count": len(model_catalog_items),
        "raw_catalog_count": len(catalog_items),
        "fragmented_page_count": fragmented_page_count,
        "text_fragment_count": text_fragment_count,
        "max_text_fragment_blocks": max_text_fragment_blocks,
        "max_text_fragment_json_chars": max_text_fragment_json_chars,
    }
    return {
        "text_items": text_items,
        "vision_items": vision_items,
        "item_count": item_count,
        "text_item_count": len(text_items),
        "vision_item_count": len(vision_items),
        "source_kind": source_kind,
    }


def _validated_document_anchor(
    anchor: dict[str, Any],
    prepared: dict[str, Any],
) -> tuple[dict[str, Any], bool, set[str]]:
    document_id = int(anchor.get("document_id") or 0)
    page_number = int(anchor.get("page_number") or 0)
    raw_block_ids = anchor.get("block_id")
    if isinstance(raw_block_ids, list):
        block_ids = [
            _required_text(value, "source_anchor.block_id[]")
            for value in raw_block_ids
        ]
        if not block_ids or len(block_ids) != len(set(block_ids)):
            raise ValueError(
                "source_anchor.block_id 数组必须包含至少一个不重复的页面块"
            )
    elif raw_block_ids is not None:
        block_ids = [_required_text(raw_block_ids, "source_anchor.block_id")]
    else:
        block_ids = []
    if document_id != int(prepared["document_id"]) or page_number != int(
        prepared["page_number"]
    ):
        raise ValueError("来源事实的 document_id/page_number 与分析输入不一致")
    asset_hash = str(anchor.get("asset_source_sha256") or prepared["asset_source_sha256"]).strip().lower()
    page_hash = str(anchor.get("page_image_sha256") or prepared["page_image_sha256"]).strip().lower()
    if asset_hash != str(prepared["asset_source_sha256"]) or page_hash != str(
        prepared["page_image_sha256"]
    ):
        raise ValueError("来源事实的资产指纹与分析输入不一致")
    blocks = {str(item["block_id"]): dict(item) for item in prepared["blocks"]}
    unknown_blocks = set(block_ids) - set(blocks)
    if unknown_blocks:
        raise ValueError(f"来源事实引用了未知页面块: blocks={sorted(unknown_blocks)}")
    if block_ids:
        block_order = {block_id: index for index, block_id in enumerate(blocks)}
        block_ids = sorted(block_ids, key=block_order.__getitem__)
    raw_source_span = anchor.get("source_span")
    page_text = str(prepared["page_text"])
    try:
        fact_span = _span(raw_source_span, field_name="source_anchor.source_span")
    except ValueError:
        # 个别模型会把同一范围的 start/end 反写；仅换位明确的非负反向坐标，后续仍需通过真实块和作用域校验。
        if (
            isinstance(raw_source_span, dict)
            and isinstance(raw_source_span.get("start"), int)
            and isinstance(raw_source_span.get("end"), int)
            and raw_source_span["start"] >= 0
            and raw_source_span["end"] >= 0
            and raw_source_span["start"] > raw_source_span["end"]
        ):
            fact_span = _span(
                {
                    "start": raw_source_span["end"],
                    "end": raw_source_span["start"],
                },
                field_name="source_anchor.source_span",
            )
        elif (
            isinstance(raw_source_span, dict)
            and isinstance(raw_source_span.get("start"), int)
            and isinstance(raw_source_span.get("end"), int)
            and raw_source_span["start"] >= 0
            and raw_source_span["start"] == raw_source_span["end"]
            and isinstance(anchor.get("quote"), str)
            and bool(anchor["quote"])
            and page_text.find(anchor["quote"]) == raw_source_span["start"]
            and page_text.find(anchor["quote"], raw_source_span["start"] + 1) < 0
        ):
            # 仅用唯一命中的真实原文补齐遗漏的终点，后续仍执行全部锚点校验。
            fact_span = _span(
                {
                    "start": raw_source_span["start"],
                    "end": raw_source_span["start"] + len(anchor["quote"]),
                },
                field_name="source_anchor.source_span",
            )
        elif raw_source_span is None:
            # 模型不再需要计算整页绝对坐标：使用页面块和逐字引用在真实正文中唯一定位。
            raw_quote = anchor.get("quote")
            if not isinstance(raw_quote, str) or not raw_quote:
                # 模型只负责选择真实块；规范坐标和引用由平台从连续页面块生成。
                if not block_ids:
                    raise
                selected_spans = [
                    dict(blocks[block_id]["source_span"])
                    for block_id in block_ids
                ]
                block_envelope = {
                    "start": min(int(span["start"]) for span in selected_spans),
                    "end": max(int(span["end"]) for span in selected_spans),
                }
                envelope_block_ids = [
                    block_id
                    for block_id, block in blocks.items()
                    if _overlaps(block_envelope, dict(block["source_span"]))
                ]
                if envelope_block_ids != block_ids:
                    raise ValueError(
                        "source_anchor.block_id 必须按页面顺序选择连续页面块: "
                        f"declared={block_ids}, covered={envelope_block_ids}"
                    )
                fact_span = _span(
                    block_envelope,
                    field_name="source_anchor.source_span",
                )
                raw_quote = page_text[fact_span["start"] : fact_span["end"]]
                if not raw_quote:
                    raise ValueError(
                        f"来源页面块正文为空，无法生成 quote: block_id={block_ids}"
                    )
                # 后续统一执行页面块和证据作用域校验。
                quote_starts = [fact_span["start"]]
            else:
                quote_starts = []
            if not quote_starts:
                search_from = 0
                while True:
                    quote_start = page_text.find(raw_quote, search_from)
                    if quote_start < 0:
                        break
                    quote_starts.append(quote_start)
                    search_from = quote_start + 1
            if not quote_starts:
                # 允许终审判断模型对单个真实页面块的合理扩展；来源边界仍由唯一 block_id 锁定，
                # 规范锚点使用真实块正文，避免把模型拼接文本写入公开来源契约。
                if len(block_ids) != 1 or block_ids[0] not in blocks:
                    raise ValueError(
                        "source_anchor.quote 必须逐字命中页面正文，且非原文引用只能绑定单个真实 block_id: "
                        f"quote={raw_quote[:80]!r}, block_id={block_ids}"
                    )
                fact_span = _span(
                    dict(blocks[block_ids[0]]["source_span"]),
                    field_name="source_anchor.source_span",
                )
                raw_quote = page_text[fact_span["start"] : fact_span["end"]]
                quote_starts = [fact_span["start"]]
                anchor["quote"] = raw_quote
            if len(quote_starts) > 1:
                # 同一页可能重复出现相同文案；使用模型声明的真实布局块做确定性消歧。
                declared_blocks = set(block_ids)
                matched_spans = []
                for start in quote_starts:
                    candidate = {"start": start, "end": start + len(raw_quote)}
                    covered = {
                        block_id
                        for block_id, block in blocks.items()
                        if _overlaps(candidate, dict(block["source_span"]))
                    }
                    if declared_blocks and covered == declared_blocks:
                        matched_spans.append(candidate)
                if len(matched_spans) != 1:
                    raise ValueError(
                        "source_anchor.quote 在页面中重复，且无法由 block_id 唯一消歧: "
                        f"quote={raw_quote[:80]!r}, block_id={block_ids}"
                    )
                fact_span = _span(
                    matched_spans[0],
                    field_name="source_anchor.source_span",
                )
            else:
                fact_span = _span(
                    {"start": quote_starts[0], "end": quote_starts[0] + len(raw_quote)},
                    field_name="source_anchor.source_span",
                )
        else:
            raise
    if fact_span["end"] > len(page_text):
        raise ValueError(
            "source_anchor.source_span 超出页面正文长度: "
            f"start={fact_span['start']}, end={fact_span['end']}, "
            f"page_text_length={len(page_text)}"
        )
    page_quote = page_text[fact_span["start"] : fact_span["end"]]
    raw_quote = anchor.get("quote")
    quote = _validated_source_quote(
        page_quote if raw_quote in (None, "") else raw_quote,
        source_text=page_quote,
        field_name="source_anchor.quote",
    )
    covered_block_ids = [
        block_id
        for block_id, block in blocks.items()
        if _overlaps(fact_span, dict(block["source_span"]))
    ]
    if not covered_block_ids:
        raise ValueError("来源事实 source_span 未命中任何页面块")
    if not block_ids:
        block_ids = covered_block_ids
    covered_blocks = set(covered_block_ids)
    if covered_blocks != set(block_ids):
        raise ValueError(
            "来源事实 block_id 与 source_span 覆盖范围不一致: "
            f"declared={block_ids}, covered={sorted(covered_blocks)}"
        )
    superseded_by_mark = any(
        (
            not str(mark.get("block_id") or "")
            or str(mark["block_id"]) in set(block_ids)
        )
        and _overlaps(fact_span, dict(mark["source_span"]))
        for mark in list(prepared.get("strikeout_spans") or [])
    )
    source_scopes = [
        dict(scope)
        for scope in list(prepared.get("source_scopes") or [])
        if str(scope.get("scope_id") or "").strip()
        and set(block_ids).issubset(set(scope.get("allowed_block_ids") or []))
        and _overlaps(fact_span, dict(scope.get("source_span") or {}))
    ]
    if not source_scopes:
        raise ValueError(f"来源事实未命中证据作用域: blocks={block_ids}")
    normalized_anchor: dict[str, Any] = {
        "source_kind": "document",
        "document_id": document_id,
        "page_number": page_number,
        "source_span": fact_span,
        "quote": quote,
        "asset_source_sha256": asset_hash,
        "page_image_sha256": page_hash,
    }
    if len(block_ids) == 1:
        normalized_anchor["block_id"] = block_ids[0]
    else:
        # 规范契约沿用 source_anchor.block_id 的数组形式，避免引入未声明的 block_ids 字段。
        normalized_anchor["block_id"] = block_ids
    return (
        normalized_anchor,
        superseded_by_mark,
        {str(scope["scope_id"]) for scope in source_scopes},
    )


def _governed_value_spans_for_materialization(
    *,
    fact: dict[str, Any],
) -> list[dict[str, int]] | None:
    """按模型选择的事实策略返回待平台切片的真实来源坐标。"""

    raw_spans = fact.get("governed_value_spans")
    if raw_spans is None:
        return None
    fact_id = str(fact.get("fact_id") or "<缺少 fact_id>").strip()
    if not isinstance(raw_spans, list):
        raise ValueError(f"governed_value_spans 必须是数组: fact_id={fact_id}")
    if str(fact.get("value_policy") or "").strip() == "exact":
        return []
    return [
        _span(raw_span, field_name="governed_value_spans")
        for raw_span in raw_spans
    ]


def _expanded_anchor_for_governed_values(
    anchor: dict[str, Any],
    *,
    fact: dict[str, Any],
    prepared: dict[str, Any],
) -> dict[str, Any]:
    """用紧邻的真实值坐标扩展事实锚点，保证值和来源原文保持同一追踪边界。"""

    value_spans = _governed_value_spans_for_materialization(fact=fact)
    if not value_spans:
        return anchor
    source_kind = str(prepared.get("source_kind") or "")
    try:
        if source_kind == "document":
            source_text = str(prepared.get("page_text") or "")
            anchor_span = _span(
                anchor.get("source_span"),
                field_name="source_anchor.source_span",
            )
        elif source_kind == "inline":
            source_text = str(prepared.get("requirement") or "")
            anchor_span = _span(
                {
                    "start": anchor.get("source_offset_start"),
                    "end": anchor.get("source_offset_end"),
                },
                field_name="source_anchor",
            )
        else:
            return anchor
    except ValueError:
        return anchor
    if any(span["end"] > len(source_text) for span in value_spans):
        return anchor
    segments = sorted([anchor_span, *value_spans], key=lambda item: (item["start"], item["end"]))
    expanded_span = {
        "start": min(segment["start"] for segment in segments),
        "end": max(segment["end"] for segment in segments),
    }
    if expanded_span == anchor_span:
        return anchor
    if source_kind == "document":
        covered_blocks = sorted(
            (
                _span(dict(block.get("source_span") or {}), field_name="block.source_span")
                for block in list(prepared.get("blocks") or [])
                if _overlaps(expanded_span, dict(block.get("source_span") or {}))
            ),
            key=lambda item: (item["start"], item["end"]),
        )
        cursor = expanded_span["start"]
        for block_span in covered_blocks:
            gap_end = min(block_span["start"], expanded_span["end"])
            if gap_end > cursor and source_text[cursor:gap_end].strip():
                return anchor
            cursor = max(cursor, min(block_span["end"], expanded_span["end"]))
        if cursor < expanded_span["end"] and source_text[cursor : expanded_span["end"]].strip():
            return anchor
    else:
        cursor = segments[0]["end"]
        for segment in segments[1:]:
            if segment["start"] > cursor and source_text[cursor : segment["start"]].strip():
                return anchor
            cursor = max(cursor, segment["end"])
    expanded = dict(anchor)
    expanded["quote"] = source_text[expanded_span["start"] : expanded_span["end"]]
    if source_kind == "inline":
        expanded["source_offset_start"] = expanded_span["start"]
        expanded["source_offset_end"] = expanded_span["end"]
        return expanded
    expanded["source_span"] = expanded_span
    expanded["block_id"] = [
        str(block.get("block_id") or "")
        for block in list(prepared.get("blocks") or [])
        if _overlaps(expanded_span, dict(block.get("source_span") or {}))
    ]
    return expanded


def _validated_inline_anchor(
    anchor: dict[str, Any],
    prepared: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    requirement = str(prepared["requirement"])
    requirement_sha256 = str(
        anchor.get("requirement_sha256") or prepared["requirement_sha256"]
    ).strip().lower()
    if requirement_sha256 != str(prepared["requirement_sha256"]):
        raise ValueError("纯文本事实的 requirement_sha256 与分析输入不一致")
    start = int(anchor.get("source_offset_start", -1))
    end = int(anchor.get("source_offset_end", -1))
    if start < 0 or end <= start or end > len(requirement):
        raise ValueError("纯文本事实的来源坐标无效")
    quote = _validated_source_quote(
        anchor.get("quote", requirement[start:end]),
        source_text=requirement[start:end],
        field_name="source_anchor.quote",
    )
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


def _fact_namespace(prepared: dict[str, Any]) -> str:
    """根据真实来源身份生成事实 ID 命名空间。

    来源 Agent 在并行页面中各自从局部编号开始（例如都返回 F001），因此
    合并前必须把局部编号提升为平台级稳定 ID。命名空间只依赖平台已经校验
    过的真实来源字段，不依赖模型输出或处理顺序。
    """

    source_kind = str(prepared.get("source_kind") or "").strip()
    if source_kind == "document":
        document_id = int(prepared.get("document_id") or 0)
        page_number = int(prepared.get("page_number") or 0)
        if document_id < 1 or page_number < 1:
            raise ValueError("文档事实命名空间缺少有效 document_id/page_number")
        namespace = f"DOC{document_id}-P{page_number:04d}"
        blocks = {
            str(block.get("block_id") or ""): dict(block)
            for block in list(prepared.get("blocks") or [])
            if isinstance(block, dict) and str(block.get("block_id") or "")
        }
        allowed_block_ids = {
            str(value)
            for scope in list(prepared.get("source_scopes") or [])
            if isinstance(scope, dict)
            for value in list(scope.get("allowed_block_ids") or [])
            if str(value) in blocks
        }
        if allowed_block_ids and allowed_block_ids != set(blocks):
            spans = [
                _span(
                    blocks[block_id].get("source_span"),
                    field_name=f"{block_id}.source_span",
                )
                for block_id in allowed_block_ids
            ]
            namespace += (
                f"-S{min(span['start'] for span in spans)}"
                f"-E{max(span['end'] for span in spans)}"
            )
        return namespace
    if source_kind == "inline":
        requirement_sha256 = _required_text(
            prepared.get("requirement_sha256"), "requirement_sha256"
        ).lower()
        scopes = [
            dict(scope)
            for scope in list(prepared.get("source_scopes") or [])
            if isinstance(scope, dict)
        ]
        if not scopes:
            raise ValueError("纯文本事实命名空间缺少 source_scopes")
        starts: list[int] = []
        ends: list[int] = []
        for scope in scopes:
            if "source_offset_start" in scope or "source_offset_end" in scope:
                span = _span(
                    {
                        "start": scope.get("source_offset_start"),
                        "end": scope.get("source_offset_end"),
                    },
                    field_name="source_scope",
                )
            else:
                span = _span(scope.get("source_span"), field_name="source_scope")
            starts.append(span["start"])
            ends.append(span["end"])
        return f"INLINE-{requirement_sha256}-S{min(starts)}-E{max(ends)}"
    raise ValueError(f"不支持的事实来源类型: {source_kind}")


def _prepared_source_for_fact(
    prepared: dict[str, Any],
    source_anchor: dict[str, Any],
) -> dict[str, Any]:
    """把文本批次中的事实还原到唯一真实页面，供统一锚点校验。"""

    source_kind = str(prepared.get("source_kind") or "")
    if source_kind != "document_batch":
        return prepared
    page_number = int(source_anchor.get("page_number") or 0)
    matches = [
        dict(page)
        for page in list(prepared.get("pages") or [])
        if int(dict(page).get("page_number") or 0) == page_number
    ]
    if len(matches) != 1:
        raise ValueError(
            f"批量来源事实未命中唯一页面: page_number={page_number}, matches={len(matches)}"
        )
    page = matches[0]
    page["source_kind"] = "document"
    return page


def _canonical_fact_id(namespace: str, local_fact_id: str) -> str:
    """将来源 Agent 的局部事实 ID 转为幂等的平台级 ID。"""

    prefix = f"{namespace}-"
    return local_fact_id if local_fact_id.startswith(prefix) else f"{prefix}{local_fact_id}"


def _planning_scopes(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按来源范围聚合规划事实，让每个有效 scope 在 Planner 输入中只出现一次。"""

    scopes: list[dict[str, Any]] = []
    by_scope_id: dict[str, dict[str, Any]] = {}
    for fact in facts:
        scope_id = str(fact["scope_id"])
        scope = by_scope_id.get(scope_id)
        if scope is None:
            scope = {"scope_id": scope_id, "facts": []}
            by_scope_id[scope_id] = scope
            scopes.append(scope)
        scope["facts"].append(
            {
                "fact_id": str(fact["fact_id"]),
                "assertion": str(fact["assertion"]),
                "value_policy": str(fact["value_policy"]),
                "governed_values": list(fact.get("governed_values") or []),
                "governed_by": [
                    dict(item) for item in list(fact.get("governed_by") or [])
                ],
            }
        )
    return scopes


def _materialize_governed_values(
    *,
    fact: dict[str, Any],
    prepared: dict[str, Any],
    normalized_anchor: dict[str, Any],
    fact_id: str,
    value_policy: str,
    assertion: str,
) -> list[str]:
    """只接受来源坐标，由平台从真实正文切片生成受治理示例值。"""

    materializable_spans = _governed_value_spans_for_materialization(fact=fact)
    if materializable_spans is not None:
        source_kind = str(prepared.get("source_kind") or "")
        if source_kind == "document":
            source_text = str(prepared.get("page_text") or "")
            anchor_span = dict(normalized_anchor.get("source_span") or {})
            anchor_start = int(anchor_span.get("start") or 0)
            anchor_end = int(anchor_span.get("end") or 0)
        else:
            source_text = str(prepared.get("requirement") or "")
            anchor_start = int(normalized_anchor.get("source_offset_start") or 0)
            anchor_end = int(normalized_anchor.get("source_offset_end") or 0)
        values: list[str] = []
        for span in materializable_spans:
            if span["start"] < anchor_start or span["end"] > anchor_end:
                raise ValueError(
                    f"governed_value_span 超出事实来源范围: fact_id={fact_id}, span={span}"
                )
            if span["end"] > len(source_text):
                raise ValueError(
                    f"governed_value_span 超出真实正文: fact_id={fact_id}, span={span}"
                )
            value = source_text[span["start"] : span["end"]]
            if not value.strip():
                raise ValueError(f"governed_value_span 命中空白内容: fact_id={fact_id}")
            values.append(value)
    else:
        # 已完成单项后处理的结果会携带平台切片后的 governed_values。
        raw_values = fact.get("governed_values")
        if not isinstance(raw_values, list):
            raise ValueError(f"governed_values 必须是数组: fact_id={fact_id}")
        values = [_required_text(item, "governed_values") for item in raw_values]

    if len(values) != len(set(values)):
        raise ValueError(f"governed_values 包含重复值: fact_id={fact_id}")
    if value_policy == "exact" and values:
        raise ValueError(f"exact 事实的 governed_values 必须为空: fact_id={fact_id}")
    anchor_text = str(normalized_anchor.get("quote") or "")
    for value in values:
        if value not in assertion and value not in anchor_text:
            raise ValueError(
                f"governed_value 未命中 assertion 或精确来源原文: "
                f"fact_id={fact_id}, value={value}"
            )
    # runtime_configured 可以没有示例值；“由配置决定”本身不等于来源声明了具体示例。
    return values


def _merge_source_semantics_records(
    arguments: dict[str, Any],
    *,
    require_effective: bool,
) -> dict[str, Any]:
    """校验 Agent 事实锚点，并仅把仍生效的事实交给规划和生成。"""

    prepared_items = list(arguments.get("semantic_inputs") or [])
    records = list(arguments.get("semantic_records") or [])
    if not prepared_items and not records:
        prepared_items = []
        records = []
        for input_key, record_key in (
            ("text_inputs", "text_records"),
            ("vision_inputs", "vision_records"),
        ):
            group_inputs = list(arguments.get(input_key) or [])
            group_records = list(arguments.get(record_key) or [])
            if len(group_inputs) != len(group_records):
                raise ValueError(f"source semantics 分流输入与结果数量不一致: {input_key}")
            offset = len(prepared_items)
            prepared_items.extend(group_inputs)
            for local_index, raw_record in enumerate(group_records):
                if not isinstance(raw_record, dict):
                    raise ValueError("source semantics 分流结果只能包含对象")
                record = dict(raw_record)
                record["item_index"] = offset + int(record.get("item_index", local_index))
                records.append(record)
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
        local_to_canonical: dict[str, str] = {}
        canonical_to_local: dict[str, str] = {}
        for raw_fact in output["authoritative_facts"]:
            if not isinstance(raw_fact, dict):
                raise ValueError("authoritative_facts 只能包含对象")
            local_fact_id = _required_text(raw_fact.get("fact_id"), "fact_id")
            fact_prepared = _prepared_source_for_fact(
                prepared,
                dict(raw_fact.get("source_anchor") or {}),
            )
            namespace = _fact_namespace(fact_prepared)
            canonical_fact_id = _canonical_fact_id(namespace, local_fact_id)
            if local_fact_id in local_to_canonical:
                raise ValueError(
                    f"当前来源内 authoritative fact_id 重复: item_index={item_index}, fact_id={local_fact_id}"
                )
            if canonical_fact_id in canonical_to_local:
                raise ValueError(
                    f"当前来源内 authoritative fact_id 归一化后重复: item_index={item_index}, "
                    f"fact_id={local_fact_id}"
                )
            local_to_canonical[local_fact_id] = canonical_fact_id
            canonical_to_local[canonical_fact_id] = local_fact_id
        # 同一页面 Agent 后续的 governed_by 可能使用规范 ID；两种写法都只解析到当前页面。
        local_fact_id_lookup = {
            **local_to_canonical,
            **{canonical_id: canonical_id for canonical_id in canonical_to_local},
        }
        for raw_fact in output["authoritative_facts"]:
            if not isinstance(raw_fact, dict):
                raise ValueError("authoritative_facts 只能包含对象")
            fact = dict(raw_fact)
            local_fact_id = _required_text(fact.get("fact_id"), "fact_id")
            fact_id = local_to_canonical[local_fact_id]
            if fact_id in fact_ids:
                raise ValueError(f"authoritative fact_id 重复: {fact_id}")
            fact_ids.add(fact_id)
            source_anchor = dict(fact.get("source_anchor") or {})
            fact_prepared = _prepared_source_for_fact(prepared, source_anchor)
            source_kind = str(fact_prepared.get("source_kind") or "")
            # 来源类型只能来自平台准备的真实输入，不接受模型重复声明。
            source_anchor["source_kind"] = source_kind
            superseded_by_mark = False
            if source_kind == "document":
                normalized_anchor, superseded_by_mark, allowed_scope_ids = _validated_document_anchor(
                    source_anchor,
                    fact_prepared,
                )
                expanded_anchor = _expanded_anchor_for_governed_values(
                    normalized_anchor,
                    fact=fact,
                    prepared=fact_prepared,
                )
                if expanded_anchor != normalized_anchor:
                    normalized_anchor, expanded_by_mark, allowed_scope_ids = (
                        _validated_document_anchor(expanded_anchor, fact_prepared)
                    )
                    superseded_by_mark = superseded_by_mark or expanded_by_mark
            else:
                source_anchor = _expanded_anchor_for_governed_values(
                    source_anchor,
                    fact=fact,
                    prepared=fact_prepared,
                )
                normalized_anchor, allowed_scope_ids = _validated_inline_anchor(
                    source_anchor, fact_prepared
                )
            raw_governed_by = fact.get("governed_by")
            if not isinstance(raw_governed_by, list):
                raise ValueError(f"governed_by 必须是数组: fact_id={fact_id}")
            governed_by: list[dict[str, str]] = []
            governance_keys: set[tuple[str, str]] = set()
            for raw_directive in raw_governed_by:
                if not isinstance(raw_directive, dict):
                    raise ValueError(f"governed_by 只能包含对象: fact_id={fact_id}")
                relation = str(raw_directive.get("relation") or "").strip()
                relation = GOVERNANCE_RELATION_ALIASES.get(relation, relation)
                if relation in _CROSS_FIELD_RELATION_VALUES:
                    # 这些值属于同一事实的其他字段，不能表达事实间治理关系。
                    continue
                if relation not in _GOVERNANCE_RELATIONS:
                    raise ValueError(f"governed_by.relation 无效: fact_id={fact_id}")
                local_directive_fact_id = _required_text(
                    raw_directive.get("directive_fact_id"),
                    "governed_by.directive_fact_id",
                )
                directive_fact_id = local_fact_id_lookup.get(local_directive_fact_id)
                if not directive_fact_id:
                    raise ValueError(
                        f"governed_by 引用了当前来源外 fact_id: fact_id={fact_id}, "
                        f"unknown={local_directive_fact_id}"
                    )
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
            assertion = _required_text(fact.get("assertion"), "assertion")
            governed_values = _materialize_governed_values(
                fact=fact,
                prepared=fact_prepared,
                normalized_anchor=normalized_anchor,
                fact_id=fact_id,
                value_policy=value_policy,
                assertion=assertion,
            )
            if superseded_by_mark:
                status = "superseded"
            if len(allowed_scope_ids) != 1:
                raise ValueError(
                    "事实来源锚点必须命中唯一证据作用域: "
                    f"fact_id={fact_id}, scope_ids={sorted(allowed_scope_ids)}"
                )
            # scope_id 完全由真实锚点派生，避免模型在多页批次中串写相邻页面作用域。
            scope_id = next(iter(allowed_scope_ids))
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
    if require_effective and not effective_facts:
        raise ValueError("source semantics 没有可供规划和生成使用的有效事实")
    inspected_pages = {
        (int(page.get("document_id") or item.get("document_id") or 0), int(page.get("page_number") or 0))
        for item in prepared_items
        if isinstance(item, dict)
        for page in (
            list(item.get("pages") or [])
            if item.get("source_kind") == "document_batch"
            else [item] if item.get("source_kind") == "document" else []
        )
        if isinstance(page, dict)
    }
    result = {
        "authoritative_facts": facts,
        "effective_facts": effective_facts,
        "planning_scopes": _planning_scopes(effective_facts),
        "inspected_page_count": len(inspected_pages),
    }
    return result


def postprocess_source_semantics_item(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """在每个来源 Agent 返回时立即完成坐标切片和事实锚点校验。"""

    item_input = arguments.get("item_input")
    item_output = arguments.get("item_output")
    if not isinstance(item_input, dict) or not isinstance(item_output, dict):
        raise ValueError("source semantics 单项后处理缺少输入或输出")
    hydrated_item_input = _hydrate_source_semantics_item(context, dict(item_input))
    hydrated_item_output = _translate_source_semantics_output(
        context,
        dict(item_output),
        assume_local_coordinates=_source_item_uses_compressed_coordinates(
            context,
            hydrated_item_input,
        ),
    )
    merged = _merge_source_semantics_records(
        {
            "semantic_inputs": [hydrated_item_input],
            "semantic_records": [{"item_index": 0, "output": hydrated_item_output}],
        },
        require_effective=False,
    )
    return {"authoritative_facts": merged["authoritative_facts"]}


def merge_source_semantics(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """合并已经逐项校验的来源事实，并做跨任务唯一性检查。"""

    result = _merge_source_semantics_records(
        _hydrate_semantic_arguments(context, arguments),
        require_effective=True,
    )
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


def _requires_authority_review(facts: list[dict[str, Any]]) -> bool:
    """只有存在明确治理信号时才激活跨来源协调 Agent。"""

    for fact in facts:
        if str(fact.get("status") or "") != "effective":
            return True
        if list(fact.get("governed_by") or []):
            return True
        assertion = str(fact.get("assertion") or "")
        if any(signal in assertion for signal in _AUTHORITY_REVIEW_SIGNALS):
            return True
    return False


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

    effective_fact_ids = {
        str(fact["fact_id"])
        for fact in facts
        if str(fact.get("status") or "") == "effective"
    }
    planned_fact_ids: set[str] = set()
    for raw_module in plan["business_modules"]:
        if isinstance(raw_module, dict):
            planned_fact_ids.update(
                str(value or "").strip()
                for value in list(raw_module.get("fact_ids") or [])
                if str(value or "").strip()
            )
    unknown_fact_ids = planned_fact_ids - fact_ids
    missing_fact_ids = effective_fact_ids - planned_fact_ids
    if unknown_fact_ids or missing_fact_ids:
        raise ValueError(
            "业务规划 fact_ids 未完整对应有效事实: "
            f"unknown={sorted(unknown_fact_ids)}, missing={sorted(missing_fact_ids)}"
        )

    items: list[dict[str, Any]] = []
    skipped_modules: list[dict[str, Any]] = []
    for module_index, raw_module in enumerate(plan["business_modules"]):
        if not isinstance(raw_module, dict):
            raise ValueError(f"business_modules[{module_index}] 必须是对象")
        module = dict(raw_module)
        module_fact_ids = {
            str(value or "").strip()
            for value in list(module.get("fact_ids") or [])
            if str(value or "").strip()
        }
        if not module_fact_ids:
            raise ValueError(f"规划模块缺少 fact_ids: module_index={module_index}")
        module_facts = sorted(
            [dict(fact) for fact in facts if str(fact.get("fact_id") or "") in module_fact_ids],
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
        if not _requires_authority_review(module_facts):
            skipped_modules.append(
                {
                    "module_index": module_index,
                    "module_name": str(module.get("name") or ""),
                    "reason": "no_explicit_governance_signal",
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
) -> dict[str, Any] | None:
    if not isinstance(raw_decision, dict):
        raise ValueError("authority reconciliation decisions 每项必须是对象")
    decision = dict(raw_decision)
    fact_id = _required_text(decision.get("fact_id"), "decision.fact_id")
    if fact_id != str(original.get("fact_id") or ""):
        raise ValueError(f"authority reconciliation fact_id 与输入顺序不一致: {fact_id}")
    status = str(decision.get("status", original.get("status")) or "").strip()
    if status not in _FACT_STATUSES:
        raise ValueError(f"authority reconciliation status 无效: fact_id={fact_id}")
    original_status = str(original.get("status") or "")
    if original_status != "effective" and status != original_status:
        raise ValueError(f"authority reconciliation 不得重新激活或改写失效事实: fact_id={fact_id}")

    value_policy = str(
        decision.get("value_policy", original.get("value_policy")) or ""
    ).strip()
    original_policy = str(original.get("value_policy") or "")
    if value_policy not in {"exact", "runtime_configured"}:
        raise ValueError(f"authority reconciliation value_policy 无效: fact_id={fact_id}")
    if original_policy == "runtime_configured" and value_policy != original_policy:
        raise ValueError(f"authority reconciliation 不得把动态配置降级为固定值: fact_id={fact_id}")

    raw_values = decision.get("governed_values", original.get("governed_values"))
    if not isinstance(raw_values, list):
        raise ValueError(f"authority reconciliation governed_values 必须是数组: fact_id={fact_id}")
    governed_values = [_required_text(value, "governed_values") for value in raw_values]
    if len(governed_values) != len(set(governed_values)):
        raise ValueError(f"authority reconciliation governed_values 重复: fact_id={fact_id}")
    if value_policy == "exact" and governed_values:
        raise ValueError(f"exact 事实不得携带 governed_values: fact_id={fact_id}")
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

    raw_governed_by = decision.get("governed_by", original.get("governed_by"))
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
        relation = GOVERNANCE_RELATION_ALIASES.get(relation, relation)
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
    normalized = {
        "fact_id": fact_id,
        "status": status,
        "value_policy": value_policy,
        "governed_values": governed_values,
        "governed_by": governed_by,
        "reason": reason,
    }
    comparable_fields = ("status", "value_policy", "governed_values", "governed_by")
    if all(normalized[key] == original.get(key) for key in comparable_fields):
        # 空 decisions 本就表示保持原事实；显式返回原值补丁与其语义等价。
        return None
    return normalized


def _normalize_authority_reconciliation_output(
    *,
    prepared: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    """在单个审查实例边界规范化稀疏补丁，供即时校验与最终合并复用。"""

    module_facts = [
        dict(fact) for fact in list(prepared.get("authoritative_facts") or [])
    ]
    if not module_facts:
        raise ValueError("authority reconciliation 输入缺少 authoritative_facts")
    raw_decisions = output.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("authority reconciliation 输出缺少 decisions")
    module_facts_by_id = {
        str(fact.get("fact_id") or ""): fact for fact in module_facts
    }
    module_fact_ids = set(module_facts_by_id)
    normalized_decisions: list[dict[str, Any]] = []
    seen_fact_ids: set[str] = set()
    for raw_decision in raw_decisions:
        if not isinstance(raw_decision, dict):
            raise ValueError("authority reconciliation decision 必须是对象")
        decision_fact_id = _required_text(
            raw_decision.get("fact_id"),
            "decision.fact_id",
        )
        if decision_fact_id in seen_fact_ids:
            raise ValueError(
                f"authority reconciliation decision fact_id 重复: {decision_fact_id}"
            )
        seen_fact_ids.add(decision_fact_id)
        original = module_facts_by_id.get(decision_fact_id)
        if original is None:
            raise ValueError(
                f"authority reconciliation decision 引用了模块外事实: {decision_fact_id}"
            )
        normalized = _normalize_reconciled_decision(
            raw_decision,
            original=original,
            module_fact_ids=module_fact_ids,
        )
        if normalized is not None:
            normalized_decisions.append(normalized)
    return {"decisions": normalized_decisions}


def _materialize_authority_agent_output(output: dict[str, Any]) -> dict[str, Any]:
    """把模型侧简化关系字段转换为平台规范字段。"""

    raw_decisions = output.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("authority reconciliation 输出缺少 decisions")
    decisions: list[Any] = []
    for raw_decision in raw_decisions:
        if not isinstance(raw_decision, dict):
            decisions.append(raw_decision)
            continue
        decision = dict(raw_decision)
        raw_governed_by = decision.get("governed_by")
        if isinstance(raw_governed_by, list):
            decision["governed_by"] = [
                {
                    "relation": dict(raw_relation).get("relation"),
                    "directive_fact_id": dict(raw_relation).get("fact_id"),
                }
                if isinstance(raw_relation, dict)
                else raw_relation
                for raw_relation in raw_governed_by
            ]
        decisions.append(decision)
    return {"decisions": decisions}


def postprocess_authority_reconciliation_item(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """审查实例完成时立即校验补丁，使失败恢复保持在单模块粒度。"""

    del context
    return _normalize_authority_reconciliation_output(
        prepared=dict(arguments.get("item_input") or {}),
        output=_materialize_authority_agent_output(
            dict(arguments.get("item_output") or {})
        ),
    )


def merge_authority_reconciliation(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """确定性应用模块级协调补丁，未返回的事实保持原值。"""

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
        output = records_by_index[item_index].get("output")
        if not isinstance(output, dict):
            raise ValueError(f"authority reconciliation 输出缺少 decisions: item_index={item_index}")
        normalized_output = _normalize_authority_reconciliation_output(
            prepared=prepared,
            output=output,
        )
        for normalized in normalized_output["decisions"]:
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
