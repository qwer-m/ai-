from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from core.settings.config import settings
from modules.orchestration.background_task_governance import (
    BackgroundTaskKind,
    submit_background_task,
)
from .test_generation_generate_routes_runtime import (
    build_generation_qm,
    detect_duplicate_document,
    get_current_user,
    get_db,
    get_owned_project,
    knowledge_base,
    log_to_db,
    parse_requirement_for_generation,
    test_generator,
)

router = APIRouter()


@router.post("/generate-tests-file")
async def generate_tests_from_file(
    file: UploadFile = File(...),
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    prototype_file: UploadFile | None = File(None),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    enable_sample_pool_feedback: bool = Form(True),
    force: bool = Form(False),
    append: bool = Form(False),
    current_biz_key: str = Form(""),
    only_current_biz: bool = Form(False),
    multi_pass: bool = Form(True),
    generation_mode: str = Form(""),
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
            source="generate_tests_from_file",
        )
        log_to_db(db, project_id, "system", f"GEN_DIAG:{json.dumps(parse_diag, ensure_ascii=False)}", user_id=current_user.id)

        duplicate_payload = detect_duplicate_document(
            db,
            filename=file.filename,
            content=content,
            doc_type=doc_type,
            project_id=project_id,
            force=force,
            user_id=current_user.id,
        )
        if duplicate_payload and not append:
            return duplicate_payload

        log_to_db(
            db,
            project_id,
            "system",
            (
                f"文件生成测试用例: 主文档长度={len(content)}, 类型={doc_type}, "
                f"压缩={compress}, 期望数量={expected_count}, "
                f"模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}"
            ),
            user_id=current_user.id,
        )
        result = await run_in_threadpool(
            test_generator.generate_test_cases_json,
            content,
            project_id,
            db,
            doc_type,
            compress,
            expected_count,
            20,
            0,
            current_user.id,
            current_biz_key,
            only_current_biz,
            multi_pass,
            generation_mode,
            enable_sample_pool_feedback,
        )
        try:
            count = len(result) if isinstance(result, list) else 0
            log_to_db(db, project_id, "system", f"文件生成完成: 数量={count}", user_id=current_user.id)
            kb_ctx = knowledge_base.get_all_context(db, project_id, user_id=current_user.id) if db else ""
            diag = {
                "kind": "gen_diag",
                "mode": "file",
                "doc_type": doc_type,
                "compress": compress,
                "expected_count": expected_count,
                "generated_count": count,
                "content_length": len(content),
                "kb_length": len(kb_ctx or ""),
                "prototype_included": bool(prototype_file),
                "model": settings.MODEL_NAME,
                "max_tokens": settings.MAX_TOKENS,
                "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
            }
            log_to_db(db, project_id, "system", f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}", user_id=current_user.id)
            try:
                qm = build_generation_qm(result)
                log_to_db(db, project_id, "system", f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}", user_id=current_user.id)
            except Exception:
                pass
        except Exception:
            pass
        return result
    except ValueError as e:
        return {"error": str(e)}


@router.post("/generate-tests-file/async")
async def generate_tests_from_file_async(
    file: UploadFile = File(...),
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    prototype_file: UploadFile | None = File(None),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    enable_sample_pool_feedback: bool = Form(True),
    force: bool = Form(False),
    append: bool = Form(False),
    current_biz_key: str = Form(""),
    only_current_biz: bool = Form(False),
    multi_pass: bool = Form(True),
    generation_mode: str = Form(""),
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
            source="generate_tests_from_file_async",
        )
        log_to_db(db, project_id, "system", f"GEN_DIAG:{json.dumps(parse_diag, ensure_ascii=False)}", user_id=current_user.id)

        duplicate_payload = detect_duplicate_document(
            db,
            filename=file.filename,
            content=content,
            doc_type=doc_type,
            project_id=project_id,
            force=force,
            user_id=current_user.id,
        )
        if duplicate_payload and not append:
            return duplicate_payload

        queue_result = submit_background_task(
            BackgroundTaskKind.TEST_GENERATION,
            kwargs={
                "requirement": content,
                "project_id": project_id,
                "doc_type": doc_type,
                "compress": compress,
                "expected_count": expected_count,
                "user_id": current_user.id,
                "current_biz_key": current_biz_key,
                "only_current_biz": only_current_biz,
                "multi_pass": multi_pass,
                "generation_mode": generation_mode,
                "enable_sample_pool_feedback": enable_sample_pool_feedback,
            },
            business_id=project_id,
            reason="generate_tests_file_async",
        )
        return {
            "task_id": queue_result.id,
            "status": "PENDING",
            "message": "File processed and task submitted successfully",
            "queue_result": queue_result.to_dict(),
        }

    except ValueError as e:
        return {"error": str(e)}
