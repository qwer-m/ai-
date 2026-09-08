from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from copy import deepcopy
from typing import Any, TYPE_CHECKING

from modules.knowledge_base_components.adapters.chroma_vector_store import get_vector_store
from modules.knowledge_base_components.document.document_asset_service import (
    detect_high_confidence_page_continuations,
    document_page_text,
    load_document_manifest,
)

from .test_generation_facts import (
    binding_index,
    bound_fact_ids,
    derive_test_design_item_ids,
    index_effective_facts,
    materialize_inline_grounding,
    replace_binding_case_id,
    validate_case_fact_bindings,
)
from .output_repair import OutputRepairError, repairable_output
from .test_generation_repair import GENERATION_REPAIR_STRATEGY

if TYPE_CHECKING:
    from .registry import ToolExecutionContext


GENERATION_MAX_PAGES_PER_BATCH = 10
GENERATION_MAX_FACT_JSON_CHARS_PER_BATCH = 16_000
# 逐字段绑定会随 fact_id 数量增加输出校验复杂度，单独限制契约规模。
GENERATION_MAX_REQUIRED_FACTS_PER_BATCH = 16




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


def _module_fact_ids(module: dict[str, Any]) -> list[str]:
    """严格读取事实路由结果，不再由证据范围隐式扩张事实集合。"""

    raw_ids = module.get("fact_ids")
    module_name = str(module.get("name") or "")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError(f"模块 fact_ids 必须是非空数组: module={module_name}")
    fact_ids = [str(value or "").strip() for value in raw_ids]
    if not all(fact_ids) or len(fact_ids) != len(set(fact_ids)):
        raise ValueError(f"模块 fact_ids 包含空值或重复 ID: module={module_name}")
    return fact_ids


def _fact_json_chars(fact: dict[str, Any]) -> int:
    """估算单条事实进入模型请求后的结构化字符负载。"""

    return len(json.dumps(fact, ensure_ascii=False, separators=(",", ":")))


def _fact_source_order(
    fact: dict[str, Any],
    *,
    fallback_index: int,
) -> tuple[int, int, int, int, int]:
    """按真实来源坐标形成稳定顺序，内联事实保持原输入顺序。"""

    anchor = dict(fact.get("source_anchor") or {})
    if str(anchor.get("source_kind") or "") == "document":
        span = dict(anchor.get("source_span") or {})
        return (
            0,
            int(anchor.get("document_id") or 0),
            int(anchor.get("page_number") or 0),
            int(span.get("start") or 0),
            fallback_index,
        )
    return (1, 0, 0, 0, fallback_index)


def _fact_document_page(fact: dict[str, Any]) -> tuple[int, int] | None:
    anchor = dict(fact.get("source_anchor") or {})
    if str(anchor.get("source_kind") or "") != "document":
        return None
    document_id = int(anchor.get("document_id") or 0)
    page_number = int(anchor.get("page_number") or 0)
    if document_id < 1 or page_number < 1:
        return None
    return document_id, page_number


def _fact_source_group(fact: dict[str, Any]) -> tuple[str, int]:
    anchor = dict(fact.get("source_anchor") or {})
    source_kind = str(anchor.get("source_kind") or "inline").strip() or "inline"
    if source_kind == "document":
        return source_kind, int(anchor.get("document_id") or 0)
    return source_kind, 0


def _generation_fact_groups(
    facts: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """按来源文档和通用负载上限构造生成上下文包。"""

    ordered_facts = [
        fact
        for _, fact in sorted(
            enumerate(facts),
            key=lambda item: _fact_source_order(item[1], fallback_index=item[0]),
        )
    ]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    current_source_group: tuple[str, int] | None = None
    current_pages: set[int] = set()

    for fact in ordered_facts:
        source_group = _fact_source_group(fact)
        location = _fact_document_page(fact)
        fact_page = location[1] if location is not None else None
        fact_chars = _fact_json_chars(fact)
        if fact_chars > GENERATION_MAX_FACT_JSON_CHARS_PER_BATCH:
            raise ValueError(
                "单条权威事实超过生成上下文字符上限: "
                f"fact_id={fact.get('fact_id')}, chars={fact_chars}"
            )
        next_pages = set(current_pages)
        if fact_page is not None:
            next_pages.add(fact_page)
        exceeds_limits = bool(current) and (
            current_chars + fact_chars > GENERATION_MAX_FACT_JSON_CHARS_PER_BATCH
            or len(current) >= GENERATION_MAX_REQUIRED_FACTS_PER_BATCH
            or len(next_pages) > GENERATION_MAX_PAGES_PER_BATCH
            or current_source_group != source_group
        )
        if exceeds_limits:
            groups.append(current)
            current = []
            current_chars = 0
            current_source_group = None
            current_pages = set()

        current.append(fact)
        current_chars += fact_chars
        current_source_group = source_group
        if fact_page is not None:
            current_pages.add(fact_page)

    if current:
        groups.append(current)
    return groups


def _split_generation_fact_group(
    facts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """优先在页面边界把上下文包拆成结构负载接近的两半。"""

    if len(facts) < 2:
        return None
    cumulative: list[int] = []
    running = 0
    for fact in facts:
        running += _fact_json_chars(fact)
        cumulative.append(running)
    total = cumulative[-1]
    candidates: list[tuple[int, int, int]] = []
    for index in range(1, len(facts)):
        left_page = _fact_document_page(facts[index - 1])
        right_page = _fact_document_page(facts[index])
        same_page_penalty = int(left_page == right_page)
        balance = abs(total - 2 * cumulative[index - 1])
        candidates.append((same_page_penalty, balance, index))
    _, _, split_index = min(candidates)
    return facts[:split_index], facts[split_index:]


def _expand_generation_fact_groups(
    groups: list[list[dict[str, Any]]],
    *,
    target_count: int,
) -> list[list[dict[str, Any]]]:
    """为用例预算扩展上下文包数量，同时保证事实不复制、不丢失。"""

    expanded = [list(group) for group in groups]
    while len(expanded) < target_count:
        candidates = [
            (sum(_fact_json_chars(fact) for fact in group), len(group), index)
            for index, group in enumerate(expanded)
            if len(group) >= 2
        ]
        if not candidates:
            raise ValueError(
                "有效事实不足以按 batch_case_limit 拆分生成包，禁止复制事实凑批次"
            )
        _, _, target_index = max(candidates)
        split = _split_generation_fact_group(expanded[target_index])
        if split is None:
            raise ValueError("生成上下文包无法继续拆分")
        expanded[target_index : target_index + 1] = [split[0], split[1]]
    return expanded


def _allocate_generation_batch_budgets(
    groups: list[list[dict[str, Any]]],
    *,
    case_target: int,
    batch_case_limit: int,
) -> list[int]:
    """按事实负载为上下文包分配精确用例数。"""

    if not groups or case_target < len(groups):
        raise ValueError("生成上下文包数量超过当前模块的用例预算")
    budgets = [1 for _ in groups]
    remaining = case_target - len(groups)
    while remaining > 0:
        candidates = [
            index for index, budget in enumerate(budgets) if budget < batch_case_limit
        ]
        if not candidates:
            raise ValueError("模块用例预算超过 batch_case_limit 可承载范围")
        target_index = max(
            candidates,
            key=lambda index: (
                len(groups[index]) / (budgets[index] + 1),
                sum(_fact_json_chars(fact) for fact in groups[index])
                / (budgets[index] + 1),
                -index,
            ),
        )
        budgets[target_index] += 1
        remaining -= 1
    return budgets


def _allocate_module_case_targets(
    *,
    module_facts_by_index: list[list[dict[str, Any]]],
    module_fact_groups_by_index: list[list[list[dict[str, Any]]]],
    case_budget: int,
    batch_case_limit: int,
) -> list[int]:
    """先满足真实上下文包的最低用例数，再按事实密度分配剩余额度。"""

    minimum_targets = [len(groups) for groups in module_fact_groups_by_index]
    minimum_required = sum(minimum_targets)
    if case_budget < minimum_required:
        raise ValueError(
            "总用例预算不足以覆盖全部真实来源上下文包: "
            f"case_budget={case_budget}, minimum_required={minimum_required}"
        )

    facts_per_case = 10
    while True:
        module_targets = [
            max(minimum_target, math.ceil(len(module_facts) / facts_per_case))
            for module_facts, minimum_target in zip(
                module_facts_by_index,
                minimum_targets,
                strict=True,
            )
        ]
        if sum(module_targets) <= case_budget:
            break
        facts_per_case += 1

    remaining_budget = case_budget - sum(module_targets)
    while remaining_budget > 0:
        candidates = [
            index
            for index, module_facts in enumerate(module_facts_by_index)
            if math.ceil((module_targets[index] + 1) / batch_case_limit)
            <= len(module_facts)
        ]
        if not candidates:
            raise ValueError(
                "有效事实不足以承载总用例预算，禁止复制事实凑生成包: "
                f"case_budget={case_budget}"
            )
        module_index = max(
            candidates,
            key=lambda index: (
                len(module_facts_by_index[index]) / module_targets[index],
                sum(_fact_json_chars(fact) for fact in module_facts_by_index[index])
                / module_targets[index],
                -index,
            ),
        )
        module_targets[module_index] += 1
        remaining_budget -= 1
    return module_targets


def _generation_batch_source_context(
    *,
    module: dict[str, Any],
    facts: list[dict[str, Any]],
    coverage_points: list[str],
) -> dict[str, Any]:
    """形成只用于关联和诊断的摘要索引，原始事实仍是唯一生成依据。"""

    document_ids: list[int] = []
    page_numbers: list[int] = []
    scope_ids: list[str] = []
    for fact in facts:
        location = _fact_document_page(fact)
        if location is not None:
            if location[0] not in document_ids:
                document_ids.append(location[0])
            if location[1] not in page_numbers:
                page_numbers.append(location[1])
        scope_id = str(fact.get("scope_id") or "").strip()
        if scope_id and scope_id not in scope_ids:
            scope_ids.append(scope_id)
    keywords: list[str] = []
    for raw_keyword in [str(module.get("name") or ""), *coverage_points]:
        keyword = raw_keyword.strip()
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    return {
        "source_document_ids": sorted(document_ids),
        "source_page_numbers": sorted(page_numbers),
        "source_scope_ids": scope_ids,
        "semantic_summary": _batch_focus(module, coverage_points),
        "semantic_keywords": keywords,
        "fact_count": len(facts),
        "fact_json_chars": sum(_fact_json_chars(fact) for fact in facts),
    }


def _test_design_items_by_module(
    *,
    modules: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """从模型规划的测试方法确定性展平覆盖项并生成稳定标识。"""

    grouped: list[list[dict[str, Any]]] = [[] for _ in modules]
    for module_index, module in enumerate(modules):
        module_name = str(module.get("name") or "").strip()
        test_points = module.get("test_points")
        if not isinstance(test_points, list) or not test_points:
            raise ValueError(f"业务模块缺少测试点: module={module_name}")
        for point_index, raw_point in enumerate(test_points):
            if not isinstance(raw_point, dict):
                raise ValueError(f"业务模块测试点必须是对象: module={module_name}")
            point = dict(raw_point)
            point_name = str(point.get("name") or "").strip()
            designs = point.get("test_designs")
            if not point_name or not isinstance(designs, list) or not designs:
                raise ValueError(f"测试点缺少名称或测试方法: module={module_name}")
            point_item_number = 0
            for raw_design in designs:
                if not isinstance(raw_design, dict):
                    raise ValueError(f"测试方法必须是对象: point={point_name}")
                design = dict(raw_design)
                technique = str(design.get("technique") or "").strip()
                rationale = str(design.get("rationale") or "").strip()
                coverage_items = design.get("coverage_items")
                if not technique or not rationale or not isinstance(coverage_items, list):
                    raise ValueError(f"测试方法字段不完整: point={point_name}")
                for coverage_intent in coverage_items:
                    point_item_number += 1
                    intent = str(coverage_intent or "").strip()
                    if not intent:
                        raise ValueError(f"测试设计覆盖意图为空: point={point_name}")
                    grouped[module_index].append(
                        {
                            "test_design_item_id": (
                                f"TD-{module_index + 1:03d}-{point_index + 1:03d}-"
                                f"{point_item_number:03d}"
                            ),
                            "module_index": module_index,
                            "module_name": module_name,
                            "test_point": point_name,
                            "technique": technique,
                            "rationale": rationale,
                            "coverage_intent": intent,
                        }
                    )
    if any(not items for items in grouped):
        raise ValueError("每个生效业务模块都必须包含测试设计覆盖项")
    return grouped


def _normalize_fact_design_route_indexes(
    *,
    fact_design_routes: list[dict[str, Any]],
    design_item_count: int,
    expected_fact_ids: set[str],
) -> dict[str, list[int]]:
    """校验事实到设计项路由，并返回便于后续投影的索引表。"""

    route_indexes_by_fact_id: dict[str, list[int]] = {}
    for raw_route in fact_design_routes:
        if not isinstance(raw_route, dict):
            raise ValueError("fact_design_routes 每项必须是对象")
        route = dict(raw_route)
        fact_id = str(route.get("fact_id") or "").strip()
        design_indexes = route.get("test_design_item_indexes")
        if (
            not fact_id
            or fact_id in route_indexes_by_fact_id
            or not isinstance(design_indexes, list)
            or len(design_indexes) != len(set(design_indexes))
            or any(
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < design_item_count
                for index in design_indexes
            )
        ):
            raise ValueError(f"事实到测试设计项路由无效: fact_id={fact_id}")
        route_indexes_by_fact_id[fact_id] = list(design_indexes)

    if set(route_indexes_by_fact_id) != expected_fact_ids:
        raise ValueError("事实到测试设计项路由没有精确覆盖当前模块事实")
    return route_indexes_by_fact_id


def _project_effective_test_design_context(
    *,
    module: dict[str, Any],
    design_items: list[dict[str, Any]],
    effective_fact_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """按权威协调后的有效事实投影设计目录，并重建批次局部索引。"""

    planned_fact_ids = _module_fact_ids(module)
    route_indexes_by_fact_id = _normalize_fact_design_route_indexes(
        fact_design_routes=list(module.get("fact_design_routes") or []),
        design_item_count=len(design_items),
        expected_fact_ids=set(planned_fact_ids),
    )
    if not effective_fact_ids or not effective_fact_ids.issubset(set(planned_fact_ids)):
        raise ValueError("有效事实集合必须是当前模块规划事实的非空子集")

    active_original_indexes = sorted(
        {
            design_index
            for fact_id in effective_fact_ids
            for design_index in route_indexes_by_fact_id[fact_id]
        }
    )
    original_to_active_index = {
        original_index: active_index
        for active_index, original_index in enumerate(active_original_indexes)
    }
    active_design_items = [
        dict(design_items[original_index])
        for original_index in active_original_indexes
    ]
    active_routes = [
        {
            "fact_id": fact_id,
            "test_design_item_indexes": [
                original_to_active_index[index]
                for index in route_indexes_by_fact_id[fact_id]
            ],
        }
        for fact_id in planned_fact_ids
        if fact_id in effective_fact_ids
    ]
    inactive_design_item_ids = [
        str(item.get("test_design_item_id") or "")
        for original_index, item in enumerate(design_items)
        if original_index not in original_to_active_index
    ]
    return active_design_items, active_routes, inactive_design_item_ids


def _allocate_test_design_items_to_fact_groups(
    *,
    design_items: list[dict[str, Any]],
    fact_groups: list[list[dict[str, Any]]],
    fact_design_routes: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """按规划路由 Agent 给出的事实到设计项映射形成批次设计目录。"""

    allocated: list[list[dict[str, Any]]] = [[] for _ in fact_groups]
    allocated_ids: list[set[str]] = [set() for _ in fact_groups]
    grouped_fact_ids = {
        str(fact.get("fact_id") or "")
        for facts in fact_groups
        for fact in facts
    }
    route_indexes_by_fact_id = _normalize_fact_design_route_indexes(
        fact_design_routes=fact_design_routes,
        design_item_count=len(design_items),
        expected_fact_ids=grouped_fact_ids,
    )

    for group_index, facts in enumerate(fact_groups):
        for fact in facts:
            fact_id = str(fact.get("fact_id") or "")
            for design_index in route_indexes_by_fact_id[fact_id]:
                item = design_items[design_index]
                item_id = str(item.get("test_design_item_id") or "")
                if item_id in allocated_ids[group_index]:
                    continue
                allocated[group_index].append(dict(item))
                allocated_ids[group_index].add(item_id)

    expected_design_ids = {
        str(item.get("test_design_item_id") or "") for item in design_items
    }
    assigned_design_ids = set().union(*allocated_ids)
    if assigned_design_ids != expected_design_ids:
        raise ValueError("事实到测试设计项路由没有承接全部规划覆盖项")
    return allocated


def _build_case_fact_contract(
    facts: list[dict[str, Any]],
    *,
    case_budget: int,
    test_design_items: list[dict[str, Any]],
    fact_design_item_ids: dict[str, list[str]],
) -> dict[str, Any]:
    """依据 Planner 路由编译逐用例覆盖槽位，避免模型自行扫描覆盖全集。"""

    if not facts or case_budget < 1:
        raise ValueError("事实覆盖契约需要非空事实和正数用例预算")
    target_case_ids = [f"TC-{index + 1:03d}" for index in range(case_budget)]
    design_item_ids = [
        str(item["test_design_item_id"]) for item in test_design_items
    ]
    available_design_ids = set(design_item_ids)
    for fact in facts:
        fact_id = str(fact["fact_id"])
        routed_design_ids = set(fact_design_item_ids.get(fact_id) or [])
        invalid_design_ids = routed_design_ids - available_design_ids
        if invalid_design_ids:
            raise ValueError(
                "事实槽位引用了批次外测试设计项: "
                f"fact_id={fact_id}, invalid={sorted(invalid_design_ids)}"
            )

    all_fact_ids = [str(fact["fact_id"]) for fact in facts]
    design_ids_by_slot: list[list[str]] = [[] for _ in target_case_ids]
    for design_item_id in design_item_ids:
        target_index = min(
            range(case_budget),
            key=lambda index: (len(design_ids_by_slot[index]), index),
        )
        design_ids_by_slot[target_index].append(design_item_id)

    fact_ids_by_slot: list[list[str]] = [[] for _ in target_case_ids]
    if len(all_fact_ids) >= case_budget:
        base_size, remainder = divmod(len(all_fact_ids), case_budget)
        target_sizes = [
            base_size + int(slot_index < remainder)
            for slot_index in range(case_budget)
        ]
        for fact_id in all_fact_ids:
            routed_design_ids = set(fact_design_item_ids.get(fact_id) or [])
            available_indexes = [
                index
                for index in range(case_budget)
                if len(fact_ids_by_slot[index]) < target_sizes[index]
            ]
            preferred_indexes = [
                index
                for index in available_indexes
                if routed_design_ids.intersection(design_ids_by_slot[index])
            ]
            candidate_indexes = preferred_indexes or available_indexes
            target_index = min(
                candidate_indexes,
                key=lambda index: (len(fact_ids_by_slot[index]), index),
            )
            fact_ids_by_slot[target_index].append(fact_id)
    else:
        # 用例数多于事实数时按设计路由选择最匹配的真实事实并稳定复用。
        for slot_index in range(case_budget):
            slot_design_ids = set(design_ids_by_slot[slot_index])
            matched_fact_id = next(
                (
                    fact_id
                    for fact_id in all_fact_ids
                    if slot_design_ids.intersection(
                        fact_design_item_ids.get(fact_id) or []
                    )
                ),
                all_fact_ids[slot_index % len(all_fact_ids)],
            )
            fact_ids_by_slot[slot_index].append(matched_fact_id)

    for design_item_id in design_item_ids:
        if not any(
            design_item_id in (fact_design_item_ids.get(fact_id) or [])
            for fact_id in all_fact_ids
        ):
            raise ValueError(
                "批次测试设计项没有可承接的真实事实: "
                f"test_design_item_id={design_item_id}"
            )

    # 只把当前批次事实可承接的设计项路由写入契约。模块级路由可能包含
    # 其他批次的设计项，直接透传会让模型看到跨批次编号并放大校验失败。
    normalized_fact_design_item_ids = {
        fact_id: [
            design_item_id
            for design_item_id in list(fact_design_item_ids.get(fact_id) or [])
            if design_item_id in available_design_ids
        ]
        for fact_id in all_fact_ids
    }

    return {
        "target_case_ids": target_case_ids,
        "required_fact_ids": all_fact_ids,
        "required_test_design_item_ids": design_item_ids,
        "fact_design_item_ids": normalized_fact_design_item_ids,
        "coverage_slots": [
            {
                "case_id": case_id,
                "required_fact_ids": fact_ids_by_slot[index],
                "required_test_design_item_ids": design_ids_by_slot[index],
            }
            for index, case_id in enumerate(target_case_ids)
        ],
    }


def prepare_test_case_batches(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """依据业务规划和预算生成通用、可追踪的 Agent 映射输入。"""

    plan = dict(arguments.get("plan") or {})
    planned_modules = [
        dict(item)
        for item in _required_list(plan.get("business_modules"), "business_modules")
    ]
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
    batch_case_limit = int(arguments.get("batch_case_limit") or 5)
    if batch_case_limit < 1 or batch_case_limit > 20:
        raise ValueError("batch_case_limit 必须在 1 到 20 之间")

    effective_by_id = {str(fact["fact_id"]): fact for fact in effective_facts}
    routed_effective_ids: set[str] = set()
    fact_assignment_counts: dict[str, int] = {}
    modules: list[dict[str, Any]] = []
    module_facts_by_index: list[list[dict[str, Any]]] = []
    inactive_modules: list[dict[str, Any]] = []
    for planned_module_index, module in enumerate(planned_modules):
        _module_evidence_ids(module)
        module_fact_ids = _module_fact_ids(module)
        module_facts = [
            dict(effective_by_id[fact_id])
            for fact_id in module_fact_ids
            if fact_id in effective_by_id
        ]
        if not module_facts:
            inactive_modules.append(
                {
                    "planned_module_index": planned_module_index,
                    "module_name": str(module.get("name") or ""),
                    "reason": "all_routed_facts_inactive",
                }
            )
            continue
        for fact in module_facts:
            fact_id = str(fact["fact_id"])
            fact_assignment_counts[fact_id] = fact_assignment_counts.get(fact_id, 0) + 1
        modules.append(module)
        routed_effective_ids.update(str(fact["fact_id"]) for fact in module_facts)
        module_facts_by_index.append(module_facts)
    if not modules:
        raise ValueError("权威协调后没有可生成用例的有效业务模块")
    missing_effective_ids = set(effective_by_id) - routed_effective_ids
    if missing_effective_ids:
        raise ValueError(
            "业务模块 fact_ids 未完整覆盖 effective facts: "
            f"missing={sorted(missing_effective_ids)}"
        )
    allocated_focus = _allocate_coverage_points(modules, plan.get("coverage_focus"))
    allocated_risks = _allocate_risks(modules, plan.get("risks"))
    design_items_by_module = _test_design_items_by_module(
        modules=modules,
    )
    active_design_items_by_module: list[list[dict[str, Any]]] = []
    active_fact_design_routes_by_module: list[list[dict[str, Any]]] = []
    inactive_test_design_item_ids: list[str] = []
    active_design_assignment_counts: dict[str, int] = {
        str(fact["fact_id"]): 0 for fact in effective_facts
    }
    for module_index, module in enumerate(modules):
        active_design_items, active_routes, inactive_design_ids = (
            _project_effective_test_design_context(
                module=module,
                design_items=design_items_by_module[module_index],
                effective_fact_ids={
                    str(fact["fact_id"])
                    for fact in module_facts_by_index[module_index]
                },
            )
        )
        active_design_items_by_module.append(active_design_items)
        active_fact_design_routes_by_module.append(active_routes)
        inactive_test_design_item_ids.extend(inactive_design_ids)
        for route in active_routes:
            fact_id = str(route["fact_id"])
            active_design_assignment_counts[fact_id] += len(
                route["test_design_item_indexes"]
            )
    unmatched_test_design_fact_ids = [
        fact_id
        for fact_id in effective_by_id
        if active_design_assignment_counts[fact_id] == 0
    ]
    module_fact_groups_by_index = [
        _generation_fact_groups(module_facts)
        for module_facts in module_facts_by_index
    ]
    module_targets = _allocate_module_case_targets(
        module_facts_by_index=module_facts_by_index,
        module_fact_groups_by_index=module_fact_groups_by_index,
        case_budget=case_budget,
        batch_case_limit=batch_case_limit,
    )

    items: list[dict[str, Any]] = []
    batch_number = 0
    for module_index, module in enumerate(modules):
        module_target = module_targets[module_index]
        if module_target <= 0:
            continue
        module_facts = module_facts_by_index[module_index]
        fact_groups = module_fact_groups_by_index[module_index]
        minimum_batch_count = math.ceil(module_target / batch_case_limit)
        fact_groups = _expand_generation_fact_groups(
            fact_groups,
            target_count=max(len(fact_groups), minimum_batch_count),
        )
        batch_budgets = _allocate_generation_batch_budgets(
            fact_groups,
            case_target=module_target,
            batch_case_limit=batch_case_limit,
        )
        module_batch_count = len(fact_groups)
        design_items_by_batch = _allocate_test_design_items_to_fact_groups(
            design_items=active_design_items_by_module[module_index],
            fact_groups=fact_groups,
            fact_design_routes=active_fact_design_routes_by_module[module_index],
        )
        module_design_items = active_design_items_by_module[module_index]
        module_fact_design_item_ids = {
            str(route["fact_id"]): [
                str(module_design_items[index]["test_design_item_id"])
                for index in route["test_design_item_indexes"]
            ]
            for route in active_fact_design_routes_by_module[module_index]
        }
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
        for module_batch_index in range(module_batch_count):
            batch_target = batch_budgets[module_batch_index]
            batch_number += 1
            batch_points = points_by_batch[module_batch_index]
            batch_facts = fact_groups[module_batch_index]
            batch_design_items = design_items_by_batch[module_batch_index]
            batch_design_item_ids = {
                str(item["test_design_item_id"]) for item in batch_design_items
            }
            # 路由表按批次裁剪，避免把同模块其他批次的测试设计项带进当前契约。
            batch_fact_design_item_ids = {
                fact_id: [
                    design_item_id
                    for design_item_id in list(module_fact_design_item_ids.get(fact_id) or [])
                    if design_item_id in batch_design_item_ids
                ]
                for fact_id in (str(fact["fact_id"]) for fact in batch_facts)
            }
            source_context = _generation_batch_source_context(
                module=module,
                facts=batch_facts,
                coverage_points=batch_points,
            )
            focus = str(source_context["semantic_summary"])
            batch_business_module = {
                key: deepcopy(value)
                for key, value in module.items()
                if key not in {"fact_design_routes", "test_points"}
            }
            batch_business_module["fact_ids"] = [
                str(fact["fact_id"]) for fact in module_facts
            ]
            items.append(
                {
                    "requirement": "\n".join(
                        str(fact["assertion"]) for fact in batch_facts
                    ),
                    "plan": {
                        "requirement_summary": str(plan.get("requirement_summary") or ""),
                        "business_module": batch_business_module,
                        "coverage_focus": focus,
                        "risks": risks_by_batch[module_batch_index],
                        "test_design_items": batch_design_items,
                    },
                    "case_budget": batch_target,
                    "batch": {
                        "batch_id": (
                            f"M{module_index + 1:03d}-B{module_batch_index + 1:03d}"
                        ),
                        "batch_number": batch_number,
                        "module_index": module_index,
                        "module_batch_index": module_batch_index,
                        "module_batch_count": module_batch_count,
                        "module_name": str(module.get("name") or ""),
                        "coverage_focus": focus,
                        "required_test_design_item_ids": [
                            str(item["test_design_item_id"])
                            for item in batch_design_items
                        ],
                        **source_context,
                    },
                    "authoritative_facts": batch_facts,
                    "case_fact_contract": _build_case_fact_contract(
                        batch_facts,
                        case_budget=batch_target,
                        test_design_items=batch_design_items,
                        fact_design_item_ids=batch_fact_design_item_ids,
                    ),
                }
            )

    for index, item in enumerate(items, start=1):
        item["batch"]["batch_number"] = index
        item["batch"]["batch_count"] = len(items)
    context.artifacts["generation_batch_plan"] = {
        "case_budget": case_budget,
        "batch_case_limit": batch_case_limit,
        "batch_count": len(items),
        "max_pages_per_batch": GENERATION_MAX_PAGES_PER_BATCH,
        "max_fact_json_chars_per_batch": GENERATION_MAX_FACT_JSON_CHARS_PER_BATCH,
        "max_required_facts_per_batch": GENERATION_MAX_REQUIRED_FACTS_PER_BATCH,
        "requires_contiguous_document_pages": False,
        "planned_module_count": len(planned_modules),
        "active_module_count": len(modules),
        "inactive_modules": inactive_modules,
        "inactive_test_design_item_ids": inactive_test_design_item_ids,
        "unmatched_test_design_fact_ids": unmatched_test_design_fact_ids,
        "effective_fact_count": len(effective_facts),
        "fact_assignment_count": sum(fact_assignment_counts.values()),
        "shared_fact_count": sum(
            count > 1 for count in fact_assignment_counts.values()
        ),
        "max_fact_reuse": max(fact_assignment_counts.values(), default=0),
        "batches": [
            {
                **dict(item["batch"]),
                "case_budget": int(item["case_budget"]),
                "authoritative_fact_ids": [
                    str(fact["fact_id"]) for fact in item["authoritative_facts"]
                ],
                "case_fact_contract": dict(item["case_fact_contract"]),
            }
            for item in items
        ],
    }
    return {"items": items, "batch_count": len(items), "case_budget": case_budget}


@repairable_output(GENERATION_REPAIR_STRATEGY)
def postprocess_generation_batch_item(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """拆分模型内联事实，并校验数量、模块边界和事实覆盖。"""

    source_input = dict(arguments.get("item_input") or {})
    raw_output = dict(arguments.get("item_output") or {})
    expected_count = int(source_input.get("case_budget") or 0)
    expected_case_ids = [f"TC-{index + 1:03d}" for index in range(expected_count)]
    module_name = str(
        dict(source_input.get("batch") or {}).get("module_name")
        or dict(dict(source_input.get("plan") or {}).get("business_module") or {}).get("name")
        or ""
    ).strip()
    contract = dict(source_input.get("case_fact_contract") or {})
    fact_design_item_ids = contract.get("fact_design_item_ids")
    allow_missing_design_item_ids = isinstance(fact_design_item_ids, dict)
    output = materialize_inline_grounding(
        raw_cases=raw_output.get("test_cases"),
        case_ids=expected_case_ids,
        module_name=module_name,
        allow_missing_design_item_ids=allow_missing_design_item_ids,
    )
    if allow_missing_design_item_ids:
        required_fact_ids = [
            str(value).strip()
            for value in list(contract.get("required_fact_ids") or [])
            if str(value).strip()
        ]
        required_design_item_ids = [
            str(value).strip()
            for value in list(contract.get("required_test_design_item_ids") or [])
            if str(value).strip()
        ]
        derived_by_case = derive_test_design_item_ids(
            case_fact_bindings=output.get("case_fact_bindings"),
            fact_design_item_ids=fact_design_item_ids,
            required_fact_ids=required_fact_ids,
            required_design_item_ids=required_design_item_ids,
        )
        for case in list(output.get("test_cases") or []):
            case_id = str(case.get("case_id") or "").strip()
            # 路由契约是编号的唯一可信来源。即使旧模型仍返回部分编号，
            # 也以实际事实绑定重新物化，避免半残编号绕过覆盖校验。
            case["test_design_item_ids"] = list(derived_by_case.get(case_id) or [])
    return _validate_generation_batch_output(source_input=source_input, output=output)




def _covered_fact_ids_by_case(
    bindings: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """按平台生成的 case_id 汇总全部内联事实引用。"""

    return {
        str(binding.get("case_id") or ""): bound_fact_ids(binding)
        for binding in bindings
    }


def _validate_generation_batch_output(
    *,
    source_input: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    """校验已经由平台拆分完成的单批生成结果。"""

    raw_test_cases = _required_list(output.get("test_cases"), "test_cases")
    test_cases: list[dict[str, Any]] = []
    for raw_case in raw_test_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("test_cases 每项必须是对象")
        case = dict(raw_case)
        case.setdefault("tags", [])
        design_item_ids = case.get("test_design_item_ids")
        if not isinstance(design_item_ids, list):
            raise ValueError("每条用例的 test_design_item_ids 必须是数组")
        normalized_design_item_ids = [str(value).strip() for value in design_item_ids]
        if any(not value for value in normalized_design_item_ids) or len(
            normalized_design_item_ids
        ) != len(set(normalized_design_item_ids)):
            raise ValueError("用例 test_design_item_ids 包含空值或重复值")
        case["test_design_item_ids"] = normalized_design_item_ids
        test_cases.append(case)
    expected_count = int(source_input.get("case_budget") or 0)
    if len(test_cases) != expected_count:
        raise ValueError(
            "生成批次没有精确达到分配数量: "
            f"target={expected_count}, actual={len(test_cases)}"
        )
    module_name = str(
        dict(source_input.get("batch") or {}).get("module_name")
        or dict(dict(source_input.get("plan") or {}).get("business_module") or {}).get("name")
        or ""
    ).strip()
    if not module_name:
        raise ValueError("生成批次缺少模块名称")
    bindings = validate_case_fact_bindings(
        test_cases=test_cases,
        raw_bindings=output.get("case_fact_bindings"),
        authoritative_facts=source_input.get("authoritative_facts"),
        expected_module_name=module_name,
    )
    expected_case_ids = [f"TC-{index + 1:03d}" for index in range(expected_count)]
    contract = source_input.get("case_fact_contract")
    if not isinstance(contract, dict):
        raise ValueError("生成批次缺少事实覆盖契约")
    target_case_ids = contract.get("target_case_ids")
    if target_case_ids != expected_case_ids:
        raise ValueError("事实覆盖契约的 target_case_ids 与用例预算不一致")
    available_fact_ids = [
        str(fact.get("fact_id") or "")
        for fact in list(source_input.get("authoritative_facts") or [])
    ]
    required_fact_ids = contract.get("required_fact_ids")
    if required_fact_ids != available_fact_ids:
        raise ValueError("事实覆盖契约与当前批次权威事实不一致")
    plan_design_item_ids = [
        str(item.get("test_design_item_id") or "")
        for item in list(dict(source_input.get("plan") or {}).get("test_design_items") or [])
    ]
    batch_design_item_ids = list(
        dict(source_input.get("batch") or {}).get("required_test_design_item_ids") or []
    )
    required_design_item_ids = list(contract.get("required_test_design_item_ids") or [])
    if not (
        required_design_item_ids == plan_design_item_ids == batch_design_item_ids
        and len(required_design_item_ids) == len(set(required_design_item_ids))
    ):
        raise ValueError("测试设计覆盖契约与当前批次规划不一致")
    raw_coverage_slots = contract.get("coverage_slots")
    if not isinstance(raw_coverage_slots, list) or len(raw_coverage_slots) != expected_count:
        raise ValueError("事实覆盖契约的 coverage_slots 与用例预算不一致")
    coverage_slots: list[dict[str, Any]] = []
    assigned_fact_ids: set[str] = set()
    assigned_design_item_ids: set[str] = set()
    for slot_index, raw_slot in enumerate(raw_coverage_slots):
        if not isinstance(raw_slot, dict):
            raise ValueError("coverage_slots 每项必须是对象")
        slot = dict(raw_slot)
        case_id = str(slot.get("case_id") or "").strip()
        if case_id != expected_case_ids[slot_index]:
            raise ValueError("coverage_slots 必须保持平台确定的 case_id 及顺序")
        slot_fact_ids = slot.get("required_fact_ids")
        if (
            not isinstance(slot_fact_ids, list)
            or not slot_fact_ids
            or any(str(value) not in available_fact_ids for value in slot_fact_ids)
            or len(slot_fact_ids) != len(set(slot_fact_ids))
        ):
            raise ValueError(f"coverage_slots 事实分配无效: case_id={case_id}")
        slot_design_item_ids = slot.get("required_test_design_item_ids")
        if (
            not isinstance(slot_design_item_ids, list)
            or any(str(value) not in required_design_item_ids for value in slot_design_item_ids)
            or len(slot_design_item_ids) != len(set(slot_design_item_ids))
        ):
            raise ValueError(f"coverage_slots 测试设计分配无效: case_id={case_id}")
        normalized_slot = {
            "case_id": case_id,
            "required_fact_ids": [str(value) for value in slot_fact_ids],
            "required_test_design_item_ids": [
                str(value) for value in slot_design_item_ids
            ],
        }
        coverage_slots.append(normalized_slot)
        assigned_fact_ids.update(normalized_slot["required_fact_ids"])
        assigned_design_item_ids.update(
            normalized_slot["required_test_design_item_ids"]
        )
    if assigned_fact_ids != set(required_fact_ids):
        raise ValueError("coverage_slots 没有完整承接批次事实覆盖契约")
    if assigned_design_item_ids != set(required_design_item_ids):
        raise ValueError("coverage_slots 没有完整承接批次测试设计覆盖契约")
    covered_design_item_ids = {
        design_item_id
        for case in test_cases
        for design_item_id in list(case.get("test_design_item_ids") or [])
    }
    invalid_design_item_ids = sorted(covered_design_item_ids - set(required_design_item_ids))
    missing_design_item_ids = sorted(set(required_design_item_ids) - covered_design_item_ids)
    actual_case_ids = [str(case.get("case_id") or "").strip() for case in test_cases]
    covered_fact_ids_by_case = _covered_fact_ids_by_case(bindings)
    covered_fact_ids = set().union(*covered_fact_ids_by_case.values())
    missing_fact_ids = sorted(set(required_fact_ids) - covered_fact_ids)
    coverage_errors: list[str] = []
    if actual_case_ids != expected_case_ids:
        coverage_errors.append(
            "生成批次必须保持平台确定的 case_id 及顺序: "
            f"expected={expected_case_ids}, actual={actual_case_ids}"
        )
    if missing_fact_ids:
        coverage_errors.append(
            f"生成批次未完整覆盖平台要求的事实: missing={missing_fact_ids}"
        )
    if invalid_design_item_ids or missing_design_item_ids:
        coverage_errors.append(
            "生成批次测试设计覆盖不符合平台契约: "
            f"missing={missing_design_item_ids}, invalid={invalid_design_item_ids}"
        )
    if coverage_errors:
        raise OutputRepairError(
            "；".join(coverage_errors),
            strategy_key=GENERATION_REPAIR_STRATEGY,
            details={
                "missing_fact_ids": missing_fact_ids,
                "missing_test_design_item_ids": missing_design_item_ids,
                "invalid_test_design_item_ids": invalid_design_item_ids,
                "case_ids": expected_case_ids if actual_case_ids != expected_case_ids else [],
            },
        )
    return {
        "test_cases": [dict(case) for case in test_cases],
        "case_fact_bindings": bindings,
    }




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
        normalized_output = _validate_generation_batch_output(
            source_input=source_input,
            output=dict(output),
        )
        test_cases = normalized_output["test_cases"]
        bindings = normalized_output["case_fact_bindings"]
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


def select_execution_chain(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """按确定性候选顺序选择首条严格可达主链，不再调用模型重复判断。"""

    candidate_chains = list(arguments.get("candidate_chains") or [])
    if not candidate_chains:
        return {"name": "", "goal": "", "case_ids": []}
    first = dict(candidate_chains[0])
    case_ids = [str(value) for value in list(first.get("case_ids") or [])]
    cases = [dict(item) for item in list(first.get("cases") or [])]
    modules: list[str] = []
    for case in cases:
        module = str(case.get("module") or "").strip()
        if module and module not in modules:
            modules.append(module)
    result = {
        "name": "核心业务执行主链",
        "goal": "按严格状态衔接顺序验证" + ("、".join(modules) if modules else "核心流程"),
        "case_ids": case_ids,
    }
    context.artifacts["execution_chain_selection"] = {
        "candidate_id": str(first.get("candidate_id") or ""),
        "case_count": len(case_ids),
        "selection_rule": "first_strict_candidate",
    }
    return result


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
                "case_ids": selected_ids,
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
