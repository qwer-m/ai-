from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from modules.knowledge_base_components.document.document_asset_service import (
    _prepare_page_layout,
    delete_document_assets,
    detect_high_confidence_page_continuations,
    document_page_image_path,
    document_page_layout,
    document_page_text,
    load_document_manifest,
    prepare_document_assets,
)


class _LayoutPage:
    width = 600
    height = 800
    images: list[dict] = []
    lines: list[dict] = []
    rects: list[dict] = []
    annots: list[dict] = []

    def extract_words(self, **kwargs):
        assert kwargs["extra_attrs"] == ["fontname", "size"]
        assert kwargs["return_chars"] is True
        return [
            {
                "text": "1. 通用分项",
                "x0": 72,
                "top": 640,
                "x1": 160,
                "bottom": 652,
                "fontname": "GenericSans",
                "size": 11,
            }
        ]


class _ControlOnlyLayoutPage(_LayoutPage):
    def extract_words(self, **kwargs):
        return [
            *super().extract_words(**kwargs),
            {
                "text": "\x01",
                "x0": 72,
                "top": 680,
                "x1": 80,
                "bottom": 692,
                "fontname": "GenericSans",
                "size": 11,
            },
        ]


class _MarkedLayoutPage(_LayoutPage):
    rects = [
        {
            "x0": 96,
            "x1": 132,
            "top": 106,
            "bottom": 106.75,
            "fill": True,
            "non_stroking_color": (0.12, 0.13, 0.16),
        },
        {
            "x0": 144,
            "x1": 168,
            "top": 99,
            "bottom": 113,
            "fill": True,
            "non_stroking_color": (1.0, 0.96, 0.48),
        },
    ]
    annots = [
        {
            "x0": 72,
            "x1": 180,
            "top": 130,
            "bottom": 150,
            "contents": "请复核此处",
            "title": "审阅者",
            "data": {"Subtype": "FreeText"},
        },
        {
            "x0": 72,
            "x1": 180,
            "top": 170,
            "bottom": 190,
            "data": {"Subtype": "Link"},
        },
    ]

    def extract_words(self, **kwargs):
        assert kwargs["return_chars"] is True
        text = "通用规则当前值"
        chars = [
            {
                "text": character,
                "x0": 72 + index * 12,
                "x1": 84 + index * 12,
                "top": 100,
                "bottom": 112,
                "fontname": "GenericSans",
                "size": 12,
                "stroking_color": (0.12, 0.13, 0.16),
                "non_stroking_color": (0.12, 0.13, 0.16),
            }
            for index, character in enumerate(text)
        ]
        return [
            {
                "text": text,
                "x0": 72,
                "top": 100,
                "x1": 72 + len(text) * 12,
                "bottom": 112,
                "fontname": "GenericSans",
                "size": 12,
                "chars": chars,
            }
        ]


def _layout_line(
    *,
    block_id: str,
    text: str,
    y: float,
    font_name: str = "GenericSans",
    font_size: float = 11.0,
    indent: float = 0.12,
) -> dict:
    return {
        "block_id": block_id,
        "type": "text_line",
        "text": text,
        "bbox": {"x": indent, "y": y, "width": 0.5, "height": 0.02},
        "source_bbox": {"x0": 72, "top": 640, "x1": 300, "bottom": 652},
        "indent": {"x": 72, "normalized_x": indent},
        "font_name": font_name,
        "font_size": font_size,
        "source": "pdf_text",
    }


def _continuity_manifest(*, left_y: float = 0.72, right_suffix: str = ".") -> dict:
    return {
        "pages": [
            {
                "page_number": 31,
                "blocks": [
                    _layout_line(
                        block_id="left-support",
                        text="8. 通用分项",
                        y=max(0.60, left_y - 0.04),
                    ),
                    _layout_line(
                        block_id="left-marker",
                        text="9. 通用分项",
                        y=left_y,
                    ),
                ],
            },
            {
                "page_number": 32,
                "blocks": [
                    _layout_line(
                        block_id="right-marker",
                        text=f"10{right_suffix} 通用分项",
                        y=0.08,
                    ),
                    _layout_line(
                        block_id="right-support",
                        text=f"11{right_suffix} 通用分项",
                        y=0.12,
                    ),
                ],
            },
        ]
    }


def test_layout_text_line_keeps_generic_style_indent_and_source_bbox() -> None:
    page_text, blocks, marks = _prepare_page_layout(
        _LayoutPage(),
        page_number=31,
        asset_source_sha256="a" * 64,
    )

    assert page_text == "1. 通用分项"
    assert marks == []
    assert blocks == [
        {
            "block_id": "P0031-T0001",
            "type": "text_line",
            "text": "1. 通用分项",
            "bbox": {
                "x": 0.12,
                "y": 0.8,
                "width": 0.146667,
                "height": 0.015,
            },
            "source_bbox": {"x0": 72.0, "top": 640.0, "x1": 160.0, "bottom": 652.0},
            "indent": {"x": 72.0, "normalized_x": 0.12},
            "font_name": "GenericSans",
            "font_size": 11.0,
            "source": "pdf_text",
            "asset_source_sha256": "a" * 64,
            "source_span": {"start": 0, "end": 7},
            "text_runs": [
                {
                    "run_id": "P0031-T0001-R0001",
                    "text": "1. 通用分项",
                    "source_span": {"start": 0, "end": 7},
                    "source_bbox": {
                        "x0": 72.0,
                        "top": 640.0,
                        "x1": 160.0,
                        "bottom": 652.0,
                    },
                    "bbox": {
                        "x": 0.12,
                        "y": 0.8,
                        "width": 0.146667,
                        "height": 0.015,
                    },
                    "font_name": "GenericSans",
                    "font_size": 11.0,
                    "decorations": [],
                    "mark_ids": [],
                    "asset_source_sha256": "a" * 64,
                }
            ],
        }
    ]


def test_layout_skips_control_only_text_line() -> None:
    _, blocks, _ = _prepare_page_layout(
        _ControlOnlyLayoutPage(),
        page_number=31,
        asset_source_sha256="a" * 64,
    )

    assert [block["text"] for block in blocks] == ["1. 通用分项"]


def test_layout_marks_bind_partial_strikeout_highlight_and_annotation() -> None:
    page_text, blocks, marks = _prepare_page_layout(
        _MarkedLayoutPage(),
        page_number=8,
        asset_source_sha256="b" * 64,
    )

    assert page_text == "通用规则当前值"
    marks_by_type = {mark["type"]: mark for mark in marks}
    assert set(marks_by_type) == {"strikeout", "highlight", "annotation"}
    assert marks_by_type["strikeout"]["target_source_spans"] == [
        {"start": 2, "end": 5}
    ]
    assert marks_by_type["highlight"]["target_source_spans"] == [
        {"start": 6, "end": 7}
    ]
    assert marks_by_type["annotation"]["target_source_spans"] == []
    assert marks_by_type["annotation"]["contents"] == "请复核此处"
    assert all(mark["asset_source_sha256"] == "b" * 64 for mark in marks)

    runs = blocks[0]["text_runs"]
    assert [(run["text"], run["decorations"]) for run in runs] == [
        ("通用", []),
        ("规则当", ["strikeout"]),
        ("前", []),
        ("值", ["highlight"]),
    ]


def test_detects_only_boundary_continuation_with_matching_marker_style() -> None:
    links = detect_high_confidence_page_continuations(_continuity_manifest())
    assert len(links) == 1
    assert links[0]["left_marker"]["ordinal"] == 9
    assert links[0]["right_marker"]["ordinal"] == 10
    assert links[0]["right_page_is_whole_item"] is True

    assert detect_high_confidence_page_continuations(
        _continuity_manifest(right_suffix="、")
    ) == []
    assert detect_high_confidence_page_continuations(
        _continuity_manifest(left_y=0.45)
    ) == []


def test_pdf_assets_are_versioned_and_addressable(monkeypatch, tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    source = tmp_path / "requirement.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    with source.open("wb") as output:
        writer.write(output)

    monkeypatch.setenv("DOCUMENT_ASSET_DIR", str(asset_root))
    result = prepare_document_assets(
        document_id=7,
        source_path=str(source),
        original_filename="通用需求.pdf",
    )

    manifest = load_document_manifest(7)
    assert manifest == result["manifest"]
    assert manifest["page_count"] == 2
    assert manifest["source_path"].startswith(f"revisions/{manifest['source_sha256']}/")
    assert document_page_text(7, 1) == ""
    assert document_page_layout(7, 1) == []
    assert document_page_image_path(7, 2).is_file()
    assert manifest["pages"][0]["image_sha256"]

    assert delete_document_assets(7) is True
    assert not (asset_root / "7").exists()
    assert delete_document_assets(7) is False
