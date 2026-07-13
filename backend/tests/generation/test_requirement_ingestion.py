from __future__ import annotations

import ast
import asyncio
import io
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from starlette.datastructures import Headers

from core.processing import file_processing
from routers.test_generation_routes import support


GENERATION_ROUTE_FILES = (
    Path("backend/routers/automation/test_generation_generate_routes_estimate.py"),
    Path("backend/routers/automation/test_generation_generate_routes_stream.py"),
    Path("backend/routers/automation/test_generation_generate_routes_json.py"),
    Path("backend/routers/automation/test_generation_generate_routes_file.py"),
    Path("backend/routers/automation/test_generation_generate_routes_excel.py"),
)

GENERATION_REQUIREMENT_PARSE_ROUTE_FILES = (
    Path("backend/routers/automation/test_generation_generate_routes_estimate.py"),
    Path("backend/routers/automation/test_generation_generate_routes_stream.py"),
    Path("backend/routers/automation/test_generation_generate_routes_file.py"),
    Path("backend/routers/automation/test_generation_generate_routes_excel.py"),
)

GENERATION_STREAM_ROUTE_FILES = (
    Path("backend/routers/automation/test_generation_generate_routes_stream.py"),
)


class _DbSession:
    pass


def _upload_file(filename: str, content: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": content_type}),
    )


def _image_only_pdf_bytes() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (420, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.text((24, 32), "Forum reply button and pinned post label", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PDF")
    return buffer.getvalue()


def _prompt_section(content: str, title: str) -> str:
    marker = f"[{title}]"
    start = content.find(marker)
    if start < 0:
        return ""
    next_start = content.find("\n\n[", start + len(marker))
    if next_start < 0:
        return content[start:]
    return content[start:next_start]


def test_parse_requirement_artifact_passes_user_context_to_image_ocr(monkeypatch) -> None:
    db = _DbSession()
    calls: list[dict[str, Any]] = []

    def _fake_ocr(filename: str, content_bytes: bytes, image_prompt: str, db: Any = None, user_id: int | None = None):
        calls.append(
            {
                "filename": filename,
                "content_bytes": content_bytes,
                "image_prompt": image_prompt,
                "db": db,
                "user_id": user_id,
            }
        )
        return (
            "Checkout requirement image: submit order button and total amount label.",
            {"ocr_source": "cloud", "cloud_fallback": True, "error": ""},
        )

    monkeypatch.setattr(file_processing, "parse_image_bytes_with_fallback", _fake_ocr)

    artifact = asyncio.run(
        support.parse_requirement_artifact(
            _upload_file("requirement.png", b"real-image-bytes", "image/png"),
            "requirement",
            db=db,
            user_id=42,
        )
    )
    content = artifact.content

    assert "Checkout requirement image" in content
    assert "[Parsed Requirement Evidence]" in content
    assert "ocr_source=cloud" in content
    assert calls == [
        {
            "filename": "requirement.png",
            "content_bytes": b"real-image-bytes",
            "image_prompt": "OCR: Extract all text from this image.",
            "db": db,
            "user_id": 42,
        }
    ]


def test_incomplete_requirement_adds_prototype_evidence_alignment(monkeypatch) -> None:
    db = _DbSession()
    calls: list[dict[str, Any]] = []

    def _fake_ocr(filename: str, content_bytes: bytes, image_prompt: str, db: Any = None, user_id: int | None = None):
        calls.append({"filename": filename, "db": db, "user_id": user_id, "prompt": image_prompt})
        return (
            "Checkout page shows total amount, submit order button, and payment method selector.",
            {"ocr_source": "cloud", "cloud_fallback": True, "error": ""},
        )

    monkeypatch.setattr(file_processing, "parse_image_bytes_with_fallback", _fake_ocr)

    artifact = asyncio.run(
        support.parse_requirement_artifact(
            _upload_file(
                "requirement.txt",
                b"Checkout page must show total amount and allow the user to submit order.",
                "text/plain",
            ),
            "incomplete",
            _upload_file("prototype.png", b"prototype-image-bytes", "image/png"),
            db=db,
            user_id=7,
        )
    )
    content = artifact.content

    assert "[Prototype Analysis]" in content
    assert "payment method selector" in content
    assert "[Multimodal Evidence Alignment]" in content
    assert "prototype:prototype.png" in content
    assert calls == [
        {
            "filename": "prototype.png",
            "db": db,
            "user_id": 7,
            "prompt": (
                "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely "
                "interactions. Identify input fields, buttons, navigation menus, and any visual indicators of state."
            ),
        }
    ]


def test_parse_requirement_for_generation_returns_observable_diag(monkeypatch) -> None:
    db = _DbSession()

    def _fake_ocr(filename: str, content_bytes: bytes, image_prompt: str, db: Any = None, user_id: int | None = None):
        return (
            "Checkout page shows total amount and submit order button.",
            {"ocr_source": "cloud", "cloud_fallback": True, "error": ""},
        )

    monkeypatch.setattr(file_processing, "parse_image_bytes_with_fallback", _fake_ocr)

    content, diag = asyncio.run(
        support.parse_requirement_for_generation(
            _upload_file("requirement.txt", b"Checkout page must show total amount.", "text/plain"),
            "incomplete",
            _upload_file("prototype.png", b"prototype-image-bytes", "image/png"),
            db=db,
            user_id=9,
            project_id=88,
            source="generate_tests_stream",
        )
    )

    assert "Prototype Analysis" in content
    assert diag["kind"] == "requirement_parse"
    assert diag["source"] == "generate_tests_stream"
    assert diag["project_id"] == 88
    assert diag["doc_type"] == "incomplete"
    assert diag["alignment_count"] >= 1
    assert diag["blocks"][0]["role"] == "main_requirement"
    assert diag["blocks"][1]["role"] == "prototype"
    assert diag["blocks"][1]["ocr_source"] == "cloud"
    assert diag["blocks"][1]["cloud_fallback"] is True


def test_cloud_ocr_success_keeps_local_failure_as_warning() -> None:
    block = support.RequirementContentBlock(
        role="prototype",
        filename="prototype.png",
        text="Cloud OCR extracted button text.",
        meta={
            "is_image": True,
            "ocr": {
                "ocr_source": "cloud",
                "cloud_fallback": True,
                "error": "",
                "local_ocr_error": "local_ocr_dependency_missing: No module named 'pytesseract'",
            },
        },
    )

    meta = block.to_meta()

    assert meta["ocr_source"] == "cloud"
    assert meta["ocr_error"] == ""
    assert meta["ocr_warning"] == "local_ocr_dependency_missing: No module named 'pytesseract'"
    assert meta["ocr_status"] == "ok_with_warning"
    assert meta["ocr_blocking"] is False


def test_failed_pdf_visual_ocr_is_non_blocking_diag() -> None:
    block = support.RequirementContentBlock(
        role="pdf_visual",
        filename="X46.jpg",
        text="[Image OCR Failed: model is not multimodal]",
        meta={
            "is_image": True,
            "parse_strategy": "pdf_image_ocr",
            "ocr": {
                "ocr_source": "failed",
                "cloud_fallback": True,
                "error": "Error: HTTP 400 - model is not multimodal",
                "local_ocr_error": "local_ocr_empty",
            },
        },
    )

    meta = block.to_meta()

    assert meta["ocr_source"] == "failed"
    assert meta["ocr_error"] == "Error: HTTP 400 - model is not multimodal"
    assert meta["ocr_warning"] == "local_ocr_empty"
    assert meta["ocr_status"] == "failed"
    assert meta["ocr_blocking"] is False


def test_invalid_pdf_visual_ocr_is_excluded_from_prompt_evidence_but_kept_in_meta() -> None:
    blocks = [
        support.RequirementContentBlock(
            role="main_requirement",
            filename="requirement.txt",
            text="订单结算页必须展示订单金额和支付按钮，并支持用户提交订单。",
            meta={"parse_strategy": "file_text"},
        ),
        support.RequirementContentBlock(
            role="pdf_visual",
            filename="valid-page.png",
            text="结算页展示订单金额、支付按钮、提交订单按钮。",
            meta={
                "is_image": True,
                "parse_strategy": "pdf_image_ocr",
                "ocr": {"ocr_source": "cloud", "cloud_fallback": True, "error": ""},
            },
        ),
        support.RequirementContentBlock(
            role="pdf_visual",
            filename="invalid-page.png",
            text="与需求不匹配，无法提取截图中的有效信息，请上传正确截图；不存在UI控件，无相关内容。订单金额 支付按钮 提交订单",
            meta={
                "is_image": True,
                "parse_strategy": "pdf_image_ocr",
                "ocr": {"ocr_source": "cloud", "cloud_fallback": True, "error": ""},
            },
        ),
    ]
    artifact = support.RequirementParseArtifact(
        blocks=blocks,
        alignments=support._align_blocks_to_requirement(blocks),
    )

    content = artifact.content
    parsed_evidence = _prompt_section(content, "Parsed Requirement Evidence")
    alignment = _prompt_section(content, "Multimodal Evidence Alignment")
    understanding = _prompt_section(content, "Requirement Understanding")

    assert "Requirement Understanding" in understanding
    assert "valid-page.png" in understanding
    assert "valid-page.png" in parsed_evidence
    assert "valid-page.png" in alignment
    assert "invalid-page.png" not in parsed_evidence
    assert "invalid-page.png" not in alignment
    assert "invalid-page.png" not in understanding
    assert "请上传正确截图" not in content
    assert "不存在UI控件" not in content
    assert all(item["filename"] != "invalid-page.png" for item in artifact.alignments)
    meta = artifact.to_meta()
    assert any(item["filename"] == "invalid-page.png" for item in meta["blocks"])
    assert meta["requirement_understanding"]["visual_fact_count"] == 1
    assert meta["requirement_understanding"]["invalid_visual_block_count"] == 1


def test_pdf_visual_blocks_are_exposed_after_file_text_parse() -> None:
    artifact = asyncio.run(
        support.parse_requirement_artifact(
            _upload_file("forum-visual.pdf", _image_only_pdf_bytes(), "application/pdf"),
            "requirement",
        )
    )

    assert artifact.blocks[0].role == "main_requirement"
    assert artifact.blocks[0].meta["parse_strategy"] == "file_text+pdf_visual_ocr"
    visual_blocks = [block for block in artifact.blocks if block.role == "pdf_visual"]
    assert len(visual_blocks) >= 1
    visual_meta = visual_blocks[0].meta
    assert visual_meta["parse_strategy"] == "pdf_image_ocr"
    assert visual_meta["parent_filename"] == "forum-visual.pdf"
    assert visual_meta["source_kind"] in {"embedded_image", "rendered_page"}
    assert int(artifact.blocks[0].meta["pdf_visual_extraction"]["pdf_visual_block_count"]) >= 1
    assert "[Attachment:" not in artifact.content
    assert "[Requirement Understanding]" in artifact.content
    assert "pdf_visual" in artifact.content


def test_pdf_visual_blocks_are_in_requirement_parse_diag() -> None:
    _content, diag = asyncio.run(
        support.parse_requirement_for_generation(
            _upload_file("forum-visual.pdf", _image_only_pdf_bytes(), "application/pdf"),
            "requirement",
            project_id=88,
            source="generate_tests_stream",
        )
    )

    assert diag["kind"] == "requirement_parse"
    assert diag["project_id"] == 88
    assert diag["blocks"][0]["role"] == "main_requirement"
    assert diag["blocks"][0]["parse_strategy"] == "file_text+pdf_visual_ocr"
    assert diag["blocks"][0]["pdf_visual_extraction"]["visual_block_count"] >= 1
    visual_meta = next(item for item in diag["blocks"] if item["role"] == "pdf_visual")
    assert visual_meta["is_image"] is True
    assert visual_meta["parent_filename"] == "forum-visual.pdf"
    assert visual_meta["source_page"] >= 1
    assert diag["requirement_understanding"]["visual_fact_count"] >= 1
    assert diag["requirement_understanding"]["visual_facts"][0]["source"].startswith("pdf_visual:")


def test_generation_routes_pass_context_to_observable_requirement_parser() -> None:
    offenders: list[str] = []

    for route_file in GENERATION_REQUIREMENT_PARSE_ROUTE_FILES:
        tree = ast.parse(route_file.read_text(encoding="utf-8"), filename=str(route_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "parse_requirement_for_generation":
                continue
            keyword_names = {keyword.arg for keyword in node.keywords}
            if not {"db", "user_id", "project_id", "source"}.issubset(keyword_names):
                offenders.append(f"{route_file}:{node.lineno}")

    assert offenders == []


def test_generation_routes_use_observable_requirement_parser() -> None:
    offenders: list[str] = []

    for route_file in GENERATION_ROUTE_FILES:
        tree = ast.parse(route_file.read_text(encoding="utf-8"), filename=str(route_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "parse_requirement_content":
                offenders.append(f"{route_file}:{node.lineno}")

    assert offenders == []


def test_stream_routes_emit_requirement_parse_diag_to_client() -> None:
    offenders: list[str] = []

    for route_file in GENERATION_STREAM_ROUTE_FILES:
        source = route_file.read_text(encoding="utf-8")
        if "initial_diag_lines: list[str] = []" not in source:
            offenders.append(f"{route_file}:missing_initial_diag_buffer")
        if 'parse_diag_line = f"GEN_DIAG:{json.dumps(parse_diag, ensure_ascii=False)}\\n"' not in source:
            offenders.append(f"{route_file}:missing_parse_diag_line")
        if "initial_diag_lines.append(parse_diag_line)" not in source:
            offenders.append(f"{route_file}:missing_parse_diag_append")
        if source.count("yield from initial_diag_lines") < 2:
            offenders.append(f"{route_file}:missing_stream_yield")

    assert offenders == []
