"""
测试生成路由层。

职责边界：
1. 接收并校验 HTTP 参数、鉴权上下文。
2. 调用测试生成与上下文编排模块完成业务。
3. 维护接口协议（流式返回、重复文档提示、Excel 导出等）。
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
import json

from core.db.database import get_db
from core.db.models import User
from core.authn.auth import get_current_user
from core.processing.utils import log_to_db, logger
from core.settings.config import settings
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from schemas.automation.test_generation import TestGenRequest

from modules.orchestration.context_orchestrator import context_orchestrator
from modules.testing.test_generation import test_generator
from modules.domain.knowledge_base import knowledge_base
from modules.orchestration.tasks import generate_test_cases_task
from routers.test_generation_routes.support import (
    build_generation_qm,
    detect_duplicate_document,
    get_owned_project,
    parse_requirement_content,
)

router = APIRouter(
    prefix="",  # Prefix will be handled by main app inclusion or we can put specific prefixes here
    tags=["Test Generation"]
)
# Note: In main.py, prefix was /api, and routes were /generate-tests.
# So if we mount this router with prefix /api, then routes here should be /generate-tests

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
    """
    估算测试用例数量，兼容文本与文件两种输入模式。
    """
    get_owned_project(project_id, db, current_user.id)

    req_text = (requirement or "").strip()
    if not req_text:
        if not file:
            return {"count": 20}
        req_text = await parse_requirement_content(file, doc_type, prototype_file)

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
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=502, detail=f"Estimate test count failed: {detail}")


@router.post("/generate-tests-stream")
async def generate_tests_stream(
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    force: bool = Form(False),
    append: bool = Form(False),
    requirement_text: str = Form(""),
    file: UploadFile | None = File(None),
    prototype_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    流式生成测试用例，返回纯文本流（前端按 chunk 增量解析）。
    """
    get_owned_project(project_id, db, current_user.id)

    content = (requirement_text or "").strip()
    uploaded_filename: str | None = None
    if not content:
        if not file:
            return JSONResponse(status_code=400, content={"error": "Missing requirement_text or file"})
        uploaded_filename = file.filename
        content = await parse_requirement_content(file, doc_type, prototype_file)

        # 文件模式下保留“重复文档提示”能力，和前端 @@DUPLICATE@@ 协议对齐
        payload = detect_duplicate_document(
            db,
            filename=uploaded_filename or "uploaded_file",
            content=content,
            doc_type=doc_type,
            project_id=project_id,
            force=force,
            user_id=current_user.id,
        )
        if payload:
            def duplicate_stream():
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
    )
    return StreamingResponse(stream_iter, media_type="text/plain; charset=utf-8")


@router.post("/generate-tests")
def generate_tests(request: TestGenRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    鍚屾鐢熸垚娴嬭瘯鐢ㄤ緥 (Synchronous Test Generation)
    """
    # Verify project
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
    log_to_db(db, request.project_id, "system", f"开始生成测试用例(批次{request.batch_index}): 长度={len(request.requirement)}, 压缩={request.compress}, 预期数量={request.expected_count}, 批次大小={request.batch_size}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}", user_id=current_user.id)
    result = test_generator.generate_test_cases_json(request.requirement, request.project_id, db, "requirement", request.compress, request.expected_count, request.batch_size, request.batch_index, user_id=current_user.id)
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
            "batch_index": request.batch_index
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
async def generate_tests_async(request: TestGenRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Trigger test generation asynchronously using Celery.
    Returns task_id for status tracking.
    """
    # Verify project
    get_owned_project(request.project_id, db, current_user.id)

    task = generate_test_cases_task.delay(
        requirement=request.requirement,
        project_id=request.project_id,
        doc_type="requirement",
        compress=request.compress,
        expected_count=request.expected_count,
        batch_index=request.batch_index,
        batch_size=request.batch_size,
        user_id=current_user.id
    )
    return {"task_id": task.id, "status": "PENDING", "message": "Task submitted successfully"}

@router.post("/generate-tests-file")
async def generate_tests_from_file(
    file: UploadFile = File(...), 
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    prototype_file: UploadFile | None = File(None),
    compress: bool = Form(False),
    expected_count: int = Form(20),
    force: bool = Form(False),
    append: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify project
    get_owned_project(project_id, db, current_user.id)

    try:
        content = await parse_requirement_content(file, doc_type, prototype_file)

        # 文件模式保留重复文档提示能力，返回结构与历史实现保持一致。
        duplicate_payload = detect_duplicate_document(
            db,
            filename=file.filename,
            content=content,
            doc_type=doc_type,
            project_id=project_id,
            force=force,
            user_id=current_user.id,
        )
        if duplicate_payload:
            return duplicate_payload

        log_to_db(db, project_id, "system", f"文件生成测试用例: 主文档长度={len(content)}, 类型={doc_type}, 压缩={compress}, 预期数量={expected_count}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}", user_id=current_user.id)
        # Run sync generation in threadpool to avoid blocking event loop
        result = await run_in_threadpool(
            test_generator.generate_test_cases_json,
            content, project_id, db, doc_type, compress, expected_count, 20, 0, current_user.id
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
                "max_tokens": settings.MAX_TOKENS
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
    force: bool = Form(False),
    append: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Async version of generate-tests-file.
    Uploads file, parses it (sync), then submits Celery task.
    """
    # Verify project
    get_owned_project(project_id, db, current_user.id)

    try:
        content = await parse_requirement_content(file, doc_type, prototype_file)

        duplicate_payload = detect_duplicate_document(
            db,
            filename=file.filename,
            content=content,
            doc_type=doc_type,
            project_id=project_id,
            force=force,
            user_id=current_user.id,
        )
        if duplicate_payload:
            return duplicate_payload

        # Submit task
        task = generate_test_cases_task.delay(
            requirement=content,
            project_id=project_id,
            doc_type=doc_type,
            compress=compress,
            expected_count=expected_count,
            user_id=current_user.id
        )
        return {"task_id": task.id, "status": "PENDING", "message": "File processed and task submitted successfully"}

    except ValueError as e:
        return {"error": str(e)}

@router.post("/generate-tests-excel")
def generate_tests_excel(request: TestGenRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        # Verify project
        get_owned_project(request.project_id, db, current_user.id)

        # request doesn't have doc_type, assuming standard requirement
        excel_bytes = test_generator.generate_test_cases_excel(request.requirement, request.project_id, db, user_id=current_user.id)
        return Response(content=excel_bytes, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"})
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
    current_user: User = Depends(get_current_user)
):
    # Verify project
    get_owned_project(project_id, db, current_user.id)

    try:
        content = await parse_requirement_content(file, doc_type, prototype_file)
        log_to_db(db, project_id, "system", f"文件生成Excel: 主文档长度={len(content)}, 类型={doc_type}, 压缩={compress}, 预期数量={expected_count}, 模型={settings.MODEL_NAME}, max_tokens={settings.MAX_TOKENS}", user_id=current_user.id)
        excel_bytes = test_generator.generate_test_cases_excel(content, project_id, db, doc_type, compress, user_id=current_user.id)
        is_excel = True
        if len(excel_bytes) < 4 or excel_bytes[:2] != b'PK':
            is_excel = False
        if is_excel:
            return Response(content=excel_bytes, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"})
        else:
            return Response(content=excel_bytes, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=test_cases.csv"})
    except ValueError as e:
        return {"error": str(e)}

@router.post("/export-tests-excel")
def export_tests_excel(
    request: list[dict] | dict, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        data_bytes = test_generator.convert_json_to_excel(request)
        is_excel = True
        # Heuristic: if starts with 'PK' (zip signature) it's xlsx; otherwise CSV
        if len(data_bytes) < 4 or data_bytes[:2] != b'PK':
            is_excel = False

        if is_excel:
            return Response(content=data_bytes, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"})
        else:
            return Response(content=data_bytes, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=test_cases.csv"})
    except Exception as e:
        return {"error": str(e)}

