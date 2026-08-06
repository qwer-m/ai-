"""通用文档资产准备与读取。

该模块只负责把原文件转换为可寻址的页面文本、页面图和 manifest，
不在准备阶段预判文档业务类型，也不提取特定业务规则。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pypdf
import pdfplumber


BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DOCUMENT_ASSET_DIR = BACKEND_ROOT / "runtime" / "knowledge_assets"
MANIFEST_VERSION = 3
PAGE_TAIL_BAND_START = 0.60
PAGE_HEAD_BAND_END = 0.40


_ORDERED_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "arabic",
        re.compile(r"^\s*(?P<value>\d{1,4})\s*(?P<suffix>[.．、)）：:])\s*"),
    ),
    (
        "latin_upper",
        re.compile(r"^\s*(?P<value>[A-Z])\s*(?P<suffix>[.．、)）：:])\s*"),
    ),
    (
        "latin_lower",
        re.compile(r"^\s*(?P<value>[a-z])\s*(?P<suffix>[.．、)）：:])\s*"),
    ),
)


def resolve_document_asset_dir() -> Path:
    """解析文档资产根目录，相对路径统一以 backend 为基准。"""

    raw = str(os.getenv("DOCUMENT_ASSET_DIR") or "").strip()
    if not raw:
        return DEFAULT_DOCUMENT_ASSET_DIR
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    return path.resolve()


def _document_dir(document_id: int) -> Path:
    normalized_id = int(document_id)
    if normalized_id < 1:
        raise ValueError("document_id 必须大于 0")
    return resolve_document_asset_dir() / str(normalized_id)


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    _write_bytes(path, value.encode("utf-8"))


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    _write_bytes(path, payload)


def _render_pdf_page(page: Any, *, scale: float) -> tuple[bytes, int, int]:
    try:
        import pypdfium2 as pdfium
    except Exception as exc:  # pragma: no cover - 依赖缺失时由真实上传链路报错
        raise RuntimeError(f"PDF 页面渲染依赖不可用: {exc}") from exc

    bitmap = page.render(scale=scale)
    image = bitmap.to_pil()
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), int(image.width), int(image.height)


def _normalized_bbox(
    *,
    x0: float,
    top: float,
    x1: float,
    bottom: float,
    page_width: float,
    page_height: float,
) -> dict[str, float]:
    left = max(0.0, min(float(x0), page_width))
    upper = max(0.0, min(float(top), page_height))
    right = max(left, min(float(x1), page_width))
    lower = max(upper, min(float(bottom), page_height))
    return {
        "x": round(left / page_width, 6),
        "y": round(upper / page_height, 6),
        "width": round((right - left) / page_width, 6),
        "height": round((lower - upper) / page_height, 6),
    }


def _clean_layout_text(value: Any) -> str:
    """规范化版面文字，同时移除不能进入 JSON 事实层的控制字符。"""

    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\x00-\x1f\x7f]+", "", text)


def _source_bbox(value: dict[str, Any]) -> dict[str, float] | None:
    """从 pdfplumber 对象读取统一的左上角坐标框。"""

    try:
        x0 = float(value.get("x0"))
        x1 = float(value.get("x1"))
        top = float(value.get("top"))
        bottom = float(value.get("bottom"))
    except (TypeError, ValueError):
        return None
    left, right = sorted((x0, x1))
    upper, lower = sorted((top, bottom))
    return {
        "x0": round(left, 3),
        "top": round(upper, 3),
        "x1": round(right, 3),
        "bottom": round(lower, 3),
    }


def _bbox_intersection_area(left: dict[str, float], right: dict[str, float]) -> float:
    width = max(0.0, min(left["x1"], right["x1"]) - max(left["x0"], right["x0"]))
    height = max(
        0.0,
        min(left["bottom"], right["bottom"]) - max(left["top"], right["top"]),
    )
    return width * height


def _bbox_union(values: list[dict[str, float]]) -> dict[str, float]:
    return {
        "x0": round(min(value["x0"] for value in values), 3),
        "top": round(min(value["top"] for value in values), 3),
        "x1": round(max(value["x1"] for value in values), 3),
        "bottom": round(max(value["bottom"] for value in values), 3),
    }


def _normalized_source_bbox(
    value: dict[str, float],
    *,
    page_width: float,
    page_height: float,
) -> dict[str, float]:
    return _normalized_bbox(
        x0=value["x0"],
        top=value["top"],
        x1=value["x1"],
        bottom=value["bottom"],
        page_width=page_width,
        page_height=page_height,
    )


def _serializable_color(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    try:
        return [round(float(component), 6) for component in value]
    except (TypeError, ValueError):
        return None


def _page_layout_blocks(
    page: Any,
    *,
    page_number: int,
    asset_source_sha256: str,
) -> list[dict[str, Any]]:
    """提取通用文本行、字符坐标和图像区域，不解释业务语义。"""

    page_width = max(float(page.width), 1.0)
    page_height = max(float(page.height), 1.0)
    blocks: list[dict[str, Any]] = []
    words = sorted(
        list(
            page.extract_words(
                use_text_flow=True,
                keep_blank_chars=False,
                extra_attrs=["fontname", "size"],
                return_chars=True,
            )
            or []
        ),
        key=lambda item: (float(item.get("top") or 0), float(item.get("x0") or 0)),
    )
    lines: list[list[dict[str, Any]]] = []
    for word in words:
        if not lines or abs(float(word.get("top") or 0) - float(lines[-1][0].get("top") or 0)) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    for line_index, line in enumerate(lines, start=1):
        ordered = sorted(line, key=lambda item: float(item.get("x0") or 0))
        words: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
        for word in ordered:
            glyphs: list[dict[str, Any]] = []
            for raw_char in list(word.get("chars") or []):
                char_text = _clean_layout_text(raw_char.get("text"))
                char_bbox = _source_bbox(raw_char)
                if not char_text or char_bbox is None:
                    continue
                glyphs.append(
                    {
                        "text": char_text,
                        "source_bbox": char_bbox,
                        "font_name": str(raw_char.get("fontname") or "").strip(),
                        "font_size": round(float(raw_char.get("size") or 0.0), 3),
                        "stroking_color": _serializable_color(
                            raw_char.get("stroking_color")
                        ),
                        "non_stroking_color": _serializable_color(
                            raw_char.get("non_stroking_color")
                        ),
                    }
                )
            word_text = "".join(glyph["text"] for glyph in glyphs)
            if not word_text:
                word_text = _clean_layout_text(word.get("text")).strip()
                word_bbox = _source_bbox(word)
                if word_text and word_bbox is not None:
                    glyphs = [
                        {
                            "text": word_text,
                            "source_bbox": word_bbox,
                            "font_name": str(word.get("fontname") or "").strip(),
                            "font_size": round(float(word.get("size") or 0.0), 3),
                            "stroking_color": None,
                            "non_stroking_color": None,
                        }
                    ]
            if word_text:
                words.append((word_text, word, glyphs))
        text = " ".join(word_text for word_text, _, _ in words).strip()
        if not text:
            continue
        x0 = min(float(item.get("x0") or 0) for _, item, _ in words)
        top = min(float(item.get("top") or 0) for _, item, _ in words)
        x1 = max(float(item.get("x1") or 0) for _, item, _ in words)
        bottom = max(float(item.get("bottom") or 0) for _, item, _ in words)
        # 中文注释：按文字长度选择行的主样式，不推断标题或业务语义。
        style_weights: dict[tuple[str, float], int] = {}
        for _, word, _ in words:
            style = (
                str(word.get("fontname") or "").strip(),
                round(float(word.get("size") or 0.0), 3),
            )
            style_weights[style] = style_weights.get(style, 0) + max(
                1,
                len(str(word.get("text") or "").strip()),
            )
        (font_name, font_size), _ = max(
            style_weights.items(),
            key=lambda item: (item[1], item[0]),
        )
        glyph_records: list[dict[str, Any]] = []
        line_cursor = 0
        for word_position, (word_text, _, glyphs) in enumerate(words):
            if word_position:
                line_cursor += 1
            for glyph in glyphs:
                glyph_text = str(glyph["text"])
                glyph_records.append(
                    {
                        **glyph,
                        "line_offset_start": line_cursor,
                        "line_offset_end": line_cursor + len(glyph_text),
                        "decorations": [],
                        "mark_ids": [],
                    }
                )
                line_cursor += len(glyph_text)
        blocks.append(
            {
                "block_id": f"P{page_number:04d}-T{line_index:04d}",
                "type": "text_line",
                "text": text,
                "bbox": _normalized_bbox(
                    x0=x0,
                    top=top,
                    x1=x1,
                    bottom=bottom,
                    page_width=page_width,
                    page_height=page_height,
                ),
                "source_bbox": {
                    "x0": round(x0, 3),
                    "top": round(top, 3),
                    "x1": round(x1, 3),
                    "bottom": round(bottom, 3),
                },
                "indent": {
                    "x": round(x0, 3),
                    "normalized_x": round(x0 / page_width, 6),
                },
                "font_name": font_name,
                "font_size": font_size,
                "source": "pdf_text",
                "asset_source_sha256": asset_source_sha256,
                "_glyphs": glyph_records,
            }
        )
    for image_index, image in enumerate(list(page.images or []), start=1):
        blocks.append(
            {
                "block_id": f"P{page_number:04d}-I{image_index:04d}",
                "type": "image",
                "text": "",
                "bbox": _normalized_bbox(
                    x0=float(image.get("x0") or 0),
                    top=float(image.get("top") or 0),
                    x1=float(image.get("x1") or page_width),
                    bottom=float(image.get("bottom") or page_height),
                    page_width=page_width,
                    page_height=page_height,
                ),
                "source": "pdf_image",
                "asset_source_sha256": asset_source_sha256,
            }
        )
    return blocks


def _page_text_with_source_spans(blocks: list[dict[str, Any]]) -> str:
    """从同源版式文本行生成页正文，并持久化每行的精确字符坐标。"""

    parts: list[str] = []
    cursor = 0
    for block in blocks:
        if str(block.get("type") or "") != "text_line":
            continue
        text = str(block.get("text") or "")
        if not text:
            raise ValueError(f"版式文本行为空: block_id={block.get('block_id')}")
        if parts:
            parts.append("\n")
            cursor += 1
        start = cursor
        parts.append(text)
        cursor += len(text)
        block["source_span"] = {"start": start, "end": cursor}
        for glyph in list(block.get("_glyphs") or []):
            glyph_start = start + int(glyph["line_offset_start"])
            glyph_end = start + int(glyph["line_offset_end"])
            if text[glyph_start - start : glyph_end - start] != str(glyph["text"]):
                raise ValueError(
                    "字符版式坐标与行正文不一致: "
                    f"block_id={block.get('block_id')}, start={glyph_start}, end={glyph_end}"
                )
            glyph["source_span"] = {"start": glyph_start, "end": glyph_end}
    return "".join(parts)


def _annotation_subtype(value: dict[str, Any]) -> str:
    raw = dict(value.get("data") or {}).get("Subtype")
    return str(raw or "").strip().strip("/'")


def _filled_highlight_candidate(value: dict[str, Any]) -> bool:
    """只把覆盖文字的高明度彩色填充识别为高亮候选。"""

    if not bool(value.get("fill")):
        return False
    color = _serializable_color(value.get("non_stroking_color"))
    if color is None or len(color) < 3:
        return False
    rgb = color[:3]
    return max(rgb) - min(rgb) >= 0.12 and sum(rgb) / 3 >= 0.45


def _strikeout_targets(
    mark_bbox: dict[str, float],
    glyphs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    mark_height = mark_bbox["bottom"] - mark_bbox["top"]
    mark_width = mark_bbox["x1"] - mark_bbox["x0"]
    if mark_width < 4.0 or mark_height > 3.0:
        return []
    mark_middle = (mark_bbox["top"] + mark_bbox["bottom"]) / 2
    targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for block, glyph in glyphs:
        glyph_bbox = dict(glyph["source_bbox"])
        glyph_height = glyph_bbox["bottom"] - glyph_bbox["top"]
        glyph_width = glyph_bbox["x1"] - glyph_bbox["x0"]
        horizontal_overlap = max(
            0.0,
            min(mark_bbox["x1"], glyph_bbox["x1"])
            - max(mark_bbox["x0"], glyph_bbox["x0"]),
        )
        if glyph_height <= 0 or glyph_width <= 0:
            continue
        if (
            glyph_bbox["top"] + glyph_height * 0.2
            <= mark_middle
            <= glyph_bbox["bottom"] - glyph_height * 0.2
            and horizontal_overlap >= min(2.0, glyph_width * 0.3)
        ):
            targets.append((block, glyph))
    return targets


def _highlight_targets(
    mark_bbox: dict[str, float],
    glyphs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for block, glyph in glyphs:
        glyph_bbox = dict(glyph["source_bbox"])
        glyph_area = max(
            0.0,
            (glyph_bbox["x1"] - glyph_bbox["x0"])
            * (glyph_bbox["bottom"] - glyph_bbox["top"]),
        )
        if glyph_area and _bbox_intersection_area(mark_bbox, glyph_bbox) / glyph_area >= 0.35:
            targets.append((block, glyph))
    return targets


def _overlap_targets(
    mark_bbox: dict[str, float],
    glyphs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (block, glyph)
        for block, glyph in glyphs
        if _bbox_intersection_area(mark_bbox, dict(glyph["source_bbox"])) > 0
    ]


def _merge_source_spans(values: list[dict[str, int]]) -> list[dict[str, int]]:
    merged: list[dict[str, int]] = []
    for value in sorted(values, key=lambda item: (int(item["start"]), int(item["end"]))):
        start, end = int(value["start"]), int(value["end"])
        if merged and start <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], end)
        else:
            merged.append({"start": start, "end": end})
    return merged


def _finalize_text_runs(
    blocks: list[dict[str, Any]],
    *,
    page_width: float,
    page_height: float,
) -> None:
    """按连续样式和版面标记归并字符，形成可持久化的精确文本 run。"""

    for block in blocks:
        if str(block.get("type") or "") != "text_line":
            continue
        glyphs = list(block.pop("_glyphs", []) or [])
        groups: list[list[dict[str, Any]]] = []
        for glyph in glyphs:
            key = (
                str(glyph.get("font_name") or ""),
                float(glyph.get("font_size") or 0.0),
                tuple(glyph.get("stroking_color") or []),
                tuple(glyph.get("non_stroking_color") or []),
                tuple(glyph.get("decorations") or []),
                tuple(glyph.get("mark_ids") or []),
            )
            previous = groups[-1][-1] if groups else None
            previous_key = groups[-1][0].get("_group_key") if groups else None
            contiguous = bool(
                previous
                and int(previous["source_span"]["end"])
                == int(glyph["source_span"]["start"])
            )
            glyph["_group_key"] = key
            if groups and previous_key == key and contiguous:
                groups[-1].append(glyph)
            else:
                groups.append([glyph])

        text_runs: list[dict[str, Any]] = []
        for run_index, group in enumerate(groups, start=1):
            run_id = f"{block['block_id']}-R{run_index:04d}"
            source_span = {
                "start": int(group[0]["source_span"]["start"]),
                "end": int(group[-1]["source_span"]["end"]),
            }
            line_start = int(block["source_span"]["start"])
            run_text = str(block["text"])[
                source_span["start"] - line_start : source_span["end"] - line_start
            ]
            if run_text != "".join(str(glyph["text"]) for glyph in group):
                raise ValueError(f"文本 run 与行正文不一致: run_id={run_id}")
            source_bbox = _bbox_union(
                [dict(glyph["source_bbox"]) for glyph in group]
            )
            run = {
                "run_id": run_id,
                "text": run_text,
                "source_span": source_span,
                "source_bbox": source_bbox,
                "bbox": _normalized_source_bbox(
                    source_bbox,
                    page_width=page_width,
                    page_height=page_height,
                ),
                "font_name": str(group[0].get("font_name") or ""),
                "font_size": float(group[0].get("font_size") or 0.0),
                "decorations": list(group[0].get("decorations") or []),
                "mark_ids": list(group[0].get("mark_ids") or []),
                "asset_source_sha256": str(block.get("asset_source_sha256") or ""),
            }
            for glyph in group:
                glyph["_run_id"] = run_id
            text_runs.append(run)
        block["text_runs"] = text_runs
        block["_finalized_glyphs"] = glyphs


def _page_layout_marks(
    page: Any,
    blocks: list[dict[str, Any]],
    *,
    page_number: int,
    asset_source_sha256: str,
) -> list[dict[str, Any]]:
    """提取删除线、高亮与作者批注，并绑定同页字符锚点。"""

    page_width = max(float(page.width), 1.0)
    page_height = max(float(page.height), 1.0)
    glyphs = [
        (block, glyph)
        for block in blocks
        if str(block.get("type") or "") == "text_line"
        for glyph in list(block.get("_glyphs") or [])
    ]
    candidates: list[dict[str, Any]] = []
    for value in [*list(getattr(page, "lines", []) or []), *list(getattr(page, "rects", []) or [])]:
        bbox = _source_bbox(value)
        if bbox is None:
            continue
        strike_targets = _strikeout_targets(bbox, glyphs)
        if strike_targets:
            candidates.append(
                {
                    "type": "strikeout",
                    "source": "pdf_graphics",
                    "source_bbox": bbox,
                    "targets": strike_targets,
                }
            )
            continue
        if _filled_highlight_candidate(value):
            highlight_targets = _highlight_targets(bbox, glyphs)
            if highlight_targets:
                candidates.append(
                    {
                        "type": "highlight",
                        "source": "pdf_graphics",
                        "source_bbox": bbox,
                        "targets": highlight_targets,
                    }
                )

    for annotation in list(getattr(page, "annots", []) or []):
        subtype = _annotation_subtype(annotation)
        if subtype.casefold() in {"link", "widget", "popup"}:
            continue
        bbox = _source_bbox(annotation)
        if bbox is None:
            continue
        lowered = subtype.casefold()
        mark_type = (
            "highlight"
            if lowered == "highlight"
            else "strikeout"
            if lowered == "strikeout"
            else "annotation"
        )
        targets = (
            _strikeout_targets(bbox, glyphs)
            if mark_type == "strikeout"
            else _highlight_targets(bbox, glyphs)
            if mark_type == "highlight"
            else _overlap_targets(bbox, glyphs)
        )
        candidates.append(
            {
                "type": mark_type,
                "source": "pdf_annotation",
                "source_bbox": bbox,
                "targets": targets,
                "annotation_subtype": subtype or "Unknown",
                "contents": str(annotation.get("contents") or "").strip(),
                "title": str(annotation.get("title") or "").strip(),
            }
        )

    unique_candidates: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for candidate in sorted(
        candidates,
        key=lambda item: (
            float(item["source_bbox"]["top"]),
            float(item["source_bbox"]["x0"]),
            str(item["type"]),
            str(item["source"]),
        ),
    ):
        bbox = candidate["source_bbox"]
        identity = (
            candidate["type"],
            round(float(bbox["x0"]), 2),
            round(float(bbox["top"]), 2),
            round(float(bbox["x1"]), 2),
            round(float(bbox["bottom"]), 2),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique_candidates.append(candidate)

    for mark_index, candidate in enumerate(unique_candidates, start=1):
        mark_id = f"P{page_number:04d}-M{mark_index:04d}"
        candidate["mark_id"] = mark_id
        for _, glyph in candidate["targets"]:
            if candidate["type"] not in glyph["decorations"]:
                glyph["decorations"].append(candidate["type"])
            if mark_id not in glyph["mark_ids"]:
                glyph["mark_ids"].append(mark_id)

    _finalize_text_runs(
        blocks,
        page_width=page_width,
        page_height=page_height,
    )
    marks: list[dict[str, Any]] = []
    for candidate in unique_candidates:
        target_runs = sorted(
            {
                str(glyph.get("_run_id") or "")
                for _, glyph in candidate["targets"]
                if str(glyph.get("_run_id") or "")
            }
        )
        target_blocks = sorted(
            {str(block.get("block_id") or "") for block, _ in candidate["targets"]}
        )
        target_spans = _merge_source_spans(
            [dict(glyph["source_span"]) for _, glyph in candidate["targets"]]
        )
        mark = {
            "mark_id": candidate["mark_id"],
            "type": candidate["type"],
            "source": candidate["source"],
            "source_bbox": dict(candidate["source_bbox"]),
            "bbox": _normalized_source_bbox(
                dict(candidate["source_bbox"]),
                page_width=page_width,
                page_height=page_height,
            ),
            "target_block_ids": target_blocks,
            "target_run_ids": target_runs,
            "target_source_spans": target_spans,
            "asset_source_sha256": asset_source_sha256,
        }
        if candidate["source"] == "pdf_annotation":
            mark.update(
                {
                    "annotation_subtype": candidate["annotation_subtype"],
                    "contents": candidate["contents"],
                    "title": candidate["title"],
                }
            )
        marks.append(mark)
    for block in blocks:
        block.pop("_finalized_glyphs", None)
    return marks


def _prepare_page_layout(
    page: Any,
    *,
    page_number: int,
    asset_source_sha256: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """生成同源页正文、最终版面块和版面标记。"""

    blocks = _page_layout_blocks(
        page,
        page_number=page_number,
        asset_source_sha256=asset_source_sha256,
    )
    page_text = _page_text_with_source_spans(blocks)
    marks = _page_layout_marks(
        page,
        blocks,
        page_number=page_number,
        asset_source_sha256=asset_source_sha256,
    )
    return page_text, blocks, marks


def _ordered_marker(block: dict[str, Any]) -> dict[str, Any] | None:
    """从单行文本中读取通用有序标记，不使用领域词表。"""

    text = str(block.get("text") or "")
    for marker_kind, pattern in _ORDERED_MARKER_PATTERNS:
        matched = pattern.match(text)
        if matched is None:
            continue
        raw_value = matched.group("value")
        ordinal = int(raw_value) if marker_kind == "arabic" else ord(raw_value.lower()) - 96
        return {
            "kind": marker_kind,
            "ordinal": ordinal,
            "raw": matched.group(0).strip(),
            "suffix": matched.group("suffix"),
            "block_id": str(block.get("block_id") or ""),
            "line_text": text,
        }
    return None


def _same_marker_style(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_font = str(left.get("font_name") or "").strip()
    right_font = str(right.get("font_name") or "").strip()
    left_size = float(left.get("font_size") or 0.0)
    right_size = float(right.get("font_size") or 0.0)
    left_indent = float((left.get("indent") or {}).get("normalized_x") or 0.0)
    right_indent = float((right.get("indent") or {}).get("normalized_x") or 0.0)
    return bool(
        left_font
        and left_font == right_font
        and left_size > 0
        and right_size > 0
        and abs(left_size - right_size) <= max(0.2, left_size * 0.02)
        and abs(left_indent - right_indent) <= 0.012
    )


def detect_high_confidence_page_continuations(
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    """仅用相邻物理页的有序标记和版式一致性识别高置信跨页候选。"""

    pages = sorted(
        [dict(page) for page in list(manifest.get("pages") or [])],
        key=lambda page: int(page.get("page_number") or 0),
    )
    links: list[dict[str, Any]] = []
    for left_page, right_page in zip(pages, pages[1:]):
        left_number = int(left_page.get("page_number") or 0)
        right_number = int(right_page.get("page_number") or 0)
        if left_number < 1 or right_number != left_number + 1:
            continue
        left_lines = [
            dict(block)
            for block in list(left_page.get("blocks") or [])
            if block.get("type") == "text_line"
        ]
        right_lines = [
            dict(block)
            for block in list(right_page.get("blocks") or [])
            if block.get("type") == "text_line"
        ]
        left_tail = left_lines[-8:]
        right_head = right_lines[:24]
        left_candidates = [
            (line, marker)
            for line in left_tail
            if (marker := _ordered_marker(line)) is not None
        ]
        right_candidates = [
            (line, marker)
            for line in right_head
            if (marker := _ordered_marker(line)) is not None
        ]
        if not left_candidates or not right_candidates:
            continue
        left_line, left_marker = left_candidates[-1]
        right_line, right_marker = right_candidates[0]
        if (
            left_marker["kind"] != right_marker["kind"]
            or right_marker["ordinal"] != left_marker["ordinal"] + 1
            or left_marker["suffix"] != right_marker["suffix"]
            or not _same_marker_style(left_line, right_line)
        ):
            continue
        left_bbox = dict(left_line.get("bbox") or {})
        right_bbox = dict(right_line.get("bbox") or {})
        left_y = float(left_bbox.get("y") or 0.0)
        right_y = float(right_bbox.get("y") or 0.0)
        if left_y < PAGE_TAIL_BAND_START or right_y > PAGE_HEAD_BAND_END:
            continue

        # 中文注释：边界两个编号外至少再需一个同样式连续编号，避免把页脚等偶然数字当作列表。
        support: list[dict[str, Any]] = []
        for line, marker in [*left_candidates[:-1], *right_candidates[1:]]:
            if marker["kind"] != left_marker["kind"]:
                continue
            if not _same_marker_style(left_line, line):
                continue
            if marker["ordinal"] in {
                left_marker["ordinal"] - 1,
                right_marker["ordinal"] + 1,
            }:
                support.append(marker)
        if not support:
            continue
        right_marker_index = next(
            (
                index
                for index, line in enumerate(right_head)
                if str(line.get("block_id") or "") == right_marker["block_id"]
            ),
            -1,
        )
        if right_marker_index < 0:
            continue
        if right_marker_index > 0:
            # 中文注释：页首无编号文本是左页最后一项的 body，新编号属于后续独立段。
            right_continuation_lines = right_head[:right_marker_index]
        else:
            right_continuation_end = right_marker_index
            expected_ordinal = int(right_marker["ordinal"]) + 1
            body_line_count = 0
            previous_line = right_line
            for line_index in range(right_marker_index + 1, len(right_head)):
                line = right_head[line_index]
                text = str(line.get("text") or "").strip()
                if not text:
                    continue
                candidate = _ordered_marker(line)
                if candidate is not None:
                    if (
                        candidate["kind"] == right_marker["kind"]
                        and candidate["suffix"] == right_marker["suffix"]
                        and _same_marker_style(right_line, line)
                        and int(candidate["ordinal"]) == expected_ordinal
                    ):
                        right_continuation_end = line_index
                        expected_ordinal += 1
                        body_line_count = 0
                        previous_line = line
                        continue
                    break
                font_size = float(line.get("font_size") or 0.0)
                base_font_size = float(right_line.get("font_size") or 0.0)
                indent = float((line.get("indent") or {}).get("normalized_x") or 0.0)
                base_indent = float(
                    (right_line.get("indent") or {}).get("normalized_x") or 0.0
                )
                if (
                    font_size > base_font_size * 1.15
                    or indent < base_indent - 0.08
                ):
                    break
                previous_bbox = dict(previous_line.get("bbox") or {})
                current_bbox = dict(line.get("bbox") or {})
                vertical_gap = float(current_bbox.get("y") or 0.0) - (
                    float(previous_bbox.get("y") or 0.0)
                    + float(previous_bbox.get("height") or 0.0)
                )
                is_unordered_item = bool(re.match(r"^[•◦▪●○\-–—]", text))
                if vertical_gap > 0.03 or (
                    body_line_count >= 2
                    and vertical_gap > 0.004
                    and not is_unordered_item
                ):
                    break
                right_continuation_end = line_index
                body_line_count += 1
                previous_line = line
            right_continuation_lines = right_head[: right_continuation_end + 1]
        if not right_continuation_lines:
            continue
        right_page_text_block_ids = [
            str(line.get("block_id") or "") for line in right_lines
        ]
        links.append(
            {
                "confidence": "high",
                "left_page_number": left_number,
                "right_page_number": right_number,
                "left_marker": left_marker,
                "right_marker": right_marker,
                "support_markers": support,
                "style": {
                    "font_name": str(left_line.get("font_name") or ""),
                    "font_size": float(left_line.get("font_size") or 0.0),
                    "normalized_indent": float(
                        (left_line.get("indent") or {}).get("normalized_x") or 0.0
                    ),
                },
                "left_tail_block_ids": [
                    str(line.get("block_id") or "") for line in left_tail
                ],
                "right_head_block_ids": [
                    str(line.get("block_id") or "") for line in right_head
                ],
                "right_continuation_block_ids": [
                    str(line.get("block_id") or "")
                    for line in right_continuation_lines
                ],
                "right_continuation_line_texts": [
                    str(line.get("text") or "")
                    for line in right_continuation_lines
                ],
                # 中文注释：只有右页全部有效文本行都属于续项时，才允许下游建立整块继承关系。
                "right_page_is_whole_item": (
                    [
                        str(line.get("block_id") or "")
                        for line in right_continuation_lines
                    ]
                    == right_page_text_block_ids
                ),
            }
        )
    return links


def prepare_document_assets(
    *,
    document_id: int,
    source_path: str,
    original_filename: str,
) -> dict[str, Any]:
    """保存原文件并生成可重建的通用页面资产。"""

    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"上传原文件不存在: {source}")

    content_bytes = source.read_bytes()
    if not content_bytes:
        raise ValueError("上传原文件为空")
    source_sha256 = hashlib.sha256(content_bytes).hexdigest()
    asset_dir = _document_dir(document_id)
    revision_dir = asset_dir / "revisions" / source_sha256
    revision_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(original_filename or source.name).suffix.lower() or ".bin"
    stored_source = revision_dir / f"source{suffix}"
    _write_bytes(stored_source, content_bytes)

    pages: list[dict[str, Any]] = []
    document_text = ""
    media_type = "application/octet-stream"
    render_scale = 0.0
    if suffix == ".pdf":
        media_type = "application/pdf"
        reader = pypdf.PdfReader(io.BytesIO(content_bytes))
        try:
            import pypdfium2 as pdfium
        except Exception as exc:
            raise RuntimeError(f"PDF 页面渲染依赖不可用: {exc}") from exc

        pdf = pdfium.PdfDocument(content_bytes)
        layout_pdf = pdfplumber.open(io.BytesIO(content_bytes))
        scale = float(os.getenv("DOCUMENT_ASSET_RENDER_SCALE") or "2.0")
        scale = max(0.5, min(4.0, scale))
        render_scale = scale
        text_parts: list[str] = []
        for page_index, page in enumerate(reader.pages):
            page_number = page_index + 1
            revision_relative = Path("revisions") / source_sha256
            text_relative = revision_relative / "pages" / f"page-{page_number:04d}.txt"
            image_relative = revision_relative / "pages" / f"page-{page_number:04d}.png"
            image_bytes, width, height = _render_pdf_page(pdf[page_index], scale=scale)
            layout_page = layout_pdf.pages[page_index]
            page_text, blocks, marks = _prepare_page_layout(
                layout_page,
                page_number=page_number,
                asset_source_sha256=source_sha256,
            )
            text_parts.append(f"【第 {page_number} 页】\n{page_text}")
            _write_text(asset_dir / text_relative, page_text)
            _write_bytes(asset_dir / image_relative, image_bytes)
            pages.append(
                {
                    "page_number": page_number,
                    "text_path": text_relative.as_posix(),
                    "image_path": image_relative.as_posix(),
                    "text_chars": len(page_text),
                    "text_sha256": hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
                    "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
                    "width": width,
                    "height": height,
                    "media_width": float(layout_page.width),
                    "media_height": float(layout_page.height),
                    "rotation": int(getattr(page, "rotation", 0) or 0),
                    "render_scale": scale,
                    "blocks": blocks,
                    "marks": marks,
                }
            )
        layout_pdf.close()
        document_text = "\n\n".join(text_parts).strip()

    manifest = {
        "schema_version": MANIFEST_VERSION,
        "document_id": int(document_id),
        "original_filename": str(original_filename or source.name),
        "media_type": media_type,
        "source_sha256": source_sha256,
        "source_size": len(content_bytes),
        "source_path": stored_source.relative_to(asset_dir).as_posix(),
        "page_count": len(pages),
        "pages": pages,
        "parser": {
            "name": "pdfplumber_layout_text",
            "version": str(getattr(pdfplumber, "__version__", "")),
            "text_mode": "ordered_text_runs_with_marks",
        },
        "layout_parser": {
            "name": "pdfplumber",
            "version": str(getattr(pdfplumber, "__version__", "")),
        },
        "renderer": {
            "name": "pypdfium2" if render_scale else "",
            "scale": render_scale,
        },
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_manifest(asset_dir / "manifest.json", manifest)
    return {"manifest": manifest, "document_text": document_text}


def load_document_manifest(document_id: int) -> dict[str, Any]:
    manifest_path = _document_dir(document_id) / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"文档资产尚未准备: document_id={int(document_id)}")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or int(value.get("document_id") or 0) != int(document_id):
        raise ValueError(f"文档 manifest 无效: document_id={int(document_id)}")
    return value


def delete_document_assets(document_id: int) -> bool:
    """删除单个文档的派生资产，严格限定在资产根目录内。"""

    root = resolve_document_asset_dir().resolve()
    target = _document_dir(document_id).resolve()
    if root not in target.parents:
        raise ValueError("文档资产删除目标超出资产根目录")
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True


def _manifest_page(manifest: dict[str, Any], page_number: int) -> dict[str, Any]:
    normalized = int(page_number)
    for page in list(manifest.get("pages") or []):
        if int(page.get("page_number") or 0) == normalized:
            return dict(page)
    raise ValueError(f"页码不存在: page_number={normalized}")


def document_page_text(document_id: int, page_number: int) -> str:
    manifest = load_document_manifest(document_id)
    page = _manifest_page(manifest, page_number)
    path = _document_dir(document_id) / str(page.get("text_path") or "")
    if not path.is_file():
        raise FileNotFoundError(f"页面文本资产不存在: page_number={int(page_number)}")
    return path.read_text(encoding="utf-8")


def document_page_layout(document_id: int, page_number: int) -> list[dict[str, Any]]:
    manifest = load_document_manifest(document_id)
    page = _manifest_page(manifest, page_number)
    return [dict(item) for item in list(page.get("blocks") or [])]


def document_page_image_path(document_id: int, page_number: int) -> Path:
    manifest = load_document_manifest(document_id)
    page = _manifest_page(manifest, page_number)
    path = (_document_dir(document_id) / str(page.get("image_path") or "")).resolve()
    asset_dir = _document_dir(document_id).resolve()
    if asset_dir not in path.parents or not path.is_file():
        raise FileNotFoundError(f"页面图像资产不存在: page_number={int(page_number)}")
    return path


def _query_terms(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(query or "")).casefold()
    terms = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", normalized)
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", term):
            expanded.extend(term[index : index + 2] for index in range(len(term) - 1))
    return list(dict.fromkeys(expanded))


def search_document_pages(document_id: int, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """在逐页文本中做通用词法检索，将页码作为视觉检查的候选。"""

    terms = _query_terms(query)
    if not terms:
        raise ValueError("检索词不能为空")
    manifest = load_document_manifest(document_id)
    results: list[dict[str, Any]] = []
    for page in list(manifest.get("pages") or []):
        page_number = int(page.get("page_number") or 0)
        text = document_page_text(document_id, page_number)
        normalized = unicodedata.normalize("NFKC", text).casefold()
        matched = [term for term in terms if term in normalized]
        if not matched:
            continue
        score = sum(normalized.count(term) * max(1, len(term)) for term in matched)
        first_index = min(normalized.find(term) for term in matched)
        start = max(0, first_index - 120)
        end = min(len(text), first_index + 360)
        results.append(
            {
                "page_number": page_number,
                "score": score,
                "matched_terms": matched[:12],
                "snippet": text[start:end].strip(),
            }
        )
    results.sort(key=lambda item: (-int(item["score"]), int(item["page_number"])))
    return results[: max(1, min(int(limit), 20))]
