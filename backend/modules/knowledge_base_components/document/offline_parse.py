"""知识库离线解析组件（阶段1）。"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.cache_layer.chroma_client import chroma_client
from core.processing.file_processing import parse_file_path
from core.db.models import KnowledgeDocument
from modules.domain.knowledge_base_components.document.document_ops import INDEXABLE_DOC_TYPES
from modules.domain.knowledge_base_components.document.offline_parse_support import (
    has_injection_flag,
    safe_error_message,
    validate_parsed_content,
)

logger = logging.getLogger(__name__)
OFFLINE_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "runtime" / "knowledge_uploads"


def _build_storage_name(filename: str) -> str:
    suffix = Path(filename or "").suffix or ".bin"
    return f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid4().hex}{suffix}"


async def save_upload_file_for_offline_parse(file: UploadFile) -> str:
    """先落盘再入队，避免 UploadFile 在请求结束后失效。"""
    OFFLINE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = _build_storage_name(file.filename or "upload.bin")
    file_path = OFFLINE_UPLOAD_DIR / stored_name
    file_path.write_bytes(await file.read())
    return str(file_path)


def cleanup_offline_file(file_path: str) -> None:
    """清理临时文件，失败仅记录日志。"""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.warning("离线解析临时文件清理失败 file=%s err=%s", file_path, e)


def create_pending_document_impl(
    module,
    filename: str,
    doc_type: str,
    project_id: int,
    db: Session,
    user_id: Optional[int] = None,
) -> KnowledgeDocument:
    """创建 pending 文档记录（不在请求线程做解析）。"""
    min_order = (
        db.query(func.min(KnowledgeDocument.display_order))
        .filter(KnowledgeDocument.project_id == project_id)
        .scalar()
    )
    new_order = (min_order if min_order is not None else 0.0) - 1.0

    doc = KnowledgeDocument(
        filename=filename,
        content="",
        doc_type=doc_type,
        content_hash=None,
        project_id=project_id,
        project_specific_id=0,
        user_id=user_id,
        display_order=new_order,
        parse_status="pending",
        parse_error=None,
        parsed_at=None,
        task_id=None,
        retry_count=0,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    module.reindex_project_specific_ids(doc_type, project_id, db)
    db.refresh(doc)
    return doc


def bind_parse_task_impl(doc_id: int, task_id: str, db: Session) -> None:
    """把任务ID回写到文档；已 success 不回退。"""
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc or doc.parse_status == "success":
        return
    doc.task_id = task_id
    doc.parse_status = "pending"
    db.commit()


def mark_parse_retry_impl(
    doc_id: int,
    retry_count: int,
    error: Any,
    db: Session,
    task_id: Optional[str] = None,
) -> None:
    """重试前状态回退到 pending；已 success 不覆盖。"""
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc or doc.parse_status == "success":
        return
    doc.parse_status = "pending"
    doc.retry_count = retry_count
    doc.parse_error = f"第 {retry_count} 次重试前失败：{safe_error_message(error)}"
    if task_id:
        doc.task_id = task_id
    db.commit()


def mark_parse_failed_impl(
    doc_id: int,
    error: Any,
    db: Session,
    task_id: Optional[str] = None,
    retry_count: Optional[int] = None,
) -> None:
    """最终失败落库；已 success 不覆盖。"""
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc or doc.parse_status == "success":
        return
    doc.parse_status = "failed"
    doc.parse_error = safe_error_message(error)
    doc.parsed_at = datetime.utcnow()
    if task_id:
        doc.task_id = task_id
    if retry_count is not None:
        doc.retry_count = retry_count
    db.commit()


def parse_document_offline_impl(
    module,
    doc_id: int,
    file_path: str,
    db: Session,
    force: bool = False,
    user_id: Optional[int] = None,
    task_id: Optional[str] = None,
    retry_count: int = 0,
) -> dict:
    """离线解析主流程：解析、摘要、双索引、状态落库。"""
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        raise ValueError(f"知识库文档不存在: {doc_id}")

    # 幂等保护：已成功文档重复消费直接跳过。
    if doc.parse_status == "success" and not force:
        cleanup_offline_file(file_path)
        logger.info("离线解析跳过，文档已成功 doc_id=%s task_id=%s", doc_id, task_id)
        return {"status": "already_success", "document_id": doc_id}

    # 幂等保护：只处理当前绑定任务，旧任务直接忽略。
    if task_id and doc.task_id and doc.task_id != task_id:
        cleanup_offline_file(file_path)
        logger.warning(
            "离线解析忽略过期任务 doc_id=%s task_id=%s current_task_id=%s",
            doc_id,
            task_id,
            doc.task_id,
        )
        return {"status": "stale_task_ignored", "document_id": doc_id}

    doc.parse_status = "parsing"
    doc.parse_error = None
    doc.task_id = task_id or doc.task_id
    doc.retry_count = retry_count
    db.commit()

    logger.info("离线解析开始 doc_id=%s task_id=%s retry=%s", doc_id, task_id, retry_count)

    if has_injection_flag(doc.filename, "runtime_fail", doc_id):
        raise RuntimeError("离线解析注入失败：runtime_fail")
    if has_injection_flag(doc.filename, "fail_once", doc_id) and retry_count == 0:
        raise RuntimeError("离线解析注入失败：fail_once")

    content = parse_file_path(file_path)
    validate_parsed_content(content)

    content_hash = module.calculate_hash(content)
    existing = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.content_hash == content_hash,
            KnowledgeDocument.project_id == doc.project_id,
            KnowledgeDocument.id != doc.id,
        )
        .first()
    )
    if existing and not force:
        doc.parse_status = "failed"
        doc.parse_error = f"检测到重复文档：已存在《{existing.filename}》，请勿重复上传。"
        doc.parsed_at = datetime.utcnow()
        db.commit()
        cleanup_offline_file(file_path)
        return {
            "status": "duplicate",
            "document_id": doc.id,
            "existing_doc_id": existing.id,
            "existing_filename": existing.filename,
        }

    doc.content = content
    doc.content_hash = content_hash
    if user_id is not None and not doc.user_id:
        doc.user_id = user_id
    db.commit()
    db.refresh(doc)

    if has_injection_flag(doc.filename, "summary_fail", doc_id):
        raise RuntimeError("离线解析注入失败：summary_fail")
    summary = module._ensure_summary(doc, db, user_id or doc.user_id)

    indexed_raw = False
    indexed_summary = False

    if doc.doc_type in INDEXABLE_DOC_TYPES:
        if not getattr(chroma_client, "collection", None):
            raise RuntimeError("向量库不可用，无法完成知识库索引。")
        if has_injection_flag(doc.filename, "chroma_fail", doc_id):
            raise RuntimeError("离线解析注入失败：chroma_fail")

        # 幂等策略：写入前先删除旧索引，避免重复分块积累。
        chroma_client.delete_document(str(doc.id), raise_on_error=True)
        chroma_client.delete_document(f"{doc.id}_summary", raise_on_error=True)
        chroma_client.add_document(
            doc_id=str(doc.id),
            content=content,
            metadata={
                "project_id": doc.project_id,
                "doc_type": doc.doc_type,
                "filename": doc.filename,
                "doc_id": doc.id,
                "user_id": doc.user_id,
                "is_summary": False,
            },
            raise_on_error=True,
        )
        indexed_raw = True
        if summary and summary != content:
            chroma_client.add_document(
                doc_id=f"{doc.id}_summary",
                content=summary,
                metadata={
                    "project_id": doc.project_id,
                    "doc_type": doc.doc_type,
                    "filename": f"{doc.filename} (Summary)",
                    "doc_id": doc.id,
                    "user_id": doc.user_id,
                    "is_summary": True,
                },
                raise_on_error=True,
            )
            indexed_summary = True

    try:
        doc.parse_status = "success"
        doc.parse_error = None
        doc.parsed_at = datetime.utcnow()
        doc.retry_count = retry_count
        db.commit()
    except Exception:
        # 防止“向量已写入但 DB 状态未成功”导致主状态与索引不一致。
        db.rollback()
        if doc.doc_type in INDEXABLE_DOC_TYPES and (indexed_raw or indexed_summary):
            try:
                chroma_client.delete_document(str(doc.id), raise_on_error=True)
                chroma_client.delete_document(f"{doc.id}_summary", raise_on_error=True)
            except Exception as rollback_error:
                logger.error("离线解析索引回滚失败 doc_id=%s err=%s", doc_id, rollback_error)
        raise

    cleanup_offline_file(file_path)
    logger.info("离线解析成功 doc_id=%s task_id=%s", doc_id, task_id)
    return {"status": "success", "document_id": doc.id}


def queue_document_parse_impl(
    doc_id: int,
    file_path: str,
    force: bool = False,
    user_id: Optional[int] = None,
):
    """统一封装 Celery 入队，避免路由层直接依赖任务细节。"""
    from modules.orchestration.tasks import parse_knowledge_document_task

    return parse_knowledge_document_task.delay(
        document_id=doc_id,
        file_path=file_path,
        force=force,
        user_id=user_id,
    )
