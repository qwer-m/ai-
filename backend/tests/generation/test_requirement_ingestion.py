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


class _DbSession:
    pass


def _upload_file(filename: str, content: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": content_type}),
    )


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


def test_generation_routes_pass_context_to_observable_requirement_parser() -> None:
    route_files = (
        Path("backend/routers/automation/test_generation_generate_routes_impl.py"),
        Path("backend/routers/automation/test_generation_generate_routes_split_helpers.py"),
    )
    offenders: list[str] = []

    for route_file in route_files:
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
    route_files = (
        Path("backend/routers/automation/test_generation_generate_routes_impl.py"),
        Path("backend/routers/automation/test_generation_generate_routes_split_helpers.py"),
    )
    offenders: list[str] = []

    for route_file in route_files:
        tree = ast.parse(route_file.read_text(encoding="utf-8"), filename=str(route_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "parse_requirement_content":
                offenders.append(f"{route_file}:{node.lineno}")

    assert offenders == []


def test_stream_routes_emit_requirement_parse_diag_to_client() -> None:
    route_files = (
        Path("backend/routers/automation/test_generation_generate_routes_impl.py"),
        Path("backend/routers/automation/test_generation_generate_routes_split_helpers.py"),
    )
    offenders: list[str] = []

    for route_file in route_files:
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
