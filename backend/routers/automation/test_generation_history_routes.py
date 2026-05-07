from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from modules.test_generation_components.services.final_case_learning_service import (
    FinalCaseLearningService,
)
from modules.test_generation_components.services.history_service import TestGenerationHistoryService

router = APIRouter()


class PrioritySamplePoolSaveRequest(BaseModel):
    generation_id: int | None = Field(default=None)
    samples: list[dict] = Field(default_factory=list)


class FinalCaseLearningRequest(BaseModel):
    final_cases: list[dict] = Field(default_factory=list)
    final_case_doc_ids: list[int] = Field(default_factory=list)
    source_doc_ids: list[int] = Field(default_factory=list)
    include_linked_docs: bool = Field(default=True)
    include_negative_samples: bool = Field(default=True)
    dry_run: bool = Field(default=True)


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
