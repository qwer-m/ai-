from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session, defer

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import AgentRun, AgentWorkflowDefinition, KnowledgeDocument, User
from core.processing.file_processing import is_image_filename, parse_file_bytes, parse_image_bytes_with_fallback
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.agent_platform.evaluation_spreadsheet import evaluation_spreadsheet_reference
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sources import SourceSnapshot, latest_successful_run_for_source, persisted_source_snapshot
from modules.agent_platform.serialization import serialize_run
from modules.agent_platform.test_case_evaluation import EvaluationAlreadyRunningError, create_test_case_evaluation_run
from modules.agent_platform.test_case_evaluation_feedback import evaluation_source_inputs
from modules.domain.knowledge_base import knowledge_base
from modules.knowledge_base_components.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from modules.knowledge_base_components.document.offline_parse_support import validate_parsed_content
from routers.system.common_responses import build_upload_knowledge_response
from routers.orchestration.evaluation_shared import get_owned_project, is_attachment_ocr_ok

router = APIRouter()


def _document_source(db: Session, project_id: int, document_id: int) -> SourceSnapshot:
    document = KnowledgeDocumentRepository(db).get_project_doc(project_id=project_id, doc_id=document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="当前项目中没有这份需求文档")
    try:
        return SourceSnapshot.from_document(document)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _generation_run(db: Session, project_id: int, user_id: int, run_id: int) -> AgentRun:
    run = db.query(AgentRun).join(AgentWorkflowDefinition).filter(
        AgentRun.id == run_id, AgentRun.project_id == project_id, AgentRun.user_id == user_id,
        AgentWorkflowDefinition.workflow_key == "test_generation",
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="无效 Run ID：当前项目中没有这次生成运行")
    return run


def _evaluation_inputs(run: AgentRun, source: SourceSnapshot | None) -> tuple[str, list[dict[str, Any]]]:
    try:
        requirement, cases, snapshot = evaluation_source_inputs(run)
        if source is not None and source.document_id != snapshot.document_id:
            raise ValueError("该生成运行属于其他需求文档")
        if source is not None and source.content_hash != snapshot.content_hash:
            raise ValueError("该生成运行使用了这份需求文档的其他版本")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=f"无效 Run ID：{error}") from error
    return requirement, cases


@router.post("/evaluation/requirement")
async def upload_evaluation_requirement(
    project_id: int = Form(...), file: UploadFile = File(...),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)
    digest = hashlib.sha256()
    while chunk := await file.read(1024 * 1024):
        digest.update(chunk)
    documents = db.query(KnowledgeDocument).options(defer(KnowledgeDocument.content)).filter(
        KnowledgeDocument.project_id == project_id, KnowledgeDocument.parse_status == "success",
        KnowledgeDocument.doc_type.in_(SourceSnapshot.DOCUMENT_TYPES),
        KnowledgeDocument.content_hash == digest.hexdigest(),
    ).order_by(KnowledgeDocument.id.desc()).all()
    if documents:
        document = documents[0]
        if len(documents) > 1:
            by_id = {doc.id: doc for doc in documents}
            latest = latest_successful_run_for_source(
                AgentRunRepository(db), project_id=project_id, user_id=current_user.id,
                workflow_key="test_generation", source=SourceSnapshot.from_document(document), document_ids=set(by_id),
            )
            if latest:
                document = by_id[persisted_source_snapshot(latest).document_id]
        return build_upload_knowledge_response(document, {})
    await file.seek(0)
    result = await knowledge_base.enqueue_document_for_offline_parse(
        file=file, project_id=project_id, doc_type="requirement", db=db, user_id=current_user.id,
    )
    return build_upload_knowledge_response(result["document"], result)


@router.get("/evaluation/source")
def get_evaluation_source(
    project_id: int, requirement_doc_id: int, run_id: int | None = None,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)
    source = _document_source(db, project_id, requirement_doc_id)
    if run_id is None:
        latest = latest_successful_run_for_source(
            AgentRunRepository(db), project_id=project_id, user_id=current_user.id,
            workflow_key="test_generation", source=source, document_ids={requirement_doc_id},
        )
        if latest is None:
            return {"source": None}
        run = latest
    else:
        run = _generation_run(db, project_id, current_user.id, run_id)
    requirement, cases = _evaluation_inputs(run, source)
    evaluation = (run.run_context or {}).get("artifacts", {}).get("test_evaluation")
    return {"source": {
        "project_id": project_id, "document_id": source.document_id, "run_id": run.id,
        "requirement": requirement, "cases": cases,
        "evaluation": {key: evaluation[key] for key in (
            "source_run_id", "evaluation_run_id", "project_id", "upload", "reference_content", "evaluation",
        )} if evaluation else None,
    }}


@router.post("/evaluate-test-cases", status_code=202)
async def evaluate_test_cases(
    reference_content: str = Form(""),
    baseline_groups: str = Form("[]"),
    project_id: int = Form(...),
    run_id: int = Form(...),
    requirement_doc_id: int | None = Form(None),
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

    source = _document_source(db, project_id, requirement_doc_id) if requirement_doc_id is not None else None
    run = _generation_run(db, project_id, current_user.id, run_id)
    requirement_text, generated_cases = _evaluation_inputs(run, source)
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
