from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any, TYPE_CHECKING

from modules.knowledge_base_components.adapters.chroma_vector_store import get_vector_store
from modules.knowledge_base_components.document.document_asset_service import (
    detect_high_confidence_page_continuations,
    document_page_text,
    load_document_manifest,
)

from .test_generation_facts import (
    binding_index,
    index_effective_facts,
    replace_binding_case_id,
    validate_case_fact_bindings,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext


MAX_EVIDENCE_ACCOUNTING_ITEMS_PER_BATCH = 4
TARGET_EVIDENCE_ACCOUNTING_TEXT_CHARS = 7000
MAX_EVIDENCE_ACCOUNTING_NEIGHBOR_CHARS = 1200
MAX_EVIDENCE_ACCOUNTING_BATCHES = 100


def _required_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name}必须是非空数组")
    return value


def _identity(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\W_]+", "", normalized.strip().casefold())


def _text_ngrams(value: Any, *, size: int) -> set[str]:
    """提取与语言分词器无关的字符片段，用于覆盖点和模块的通用匹配。"""

    normalized = _identity(value)
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {
        normalized[index : index + size]
        for index in range(len(normalized) - size + 1)
    }


def _coverage_points(raw_focus: Any) -> list[str]:
    """把规划中的复合覆盖描述拆成可分配的原子覆盖点。"""

    if isinstance(raw_focus, str):
        focus_items = [raw_focus]
    else:
        focus_items = [str(item) for item in (raw_focus or [])]

    points: list[str] = []
    seen: set[str] = set()
    for raw_item in focus_items:
        item = raw_item.strip()
        if not item:
            continue
        label = ""
        body = item
        matched = re.match(r"^([^：:\n]{1,24})[：:]\s*(.+)$", item, flags=re.DOTALL)
        if matched:
            label = matched.group(1).strip()
            body = matched.group(2).strip()
        parts = _split_top_level_items(body)
        for part in parts or [body]:
            point = f"{label}：{part}" if label else part
            identity = _identity(point)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            points.append(point)
    return points


def _split_top_level_items(value: str) -> list[str]:
    """仅按括号外标点拆分，避免把枚举内容截成残缺语义。"""

    opening_to_closing = {"(": ")", "（": "）", "[": "]", "【": "】", "{": "}"}
    closing = set(opening_to_closing.values())
    separators = {"、", "，", ",", "；", ";", "。", "\n"}
    stack: list[str] = []
    parts: list[str] = []
    buffer: list[str] = []
    for character in value:
        if character in opening_to_closing:
            stack.append(opening_to_closing[character])
            buffer.append(character)
            continue
        if character in closing:
            if stack and character == stack[-1]:
                stack.pop()
            buffer.append(character)
            continue
        if character in separators and not stack:
            part = "".join(buffer).strip()
            if part:
                parts.append(part)
            buffer = []
            continue
        buffer.append(character)
    tail = "".join(buffer).strip()
    if tail:
        parts.append(tail)
    return parts


def _coverage_relevance(module: dict[str, Any], point: str) -> int:
    """计算覆盖点与单个业务模块的语义表面相关性，不引入领域词表。"""

    coverage_focus = module.get("coverage_focus") or []
    if isinstance(coverage_focus, str):
        coverage_items = [coverage_focus]
    else:
        coverage_items = [str(item) for item in coverage_focus]
    applicable_coverage = [
        item
        for item in coverage_items
        if _coverage_reference_applies(item, point)
    ]
    module_text = "\n".join(
        [
            str(module.get("name") or ""),
            str(module.get("objective") or ""),
            str(module.get("lifecycle") or ""),
            *applicable_coverage,
        ]
    )
    module_identity = _identity(module_text)
    point_identity = _identity(point)
    if not module_identity or not point_identity:
        return 0

    module_name = _identity(module.get("name"))
    score = 0
    if module_name and module_name in point_identity:
        score += 100 + len(module_name)
    for name_part in re.split(r"[与和及、/&]+", str(module.get("name") or "")):
        part_identity = _identity(name_part)
        if len(part_identity) >= 2 and part_identity in point_identity:
            score += 40 + len(part_identity)
    if point_identity in module_identity:
        score += 60 + len(point_identity)
    score += 4 * len(_text_ngrams(module_text, size=3) & _text_ngrams(point, size=3))
    score += len(_text_ngrams(module_text, size=2) & _text_ngrams(point, size=2))
    governing_text = re.split(r"[（(【\[]", point, maxsplit=1)[0].strip()
    if "coverage_focus" not in module and governing_text and governing_text != point.strip():
        # 括号前是枚举的支配语义，权重应高于括号内各项的偶然词面命中。
        score += 2 * _surface_relevance(governing_text, module_text)
    return score


def _coverage_reference_applies(reference: str, candidate: str) -> bool:
    """条件式覆盖点需命中结果动作，不能仅凭前置条件抢走证据。"""

    body = re.sub(r"^[^：:\n]{1,24}[：:]\s*", "", reference.strip())
    matched = re.search(r"(?:时|后|前)([^，,；;。\n]{2,})$", body)
    if not matched:
        return True
    consequence = matched.group(1).strip()
    return bool(
        _text_ngrams(consequence, size=2)
        & _text_ngrams(candidate, size=2)
    )


def _allocate_unique_items(
    modules: list[dict[str, Any]],
    items: list[str],
) -> list[list[str]]:
    """每个规划项只分配给唯一最相关模块，歧义项保持未分配。"""

    allocated: list[list[str]] = [[] for _ in modules]
    for item in items:
        scores = [_coverage_relevance(module, item) for module in modules]
        best_score = max(scores, default=0)
        if best_score <= 0:
            continue
        candidates = [index for index, score in enumerate(scores) if score == best_score]
        # 无法由模块自身信息消除歧义的覆盖点不强行分配，避免随机跨模块。
        if len(candidates) != 1:
            continue
        target_index = candidates[0]
        allocated[target_index].append(item)
    return allocated


def _allocate_coverage_points(
    modules: list[dict[str, Any]],
    raw_focus: Any,
) -> list[list[str]]:
    """拆分并唯一分配覆盖点，避免跨模块复用全局描述。"""

    return _allocate_unique_items(modules, _coverage_points(raw_focus))


def _risk_items(raw_risks: Any) -> list[str]:
    """风险保持规划中的完整语义，不按标点破坏因果描述。"""

    if isinstance(raw_risks, str):
        values = [raw_risks]
    else:
        values = [str(item) for item in (raw_risks or [])]
    risks: list[str] = []
    seen: set[str] = set()
    for value in values:
        risk = value.strip()
        identity = _identity(risk)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        risks.append(risk)
    return risks


def _allocate_risks(
    modules: list[dict[str, Any]],
    raw_risks: Any,
) -> list[list[str]]:
    """风险只进入唯一相关模块，无法唯一归属时不注入任何模块。"""

    return _allocate_unique_items(modules, _risk_items(raw_risks))


def _batch_focus(module: dict[str, Any], coverage_points: list[str]) -> str:
    """仅使用当前模块自身事实和已分配覆盖点构造批次焦点。"""

    parts = [f"模块目标：{str(module.get('objective') or '').strip()}"]
    lifecycle = str(module.get("lifecycle") or "").strip()
    if lifecycle:
        parts.append(f"生命周期：{lifecycle}")
    if coverage_points:
        parts.append(f"当前覆盖点：{'；'.join(coverage_points)}")
    return "；".join(part for part in parts if not part.endswith("："))


def _flatten_vector_result(result: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    if documents and isinstance(documents[0], list):
        documents = documents[0]
    if metadatas and isinstance(metadatas[0], list):
        metadatas = metadatas[0]
    return (
        [str(item or "").strip() for item in documents],
        [dict(item or {}) for item in metadatas],
    )


def _compact_fragment_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _raw_chunk_fragments(
    *,
    document_id: int,
    text: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """把上游通用语义块转成证据分片，不再按文档编号规则二次切分。"""

    fragment_text = str(text or "").strip()
    if not fragment_text:
        return []

    chunk_index = int(metadata.get("chunk_index") or 0)
    page_value = next(
        (
            metadata.get(key)
            for key in ("page_number", "page", "page_index")
            if metadata.get(key) not in (None, "")
        ),
        None,
    )
    if page_value is None:
        raise ValueError(f"文档证据块缺少真实页码: document_id={document_id}")
    raw_block_ids = metadata.get("block_ids") or []
    if isinstance(raw_block_ids, str):
        try:
            decoded_block_ids = json.loads(raw_block_ids)
        except json.JSONDecodeError:
            decoded_block_ids = []
    else:
        decoded_block_ids = raw_block_ids
    block_ids = [
        str(item)
        for item in (decoded_block_ids if isinstance(decoded_block_ids, list) else [])
        if str(item).strip()
    ]
    source_offset_start = int(metadata.get("source_offset_start") or 0)
    source_offset_end = int(metadata.get("source_offset_end") or 0)
    asset_source_sha256 = str(metadata.get("asset_source_sha256") or "").strip().lower()
    if len(asset_source_sha256) != 64:
        raise ValueError(f"文档证据块缺少资产指纹: document_id={document_id}")
    return [
        {
            "document_id": document_id,
            "chunk_index": chunk_index,
            "biz_key": "",
            "text": fragment_text,
            "page_number": int(page_value),
            "block_ids": block_ids,
            "source_offset_start": source_offset_start,
            "source_offset_end": source_offset_end,
            "asset_source_sha256": asset_source_sha256,
        }
    ]


def _document_fragments(
    *,
    source: dict[str, Any],
    requirement: str,
) -> list[dict[str, Any]]:
    """读取当前文档全部非摘要原始块，并转换为带来源锚点的候选分片。"""

    document_id = source.get("document_id")
    if document_id is None:
        return []
    result = get_vector_store().search_by_metadata(
        where={
            "$and": [
                {"doc_id": str(document_id)},
                {"is_summary": False},
            ]
        },
        # 每个非空原始块至少对应一个正文字符，按真实正文长度读取即可覆盖全量块。
        n_results=max(1, len(requirement)),
        raise_on_error=True,
    )
    documents, metadatas = _flatten_vector_result(result)
    fragments: list[dict[str, Any]] = []
    seen_raw_chunks: set[tuple[int, str]] = set()
    for text, metadata in zip(documents, metadatas):
        chunk_index = int(metadata.get("chunk_index") or 0)
        raw_identity = (chunk_index, text)
        if not text or raw_identity in seen_raw_chunks:
            continue
        seen_raw_chunks.add(raw_identity)
        raw_fragments = _raw_chunk_fragments(
                document_id=int(document_id),
                text=text,
                metadata=metadata,
            )
        for fragment in raw_fragments:
            page_text = document_page_text(
                int(document_id),
                int(fragment["page_number"]),
            )
            start = int(fragment["source_offset_start"])
            end = int(fragment["source_offset_end"])
            if start < 0 or end <= start or page_text[start:end] != fragment["text"]:
                raise ValueError(
                    "文档证据块与真实页文本坐标不一致: "
                    f"document_id={document_id}, page_number={fragment['page_number']}, "
                    f"start={start}, end={end}"
                )
        fragments.extend(raw_fragments)
    if not fragments:
        raise ValueError(f"需求文档没有可用原始证据块: document_id={document_id}")
    return fragments


def _evidence_chunk_from_catalog_item(item: dict[str, Any]) -> dict[str, Any]:
    """把目录项恢复为生成链使用的完整可追踪证据块。"""

    required_fields = (
        "document_id",
        "chunk_index",
        "biz_key",
        "text",
        "page_number",
        "block_ids",
        "source_offset_start",
        "source_offset_end",
        "asset_source_sha256",
    )
    missing_fields = [field for field in required_fields if field not in item]
    if missing_fields:
        raise ValueError(
            "证据目录项缺少可追踪字段: "
            f"evidence_id={item.get('evidence_id')}, fields={missing_fields}"
        )
    text = str(item.get("text") or "").strip()
    if not text:
        raise ValueError(f"证据目录项正文不能为空: evidence_id={item.get('evidence_id')}")
    asset_source_sha256 = str(item.get("asset_source_sha256") or "").strip().lower()
    if len(asset_source_sha256) != 64:
        raise ValueError(
            f"证据目录项缺少资产指纹: evidence_id={item.get('evidence_id')}"
        )
    block_ids = item.get("block_ids")
    if not isinstance(block_ids, list):
        raise ValueError(
            f"证据目录项 block_ids 必须是数组: evidence_id={item.get('evidence_id')}"
        )
    document_id = item.get("document_id")
    page_number = item.get("page_number")
    return {
        "document_id": int(document_id) if document_id is not None else None,
        "chunk_index": int(item["chunk_index"]),
        "biz_key": str(item.get("biz_key") or ""),
        "text": text,
        "page_number": int(page_number) if page_number is not None else None,
        "block_ids": [str(value) for value in block_ids],
        "source_offset_start": int(item.get("source_offset_start") or 0),
        "source_offset_end": int(item.get("source_offset_end") or 0),
        "asset_source_sha256": asset_source_sha256,
    }


def _build_evidence_catalog_from_fragments(
    fragments: list[dict[str, Any]],
    *,
    manifest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按真实来源顺序为证据分片分配稳定、无业务含义的目录 ID。"""

    ordered_fragments = sorted(
        fragments,
        key=lambda item: (
            int(item.get("document_id") or 0),
            int(item.get("page_number") or 0),
            int(item.get("source_offset_start") or 0),
            int(item.get("source_offset_end") or 0),
            int(item.get("chunk_index") or 0),
            str(item.get("biz_key") or ""),
            str(item.get("asset_source_sha256") or ""),
            str(item.get("text") or ""),
        ),
    )
    catalog: list[dict[str, Any]] = []
    for catalog_index, fragment in enumerate(ordered_fragments, start=1):
        evidence_chunk = _evidence_chunk_from_catalog_item(fragment)
        # 中文注释：资产分块中的首行不等于标题，Agent 目录不暴露伪 heading 业务键。
        evidence_chunk["biz_key"] = ""
        catalog.append(
            {
                "evidence_id": f"EV-{catalog_index:04d}",
                **evidence_chunk,
                "continuation": None,
            }
        )
    if manifest is not None:
        _attach_high_confidence_continuations(catalog, manifest=manifest)
    return catalog


def _attach_high_confidence_continuations(
    catalog: list[dict[str, Any]],
    *,
    manifest: dict[str, Any],
) -> None:
    """将页级版式候选绑定到相邻的真实证据项，不改写正文。"""

    positions = {id(item): index for index, item in enumerate(catalog)}
    items_by_page: dict[int, list[dict[str, Any]]] = {}
    for item in catalog:
        page_number = int(item.get("page_number") or 0)
        if page_number > 0:
            items_by_page.setdefault(page_number, []).append(item)
    for link in detect_high_confidence_page_continuations(manifest):
        # 中文注释：局部页首续项仍由总账 Agent 自主路由，只有右页整块承接才建立继承关系。
        if not bool(link.get("right_page_is_whole_item")):
            continue
        left_items = items_by_page.get(int(link["left_page_number"]), [])
        right_items = items_by_page.get(int(link["right_page_number"]), [])
        if not left_items:
            raise ValueError(
                "整页续项缺少左页证据块: "
                f"page_number={link['left_page_number']}"
            )
        if len(right_items) != 1:
            raise ValueError(
                "整页续项必须对应唯一右页证据块: "
                f"page_number={link['right_page_number']}, chunk_count={len(right_items)}"
            )
        left_page_number = int(link["left_page_number"])
        left_page = next(
            (
                dict(page)
                for page in list(manifest.get("pages") or [])
                if int(page.get("page_number") or 0) == left_page_number
            ),
            None,
        )
        if left_page is None:
            raise ValueError(f"整页续项缺少左页版式资产: page_number={left_page_number}")
        left_block_index = {
            str(block.get("block_id") or ""): dict(block)
            for block in list(left_page.get("blocks") or [])
            if block.get("type") == "text_line" and str(block.get("block_id") or "")
        }
        marker = dict(link["left_marker"])
        marker_block = left_block_index.get(str(marker.get("block_id") or ""))
        support_markers = [
            dict(item)
            for item in list(link.get("support_markers") or [])
            if item.get("kind") == marker.get("kind")
            and int(item.get("ordinal") or 0) == int(marker.get("ordinal") or 0) - 1
            and str(item.get("block_id") or "") in left_block_index
        ]
        if marker_block is None or len(support_markers) != 1:
            raise ValueError(
                "整页续项缺少唯一左页 marker/support 锚点: "
                f"page_number={left_page_number}"
            )
        support_block = left_block_index[str(support_markers[0]["block_id"])]
        marker_source_span = dict(marker_block.get("source_span") or {})
        support_source_span = dict(support_block.get("source_span") or {})
        marker_source_start = int(marker_source_span.get("start") or 0)
        marker_source_end = int(marker_source_span.get("end") or 0)
        support_source_start = int(support_source_span.get("start") or 0)
        containing_left_items = [
            item
            for item in left_items
            if int(item.get("source_offset_start") or 0) <= support_source_start
            and int(item.get("source_offset_end") or 0) >= marker_source_end
        ]
        if len(containing_left_items) != 1:
            raise ValueError(
                "整页续项治理范围必须由唯一左页证据块完整覆盖: "
                f"page_number={left_page_number}, chunk_count={len(containing_left_items)}"
            )
        left_item = containing_left_items[0]
        last_left_item = max(
            left_items,
            key=lambda item: (
                int(item.get("source_offset_end") or 0),
                positions[id(item)],
            ),
        )
        if left_item is not last_left_item:
            raise ValueError(
                "整页续项的治理范围与左页页尾正文跨证据块: "
                f"page_number={left_page_number}"
            )
        right_item = min(
            right_items,
            key=lambda item: (
                int(item.get("source_offset_start") or 0),
                positions[id(item)],
            ),
        )
        left_position = positions[id(left_item)]
        right_position = positions[id(right_item)]
        if right_position != left_position + 1:
            raise ValueError(
                "整页续项左右证据块不相邻: "
                f"left_page={left_page_number}, right_page={link['right_page_number']}"
            )
        left_text = str(left_item.get("text") or "")
        right_text = str(right_item.get("text") or "")
        if not left_text or not right_text:
            raise ValueError("整页续项左右证据块正文不能为空")
        required_right_block_ids = [
            str(value)
            for value in list(link.get("right_continuation_block_ids") or [])
            if str(value).strip()
        ]
        if list(right_item.get("block_ids") or []) != required_right_block_ids:
            raise ValueError(
                "整页续项证据块与同源版式锚点不一致: "
                f"page_number={link['right_page_number']}"
            )
        layout_text = "\n".join(
            str(value)
            for value in list(link.get("right_continuation_line_texts") or [])
        )
        if layout_text != right_text:
            raise ValueError(
                "整页续项证据块与同源页文本不一致: "
                f"page_number={link['right_page_number']}"
            )
        chunk_source_start = int(left_item.get("source_offset_start") or 0)
        raw_marker = str(marker.get("raw") or "")
        marker_line_text = str(marker_block.get("text") or "")
        marker_in_line = marker_line_text.rfind(raw_marker)
        if not raw_marker or marker_in_line < 0:
            raise ValueError(
                "整页续项 marker 与同源版式文本不一致: "
                f"block_id={marker.get('block_id')}"
            )
        marker_start = marker_source_start - chunk_source_start + marker_in_line
        marker_end = marker_start + len(raw_marker)
        support_line_start = support_source_start - chunk_source_start
        marker_line_end = marker_source_end - chunk_source_start
        left_tail_start = max(0, len(left_text) - 800)
        minimum_start = _governing_context_start(left_text, support_line_start)
        if minimum_start is None:
            raise ValueError(
                "整页续项治理上下文跨左页证据块或不存在: "
                f"page_number={left_page_number}"
            )
        left_tail_start = min(left_tail_start, minimum_start)
        right_head_end = min(len(right_text), 1200)
        right_item["continuation"] = {
            "confidence": "high",
            "previous_evidence_id": str(left_item["evidence_id"]),
            "left_tail_span": {
                "start": left_tail_start,
                "end": len(left_text),
            },
            "left_marker_span": {
                "start": marker_start,
                "end": marker_end,
            },
            "minimum_governing_span": {
                "start": minimum_start,
                "end": marker_line_end,
            },
            "right_range": {
                "start": 0,
                "end": len(right_text),
                "head_end": right_head_end,
            },
            "left_marker": dict(link["left_marker"]),
            "right_marker": dict(link["right_marker"]),
            "support_markers": [dict(item) for item in link["support_markers"]],
            "style": dict(link["style"]),
            "left_tail_block_ids": list(link["left_tail_block_ids"]),
            "right_head_block_ids": list(link["right_head_block_ids"]),
            "right_continuation_block_ids": list(
                link["right_continuation_block_ids"]
            ),
            "right_continuation_line_texts": list(
                link["right_continuation_line_texts"]
            ),
        }
def _governing_context_start(text: str, support_line_start: int) -> int | None:
    """从支持编号前寻找非列表引导行，并保留它前一个非空上下文行。"""

    line_matches = list(re.finditer(r"(?m)^.*$", text[:support_line_start]))
    nonempty = [match for match in line_matches if match.group(0).strip()]
    if not nonempty:
        return None
    ordered_line = re.compile(
        r"^\s*(?:\d{1,4}|[A-Za-z])\s*[.．、)）：:]\s*"
    )
    intro_position = next(
        (
            position
            for position in range(len(nonempty) - 1, -1, -1)
            if ordered_line.match(nonempty[position].group(0)) is None
        ),
        None,
    )
    if intro_position is None:
        return None
    start_position = max(0, intro_position - 1)
    return nonempty[start_position].start()


def build_planning_evidence_catalog(
    *,
    source: dict[str, Any],
    requirement: str,
) -> dict[str, Any]:
    """从真实文档分片构建供 Planner 选择的稳定证据目录。"""

    document_id = source.get("document_id")
    manifest = None
    if document_id is None:
        inline_text = str(requirement or "")
        if not inline_text.strip():
            raise ValueError("纯文本需求不能为空")
        inline_hash = hashlib.sha256(inline_text.encode("utf-8")).hexdigest()
        if inline_hash != str(source.get("content_hash") or "").strip().lower():
            raise ValueError("纯文本需求与事实源指纹不一致")
        return {
            "document_id": None,
            "items": [
                {
                    "evidence_id": "EV-0001",
                    "document_id": None,
                    "chunk_index": 0,
                    "biz_key": "",
                    "text": inline_text,
                    "page_number": None,
                    "block_ids": [],
                    "source_offset_start": 0,
                    "source_offset_end": len(inline_text),
                    "asset_source_sha256": inline_hash,
                    "continuation": None,
                }
            ],
        }
    if document_id is not None:
        manifest = load_document_manifest(int(document_id))
        if int(manifest.get("schema_version") or 0) != 3:
            raise ValueError(
                "文档页面资产版本不受支持，必须重新解析: "
                f"document_id={document_id}, schema_version={manifest.get('schema_version')}"
            )
    return {
        "document_id": int(document_id) if document_id is not None else None,
        "items": _build_evidence_catalog_from_fragments(
            _document_fragments(source=source, requirement=requirement),
            manifest=manifest,
        ),
    }


def _surface_relevance(reference: str, candidate: str) -> int:
    return (
        4 * len(_text_ngrams(reference, size=3) & _text_ngrams(candidate, size=3))
        + len(_text_ngrams(reference, size=2) & _text_ngrams(candidate, size=2))
    )


def _evidence_catalog_index(
    evidence_catalog: Any,
    *,
    source: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """校验证据目录的 ID 与来源，并建立只读索引。"""

    if not isinstance(evidence_catalog, dict):
        raise ValueError("evidence_catalog 必须是对象")
    catalog_document_id = evidence_catalog.get("document_id")
    source_document_id = source.get("document_id")
    if source_document_id is None:
        if catalog_document_id is not None:
            raise ValueError("证据目录与无文档需求来源不一致")
    elif catalog_document_id is None or int(catalog_document_id) != int(source_document_id):
        raise ValueError(
            "证据目录与真实需求来源不一致: "
            f"catalog_document_id={catalog_document_id}, source_document_id={source_document_id}"
        )
    catalog_items = evidence_catalog.get("items")
    if not isinstance(catalog_items, list):
        raise ValueError("evidence_catalog.items 必须是数组")
    catalog_index: dict[str, dict[str, Any]] = {}
    for catalog_position, raw_item in enumerate(catalog_items):
        if not isinstance(raw_item, dict):
            raise ValueError(
                f"evidence_catalog 只能包含对象: catalog_position={catalog_position}"
            )
        item = dict(raw_item)
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not re.fullmatch(r"EV-\d{4,}", evidence_id):
            raise ValueError(
                "证据目录 ID 格式无效: "
                f"catalog_position={catalog_position}, evidence_id={evidence_id}"
            )
        if evidence_id in catalog_index:
            raise ValueError(f"证据目录包含重复 ID: evidence_id={evidence_id}")
        evidence_chunk = _evidence_chunk_from_catalog_item(item)
        if (
            source_document_id is not None
            and evidence_chunk["document_id"] != int(source_document_id)
        ):
            raise ValueError(
                "证据目录与真实需求来源不一致: "
                f"evidence_id={evidence_id}, document_id={evidence_chunk['document_id']}"
            )
        catalog_index[evidence_id] = evidence_chunk
    return catalog_index


def _module_evidence_ids(module: dict[str, Any]) -> list[str]:
    """严格读取 Planner 选择的证据 ID，不接受重复或隐式转换。"""

    raw_ids = module.get("evidence_ids")
    module_name = str(module.get("name") or "")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError(f"模块 evidence_ids 必须是非空数组: module={module_name}")
    evidence_ids: list[str] = []
    seen: set[str] = set()
    for evidence_position, raw_id in enumerate(raw_ids):
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError(
                "模块 evidence_ids 只能包含非空字符串: "
                f"module={module_name}, evidence_position={evidence_position}"
            )
        evidence_id = raw_id.strip()
        if evidence_id in seen:
            raise ValueError(
                f"模块 evidence_ids 包含重复 ID: module={module_name}, evidence_id={evidence_id}"
            )
        seen.add(evidence_id)
        evidence_ids.append(evidence_id)
    return evidence_ids


def _select_module_evidence(
    *,
    module: dict[str, Any],
    catalog_index: dict[str, dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """只按 Planner 选择的稳定证据 ID 恢复真实分片。"""

    module_name = str(module.get("name") or "")
    evidence_ids = _module_evidence_ids(module)
    evidence_chunks: list[dict[str, Any]] = []
    for evidence_position, evidence_id in enumerate(evidence_ids):
        evidence_chunk = catalog_index.get(evidence_id)
        if evidence_chunk is None:
            raise ValueError(
                "模块引用了未知证据 ID: "
                f"module={module_name}, evidence_position={evidence_position}, "
                f"evidence_id={evidence_id}"
            )
        evidence_chunks.append(dict(evidence_chunk))
    return "\n\n".join(item["text"] for item in evidence_chunks), evidence_chunks


def _stable_payload_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _neighbor_context(
    *,
    catalog_items: list[dict[str, Any]],
    first_position: int,
    last_position: int,
) -> list[dict[str, Any]]:
    """仅保留分片边界两侧的短上下文，供模型判断续表或未完句。"""

    neighbors: list[tuple[str, dict[str, Any]]] = []
    if first_position > 0:
        neighbors.append(("previous", catalog_items[first_position - 1]))
    if last_position + 1 < len(catalog_items):
        neighbors.append(("next", catalog_items[last_position + 1]))
    if not neighbors:
        return []

    text_limit = max(1, MAX_EVIDENCE_ACCOUNTING_NEIGHBOR_CHARS // len(neighbors))
    context: list[dict[str, Any]] = []
    for direction, item in neighbors:
        text = str(item.get("text") or "")
        excerpt = text[-text_limit:] if direction == "previous" else text[:text_limit]
        context.append(
            {
                "relative_position": direction,
                "evidence_id": str(item["evidence_id"]),
                "page_number": item.get("page_number"),
                "chunk_index": item.get("chunk_index"),
                "text": excerpt,
                "text_truncated": len(text) > text_limit,
            }
        )
    return context


def prepare_evidence_accounting_batches(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """按证据目录顺序切分路由复核输入，避免单次请求承载全部正文。"""

    if set(arguments) != {"draft_plan", "evidence_catalog"}:
        raise ValueError("证据核算准备只允许 draft_plan 和 evidence_catalog")

    raw_plan = arguments.get("draft_plan")
    if not isinstance(raw_plan, dict):
        raise ValueError("draft_plan 必须是对象")
    draft_plan = dict(raw_plan)
    modules = _required_list(
        draft_plan.get("business_modules"),
        "draft_plan.business_modules",
    )
    if not all(isinstance(module, dict) for module in modules):
        raise ValueError("draft_plan.business_modules 每项必须是对象")

    evidence_catalog = arguments.get("evidence_catalog")
    catalog_document_id = (
        evidence_catalog.get("document_id")
        if isinstance(evidence_catalog, dict)
        else None
    )
    catalog_index = _evidence_catalog_index(
        evidence_catalog,
        source={"document_id": catalog_document_id},
    )
    if not catalog_index:
        raise ValueError("evidence_catalog.items 不能为空")
    raw_catalog_items = list(evidence_catalog.get("items") or [])
    catalog_items = [dict(item) for item in raw_catalog_items]
    grouped_positions: list[list[int]] = []
    current_positions: list[int] = []
    current_text_chars = 0
    for catalog_position, item in enumerate(catalog_items):
        text_chars = len(str(item.get("text") or ""))
        if text_chars > TARGET_EVIDENCE_ACCOUNTING_TEXT_CHARS:
            raise ValueError(
                "单条证据正文超过路由复核分片字符预算: "
                f"evidence_id={item['evidence_id']}, text_chars={text_chars}, "
                f"limit={TARGET_EVIDENCE_ACCOUNTING_TEXT_CHARS}"
            )
        exceeds_item_limit = (
            len(current_positions) >= MAX_EVIDENCE_ACCOUNTING_ITEMS_PER_BATCH
        )
        exceeds_text_target = bool(current_positions) and (
            current_text_chars + text_chars
            > TARGET_EVIDENCE_ACCOUNTING_TEXT_CHARS
        )
        if exceeds_item_limit or exceeds_text_target:
            grouped_positions.append(current_positions)
            current_positions = []
            current_text_chars = 0
        current_positions.append(catalog_position)
        current_text_chars += text_chars
    if current_positions:
        grouped_positions.append(current_positions)
    if len(grouped_positions) > MAX_EVIDENCE_ACCOUNTING_BATCHES:
        raise ValueError(
            "证据核算分片数超过 agent_map 上限: "
            f"batch_count={len(grouped_positions)}, "
            f"limit={MAX_EVIDENCE_ACCOUNTING_BATCHES}"
        )

    items: list[dict[str, Any]] = []
    for positions in grouped_positions:
        target_items = [dict(catalog_items[position]) for position in positions]
        items.append(
            {
                "draft_plan": draft_plan,
                "target_evidence_items": target_items,
                "neighbor_context": _neighbor_context(
                    catalog_items=catalog_items,
                    first_position=positions[0],
                    last_position=positions[-1],
                ),
            }
        )

    context.artifacts["evidence_accounting_batches"] = {
        "batch_count": len(items),
        "evidence_count": len(catalog_items),
        "batches": [
            {
                "batch_position": batch_position,
                "evidence_ids": [
                    evidence["evidence_id"]
                    for evidence in item["target_evidence_items"]
                ],
            }
            for batch_position, item in enumerate(items)
        ],
    }
    return {
        "items": items,
        "batch_count": len(items),
        "evidence_count": len(catalog_items),
    }


def _normalize_batch_accounting_item(
    *,
    raw_item: Any,
    module_count: int,
) -> dict[str, Any]:
    """规范化单条分片记账，完整目录校验留给后续合并规划节点。"""

    if not isinstance(raw_item, dict):
        raise ValueError("evidence_accounting 只能包含对象")
    evidence_id = str(raw_item.get("evidence_id") or "").strip()
    if not re.fullmatch(r"EV-\d{4,}", evidence_id):
        raise ValueError(
            f"evidence_accounting.evidence_id 格式无效: evidence_id={evidence_id}"
        )
    raw_module_indexes = raw_item.get("module_indexes")
    if not isinstance(raw_module_indexes, list):
        raise ValueError(
            "evidence_accounting.module_indexes 必须是数组: "
            f"evidence_id={evidence_id}"
        )
    module_indexes: list[int] = []
    for raw_module_index in raw_module_indexes:
        if isinstance(raw_module_index, bool) or not isinstance(raw_module_index, int):
            raise ValueError(
                "evidence_accounting.module_indexes 只能包含整数: "
                f"evidence_id={evidence_id}"
            )
        if raw_module_index < 0 or raw_module_index >= module_count:
            raise ValueError(
                "evidence_accounting 包含越界 module_index: "
                f"evidence_id={evidence_id}, module_index={raw_module_index}"
            )
        if raw_module_index in module_indexes:
            raise ValueError(
                "evidence_accounting.module_indexes 包含重复下标: "
                f"evidence_id={evidence_id}, module_index={raw_module_index}"
            )
        module_indexes.append(raw_module_index)

    disposition = raw_item.get("disposition")
    if disposition not in {"assigned", "context_only", "plan_gap"}:
        raise ValueError(
            "evidence_accounting.disposition 无效: "
            f"evidence_id={evidence_id}, disposition={disposition}"
        )
    if disposition == "assigned" and not module_indexes:
        raise ValueError(
            f"assigned 证据必须包含至少一个 module_index: evidence_id={evidence_id}"
        )
    if disposition in {"context_only", "plan_gap"} and module_indexes:
        raise ValueError(
            f"{disposition} 证据的 module_indexes 必须为空: "
            f"evidence_id={evidence_id}"
        )
    reason = str(raw_item.get("reason") or "").strip()
    if not reason or len(reason) > 160:
        raise ValueError(
            "evidence_accounting.reason 必须是 1 至 160 字的字符串: "
            f"evidence_id={evidence_id}"
        )
    if set(raw_item) != {"evidence_id", "module_indexes", "disposition", "reason"}:
        raise ValueError(
            "evidence_accounting 包含未允许字段: "
            f"evidence_id={evidence_id}"
        )
    return {
        "evidence_id": evidence_id,
        "module_indexes": module_indexes,
        "disposition": disposition,
        "reason": reason,
    }


def merge_evidence_accounting_batches(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """严格校验并按原目录顺序合并证据核算 agent_map 结果。"""

    prepared_items = _required_list(arguments.get("prepared_items"), "prepared_items")
    routing_records = _required_list(arguments.get("routing_records"), "routing_records")
    if len(prepared_items) != len(routing_records):
        raise ValueError("证据核算分片输入与结果数量不一致")

    records_by_index: dict[int, dict[str, Any]] = {}
    for record_position, raw_record in enumerate(routing_records):
        if not isinstance(raw_record, dict):
            raise ValueError(
                f"证据核算结果只能包含对象: record_position={record_position}"
            )
        raw_item_index = raw_record.get("item_index")
        if isinstance(raw_item_index, bool) or not isinstance(raw_item_index, int):
            raise ValueError("证据核算结果 item_index 必须是整数")
        if raw_item_index < 0 or raw_item_index >= len(prepared_items):
            raise ValueError(
                f"证据核算结果引用了无效 item_index: {raw_item_index}"
            )
        if raw_item_index in records_by_index:
            raise ValueError(
                f"证据核算结果 item_index 重复: {raw_item_index}"
            )
        records_by_index[raw_item_index] = raw_record

    plan_identity = ""
    module_count = 0
    global_target_ids: list[str] = []
    normalized_batches: list[tuple[dict[str, Any], list[str], set[str]]] = []
    for item_index, raw_prepared in enumerate(prepared_items):
        if not isinstance(raw_prepared, dict):
            raise ValueError(f"prepared_items 每项必须是对象: item_index={item_index}")
        prepared = dict(raw_prepared)
        if set(prepared) != {
            "draft_plan",
            "target_evidence_items",
            "neighbor_context",
        }:
            raise ValueError(
                f"证据核算分片包含未允许字段: item_index={item_index}"
            )
        draft_plan = prepared.get("draft_plan")
        if not isinstance(draft_plan, dict):
            raise ValueError(f"证据核算分片缺少 draft_plan: item_index={item_index}")
        current_plan_identity = json.dumps(
            draft_plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        current_modules = draft_plan.get("business_modules")
        if not isinstance(current_modules, list) or not current_modules:
            raise ValueError("draft_plan.business_modules 必须是非空数组")
        if item_index == 0:
            plan_identity = current_plan_identity
            module_count = len(current_modules)
        elif current_plan_identity != plan_identity:
            raise ValueError("证据核算分片使用了不一致的 draft_plan")

        raw_targets = prepared.get("target_evidence_items")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ValueError(
                f"证据核算分片目标证据不能为空: item_index={item_index}"
            )
        if len(raw_targets) > MAX_EVIDENCE_ACCOUNTING_ITEMS_PER_BATCH:
            raise ValueError(
                f"证据核算分片超过最大项数: item_index={item_index}"
            )
        target_ids: list[str] = []
        for raw_target in raw_targets:
            if not isinstance(raw_target, dict):
                raise ValueError("目标证据项必须是对象")
            evidence_id = str(raw_target.get("evidence_id") or "").strip()
            _evidence_chunk_from_catalog_item(raw_target)
            if not re.fullmatch(r"EV-\d{4,}", evidence_id):
                raise ValueError(f"目标证据 ID 格式无效: evidence_id={evidence_id}")
            if evidence_id in target_ids or evidence_id in global_target_ids:
                raise ValueError(f"目标证据 ID 重复: evidence_id={evidence_id}")
            target_ids.append(evidence_id)
            global_target_ids.append(evidence_id)

        raw_neighbors = prepared.get("neighbor_context")
        if not isinstance(raw_neighbors, list):
            raise ValueError("neighbor_context 必须是数组")
        neighbor_ids: set[str] = set()
        neighbor_text_chars = 0
        for neighbor in raw_neighbors:
            if not isinstance(neighbor, dict):
                raise ValueError("neighbor_context 只能包含对象")
            if set(neighbor) != {
                "relative_position",
                "evidence_id",
                "page_number",
                "chunk_index",
                "text",
                "text_truncated",
            }:
                raise ValueError("neighbor_context 字段不完整或包含额外字段")
            if neighbor.get("relative_position") not in {"previous", "next"}:
                raise ValueError("neighbor_context.relative_position 无效")
            evidence_id = str(neighbor.get("evidence_id") or "").strip()
            if not re.fullmatch(r"EV-\d{4,}", evidence_id):
                raise ValueError("neighbor_context.evidence_id 格式无效")
            if evidence_id in neighbor_ids:
                raise ValueError("neighbor_context 包含重复证据 ID")
            neighbor_ids.add(evidence_id)
            neighbor_text_chars += len(str(neighbor.get("text") or ""))
        if neighbor_text_chars > MAX_EVIDENCE_ACCOUNTING_NEIGHBOR_CHARS:
            raise ValueError("neighbor_context 超过总字符预算")
        if neighbor_ids & set(target_ids):
            raise ValueError("neighbor_context 不能包含当前分片的目标证据")
        normalized_batches.append((prepared, target_ids, neighbor_ids))

    merged_accounting: list[dict[str, Any]] = []
    for item_index, (prepared, target_ids, neighbor_ids) in enumerate(normalized_batches):
        record = records_by_index[item_index]
        if record.get("input_hash") != _stable_payload_hash(prepared):
            raise ValueError(
                f"证据核算结果与准备输入指纹不一致: item_index={item_index}"
            )
        output = record.get("output")
        if not isinstance(output, dict) or set(output) != {"evidence_accounting"}:
            raise ValueError(
                f"证据核算分片输出顶层只允许 evidence_accounting: item_index={item_index}"
            )
        raw_accounting = output.get("evidence_accounting")
        if not isinstance(raw_accounting, list):
            raise ValueError("evidence_accounting 必须是数组")
        accounting_by_id: dict[str, dict[str, Any]] = {}
        for raw_accounting_item in raw_accounting:
            normalized = _normalize_batch_accounting_item(
                raw_item=raw_accounting_item,
                module_count=module_count,
            )
            evidence_id = normalized["evidence_id"]
            if evidence_id in neighbor_ids:
                raise ValueError(
                    "证据核算分片输出不得记账 neighbor_context: "
                    f"item_index={item_index}, evidence_id={evidence_id}"
                )
            if evidence_id in accounting_by_id:
                raise ValueError(
                    f"证据核算分片输出重复 ID: evidence_id={evidence_id}"
                )
            accounting_by_id[evidence_id] = normalized
        if set(accounting_by_id) != set(target_ids):
            raise ValueError(
                "证据核算分片输出未严格覆盖目标 ID 全集: "
                f"item_index={item_index}, expected={target_ids}, "
                f"actual={list(accounting_by_id)}"
            )
        merged_accounting.extend(accounting_by_id[evidence_id] for evidence_id in target_ids)

    context.artifacts["evidence_accounting_merge"] = {
        "batch_count": len(normalized_batches),
        "evidence_count": len(merged_accounting),
        "evidence_ids": list(global_target_ids),
    }
    return {"evidence_accounting": merged_accounting}


def _validate_evidence_accounting(
    *,
    routing: dict[str, Any],
    catalog_index: dict[str, dict[str, Any]],
    module_count: int,
) -> dict[str, dict[str, Any]]:
    """验证 Reviewer 对完整证据目录的逐项记账。"""

    raw_accounting = routing.get("evidence_accounting")
    if not isinstance(raw_accounting, list):
        raise ValueError("routing.evidence_accounting 必须是数组")

    accounting_by_id: dict[str, dict[str, Any]] = {}
    for accounting_position, raw_item in enumerate(raw_accounting):
        if not isinstance(raw_item, dict):
            raise ValueError(
                "evidence_accounting 只能包含对象: "
                f"accounting_position={accounting_position}"
            )
        raw_evidence_id = raw_item.get("evidence_id")
        if not isinstance(raw_evidence_id, str) or not re.fullmatch(
            r"EV-\d{4,}", raw_evidence_id.strip()
        ):
            raise ValueError(
                "evidence_accounting.evidence_id 格式无效: "
                f"accounting_position={accounting_position}, "
                f"evidence_id={raw_evidence_id}"
            )
        evidence_id = raw_evidence_id.strip()
        if evidence_id in accounting_by_id:
            raise ValueError(
                "evidence_accounting 包含重复证据 ID: "
                f"evidence_id={evidence_id}"
            )
        if evidence_id not in catalog_index:
            raise ValueError(
                "evidence_accounting 包含未知证据 ID: "
                f"evidence_id={evidence_id}"
            )

        raw_module_indexes = raw_item.get("module_indexes")
        if not isinstance(raw_module_indexes, list):
            raise ValueError(
                "evidence_accounting.module_indexes 必须是数组: "
                f"evidence_id={evidence_id}"
            )
        module_indexes: list[int] = []
        seen_module_indexes: set[int] = set()
        for module_position, raw_module_index in enumerate(raw_module_indexes):
            if isinstance(raw_module_index, bool) or not isinstance(
                raw_module_index, int
            ):
                raise ValueError(
                    "evidence_accounting.module_indexes 只能包含整数: "
                    f"evidence_id={evidence_id}, module_position={module_position}, "
                    f"module_index={raw_module_index}"
                )
            if raw_module_index < 0 or raw_module_index >= module_count:
                raise ValueError(
                    "evidence_accounting 包含越界 module_index: "
                    f"evidence_id={evidence_id}, module_index={raw_module_index}, "
                    f"module_count={module_count}"
                )
            if raw_module_index in seen_module_indexes:
                raise ValueError(
                    "evidence_accounting.module_indexes 包含重复下标: "
                    f"evidence_id={evidence_id}, module_index={raw_module_index}"
                )
            seen_module_indexes.add(raw_module_index)
            module_indexes.append(raw_module_index)

        disposition = raw_item.get("disposition")
        if disposition not in {"assigned", "context_only", "plan_gap"}:
            raise ValueError(
                "evidence_accounting.disposition 无效: "
                f"evidence_id={evidence_id}, disposition={disposition}"
            )
        if disposition == "assigned" and not module_indexes:
            raise ValueError(
                "assigned 证据必须包含至少一个 module_index: "
                f"evidence_id={evidence_id}"
            )
        if disposition in {"context_only", "plan_gap"} and module_indexes:
            raise ValueError(
                f"{disposition} 证据的 module_indexes 必须为空: "
                f"evidence_id={evidence_id}, module_indexes={module_indexes}"
            )

        reason = raw_item.get("reason")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason.strip()) > 160
        ):
            raise ValueError(
                "evidence_accounting.reason 必须是 1 至 160 字的字符串: "
                f"evidence_id={evidence_id}"
            )
        accounting_by_id[evidence_id] = {
            "evidence_id": evidence_id,
            "module_indexes": module_indexes,
            "disposition": disposition,
            "reason": reason.strip(),
        }

    missing_evidence_ids = [
        evidence_id
        for evidence_id in catalog_index
        if evidence_id not in accounting_by_id
    ]
    if missing_evidence_ids:
        raise ValueError(
            "evidence_accounting 未完整覆盖证据目录: "
            f"missing_evidence_ids={missing_evidence_ids}"
        )

    plan_gaps = [
        {
            "evidence_id": evidence_id,
            "reason": accounting_by_id[evidence_id]["reason"],
        }
        for evidence_id in catalog_index
        if accounting_by_id[evidence_id]["disposition"] == "plan_gap"
    ]
    if plan_gaps:
        raise ValueError(
            "证据复核发现业务规划缺口，必须返回规划节点修正后再路由: "
            f"plan_gaps={plan_gaps}"
        )

    return accounting_by_id


def merge_plan_evidence_routing(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把 Reviewer 形成的唯一证据总账确定性合并回业务规划。"""

    draft_plan = dict(arguments.get("draft_plan") or {})
    modules = [
        dict(item)
        for item in _required_list(
            draft_plan.get("business_modules"),
            "draft_plan.business_modules",
        )
    ]
    for module_index, module in enumerate(modules):
        if "evidence_ids" in module:
            raise ValueError(
                "draft_plan 模块不能预置 evidence_ids: "
                f"module_index={module_index}, module={module.get('name')}"
            )

    evidence_catalog = arguments.get("evidence_catalog")
    catalog_document_id = (
        evidence_catalog.get("document_id")
        if isinstance(evidence_catalog, dict)
        else None
    )
    catalog_index = _evidence_catalog_index(
        evidence_catalog,
        source={"document_id": catalog_document_id},
    )

    routing = arguments.get("routing")
    if not isinstance(routing, dict):
        raise ValueError("routing 必须是对象")
    accounting_by_id = _validate_evidence_accounting(
        routing=routing,
        catalog_index=catalog_index,
        module_count=len(modules),
    )
    routes_by_module: dict[int, list[str]] = {
        module_index: [] for module_index in range(len(modules))
    }
    for evidence_id in catalog_index:
        for module_index in accounting_by_id[evidence_id]["module_indexes"]:
            routes_by_module[module_index].append(evidence_id)

    missing_module_indexes = [
        module_index
        for module_index, evidence_ids in routes_by_module.items()
        if not evidence_ids
    ]
    if missing_module_indexes:
        raise ValueError(
            "证据总账未完整覆盖业务模块: "
            f"missing_module_indexes={missing_module_indexes}"
        )

    routed_modules = [
        {
            **module,
            "evidence_ids": list(routes_by_module[module_index]),
        }
        for module_index, module in enumerate(modules)
    ]
    merged_plan = {
        "requirement_summary": str(draft_plan.get("requirement_summary") or ""),
        "business_modules": routed_modules,
        "coverage_focus": draft_plan.get("coverage_focus") or [],
        "risks": draft_plan.get("risks") or [],
    }
    context.artifacts["evidence_routing"] = {
        "document_id": catalog_document_id,
        "module_count": len(routed_modules),
        "evidence_dispositions": {
            disposition: {
                "count": sum(
                    item["disposition"] == disposition
                    for item in accounting_by_id.values()
                ),
                "evidence_ids": [
                    evidence_id
                    for evidence_id in catalog_index
                    if accounting_by_id[evidence_id]["disposition"]
                    == disposition
                ],
            }
            for disposition in ("assigned", "context_only")
        },
        "module_routes": [
            {
                "module_index": module_index,
                "module_name": str(module.get("name") or ""),
                "evidence_ids": list(routes_by_module[module_index]),
            }
            for module_index, module in enumerate(modules)
        ],
    }
    return merged_plan


def prepare_test_case_batches(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """依据业务规划和预算生成通用、可追踪的 Agent 映射输入。"""

    plan = dict(arguments.get("plan") or {})
    modules = [dict(item) for item in _required_list(plan.get("business_modules"), "business_modules")]
    raw_effective_facts = _required_list(arguments.get("effective_facts"), "effective_facts")
    effective_facts: list[dict[str, Any]] = []
    seen_fact_ids: set[str] = set()
    for raw_fact in raw_effective_facts:
        if not isinstance(raw_fact, dict):
            raise ValueError("effective_facts 只能包含对象")
        fact = dict(raw_fact)
        fact_id = str(fact.get("fact_id") or "").strip()
        if not fact_id or fact_id in seen_fact_ids:
            raise ValueError(f"effective_facts fact_id 无效或重复: {fact_id}")
        if str(fact.get("status") or "") != "effective":
            raise ValueError(f"prepare_batches 只接受 effective 事实: fact_id={fact_id}")
        if not str(fact.get("scope_id") or "").strip():
            raise ValueError(f"effective fact 缺少 scope_id: fact_id={fact_id}")
        seen_fact_ids.add(fact_id)
        effective_facts.append(fact)
    case_budget = int(arguments.get("case_budget") or 0)
    if case_budget < 1:
        raise ValueError("用例预算必须大于 0")
    batch_case_limit = int(arguments.get("batch_case_limit") or 8)
    if batch_case_limit < 1 or batch_case_limit > 20:
        raise ValueError("batch_case_limit 必须在 1 到 20 之间")

    allocated_focus = _allocate_coverage_points(modules, plan.get("coverage_focus"))
    allocated_risks = _allocate_risks(modules, plan.get("risks"))
    base_target, remainder = divmod(case_budget, len(modules))
    items: list[dict[str, Any]] = []
    batch_number = 0
    for module_index, module in enumerate(modules):
        module_target = base_target + (1 if module_index < remainder else 0)
        if module_target <= 0:
            continue
        module_batch_count = math.ceil(module_target / batch_case_limit)
        remaining = module_target
        module_points = allocated_focus[module_index]
        points_by_batch = [
            module_points[index::module_batch_count]
            for index in range(module_batch_count)
        ]
        module_risks = allocated_risks[module_index]
        risks_by_batch = [
            module_risks[index::module_batch_count]
            for index in range(module_batch_count)
        ]
        module_scope_ids = set(_module_evidence_ids(module))
        module_facts = [
            dict(fact)
            for fact in effective_facts
            if str(fact.get("scope_id") or "") in module_scope_ids
        ]
        if not module_facts:
            raise ValueError(
                "业务模块没有可达的 effective authoritative facts: "
                f"module_index={module_index}, module={module.get('name')}"
            )
        module_requirement = "\n".join(str(fact["assertion"]) for fact in module_facts)
        for module_batch_index in range(module_batch_count):
            batch_target = min(batch_case_limit, remaining)
            remaining -= batch_target
            batch_number += 1
            focus = _batch_focus(module, points_by_batch[module_batch_index])
            items.append(
                {
                    "requirement": module_requirement,
                    "plan": {
                        "requirement_summary": str(plan.get("requirement_summary") or ""),
                        "business_module": module,
                        "coverage_focus": focus,
                        "risks": risks_by_batch[module_batch_index],
                    },
                    "case_budget": batch_target,
                    "batch": {
                        "batch_number": batch_number,
                        "module_index": module_index,
                        "module_batch_index": module_batch_index,
                        "module_batch_count": module_batch_count,
                        "module_name": str(module.get("name") or ""),
                        "coverage_focus": focus,
                    },
                    "authoritative_facts": module_facts,
                }
            )

    for index, item in enumerate(items, start=1):
        item["batch"]["batch_number"] = index
        item["batch"]["batch_count"] = len(items)
    context.artifacts["generation_batch_plan"] = {
        "case_budget": case_budget,
        "batch_case_limit": batch_case_limit,
        "batch_count": len(items),
        "batches": [
            {
                **dict(item["batch"]),
                "case_budget": int(item["case_budget"]),
                "authoritative_fact_ids": [
                    str(fact["fact_id"]) for fact in item["authoritative_facts"]
                ],
            }
            for item in items
        ],
    }
    return {"items": items, "batch_count": len(items), "case_budget": case_budget}




def merge_grounded_generation_batches(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """合并生成结果，并在平台侧一次性校验数量、模块和逐字段事实绑定。"""

    generation_inputs = _required_list(arguments.get("generation_inputs"), "generation_inputs")
    generation_records = _required_list(arguments.get("generation_records"), "generation_records")
    case_budget = int(arguments.get("case_budget") or 0)
    if case_budget < 1:
        raise ValueError("用例预算必须大于 0")
    if len(generation_inputs) != len(generation_records):
        raise ValueError("生成输入与生成结果数量不一致")

    merged_cases: list[dict[str, Any]] = []
    merged_bindings: list[dict[str, Any]] = []
    seen_case_identities: set[tuple[str, str]] = set()
    for item_index, raw_input in enumerate(generation_inputs):
        if not isinstance(raw_input, dict):
            raise ValueError("generation_inputs 每项必须是对象")
        source_input = dict(raw_input)
        record = generation_records[item_index]
        if not isinstance(record, dict) or int(record.get("item_index", -1)) != item_index:
            raise ValueError(f"生成结果顺序与输入不一致: item_index={item_index}")
        output = record.get("output")
        if not isinstance(output, dict):
            raise ValueError(f"生成结果缺少 output: item_index={item_index}")
        test_cases = _required_list(output.get("test_cases"), "test_cases")
        expected_count = int(source_input.get("case_budget") or 0)
        if len(test_cases) != expected_count:
            raise ValueError(
                "生成批次没有精确达到分配数量: "
                f"item_index={item_index}, target={expected_count}, actual={len(test_cases)}"
            )
        module_name = str(
            dict(source_input.get("batch") or {}).get("module_name")
            or dict(dict(source_input.get("plan") or {}).get("business_module") or {}).get("name")
            or ""
        ).strip()
        if not module_name:
            raise ValueError(f"生成批次缺少模块名称: item_index={item_index}")
        bindings = validate_case_fact_bindings(
            test_cases=test_cases,
            raw_bindings=output.get("case_fact_bindings"),
            authoritative_facts=source_input.get("authoritative_facts"),
            expected_module_name=module_name,
        )
        bindings_by_case_id = binding_index(bindings)
        for raw_case in test_cases:
            if not isinstance(raw_case, dict):
                raise ValueError("test_cases 每项必须是对象")
            case = dict(raw_case)
            source_case_id = str(case.get("case_id") or "").strip()
            identity = (_identity(case.get("module")), _identity(case.get("title")))
            if identity in seen_case_identities:
                raise ValueError(
                    f"跨批次生成了重复用例: module={case.get('module')}, title={case.get('title')}"
                )
            seen_case_identities.add(identity)
            case_id = f"TC-{len(merged_cases) + 1:03d}"
            case["case_id"] = case_id
            merged_cases.append(case)
            merged_bindings.append(
                replace_binding_case_id(
                    bindings_by_case_id[source_case_id],
                    case_id=case_id,
                )
            )

    if len(merged_cases) != case_budget:
        raise ValueError(
            f"生成结果没有达到精确目标: target={case_budget}, actual={len(merged_cases)}"
        )
    context.artifacts["grounded_generation_merge"] = {
        "batch_count": len(generation_inputs),
        "case_count": len(merged_cases),
        "case_budget": case_budget,
    }
    return {
        "test_cases": merged_cases,
        "case_fact_bindings": merged_bindings,
        "batch_count": len(generation_inputs),
        "case_count": len(merged_cases),
    }


def prepare_execution_chain_context(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """确定性计算严格可达的少量主链候选，避免把全部用例交给 Agent。"""

    cases = _required_list(arguments.get("test_cases"), "test_cases")
    _, transitions = _index_case_transitions(cases)
    transition_by_id = {item["case_id"]: item for item in transitions}
    adjacency: dict[str, list[str]] = {}
    for source in transitions:
        adjacency[source["case_id"]] = [
            target["case_id"]
            for target in transitions
            if target["case_id"] != source["case_id"]
            and source["to_state"] in target["entry_states"]
            and source["to_state"] != target["to_state"]
        ]

    edge_seeds = [
        (source_id, target_id)
        for source_id, target_ids in adjacency.items()
        for target_id in target_ids
    ]
    if not edge_seeds:
        plan = dict(arguments.get("plan") or {})
        plan_summary = {
            "requirement_summary": str(plan.get("requirement_summary") or ""),
            "business_modules": [
                {
                    "name": str((raw_module or {}).get("name") or ""),
                    "objective": str((raw_module or {}).get("objective") or ""),
                }
                for raw_module in (plan.get("business_modules") or [])
                if isinstance(raw_module, dict)
            ],
        }
        context.artifacts["execution_chain_candidates"] = {
            "eligible_case_count": len(transitions),
            "strict_edge_count": 0,
            "candidate_count": 0,
            "candidate_case_counts": [],
            "execution_mode": "collection_only",
        }
        return {
            "plan_summary": plan_summary,
            "candidate_chains": [],
        }

    priority_order = {"P0": 0, "P1": 1, "P2": 2}

    def next_rank(case_id: str) -> tuple[int, int]:
        item = transition_by_id[case_id]
        return (
            priority_order.get(str(item.get("priority") or ""), 3),
            int(item["source_index"]),
        )

    candidate_paths: set[tuple[str, ...]] = set()
    for source_id, target_id in edge_seeds:
        path = [source_id, target_id]
        while len(path) < 12:
            next_ids = [
                case_id
                for case_id in adjacency.get(path[-1], [])
                if case_id not in path
            ]
            if not next_ids:
                break
            path.append(min(next_ids, key=next_rank))
        candidate_paths.add(tuple(path))

    def path_rank(path: tuple[str, ...]) -> tuple[int, int, tuple[int, ...]]:
        return (
            -len(path),
            sum(next_rank(case_id)[0] for case_id in path),
            tuple(int(transition_by_id[case_id]["source_index"]) for case_id in path),
        )

    selected_paths = sorted(candidate_paths, key=path_rank)[:6]
    candidates: list[dict[str, Any]] = []
    for candidate_index, path in enumerate(selected_paths, start=1):
        case_summaries: list[dict[str, Any]] = []
        previous_to_state: str | None = None
        for case_id in path:
            item = transition_by_id[case_id]
            from_state = (
                previous_to_state
                if previous_to_state is not None
                else item["entry_states"][0]
            )
            case_summaries.append(
                {
                    "case_id": case_id,
                    "title": item["title"],
                    "module": item["module"],
                    "priority": item["priority"],
                    "from_state": from_state,
                    "to_state": item["to_state"],
                    "first_action": item["first_action"],
                    "last_action": item["last_action"],
                }
            )
            previous_to_state = item["to_state"]
        candidates.append(
            {
                "candidate_id": f"chain-candidate-{candidate_index:02d}",
                "case_ids": list(path),
                "cases": case_summaries,
            }
        )

    plan = dict(arguments.get("plan") or {})
    plan_summary = {
        "requirement_summary": str(plan.get("requirement_summary") or ""),
        "business_modules": [
            {
                "name": str((raw_module or {}).get("name") or ""),
                "objective": str((raw_module or {}).get("objective") or ""),
            }
            for raw_module in (plan.get("business_modules") or [])
            if isinstance(raw_module, dict)
        ],
    }
    context.artifacts["execution_chain_candidates"] = {
        "eligible_case_count": len(transitions),
        "strict_edge_count": len(edge_seeds),
        "candidate_count": len(candidates),
        "candidate_case_counts": [len(item["case_ids"]) for item in candidates],
    }
    return {
        "plan_summary": plan_summary,
        "candidate_chains": candidates,
    }


def _index_case_transitions(
    cases: list[Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """提取可参与严格状态迁移的用例，不归一化或改写任何状态事实。"""

    cases_by_id: dict[str, dict[str, Any]] = {}
    transitions: list[dict[str, Any]] = []
    for source_index, raw_case in enumerate(cases):
        if not isinstance(raw_case, dict):
            raise ValueError("test_cases 每项必须是对象")
        case = dict(raw_case)
        case_id = str(case.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("源测试用例 case_id 不能为空")
        if case_id in cases_by_id:
            raise ValueError(f"源测试用例 case_id 重复: {case_id}")
        cases_by_id[case_id] = case

        steps = list(case.get("steps") or [])
        terminal_state = str((steps[-1] if steps else {}).get("expected") or "")
        entry_states: list[str] = []
        for raw_state in case.get("preconditions") or []:
            state = str(raw_state or "")
            if state and state != terminal_state and state not in entry_states:
                entry_states.append(state)
        if not terminal_state or not entry_states:
            continue
        transitions.append(
            {
                "case_id": case_id,
                "title": str(case.get("title") or ""),
                "module": str(case.get("module") or ""),
                "priority": str(case.get("priority") or ""),
                "entry_states": entry_states,
                "to_state": terminal_state,
                "first_action": str((steps[0] if steps else {}).get("action") or ""),
                "last_action": str((steps[-1] if steps else {}).get("action") or ""),
                "source_index": source_index,
            }
        )
    return cases_by_id, transitions


def validate_execution_chain(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验 Agent 的主链选择，并确定性构造完整执行套件。"""

    cases = _required_list(arguments.get("test_cases"), "test_cases")
    cases_by_id, transitions = _index_case_transitions(cases)
    transition_by_id = {item["case_id"]: item for item in transitions}
    known_ids = set(cases_by_id)
    chain_selection = dict(arguments.get("chain_selection") or {})
    if set(chain_selection) != {"name", "goal", "case_ids"}:
        raise ValueError("chain_selection 只能包含 name、goal、case_ids")
    name = str(chain_selection.get("name") or "").strip()
    goal = str(chain_selection.get("goal") or "").strip()
    selected_ids = [
        str(item).strip() for item in (chain_selection.get("case_ids") or [])
    ]

    strict_edge_count = sum(
        1
        for source in transitions
        for target in transitions
        if target["case_id"] != source["case_id"]
        and source["to_state"] in target["entry_states"]
        and source["to_state"] != target["to_state"]
    )
    if strict_edge_count == 0:
        if name or goal or selected_ids:
            raise ValueError("无可靠状态边时不能伪造执行主链")
        collection_ids_by_module: dict[str, list[str]] = {}
        for raw_case in cases:
            case = dict(raw_case)
            case_id = str(case.get("case_id") or "").strip()
            module = str(case.get("module") or "").strip()
            if not module:
                raise ValueError(f"测试用例 module 不能为空: case_id={case_id}")
            collection_ids_by_module.setdefault(module, []).append(case_id)
        suites = [
            {
                "suite_id": f"suite-module-{module_index:03d}",
                "name": f"{module}用例集",
                "goal": f"执行{module}模块独立用例",
                "suite_type": "collection",
                "case_ids": case_ids,
                "transitions": [],
            }
            for module_index, (module, case_ids) in enumerate(
                collection_ids_by_module.items(), start=1
            )
        ]
        assigned = [
            case_id
            for case_ids in collection_ids_by_module.values()
            for case_id in case_ids
        ]
        if len(assigned) != len(set(assigned)) or set(assigned) != known_ids:
            raise ValueError("执行套件未能把每条测试用例恰好分配一次")
        result = {
            "status": "passed",
            "suite_count": len(suites),
            "assigned_count": len(assigned),
            "main_chain_case_count": 0,
            "execution_plan": {
                "main_chain_suite_id": "",
                "suites": suites,
            },
        }
        context.artifacts["execution_plan_validation"] = {
            key: value for key, value in result.items() if key != "execution_plan"
        }
        return result

    if not name or not goal:
        raise ValueError("主链 name 和 goal 不能为空")
    if len(selected_ids) < 2:
        raise ValueError("execution_chain_not_reachable: 主链至少需要两条连续用例")
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("主链不能重复选择同一测试用例")
    unknown = set(selected_ids) - known_ids
    if unknown:
        raise ValueError(f"主链引用未知用例: {sorted(unknown)}")

    chain_transitions: list[dict[str, str]] = []
    previous_to_state: str | None = None
    for transition_index, case_id in enumerate(selected_ids):
        item = transition_by_id.get(case_id)
        if item is None:
            raise ValueError(f"无有效入口或终态的用例不能进入主链: case_id={case_id}")
        if previous_to_state is None:
            from_state = item["entry_states"][0]
        elif previous_to_state in item["entry_states"]:
            from_state = previous_to_state
        else:
            raise ValueError(
                "chain 相邻迁移不连续: "
                f"transition_index={transition_index}, "
                f"previous_to_state={previous_to_state}, "
                f"case_id={case_id}"
            )
        to_state = item["to_state"]
        chain_transitions.append(
            {
                "case_id": case_id,
                "from_state": from_state,
                "to_state": to_state,
            }
        )
        previous_to_state = to_state

    suites: list[dict[str, Any]] = [
        {
            "suite_id": "suite-main",
            "name": name,
            "goal": goal,
            "suite_type": "chain",
            "case_ids": [],
            "transitions": chain_transitions,
        }
    ]
    selected_id_set = set(selected_ids)
    collection_ids_by_module: dict[str, list[str]] = {}
    for raw_case in cases:
        case = dict(raw_case)
        case_id = str(case.get("case_id") or "").strip()
        if case_id in selected_id_set:
            continue
        module = str(case.get("module") or "").strip()
        if not module:
            raise ValueError(f"测试用例 module 不能为空: case_id={case_id}")
        collection_ids_by_module.setdefault(module, []).append(case_id)
    for module_index, (module, case_ids) in enumerate(
        collection_ids_by_module.items(), start=1
    ):
        suites.append(
            {
                "suite_id": f"suite-module-{module_index:03d}",
                "name": f"{module}用例集",
                "goal": f"执行{module}模块未进入主链的独立用例",
                "suite_type": "collection",
                "case_ids": case_ids,
                "transitions": [],
            }
        )

    execution_plan = {
        "main_chain_suite_id": "suite-main",
        "suites": suites,
    }
    assigned = selected_ids + [
        case_id
        for case_ids in collection_ids_by_module.values()
        for case_id in case_ids
    ]
    if len(assigned) != len(set(assigned)) or set(assigned) != known_ids:
        raise ValueError("执行套件未能把每条测试用例恰好分配一次")
    result = {
        "status": "passed",
        "suite_count": len(suites),
        "assigned_count": len(assigned),
        "main_chain_case_count": len(selected_ids),
        "execution_plan": execution_plan,
    }
    context.artifacts["execution_plan_validation"] = {
        key: value for key, value in result.items() if key != "execution_plan"
    }
    return result
