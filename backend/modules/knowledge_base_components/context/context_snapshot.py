"""项目级 context snapshot 编排层。"""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from modules.knowledge_base_components.context.context_snapshot_parts import (
    _query_snapshot_row,
)
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from modules.knowledge_base_components.snapshot.snapshot_builder import (
    SNAPSHOT_CONFIG,
    doc_content_hash,
    estimate_source_stats,
    safe_json_loads,
)
from modules.knowledge_base_components.snapshot.snapshot_readiness import evaluate_snapshot_readiness

logger = logging.getLogger(__name__)

from modules.knowledge_base_components.context.context_snapshot_split_helpers import (
    enqueue_context_snapshot_rebuild_impl,
    get_or_build_context_snapshot_impl,
)

def get_context_snapshot_status_impl(project_id: int, db: Session) -> dict:
    """查询快照状态，并补充输入预算相关观测字段。"""
    try:
        snapshot, schema_compatible = _query_snapshot_row(db, project_id)
    except SQLAlchemyError:
        snapshot = None
        schema_compatible = False
    docs = KnowledgeDocumentRepository(db).list_project_docs_ordered_by_id(project_id=project_id)
    parts = [f"{doc.id}:{doc_content_hash(doc)}" for doc in docs]
    current_hash = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest() if parts else ""
    if snapshot:
        previous_fingerprints = safe_json_loads(snapshot.source_fingerprints)
        changed_doc_ids = [f"{doc.id}" for doc in docs if previous_fingerprints.get(f"{doc.id}") != doc_content_hash(doc)]
    else:
        changed_doc_ids = [f"{doc.id}" for doc in docs]
    readiness = evaluate_snapshot_readiness(
        snapshot=snapshot,
        current_corpus_hash=current_hash,
        current_doc_count=len(docs),
        changed_doc_ids=changed_doc_ids,
    )
    estimated_stats = estimate_source_stats(docs)
    source_type_counts: dict[str, int] = {}
    source_owner_counts: dict[str, int] = {}
    requirement_like_types = {"requirement", "product_requirement", "incomplete"}
    requirement_like_count = 0
    non_requirement_examples: list[str] = []
    for doc in docs:
        doc_type = str(doc.doc_type or "unknown")
        source_type_counts[doc_type] = int(source_type_counts.get(doc_type, 0)) + 1
        owner_key = "none" if doc.user_id is None else str(doc.user_id)
        source_owner_counts[owner_key] = int(source_owner_counts.get(owner_key, 0)) + 1
        if doc_type in requirement_like_types:
            requirement_like_count += 1
        elif len(non_requirement_examples) < 8:
            non_requirement_examples.append(f"{doc.id}:{doc.filename}:{doc_type}")

    if not snapshot:
        return {
            "exists": False,
            **readiness,
            "project_id": project_id,
            "current_corpus_hash": current_hash,
            "snapshot_corpus_hash": "",
            "corpus_hash": current_hash,
            "snapshot_hash": "",
            "snapshot_version": 0,
            "snapshot_fingerprint": "",
            "build_latency_ms": 0.0,
            "source_doc_count": 0,
            "last_built_at": None,
            "last_used_at": None,
            "build_error": None,
            "rebuild_reason": None,
            "incremental_merge_count": 0,
            "is_stale": True,
            "prewarm_task_id": None,
            "build_status": "not_exists",
            "input_soft_limit": SNAPSHOT_CONFIG.input_soft_limit,
            "single_doc_limit": SNAPSHOT_CONFIG.single_doc_max_chars,
            "batch_max_docs": SNAPSHOT_CONFIG.batch_max_docs,
            "final_merge_limit": SNAPSHOT_CONFIG.final_merge_limit,
            "source_doc_type_counts": source_type_counts,
            "source_owner_user_counts": source_owner_counts,
            "source_requirement_like_doc_count": requirement_like_count,
            "source_non_requirement_doc_count": max(0, len(docs) - requirement_like_count),
            "source_non_requirement_examples": non_requirement_examples,
            "schema_compatible": bool(schema_compatible),
            **estimated_stats,
        }

    return {
        "exists": True,
        **readiness,
        "project_id": project_id,
        "current_corpus_hash": current_hash,
        "snapshot_corpus_hash": snapshot.corpus_hash,
        # 中文注释：保留旧字段，兼容老调用方。
        "corpus_hash": current_hash,
        "snapshot_hash": snapshot.corpus_hash,
        "snapshot_version": int(snapshot.snapshot_version or 0),
        "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
        "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
        "source_doc_count": int(snapshot.source_doc_count or 0),
        "last_built_at": snapshot.last_built_at.isoformat() if snapshot.last_built_at else None,
        "last_used_at": snapshot.last_used_at.isoformat() if snapshot.last_used_at else None,
        "build_error": snapshot.build_error,
        "rebuild_reason": snapshot.rebuild_reason,
        "incremental_merge_count": int(snapshot.incremental_merge_count or 0),
        "is_stale": bool(readiness.get("snapshot_status") == "stale"),
        "prewarm_task_id": None,
        "build_status": snapshot.build_status,
        "input_soft_limit": SNAPSHOT_CONFIG.input_soft_limit,
        "single_doc_limit": SNAPSHOT_CONFIG.single_doc_max_chars,
        "batch_max_docs": SNAPSHOT_CONFIG.batch_max_docs,
        "final_merge_limit": SNAPSHOT_CONFIG.final_merge_limit,
        "source_doc_type_counts": source_type_counts,
        "source_owner_user_counts": source_owner_counts,
        "source_requirement_like_doc_count": requirement_like_count,
        "source_non_requirement_doc_count": max(0, len(docs) - requirement_like_count),
        "source_non_requirement_examples": non_requirement_examples,
        "schema_compatible": bool(schema_compatible),
        **estimated_stats,
    }

