from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from .test_generation_generate_routes_runtime import (
    WorkflowKind,
    WorkflowStage,
    context_orchestrator,
    get_current_user,
    get_db,
    get_owned_project,
    log_to_db,
    log_workflow_trace,
    parse_requirement_for_generation,
    test_generator,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/estimate-test-count")
async def estimate_test_count(
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    requirement: str = Form(""),
    file: UploadFile | None = File(None),
    prototype_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user),
):
    get_owned_project(project_id, db, current_user.id)

    req_text = (requirement or "").strip()
    if not req_text:
        if not file:
            return {"count": 20}
        req_text, parse_diag = await parse_requirement_for_generation(
            file,
            doc_type,
            prototype_file,
            db=db,
            user_id=current_user.id,
            project_id=project_id,
            source="estimate_test_count",
        )
        log_to_db(
            db,
            project_id,
            "system",
            f"GEN_DIAG:{json.dumps(parse_diag, ensure_ascii=False)}",
            user_id=current_user.id,
        )

    try:
        context_bundle = context_orchestrator.assemble_context(
            WorkflowKind.TEST_GENERATION,
            project_id,
            db,
            user_id=current_user.id,
            query_text=req_text[:500],
            requirement_text=req_text[:2000],
            include_knowledge=True,
            include_logs=True,
            knowledge_limit=2,
            log_limit=6,
        )
        log_workflow_trace(
            db,
            project_id,
            current_user.id,
            WorkflowKind.TEST_GENERATION,
            WorkflowStage.CONTEXT,
            {"action": "estimate_test_count", **context_bundle["diagnostics"]},
        )
        count = await run_in_threadpool(
            test_generator.estimate_test_count,
            req_text,
            project_id,
            db,
            current_user.id,
        )
        return {"count": max(1, int(count))}
    except Exception as e:
        logger.warning(f"Estimate test count failed ({type(e).__name__}): {e}")
        detail = str(e).strip() or f"{type(e).__name__}: estimate failed"
        if "Saved AI API key cannot be decrypted" in detail:
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=502, detail=f"Estimate test count failed: {detail}")
