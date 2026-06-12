import json
import logging
import re
from typing import Any, Optional

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from celery_config import celery_app
from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from modules.domain.knowledge_base import knowledge_base
from modules.knowledge_base_components.document.index_audit import run_index_consistency_audit
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from schemas.base.common import ErrorTranslateRequest
from routers.system.common_responses import (
    build_knowledge_detail_response,
    build_knowledge_list_related_maps,
    build_knowledge_list_response,
    build_parse_status_response,
    build_upload_knowledge_response,
)
from routers.system.common_support import (
    MoveDocumentRequest,
    RelationUpdateRequest,
    RetrieveContextRequest,
    _get_owned_doc_by_id_or_project_specific_id,
    _get_owned_project,
    _serialize_doc,
    extract_error_text,
    translate_error_text,
)

router = APIRouter(tags=["Common"])
logger = logging.getLogger(__name__)

@router.get("/knowledge-list")

def list_knowledge(
    project_id: int,
    page: int = 1,
    page_size: int = 10,
    search: Optional[str] = None,
    doc_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_linked_test_cases: bool = False,
    include_evaluation_reports: bool = False,
    include_internal_artifacts: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    知识库列表接口。

    默认隐藏：
    1. 已关联测试用例（source_doc_id 非空）
    2. 评估报告（evaluation_report）
    """
    project = _get_owned_project(project_id, current_user.id, db)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    total, documents = KnowledgeDocumentRepository(db).list_project_documents_paginated(
        project_id=project_id,
        page=page,
        page_size=page_size,
        search=search,
        doc_type=doc_type,
        include_linked_test_cases=include_linked_test_cases,
        include_evaluation_reports=include_evaluation_reports,
        include_internal_artifacts=include_internal_artifacts,
        start_date=start_date,
        end_date=end_date,
    )
    total_pages = (total + page_size - 1) // page_size if total else 1

    linked_map, source_name_map = build_knowledge_list_related_maps(db, project_id, documents)

    serialized_docs = [_serialize_doc(doc, source_name_map, linked_map) for doc in documents]
    return build_knowledge_list_response(serialized_docs, page, page_size, total, total_pages)


@router.post("/upload-knowledge")
async def upload_knowledge(
    file: UploadFile = File(...),
    project_id: int = Form(...),
    doc_type: str = Form("requirement"),
    force: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传知识库文件并入队离线解析。"""
    project = _get_owned_project(project_id, current_user.id, db)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    enqueue_result = await knowledge_base.enqueue_document_for_offline_parse(
        file=file,
        project_id=project_id,
        doc_type=doc_type,
        db=db,
        force=force,
        user_id=current_user.id,
    )
    doc = enqueue_result["document"]

    return build_upload_knowledge_response(doc, enqueue_result)
