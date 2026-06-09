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
from modules.orchestration.tasks import generate_test_cases_task
from modules.testing.test_generation import test_generator
from routers.test_generation_routes.support import (
    build_generation_qm,
    detect_duplicate_document,
    get_owned_project,
    parse_requirement_content,
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
    if not content:
        if not file:
            return JSONResponse(status_code=400, content={"error": "Missing requirement_text or file"})
        uploaded_filename = file.filename
        content = await parse_requirement_content(file, doc_type, prototype_file)

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
    if isinstance(result, list) and len(result) == 0:
        error_payload = {
            "error_code": "EMPTY_GENERATED_RESULT",
            "error_message": "生成完成但最终测试用例为空",
            "final_status": "empty_result_failed",
            "empty_result_guard_triggered": True,
            "empty_result_stage": "api_response_guard",
        }
        log_to_db(
            db,
            request.project_id,
            "system",
            f"GEN_DIAG:{json.dumps({'kind': 'generation_summary', **error_payload}, ensure_ascii=False)}",
            user_id=current_user.id,
        )
        raise HTTPException(status_code=502, detail=error_payload)
    if isinstance(result, dict):
        error_code = str(result.get("error_code") or result.get("error") or "").strip()
        if error_code == "EMPTY_GENERATED_RESULT":
            error_payload = {
                "error_code": "EMPTY_GENERATED_RESULT",
                "error_message": str(result.get("error_message") or "生成完成但最终测试用例为空"),
                "final_status": str(result.get("final_status") or "empty_result_failed"),
                "empty_result_guard_triggered": bool(result.get("empty_result_guard_triggered", True)),
                "empty_result_stage": str(result.get("empty_result_stage") or "generation_postprocess"),
            }
            raise HTTPException(status_code=502, detail=error_payload)
        if error_code == "LOW_QUALITY_GENERATED_CASES":
            error_payload = {
                "error_code": "LOW_QUALITY_GENERATED_CASES",
                "error_message": str(result.get("error_message") or "生成结果未通过质量门禁"),
                "final_status": str(result.get("final_status") or "quality_gate_failed"),
                "quality_gate_failed": bool(result.get("quality_gate_failed", True)),
                "failed_checks": list(result.get("failed_checks") or []),
                "priority_final_null_count": int(result.get("priority_final_null_count") or 0),
                "invalid_priority_final_case_ids": list(result.get("invalid_priority_final_case_ids") or []),
                "non_assertable_expected_result_count": int(result.get("non_assertable_expected_result_count") or 0),
                "truncated_text_count": int(result.get("truncated_text_count") or 0),
                "non_assertable_case_ids": list(result.get("non_assertable_case_ids") or []),
                "truncated_case_ids": list(result.get("truncated_case_ids") or []),
            }
            log_to_db(
                db,
                request.project_id,
                "system",
                f"GEN_DIAG:{json.dumps({'kind': 'generation_summary', **error_payload}, ensure_ascii=False)}",
                user_id=current_user.id,
            )
            raise HTTPException(status_code=502, detail=error_payload)
        if error_code == "execution_plan_failed":
            error_payload = {
                "error_code": "execution_plan_failed",
                "error_message": str(result.get("error_message") or "生成结果未通过执行计划门禁"),
                "final_status": str(result.get("final_status") or "execution_plan_failed"),
                "persistence_gate_failed": bool(result.get("persistence_gate_failed", True)),
                "failure_reasons": list(result.get("failure_reasons") or []),
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
