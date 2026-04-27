from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from modules.rag_eval.services.rag_dataset_management_service import RagDatasetManagementService
from schemas.rag.rag_dataset import (
    RagDatasetCreate,
    RagDatasetImportResponse,
    RagDatasetOut,
    RagDatasetUpdate,
    RagSampleCreate,
    RagSampleOut,
    RagSampleUpdate,
)

router = APIRouter(tags=["RAG Datasets"])


@router.get("/rag/datasets", response_model=list[RagDatasetOut])
def list_rag_datasets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    datasets = RagDatasetManagementService(db).list_datasets(user_id=current_user.id)
    result: list[RagDatasetOut] = []
    for ds in datasets:
        result.append(RagDatasetOut.model_validate(ds))
    return result


@router.post("/rag/datasets", response_model=RagDatasetOut)
def create_rag_dataset(payload: RagDatasetCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    status, ds = RagDatasetManagementService(db).create_dataset(
        user_id=current_user.id,
        payload=payload.model_dump(),
    )
    if status == "exists":
        raise HTTPException(status_code=400, detail="Dataset name already exists")
    if not ds:
        raise HTTPException(status_code=500, detail="Failed to create dataset")
    return RagDatasetOut.model_validate({**ds.__dict__, "sample_count": 0})


@router.put("/rag/datasets/{dataset_id}", response_model=RagDatasetOut)
def update_rag_dataset(dataset_id: int, payload: RagDatasetUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    status, ds = RagDatasetManagementService(db).update_dataset(
        dataset_id=dataset_id,
        user_id=current_user.id,
        payload=payload.model_dump(exclude_unset=True),
    )
    if status == "not_found" or not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    count = RagDatasetManagementService(db).count_samples(dataset_id=dataset_id)
    return RagDatasetOut.model_validate({**ds.__dict__, "sample_count": count})


@router.delete("/rag/datasets/{dataset_id}")
def delete_rag_dataset(dataset_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    deleted = RagDatasetManagementService(db).delete_dataset(dataset_id=dataset_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return {"success": True}


@router.get("/rag/datasets/{dataset_id}/samples", response_model=list[RagSampleOut])
def list_dataset_samples(
    dataset_id: int,
    tags: str | None = Query(default=None),
    difficulty: str = Query(default="all"),
    enabled_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    parsed_tags = [x.strip() for x in (tags or "").split(",") if x.strip()] if tags else None
    status, rows = RagDatasetManagementService(db).list_samples(
        dataset_id=dataset_id,
        user_id=current_user.id,
        tags=parsed_tags,
        difficulty=difficulty,
        enabled_only=enabled_only,
        page=page,
        page_size=page_size,
    )
    if status == "dataset_not_found":
        raise HTTPException(status_code=404, detail="Dataset not found")
    return [RagSampleOut.model_validate(x) for x in rows]


@router.post("/rag/datasets/{dataset_id}/samples", response_model=RagSampleOut)
def create_dataset_sample(dataset_id: int, payload: RagSampleCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    status, row = RagDatasetManagementService(db).create_sample(
        dataset_id=dataset_id,
        user_id=current_user.id,
        payload=payload.model_dump(),
    )
    if status == "dataset_not_found" or not row:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return RagSampleOut.model_validate(row)


@router.put("/rag/datasets/samples/{sample_id}", response_model=RagSampleOut)
def update_dataset_sample(sample_id: int, payload: RagSampleUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    status, row = RagDatasetManagementService(db).update_sample(
        sample_id=sample_id,
        user_id=current_user.id,
        payload=payload.model_dump(exclude_unset=True),
    )
    if status == "not_found" or not row:
        raise HTTPException(status_code=404, detail="Sample not found")
    return RagSampleOut.model_validate(row)


@router.delete("/rag/datasets/samples/{sample_id}")
def delete_dataset_sample(sample_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    deleted = RagDatasetManagementService(db).delete_sample(sample_id=sample_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Sample not found")
    return {"success": True}


@router.post("/rag/datasets/import", response_model=RagDatasetImportResponse)
async def import_rag_dataset(
    file: UploadFile = File(...),
    dataset_id: int | None = Form(default=None),
    name: str | None = Form(default=None),
    type: str = Form(default="validation"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    raw = (await file.read()).decode("utf-8", errors="ignore")
    try:
        ds_id, imported, skipped, errors = RagDatasetManagementService(db).import_samples(
            user_id=current_user.id,
            raw_content=raw,
            dataset_id=dataset_id,
            name=name,
            dataset_type=type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RagDatasetImportResponse(
        success=True,
        dataset_id=ds_id,
        imported_count=imported,
        skipped_count=skipped,
        errors=errors,
    )


@router.get("/rag/datasets/export/{dataset_id}")
def export_rag_dataset(dataset_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    status, content = RagDatasetManagementService(db).export_dataset_lines(
        dataset_id=dataset_id,
        user_id=current_user.id,
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Dataset not found")
    return Response(
        content=content.encode("utf-8"),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename=rag_dataset_{dataset_id}.jsonl"},
    )

