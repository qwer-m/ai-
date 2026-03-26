from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import TestGeneration, User
from modules.testing.evaluation_artifact_store import load_compare_artifact_payload
from routers.automation.test_generation_shared import (
    build_history_key,
    extract_history_title,
    find_matching_comparison,
    infer_compare_filename,
)
from routers.test_generation_routes.support import get_owned_project

router = APIRouter()


@router.get("/test-generations")
def list_test_generations(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)
    rows: list[TestGeneration] = (
        db.query(TestGeneration)
        .filter(TestGeneration.project_id == project_id)
        .order_by(TestGeneration.created_at.desc(), TestGeneration.id.desc())
        .all()
    )
    latest_by_key: dict[str, TestGeneration] = {}
    for row in rows:
        key = build_history_key(row.requirement_text or "")
        if key not in latest_by_key:
            latest_by_key[key] = row

    dedup_rows = sorted(
        latest_by_key.values(),
        key=lambda item: (item.created_at or datetime.min, item.id or 0),
        reverse=True,
    )

    return_rows = []
    for row in dedup_rows:
        matched = find_matching_comparison(
            project_id=row.project_id or 0,
            user_id=current_user.id,
            generated_result=row.generated_result or "",
            generation_created_at=row.created_at,
            db=db,
        )
        artifact = None
        if row.project_id is not None:
            artifact = load_compare_artifact_payload(
                db=db,
                project_id=row.project_id,
                user_id=current_user.id,
                generation_id=row.id,
            )
        has_artifact_comparison = bool((artifact or {}).get("comparison_result"))
        history_title = extract_history_title(row.requirement_text or "")
        history_key = build_history_key(row.requirement_text or "")
        return_rows.append(
            {
                "id": row.id,
                "project_id": row.project_id,
                "requirement_text": row.requirement_text or "",
                "created_at": row.created_at,
                "history_title": history_title,
                "history_key": history_key,
                "has_comparison": bool(matched) or has_artifact_comparison,
            }
        )
    return return_rows


@router.get("/test-generations/{generation_id}")
def get_test_generation(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entry = db.query(TestGeneration).filter(TestGeneration.id == generation_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Test generation not found")

    if entry.project_id is not None:
        get_owned_project(entry.project_id, db, current_user.id)
    elif entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Test generation not found")

    if entry.generated_result:
        try:
            return json.loads(entry.generated_result)
        except Exception:
            pass

    return {
        "id": entry.id,
        "project_id": entry.project_id,
        "requirement_text": entry.requirement_text or "",
        "generated_result": entry.generated_result,
        "created_at": entry.created_at,
    }


@router.get("/test-generations/{generation_id}/bundle")
def get_test_generation_bundle(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entry = db.query(TestGeneration).filter(TestGeneration.id == generation_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Test generation not found")

    if entry.project_id is not None:
        get_owned_project(entry.project_id, db, current_user.id)
    elif entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Test generation not found")

    generated_result = entry.generated_result or ""
    matched = (
        find_matching_comparison(
            project_id=entry.project_id or 0,
            user_id=current_user.id,
            generated_result=generated_result,
            generation_created_at=entry.created_at,
            db=db,
        )
        if entry.project_id is not None
        else None
    )
    artifact = (
        load_compare_artifact_payload(
            db=db,
            project_id=entry.project_id or 0,
            user_id=current_user.id,
            generation_id=entry.id,
        )
        if entry.project_id is not None
        else None
    )

    comparison = None
    if matched or artifact:
        artifact_modified = (artifact or {}).get("modified_test_case") or ""
        artifact_result = (artifact or {}).get("comparison_result") or ""
        merged_modified = (matched.modified_test_case or "") if matched else artifact_modified
        merged_result = (matched.comparison_result or "") if matched else artifact_result
        merged_filename = (
            (artifact or {}).get("source_filename")
            or getattr(matched, "source_filename", None)
            or infer_compare_filename(merged_modified)
        )
        comparison = {
            "id": matched.id if matched else None,
            "modified_test_case": merged_modified,
            "comparison_result": merged_result,
            "source_filename": merged_filename,
            "created_at": matched.created_at if matched else (artifact or {}).get("updated_at"),
            "artifact_doc_id": (artifact or {}).get("artifact_doc_id"),
            "source_file_content_type": (artifact or {}).get("source_file_content_type"),
            "source_file_size": (artifact or {}).get("source_file_size"),
            "ocr": (artifact or {}).get("ocr"),
        }
    has_comparison = bool(comparison and (comparison.get("comparison_result") or comparison.get("modified_test_case")))

    return {
        "generation": {
            "id": entry.id,
            "project_id": entry.project_id,
            "requirement_text": entry.requirement_text or "",
            "generated_result": generated_result,
            "created_at": entry.created_at,
            "history_title": extract_history_title(entry.requirement_text or ""),
            "history_key": build_history_key(entry.requirement_text or ""),
        },
        "comparison": comparison,
        "comparison_status": "found" if has_comparison else "missing",
    }
