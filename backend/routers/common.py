import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy.orm import Session

from celery_config import celery_app
from core.auth import get_current_user
from core.database import get_db
from core.models import KnowledgeDocument, Project, User
from modules.knowledge_base import knowledge_base
from schemas.common import ErrorTranslateRequest

router = APIRouter(tags=["Common"])
logger = logging.getLogger(__name__)

# 需求类文档类型集合：这些文档可以作为测试用例的来源文档。
REQUIREMENT_LIKE_TYPES = {"requirement", "product_requirement", "incomplete"}


class RelationUpdateRequest(BaseModel):
    """更新测试用例与需求文档关联关系的请求体。"""

    doc_id: int
    source_doc_id: Optional[int] = None


class MoveDocumentRequest(BaseModel):
    """知识库拖拽排序请求体。"""

    project_id: int
    doc_id: int
    anchor_doc_id: int
    position: str


def _to_iso(dt: Any) -> Optional[str]:
    """统一把时间字段转为可序列化的 ISO 字符串。"""
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def _serialize_linked_doc(doc: KnowledgeDocument) -> dict:
    """把关联测试用例序列化为前端可直接消费的数据结构。"""
    return {
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "filename": doc.filename,
        "content_preview": (doc.content or "")[:180],
    }


def _serialize_doc(
    doc: KnowledgeDocument,
    source_name_map: dict[int, str],
    linked_map: dict[int, list[dict]],
) -> dict:
    """把知识库文档序列化为列表结构，同时补充离线解析状态字段。"""
    content = doc.content or ""
    return {
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "created_at": _to_iso(doc.created_at),
        "file_size": len(content.encode("utf-8")),
        "source_doc_id": doc.source_doc_id,
        "source_doc_name": source_name_map.get(doc.source_doc_id),
        "linked_test_cases": linked_map.get(doc.id, []),
        "content_preview": content[:180],
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "parsed_at": _to_iso(doc.parsed_at),
        "task_id": doc.task_id,
        "retry_count": doc.retry_count,
    }


def _get_owned_project(project_id: int, user_id: int, db: Session) -> Optional[Project]:
    """校验项目归属，避免跨用户访问。"""
    return db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()


def _get_owned_doc_by_id_or_project_specific_id(
    doc_id: int,
    user_id: int,
    db: Session,
) -> Optional[KnowledgeDocument]:
    """先按全局ID查询，查不到再按 project_specific_id 兜底。"""
    doc = (
        db.query(KnowledgeDocument)
        .join(Project, Project.id == KnowledgeDocument.project_id)
        .filter(KnowledgeDocument.id == doc_id, Project.user_id == user_id)
        .first()
    )
    if doc:
        return doc

    return (
        db.query(KnowledgeDocument)
        .join(Project, Project.id == KnowledgeDocument.project_id)
        .filter(KnowledgeDocument.project_specific_id == doc_id, Project.user_id == user_id)
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
        .first()
    )


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

    query = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)

    if search:
        query = query.filter(KnowledgeDocument.filename.like(f"%{search}%"))

    if doc_type:
        query = query.filter(KnowledgeDocument.doc_type == doc_type)

    if not include_linked_test_cases:
        query = query.filter(
            ~and_(
                KnowledgeDocument.doc_type == "test_case",
                KnowledgeDocument.source_doc_id.isnot(None),
            )
        )

    if not include_evaluation_reports:
        query = query.filter(KnowledgeDocument.doc_type != "evaluation_report")

    if start_date:
        try:
            query = query.filter(
                KnowledgeDocument.created_at >= datetime.strptime(start_date, "%Y-%m-%d")
            )
        except ValueError:
            query = query.filter(KnowledgeDocument.created_at >= start_date)

    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            query = query.filter(KnowledgeDocument.created_at <= end_dt)
        except ValueError:
            query = query.filter(KnowledgeDocument.created_at <= end_date)

    total = query.count()
    total_pages = (total + page_size - 1) // page_size if total else 1

    documents = (
        query.order_by(KnowledgeDocument.created_at.asc(), KnowledgeDocument.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    requirement_ids = [d.id for d in documents if d.doc_type in REQUIREMENT_LIKE_TYPES]
    linked_map: dict[int, list[dict]] = {}
    if requirement_ids:
        linked_docs = (
            db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.doc_type == "test_case",
                KnowledgeDocument.source_doc_id.in_(requirement_ids),
            )
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
            .all()
        )
        for linked in linked_docs:
            linked_map.setdefault(linked.source_doc_id, []).append(_serialize_linked_doc(linked))

    source_ids = {d.source_doc_id for d in documents if d.source_doc_id}
    source_name_map: dict[int, str] = {}
    if source_ids:
        source_docs = (
            db.query(KnowledgeDocument.id, KnowledgeDocument.filename)
            .filter(KnowledgeDocument.project_id == project_id, KnowledgeDocument.id.in_(source_ids))
            .all()
        )
        source_name_map = {doc.id: doc.filename for doc in source_docs}

    serialized_docs = [_serialize_doc(doc, source_name_map, linked_map) for doc in documents]

    return {
        "documents": serialized_docs,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


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

    return {
        "success": True,
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "created_at": _to_iso(doc.created_at),
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "parsed_at": _to_iso(doc.parsed_at),
        "task_id": enqueue_result.get("task_id"),
        "retry_count": doc.retry_count,
    }


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

    linked_docs = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.project_id == doc.project_id,
            KnowledgeDocument.doc_type == "test_case",
            KnowledgeDocument.source_doc_id == doc.id,
        )
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
        .all()
    )

    return {
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "created_at": _to_iso(doc.created_at),
        "content": doc.content,
        "source_doc_id": doc.source_doc_id,
        "linked_docs": [_serialize_linked_doc(linked) for linked in linked_docs],
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "parsed_at": _to_iso(doc.parsed_at),
        "task_id": doc.task_id,
        "retry_count": doc.retry_count,
    }


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
            task_state = AsyncResult(doc.task_id, app=celery_app).state
        except Exception as e:
            # Celery 结果后端偶发返回异常元数据时，状态接口应降级而非 500。
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

        # 当任务已到最大重试且状态异常（PENDING/UNKNOWN）时，避免长期卡在 parsing。
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



    return {
        "id": doc.project_specific_id or doc.id,
        "global_id": doc.id,
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "parsed_at": _to_iso(doc.parsed_at),
        "task_id": doc.task_id,
        "retry_count": doc.retry_count,
        "task_state": task_state,
    }


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


def extract_error_text(err: Any) -> str:
    """从不同错误结构中提取可读文本。"""
    if err is None:
        return ""
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        for key in ["message", "detail", "error", "msg", "code"]:
            val = err.get(key)
            if val:
                return str(val)
        try:
            return json.dumps(err, ensure_ascii=False)
        except Exception:
            return str(err)
    return str(err)


def translate_error_text(text: str) -> str:
    """将常见英文错误关键词映射成中文。"""
    if not text:
        return "发生未知错误"
    if re.search(r"[\u4e00-\u9fff]", text):
        return text

    lower = text.lower()
    mapping = [
        ("timeout", "请求超时"),
        ("timed out", "请求超时"),
        ("failed to fetch", "网络请求失败"),
        ("networkerror", "网络请求失败"),
        ("ssl", "网络连接异常，请检查网络环境后重试"),
        ("unexpected_eof_while_reading", "网络连接被中断，请稍后重试"),
        ("econnrefused", "连接被拒绝"),
        ("connection refused", "连接被拒绝"),
        ("unauthorized", "未授权或登录已过期"),
        ("forbidden", "权限不足"),
        ("not found", "资源不存在"),
        ("bad request", "请求参数错误"),
        ("invalidparameter", "参数错误"),
        ("quotaexhausted", "额度已耗尽"),
        ("arrearage", "余额不足"),
        ("paymentrequired", "需要付费或余额不足"),
        ("rate limit", "请求过于频繁"),
        ("json", "响应解析失败"),
        ("parse", "响应解析失败"),
        ("500", "服务端异常"),
        ("502", "网关错误"),
        ("503", "服务暂不可用"),
        ("504", "网关超时"),
    ]
    for key, msg in mapping:
        if key in lower:
            return msg
    return "发生错误，请稍后重试"
