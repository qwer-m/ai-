from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import SessionLocal, get_db
from core.db.models import TestGenerationComparison, User
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


def _parse_compare_status(result: str) -> str:
    try:
        payload = json.loads(result or "{}")
    except Exception:
        return ""
    return str(payload.get("analysis_status") or "")


def _persist_compare_result(
    *,
    comparison_id: int,
    project_id: int,
    user_id: int,
    result: str | dict[str, Any],
) -> None:
    result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
    progress_db = SessionLocal()
    try:
        row = (
            progress_db.query(TestGenerationComparison)
            .filter(
                TestGenerationComparison.id == comparison_id,
                TestGenerationComparison.project_id == project_id,
                TestGenerationComparison.user_id == user_id,
            )
            .first()
        )
        if row is not None:
            row.comparison_result = result_text
            progress_db.commit()
    except Exception:
        progress_db.rollback()
    finally:
        progress_db.close()


def _run_compare_background(
    *,
    comparison_id: int,
    generated_test_case: str,
    modified_test_case: str,
    project_id: int,
    user_id: int,
    generation_id: Optional[int],
    requirement_text: str,
    upload_persist: dict[str, Any],
) -> None:
    db = SessionLocal()
    try:
        def progress_callback(payload: dict[str, Any]) -> None:
            _persist_compare_result(
                comparison_id=comparison_id,
                project_id=project_id,
                user_id=user_id,
                result=payload,
            )

        try:
            context_bundle = context_orchestrator.assemble_context(
                WorkflowKind.EVALUATION,
                project_id,
                db,
                user_id=user_id,
                requirement_text=generated_test_case,
                include_knowledge=True,
                include_logs=True,
                knowledge_limit=3,
                log_limit=8,
            )
            log_workflow_trace(
                db,
                project_id,
                user_id,
                WorkflowKind.EVALUATION,
                WorkflowStage.CONTEXT,
                {"action": "compare_test_cases_background", **context_bundle["diagnostics"]},
            )
        except Exception:
            db.rollback()

        try:
            result = evaluator.compare_test_cases(
                generated_test_case,
                modified_test_case,
                db=db,
                project_id=project_id,
                user_id=user_id,
                requirement_text=requirement_text,
                persist_result=False,
                progress_callback=progress_callback,
                comparison_id=comparison_id,
            )
        except Exception as e:
            result = evaluator.build_background_exception_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                fallback_reason=f"后台模型评测异常：{e}",
                comparison_id=comparison_id,
            )

        _persist_compare_result(
            comparison_id=comparison_id,
            project_id=project_id,
            user_id=user_id,
            result=result,
        )

        if generation_id is not None:
            try:
                artifact_payload: dict[str, Any] = {
                    "project_id": project_id,
                    "source_filename": upload_persist.get("filename", ""),
                    "source_file_content_type": upload_persist.get("content_type", ""),
                    "source_file_size": upload_persist.get("size", 0),
                    "modified_test_case": modified_test_case,
                    "requirement_text": requirement_text,
                    "comparison_result": result,
                    "ocr": {
                        "source": upload_persist.get("ocr_source", "unknown"),
                        "ok": bool(upload_persist.get("ocr_ok", False)),
                        "cloud_fallback": bool(upload_persist.get("cloud_fallback", False)),
                        "error": upload_persist.get("ocr_error", ""),
                    },
                }
                upsert_compare_artifact(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    generation_id=generation_id,
                    payload=artifact_payload,
                )
            except Exception:
                db.rollback()
    finally:
        db.close()


@router.post("/compare-test-cases")
async def compare_test_cases(
    background_tasks: BackgroundTasks,
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

    if evaluator.should_run_compare_in_background(generated_test_case, final_modified):
        db_entry = TestGenerationComparison(
            project_id=project_id,
            generated_test_case=generated_test_case,
            modified_test_case=final_modified,
            comparison_result="",
            user_id=current_user.id,
        )
        db.add(db_entry)
        db.commit()
        db.refresh(db_entry)
        running_result = evaluator.build_running_compare_result(
            comparison_id=db_entry.id,
            generated_test_case=generated_test_case,
            modified_test_case=final_modified,
        )
        db_entry.comparison_result = running_result
        db.commit()
        background_tasks.add_task(
            _run_compare_background,
            comparison_id=db_entry.id,
            generated_test_case=generated_test_case,
            modified_test_case=final_modified,
            project_id=project_id,
            user_id=current_user.id,
            generation_id=generation_id,
            requirement_text=requirement_text,
            upload_persist=dict(upload_persist),
        )
        return {
            "result": running_result,
            "comparison_id": db_entry.id,
            "analysis_status": "running",
            "context_diagnostics": {
                "deferred": True,
                "reason": "large_compare_background",
                "generated_chars": len(generated_test_case or ""),
                "modified_chars": len(final_modified or ""),
            },
            "persistence": {
                "generation_id": generation_id,
                "comparison_id": db_entry.id,
                "artifact_saved": False,
                "artifact_doc_id": None,
                "source_filename": upload_persist["filename"],
                "ocr_ok": upload_persist["ocr_ok"],
                "ocr_source": upload_persist["ocr_source"],
            },
        }

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
        "analysis_status": _parse_compare_status(result),
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


@router.get("/compare-test-cases/{comparison_id}")
def get_compare_test_case_result(
    comparison_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (
        db.query(TestGenerationComparison)
        .filter(
            TestGenerationComparison.id == comparison_id,
            TestGenerationComparison.user_id == current_user.id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Comparison not found")
    get_owned_project(row.project_id, db, current_user.id)
    return {
        "comparison_id": row.id,
        "result": row.comparison_result or "",
        "analysis_status": _parse_compare_status(row.comparison_result or ""),
        "updated_at": getattr(row, "updated_at", None),
        "created_at": getattr(row, "created_at", None),
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
