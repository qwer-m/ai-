import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.auth import get_current_user
from core.database import get_db
from core.file_processing import parse_file_content
from core.models import Evaluation, KnowledgeDocument, Project, TestGenerationComparison, User
from core.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.context_orchestrator import context_orchestrator
from modules.evaluation import evaluator
from schemas.api_testing import APITestEvalRequest
from schemas.ui_automation import UIAutoEvalRequest

router = APIRouter(tags=["Evaluation"])


def _get_owned_project(project_id: int, db: Session, user_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/compare-test-cases")
async def compare_test_cases(
    generated_test_case: str = Form(...),
    modified_test_case: str = Form(""),
    project_id: int = Form(...),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)

    final_modified = (modified_test_case or "").strip()
    if not final_modified and file is not None:
        final_modified = await parse_file_content(file)
    if not final_modified:
        raise HTTPException(status_code=400, detail="Missing modified_test_case or file")

    context_bundle = context_orchestrator.assemble_context(
        WorkflowKind.EVALUATION,
        project_id,
        db,
        user_id=current_user.id,
        requirement_text=generated_test_case,
        include_knowledge=True,
        include_logs=True,
        knowledge_limit=3,
        log_limit=8,
    )
    log_workflow_trace(
        db,
        project_id,
        current_user.id,
        WorkflowKind.EVALUATION,
        WorkflowStage.CONTEXT,
        {"action": "compare_test_cases", **context_bundle["diagnostics"]},
    )

    result = evaluator.compare_test_cases(
        generated_test_case,
        final_modified,
        db=db,
        project_id=project_id,
        user_id=current_user.id,
    )
    return {"result": result, "context_diagnostics": context_bundle["diagnostics"]}


@router.post("/evaluate-ui-automation")
def evaluate_ui_automation(
    req: UIAutoEvalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(req.project_id, db, current_user.id)

    journey_json: Optional[dict[str, Any]] = None
    if req.journey_json:
        if isinstance(req.journey_json, str):
            try:
                journey_json = json.loads(req.journey_json)
            except Exception:
                journey_json = {"raw": req.journey_json}
        else:
            journey_json = req.journey_json

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

    result = evaluator.evaluate_ui_automation(
        req.script,
        req.execution_result,
        db=db,
        project_id=req.project_id,
        user_id=current_user.id,
        journey_json=journey_json,
    )
    return {"result": result, "context_diagnostics": context_bundle["diagnostics"]}


@router.post("/evaluate-api-test")
def evaluate_api_test(
    req: APITestEvalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(req.project_id, db, current_user.id)

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

    result = evaluator.evaluate_api_test(
        req.script,
        req.execution_result,
        db=db,
        project_id=req.project_id,
        user_id=current_user.id,
        openapi_spec=effective_spec,
    )
    return {"result": result, "context_diagnostics": context_bundle["diagnostics"]}


@router.get("/evaluation/history/{project_id}")
def get_evaluation_history(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)

    eval_items = (
        db.query(Evaluation)
        .filter(Evaluation.project_id == project_id, Evaluation.user_id == current_user.id)
        .order_by(desc(Evaluation.created_at), desc(Evaluation.id))
        .limit(30)
        .all()
    )
    compare_items = (
        db.query(TestGenerationComparison)
        .filter(
            TestGenerationComparison.project_id == project_id,
            TestGenerationComparison.user_id == current_user.id,
        )
        .order_by(desc(TestGenerationComparison.created_at), desc(TestGenerationComparison.id))
        .limit(30)
        .all()
    )

    history = [
        {
            "id": f"eval-{item.id}",
            "type": "evaluation",
            "created_at": item.created_at,
            "preview": (item.evaluation_result or "")[:200],
        }
        for item in eval_items
    ] + [
        {
            "id": f"compare-{item.id}",
            "type": "comparison",
            "created_at": item.created_at,
            "preview": (item.comparison_result or "")[:200],
        }
        for item in compare_items
    ]
    history.sort(key=lambda x: x["created_at"] or datetime.min, reverse=True)
    return {"history": history[:50]}


@router.get("/evaluation/latest-supplement/{project_id}")
def get_latest_supplement(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)

    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.user_id == current_user.id,
            KnowledgeDocument.doc_type == "evaluation_report",
        )
        .order_by(desc(KnowledgeDocument.created_at), desc(KnowledgeDocument.id))
        .first()
    )
    if not doc:
        return {"found": False}
    return {"found": True, "doc_id": doc.id, "supplement": doc.content or ""}


@router.post("/evaluation/save-knowledge")
async def save_evaluation_knowledge(
    project_id: int = Form(...),
    defect_analysis: str = Form(""),
    user_supplement: str = Form(""),
    doc_id: Optional[int] = Form(None),
    files: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)

    attachments: list[str] = []
    for upload in files or []:
        parsed = await parse_file_content(upload)
        attachments.append(f"## Attachment: {upload.filename}\n{parsed}")

    sections = [
        "# Evaluation Knowledge",
        "## Defect Analysis",
        defect_analysis or "(empty)",
        "## User Supplement",
        user_supplement or "(empty)",
    ]
    if attachments:
        sections.append("## Attachments")
        sections.extend(attachments)
    content = "\n\n".join(sections)
    filename = f"evaluation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    if doc_id:
        doc = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id == doc_id,
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.user_id == current_user.id,
        ).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Knowledge document not found")
        doc.filename = filename
        doc.content = content
        doc.doc_type = "evaluation_report"
        db.commit()
        db.refresh(doc)
    else:
        from modules.knowledge_base import knowledge_base

        created = knowledge_base.add_document(
            filename,
            content,
            "evaluation_report",
            project_id,
            db,
            force=False,
            user_id=current_user.id,
        )
        if isinstance(created, dict):
            raise HTTPException(status_code=409, detail=created)
        doc = created

    log_workflow_trace(
        db,
        project_id,
        current_user.id,
        WorkflowKind.EVALUATION,
        WorkflowStage.LEARN,
        {
            "action": "save_evaluation_knowledge",
            "doc_id": doc.id,
            "attachments": len(attachments),
            "content_length": len(content),
        },
    )
    return {"success": True, "result": {"id": doc.id, "filename": doc.filename}}
