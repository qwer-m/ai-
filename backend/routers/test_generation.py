from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
import json
import os

from core.database import get_db
from core.models import Project, User, TestGeneration
from core.auth import get_current_user
from core.utils import log_to_db, logger
from core.file_processing import parse_file_content
from core.config import settings
from schemas.test_generation import TestGenRequest

from modules.test_generation import test_generator
from modules.knowledge_base import knowledge_base
from modules.tasks import generate_test_cases_task

router = APIRouter(
    prefix="",  # Prefix will be handled by main app inclusion or we can put specific prefixes here
    tags=["Test Generation"]
)
# Note: In main.py, prefix was /api, and routes were /generate-tests. 
# So if we mount this router with prefix /api, then routes here should be /generate-tests


def _get_owned_project(project_id: int, db: Session, user_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_previous_generation_json(
    db: Session,
    project_id: int,
    user_id: int,
    requirement_text: str,
):
    prev = db.query(TestGeneration).filter(
        TestGeneration.project_id == project_id,
        TestGeneration.requirement_text == requirement_text,
        TestGeneration.user_id == user_id
    ).order_by(TestGeneration.created_at.desc()).first()

    if not prev and len(requirement_text) > 60000:
        prefix = requirement_text[:60000]
        prev = db.query(TestGeneration).filter(
            TestGeneration.project_id == project_id,
            TestGeneration.requirement_text.startswith(prefix),
            TestGeneration.user_id == user_id
        ).order_by(TestGeneration.created_at.desc()).first()

    prev_json = None
    if prev and prev.generated_result:
        try:
            prev_json = json.loads(prev.generated_result)
        except Exception:
            prev_json = {"raw": prev.generated_result}
    return prev_json


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
    _get_owned_project(project_id, db, current_user.id)

    req_text = (requirement or "").strip()
    if not req_text:
        if not file:
            return {"count": 20}
        base_prompt = "OCR: Extract all text from this image."
        proto_prompt = (
            "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
            "Identify input fields, buttons, navigation menus, and any visual indicators of state."
        )
        req_text = await parse_file_content(file, base_prompt)
        if doc_type == "incomplete" and prototype_file is not None:
            proto_text = await parse_file_content(prototype_file, proto_prompt)
            req_text = f"{req_text}\n\n[Prototype Analysis]\n{proto_text}"

    try:
        count = await run_in_threadpool(
            test_generator.estimate_test_count,
            req_text,
            project_id,
            db,
            current_user.id,
        )
        return {"count": max(1, int(count))}
    except Exception as e:
        logger.warning(f"Estimate test count failed: {e}")
        return {"count": 20}


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
    _get_owned_project(project_id, db, current_user.id)

    content = (requirement_text or "").strip()
    uploaded_filename: str | None = None
    if not content:
        if not file:
            return JSONResponse(status_code=400, content={"error": "Missing requirement_text or file"})
        uploaded_filename = file.filename
        base_prompt = "OCR: Extract all text from this image."
        proto_prompt = (
            "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
            "Identify input fields, buttons, navigation menus, and any visual indicators of state."
        )
        content = await parse_file_content(file, base_prompt)
        if doc_type == "incomplete" and prototype_file is not None:
            proto_text = await parse_file_content(prototype_file, proto_prompt)
            content = f"{content}\n\n[Prototype Analysis]\n{proto_text}"

        # 文件模式下保留“重复文档提示”能力，和前端 @@DUPLICATE@@ 协议对齐
        try:
            kb_add = knowledge_base.add_document(
                uploaded_filename or "uploaded_file",
                content,
                doc_type,
                project_id,
                db,
                force=force,
                user_id=current_user.id,
            )
            if isinstance(kb_add, dict) and kb_add.get("status") == "duplicate" and not force:
                previous_json = _get_previous_generation_json(db, project_id, current_user.id, content)
                payload = {
                    "duplicate": True,
                    "filename": kb_add.get("existing_filename"),
                    "previous_json": previous_json,
                }

                def duplicate_stream():
                    yield "@@DUPLICATE@@" + json.dumps(payload, ensure_ascii=False)

                return StreamingResponse(duplicate_stream(), media_type="text/plain; charset=utf-8")
        except Exception:
            # 重复检测失败不阻断生成流程
            pass

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
    同步生成测试用例 (Synchronous Test Generation)
    """
    # Verify project
    project = db.query(Project).filter(Project.id == request.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

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
            # Metrics calculation for this batch
            positive = 0
            negative = 0
            edge = 0
            avg_steps = 0.0
            pending = 0
            steps_count = 0
            steps_items = 0
            kw_neg = ["失败", "错误", "异常", "不可用", "拒绝", "超时"]
            kw_edge = ["边界", "最大值", "最小值", "极限", "临界", "空值", "重复", "特殊字符"]
            if isinstance(result, list):
                for item in result:
                    desc = (item.get("description") or "") + " " + (item.get("expected_result") or "")
                    is_neg = any(k in desc for k in kw_neg)
                    is_edge = any(k in desc for k in kw_edge)
                    if is_neg:
                        negative += 1
                    elif is_edge:
                        edge += 1
                    else:
                        positive += 1
                    steps = item.get("steps")
                    if isinstance(steps, list):
                        steps_count += len(steps)
                        steps_items += 1
                    elif isinstance(steps, str):
                        lines = [s for s in steps.splitlines() if s.strip()]
                        steps_count += len(lines)
                        steps_items += 1
                    if isinstance(item.get("description"), str) and "[Pending Confirmation]" in item.get("description"):
                        pending += 1
            avg_steps = steps_count / steps_items if steps_items else 0.0
            qm = {
                "positive": positive,
                "negative": negative,
                "edge": edge,
                "avg_steps": avg_steps,
                "pending": pending,
                "generated_count": count,
                "batch_index": request.batch_index
            }
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
    project = db.query(Project).filter(Project.id == request.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

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
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        base_prompt = "OCR: Extract all text from this image."
        proto_prompt = (
            "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
            "Identify input fields, buttons, navigation menus, and any visual indicators of state."
        )
        # Parse main document (text or image)
        content = await parse_file_content(file, base_prompt)

        # If incomplete with prototype image, parse prototype and merge content
        if doc_type == "incomplete" and prototype_file is not None:
            proto_text = await parse_file_content(prototype_file, proto_prompt)
            content = f"{content}\n\n[Prototype Analysis]\n{proto_text}"

        # Try to store document into Knowledge Base; if duplicate and not forced, return promptly
        try:
            kb_add = knowledge_base.add_document(file.filename, content, doc_type, project_id, db, force=force, user_id=current_user.id)
            if isinstance(kb_add, dict) and kb_add.get("status") == "duplicate" and not force:
                # Try exact match first
                prev = db.query(TestGeneration).filter(
                    TestGeneration.project_id == project_id,
                    TestGeneration.requirement_text == content,
                    TestGeneration.user_id == current_user.id
                ).order_by(TestGeneration.created_at.desc()).first()
                
                # If exact match fails (e.g. truncated history), try prefix match for long content
                if not prev and len(content) > 60000:
                    prefix = content[:60000]
                    prev = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text.startswith(prefix),
                        TestGeneration.user_id == current_user.id
                    ).order_by(TestGeneration.created_at.desc()).first()

                prev_json = None
                if prev and prev.generated_result:
                    try:
                        prev_json = json.loads(prev.generated_result)
                    except Exception:
                        prev_json = {"raw": prev.generated_result}
                return {
                    "duplicate": True,
                    "filename": kb_add.get("existing_filename"),
                    "previous_json": prev_json
                }
        except Exception:
            pass

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
                positive = 0
                negative = 0
                edge = 0
                avg_steps = 0.0
                pending = 0
                steps_count = 0
                steps_items = 0
                kw_neg = ["失败", "错误", "异常", "不可用", "拒绝", "超时"]
                kw_edge = ["边界", "最大值", "最小值", "极限", "临界", "空值", "重复", "特殊字符"]
                if isinstance(result, list):
                    for item in result:
                        desc = (item.get("description") or "") + " " + (item.get("expected_result") or "")
                        is_neg = any(k in desc for k in kw_neg)
                        is_edge = any(k in desc for k in kw_edge)
                        if is_neg:
                            negative += 1
                        elif is_edge:
                            edge += 1
                        else:
                            positive += 1
                        steps = item.get("steps")
                        if isinstance(steps, list):
                            steps_count += len(steps)
                            steps_items += 1
                        elif isinstance(steps, str):
                            lines = [s for s in steps.splitlines() if s.strip()]
                            steps_count += len(lines)
                            steps_items += 1
                        if isinstance(item.get("description"), str) and "[Pending Confirmation]" in item.get("description"):
                            pending += 1
                avg_steps = steps_count / steps_items if steps_items else 0.0
                qm = {
                    "positive": positive,
                    "negative": negative,
                    "edge": edge,
                    "avg_steps": avg_steps,
                    "pending": pending,
                    "generated_count": count
                }
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
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        base_prompt = "OCR: Extract all text from this image."
        proto_prompt = (
            "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
            "Identify input fields, buttons, navigation menus, and any visual indicators of state."
        )
        # Parse main document (text or image)
        content = await parse_file_content(file, base_prompt)

        # If incomplete with prototype image, parse prototype and merge content
        if doc_type == "incomplete" and prototype_file is not None:
            proto_text = await parse_file_content(prototype_file, proto_prompt)
            content = f"{content}\n\n[Prototype Analysis]\n{proto_text}"

        # Try to store document into Knowledge Base; if duplicate and not forced, return promptly
        try:
            kb_add = knowledge_base.add_document(file.filename, content, doc_type, project_id, db, force=force, user_id=current_user.id)
            if isinstance(kb_add, dict) and kb_add.get("status") == "duplicate" and not force:
                # Try exact match first
                prev = db.query(TestGeneration).filter(
                    TestGeneration.project_id == project_id,
                    TestGeneration.requirement_text == content,
                    TestGeneration.user_id == current_user.id
                ).order_by(TestGeneration.created_at.desc()).first()
                
                # If exact match fails (e.g. truncated history), try prefix match for long content
                if not prev and len(content) > 60000:
                    prefix = content[:60000]
                    prev = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text.startswith(prefix),
                        TestGeneration.user_id == current_user.id
                    ).order_by(TestGeneration.created_at.desc()).first()

                prev_json = None
                if prev and prev.generated_result:
                    try:
                        prev_json = json.loads(prev.generated_result)
                    except Exception:
                        prev_json = {"raw": prev.generated_result}
                return {
                    "duplicate": True,
                    "filename": kb_add.get("existing_filename"),
                    "previous_json": prev_json
                }
        except Exception:
            pass

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
        project = db.query(Project).filter(Project.id == request.project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

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
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from fastapi.concurrency import run_in_threadpool
    try:
        base_prompt = "OCR: Extract all text from this image."
        proto_prompt = (
            "Analyze this UI prototype image. Describe every UI element, their layout, text content, and likely interactions. "
            "Identify input fields, buttons, navigation menus, and any visual indicators of state."
        )
        content = await parse_file_content(file, base_prompt)
        if doc_type == "incomplete" and prototype_file is not None:
            proto_text = await parse_file_content(prototype_file, proto_prompt)
            content = f"{content}\n\n[Prototype Analysis]\n{proto_text}"
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
