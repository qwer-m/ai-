from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from .test_generation_generate_routes_runtime import (
    detect_duplicate_document,
    get_current_user,
    get_db,
    get_owned_project,
    log_to_db,
    parse_requirement_for_generation,
    test_generator,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/generate-tests-stream")
async def generate_tests_stream(
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    enable_sample_pool_feedback: bool = Form(True),
    force: bool = Form(False),
    append: bool = Form(False),
    previous_generation_id: int | None = Form(None),
    current_biz_key: str = Form(""),
    only_current_biz: bool = Form(False),
    multi_pass: bool = Form(True),
    generation_mode: str = Form(""),
    requirement_text: str = Form(""),
    file: UploadFile | None = File(None),
    prototype_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)

    content = (requirement_text or "").strip()
    uploaded_filename: str | None = None
    initial_diag_lines: list[str] = []
    if not content:
        if not file:
            return JSONResponse(status_code=400, content={"error": "Missing requirement_text or file"})
        uploaded_filename = file.filename
        content, parse_diag = await parse_requirement_for_generation(
            file,
            doc_type,
            prototype_file,
            db=db,
            user_id=current_user.id,
            project_id=project_id,
            source="generate_tests_stream",
        )
        parse_diag_line = f"GEN_DIAG:{json.dumps(parse_diag, ensure_ascii=False)}\n"
        initial_diag_lines.append(parse_diag_line)
        log_to_db(db, project_id, "system", parse_diag_line.strip(), user_id=current_user.id)

        payload = detect_duplicate_document(
            db,
            filename=uploaded_filename or "uploaded_file",
            content=content,
            doc_type=doc_type,
            project_id=project_id,
            force=force,
            user_id=current_user.id,
        )
        if payload and not append:

            def duplicate_stream():
                yield from initial_diag_lines
                yield "@@DUPLICATE@@" + json.dumps(payload, ensure_ascii=False)

            return StreamingResponse(duplicate_stream(), media_type="text/plain; charset=utf-8")

    stream_iter = test_generator.generate_test_cases_stream(
        requirement=content,
        project_id=project_id,
        db=db,
        doc_type=doc_type,
        compress=compress,
        expected_count=max(1, int(expected_count)),
        batch_size=10,
        overwrite=force,
        append=append,
        previous_generation_id=previous_generation_id,
        user_id=current_user.id,
        current_biz_key=current_biz_key,
        only_current_biz=only_current_biz,
        multi_pass=multi_pass,
        generation_mode=generation_mode,
        enable_sample_pool_feedback=enable_sample_pool_feedback,
    )

    def guarded_stream():
        try:
            yield from initial_diag_lines
            yield from stream_iter
        except Exception as e:
            logger.exception("generate-tests-stream failed: %s", e)
            yield "\n@@STATUS@@:生成失败\n"
            yield f"Error: {type(e).__name__}: {str(e) or 'unknown error'}\n"

    return StreamingResponse(guarded_stream(), media_type="text/plain; charset=utf-8")
