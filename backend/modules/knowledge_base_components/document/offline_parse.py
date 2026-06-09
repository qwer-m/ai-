"""Knowledge-base offline parsing pipeline."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument
from core.processing.biz_key_extractor import extract_biz_key
from core.processing.business_chunking import BusinessChunkerDispatcher, Chunk
from core.processing.file_processing import parse_file_path
from modules.knowledge_base_components.document.document_index_service import (
    delete_document_indexes,
    is_vector_store_ready,
    upsert_document_indexes,
)
from modules.knowledge_base_components.document.document_ops import INDEXABLE_DOC_TYPES
from modules.knowledge_base_components.document.document_summary_service import (
    ensure_document_summary,
)
from modules.knowledge_base_components.document.document_task_dispatcher import (
    enqueue_parse_document_task,
)
from modules.knowledge_base_components.document.offline_parse_support import (
    has_injection_flag,
    safe_error_message,
    validate_parsed_content,
)
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)

logger = logging.getLogger(__name__)
OFFLINE_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "runtime" / "knowledge_uploads"


def _to_chroma_chunk_payloads(
    chunks: list[Chunk],
    *,
    default_module: str | None,
    default_biz_key: str,
) -> list[dict]:
    """Convert chunking output to Chroma add_document payload chunks."""
    payloads: list[dict] = []
    for item in chunks:
        chunk_text = str(getattr(item, "text", "") or "").strip()
        if not chunk_text:
            continue
        module_value = str(getattr(item, "module", "") or "").strip() or default_module
        biz_key_value = str(getattr(item, "biz_key", "") or "").strip() or default_biz_key
        requirement_id = str(getattr(item, "requirement_id", "") or "").strip() or None
        test_case_id = str(getattr(item, "test_case_id", "") or "").strip() or None

        related_ids: list[str] = []
        if requirement_id:
            related_ids.append(requirement_id)
        if test_case_id:
            related_ids.append(test_case_id)

        payloads.append(
            {
                "chunk_text": chunk_text,
                "metadata": {
                    "module": module_value,
                    "biz_key": biz_key_value,
                    "requirement_id": requirement_id,
                    "test_case_id": test_case_id,
                    "related_ids": related_ids,
                },
            }
        )
    return payloads


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
    repo.commit()
    repo.refresh(doc)

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
    """Offline parse flow: parse -> summarize -> index -> finalize state."""
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

    if has_injection_flag(doc.filename, "runtime_fail", doc_id):
        raise RuntimeError("offline parse injected failure: runtime_fail")
    if has_injection_flag(doc.filename, "fail_once", doc_id) and retry_count == 0:
        raise RuntimeError("offline parse injected failure: fail_once")

    content = parse_file_path(file_path)
    validate_parsed_content(content)

    content_hash = module.calculate_hash(content)
    existing = repo.find_duplicate_by_hash(
        project_id=doc.project_id,
        content_hash=content_hash,
        exclude_doc_id=doc.id,
    )
    if existing and not force:
        doc.parse_status = "failed"
        doc.parse_error = f"duplicate document detected: existing file '{existing.filename}'"
        doc.parsed_at = datetime.utcnow()
        repo.commit()
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
    repo.commit()
    repo.refresh(doc)

    if has_injection_flag(doc.filename, "summary_fail", doc_id):
        raise RuntimeError("offline parse injected failure: summary_fail")
    summary = ensure_document_summary(module, doc=doc, db=db, user_id=user_id or doc.user_id)

    indexed_raw = False
    indexed_summary = False

    if doc.doc_type in INDEXABLE_DOC_TYPES:
        if not is_vector_store_ready():
            raise RuntimeError("vector store is unavailable")
        if has_injection_flag(doc.filename, "chroma_fail", doc_id):
            raise RuntimeError("offline parse injected failure: chroma_fail")

        dispatcher = BusinessChunkerDispatcher()
        raw_chunk_objects = dispatcher.chunk(str(doc.doc_type or ""), content)
        if not raw_chunk_objects:
            raw_chunk_objects = [Chunk(text=content)]

        module_hint = next(
            (str(c.module).strip() for c in raw_chunk_objects if getattr(c, "module", None)),
            None,
        )
        module_hint = module_hint or None
        doc_biz_key = extract_biz_key(content, module_hint or "")

        raw_chunks = _to_chroma_chunk_payloads(
            raw_chunk_objects,
            default_module=module_hint,
            default_biz_key=doc_biz_key,
        )

        summary_chunks = None
        if summary and summary != content:
            summary_chunk_objects = dispatcher.chunk(str(doc.doc_type or ""), summary)
            if not summary_chunk_objects:
                summary_chunk_objects = [Chunk(text=summary, module=module_hint, biz_key=doc_biz_key)]
            summary_chunks = _to_chroma_chunk_payloads(
                summary_chunk_objects,
                default_module=module_hint,
                default_biz_key=doc_biz_key,
            )

        indexed_raw, indexed_summary = upsert_document_indexes(
            doc_id=doc.id,
            content=content,
            metadata={
                "project_id": doc.project_id,
                "doc_type": doc.doc_type,
                "filename": doc.filename,
                "doc_id": doc.id,
                "user_id": doc.user_id,
                "module": module_hint,
                "biz_key": doc_biz_key,
                "requirement_id": None,
                "test_case_id": None,
                "source_doc_name": doc.filename,
                "is_summary": False,
            },
            summary_text=summary,
            summary_metadata={
                "project_id": doc.project_id,
                "doc_type": doc.doc_type,
                "filename": f"{doc.filename} (Summary)",
                "doc_id": doc.id,
                "user_id": doc.user_id,
                "module": module_hint,
                "biz_key": doc_biz_key,
                "requirement_id": None,
                "test_case_id": None,
                "source_doc_name": doc.filename,
                "is_summary": True,
            },
            chunks=raw_chunks,
            summary_chunks=summary_chunks,
            raise_on_error=True,
        )

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
