from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_
import json
import os
import re
from typing import Any

from core.database import get_db
from core.models import Project, User, TestGeneration, LogEntry
from core.auth import get_current_user
from core.utils import log_to_db, logger
from core.file_processing import parse_file_content
from core.config import settings
from core.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from schemas.test_generation import TestGenRequest

from modules.context_orchestrator import context_orchestrator
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
    prev = _get_previous_generation_record(db, project_id, user_id, requirement_text)
    prev_json = None
    if prev and prev.generated_result:
        try:
            prev_json = json.loads(prev.generated_result)
        except Exception:
            prev_json = {"raw": prev.generated_result}
    return prev_json


def _get_previous_generation_record(
    db: Session,
    project_id: int,
    user_id: int,
    requirement_text: str,
) -> TestGeneration | None:
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

    return prev


def _parse_generation_result(generated_result: str | None) -> Any:
    if not generated_result:
        return []

    try:
        return json.loads(generated_result)
    except Exception:
        return {"raw": generated_result}


def _get_owned_generation(generation_id: int, db: Session, user_id: int) -> TestGeneration:
    generation = db.query(TestGeneration).filter(TestGeneration.id == generation_id).first()
    if not generation:
        raise HTTPException(status_code=404, detail="Test generation not found")

    if generation.user_id is not None and generation.user_id != user_id:
        raise HTTPException(status_code=404, detail="Test generation not found")

    if generation.project_id is not None:
        _get_owned_project(generation.project_id, db, user_id)

    return generation


def _serialize_generation_summary(generation: TestGeneration) -> dict[str, Any]:
    parsed = _parse_generation_result(generation.generated_result)
    case_count = len(parsed) if isinstance(parsed, list) else 0
    return {
        "id": generation.id,
        "project_id": generation.project_id,
        "requirement_text": generation.requirement_text,
        "created_at": generation.created_at.isoformat() if generation.created_at else None,
        "case_count": case_count,
    }


def _parse_gen_diag_message(message: str | None) -> dict[str, Any] | None:
    if not message or "GEN_DIAG:" not in message:
        return None

    raw = message.split("GEN_DIAG:", 1)[1].strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        return None

    return parsed if isinstance(parsed, dict) else None


def _find_execution_suite_payload(db: Session, generation: TestGeneration) -> dict[str, Any] | None:
    generation_id = int(generation.id)
    id_with_space = f'%"generation_id": {generation_id}%'
    id_without_space = f'%"generation_id":{generation_id}%'

    query = db.query(LogEntry).filter(
        LogEntry.project_id == generation.project_id,
        LogEntry.message.like("%generation_execution_suite%"),
        or_(
            LogEntry.message.like(id_with_space),
            LogEntry.message.like(id_without_space),
        ),
    )
    if generation.user_id is not None:
        query = query.filter(or_(LogEntry.user_id == generation.user_id, LogEntry.user_id.is_(None)))

    logs = query.order_by(desc(LogEntry.created_at), desc(LogEntry.id)).limit(20).all()
    for log in logs:
        payload = _parse_gen_diag_message(log.message)
        if not payload or payload.get("kind") != "generation_execution_suite":
            continue
        if int(payload.get("generation_id") or 0) == generation_id:
            payload["_log_id"] = log.id
            payload["_log_created_at"] = log.created_at.isoformat() if log.created_at else None
            return payload

    return None


def _extract_compact_execution_suite(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None

    for key in ("execution_suite_compact", "execution_suite"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    if payload.get("kind") == "execution_suite":
        return payload

    return None


def _case_id_number(case_id: str) -> int | None:
    match = re.search(r"(\d+)", case_id or "")
    return int(match.group(1)) if match else None


def _build_public_case_lookup(cases: Any) -> tuple[dict[str, dict[str, Any]], bool]:
    if not isinstance(cases, list):
        return {}, True

    lookup: dict[str, dict[str, Any]] = {}
    ordered_numbers: list[int] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id") or case.get("ID") or "").strip()
        if not case_id:
            continue
        number = _case_id_number(case_id)
        if number is not None:
            ordered_numbers.append(number)
        lookup[case_id] = {
            "public_order": index + 1,
            "description": case.get("description") or "",
            "test_module": case.get("test_module") or "",
            "priority": case.get("priority_final") or case.get("priority") or "",
            "expected_result": case.get("expected_result") or "",
        }

    monotonic = all(ordered_numbers[i] <= ordered_numbers[i + 1] for i in range(len(ordered_numbers) - 1))
    return lookup, monotonic


def _merge_execution_case(case_ref: dict[str, Any], public_cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    case_id = str(case_ref.get("case_id") or case_ref.get("id") or "").strip()
    public = public_cases.get(case_id, {})
    depends_on = case_ref.get("depends_on") or []
    if not isinstance(depends_on, list):
        depends_on = [str(depends_on)]

    return {
        "case_id": case_id,
        "suite_order": case_ref.get("suite_order"),
        "execution_sequence": case_ref.get("execution_sequence"),
        "depends_on": depends_on,
        "role": case_ref.get("role") or "",
        "session_key": case_ref.get("session_key") or "",
        "fixture_key": case_ref.get("fixture_key") or "",
        "source_state": case_ref.get("source_state") or "",
        "target_state": case_ref.get("target_state") or "",
        "action": case_ref.get("action") or "",
        "setup_hint": case_ref.get("setup_hint") or "",
        "teardown_hint": case_ref.get("teardown_hint") or "",
        "runnable": case_ref.get("runnable", True),
        **public,
    }


def _fallback_execution_suites(cases: Any, public_cases: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(cases, list):
        return []

    fallback_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id") or case.get("ID") or f"CASE-{index + 1}").strip()
        fallback_cases.append(_merge_execution_case({
            "case_id": case_id,
            "suite_order": index + 1,
            "execution_sequence": index + 1,
            "depends_on": [],
            "runnable": True,
        }, public_cases))

    return [{
        "suite_id": "public_result_order",
        "suite_name": "公开结果顺序",
        "execution_group": "public_result",
        "run_mode": "listed",
        "case_count": len(fallback_cases),
        "runnable": False,
        "warnings": ["未找到执行套件诊断，仅展示公开结果中的列表顺序。"],
        "cases": fallback_cases,
    }]


def _build_execution_order_response(generation: TestGeneration, db: Session) -> dict[str, Any]:
    public_result = _parse_generation_result(generation.generated_result)
    public_lookup, public_id_order_monotonic = _build_public_case_lookup(public_result)
    payload = _find_execution_suite_payload(db, generation)
    suite = _extract_compact_execution_suite(payload)
    notes: list[str] = []

    suites: list[dict[str, Any]] = []
    if suite and isinstance(suite.get("suites"), list):
        for item in suite.get("suites") or []:
            if not isinstance(item, dict):
                continue
            cases = [
                _merge_execution_case(case_ref, public_lookup)
                for case_ref in (item.get("cases") or [])
                if isinstance(case_ref, dict)
            ]
            suites.append({
                "suite_id": item.get("suite_id") or "",
                "suite_name": item.get("suite_name") or "",
                "execution_group": item.get("execution_group") or "",
                "run_mode": item.get("run_mode") or "",
                "group_setup": item.get("group_setup") or "",
                "group_teardown": item.get("group_teardown") or "",
                "case_count": item.get("case_count") or len(cases),
                "runnable": item.get("runnable", True),
                "warnings": item.get("warnings") or [],
                "cases": cases,
            })
        source = "gen_diag"
        if payload and payload.get("execution_suite_omitted_due_to_size"):
            notes.append("执行套件全量字段因日志长度限制被压缩，当前视图使用 compact case refs。")
    else:
        suites = _fallback_execution_suites(public_result, public_lookup)
        source = "generated_result_fallback"
        notes.append("未找到 generation_execution_suite 诊断，无法还原真实依赖关系。")

    if not public_id_order_monotonic:
        notes.append("公开结果中的用例编号不是单调递增，阅读执行顺序应以本视图的 suite_order/execution_sequence 为准。")

    public_count = len(public_result) if isinstance(public_result, list) else 0
    return {
        "generation_id": generation.id,
        "project_id": generation.project_id,
        "created_at": generation.created_at.isoformat() if generation.created_at else None,
        "source": source,
        "diagnostic_log_id": payload.get("_log_id") if payload else None,
        "request_id": payload.get("request_id") if payload else None,
        "case_count": (suite or payload or {}).get("case_count") or public_count,
        "public_case_count": public_count,
        "suite_count": (suite or {}).get("suite_count") or len(suites),
        "runnable_suite_count": (suite or {}).get("runnable_suite_count"),
        "linear_executable": (suite or {}).get("linear_executable"),
        "execution_readiness": (suite or payload or {}).get("execution_readiness") or "legacy_manual",
        "main_suite_id": (suite or {}).get("main_suite_id"),
        "public_id_order_monotonic": public_id_order_monotonic,
        "notes": notes,
        "suites": suites,
    }


@router.get("/test-generations")
def list_test_generations(
    project_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)
    safe_limit = max(1, min(int(limit or 20), 100))
    generations = (
        db.query(TestGeneration)
        .filter(
            TestGeneration.project_id == project_id,
            or_(TestGeneration.user_id == current_user.id, TestGeneration.user_id.is_(None)),
        )
        .order_by(desc(TestGeneration.created_at), desc(TestGeneration.id))
        .limit(safe_limit)
        .all()
    )
    return [_serialize_generation_summary(item) for item in generations]


@router.get("/test-generations/latest/execution-order")
def get_latest_execution_order(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)
    generation = (
        db.query(TestGeneration)
        .filter(
            TestGeneration.project_id == project_id,
            or_(TestGeneration.user_id == current_user.id, TestGeneration.user_id.is_(None)),
        )
        .order_by(desc(TestGeneration.created_at), desc(TestGeneration.id))
        .first()
    )
    if not generation:
        raise HTTPException(status_code=404, detail="Test generation not found")
    return _build_execution_order_response(generation, db)


@router.get("/test-generations/{generation_id}/execution-order")
def get_execution_order(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    generation = _get_owned_generation(generation_id, db, current_user.id)
    return _build_execution_order_response(generation, db)


@router.get("/test-generations/{generation_id}")
def get_test_generation(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    generation = _get_owned_generation(generation_id, db, current_user.id)
    return _parse_generation_result(generation.generated_result)


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
                previous = _get_previous_generation_record(db, project_id, current_user.id, content)
                previous_json = _parse_generation_result(previous.generated_result) if previous else None
                payload = {
                    "duplicate": True,
                    "id": previous.id if previous else None,
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
    鍚屾鐢熸垚娴嬭瘯鐢ㄤ緥 (Synchronous Test Generation)
    """
    # Verify project
    project = db.query(Project).filter(Project.id == request.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

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
                    "id": prev.id if prev else None,
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
                    "id": prev.id if prev else None,
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

