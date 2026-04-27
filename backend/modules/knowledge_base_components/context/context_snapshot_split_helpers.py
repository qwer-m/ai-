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

logger = logging.getLogger(__name__)

from modules.knowledge_base_components.context.context_snapshot_split_helpers_split_helpers2 import (
    enqueue_context_snapshot_rebuild_impl,
)

def get_or_build_context_snapshot_impl(
    module,
    project_id: int,
    db: Session,
    user_id: Optional[int] = None,
    force_rebuild: bool = False,
    prefer_async_rebuild: bool = False,
) -> dict:
    """获取或构建快照；失败由调用方 fallback 到 RAG。"""
    build_started_ts = time.perf_counter()
    snapshot_repo = ContextSnapshotRepository(db)
    corpus = collect_project_docs(module, db, project_id, user_id)
    if not corpus:
        snapshot, schema_compatible = _query_snapshot_row(db, project_id)
        if not schema_compatible:
            snapshot = None
        readiness = evaluate_snapshot_readiness(
            snapshot=snapshot,
            current_corpus_hash="",
            current_doc_count=0,
            changed_doc_ids=[],
        )
        return {
            "success": False,
            "fallback_reason": "no_docs",
            "rebuild_reason": "no_docs",
            "snapshot_text": "",
            **readiness,
            "snapshot_version": 0,
            "snapshot_fingerprint": "",
            "build_latency_ms": 0.0,
            "prewarm_task_id": None,
        }

    source_breakdown = _build_source_breakdown(corpus, request_user_id=user_id)
    corpus_hash, current_fingerprints = build_corpus_hash(corpus)
    snapshot, schema_compatible = _query_snapshot_row(db, project_id)
    if not schema_compatible:
        return {
            "success": False,
            "fallback_reason": "snapshot_schema_incompatible",
            "rebuild_reason": "schema_incompatible",
            "snapshot_text": "",
            "snapshot_version": 0,
            "snapshot_fingerprint": "",
            "build_latency_ms": 0.0,
            "prewarm_task_id": None,
            **evaluate_snapshot_readiness(
                snapshot=None,
                current_corpus_hash=corpus_hash,
                current_doc_count=len(corpus),
                changed_doc_ids=[str(item.get("doc_id")) for item in corpus if item.get("doc_id") is not None],
            ),
        }
    if not snapshot:
        snapshot = ProjectContextSnapshot(project_id=project_id, user_id=user_id, build_status="pending")
        snapshot_repo.add(snapshot)
        snapshot_repo.commit()
        snapshot_repo.refresh(snapshot)

    previous_fingerprints = safe_json_loads(snapshot.source_fingerprints)
    changed_doc_ids = [
        doc_id for doc_id, fp in current_fingerprints.items() if previous_fingerprints.get(doc_id) != fp
    ]
    mode = decide_rebuild_mode(snapshot, changed_doc_ids, len(corpus), force_rebuild)
    readiness = evaluate_snapshot_readiness(
        snapshot=snapshot,
        current_corpus_hash=corpus_hash,
        current_doc_count=len(corpus),
        changed_doc_ids=changed_doc_ids,
    )

    if mode == "reuse" and snapshot.corpus_hash == corpus_hash:
        snapshot.last_used_at = datetime.utcnow()
        snapshot.rebuild_reason = "reuse"
        snapshot_repo.commit()
        return {
            "success": True,
            "cache_hit": True,
            "rebuild_reason": "reuse",
            "snapshot_text": snapshot.snapshot_text or "",
            "corpus_hash": corpus_hash,
            "source_doc_count": len(corpus),
            "snapshot_version": int(snapshot.snapshot_version or 0),
            "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
            "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
            **readiness,
            "prewarm_task_id": None,
        }

    # 在线链路优先异步预热，当前请求继续走 RAG fallback。
    if prefer_async_rebuild and SNAPSHOT_CONFIG.async_prewarm_enabled:
        queue_result = enqueue_context_snapshot_rebuild_impl(
            project_id=project_id,
            db=db,
            user_id=user_id,
            force_rebuild=(mode == "manual"),
        )
        return {
            "success": False,
            "fallback_reason": "snapshot_async_rebuild_queued"
            if queue_result.get("queued")
            else "snapshot_async_rebuild_skip",
            "rebuild_reason": mode,
            "queue_result": queue_result,
            "snapshot_version": int(snapshot.snapshot_version or 0),
            "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
            "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
            **readiness,
            "prewarm_task_id": queue_result.get("task_id"),
        }

    from core.ai.ai_client import get_client_for_user

    client = get_client_for_user(user_id, db)
    snapshot.build_status = "pending"
    snapshot.build_error = None
    snapshot.user_id = user_id
    snapshot.rebuild_reason = mode
    snapshot_repo.commit()

    if mode in ("full_rebuild", "manual"):
        build_result = build_snapshot_text(
            client=client,
            db=db,
            sources=corpus,
            batch_prompt="请将以下项目知识压缩为适合测试用例生成的精炼摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
            merge_prompt="请将以下分批知识摘要合并为一份项目级上下文快照，要求去重、结构清晰、覆盖关键规则。输出纯文本。",
        )
        if not build_result.get("success"):
            mark_snapshot_failed(
                snapshot,
                build_result.get("error") or "snapshot_full_rebuild_failed",
                mode,
                db,
                observability=build_result.get("build_observability"),
            )
            return {
                "success": False,
                "fallback_reason": "snapshot_full_rebuild_failed",
                "rebuild_reason": mode,
                "snapshot_version": int(snapshot.snapshot_version or 0),
                "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
                "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
            }

        snapshot_text = (build_result.get("text") or "")[: SNAPSHOT_CONFIG.max_snapshot_chars]
        build_latency_ms = max(0.0, (time.perf_counter() - build_started_ts) * 1000.0)
        _save_snapshot_success(
            snapshot=snapshot,
            snapshot_text=snapshot_text,
            corpus_hash=corpus_hash,
            current_fingerprints=current_fingerprints,
            source_doc_count=len(corpus),
            rebuild_reason=mode,
            full_rebuild=True,
            build_latency_ms=build_latency_ms,
            db=db,
        )
        build_observability = {
            **(build_result.get("build_observability") or {}),
            **source_breakdown,
        }
        logger.info(
            "snapshot build success project_id=%s mode=%s stats=%s",
            project_id,
            mode,
            json.dumps(build_observability, ensure_ascii=False),
        )
        return {
            "success": True,
            "cache_hit": False,
            "rebuild_reason": mode,
            "snapshot_text": snapshot.snapshot_text or "",
            "corpus_hash": corpus_hash,
            "source_doc_count": len(corpus),
            "snapshot_version": int(snapshot.snapshot_version or 0),
            "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
            "build_latency_ms": float(snapshot.last_build_latency_ms or build_latency_ms),
            **evaluate_snapshot_readiness(
                snapshot=snapshot,
                current_corpus_hash=corpus_hash,
                current_doc_count=len(corpus),
                changed_doc_ids=[],
            ),
            "build_observability": build_observability,
            "prewarm_task_id": None,
        }

    changed_set = set(changed_doc_ids)
    changed_items = [item for item in corpus if str(item["doc_id"]) in changed_set]
    delta_result = build_snapshot_text(
        client=client,
        db=db,
        sources=changed_items,
        batch_prompt="请将以下新增或变更知识压缩为增量摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
        merge_prompt="请将以下增量摘要合并去重为一份简洁的增量知识补充。输出纯文本。",
    )
    if not delta_result.get("success"):
        mark_snapshot_failed(
            snapshot,
            delta_result.get("error") or "snapshot_incremental_merge_failed",
            "incremental_merge",
            db,
            observability=delta_result.get("build_observability"),
        )
        return {
            "success": False,
            "fallback_reason": "snapshot_incremental_merge_failed",
            "rebuild_reason": "incremental_merge",
            "snapshot_version": int(snapshot.snapshot_version or 0),
            "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
            "build_latency_ms": float(snapshot.last_build_latency_ms or 0.0),
        }

    merged_text, merge_info = merge_incremental_snapshot(
        snapshot.snapshot_text or "",
        delta_result.get("text") or "",
        SNAPSHOT_CONFIG.max_snapshot_chars,
    )
    build_latency_ms = max(0.0, (time.perf_counter() - build_started_ts) * 1000.0)
    _save_snapshot_success(
        snapshot=snapshot,
        snapshot_text=merged_text,
        corpus_hash=corpus_hash,
        current_fingerprints=current_fingerprints,
        source_doc_count=len(corpus),
        rebuild_reason="incremental_merge",
        full_rebuild=False,
        build_latency_ms=build_latency_ms,
        db=db,
    )
    delta_source_breakdown = _build_source_breakdown(changed_items, request_user_id=user_id)
    delta_observability = {
        **(delta_result.get("build_observability") or {}),
        **delta_source_breakdown,
    }
    logger.info(
        "snapshot incremental success project_id=%s stats=%s merge=%s",
        project_id,
        json.dumps(delta_observability, ensure_ascii=False),
        json.dumps(merge_info, ensure_ascii=False),
    )
    return {
        "success": True,
        "cache_hit": False,
        "rebuild_reason": "incremental_merge",
        "snapshot_text": snapshot.snapshot_text or "",
        "corpus_hash": corpus_hash,
        "source_doc_count": len(corpus),
        "snapshot_version": int(snapshot.snapshot_version or 0),
        "snapshot_fingerprint": str(snapshot.snapshot_fingerprint or ""),
        "build_latency_ms": float(snapshot.last_build_latency_ms or build_latency_ms),
        **evaluate_snapshot_readiness(
            snapshot=snapshot,
            current_corpus_hash=corpus_hash,
            current_doc_count=len(corpus),
            changed_doc_ids=[],
        ),
        "build_observability": {
            **delta_observability,
            "final_merge_mode": merge_info.get("merge_mode"),
            "merge_trimmed": bool(merge_info.get("merge_trimmed")),
        },
        "prewarm_task_id": None,
    }
