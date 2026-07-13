from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from core.settings.config import settings
from .test_generation_generate_routes_runtime import (
    get_current_user,
    get_db,
    get_owned_project,
    log_to_db,
    parse_requirement_for_generation,
    test_generator,
)
from schemas.automation.test_generation import TestGenRequest

router = APIRouter()


def _excel_or_csv_response(data_bytes: bytes) -> Response:
    is_excel = not (len(data_bytes) < 4 or data_bytes[:2] != b"PK")
    if is_excel:
        return Response(
            content=data_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"},
        )
    return Response(
        content=data_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=test_cases.csv"},
    )


@router.post("/generate-tests-excel")
def generate_tests_excel(
    request: TestGenRequest,
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user),
):
    try:
        get_owned_project(request.project_id, db, current_user.id)
        excel_bytes = test_generator.generate_test_cases_excel(
            request.requirement,
            request.project_id,
            db,
            user_id=current_user.id,
        )
        return Response(
            content=excel_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"},
        )
    except Exception as e:
        return {"error": str(e)}


@router.post("/generate-tests-file-excel")
async def generate_tests_from_file_excel(
    file: UploadFile = File(...),
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    prototype_file: UploadFile | None = File(None),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)

    try:
        content, parse_diag = await parse_requirement_for_generation(
            file,
            doc_type,
            prototype_file,
            db=db,
            user_id=current_user.id,
            project_id=project_id,
            source="generate_tests_from_file_excel",
        )
        log_to_db(db, project_id, "system", f"GEN_DIAG:{json.dumps(parse_diag, ensure_ascii=False)}", user_id=current_user.id)
        log_to_db(
            db,
            project_id,
            "system",
            (
                f"文件生成Excel: 主文档长度={len(content)}, 类型={doc_type}, "
                f"压缩={compress}, 期望数量={expected_count}, "
                f"模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}"
            ),
            user_id=current_user.id,
        )
        excel_bytes = test_generator.generate_test_cases_excel(
            content,
            project_id,
            db,
            doc_type,
            compress,
            user_id=current_user.id,
        )
        return _excel_or_csv_response(excel_bytes)
    except ValueError as e:
        return {"error": str(e)}


@router.post("/export-tests-excel")
def export_tests_excel(
    request: list[dict] | dict,
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user),
):
    _ = (db, current_user)
    try:
        data_bytes = test_generator.convert_json_to_excel(request)
        return _excel_or_csv_response(data_bytes)
    except Exception as e:
        return {"error": str(e)}
