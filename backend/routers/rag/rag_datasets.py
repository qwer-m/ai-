from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import RagDataset, RagDatasetSample, User
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
    datasets = (
        db.query(RagDataset)
        .filter(RagDataset.user_id == current_user.id)
        .order_by(RagDataset.updated_at.desc(), RagDataset.id.desc())
        .all()
    )
    result: list[RagDatasetOut] = []
    for ds in datasets:
        count = db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == ds.id).count()
        result.append(RagDatasetOut.model_validate({**ds.__dict__, "sample_count": count}))
    return result


@router.post("/rag/datasets", response_model=RagDatasetOut)
def create_rag_dataset(payload: RagDatasetCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    exists = db.query(RagDataset).filter(RagDataset.user_id == current_user.id, RagDataset.name == payload.name).first()
    if exists:
        raise HTTPException(status_code=400, detail="Dataset name already exists")
    ds = RagDataset(user_id=current_user.id, name=payload.name, type=payload.type, description=payload.description)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return RagDatasetOut.model_validate({**ds.__dict__, "sample_count": 0})


@router.put("/rag/datasets/{dataset_id}", response_model=RagDatasetOut)
def update_rag_dataset(dataset_id: int, payload: RagDatasetUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ds = db.query(RagDataset).filter(RagDataset.id == dataset_id, RagDataset.user_id == current_user.id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(ds, key, value)
    db.commit()
    db.refresh(ds)
    count = db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == ds.id).count()
    return RagDatasetOut.model_validate({**ds.__dict__, "sample_count": count})


@router.delete("/rag/datasets/{dataset_id}")
def delete_rag_dataset(dataset_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ds = db.query(RagDataset).filter(RagDataset.id == dataset_id, RagDataset.user_id == current_user.id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == ds.id).delete()
    db.delete(ds)
    db.commit()
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
    ds = db.query(RagDataset).filter(RagDataset.id == dataset_id, RagDataset.user_id == current_user.id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    q = db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == dataset_id)
    if enabled_only:
        q = q.filter(RagDatasetSample.enabled.is_(True))
    if difficulty != "all":
        q = q.filter(RagDatasetSample.difficulty == difficulty)
    if tags:
        for t in [x.strip() for x in tags.split(",") if x.strip()]:
            q = q.filter(RagDatasetSample.tags.contains([t]))
    rows = q.order_by(RagDatasetSample.id.asc()).offset((page - 1) * page_size).limit(page_size).all()
    return [RagSampleOut.model_validate(x) for x in rows]


@router.post("/rag/datasets/{dataset_id}/samples", response_model=RagSampleOut)
def create_dataset_sample(dataset_id: int, payload: RagSampleCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ds = db.query(RagDataset).filter(RagDataset.id == dataset_id, RagDataset.user_id == current_user.id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    row = RagDatasetSample(dataset_id=dataset_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return RagSampleOut.model_validate(row)


@router.put("/rag/datasets/samples/{sample_id}", response_model=RagSampleOut)
def update_dataset_sample(sample_id: int, payload: RagSampleUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = (
        db.query(RagDatasetSample)
        .join(RagDataset, RagDataset.id == RagDatasetSample.dataset_id)
        .filter(RagDatasetSample.id == sample_id, RagDataset.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sample not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return RagSampleOut.model_validate(row)


@router.delete("/rag/datasets/samples/{sample_id}")
def delete_dataset_sample(sample_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = (
        db.query(RagDatasetSample)
        .join(RagDataset, RagDataset.id == RagDatasetSample.dataset_id)
        .filter(RagDatasetSample.id == sample_id, RagDataset.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sample not found")
    db.delete(row)
    db.commit()
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
    if dataset_id:
        ds = db.query(RagDataset).filter(RagDataset.id == dataset_id, RagDataset.user_id == current_user.id).first()
        if not ds:
            raise HTTPException(status_code=404, detail="Dataset not found")
    else:
        ds = RagDataset(user_id=current_user.id, name=name or f"import-{datetime.now().strftime('%Y%m%d%H%M%S')}", type=type, description="imported")
        db.add(ds)
        db.commit()
        db.refresh(ds)

    raw = (await file.read()).decode("utf-8", errors="ignore")
    imported = 0
    skipped = 0
    errors: list[str] = []
    for idx, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            query = str(item.get("query") or "").strip()
            if not query:
                skipped += 1
                continue
            exists = db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == ds.id, RagDatasetSample.query == query).first()
            if exists:
                skipped += 1
                continue
            row = RagDatasetSample(
                dataset_id=ds.id,
                query=query,
                gold_docs=item.get("gold_docs") or [],
                gold_chunks=item.get("gold_chunks") or [],
                gold_answer=item.get("gold_answer") or "",
                answer_points=item.get("answer_points") or [],
                tags=item.get("tags") or [],
                difficulty=item.get("difficulty") or "medium",
                metadata_filters=item.get("metadata_filters") or {},
                expected_doc_version=item.get("expected_doc_version"),
                enabled=bool(item.get("enabled", True)),
            )
            db.add(row)
            imported += 1
        except Exception as e:
            errors.append(f"line {idx}: {e}")
    db.commit()
    return RagDatasetImportResponse(success=True, dataset_id=ds.id, imported_count=imported, skipped_count=skipped, errors=errors[:30])


@router.get("/rag/datasets/export/{dataset_id}")
def export_rag_dataset(dataset_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ds = db.query(RagDataset).filter(RagDataset.id == dataset_id, RagDataset.user_id == current_user.id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    rows = db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == dataset_id).order_by(RagDatasetSample.id.asc()).all()
    lines = []
    for r in rows:
        lines.append(
            json.dumps(
                {
                    "id": r.id,
                    "query": r.query,
                    "gold_docs": r.gold_docs or [],
                    "gold_chunks": r.gold_chunks or [],
                    "gold_answer": r.gold_answer or "",
                    "answer_points": r.answer_points or [],
                    "tags": r.tags or [],
                    "difficulty": r.difficulty,
                    "metadata_filters": r.metadata_filters or {},
                    "expected_doc_version": r.expected_doc_version,
                    "enabled": bool(r.enabled),
                },
                ensure_ascii=False,
            )
        )
    content = "\n".join(lines)
    return Response(
        content=content.encode("utf-8"),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename=rag_dataset_{dataset_id}.jsonl"},
    )

