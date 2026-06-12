import json
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from modules.domain.knowledge_base import knowledge_base
from modules.knowledge_base_components.document.index_audit import run_index_consistency_audit
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from modules.orchestration.task_runtime import get_task_runtime
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
    from routers.system import common as common_module

    project = common_module._get_owned_project(project_id, current_user.id, db)
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

    linked_map, source_name_map = common_module.build_knowledge_list_related_maps(db, project_id, documents)

    serialized_docs = [common_module._serialize_doc(doc, source_name_map, linked_map) for doc in documents]
    return common_module.build_knowledge_list_response(serialized_docs, page, page_size, total, total_pages)

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

@router.get("/knowledge/{doc_id}")

def get_knowledge(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个知识库文档详情。"""
    doc = _get_owned_doc_by_id_or_project_specific_id(doc_id, current_user.id, db)
    if not doc:
        raise HTTPException(status_code=404, detail="Knowledge document not found")

    linked_docs = KnowledgeDocumentRepository(db).list_linked_test_cases_for_sources(
        project_id=doc.project_id,
        source_doc_ids=[doc.id],
    )

    return build_knowledge_detail_response(doc, linked_docs)

@router.get("/knowledge/{doc_id}/parse-status")
def get_knowledge_parse_status(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    按文档查询离线解析状态。

    额外做一次任务态对账：若任务已失败但文档还停留在 parsing，则自动回收为 failed。
    """
    doc = _get_owned_doc_by_id_or_project_specific_id(doc_id, current_user.id, db)
    if not doc:
        raise HTTPException(status_code=404, detail="Knowledge document not found")

    task_state: Optional[str] = None
    if doc.task_id:
        try:
            task_state = str(
                get_task_runtime().get_status(task_id=str(doc.task_id)).get("status") or "UNKNOWN"
            )
        except Exception as e:
            task_state = "UNKNOWN"
            logger.warning("读取 Celery 任务状态失败 task_id=%s err=%s", doc.task_id, e)

        if doc.parse_status == "parsing" and task_state in ("FAILURE", "REVOKED"):
            knowledge_base.mark_document_parse_failed(
                doc_id=doc.id,
                error=f"解析任务异常终止（{task_state}），状态已自动回收。",
                db=db,
                task_id=doc.task_id,
                retry_count=doc.retry_count,
            )
            db.refresh(doc)

        if (
            doc.parse_status == "parsing"
            and task_state in ("PENDING", "UNKNOWN")
            and doc.retry_count >= 2
        ):
            knowledge_base.mark_document_parse_failed(
                doc_id=doc.id,
                error=f"解析任务状态异常（{task_state}），已按失败回收。",
                db=db,
                task_id=doc.task_id,
                retry_count=doc.retry_count,
            )
            db.refresh(doc)

    return build_parse_status_response(doc, task_state)

@router.post("/knowledge/index-consistency/audit")
def trigger_index_consistency_audit(
    project_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    手动触发知识库索引一致性巡检任务。

    说明：
    - 传 project_id：仅巡检当前项目；
    - 不传：巡检当前用户可见范围（异步任务由 user_id 过滤）。
    """
    if project_id is not None:
        project = _get_owned_project(project_id, current_user.id, db)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

    task_id = get_task_runtime().dispatch(
        task_name="modules.orchestration.tasks.audit_knowledge_index_consistency_task",
        kwargs={
            "project_id": project_id,
            "user_id": current_user.id,
            "limit": 5000,
        },
    )
    return {
        "success": True,
        "task_id": task_id,
        "project_id": project_id,
        "message": "Index consistency audit task queued",
    }

@router.get("/knowledge/index-consistency/report")
def get_index_consistency_report(
    project_id: Optional[int] = None,
    limit: int = 5000,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    同步获取索引一致性巡检报告（轻量排障入口）。
    """
    if project_id is not None:
        project = _get_owned_project(project_id, current_user.id, db)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
    return run_index_consistency_audit(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        limit=max(100, int(limit)),
    )

@router.get("/knowledge/projects/{project_id}/context-snapshot-status")
def get_context_snapshot_status(
    project_id: int,
    force_rebuild: bool = False,
    async_rebuild: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    查询项目级上下文快照状态。

    可选参数：
    - force_rebuild=true：先触发一次手动重建，再返回最新状态。
    - async_rebuild=true：手动重建走后台异步预热（默认）。
    """
    project = _get_owned_project(project_id, current_user.id, db)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if force_rebuild:
        if async_rebuild:
            enqueue_result = knowledge_base.enqueue_context_snapshot_rebuild(
                project_id=project_id,
                db=db,
                user_id=current_user.id,
                force_rebuild=True,
            )
        else:
            enqueue_result = knowledge_base.get_or_build_context_snapshot(
                project_id=project_id,
                db=db,
                user_id=current_user.id,
                force_rebuild=True,
                prefer_async_rebuild=False,
            )
    else:
        enqueue_result = None

    status = knowledge_base.get_context_snapshot_status(project_id=project_id, db=db)
    status["last_generation_used_snapshot"] = bool(status.get("usable_for_generation", False))
    if enqueue_result is not None:
        status["rebuild_trigger"] = enqueue_result
    return status

@router.post("/knowledge/retrieve-context")
def retrieve_knowledge_context(
    req: RetrieveContextRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    检索治理层调试接口。

    说明：
    1. 默认 debug=false，仅返回压缩后的上下文文本，不影响生产链路。
    2. debug=true 时附带查询改写、多路召回、去重与压缩统计信息。
    3. 稳定性收口后，debug 还会返回 attempt_count/attempts/final_status，
       用于定位“首次失败、重试恢复”与“重试后降级为空上下文”的场景。
    """
    project = _get_owned_project(req.project_id, current_user.id, db)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    result = knowledge_base.get_relevant_context(
        query=req.query,
        project_id=req.project_id,
        limit=max(1, int(req.limit)),
        db=db,
        user_id=current_user.id,
        debug=bool(req.debug),
        max_tokens=max(128, int(req.max_tokens)),
        retrieval_options=(req.to_retrieval_options() or None),
    )

    if isinstance(result, str):
        return {"context": result}
    return result

@router.delete("/knowledge/{doc_id}")
def delete_knowledge(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除知识库文档并清理关联关系。"""
    doc = _get_owned_doc_by_id_or_project_specific_id(doc_id, current_user.id, db)
    if not doc:
        raise HTTPException(status_code=404, detail="Knowledge document not found")

    success = knowledge_base.delete_document(doc.id, db)
    if not success:
        raise HTTPException(status_code=404, detail="Knowledge document not found")

    return {"success": True}

@router.post("/knowledge/update-relation")
def update_knowledge_relation(
    req: RelationUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新测试用例与需求文档关联关系。"""
    target_doc = _get_owned_doc_by_id_or_project_specific_id(req.doc_id, current_user.id, db)
    if not target_doc:
        raise HTTPException(status_code=404, detail="Target document not found")

    if req.source_doc_id not in (None, -1):
        source_doc = _get_owned_doc_by_id_or_project_specific_id(req.source_doc_id, current_user.id, db)
        if not source_doc:
            return {"success": False, "error": "Source document not found"}
        if source_doc.project_id != target_doc.project_id:
            return {"success": False, "error": "Source document must be in the same project"}

    ok, err = knowledge_base.update_relation(target_doc.id, req.source_doc_id, db)
    if not ok:
        return {"success": False, "error": err}
    return {"success": True}

@router.post("/knowledge/move")
def move_knowledge(
    req: MoveDocumentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """拖拽调整知识库文档顺序。"""
    project = _get_owned_project(req.project_id, current_user.id, db)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if req.position not in ("before", "after"):
        return {"success": False, "error": "position must be before or after"}

    moved = knowledge_base.move_document(req.project_id, req.doc_id, req.anchor_doc_id, req.position, db)
    if not moved:
        return {"success": False, "error": "Move failed"}
    return {"success": True}

@router.post("/error/translate")
def translate_error(req: ErrorTranslateRequest, current_user: User = Depends(get_current_user)):
    """把接口/网络错误翻译成更友好的中文提示。"""
    raw = extract_error_text(req.error)
    message = translate_error_text(raw)
    return {"message": message, "raw": raw}
