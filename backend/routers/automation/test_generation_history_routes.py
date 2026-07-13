from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from core.processing.file_processing import is_image_filename, parse_file_bytes, parse_image_bytes_with_fallback
from modules.test_generation_components.services.final_case_learning_service import (
    FinalCaseLearningService,
    parse_test_cases_spreadsheet_bytes,
)
from modules.test_generation_components.services.generation_optimization_service import (
    GenerationOptimizationService,
)
from modules.test_generation_components.services.history_service import TestGenerationHistoryService
from modules.test_generation_components.execution.execution_suite import convert_execution_suite_to_excel

router = APIRouter()


class PrioritySamplePoolSaveRequest(BaseModel):
    generation_id: int | None = Field(default=None)
    samples: list[dict] = Field(default_factory=list)


class PrioritySamplePoolDeleteRequest(BaseModel):
    generation_id: int | None = Field(default=None)
    sample_id: str = Field(default="")
    delete_reason: str = Field(default="")


class PrioritySamplePoolAddRequest(BaseModel):
    generation_id: int | None = Field(default=None)
    samples: list[dict] = Field(default_factory=list)


class PrioritySamplePoolUpdateRequest(BaseModel):
    generation_id: int | None = Field(default=None)
    sample_id: str = Field(default="")
    patch: dict[str, Any] = Field(default_factory=dict)


class PrioritySamplePoolConfirmRequest(BaseModel):
    generation_id: int | None = Field(default=None)
    sample_id: str = Field(default="")
    patch: dict[str, Any] = Field(default_factory=dict)


class PrioritySamplePoolBulkArchiveRequest(BaseModel):
    generation_id: int | None = Field(default=None)
    sample_ids: list[str] = Field(default_factory=list)
    delete_reason: str = Field(default="")


class FinalCaseLearningRequest(BaseModel):
    final_cases: list[dict] = Field(default_factory=list)
    final_case_doc_ids: list[int] = Field(default_factory=list)
    source_doc_ids: list[int] = Field(default_factory=list)
    include_linked_docs: bool = Field(default=True)
    include_negative_samples: bool = Field(default=True)
    dry_run: bool = Field(default=True)


class EvaluationCaseLearningRequest(BaseModel):
    project_id: int
    generated_cases: Any = Field(default="")
    final_cases: Any = Field(default="")
    generation_id: int | None = Field(default=None)
    include_negative_samples: bool = Field(default=True)
    dry_run: bool = Field(default=True)


class EvaluationDefectLearningCandidateRequest(BaseModel):
    project_id: int
    evaluation_result: Any = Field(default="")


class ApplyEvaluationLearningCandidatesRequest(BaseModel):
    project_id: int
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = Field(default=True)


class TestGenerationOptimizeRequest(BaseModel):
    apply: bool = Field(default=True)
    max_new_cases: int = Field(default=30, ge=1, le=60)


class TestGenerationPreviewOptimizeRequest(BaseModel):
    project_id: int
    requirement_text: str = Field(default="")
    cases: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: Any = Field(default_factory=dict)
    apply: bool = Field(default=True)
    max_new_cases: int = Field(default=30, ge=1, le=60)


@router.get("/test-generations")
def list_test_generations(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, rows = TestGenerationHistoryService(db).list_generations(
        project_id=project_id,
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return rows


@router.post("/test-generations/learn-from-evaluation")
def learn_from_evaluation_cases(
    req: EvaluationCaseLearningRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = FinalCaseLearningService(db).learn_from_case_pair(
        project_id=int(req.project_id),
        user_id=current_user.id,
        generated_cases=req.generated_cases,
        final_cases=req.final_cases,
        generation_id=req.generation_id,
        include_negative_samples=bool(req.include_negative_samples),
        dry_run=bool(req.dry_run),
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status == "generation_not_found":
        raise HTTPException(status_code=404, detail="Generation not found")
    return payload


@router.post("/test-generations/learn-from-evaluation-file")
async def learn_from_evaluation_cases_file(
    project_id: int = Form(...),
    generated_cases: str = Form(""),
    final_cases: str = Form(""),
    generation_id: int | None = Form(None),
    include_negative_samples: bool = Form(True),
    dry_run: bool = Form(True),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    final_payload: Any = (final_cases or "").strip()
    file_meta: dict[str, Any] = {
        "from_upload": False,
        "filename": "",
        "content_type": "",
        "size": 0,
    }
    if file is not None:
        filename = file.filename or ""
        raw_bytes = await file.read()
        file_meta.update(
            {
                "from_upload": True,
                "filename": filename,
                "content_type": file.content_type or "",
                "size": len(raw_bytes),
            }
        )
        direct_cases = parse_test_cases_spreadsheet_bytes(filename, raw_bytes)
        if direct_cases:
            final_payload = direct_cases
            file_meta["parse_strategy"] = "spreadsheet_rows"
            file_meta["parsed_case_count"] = len(direct_cases)
        elif is_image_filename(filename) and not final_payload:
            final_payload, _ocr_meta = parse_image_bytes_with_fallback(
                filename=filename,
                content_bytes=raw_bytes,
                db=db,
                user_id=current_user.id,
            )
            file_meta["parse_strategy"] = "image_ocr"
        elif not final_payload:
            final_payload = parse_file_bytes(
                filename=filename,
                content_bytes=raw_bytes,
                db=db,
                user_id=current_user.id,
            )
            file_meta["parse_strategy"] = "file_text"

    status, payload = FinalCaseLearningService(db).learn_from_case_pair(
        project_id=int(project_id),
        user_id=current_user.id,
        generated_cases=generated_cases,
        final_cases=final_payload,
        generation_id=generation_id,
        include_negative_samples=bool(include_negative_samples),
        dry_run=bool(dry_run),
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status == "generation_not_found":
        raise HTTPException(status_code=404, detail="Generation not found")
    if isinstance(payload, dict):
        payload["file_parse"] = file_meta
    return payload


@router.post("/test-generations/learning-candidates/from-evaluation")
def build_learning_candidates_from_evaluation(
    req: EvaluationDefectLearningCandidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = FinalCaseLearningService(db).build_learning_candidates_from_evaluation(
        project_id=int(req.project_id),
        user_id=current_user.id,
        evaluation_result=req.evaluation_result,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return payload


@router.post("/test-generations/learning-candidates/apply")
def apply_learning_candidates(
    req: ApplyEvaluationLearningCandidatesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = FinalCaseLearningService(db).apply_learning_candidates(
        project_id=int(req.project_id),
        user_id=current_user.id,
        candidates=req.candidates or [],
        dry_run=bool(req.dry_run),
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return payload


@router.get("/test-generations/{generation_id}")
def get_test_generation(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).get_generation(
        generation_id=generation_id,
        user_id=current_user.id,
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Test generation not found")
    return payload


@router.get("/test-generations/{generation_id}/bundle")
def get_test_generation_bundle(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).get_bundle(
        generation_id=generation_id,
        user_id=current_user.id,
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Test generation not found")
    return payload


@router.get("/test-generations/{generation_id}/execution-suite")
def get_test_generation_execution_suite(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).get_execution_suite(
        generation_id=generation_id,
        user_id=current_user.id,
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Test generation not found")
    return payload


@router.get("/test-generations/{generation_id}/execution-suite-excel")
def export_test_generation_execution_suite_excel(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).get_execution_suite(
        generation_id=generation_id,
        user_id=current_user.id,
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Test generation not found")
    excel_bytes = convert_execution_suite_to_excel(payload or {})
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=execution_suite.xlsx"},
    )


@router.post("/test-generations/{generation_id}/optimize")
def optimize_test_generation(
    generation_id: int,
    req: TestGenerationOptimizeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = GenerationOptimizationService(db).optimize_generation(
        generation_id=generation_id,
        user_id=current_user.id,
        apply=bool(req.apply),
        max_new_cases=int(req.max_new_cases),
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Test generation not found")
    if status == "invalid_source":
        raise HTTPException(status_code=400, detail=payload or {"message": "Invalid source generation"})
    if status in {"patch_invalid", "drop_ratio_exceeded"}:
        raise HTTPException(status_code=400, detail=payload or {"message": status})
    if status == "model_error":
        raise HTTPException(status_code=502, detail=payload or {"message": "Model call failed"})
    if status == "model_timeout":
        raise HTTPException(status_code=504, detail=payload or {"message": "Optimization model timed out"})
    if status == "quality_gate_failed":
        raise HTTPException(status_code=409, detail=payload or {"message": "Optimized result failed quality gate"})
    if status == "persistence_failed":
        raise HTTPException(status_code=500, detail=payload or {"message": "Optimized result persistence failed"})
    return payload


@router.post("/test-generations/optimize-preview")
def optimize_preview_test_generation(
    req: TestGenerationPreviewOptimizeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = GenerationOptimizationService(db).optimize_preview_generation(
        project_id=int(req.project_id),
        user_id=current_user.id,
        requirement_text=req.requirement_text or "",
        cases=req.cases or [],
        diagnostics=req.diagnostics,
        apply=bool(req.apply),
        max_new_cases=int(req.max_new_cases),
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status == "invalid_source":
        raise HTTPException(status_code=400, detail=payload or {"message": "Invalid preview generation"})
    if status in {"patch_invalid", "drop_ratio_exceeded"}:
        raise HTTPException(status_code=400, detail=payload or {"message": status})
    if status == "model_error":
        raise HTTPException(status_code=502, detail=payload or {"message": "Model call failed"})
    if status == "model_timeout":
        raise HTTPException(status_code=504, detail=payload or {"message": "Optimization model timed out"})
    if status == "quality_gate_failed":
        raise HTTPException(status_code=409, detail=payload or {"message": "Optimized result failed quality gate"})
    if status == "persistence_failed":
        raise HTTPException(status_code=500, detail=payload or {"message": "Optimized result persistence failed"})
    return payload


@router.post("/test-generations/{generation_id}/learn-from-final-cases")
def learn_from_final_cases(
    generation_id: int,
    req: FinalCaseLearningRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = FinalCaseLearningService(db).learn_from_generation_final_cases(
        generation_id=generation_id,
        user_id=current_user.id,
        final_cases=req.final_cases or None,
        final_case_doc_ids=req.final_case_doc_ids or None,
        source_doc_ids=req.source_doc_ids or None,
        include_linked_docs=bool(req.include_linked_docs),
        include_negative_samples=bool(req.include_negative_samples),
        dry_run=bool(req.dry_run),
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Test generation not found")
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return payload


@router.get("/test-generations/projects/{project_id}/priority-sample-pool")
def get_priority_sample_pool(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).get_priority_sample_pool(
        project_id=project_id,
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return payload


@router.get("/test-generations/projects/{project_id}/priority-sample-pool/consistency")
def get_priority_sample_pool_consistency(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).get_priority_sample_pool_consistency(
        project_id=project_id,
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return payload


@router.put("/test-generations/projects/{project_id}/priority-sample-pool")
def save_priority_sample_pool(
    project_id: int,
    req: PrioritySamplePoolSaveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).save_priority_sample_pool(
        project_id=project_id,
        user_id=current_user.id,
        generation_id=req.generation_id,
        samples=req.samples or [],
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return payload


@router.post("/test-generations/projects/{project_id}/priority-sample-pool/delete-sample")
def delete_priority_sample_pool_item(
    project_id: int,
    req: PrioritySamplePoolDeleteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).delete_priority_sample_pool_item(
        project_id=project_id,
        user_id=current_user.id,
        generation_id=req.generation_id,
        sample_id=req.sample_id,
        delete_reason=req.delete_reason,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status == "sample_not_found":
        raise HTTPException(status_code=404, detail="Sample not found")
    return payload


@router.post("/test-generations/projects/{project_id}/priority-sample-pool/add-samples")
def add_priority_sample_pool_items(
    project_id: int,
    req: PrioritySamplePoolAddRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).add_priority_sample_pool_items(
        project_id=project_id,
        user_id=current_user.id,
        generation_id=req.generation_id,
        samples=req.samples or [],
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status == "no_samples":
        raise HTTPException(status_code=400, detail="No samples provided")
    return payload


@router.post("/test-generations/projects/{project_id}/priority-sample-pool/update-sample")
def update_priority_sample_pool_item(
    project_id: int,
    req: PrioritySamplePoolUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).update_priority_sample_pool_item(
        project_id=project_id,
        user_id=current_user.id,
        generation_id=req.generation_id,
        sample_id=req.sample_id,
        patch=req.patch,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status == "sample_not_found":
        raise HTTPException(status_code=404, detail="Sample not found")
    return payload


@router.post("/test-generations/projects/{project_id}/priority-sample-pool/confirm-sample")
def confirm_priority_sample_pool_item(
    project_id: int,
    req: PrioritySamplePoolConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).confirm_priority_sample_pool_item(
        project_id=project_id,
        user_id=current_user.id,
        generation_id=req.generation_id,
        sample_id=req.sample_id,
        patch=req.patch,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status == "sample_not_found":
        raise HTTPException(status_code=404, detail="Sample not found")
    return payload


@router.post("/test-generations/projects/{project_id}/priority-sample-pool/bulk-archive")
def bulk_archive_priority_sample_pool_items(
    project_id: int,
    req: PrioritySamplePoolBulkArchiveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, payload = TestGenerationHistoryService(db).bulk_archive_priority_sample_pool_items(
        project_id=project_id,
        user_id=current_user.id,
        generation_id=req.generation_id,
        sample_ids=req.sample_ids or [],
        delete_reason=req.delete_reason,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status == "no_samples_archived":
        raise HTTPException(status_code=404, detail="No samples matched for archival")
    return payload


@router.get("/test-generations/projects/{project_id}/priority-sample-pool/learning-history")
def get_learning_selection_history(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, events = TestGenerationHistoryService(db).get_learning_selection_history(
        project_id=project_id,
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project_id, "events": events}
