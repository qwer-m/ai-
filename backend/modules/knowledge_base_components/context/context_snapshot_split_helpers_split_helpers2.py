"""项目级 context snapshot 编排层。"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db.models import KnowledgeDocument, ProjectContextSnapshot
from modules.knowledge_base_components.context.context_snapshot_parts import (
    _build_source_breakdown,
    _query_snapshot_row,
    _save_snapshot_success,
)
from modules.knowledge_base_components.repositories.context_snapshot_repository import (
    ContextSnapshotRepository,
)
from modules.knowledge_base_components.snapshot.snapshot_builder import (
    SNAPSHOT_CONFIG,
    build_corpus_hash,
    build_snapshot_text,
    collect_project_docs,
    decide_rebuild_mode,
    doc_content_hash,
    estimate_source_stats,
    mark_snapshot_failed,
    merge_incremental_snapshot,
    safe_json_loads,
)
from modules.knowledge_base_components.snapshot.snapshot_readiness import evaluate_snapshot_readiness
from modules.knowledge_base_components.context.snapshot_task_dispatcher import (
    enqueue_snapshot_build_task,
)

logger = logging.getLogger(__name__)

def enqueue_context_snapshot_rebuild_impl(
    project_id: int,
    db: Session,
    user_id: Optional[int] = None,
    force_rebuild: bool = False,
    rebuild_reason_hint: Optional[str] = None,
) -> dict:
    """触发快照异步重建，带 pending 防抖。"""
    snapshot_repo = ContextSnapshotRepository(db)
    snapshot, schema_compatible = _query_snapshot_row(db, project_id)
    if not schema_compatible:
        return {"queued": False, "reason": "snapshot_schema_incompatible"}
    now = datetime.utcnow()
    if snapshot and snapshot.build_status == "pending" and snapshot.updated_at:
        # 仅当 pending 已有真实构建痕迹时才防抖，避免首次入队被误判跳过。
        has_trace = bool(
            (snapshot.rebuild_reason or "").strip()
            or snapshot.last_built_at
            or snapshot.last_used_at
            or (snapshot.build_error or "").strip()
        )
        if has_trace and (
            now - snapshot.updated_at
        ).total_seconds() < SNAPSHOT_CONFIG.enqueue_cooldown_seconds:
            return {
                "queued": False,
                "reason": "already_pending",
                "cooldown_seconds": SNAPSHOT_CONFIG.enqueue_cooldown_seconds,
            }

    if not snapshot:
        snapshot = ProjectContextSnapshot(project_id=project_id, user_id=user_id, build_status="pending")
        snapshot_repo.add(snapshot)
    else:
        snapshot.build_status = "pending"
        snapshot.build_error = None
        snapshot.user_id = user_id
    # 中文注释：优先记录业务触发原因（文档上传/删除/更新），便于链路排障。
    snapshot.rebuild_reason = (
        str(rebuild_reason_hint).strip()
        if rebuild_reason_hint
        else ("manual" if force_rebuild else "incremental_merge")
    )
    snapshot_repo.commit()

    try:
        task_result = enqueue_snapshot_build_task(
            project_id=project_id,
            user_id=user_id,
            force_rebuild=force_rebuild,
        )
        # 中文注释：统一 reason 枚举，便于上层日志直接区分 queued/already_pending/enqueue_failed。
        logger.info(
            "snapshot_rebuild_enqueued project_id=%s user_id=%s reason=%s task_id=%s",
            project_id,
            user_id,
            snapshot.rebuild_reason,
            task_result.id,
        )
        return {"queued": True, "task_id": task_result.id, "reason": "queued"}
    except Exception as e:
        mark_snapshot_failed(snapshot, f"enqueue_failed:{e}", snapshot.rebuild_reason or "manual", db)
        logger.warning(
            "snapshot_rebuild_enqueue_failed project_id=%s user_id=%s reason=%s error=%s",
            project_id,
            user_id,
            snapshot.rebuild_reason,
            e,
        )
        return {"queued": False, "reason": "enqueue_failed", "error": str(e)}
