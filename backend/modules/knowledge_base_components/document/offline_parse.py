"""Knowledge-base offline parsing pipeline."""

from __future__ import annotations

import logging
import os
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from core.db.model_defs import KnowledgeDocument
from core.processing.file_processing import parse_file_path
from modules.knowledge_base_components.document.document_index_service import (
    delete_document_indexes,
    is_vector_store_ready,
    reindex_document_from_persisted_content,
)
from modules.knowledge_base_components.document.document_asset_service import (
    prepare_document_assets,
)
from modules.knowledge_base_components.document.document_ops import INDEXABLE_DOC_TYPES
from modules.knowledge_base_components.document.document_task_dispatcher import (
    enqueue_parse_document_task,
)
from modules.knowledge_base_components.document.offline_parse_support import (
    safe_error_message,
    validate_parsed_content,
)
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)

logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OFFLINE_UPLOAD_DIR = BACKEND_ROOT / "runtime" / "knowledge_uploads"


def _resolve_offline_upload_dir() -> Path:
    """解析离线解析上传目录，相对路径统一以 backend 为基准。"""
    raw = str(os.getenv("OFFLINE_UPLOAD_DIR") or "").strip()
    if not raw:
        return DEFAULT_OFFLINE_UPLOAD_DIR

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    return path.resolve()


# 避免工作目录变化导致文件落到 backend 之外。
OFFLINE_UPLOAD_DIR = _resolve_offline_upload_dir()


def _build_storage_name(filename: str) -> str:
    suffix = Path(filename or "").suffix or ".bin"
    return f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid4().hex}{suffix}"


async def save_upload_file_for_offline_parse(file: UploadFile) -> str:
    """Persist upload first, then enqueue async parse."""
    OFFLINE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = _build_storage_name(file.filename or "upload.bin")
    file_path = OFFLINE_UPLOAD_DIR / stored_name
    file_path.write_bytes(await file.read())
    return str(file_path)


def cleanup_offline_file(file_path: str) -> None:
    """Best-effort cleanup for offline temp files."""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.warning("offline temp cleanup failed file=%s err=%s", file_path, e)


def create_pending_document_impl(
    module,
    filename: str,
    doc_type: str,
    project_id: int,
    db: Session,
    user_id: Optional[int] = None,
) -> KnowledgeDocument:
    """Create one pending document record before background parse."""
    repo = KnowledgeDocumentRepository(db)
    min_order = repo.get_min_display_order(project_id=project_id)
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
    repo.add(doc)
    db.flush()
    module.reindex_project_specific_ids(doc_type, project_id, db)
    repo.refresh(doc)
    return doc


def bind_parse_task_impl(doc_id: int, task_id: str, db: Session) -> None:
    """Bind task id onto document unless it already succeeded."""
    repo = KnowledgeDocumentRepository(db)
    doc = repo.get_by_id(doc_id)
    if not doc or doc.parse_status == "success":
        return
    doc.task_id = task_id
    doc.parse_status = "pending"
    repo.commit()


def mark_parse_retry_impl(
    doc_id: int,
    retry_count: int,
    error: Any,
    db: Session,
    task_id: Optional[str] = None,
) -> None:
    """Mark transient failure before retry, unless already successful."""
    repo = KnowledgeDocumentRepository(db)
    doc = repo.get_by_id(doc_id)
    if not doc or doc.parse_status == "success":
        return
    doc.parse_status = "pending"
    doc.retry_count = retry_count
    doc.parse_error = f"retry #{retry_count} failed before requeue: {safe_error_message(error)}"
    if task_id:
        doc.task_id = task_id
    repo.commit()


def mark_parse_failed_impl(
    doc_id: int,
    error: Any,
    db: Session,
    task_id: Optional[str] = None,
    retry_count: Optional[int] = None,
) -> None:
    """Mark final parse failure unless already successful."""
    repo = KnowledgeDocumentRepository(db)
    doc = repo.get_by_id(doc_id)
    if not doc or doc.parse_status == "success":
        return
    doc.parse_status = "failed"
    doc.parse_error = safe_error_message(error)
    doc.parsed_at = datetime.utcnow()
    if task_id:
        doc.task_id = task_id
    if retry_count is not None:
        doc.retry_count = retry_count
    repo.commit()


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
    """离线资产准备流程：解析真实资产、建立可重建索引并完成状态切换。"""
    repo = KnowledgeDocumentRepository(db)
    doc = repo.get_by_id(doc_id)
    if not doc:
        raise ValueError(f"knowledge document not found: {doc_id}")

    if doc.parse_status == "success" and not force:
        cleanup_offline_file(file_path)
        logger.info("offline parse skipped, already success doc_id=%s task_id=%s", doc_id, task_id)
        return {"status": "already_success", "document_id": doc_id}

    if task_id and doc.task_id and doc.task_id != task_id:
        cleanup_offline_file(file_path)
        logger.warning(
            "offline parse ignored stale task doc_id=%s task_id=%s current_task_id=%s",
            doc_id,
            task_id,
            doc.task_id,
        )
        return {"status": "stale_task_ignored", "document_id": doc_id}

    doc.parse_status = "parsing"
    doc.parse_error = None
    doc.task_id = task_id or doc.task_id
    doc.retry_count = retry_count
    repo.commit()

    logger.info("offline parse started doc_id=%s task_id=%s retry=%s", doc_id, task_id, retry_count)

    source_bytes = Path(file_path).read_bytes()
    content_hash = hashlib.sha256(source_bytes).hexdigest()
    # 每次上传都是独立文档输入；内容指纹仅用于资产一致性校验，不用于复用或拦截。

    asset_result = prepare_document_assets(
        document_id=int(doc.id),
        source_path=file_path,
        original_filename=str(doc.filename or Path(file_path).name),
    )
    content = str(asset_result.get("document_text") or "")
    if not content.strip():
        content = parse_file_path(file_path, db=db, user_id=user_id)
    validate_parsed_content(content)

    doc.content = content
    doc.content_hash = content_hash
    doc.summary = None
    doc.parse_status = "parsing"
    doc.parse_error = None
    doc.task_id = task_id or doc.task_id
    doc.retry_count = retry_count
    if user_id is not None and not doc.user_id:
        doc.user_id = user_id
    repo.commit()
    repo.refresh(doc)

    indexed_raw = False
    indexed_summary = False

    if doc.doc_type in INDEXABLE_DOC_TYPES:
        if not is_vector_store_ready():
            raise RuntimeError("vector store is unavailable")
        index_result = reindex_document_from_persisted_content(doc)
        indexed_raw = bool(index_result["indexed_raw"])
        indexed_summary = bool(index_result["indexed_summary"])

    try:
        doc.parse_status = "success"
        doc.parse_error = None
        doc.parsed_at = datetime.utcnow()
        doc.retry_count = retry_count
        repo.commit()
    except Exception:
        repo.rollback()
        if doc.doc_type in INDEXABLE_DOC_TYPES and (indexed_raw or indexed_summary):
            try:
                delete_document_indexes(doc.id, raise_on_error=True)
            except Exception as rollback_error:
                logger.error("offline index rollback failed doc_id=%s err=%s", doc_id, rollback_error)
        raise

    cleanup_offline_file(file_path)
    logger.info("offline parse success doc_id=%s task_id=%s", doc_id, task_id)
    return {"status": "success", "document_id": doc.id}


def queue_document_parse_impl(
    doc_id: int,
    file_path: str,
    force: bool = False,
    user_id: Optional[int] = None,
):
    """Queue offline parse through task dispatcher adapter."""
    return enqueue_parse_document_task(
        doc_id=doc_id,
        file_path=file_path,
        force=force,
        user_id=user_id,
    )
