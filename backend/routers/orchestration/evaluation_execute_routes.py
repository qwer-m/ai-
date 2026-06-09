from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from core.processing.file_processing import is_image_filename, parse_file_bytes, parse_image_bytes_with_fallback
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.orchestration.context_orchestrator import context_orchestrator
from modules.test_generation_components.repositories.history_repository import TestGenerationHistoryRepository
from modules.testing.evaluation import evaluator
from modules.testing.evaluation_artifact_store import upsert_compare_artifact
from routers.orchestration.evaluation_shared import get_owned_project, is_attachment_ocr_ok
from schemas.automation.api_testing import APITestEvalRequest
from schemas.automation.ui_automation import UIAutoEvalRequest

router = APIRouter()


@router.post("/compare-test-cases")
async def compare_test_cases(
    generated_test_case: str = Form(...),
    modified_test_case: str = Form(""),
    project_id: int = Form(...),
    generation_id: Optional[int] = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)

    final_modified = (modified_test_case or "").strip()
    upload_persist: dict[str, Any] = {
        "from_upload": False,
        "filename": "",
        "content_type": "",
        "size": 0,
        "ocr_source": "not_image",
        "ocr_ok": False,
        "cloud_fallback": False,
        "ocr_error": "",
    }
    if not final_modified and file is not None:
        filename = file.filename or ""
        raw_bytes = await file.read()
        upload_persist["from_upload"] = True
        upload_persist["filename"] = filename
        upload_persist["content_type"] = file.content_type or ""
        upload_persist["size"] = len(raw_bytes)

        if is_image_filename(filename):
            parsed, ocr_meta = parse_image_bytes_with_fallback(
                filename=filename,
                content_bytes=raw_bytes,
                db=db,
                user_id=current_user.id,
            )
            final_modified = parsed
            upload_persist["ocr_source"] = ocr_meta.get("ocr_source", "unknown")
            upload_persist["cloud_fallback"] = bool(ocr_meta.get("cloud_fallback", False))
            upload_persist["ocr_error"] = ocr_meta.get("error", "") or ocr_meta.get("local_ocr_error", "")
        else:
            final_modified = parse_file_bytes(
                filename=filename,
                content_bytes=raw_bytes,
                db=db,
                user_id=current_user.id,
            )
        upload_persist["ocr_ok"] = is_attachment_ocr_ok(final_modified)
    if not final_modified:
        raise HTTPException(status_code=400, detail="Missing modified_test_case or file")

    requirement_text = ""
    if generation_id is not None:
        generation = TestGenerationHistoryRepository(db).get_generation(generation_id=generation_id)
        if (
            generation is not None
            and generation.project_id == project_id
            and generation.user_id == current_user.id
        ):
            requirement_text = generation.requirement_text or ""

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
        requirement_text=requirement_text,
    )
    artifact_saved = False
    artifact_doc_id: Optional[int] = None
    if generation_id is not None:
        try:
            artifact_payload: dict[str, Any] = {
                "project_id": project_id,
                "source_filename": upload_persist["filename"],
                "source_file_content_type": upload_persist["content_type"],
                "source_file_size": upload_persist["size"],
                "modified_test_case": final_modified,
                "requirement_text": requirement_text,
                "comparison_result": result,
                "ocr": {
                    "source": upload_persist["ocr_source"],
                    "ok": upload_persist["ocr_ok"],
                    "cloud_fallback": upload_persist["cloud_fallback"],
                    "error": upload_persist["ocr_error"],
                },
            }
            doc = upsert_compare_artifact(
                db=db,
                project_id=project_id,
                user_id=current_user.id,
                generation_id=generation_id,
                payload=artifact_payload,
            )
            artifact_saved = True
            artifact_doc_id = doc.id
        except Exception:
            artifact_saved = False
            artifact_doc_id = None

    return {
        "result": result,
        "context_diagnostics": context_bundle["diagnostics"],
        "persistence": {
            "generation_id": generation_id,
            "artifact_saved": artifact_saved,
            "artifact_doc_id": artifact_doc_id,
            "source_filename": upload_persist["filename"],
            "ocr_ok": upload_persist["ocr_ok"],
            "ocr_source": upload_persist["ocr_source"],
        },
    }


@router.post("/evaluate-ui-automation")
def evaluate_ui_automation(
    req: UIAutoEvalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(req.project_id, db, current_user.id)

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

    result = evaluator.evaluate_api_test(
        req.script,
        req.execution_result,
        db=db,
        project_id=req.project_id,
        user_id=current_user.id,
        openapi_spec=effective_spec,
    )
    return {"result": result, "context_diagnostics": context_bundle["diagnostics"]}
