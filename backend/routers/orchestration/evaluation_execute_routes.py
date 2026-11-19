from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import AgentRun, User
from core.processing.file_processing import is_image_filename, parse_file_bytes, parse_image_bytes_with_fallback
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.automation_components.services.test_case_file_parser import evaluation_spreadsheet_reference
from modules.agent_platform.automation_evaluation import execute_automation_evaluation
from modules.agent_platform.serialization import serialize_run
from modules.agent_platform.test_case_evaluation import EvaluationAlreadyRunningError, create_test_case_evaluation_run
from modules.agent_platform.test_case_evaluation_feedback import evaluation_source_inputs
from modules.knowledge_base_components.document.offline_parse_support import validate_parsed_content
from modules.orchestration.context_orchestrator import context_orchestrator
from routers.orchestration.evaluation_shared import get_owned_project, is_attachment_ocr_ok
from schemas.automation.api_testing import APITestEvalRequest
from schemas.automation.ui_automation import UIAutoEvalRequest

router = APIRouter()


@router.post("/evaluate-test-cases", status_code=202)
async def evaluate_test_cases(
    reference_content: str = Form(""),
    baseline_groups: str = Form("[]"),
    project_id: int = Form(...),
    run_id: int = Form(...),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)
    if (reference_content or "").strip() and file is not None:
        raise HTTPException(status_code=422, detail="人工最终用例只能选择填写文本或上传文件中的一种")

    try:
        groups = json.loads(baseline_groups)
        if (not isinstance(groups, list) or any(not isinstance(g, str) or not g.strip() for g in groups)
                or len(set(groups)) != len(groups)):
            raise ValueError
    except ValueError as error:
        raise HTTPException(status_code=422, detail="评测基线应为无重复的正文要求数组") from error

    source_run = (
        db.query(AgentRun)
        .filter(
            AgentRun.id == run_id,
            AgentRun.project_id == project_id,
            AgentRun.user_id == current_user.id,
        )
        .first()
    )
    if source_run is None:
        raise HTTPException(status_code=404, detail="Agent Run 不存在")
    try:
        requirement_text, generated_cases, _ = evaluation_source_inputs(source_run)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    final_reference = (reference_content or "").strip()
    upload_persist: dict[str, Any] = {
        "filename": "",
        "content_type": "",
        "size": 0,
        "ocr_source": "not_image",
        "ocr_ok": False,
        "cloud_fallback": False,
        "ocr_error": "",
    }
    if not final_reference and file is not None:
        filename = file.filename or ""
        raw_bytes = await file.read()
        upload_persist.update(
            filename=filename,
            content_type=file.content_type or "",
            size=len(raw_bytes),
        )
        if is_image_filename(filename):
            parsed, ocr_meta = parse_image_bytes_with_fallback(
                filename=filename,
                content_bytes=raw_bytes,
                db=db,
                user_id=current_user.id,
            )
            final_reference = parsed
            upload_persist["ocr_source"] = ocr_meta.get("ocr_source", "unknown")
            upload_persist["cloud_fallback"] = bool(ocr_meta.get("cloud_fallback", False))
            upload_persist["ocr_error"] = ocr_meta.get("error", "") or ocr_meta.get("local_ocr_error", "")
            if upload_persist["ocr_source"] not in {"local", "cloud"} or not is_attachment_ocr_ok(final_reference):
                raise HTTPException(status_code=422, detail="图片未识别到可用的人工用例，请重新上传或填写文本")
            upload_persist["ocr_ok"] = True
        elif filename.lower().endswith((".xls", ".xlsx")):
            try:
                final_reference = evaluation_spreadsheet_reference(filename, raw_bytes)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        else:
            final_reference = parse_file_bytes(
                filename=filename,
                content_bytes=raw_bytes,
                db=db,
                user_id=current_user.id,
            )
        try:
            validate_parsed_content(final_reference)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    if not final_reference:
        raise HTTPException(status_code=400, detail="请填写人工最终用例或上传文件")

    try:
        evaluation_run = create_test_case_evaluation_run(
            db=db,
            project_id=project_id,
            user_id=current_user.id,
            source_run_id=run_id,
            requirement=requirement_text,
            generated_cases=generated_cases,
            reference_content=final_reference,
            upload=upload_persist,
            baseline_groups=groups,
        )
    except EvaluationAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"用例 Agent 评测失败：{exc}") from exc

    log_workflow_trace(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        kind=WorkflowKind.EVALUATION,
        stage=WorkflowStage.EVALUATE,
        details={
            "action": "evaluate_test_cases",
            "source_run_id": run_id,
            "evaluation_run_id": evaluation_run.id,
        },
    )
    return {"run": serialize_run(evaluation_run)}


@router.post("/evaluate-ui-automation")
def evaluate_ui_automation(
    req: UIAutoEvalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(req.project_id, db, current_user.id)
    context_bundle = context_orchestrator.assemble_context(
        WorkflowKind.EVALUATION,
        req.project_id,
        db,
        user_id=current_user.id,
        query_text=req.script[:500],
        requirement_text=req.execution_result[:2000],
        include_knowledge=True,
        include_logs=True,
        knowledge_limit=3,
        log_limit=12,
    )
    log_workflow_trace(
        db,
        req.project_id,
        current_user.id,
        WorkflowKind.EVALUATION,
        WorkflowStage.EVALUATE,
        {"action": "evaluate_ui_automation", **context_bundle["diagnostics"]},
    )
    run, artifact = execute_automation_evaluation(
        db=db,
        project_id=req.project_id,
        user_id=current_user.id,
        evaluation_type="ui",
        script=req.script,
        execution_result=req.execution_result,
        project_context=context_bundle["combined_context"],
        user_journey=req.journey_json,
    )
    return {
        "result": artifact["result"],
        "run_id": run.id,
        "status": run.status,
        "artifact": artifact,
        "context_diagnostics": context_bundle["diagnostics"],
    }


@router.post("/evaluate-api-test")
def evaluate_api_test(
    req: APITestEvalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(req.project_id, db, current_user.id)
    context_bundle = context_orchestrator.assemble_context(
        WorkflowKind.EVALUATION,
        req.project_id,
        db,
        user_id=current_user.id,
        query_text=req.script[:500],
        requirement_text=req.execution_result[:2000],
        include_knowledge=True,
        include_interfaces=True,
        include_logs=True,
        knowledge_limit=2,
        interface_limit=12,
        log_limit=12,
    )
    effective_spec = req.openapi_spec or context_bundle["interface_context"]
    log_workflow_trace(
        db,
        req.project_id,
        current_user.id,
        WorkflowKind.EVALUATION,
        WorkflowStage.EVALUATE,
        {
            "action": "evaluate_api_test",
            "used_openapi_fallback": not bool(req.openapi_spec),
            **context_bundle["diagnostics"],
        },
    )
    run, artifact = execute_automation_evaluation(
        db=db,
        project_id=req.project_id,
        user_id=current_user.id,
        evaluation_type="api",
        script=req.script,
        execution_result=req.execution_result,
        project_context=context_bundle["combined_context"],
        openapi_spec=effective_spec,
    )
    return {
        "result": artifact["result"],
        "run_id": run.id,
        "status": run.status,
        "artifact": artifact,
        "context_diagnostics": context_bundle["diagnostics"],
    }
