"""
知识库离线解析组件。

职责边界：
1. 接收上传文件并落盘，保证 Celery 任务可在请求线程外读取原始文件。
2. 维护文档解析状态机（pending/parsing/success/failed）的字段更新。
3. 复用现有摘要与向量索引逻辑，完成“原文+摘要”双索引写入。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from typing import Any, Optional

from fastapi import UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.chroma_client import chroma_client
from core.file_processing import parse_file_path
from core.models import KnowledgeDocument
from modules.knowledge_base_components.document_ops import INDEXABLE_DOC_TYPES

# 上传原始文件统一落到 backend/runtime/knowledge_uploads，避免散落在临时目录。
OFFLINE_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "runtime" / "knowledge_uploads"
MAX_PARSE_ERROR_LENGTH = 2000


def _safe_error_message(error: Any) -> str:
    """把异常对象转换为前端可展示的中文消息，并限制长度避免污染页面。"""
    text = str(error or "").strip()
    if not text:
        return "离线解析失败，请稍后重试。"

    lower = text.lower()
    mapping = [
        ("timeout", "连接超时，请检查网络或稍后重试。"),
        ("timed out", "连接超时，请检查网络或稍后重试。"),
        ("ssl", "SSL 连接异常，请检查网络环境或证书配置。"),
        ("unexpected_eof_while_reading", "网络连接被中断，请稍后重试。"),
        ("connection refused", "目标服务拒绝连接，请检查服务是否可用。"),
        ("econnrefused", "目标服务拒绝连接，请检查服务是否可用。"),
        ("not found", "未找到对应资源，请检查配置。"),
        ("permission", "权限不足，请检查当前账号配置。"),
        ("unauthorized", "鉴权失败，请检查密钥或登录状态。"),
    ]
    for key, message in mapping:
        if key in lower:
            return message

    if len(text) > MAX_PARSE_ERROR_LENGTH:
        return text[:MAX_PARSE_ERROR_LENGTH] + "..."
    return text


def _build_storage_name(filename: str) -> str:
    """生成可追踪且不会冲突的本地文件名。"""
    suffix = Path(filename or "").suffix or ".bin"
    return f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid4().hex}{suffix}"


async def save_upload_file_for_offline_parse(file: UploadFile) -> str:
    """
    把上传文件保存到本地，供 Celery 任务离线解析。

    设计原因：请求线程结束后 UploadFile 生命周期不再可靠，必须先落盘。
    """
    OFFLINE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = _build_storage_name(file.filename or "upload.bin")
    file_path = OFFLINE_UPLOAD_DIR / stored_name
    content = await file.read()
    file_path.write_bytes(content)
    return str(file_path)


def cleanup_offline_file(file_path: str) -> None:
    """解析完成后清理临时文件，避免磁盘空间持续增长。"""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        # 清理失败不影响主流程，避免因为临时文件问题阻断业务。
        pass


def create_pending_document_impl(
    module,
    filename: str,
    doc_type: str,
    project_id: int,
    db: Session,
    user_id: Optional[int] = None,
) -> KnowledgeDocument:
    """
    创建“待解析”文档记录。

    这里仅做最小入库，不做文本解析和向量入库，确保上传接口快速返回。
    """
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
    """把 Celery 任务ID回写到文档，便于前端按文档查询任务状态。"""
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        return
    doc.task_id = task_id
    doc.parse_status = "pending"
    db.commit()


def mark_parse_retry_impl(doc_id: int, retry_count: int, error: Any, db: Session, task_id: Optional[str] = None) -> None:
    """任务重试前把状态回退到 pending，并记录最近一次失败原因。"""
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        return
    doc.parse_status = "pending"
    doc.retry_count = retry_count
    doc.parse_error = f"第 {retry_count} 次重试前失败：{_safe_error_message(error)}"
    if task_id:
        doc.task_id = task_id
    db.commit()


def mark_parse_failed_impl(doc_id: int, error: Any, db: Session, task_id: Optional[str] = None, retry_count: Optional[int] = None) -> None:
    """任务最终失败时落库，保证前端能看到明确失败状态。"""
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        return
    doc.parse_status = "failed"
    doc.parse_error = _safe_error_message(error)
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
    """
    执行离线解析主流程：读文件 -> 解析文本 -> 摘要 -> 向量索引 -> 状态落库。

    注意：异常由 Celery 任务捕获并决定是否重试；这里不吞异常。
    """
    doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
    if not doc:
        raise ValueError(f"知识库文档不存在: {doc_id}")

    doc.parse_status = "parsing"
    doc.parse_error = None
    doc.task_id = task_id or doc.task_id
    doc.retry_count = retry_count
    db.commit()

    content = parse_file_path(file_path)
    if not str(content or "").strip():
        raise ValueError("未解析到可用文本内容，请检查文件格式或文件内容。")

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

    summary = module._ensure_summary(doc, db, user_id or doc.user_id)

    if doc.doc_type in INDEXABLE_DOC_TYPES:
        # 向量库不可用时直接失败，避免“状态成功但检索不可用”的假成功。
        if not getattr(chroma_client, "collection", None):
            raise RuntimeError("向量库不可用，无法完成知识库索引。")

        # 重试场景下先删旧索引，避免同文档多次入库造成向量重复。
        chroma_client.delete_document(str(doc.id))
        chroma_client.delete_document(f"{doc.id}_summary")
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
        )
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
            )

    doc.parse_status = "success"
    doc.parse_error = None
    doc.parsed_at = datetime.utcnow()
    doc.retry_count = retry_count
    db.commit()
    cleanup_offline_file(file_path)
    return {"status": "success", "document_id": doc.id}


def queue_document_parse_impl(doc_id: int, file_path: str, force: bool = False, user_id: Optional[int] = None):
    """统一封装 Celery 入队，避免路由层直接依赖任务实现细节。"""
    from modules.tasks import parse_knowledge_document_task

    return parse_knowledge_document_task.delay(
        document_id=doc_id,
        file_path=file_path,
        force=force,
        user_id=user_id,
    )
