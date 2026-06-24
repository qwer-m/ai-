from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from core.processing.utils import log_to_db, logger
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from core.settings.config import settings
from modules.domain.knowledge_base import knowledge_base
from modules.orchestration.context_orchestrator import context_orchestrator
from modules.orchestration.task_runtime import get_task_runtime
from modules.testing.test_generation import test_generator
from routers.test_generation_routes.support import (
    build_generation_qm,
    detect_duplicate_document,
    get_owned_project,
    parse_requirement_for_generation,
)
from schemas.automation.test_generation import TestGenRequest

router = APIRouter()

@router.post("/estimate-test-count")

async def estimate_test_count(
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    requirement: str = Form(""),
    file: UploadFile | None = File(None),
    prototype_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)

    req_text = (requirement or "").strip()
    if not req_text:
        if not file:
            return {"count": 20}
        req_text, parse_diag = await parse_requirement_for_generation(
            file,
            doc_type,
            prototype_file,
            db=db,
            user_id=current_user.id,
            project_id=project_id,
            source="estimate_test_count",
        )
        log_to_db(db, project_id, "system", f"GEN_DIAG:{json.dumps(parse_diag, ensure_ascii=False)}", user_id=current_user.id)

    try:
        context_bundle = context_orchestrator.assemble_context(
            WorkflowKind.TEST_GENERATION,
            project_id,
            db,
            user_id=current_user.id,
            query_text=req_text[:500],
            requirement_text=req_text[:2000],
            include_knowledge=True,
            include_logs=True,
            knowledge_limit=2,
            log_limit=6,
        )
        log_workflow_trace(
            db,
            project_id,
            current_user.id,
            WorkflowKind.TEST_GENERATION,
            WorkflowStage.CONTEXT,
            {"action": "estimate_test_count", **context_bundle["diagnostics"]},
        )
        count = await run_in_threadpool(
            test_generator.estimate_test_count,
            req_text,
            project_id,
            db,
            current_user.id,
        )
        return {"count": max(1, int(count))}
    except Exception as e:
        logger.warning(f"Estimate test count failed ({type(e).__name__}): {e}")
        detail = str(e).strip() or f"{type(e).__name__}: estimate failed"
        if "Saved AI API key cannot be decrypted" in detail:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=detail)
        from fastapi import HTTPException

        raise HTTPException(status_code=502, detail=f"Estimate test count failed: {detail}")

@router.post("/generate-tests-stream")
async def generate_tests_stream(
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    enable_sample_pool_feedback: bool = Form(True),
    force: bool = Form(False),
    append: bool = Form(False),
    current_biz_key: str = Form(""),
    only_current_biz: bool = Form(False),
    multi_pass: bool = Form(True),
    generation_mode: str = Form(""),
    requirement_text: str = Form(""),
    file: UploadFile | None = File(None),
    prototype_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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

@router.post("/generate-tests")
def generate_tests(
    request: TestGenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(request.project_id, db, current_user.id)

    context_bundle = context_orchestrator.assemble_context(
        WorkflowKind.TEST_GENERATION,
        request.project_id,
        db,
        user_id=current_user.id,
        query_text=request.requirement[:800],
        requirement_text=request.requirement[:2000],
        include_knowledge=True,
        include_logs=True,
        knowledge_limit=5,
        log_limit=10,
    )
    log_workflow_trace(
        db,
        request.project_id,
        current_user.id,
        WorkflowKind.TEST_GENERATION,
        WorkflowStage.CONTEXT,
        {
            "action": "generate_tests",
            "compress": request.compress,
            "expected_count": request.expected_count,
            **context_bundle["diagnostics"],
        },
    )
    log_to_db(
        db,
        request.project_id,
        "system",
        f"开始生成测试用例(批次{request.batch_index}): 长度={len(request.requirement)}, 压缩={request.compress}, 期望数量={request.expected_count}, 批次大小={request.batch_size}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}",
        user_id=current_user.id,
    )
    result = test_generator.generate_test_cases_json(
        request.requirement,
        request.project_id,
        db,
        "requirement",
        request.compress,
        request.expected_count,
        request.batch_size,
        request.batch_index,
        user_id=current_user.id,
        current_biz_key=request.current_biz_key,
        only_current_biz=request.only_current_biz,
        multi_pass=request.multi_pass,
        generation_mode=request.generation_mode,
        enable_sample_pool_feedback=request.enable_sample_pool_feedback,
    )
    if isinstance(result, dict):
        error_code = str(result.get("error_code") or result.get("error") or "").strip()
        if error_code in {"EMPTY_GENERATED_RESULT", "LOW_QUALITY_GENERATED_CASES", "execution_plan_failed"}:
            error_payload = {
                "error_code": error_code,
                "error_message": str(result.get("error_message") or result.get("message") or ""),
                "final_status": str(result.get("final_status") or "failed"),
                "persistence_gate_failed": bool(
                    result.get("persistence_gate_failed")
                    or error_code in {"LOW_QUALITY_GENERATED_CASES", "execution_plan_failed"}
                ),
                "failure_reasons": list(result.get("failure_reasons") or result.get("failed_checks") or []),
                "metrics": dict(result.get("metrics") or {}),
                "state_conflicts": list(result.get("state_conflicts") or []),
            }
            log_to_db(
                db,
                request.project_id,
                "system",
                f"GEN_DIAG:{json.dumps({'kind': 'generation_summary', **error_payload}, ensure_ascii=False)}",
                user_id=current_user.id,
            )
            raise HTTPException(status_code=502, detail=error_payload)
    try:
        count = len(result) if isinstance(result, list) else 0
        log_to_db(db, request.project_id, "system", f"测试用例生成完成(批次{request.batch_index}): 数量={count}", user_id=current_user.id)
        kb_ctx = knowledge_base.get_all_context(db, request.project_id, user_id=current_user.id) if db else ""
        diag = {
            "kind": "gen_diag",
            "mode": "text",
            "doc_type": "requirement",
            "compress": request.compress,
            "expected_count": request.expected_count,
            "generated_count": count,
            "requirement_length": len(request.requirement),
            "kb_length": len(kb_ctx or ""),
            "model": settings.MODEL_NAME,
            "max_tokens": settings.MAX_TOKENS,
            "batch_index": request.batch_index,
            "generation_mode": request.generation_mode or ("multi_pass" if request.multi_pass else "single_pass"),
        }
        log_to_db(db, request.project_id, "system", f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}", user_id=current_user.id)
        try:
            qm = build_generation_qm(result)
            qm["batch_index"] = request.batch_index
            log_to_db(db, request.project_id, "system", f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}", user_id=current_user.id)
        except Exception:
            pass
    except Exception:
        log_to_db(db, request.project_id, "system", "测试用例生成完成", user_id=current_user.id)
    return result

@router.post("/generate-tests/async")

async def generate_tests_async(
    request: TestGenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(request.project_id, db, current_user.id)

    task_id = get_task_runtime().dispatch(
        task_name="modules.orchestration.tasks.generate_test_cases_task",
        kwargs={
            "requirement": request.requirement,
            "project_id": request.project_id,
            "doc_type": "requirement",
            "compress": request.compress,
            "expected_count": request.expected_count,
            "batch_index": request.batch_index,
            "batch_size": request.batch_size,
            "user_id": current_user.id,
            "current_biz_key": request.current_biz_key,
            "only_current_biz": request.only_current_biz,
            "multi_pass": request.multi_pass,
            "generation_mode": request.generation_mode,
            "enable_sample_pool_feedback": request.enable_sample_pool_feedback,
        },
    )
    return {"task_id": task_id, "status": "PENDING", "message": "Task submitted successfully"}

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
    current_user: User = Depends(get_current_user),
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
            f"文件生成测试用例: 主文档长度{len(content)}, 类型={doc_type}, 压缩={compress}, 期望数量={expected_count}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}",
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
    current_user: User = Depends(get_current_user),
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

        task_id = get_task_runtime().dispatch(
            task_name="modules.orchestration.tasks.generate_test_cases_task",
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
        )
        return {
            "task_id": task_id,
            "status": "PENDING",
            "message": "File processed and task submitted successfully",
        }

    except ValueError as e:
        return {"error": str(e)}

@router.post("/generate-tests-excel")
def generate_tests_excel(
    request: TestGenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
            f"文件生成Excel: 主文档长度{len(content)}, 类型={doc_type}, 压缩={compress}, 期望数量={expected_count}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}",
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
        is_excel = not (len(excel_bytes) < 4 or excel_bytes[:2] != b"PK")
        if is_excel:
            return Response(
                content=excel_bytes,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"},
            )
        return Response(
            content=excel_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=test_cases.csv"},
        )
    except ValueError as e:
        return {"error": str(e)}

@router.post("/export-tests-excel")
def export_tests_excel(
    request: list[dict] | dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ = (db, current_user)
    try:
        data_bytes = test_generator.convert_json_to_excel(request)
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
    except Exception as e:
        return {"error": str(e)}
